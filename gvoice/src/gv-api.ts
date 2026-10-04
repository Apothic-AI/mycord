/**
 * Google Voice / VoiceClient HTTP client (unofficial).
 *
 * Reverse-engineered recipe, verified working against a live account:
 *
 *   POST https://clients6.google.com/voice/v1/voiceclient/<method>
 *        ?alt=protojson&key=<GOOGLE_API_KEY>
 *
 *   Authorization: SAPISIDHASH <ts>_<sha1>  (repeated for 1P/3P variants)
 *   Content-Type:  application/json+protobuf
 *   Referer:       https://voice.google.com/     <-- REQUIRED, see below
 *   Cookie:        full Google cookie jar
 *
 * Two things commonly make this look like an auth failure:
 *
 *  1. Missing HttpOnly cookies -> 401 SESSION_COOKIE_INVALID, and the error body names
 *     the offending cookie (typically SSID). These cannot be read from document.cookie;
 *     they must be lifted from the browser cookie jar.
 *
 *  2. The API key is HTTP-referrer-restricted. The whitelisted referrer is
 *     https://voice.google.com/ . Sending clients6.google.com, clients6.google.com/static/proxy.html,
 *     or no referrer at all all yield 403 API_KEY_HTTP_REFERRERR_BLOCKED.
 */

import { createHash } from "node:crypto";
import { cookieHeaderFor, loadSession, type StoredSession } from "./session.ts";

/** Public Google API key used by the Voice web client. Not secret. */
export const VOICE_API_KEY = "AIzaSyDTYc1N4xiODyrQYK0Kl6g_y279LjYkrBg";

/** Origin of the Grand Central (VoiceClient) API. Also the SAPISIDHASH origin. */
export const API_ORIGIN = "https://clients6.google.com";

/** The only referrer the API key accepts. */
export const ALLOWED_REFERER = "https://voice.google.com/";

/** Proto service backing these endpoints. */
export const PROTO_SERVICE = "ccc_grand_central_api.VoiceClientService";

export interface CallOptions {
  /** Extra headers merged over the defaults. */
  headers?: Record<string, string>;
  /** Abort the request after this many ms. Defaults to 30_000. */
  timeoutMs?: number;
}

/**
 * Build the SAPISIDHASH triple.
 *
 * Each of the three schemes (default / 1P / 3P) is `<unix_seconds>_<sha1hex>` where
 *   sha1hex = SHA1("<ts> " + SAPISID + " " + origin)
 * and origin is the *target* origin, i.e. https://clients6.google.com.
 *
 * The timestamp is regenerated per request; a stale one yields 401.
 */
export function sapisidHash(session: StoredSession, ts = Math.floor(Date.now() / 1000)): string {
  const digest = createHash("sha1")
    .update(`${ts} ${session.sapisid} ${API_ORIGIN}`)
    .digest("hex");
  const value = `${ts}_${digest}`;
  return `SAPISIDHASH ${value} SAPISID1PHASH ${value} SAPISID3PHASH ${value}`;
}

export class VoiceClient {
  private readonly session: StoredSession;

  /** Defaults to the jar exported by tools/receive-session.mjs. */
  constructor(session: StoredSession = loadSession()) {
    this.session = session;
  }

  /** Invoke a VoiceClientService method. Body is proto3-JSON (arrays for nested messages). */
  async call<T = unknown>(method: string, body: unknown = [], opts: CallOptions = {}): Promise<T> {
    const url = `${API_ORIGIN}/voice/v1/voiceclient/${method}?alt=protojson&key=${VOICE_API_KEY}`;

    const res = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json+protobuf",
        authorization: sapisidHash(this.session),
        cookie: cookieHeaderFor(this.session, url),
        origin: API_ORIGIN,
        referer: ALLOWED_REFERER,
        "user-agent":
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
        ...opts.headers,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(opts.timeoutMs ?? 30_000),
    });

    const text = await res.text();
    if (!res.ok) {
      throw new VoiceApiError(method, res.status, text);
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new VoiceApiError(method, res.status, `non-JSON response: ${text.slice(0, 200)}`);
    }
  }
}

/** Pull Google's structured error reason (e.g. SESSION_COOKIE_INVALID) out of a body. */
function describeError(method: string, status: number, body: string): string {
  let reason = "";
  try {
    const parsed = JSON.parse(body) as { error?: { details?: Array<Record<string, unknown>> } };
    const info = (parsed.error?.details ?? []).find((d) =>
      String(d["@type"] ?? "").includes("ErrorInfo"),
    ) as { reason?: string; metadata?: Record<string, string> } | undefined;
    if (info?.reason) {
      reason = `${info.reason}${info.metadata?.cookie ? ` (cookie=${info.metadata.cookie})` : ""}`;
    }
  } catch {
    /* body was not the expected JSON error envelope */
  }
  return `VoiceClientService.${method} -> ${status}${reason ? ` ${reason}` : ""}: ${body.slice(0, 300)}`;
}

export class VoiceApiError extends Error {
  // Declared and assigned explicitly rather than as constructor parameter properties,
  // because we run TypeScript directly via Node's type stripping (erasable syntax only).
  readonly method: string;
  readonly status: number;
  readonly body: string;

  constructor(method: string, status: number, body: string) {
    super(describeError(method, status, body));
    this.name = "VoiceApiError";
    this.method = method;
    this.status = status;
    this.body = body;
  }
}

/** Known methods, extracted from the Voice web bundle. */
export const METHODS = {
  accountGet: "account/get",
  accountUpdate: "account/update",
  threadGet: "api2thread/get",
  threadList: "api2thread/list",
  threadSearch: "api2thread/search",
  sendSms: "api2thread/sendsms",
  rcsSendMessage: "rcs/sendmessage",
  inboxColdsync: "inbox/coldsync",
  inboxWarmsync: "inbox/warmsync",
  threadingInfoGet: "threadinginfo/get",
  threadMarkAllRead: "thread/markallread",
  threadBatchDelete: "thread/batchdelete",
  threadUpdateAttributes: "thread/updateattributes",
  threadBatchUpdateAttributes: "thread/batchupdateattributes",
  threadItemBatchDelete: "threaditem/batchdelete",
  startClickToCall: "communication/startclicktocall",
  sipRegisterInfoGet: "sipregisterinfo/get",
  getNumberPortInfo: "getnumberportinfo",
  clearHistory: "clearhistory",
  clientAccessPermissionGet: "clientaccesspermission/get",
  relockNumber: "relocknumber",
} as const;