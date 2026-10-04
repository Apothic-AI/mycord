/**
 * Outbound Google Voice calls: SDP offer generation and SIP INVITE.
 *
 * The media plane is standard WebRTC — captured offer/answer pairs use plain
 * `UDP/TLS/RTP/SAVPF` with opus/red/telephone-event, ordinary DTLS fingerprints and
 * ICE, and Google's peer is ICE-lite with a directly routable host candidate. So the
 * media stack can be any libwebrtc-based library (werift, pion, aiortc); this module
 * only covers what has to happen *before* that: the SDP offer and the SIP dialog.
 *
 * Captured call flow:
 *
 *   REGISTER → 401 → REGISTER → 200
 *   INVITE  +SDP offer          (m=audio 9, no in-band candidates)
 *     100 Trying
 *     183 Session Progress      Require: 100rel, RSeq: 1  + SDP ANSWER
 *     PRACK  RAck: 1 1 INVITE   (empty body)
 *     200 OK / 180 Ringing
 *   ACK   (empty body)
 *   … DTLS-SRTP + opus …
 *   BYE
 *
 * Because Google's peer is ICE-lite, no candidate trickling is needed: it learns our
 * address from the STUN binding requests we send, which is why the winning candidate
 * pair shows up as `prflx ↔ host`. That is why the offer below uses port 9 (discard).
 *
 * ## GV-proprietary headers
 *
 * A real INVITE also carries three Birdsong-generated headers that are opaque on the
 * wire and have no known derivation:
 *
 *   Route: <sip:[…]:443;transport=udp;lr;uri-econt=<133-char token>>
 *   P-Preferred-Identity: <sip:<base64 blob>>   (decodes to binary protobuf, not E.164)
 *   X-GV-PlaceCallContext: jspb=<~5.5 KB base64>
 *
 * It is UNVERIFIED whether the server enforces these. They are therefore injectable and
 * omitted by default; a 4xx/5xx from the INVITE is the signal that one is mandatory.
 */

import { randomBytes } from "node:crypto";
import { SIP_DOMAIN, REGISTER_STATIC_HEADERS, makeBranch, makeCallId } from "./sip.ts";

const CRLF = "\r\n";

/** Fingerprint algorithm used by Google Voice. */
const DTLS_FINGERPRINT_ALGORITHM = "sha-256";

export interface SdpOptions {
  /** ICE username fragment. Generated if omitted. */
  iceUfrag?: string;
  /** ICE password. Generated if omitted. Must be 22–256 chars. */
  icePwd?: string;
  /** DTLS certificate fingerprint, colon-separated uppercase hex. */
  fingerprint: string;
  /** Audio SSRC. Generated if omitted. */
  ssrc?: number;
  /** Stream id for a=msid. Generated if omitted. */
  streamId?: string;
  /** Track id inside the stream. Generated if omitted. */
  trackId?: string;
  /**
   * Media port. 9 (discard) is what the real client offers, because candidates are not
   * sent in-band. Keep the default unless you intend to embed candidates.
   */
  port?: number;
}

const randomHex = (bytes: number): string => randomBytes(bytes).toString("hex");
const randomUfrag = (): string => randomHex(6);
const randomIcePwd = (): string => randomHex(12); // 24 hex chars, within the 22–256 limit
const randomUuid = (): string => `${randomHex(4)}-${randomHex(2)}-${randomHex(2)}-${randomHex(2)}-${randomHex(6)}`;

/**
 * Build an audio-only WebRTC offer matching what Google Voice's client sends.
 *
 * Deliberately mirrors the captured offer: opus + red + the G722/PCMU/PCMA/CN fallback
 * set, telephone-event, `sendrecv`, rtcp-mux/rsize, the same four extmaps, and BUNDLE.
 */
