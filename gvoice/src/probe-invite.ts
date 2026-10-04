/**
 * Place a Google Voice call from a plain Node client and report how far the SIP dialog gets.
 *
 *   node src/probe-invite.ts <e164> [fingerprint]
 *
 * By default the three Birdsong-generated headers (Route uri-econt,
 * P-Preferred-Identity, X-GV-PlaceCallContext) are OMITTED. The point of this probe is to
 * find out whether the server actually enforces them.
 *
 *   GV_PLCONTEXT=<base64>  supply X-GV-PlaceCallContext
 *   GV_ROUTE=<sip uri>     supply the Route header
 *   GV_PREFERRED=<sip uri> supply P-Preferred-Identity
 *   GV_VERBOSE=1           dump every SIP message
 *
 * A DTLS fingerprint is required for the SDP offer. Supply one that matches the certificate
 * your media stack will actually present, or the peer will complete signaling and then fail
 * the DTLS handshake. The default below is a placeholder that is enough to see how far the
 * dialog gets.
 */

import { SipSession } from "./registrar.ts";

const e164 = process.argv[2];
const fingerprint =
  process.argv[3] ??
  "11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00";

if (!e164) {
  console.error("usage: node src/probe-invite.ts <e164> [dtls-fingerprint]");
  process.exit(2);
}

const VERBOSE = process.env.GV_VERBOSE === "1";
const log = (dir: "->" | "<-", msg: string): void => {
  if (VERBOSE) console.log(`\n${dir} ${msg.replace(/\r\n/g, "\\n")}\n`);
  else console.log(`   ${dir} ${msg.split("\r\n")[0] ?? ""}`);
};

const session = await SipSession.open({ onMessage: (m) => log("<-", m) });

console.log(`registered as ${session.ownNumber}`);
console.log(`calling ${e164} (no GV-proprietary headers)\n`);

const result = await session.placeCall({
  toE164: e164.startsWith("+") ? e164 : `+${e164.replace(/\D/g, "")}`,
  fingerprint,
  routes: process.env.GV_ROUTE ? [process.env.GV_ROUTE] : undefined,
  preferredIdentity: process.env.GV_PREFERRED,
  placeCallContext: process.env.GV_PLCONTEXT,
});

console.log(`\nresponses: ${result.responses.join("  ")}`);
if (result.answer) {
  console.log("\nSDP answer received:");
  console.log(`  setup      : ${result.answer.setup}`);
  console.log(`  audio port : ${result.answer.audioPort}`);
  console.log(`  payloads   : ${result.answer.payloads.join(", ")}`);
  console.log(`  rtpmap     : ${JSON.stringify(result.answer.rtpmap)}`);
  console.log(`  ice ufrag  : ${result.answer.iceUfrag}`);
  console.log(`  candidates : ${result.answer.candidates.length}`);
  for (const c of result.answer.candidates) console.log(`      ${c}`);
  console.log(`\n  => signaling SUCCEEDED; media would now need DTLS-SRTP against ${result.answer.audioPort}`);
} else {
  console.log("\nno SDP answer — see the responses above for where it stopped.");
}

session.bye();
