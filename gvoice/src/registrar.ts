/**
 * Google Voice SIP session: REGISTER, then place and tear down calls.
 *
 * Wraps the SIP/WSS transport from `sip.ts` and the dialog builders from `call.ts` into a
 * small state machine, so callers do not have to track CSeq/branch/tag bookkeeping.
 *
 * Transport notes that matter (all verified against the live registrar):
 *  - the WebSocket upgrade needs `Origin: https://voice.google.com`
 *  - the first REGISTER carries `nonce="" response=""` as a cheap "is auth needed" probe
 *  - the digest is RFC 2069 style: `algorithm=MD5`, no `qop`, no cnonce
 *  - registration carries the authentication; INVITE/ACK/BYE do not re-authenticate
 *  - the answer arrives on a `183 Session Progress` with `Require: 100rel` and must be
 *    PRACKed before the call proceeds
 */

import { randomBytes, randomUUID } from "node:crypto";
import type WebSocket from "ws";
import { VoiceClient, METHODS } from "./gv-api.ts";
import {
  REGISTER_STATIC_HEADERS,
  buildDigest,
  buildMessage,
  makeBranch,
  makeCallId,
  openSipTransport,
  parseChallenge,
  parseHeaders,
  parseSipRegisterInfo,
  registerContactParams,
  type SipChallenge,
} from "./sip.ts";
import {
  buildAck,
  buildInvite,
  buildOffer,
  buildPrack,
  extractSdp,
  parseAnswer,
  type RemoteDescription,
} from "./call.ts";
import { SIP_DOMAIN } from "./sip.ts";
import { MediaPlane } from "./media.ts";

export interface PlaceCallOptions {
  /** Callee in E.164, e.g. "+18002758777". */
  toE164: string;
  /**
   * Media plane to use. When supplied its offer is used and its answer is applied, so the
   * SDP and the DTLS certificate always agree. When omitted, signalling proceeds with a
   * hand-built offer and no media — useful for probing the dialog only.
   */
  media?: MediaPlane | undefined;
  /**
   * DTLS fingerprint for the hand-built offer. Only used when `media` is absent; it must
   * match the certificate actually presented during the DTLS handshake.
   */
  fingerprint?: string | undefined;
  /** Optional overrides for the SDP offer (hand-built path only). */
  sdp?: Parameters<typeof buildOffer>[0] | undefined;
  /**
   * GV-proprietary headers, verbatim from a real client exchange. Omitted by default —
   * whether the server enforces them is unverified.
   */
  routes?: string[] | undefined;
  preferredIdentity?: string | undefined;
  placeCallContext?: string | undefined;
}

export interface PlaceCallResult {
  /** Answer SDP from the 183, if one arrived. */
  answer?: RemoteDescription;
  /** The media plane in use, when one was supplied. */
  media?: MediaPlane;
  /** Final response line for the INVITE, e.g. "SIP/2.0 200 OK". */
  finalResponse?: string;
  /** Every status line seen, in order. */
  responses: string[];
}

export interface SipSessionOptions {
  /** Called for each inbound SIP message (already split from any batch). */
  onMessage?: (msg: string) => void;
  /** Milliseconds to allow for the whole placeCall() exchange. */
  callTimeoutMs?: number;
}

/** Own number, SIP identity and digest password, from the HTTP control plane. */
async function resolveIdentity(
  client: VoiceClient,
): Promise<{ e164: string; sipUser: string; sipPassword: string }> {
  const account = (await client.call(METHODS.accountGet, [null, 1])) as unknown[];
  const first = account?.[0];
  const number = Array.isArray(first) ? first[0] : first;
  if (typeof number !== "string") {
    throw new Error(`could not read own number from account/get: ${JSON.stringify(account).slice(0, 160)}`);
  }
  const reg = await client.call(METHODS.sipRegisterInfoGet, [3, "F6IjFoL0Avfz"]);
  const creds = parseSipRegisterInfo(reg);
  return { e164: number, sipUser: creds.username, sipPassword: creds.password };
}

