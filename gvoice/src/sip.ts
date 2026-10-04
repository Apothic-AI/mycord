/**
 * Minimal SIP client for Google Voice's SIP-over-WebSocket transport.
 *
 * Google Voice signals over RFC 7118 ("SIP and SDP over Secure WebSockets"):
 *
 *   wss://web.voice.telephony.goog:443/websocket      subprotocol: "sip"
 *   registrar / target domain: web.c.pbx.voice.sip.google.com
 *
 * Observed flow for an outbound call:
 *
 *   REGISTER sip:web.c.pbx.voice.sip.google.com   ->  401 Unauthorized  (digest challenge)
 *   REGISTER (with digest response)               ->  200 OK
 *   INVITE  sip:+1…@web.c.pbx.voice.sip.google.com -> 100 Trying / 183 Session Progress /
 *                                                     180 Ringing / 200 OK
 *   PRACK / ACK
 *
 * Credentials come from VoiceClientService.GetSipRegisterInfo. Note these are for the
 * WebSocket registrar, *not* for the long-dead UDP gateway on 5060/5061.
 */

import { createHash, randomBytes } from "node:crypto";
import WebSocket from "ws";
import { clientInfoHeader, resolveClientInfo } from "./client-info.ts";

export const SIP_WS_URL = "wss://web.voice.telephony.goog:443/websocket";
export const SIP_DOMAIN = "web.c.pbx.voice.sip.google.com";
export const SIP_SUBPROTOCOL = "sip";
const CRLF = "\r\n";

export interface SipCredentials {
  /** Digest username from GetSipRegisterInfo. */
  username: string;
  /** Digest password from GetSipRegisterInfo. */
  password: string;
}

export interface SipChallenge {
  realm: string;
  nonce: string;
  opaque?: string;
  algorithm?: string;
  qop?: string;
  domain?: string;
  stale?: boolean;
}

/**
 * Credentials as returned by GetSipRegisterInfo.
 *
 * Observed shape: [["<expiryTs>", <num>], null, null, ["<b64 user>", "<b64 password>"]]
 */
export function parseSipRegisterInfo(raw: unknown): SipCredentials & { expiry?: number } {
  const fields = raw as unknown[] | null;
  const pair = fields?.[3];
  if (!Array.isArray(pair) || typeof pair[0] !== "string" || typeof pair[1] !== "string") {
    throw new Error(`unexpected GetSipRegisterInfo shape: ${JSON.stringify(raw).slice(0, 300)}`);
  }
  const expiry = Array.isArray(fields?.[0]) ? (fields[0][0] as number | undefined) : undefined;
  return { username: pair[0], password: pair[1], ...(expiry === undefined ? {} : { expiry }) };
}

/** Parse a SIP WWW-Authenticate / Proxy-Authenticate header value. */
export function parseChallenge(header: string): SipChallenge {
  const challenge: SipChallenge = { realm: "", nonce: "" };
  // Strip the leading scheme token ("Digest" / "MD5" / "MD5-sess" / whatever Google sends).
  const body = header.replace(/^\s*\S+\s+/, "");
  for (const match of body.matchAll(/(\w+)\s*=\s*(?:"([^"]*)"|([^\s,]+))/g)) {
    const key = (match[1] ?? "").toLowerCase();
    const value = match[2] ?? match[3] ?? "";
    switch (key) {
      case "realm": challenge.realm = value; break;
      case "nonce": challenge.nonce = value; break;
      case "opaque": challenge.opaque = value; break;
      case "algorithm": challenge.algorithm = value; break;
      case "qop": challenge.qop = value; break;
      case "domain": challenge.domain = value; break;
      case "stale": challenge.stale = value.toLowerCase() === "true"; break;
      default: break;
    }
  }
  if (!challenge.nonce) throw new Error(`challenge missing nonce: ${header.slice(0, 200)}`);
  return challenge;
}

/** True if the challenge advertises the given qop value (e.g. "auth", "auth-int"). */
function challengeHasQop(challenge: SipChallenge, want: string): boolean {
  if (!challenge.qop) return false;
  return challenge.qop
    .split(",")
    .map((q) => q.trim().toLowerCase())
    .includes(want);
}

const md5 = (input: string | Buffer): Buffer => createHash("md5").update(input).digest();

export interface DigestInput {
  challenge: SipChallenge;
  username: string;
  password: string;
  method: string;
  uri: string;
  body?: string;
  /** Force a qop value; otherwise auto-select from the challenge. */
  qop?: "auth" | "auth-int";
  cnonce?: string;
  nc?: string;
}

/**
 * Compute the SIP digest Authorization header value.
 *
 * Google offers qop=auth-int on this endpoint, which hashes the entity body into HA2.
 * Both variants are implemented; prefer auth-int when advertised.
 */