export function buildOffer(opts: SdpOptions): string {
  const iceUfrag = opts.iceUfrag ?? randomUfrag();
  const icePwd = opts.icePwd ?? randomIcePwd();
  const ssrc = opts.ssrc ?? (Math.floor(Math.random() * 0xffffffff) >>> 0);
  const streamId = opts.streamId ?? randomUuid();
  const trackId = opts.trackId ?? randomUuid();
  const cname = randomHex(8);
  const port = opts.port ?? 9;

  const lines = [
    "v=0",
    `o=- ${randomBytes(8).readBigUInt64BE().toString()} 2 IN IP4 127.0.0.1`,
    "s=-",
    "t=0 0",
    "a=group:BUNDLE 0",
    "a=extmap-allow-mixed",
    `a=msid-semantic: WMS ${streamId}`,
    `m=audio ${port} UDP/TLS/RTP/SAVPF 111 63 9 0 8 13 110 101`,
    "c=IN IP4 0.0.0.0",
    "a=rtpmap:111 opus/48000/2",
    "a=fmtp:111 minptime=10;useinbandfec=1",
    "a=rtpmap:63 red/48000/2",
    "a=fmtp:63 111/111",
    "a=rtpmap:9 G722/8000",
    "a=rtpmap:0 PCMU/8000",
    "a=rtpmap:8 PCMA/8000",
    "a=rtpmap:13 CN/8000",
    "a=rtpmap:110 telephone-event/48000",
    "a=rtpmap:101 telephone-event/8000",
    "a=rtcp:9 IN IP4 0.0.0.0",
    `a=ice-ufrag:${iceUfrag}`,
    `a=ice-pwd:${icePwd}`,
    "a=ice-options:trickle",
    `a=fingerprint:${DTLS_FINGERPRINT_ALGORITHM} ${opts.fingerprint}`,
    "a=setup:actpass",
    "a=mid:0",
    "a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level",
    "a=extmap:2 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
    "a=extmap:3 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
    "a=extmap:4 urn:ietf:params:rtp-hdrext:sdes:mid",
    "a=sendrecv",
    `a=msid:${streamId} ${trackId}`,
    "a=rtcp-mux",
    "a=rtcp-rsize",
    "a=rtcp-xr:rcvr-rtt=all",
    `a=ssrc:${ssrc} cname:${cname}`,
    `a=ssrc:${ssrc} msid:${streamId} ${trackId}`,
    "a=rtcp-fb:111 transport-cc",
    "",
  ];
  return lines.join(CRLF);
}

/** Pull the SDP body out of a SIP message, or undefined if there is none. */
export function extractSdp(message: string): string | undefined {
  const sep = message.indexOf(`${CRLF}${CRLF}`);
  if (sep === -1) return undefined;
  const body = message.slice(sep + 4).trim();
  return body.startsWith("v=0") ? body : undefined;
}

/** Parse the useful bits out of Google's answer. */
export interface RemoteDescription {
  fingerprint?: string;
  setup?: string;
  iceUfrag?: string;
  icePwd?: string;
  candidates: string[];
  audioPort?: number;
  payloads: number[];
  rtpmap: Record<string, string>;
}

export function parseAnswer(sdp: string): RemoteDescription {
  const out: RemoteDescription = { candidates: [], payloads: [], rtpmap: {} };
  for (const line of sdp.split(/\r?\n/)) {
    if (line.startsWith("a=fingerprint:")) {
      out.fingerprint = line.slice("a=fingerprint:".length).trim();
    } else if (line.startsWith("a=setup:")) {
      out.setup = line.slice("a=setup:".length).trim();
    } else if (line.startsWith("a=ice-ufrag:")) {
      out.iceUfrag = line.slice("a=ice-ufrag:".length).trim();
    } else if (line.startsWith("a=ice-pwd:")) {
      out.icePwd = line.slice("a=ice-pwd:".length).trim();
    } else if (line.startsWith("a=candidate:")) {
      out.candidates.push(line.slice("a=candidate:".length).trim());
    } else if (line.startsWith("a=rtpmap:")) {
      const [pt, enc] = line.slice("a=rtpmap:".length).split(" ");
      if (pt && enc) out.rtpmap[pt] = enc.trim();
    } else if (line.startsWith("m=audio")) {
      const parts = line.split(/\s+/);
      const p = Number(parts[1]);
      if (Number.isFinite(p)) out.audioPort = p;
      out.payloads = (parts[3] ?? "").split(" ").map(Number).filter(Number.isFinite);
    }
  }
  return out;
}

