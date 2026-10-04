/**
 * Google Voice SMS send — `api2thread/sendsms`.
 *
 * Reverse-engineered by capturing the request the Voice web client sends and then
 * bisecting which fields the server actually requires. Body is proto3-JSON, which for this
 * endpoint means a **positional array** — an object with braces is rejected with
 * "JSPB Fava message don't accept top-level braces".
 *
 * Observed field map (1-based, matching array index + 1):
 *
 *   f1..f4  unknown / unused in observed traffic (always null)
 *   f5      sms_message   string   the message text
 *   f6      thread_id     string   "t.<E164>" e.g. "t.+15014086007"; null for brand-new threads
 *   f7      recipients    string[] E164 list; **null when replying inside an existing thread**
 *   f8      unknown (always null)
 *   f9      attachment    message[] whose first subfield is `mime_type` (enum). Carries a
 *                          single, server-issued, single-use message id.
 *   f10     unknown (always null)
 *   f11     envelope      string[] a ~1.6 KB encrypted payload (leading "!"), present on
 *                          client-generated sends
 *   f12+    unknown
 *
 * Reply shape actually captured from the client (11 fields):
 *
 *   [null,null,null,null,"<text>","t.+15014086007",null,null,[145205452443850],null,
 *    ["!<~1.6KB base64>"]]
 *
 * Behaviour confirmed by experiment:
 *
 *  - A verbatim replay from a plain Node client **succeeds**:
 *      [null,"t.+15014086007","<msgId>",<epochMs>,[1,2]]
 *  - `f9` alone is enough, `f11` alone is enough, but **both omitted fails** even when
 *    `sms_message` is set. So the text in f5 is not by itself sufficient.
 *  - `f9` is **not** freely chosen. Values captured from real sends replay fine, but
 *    random / derived values (0, 1, 42, 1.2e14, base+1, base*2, …) are rejected with
 *    INVALID_ARGUMENT. Replaying an already-used id returns the *same* messageId, i.e. f9
 *    behaves as an idempotency key that the server validates as server-issued and
 *    single-use. Treat it as an opaque token obtained from the client flow, not a
 *    counter you can invent.
 */

import { METHODS, type VoiceClient } from "./gv-api.ts";

/** Position of `sms_message` in the positional body (index 4). */
export const F_SMS_MESSAGE = 4;
/** Position of `thread_id`. */
export const F_THREAD_ID = 5;
/** Position of the recipient E164 list. */
export const F_RECIPIENTS = 6;
/** Position of the attachment message carrying the server-issued message id. */
export const F_ATTACHMENT = 8;
/** Position of the encrypted envelope. */
export const F_ENVELOPE = 10;

export interface SendSmsOptions {
  /** Message text (f5). */
  text: string;
  /** Existing thread, "t.<E164>". Omit together with `recipients` for a new conversation. */
  threadId?: string | undefined;
  /** E164 recipients (f7). Only needed when starting a new conversation. */
  recipients?: string[] | undefined;
  /**
   * Server-issued single-use message id (f9). See the note above — this cannot be
   * invented; take it from a real client exchange.
   */
  messageId?: number | undefined;
  /** Encrypted envelope (f11) from a real client exchange. Alternative to `messageId`. */
  envelope?: string[] | undefined;
  /**
   * Set when replaying a captured request verbatim: reuse the original field count so no
   * unknown trailing fields are lost.
   */
  fieldCount?: number | undefined;
}

export interface SendSmsResult {
  threadId: string;
  messageId: string;
  /** Server timestamp, epoch milliseconds. */
  timestampMs: number;
  /** Status codes as returned by the service; [1,2] on success. */
  status: number[];
}

/** Build the positional protojson body for api2thread/sendsms. */
export function buildSendSmsRequest(opts: SendSmsOptions): unknown[] {
  if (!opts.text) throw new Error("text is required");
  if (!opts.threadId && !opts.recipients) {
    throw new Error("need either threadId (reply) or recipients (new conversation)");
  }
  if (opts.messageId === undefined && !opts.envelope) {
    throw new Error(
      "need messageId (f9) or envelope (f11): the server rejects the request without one, " +
        "and messageId cannot be synthesised — see the module comment",
    );
  }

  const size = opts.fieldCount ?? (opts.envelope ? 11 : 9);
  const body: unknown[] = new Array(size).fill(null);
  body[F_SMS_MESSAGE] = opts.text;
  if (opts.threadId) body[F_THREAD_ID] = opts.threadId;
  if (opts.recipients) body[F_RECIPIENTS] = opts.recipients;
  if (opts.messageId !== undefined) body[F_ATTACHMENT] = [opts.messageId];
  if (opts.envelope) body[F_ENVELOPE] = opts.envelope;
  return body;
}

/** Parse the positional response: [null, threadId, messageId, timestampMs, status[]]. */
export function parseSendSmsResponse(raw: unknown): SendSmsResult {
  const r = raw as unknown[] | null;
  const threadId = typeof r?.[1] === "string" ? r[1] : "";
  const messageId = typeof r?.[2] === "string" ? r[2] : "";
  const timestampMs = typeof r?.[3] === "number" ? r[3] : 0;
  const status = Array.isArray(r?.[4]) ? (r[4] as unknown[]).filter((n): n is number => typeof n === "number") : [];
  if (!threadId || !messageId) {
    throw new Error(`unexpected sendsms response: ${JSON.stringify(raw).slice(0, 200)}`);
  }
  return { threadId, messageId, timestampMs, status };
}

/**
 * Send an SMS. Requires either a captured `messageId`/`envelope` or a real thread context —
 * see the module comment for why a bare text+recipient is rejected.
 */
export async function sendSms(client: VoiceClient, opts: SendSmsOptions): Promise<SendSmsResult> {
  const raw = await client.call(METHODS.sendSms, buildSendSmsRequest(opts));
  return parseSendSmsResponse(raw);
}

/** Thread id for an E164 number, as used by the Voice web client. */
export const threadIdFor = (e164: string): string => `t.${e164.replace(/[^\d+]/g, "")}`;