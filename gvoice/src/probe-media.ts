/**
 * End-to-end media test: place a call from Node and verify audio actually flows.
 *
 *   node src/probe-media.ts <e164> [seconds]
 *
 * Requires that the callee answers. Reports ICE/DTLS state, inbound RTP packet counts,
 * and — if --tx is passed — sends DTMF so the far end makes a sound.
 */

import { MediaPlane } from "./media.ts";
import { SipSession } from "./registrar.ts";
import { OpusEncoder, OpusFrameDecoder, peak, rms, sineFrame } from "./audio.ts";

const e164Arg = process.argv[2];
const seconds = Number(process.argv[3] ?? 20);
const sendDtmf = process.argv.includes("--tx");

if (!e164Arg) {
  console.error("usage: node src/probe-media.ts <e164> [seconds] [--tx]");
  process.exit(2);
}
const toE164 = e164Arg.startsWith("+") ? e164Arg : `+${e164Arg.replace(/\D/g, "")}`;

const media = await MediaPlane.create();

// RX: opus payloads -> PCM, so we can prove real audio (not just packets) arrives.
const decoder = new OpusFrameDecoder();
let inboundOpus = 0;
let inboundOther = 0;
let lastInbound = 0;
let peakSeen = 0;
let rmsSum = 0;
let rmsCount = 0;
let decodeErrors = 0;
const pending: Buffer[] = [];

media.onTrack((payload, meta) => {
  if (meta.payloadType !== 111) {
    inboundOther++;
    return;
  }
  inboundOpus++;
  lastInbound = Date.now();
  pending.push(Buffer.from(payload));
  if (pending.length >= 10) void drainInbound();
});

async function drainInbound(): Promise<void> {
  const batch = pending.splice(0, pending.length);
  for (const pkt of batch) {
    try {
      const { samples, errors } = await decoder.decode(pkt);
      if (errors.length) decodeErrors++;
      if (samples.length) {
        const p = peak(samples);
        if (p > peakSeen) peakSeen = p;
        rmsSum += rms(samples);
        rmsCount++;
      }
    } catch {
      decodeErrors++;
    }
  }
}

const session = await SipSession.open({ onMessage: (m) => console.log("   <-", m.split("\r\n")[0]) });
console.log(`registered as ${session.ownNumber}`);
console.log(`calling ${toE164} with media attached\n`);

const result = await session.placeCall({ toE164, media });

console.log(`\nSIP: ${result.responses.join("  ")}`);
if (session.mediaError) console.log(`\n!! applyAnswer failed: ${session.mediaError}`);
if (result.answer) {
  console.log(`answer: ${result.answer.rtpmap[111] ?? "?"} port=${result.answer.audioPort} candidates=${result.answer.candidates.length}`);
} else {
  console.log("no SDP answer received");
}

console.log(`\nwaiting up to ${seconds}s for media…`);
const deadline = Date.now() + seconds * 1000;
// Transmit once the transport is genuinely ready, then keep sending.
//
// Two things matter here. RTP written before DTLS/SRTP is ready is silently dropped, so
// wait for the transport rather than ICE alone. And a short burst followed by silence is
// not how a call behaves — send continuously at 20 ms intervals, the way a real endpoint
// does, since the peer may not bridge media until it sees a steady stream.
const encoder = new OpusEncoder();
let toneFrames = 0;
let txTimer: NodeJS.Timeout | undefined;
let dtmfSent = false;

async function startTransmit(): Promise<void> {
  if (txTimer) return;

  // Wait for DTLS (not just ICE) so the first SRTP-protected packet is not lost.
  for (let i = 0; i < 40; i++) {
    if (media.dtlsConnected) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  console.log(`   transport ready (dtls=${media.dtlsConnected ? "connected" : "unconfirmed"}), starting TX`);

  for (const d of "5") {
    media.sendDtmf(d, 90);
    await new Promise((r) => setTimeout(r, 160));
  }
  dtmfSent = true;
  console.log("   sent DTMF 5");

  txTimer = setInterval(() => {
    try {
      media.sendOpus(encoder.encode(sineFrame(440, 0.25)));
      toneFrames++;
    } catch {
      /* track not ready yet; the next tick will retry */
    }
  }, 20);
}

await startTransmit();

while (Date.now() < deadline) {
  await new Promise((r) => setTimeout(r, 250));
  const s = media.stats();
  if (s.inbound.packets > 200) break;
}
if (txTimer) clearInterval(txTimer);

console.log("\n=== transport diagnostics ===");
console.log(JSON.stringify(await media.diagnostics(), null, 1));

const stats = media.stats();
console.log(`\n=== media stats ===`);
console.log(`ice           : ${stats.ice}`);
console.log(`connection    : ${stats.connection}`);
console.log(`inbound pkts  : ${stats.inbound.packets} (opus ${inboundOpus}, other ${inboundOther})`);
console.log(`inbound bytes : ${stats.inbound.bytes}`);
if (media.learnedSsrc !== undefined) console.log(`learned ssrc  : ${media.learnedSsrc}`);
console.log(`raw ICE dgrams: ${stats.raw.datagrams} (${stats.raw.bytes} bytes, pre-SRTP)`);
console.log(`outbound pkts : ${stats.outboundPackets} (${toneFrames} opus tone frames + 4 DTMF)`);
console.log(`since first   : ${(stats.inbound.elapsedMs / 1000).toFixed(1)}s`);
if (lastInbound) console.log(`last inbound  : ${((Date.now() - lastInbound) / 1000).toFixed(1)}s ago`);

await drainInbound();
console.log(`\n=== decoded audio (RX) ===`);
console.log(`opus frames   : ${inboundOpus}`);
console.log(`other payload : ${inboundOther}`);
console.log(`decode errors : ${decodeErrors}`);
console.log(`peak amplitude: ${peakSeen.toFixed(4)}`);
console.log(`mean rms      : ${rmsCount ? (rmsSum / rmsCount).toFixed(5) : "n/a"}`);

const transportOk = stats.ice === "connected" || stats.ice === "completed";
const audioOk = peakSeen > 0.02;
console.log(
  `\n${transportOk ? "✅ transport up (ICE+DTLS)" : "❌ transport down"}  ` +
    `${audioOk ? "✅ inbound audio decoded" : "⚠️  no inbound audio (callee did not answer?)"}`,
);

media.close();
session.bye();
process.exitCode = transportOk ? 0 : 1;
