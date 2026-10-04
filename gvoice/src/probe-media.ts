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

const e164Arg = process.argv[2];
const seconds = Number(process.argv[3] ?? 20);
const sendDtmf = process.argv.includes("--tx");

if (!e164Arg) {
  console.error("usage: node src/probe-media.ts <e164> [seconds] [--tx]");
  process.exit(2);
}
const toE164 = e164Arg.startsWith("+") ? e164Arg : `+${e164Arg.replace(/\D/g, "")}`;

const media = await MediaPlane.create();

let inboundOpus = 0;
let inboundOther = 0;
let lastInbound = 0;
media.onTrack((payload, meta) => {
  if (meta.payloadType === 111) {
    inboundOpus++;
    lastInbound = Date.now();
  } else {
    inboundOther++;
  }
});

const session = await SipSession.open({ onMessage: (m) => console.log("   <-", m.split("\r\n")[0]) });
console.log(`registered as ${session.ownNumber}`);
console.log(`calling ${toE164} with media attached\n`);

const result = await session.placeCall({ toE164, media });

console.log(`\nSIP: ${result.responses.join("  ")}`);
if (result.answer) {
  console.log(`answer: ${result.answer.rtpmap[111] ?? "?"} port=${result.answer.audioPort} candidates=${result.answer.candidates.length}`);
} else {
  console.log("no SDP answer received");
}

console.log(`\nwaiting up to ${seconds}s for media…`);
const deadline = Date.now() + seconds * 1000;
let dtmfSent = false;

// Transmit a short DTMF burst once the transport is up. Google's media engine may not
// emit anything until it sees inbound RTP, so sending first avoids a standstill.
async function kickIfReady(): Promise<void> {
  const s = media.stats();
  if (s.ice !== "connected" && s.ice !== "completed") return;
  if (media.stats().outboundPackets > 0) return;
  for (const d of "5555") {
    media.sendDtmf(d, 80);
    await new Promise((r) => setTimeout(r, 140));
  }
  console.log(`   sent DTMF 5 5 5 5 after ICE ${s.ice}`);
  dtmfSent = true;
}

while (Date.now() < deadline) {
  await new Promise((r) => setTimeout(r, 250));
  const s = media.stats();
  if (!dtmfSent && (sendDtmf || true)) await kickIfReady();
  if (s.inbound.packets > 200) break;
}

console.log("\n=== transport diagnostics ===");
console.log(JSON.stringify(await media.diagnostics(), null, 1));

const stats = media.stats();
console.log(`\n=== media stats ===`);
console.log(`ice           : ${stats.ice}`);
console.log(`connection    : ${stats.connection}`);
console.log(`inbound pkts  : ${stats.inbound.packets} (opus ${inboundOpus}, other ${inboundOther})`);
console.log(`inbound bytes : ${stats.inbound.bytes}`);
console.log(`outbound pkts : ${stats.outboundPackets}`);
console.log(`since first   : ${(stats.inbound.elapsedMs / 1000).toFixed(1)}s`);
if (lastInbound) console.log(`last inbound  : ${((Date.now() - lastInbound) / 1000).toFixed(1)}s ago`);

const ok = stats.ice === "completed" && stats.inbound.packets > 0;
console.log(`\n${ok ? "✅ MEDIA FLOWING" : "❌ no media"}`);

media.close();
session.bye();
process.exitCode = ok ? 0 : 1;