export interface InviteOptions {
  /** Local identity. Unlike REGISTER this is NOT the SIP credential — see module notes. */
  fromUser: string;
  /** Callee in E.164, e.g. "+18002758777". */
  toE164: string;
  /** SDP offer from `buildOffer`. */
  sdp: string;
  /** Authorization header value from the REGISTER exchange. */
  auth?: string | undefined;
  /** Opaque per-call From tag. Generated if omitted. */
  fromTag?: string;
  callId?: string;
  branch?: string;
  viaHost?: string;
  /** `Route` header, verbatim. Omitted when absent. */
  route?: string | undefined;
  /** `P-Preferred-Identity`, verbatim. Omitted when absent. */
  preferredIdentity?: string | undefined;
  /** `X-GV-PlaceCallContext`, verbatim. Omitted when absent. */
  placeCallContext?: string | undefined;
}

/** Build the INVITE request carrying an SDP offer. */
export function buildInvite(opts: InviteOptions): string {
  const viaHost = opts.viaHost ?? randomHex(5).toUpperCase().slice(0, 10);
  const callId = opts.callId ?? `${makeCallId()}_${Date.now()}`;
  const fromTag = opts.fromTag ?? randomHex(4);
  const body = opts.sdp.endsWith(CRLF) ? opts.sdp : opts.sdp + CRLF;

  const headers = [
    `INVITE sip:${opts.toE164}@${SIP_DOMAIN} SIP/2.0`,
    // Note: the captured client uses ";rport" here, not the ";rport;keep" of REGISTER.
    `Via: SIP/2.0/WSS ${viaHost}.invalid:5061;branch=${opts.branch ?? makeBranch()};rport`,
    "Max-Forwards: 70",
  ];
  if (opts.route) headers.push(`Route: ${opts.route}`);
  headers.push(
    `To: <sip:${opts.toE164}@${SIP_DOMAIN}>`,
    `From: <sip:${opts.fromUser}@${SIP_DOMAIN}>;tag=${fromTag}`,
    `Call-ID: ${callId}`,
    "CSeq: 1 INVITE",
    `Allow: ${REGISTER_STATIC_HEADERS.Allow}`,
    "Content-Type: application/sdp",
    // 100rel is mandatory: the answer arrives on a 183 which must be PRACKed.
    "Supported: outbound, path, record-aware, replaces, 100rel",
    `User-Agent: ${REGISTER_STATIC_HEADERS["User-Agent"]}`,
  );
  if (opts.preferredIdentity) headers.push(`P-Preferred-Identity: ${opts.preferredIdentity}`);
  if (opts.placeCallContext) headers.push(`X-GV-PlaceCallContext: ${opts.placeCallContext}`);
  headers.push(`X-Google-Client-Info: ${REGISTER_STATIC_HEADERS["X-Google-Client-Info"]}`);
  if (opts.auth) headers.push(`Authorization: ${opts.auth}`);
  headers.push(`Content-Length: ${Buffer.byteLength(body, "utf8")}`);

  return headers.join(CRLF) + CRLF + CRLF + body;
}

/** Build the PRACK that acknowledges a `183 Session Progress` (empty body). */
export function buildPrack(opts: { requestUri: string; from: string; to: string; callId: string; cseq: number; rseq: number }): string {
  const viaHost = randomHex(5).toUpperCase().slice(0, 10);
  const lines = [
    `PRACK ${opts.requestUri} SIP/2.0`,
    `Via: SIP/2.0/WSS ${viaHost}.invalid:5061;branch=${makeBranch()};rport`,
    "Max-Forwards: 70",
    `To: ${opts.to}`,
    `From: ${opts.from}`,
    `Call-ID: ${opts.callId}`,
    `CSeq: ${opts.cseq} PRACK`,
    `RAck: ${opts.rseq} 1 INVITE`,
    "Content-Length: 0",
  ];
  return lines.join(CRLF) + CRLF + CRLF;
}

/** Build the in-dialog ACK for a 2xx INVITE response (empty body). */
export function buildAck(opts: { requestUri: string; from: string; to: string; callId: string }): string {
  const viaHost = randomHex(5).toUpperCase().slice(0, 10);
  const lines = [
    `ACK ${opts.requestUri} SIP/2.0`,
    `Via: SIP/2.0/WSS ${viaHost}.invalid:5061;branch=${makeBranch()};rport`,
    "Max-Forwards: 70",
    `To: ${opts.to}`,
    `From: ${opts.from}`,
    `Call-ID: ${opts.callId}`,
    "CSeq: 1 ACK",
    "Content-Length: 0",
  ];
  return lines.join(CRLF) + CRLF + CRLF;
}