export class SipSession {
  private readonly opts: SipSessionOptions;
  /** Stable handle on the caller-supplied callback; placeCall() must not reassign it. */
  private readonly emit: (msg: string) => void;

  private ws?: WebSocket;
  private identity?: { e164: string; sipUser: string; sipPassword: string };
  private readonly viaHost = randomBytes(5).toString("hex").toUpperCase().slice(0, 10);
  private contact =
    `<sip:PLACEHOLDER@[::]:5061;transport=wss>;`;

  /** Opens the transport and completes the REGISTER exchange. */
  static async open(opts: SipSessionOptions = {}): Promise<SipSession> {
    const session = new SipSession(opts);
    await session.connect();
    return session;
  }

  private constructor(opts: SipSessionOptions) {
    this.opts = opts;
    this.emit = opts.onMessage ?? (() => {});
  }

  get ownNumber(): string {
    if (!this.identity) throw new Error("not connected");
    return this.identity.e164;
  }

  /** Route an inbound message to the active dialog, or the caller callback. */
  private dispatch(msg: string): void {
    if (this.dialogHandler) this.dialogHandler(msg);
    else this.emit(msg);
  }

  private async connect(): Promise<void> {
    const client = new VoiceClient();
    const identity = await resolveIdentity(client);
    this.identity = identity;

    const params = registerContactParams(randomUUID());
    const contactUser = identity.sipUser;
    this.contact =
      `<sip:${contactUser}@[::]:5061;transport=wss>;` +
      Object.entries(params).map(([k, v]) => `${k}=${v}`).join(";");

    const callId = `${makeCallId()}_${Date.now()}`;
    const uri = `sip:${SIP_DOMAIN}`;
    const from = `<sip:${contactUser}@${SIP_DOMAIN}>`;
    const to = from;

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const finish = (err?: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(guard);
        err ? reject(err) : resolve();
      };
      const guard = setTimeout(() => finish(new Error("register timed out")), 30_000);

      const send = (cseq: number, auth: string | undefined) => {
        this.ws?.send(
          buildMessage({
            method: "REGISTER",
            uri,
            from,
            to,
            callId,
            branch: `z9hG4bK-524287-1---${randomBytes(8).toString("hex")}`,
            cseq,
            contact: this.contact,
            viaHost: this.viaHost,
            auth,
            extra: { ...REGISTER_STATIC_HEADERS, Expires: "3600" },
          }),
        );
      };

      this.ws = openSipTransport({
        onOpen: () => {
          // Empty-nonce probe first, exactly as the real client does.
          send(1, `Digest username="${contactUser}",realm="${SIP_DOMAIN}",uri="${uri}",nonce="",response=""`);
        },
        onMessage: (msg) => {
          this.dispatch(msg);
          if (msg.startsWith("SIP/2.0 401")) {
            const raw = /(^|\r\n)WWW-Authenticate:([^\r\n]*)(\r\n|$)/i.exec(msg)?.[2] ?? "";
            const challenge: SipChallenge = parseChallenge(raw);
            send(
              2,
              buildDigest({
                challenge,
                username: contactUser,
                password: identity.sipPassword,
                method: "REGISTER",
                uri,
              }).header,
            );
            return;
          }
          if (msg.startsWith("SIP/2.0 200")) {
            finish();
            return;
          }
          const code = /^SIP\/2\.0 (\d{3})/.exec(msg)?.[1];
          if (code) finish(new Error(`register rejected: ${msg.split("\r\n")[0]}`));
        },
        onError: (err) => finish(new Error(`transport error: ${err.message}`)),
      });
      this.ws.on("close", () => finish(new Error("transport closed during register")));
    });

  }

  /**
   * Place a call. Resolves once a final response (2xx/4xx/5xx) for the INVITE is seen.
   *
   * Note the fingerprint must belong to the certificate the media layer will actually
   * present — signalling and DTLS are validated independently by the peer.
   */
  async placeCall(opts: PlaceCallOptions): Promise<PlaceCallResult> {
    if (!this.ws || !this.identity) throw new Error("not connected");

    const sipUser = this.identity.sipUser;
    const callId = `${makeCallId()}_${Date.now()}`;
    const fromTag = randomBytes(4).toString("hex");
    const from = `<sip:${sipUser}@${SIP_DOMAIN}>;tag=${fromTag}`;

    // The real client uses a short opaque token here, distinct from the REGISTER credential.
    // Reusing the credential is the conservative choice until that is understood.
    const sdp = opts.media
      ? await opts.media.createOffer()
      : buildOffer({ fingerprint: opts.fingerprint ?? "", ...opts.sdp });

    const invite = buildInvite({
      fromUser: sipUser,
      toE164: opts.toE164,
      sdp,
      fromTag,
      callId,
      viaHost: this.viaHost,
      contact: this.contact,
      routes: opts.routes,
      preferredIdentity: opts.preferredIdentity,
      placeCallContext: opts.placeCallContext,
    });

    const result: PlaceCallResult = { responses: [], ...(opts.media ? { media: opts.media } : {}) };
    const timeout = this.opts.callTimeoutMs ?? 45_000;

    return await new Promise<PlaceCallResult>((resolve, reject) => {
      let settled = false;
      const finish = (err?: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(guard);
        err ? reject(err) : resolve(result);
      };
      const guard = setTimeout(() => {
        result.finalResponse ??= "(no final response before timeout)";
        finish();
      }, timeout);

      const onMessage = (msg: string): void => {
        this.emit(msg);
        const line = msg.split("\r\n")[0] ?? "";
        if (!line.startsWith("SIP/2.0")) return;
        result.responses.push(line);

        const h = parseHeaders(msg);
        const toHeader = h.get("to")?.[0] ?? `<sip:${opts.toE164}@${SIP_DOMAIN}>`;
        // In-dialog requests must echo the Record-Route set learned from the dialog.
        const recordRoutes = h.get("record-route") ?? [];
        if (recordRoutes.length > 0) this.routes = recordRoutes;

        if (line.startsWith("SIP/2.0 183")) {
          const rseq = Number(h.get("rseq")?.[0] ?? "1");
          const answerSdp = extractSdp(msg);
          if (answerSdp) {
            result.answer = parseAnswer(answerSdp);
            // Apply the answer immediately so ICE/DTLS can run in parallel with the
            // PRACK round-trip rather than waiting for the 2xx.
            void opts.media?.applyAnswer(answerSdp).catch((err: unknown) => {
              this.mediaError = err instanceof Error ? err.message : String(err);
            });
          }
          // 183 must be PRACKed (Require: 100rel) before the call proceeds.
          this.ws?.send(
            buildPrack({
              requestUri: `sip:${opts.toE164}@${SIP_DOMAIN}`,
              from,
              to: toHeader,
              callId,
              cseq: 1,
              rseq,
              contact: this.contact,
              routes: this.routes,
            }),
          );
          return;
        }

        if (line.startsWith("SIP/2.0 200") && /CSeq:\s*\d+\s+PRACK/i.test(msg)) return; // PRACK ack

        if (/^SIP\/2\.0 (2\d\d)/.test(line)) {
          result.finalResponse = line;
          this.ws?.send(
            buildAck({
              requestUri: `sip:${opts.toE164}@${SIP_DOMAIN}`,
              from,
              to: toHeader,
              callId,
              contact: this.contact,
              routes: this.routes,
            }),
          );
          finish();
          return;
        }

        if (/^SIP\/2\.0 ([45]\d\d)/.test(line)) {
          result.finalResponse = line;
          finish();
        }
      };

      this.dialogHandler = onMessage;
      this.ws?.send(invite);
    });
  }

  /** Set while a call dialog is active; takes over message dispatch from `emit`. */
  private dialogHandler?: (msg: string) => void;
  /** Record-Route set learned from the dialog, echoed on in-dialog requests. */
  private routes: string[] = [];
  /** Set if applying the remote answer threw, so callers can surface it. */
  mediaError?: string;

  /** Tear the call (and the session) down. */
  bye(): void {
    try {
      this.ws?.close();
    } catch {
      /* already gone */
    }
  }
}