/**
 * Two-way speech test: place a call, speak a phrase, and detect whether the far end replies.
 *
 *   node src/probe-voice.ts <e164> ["phrase to say"] [seconds]
 *
 * Requires the callee to answer. Reports inbound audio level over time so you can see the
 * IVR's greeting, your own phrase going out, and its response coming back.
 */

import { MediaPlane } from "./media.ts";
import { SipSession } from "./registrar.ts";
import { OpusFrameDecoder, peak, rms } from "./audio.ts";
import { OpusUtteranceEncoder } from "./opusenc.ts";
import { EspeakTts, SpeechSender } from "./tts.ts";

const e164Arg = process.argv[2];
const phrase = process.argv[3] ?? "Hello, this is an automated Google Voice test.";
const seconds = Number(process.argv[4] ?? 30);

if (!e164Arg) {
  console.error('usage: node src/probe-voice.ts <e164> ["phrase"] [seconds]');
  process.exit(2);
}
const toE164 = e164Arg.startsWith("+") ? e164Arg : `+${e164Arg.replace(/\D/g, "")}`;

const media = await MediaPlane.create();
const decoder = new OpusFrameDecoder();
const encoder = new OpusUtteranceEncoder({ bitrate: 24000 });
const sender = new SpeechSender((pkt) => media.sendOpus(pkt));

// --- inbound analysis ---
let inboundFrames = 0;
let loudFrames = 0;
let peakSeen = 0;
let rmsSum = 0;
/** Loud-audio episodes, used to show greeting vs reply in the timeline. */
const regions: Array<{ atMs: number; rms: number; peak: number }> = [];
const pending: Buffer[] = [];
let firstInboundAt: number | undefined;
/** When the far end was last actually *loud*, as opposed to streaming silence. */
let lastLoudAt = 0;

media.onTrack((payload, meta) => {
  if (meta.payloadType !== 111) return;
  inboundFrames += 1;
  pending.push(Buffer.from(payload));
  if (firstInboundAt === undefined) firstInboundAt = Date.now();
  if (pending.length >= 12) void drain();
});

async function drain(): Promise<void> {
  const batch = pending.splice(0, pending.length);
  for (const pkt of batch) {
    try {
      const { samples } = await decoder.decode(pkt);
      if (!samples.length) continue;
      const p = peak(samples);
      const r = rms(samples);
      if (p > peakSeen) peakSeen = p;
      rmsSum += r;
      if (r > 0.01) {
        loudFrames += 1;
        lastLoudAt = Date.now();
        regions.push({ atMs: Date.now() - (firstInboundAt ?? Date.now()), rms: r, peak: p });
      }
    } catch {
      /* decode error; counted by probe-media */
    }
  }
}

// --- place the call ---
const session = await SipSession.open({ onMessage: (m) => console.log("   <-", m.split("\r\n")[0]) });
console.log(`registered as ${session.ownNumber}`);
console.log(`calling ${toE164}\n`);

const result = await session.placeCall({ toE164, media });
console.log(`\nSIP: ${result.responses.join("  ")}`);
if (!result.answer) {
  console.log("no SDP answer — aborting");
  process.exitCode = 1;
  session.bye();
} else {
  console.log(`answer: ${result.answer.rtpmap[111] ?? "?"} port=${result.answer.audioPort}`);

  // Wait for the transport before transmitting.
  for (let i = 0; i < 40 && !media.dtlsConnected; i++) await new Promise((r) => setTimeout(r, 250));

  console.log(`\nlistening ${seconds}s…`);
  const startedListeningAt = Date.now();
  const deadline = startedListeningAt + seconds * 1000;
  let spoke = false;
  let spokeAt: number | undefined;
  let spokeEndAt: number | undefined;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 250));

    // Speak once the far end has been *quiet* — judged by loudness, not packet arrival.
    // The far end keeps streaming silence frames, so waiting on packet gaps never fires.
    const quietFor = lastLoudAt === 0 ? 0 : Date.now() - lastLoudAt;
    const graceOver = Date.now() > startedListeningAt + 8000;
    if (!spoke && media.stats().inbound.packets > 20 && (quietFor > 1500 || graceOver)) {
      spoke = true;
      console.log(`\nspeaking: "${phrase}"`);
      const pcm = await new EspeakTts().speak(phrase, { rate: 160, gain: 0.9 });
      console.log(`  synthesised ${(pcm.length / 48000).toFixed(2)}s of speech`);
      spokeAt = Date.now();
      await sender.play(await encoder.encode(pcm));
      spokeEndAt = Date.now();
      const spoken = await sender.settled();
      console.log(`  sent ${spoken?.framesSent ?? 0} frames in ${((spoken?.durationMs ?? 0) / 1000).toFixed(2)}s`);
    }
    if (media.stats().inbound.packets > 3000) break;
  }

  await drain();
  const stats = media.stats();
  const inboundSeconds = stats.inbound.elapsedMs / 1000;
  const replySeconds = (loudFrames * 20) / 1000;

  console.log(`\n=== results ===`);
  console.log(`inbound frames  : ${inboundFrames} (${inboundSeconds.toFixed(1)}s)`);
  console.log(`peak amplitude  : ${peakSeen.toFixed(4)}`);
  console.log(`mean rms        : ${inboundFrames ? (rmsSum / inboundFrames).toFixed(5) : "n/a"}`);
  console.log(`speech sent     : ${spoke ? "yes" : "no (far end never went quiet)"}`);
  console.log(`outbound pkts   : ${stats.outboundPackets}`);
  console.log(`raw ICE dgrams  : ${stats.raw.datagrams} (decrypt ok=${stats.raw.decryptOk} fail=${stats.raw.decryptFail})`);
  // Compact 1-second timeline so the whole loop is visible at a glance:
  // far-end greeting -> our speech -> far-end reply.
  const buckets = new Map<number, number>();
  for (const r of regions) {
    const s2 = Math.floor(r.atMs / 1000);
    buckets.set(s2, Math.max(buckets.get(s2) ?? 0, r.peak));
  }
  const span = Math.max(seconds, (lastLoudAt - (firstInboundAt ?? Date.now())) / 1000 + 2);
  const spokeAtS = spokeAt === undefined ? undefined : (spokeAt - (firstInboundAt ?? spokeAt)) / 1000;
  const spokeEndS = spokeEndAt === undefined ? undefined : (spokeEndAt - (firstInboundAt ?? spokeEndAt)) / 1000;

  console.log("\nfar-end audio timeline (1s buckets, bar = peak amplitude):");
  for (let s2 = 0; s2 <= span; s2++) {
    const peakV = buckets.get(s2) ?? 0;
    const bar = "#".repeat(Math.round(peakV * 40));
    const marks = [];
    if (spokeAtS !== undefined && s2 === Math.floor(spokeAtS)) marks.push("<-- we start speaking");
    if (spokeEndS !== undefined && s2 === Math.floor(spokeEndS)) marks.push("<-- we stop speaking");
    console.log(
      `  t+${String(s2).padStart(2)}s |${bar.padEnd(42)}| ${peakV.toFixed(3)}${marks.length ? "  " + marks.join(" ") : ""}`,
    );
  }

  const twoWay = spoke && inboundFrames > 0 && peakSeen > 0.02;
  console.log(`\n${twoWay ? "✅ TWO-WAY SPEECH" : "⚠️  one-way or idle"}`);
  process.exitCode = twoWay ? 0 : 1;
}

media.close();
session.bye();