export function buildDigest(input: DigestInput): { header: string; usedQop: string | null } {
  const { challenge, username, password, method, uri } = input;
  const body = input.body ?? "";
  const cnonce = input.cnonce ?? randomBytes(8).toString("hex");
  const nc = input.nc ?? "00000001";

  const qop =
    input.qop ??
    (challengeHasQop(challenge, "auth-int")
      ? "auth-int"
      : challengeHasQop(challenge, "auth")
        ? "auth"
        : undefined);
  const usedQop = qop ?? null;

  const ha1 = md5(`${username}:${challenge.realm}:${password}`);
  const ha2 =
    qop === "auth-int"
      ? md5(`${method}:${uri}:${md5(body).toString("hex")}`)
      : md5(`${method}:${uri}`);

  const response = usedQop
    ? md5(`${ha1.toString("hex")}:${challenge.nonce}:${nc}:${cnonce}:${usedQop}:${ha2.toString("hex")}`).toString("hex")
    : md5(`${ha1.toString("hex")}:${challenge.nonce}:${ha2.toString("hex")}`).toString("hex");

  const parts = [
    `username="${username}"`,
    `realm="${challenge.realm}"`,
    `nonce="${challenge.nonce}"`,
    `uri="${uri}"`,
    `response="${response}"`,
  ];
  if (challenge.algorithm) parts.push(`algorithm=${challenge.algorithm}`);
  if (usedQop) parts.push(`qop=${usedQop}`, `nc=${nc}`, `cnonce="${cnonce}"`);
  if (challenge.opaque) parts.push(`opaque="${challenge.opaque}"`);

  return { header: `Digest ${parts.join(", ")}`, usedQop };
}

/** RFC 3261 magic-cookie branch id. Google's client uses `z9hG4bK-<num>-1---<hex>`. */
export function makeBranch(salt?: string): string {
  return salt ?? `z9hG4bK-${randomBytes(5).toString("hex")}`;
}

/**
 * Static headers the Voice client sends on every REGISTER, which the registrar appears
 * to validate. Captured from the real web client.
 */
/** "<client name/build>/<engine version>", e.g. "GoogleVoice voice.web-frontend_…/Birdsong v…". */
export const REGISTER_USER_AGENT = (() => {
  const parts = resolveClientInfo();
  return `${parts.client}/${parts.engine}`;
})();

export const REGISTER_STATIC_HEADERS = {
  Allow: "INVITE, UPDATE, ACK, CANCEL, OPTIONS, BYE, PRACK, NOTIFY",
  Supported: "outbound, path, record-aware, replaces",
  "User-Agent": REGISTER_USER_AGENT,
  // Resolved from parts + env overrides (see client-info.ts) so build/engine/browser
  // versions track reality instead of being frozen into the source.
  "X-Google-Client-Info": clientInfoHeader(),
} as const;

/** Contact parameters the registrar expects on REGISTER. */
export function registerContactParams(instanceId: string, regId = 1): Record<string, string> {
  return {
    "+sip.instance": `"<urn:uuid:${instanceId}>"`,
    "reg-id": String(regId),
    "+g.3gpp.icsi-ref": '"urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel"',
    "device-id": `"${randomBytes(6).toString("hex")}"`,
  };
}

export interface SipMessageOptions {
  method: string;
  uri: string;
  from: string;
  to: string;
  callId: string;
  branch: string;
  cseq: number;
  /** Local contact user; defaults to the user part of `from`. */
  contactUser?: string;
  /** Full Contact header, overriding the generated one. */
  contact?: string;
  /** Full Authorization header value, when present. */
  auth?: string | undefined;
  extra?: Record<string, string>;
  body?: string;
  /** Random host token; Google's own client uses a throwaway name with a .invalid TLD. */
  viaHost?: string;
}

export function buildMessage(opts: SipMessageOptions): string {
  const viaHost = opts.viaHost ?? "gvoice";

  // Contact must carry the *user* part plus our local host, e.g.
  //   <sip:+14153356728@gvoice.invalid:5061;transport=ws>
  // Deriving it from the full From URI would produce a second "@" and get a 400.
  const contactUser =
    opts.contactUser ??
    (/^<sips?:([^@>]+)@/.exec(opts.from)?.[1] ?? opts.from.replace(/^<sips?:|>$/g, ""));

  const lines = [
    `${opts.method} ${opts.uri} SIP/2.0`,
    `Via: SIP/2.0/WSS ${viaHost}.invalid:5061;branch=${opts.branch};rport;keep`,
    "Max-Forwards: 70",
    `From: ${opts.from}`,
    `To: ${opts.to}`,
    `Call-ID: ${opts.callId}`,
    `CSeq: ${opts.cseq} ${opts.method}`,
    `Contact: ${opts.contact ?? `<sip:${contactUser}@${viaHost}.invalid:5061;transport=ws>`}`,
  ];
  if (opts.auth) lines.push(`Authorization: ${opts.auth}`);
  for (const [k, v] of Object.entries(opts.extra ?? {})) lines.push(`${k}: ${v}`);
  lines.push("Content-Length: 0");
  return lines.join(CRLF) + CRLF + CRLF + (opts.body ?? "");
}

