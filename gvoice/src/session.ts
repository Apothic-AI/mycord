/**
 * Loads the Google session needed by VoiceClient, and builds correctly scoped
 * Cookie headers from it.
 *
 * Why this is fiddly: the working cookie set includes HttpOnly cookies (SSID, HSID,
 * __Secure-*PSID, __Secure-*PSIDTS) that document.cookie cannot read, so the jar has
 * to be exported from a signed-in Chrome. But a naive "dump every cookie name=value"
 * header does *not* work:
 *
 *  - `COMPASS` exists under several domains with different values (the one that
 *    matters for Voice is the clients6.google.com `voice-api=…` one). Name-keyed maps
 *    silently pick the wrong one.
 *  - A partial jar fails with 401 CREDENTIALS_MISSING even though SAPISIDHASH is valid.
 *    The auth check wants the full set for the target host.
 *
 * So the jar is stored as records with domain/path and the Cookie header is computed
 * per target URL, the way a browser would.
 *
 * Export format (see tools/receive-session.mjs):
 *   { "cookies": [{ name, value, domain, path, secure, httpOnly }, …], "sapisid": "…" }
 */

import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const SESSION_FILE = join(HERE, "..", "gv-session.json");

export interface CookieRecord {
  name: string;
  value: string;
  /** e.g. ".google.com" or "clients6.google.com" */
  domain: string;
  path: string;
  secure: boolean;
  httpOnly: boolean;
}

export interface StoredSession {
  cookies: CookieRecord[];
  sapisid: string;
}

let cached: StoredSession | undefined;

export function loadSession(file = SESSION_FILE): StoredSession {
  if (cached) return cached;
  if (!existsSync(file)) {
    throw new Error(
      `missing ${file}\n` +
        `Export the cookie jar from a signed-in Chrome first — see README "Getting a session".`,
    );
  }
  const raw = JSON.parse(readFileSync(file, "utf8")) as Partial<StoredSession>;
  if (!Array.isArray(raw.cookies) || raw.cookies.length === 0) {
    throw new Error(`${file} has no "cookies" array`);
  }
  const sapisid =
    raw.sapisid ??
    raw.cookies.find((c) => c.name === "SAPISID")?.value ??
    raw.cookies.find((c) => c.name === "__Secure-3PAPISID")?.value;
  if (!sapisid) {
    throw new Error(`${file} has no SAPISID (needed for the SAPISIDHASH Authorization header)`);
  }
  cached = { cookies: raw.cookies, sapisid };
  return cached;
}

/** True if `cookieDomain` domain-matches `host`, honouring the leading-dot form. */
function domainMatches(cookieDomain: string, host: string): boolean {
  const cd = cookieDomain.startsWith(".") ? cookieDomain.slice(1) : cookieDomain;
  return host === cd || host.endsWith(`.${cd}`);
}

function pathMatches(cookiePath: string, urlPath: string): boolean {
  if (cookiePath === "/" || urlPath === cookiePath) return true;
  if (!urlPath.startsWith(cookiePath)) return false;
  return cookiePath.endsWith("/") || urlPath.charAt(cookiePath.length) === "/";
}

/**
 * Build a Cookie header for `url`, keeping only cookies the browser would actually send.
 *
 * Ordering follows the jar order (longest path first would be more browser-accurate but
 * Google does not appear to care).
 */
export function cookieHeaderFor(session: StoredSession, url: string): string {
  const parsed = new URL(url);
  const host = parsed.hostname;
  const isSecure = parsed.protocol === "https:";
  const path = parsed.pathname || "/";

  const applicable = session.cookies.filter(
    (c) =>
      domainMatches(c.domain, host) &&
      pathMatches(c.path || "/", path) &&
      (isSecure || !c.secure),
  );

  // De-duplicate by name, preferring the most specific domain (longest suffix match).
  const best = new Map<string, CookieRecord>();
  for (const c of applicable) {
    const prev = best.get(c.name);
    if (!prev || domainMatches(c.domain, host) && c.domain.length > prev.domain.length) {
      best.set(c.name, c);
    }
  }

  if (best.size === 0) {
    throw new Error(`no cookies in the jar apply to ${host}`);
  }
  return [...best.values()].map((c) => `${c.name}=${c.value}`).join("; ");
}

/** Names of cookies that would be sent to `url` — useful for debugging auth failures. */
export function cookieNamesFor(session: StoredSession, url: string): string[] {
  return cookieHeaderFor(session, url)
    .split("; ")
    .map((p) => p.slice(0, p.indexOf("=")));
}