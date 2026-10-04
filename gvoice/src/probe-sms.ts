/**
 * Send an SMS from a plain Node client using a captured client exchange.
 *
 *   node src/probe-sms.ts <e164> "<text>"
 *   node src/probe-sms.ts <e164> "<text>" /path/to/sendsms-capture.json
 *
 * With a capture file, the captured `messageId` (f9) / `envelope` (f11) are reused, which is
 * the only way to satisfy the server's validation — see src/sms.ts for why.
 */

import { readFileSync, existsSync } from "node:fs";
import { VoiceClient } from "./gv-api.ts";
import { F_ATTACHMENT, F_ENVELOPE, sendSms, threadIdFor } from "./sms.ts";

const [e164, text, capturePath] = process.argv.slice(2);
if (!e164 || !text) {
  console.error('usage: node src/probe-sms.ts <e164> "<text>" [capture.json]');
  process.exit(2);
}

const capture = capturePath && existsSync(capturePath)
  ? (JSON.parse(readFileSync(capturePath, "utf8")) as unknown[])
  : null;

if (!capture) {
  console.error(
    "no capture supplied — the server rejects sendsms without a server-issued messageId (f9)\n" +
      "or the encrypted envelope (f11), and those cannot be synthesised. See src/sms.ts.",
  );
  process.exit(2);
}

const messageId = (capture[F_ATTACHMENT] as number[] | undefined)?.[0];
const envelope = capture[F_ENVELOPE] as string[] | undefined;

const result = await sendSms(new VoiceClient(), {
  text,
  threadId: threadIdFor(e164),
  messageId,
  envelope,
  fieldCount: capture.length,
});

console.log("sent ok");
console.log(`  thread   : ${result.threadId}`);
console.log(`  messageId: ${result.messageId}`);
console.log(`  at       : ${new Date(result.timestampMs).toISOString()}`);
console.log(`  status   : ${JSON.stringify(result.status)}`);