export function makeCallId(): string {
  return randomBytes(16).toString("hex");
}

/**
 * Parse a SIP message's headers into a case-insensitive multi-map.
 *
 * Prefer this over ad-hoc regexes: a regex like /(^|\r\n)To:(.*)/ captures the leading
 * CRLF whenever the `^` branch does not match, and injecting that straight into an
 * outgoing message produces a malformed header (Google answers such a PRACK with a 400
 * and an empty To:). List headers (Route, Record-Route, Via) legitimately repeat, hence
 * the array values.
 */
export function parseHeaders(message: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  const sep = message.indexOf("\r\n\r\n");
  const block = sep === -1 ? message : message.slice(0, sep);
  for (const line of block.split("\r\n")) {
    // Folded continuation lines start with a space/tab; append to the previous header.
    if (/^[ \t]/.test(line) && out.size > 0) {
      const keys = [...out.keys()];
      const lastKey = keys[keys.length - 1];
      if (lastKey) {
        const arr = out.get(lastKey);
        if (arr) arr[arr.length - 1] = `${arr[arr.length - 1]} ${line.trim()}`;
      }
      continue;
    }
    const idx = line.indexOf(":");
    if (idx <= 0) continue; // request/status line, or malformed
    const name = line.slice(0, idx).trim().toLowerCase();
    const value = line.slice(idx + 1).trim();
    const arr = out.get(name);
    if (arr) arr.push(value);
    else out.set(name, [value]);
  }
  return out;
}

/** First value of a header, or undefined. */
export function header(message: string, name: string): string | undefined {
  return parseHeaders(message).get(name.toLowerCase())?.[0];
}

/** All values of a possibly-repeated header. */
export function headers(message: string, name: string): string[] {
  return parseHeaders(message).get(name.toLowerCase()) ?? [];
}

/** Split a buffer that may contain several concatenated SIP messages. */
export function splitMessages(raw: string): string[] {
  const out: string[] = [];
  let rest = raw;
  while (rest.length > 0) {
    const start = rest.search(/^(SIP\/2\.0 |[A-Z]+ sip)/m);
    if (start === -1) break;
    const bodyStart = rest.indexOf(CRLF + CRLF, start);
    if (bodyStart === -1) break;
    const headerEnd = bodyStart + 4;
    const headerBlock = rest.slice(start, bodyStart);
    const lenMatch = /\r\nContent-Length:\s*(\d+)/i.exec(headerBlock);
    const bodyLen = lenMatch ? parseInt(lenMatch[1] ?? "0", 10) : 0;
    out.push(rest.slice(start, headerEnd + bodyLen));
    rest = rest.slice(headerEnd + bodyLen);
  }
  return out;
}

export interface SipTransportOptions {
  onMessage: (msg: string) => void;
  onOpen?: () => void;
  onClose?: (code: number, reason: string) => void;
  onError?: (err: Error) => void;
  timeoutMs?: number;
  /** Override the Origin sent on the HTTP upgrade. */
  origin?: string;
}

/**
 * Origin sent on the WebSocket upgrade.
 *
 * The endpoint rejects the handshake with 403 unless this is present — the same
 * "requests must look like they came from Voice" rule that governs the HTTP API's
 * referrer restriction.
 */
export const SIP_WS_ORIGIN = "https://voice.google.com";

/** Open the SIP-over-WebSocket transport and keep it alive. */
export function openSipTransport(opts: SipTransportOptions): WebSocket {
  const ws = new WebSocket(SIP_WS_URL, [SIP_SUBPROTOCOL], {
    origin: opts.origin ?? SIP_WS_ORIGIN,
  });
  const timer = setTimeout(() => {
    ws.close(4000, "sip transport open timeout");
  }, opts.timeoutMs ?? 15_000);

  ws.on("open", () => {
    clearTimeout(timer);
    opts.onOpen?.();
  });
  ws.on("message", (data: WebSocket.RawData) => {
    const text = typeof data === "string" ? data : Buffer.isBuffer(data) ? data.toString("utf8") : Buffer.from(data as ArrayBuffer).toString("utf8");
    for (const msg of splitMessages(text)) opts.onMessage(msg);
  });
  ws.on("close", (code, reason) => opts.onClose?.(code, reason.toString()));
  ws.on("error", (err) => opts.onError?.(err as Error));
  return ws;
}