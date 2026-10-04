/**
 * Builder for the `X-Google-Client-Info` header.
 *
 * The Voice web client sends this on every SIP request. Decoded, it is a small
 * protobuf of pure client telemetry — no account, session, or device identifiers:
 *
 *   field 1 (str) : client name + build   e.g. "GoogleVoice voice.web-frontend_20260928.07_p0"
 *   field 2 (str) : media engine + version e.g. "Birdsong v2.2.74"
 *   field 3 (varint) : platform enum      5 = web
 *   field 5 (str) : browser + version    e.g. "Chrome 154.0.0.0"
 *
 * It is therefore safe with respect to privacy, but it *is* brittle: it pins a build
 * date and a browser version that go stale. So rather than hardcoding one string we
 * derive it, allow overrides, and provide a discovery helper (see `discoverClientInfo`)
 * that reads the current values out of the live app.
 */

import { createHash } from "node:crypto";

/** Discovery result; every field is optional because a value may legitimately be absent. */
export type DiscoveredClientInfo = {
  client?: string | undefined;
  engine?: string | undefined;
  browser?: string | undefined;
  platform?: number | undefined;
};

export interface ClientInfoParts {
  /** e.g. "GoogleVoice voice.web-frontend_20260928.07_p0" */
  client: string;
  /** e.g. "Birdsong v2.2.74" */
  engine: string;
  /** platform enum; 5 = web */
  platform: number;
  /** e.g. "Chrome 154.0.0.0" */
  browser: string;
}

const DEFAULTS: ClientInfoParts = {
  client: "GoogleVoice voice.web-frontend_20260928.07_p0",
  engine: "Birdsong v2.2.74",
  platform: 5,
  browser: "Chrome 154.0.0.0",
};

function varint(value: number): Buffer {
  const bytes: number[] = [];
  let v = value;
  do {
    let byte = v & 0x7f;
    v >>>= 7;
    if (v > 0) byte |= 0x80;
    bytes.push(byte);
  } while (v > 0);
  return Buffer.from(bytes);
}

function lengthDelimited(field: number, payload: Buffer): Buffer {
  return Buffer.concat([Buffer.from([(field << 3) | 2]), varint(payload.length), payload]);
}

/** Encode the parts as the protobuf Google uses (verified against a real capture). */
export function encodeClientInfo(parts: ClientInfoParts): string {
  const body = Buffer.concat([
    lengthDelimited(1, Buffer.from(parts.client, "utf8")),
    lengthDelimited(2, Buffer.from(parts.engine, "utf8")),
    Buffer.from([(3 << 3) | 0]),
    varint(parts.platform),
    lengthDelimited(5, Buffer.from(parts.browser, "utf8")),
  ]);
  return body.toString("base64");
}

/**
 * Resolve client info parts.
 *
 * Precedence: explicit overrides > environment > defaults. `GV_CLIENT_BROWSER` is
 * commonly set from the real user agent so the browser field tracks reality.
 */
export function resolveClientInfo(overrides: DiscoveredClientInfo = {}): ClientInfoParts {
  const env = process.env;
  const browser = overrides.browser ?? env.GV_CLIENT_BROWSER ?? DEFAULTS.browser;
  return {
    client: overrides.client ?? env.GV_CLIENT_BUILD ?? DEFAULTS.client,
    engine: overrides.engine ?? env.GV_ENGINE_VERSION ?? DEFAULTS.engine,
    platform: overrides.platform ?? Number(env.GV_PLATFORM ?? DEFAULTS.platform),
    browser,
  };
}

/** Best-effort "Chrome 123.0.0.0" from a user-agent string. */
export function browserFromUserAgent(ua: string): string | undefined {
  const m = /(?:Chrome|Chromium)\/(\d+\.\d+\.\d+\.\d+)/.exec(ua);
  return m?.[1] ? `Chrome ${m[1]}` : undefined;
}

/**
 * Pull the current client build and engine version out of the live Voice web app.
 *
 * Run this when the defaults go stale; it prints values to export in your env.
 * Requires no credentials — the app shell and its modules are public.
 *
 * The page itself does not inline the bundle: it pulls a set of `<script type="module">`
 * chunks from /assets/, one of which carries the build identifiers, so this walks
 * those (bounded) rather than assuming a single gstatic URL.
 */
export async function discoverClientInfo(
  options: { maxScripts?: number } = {},
): Promise<DiscoveredClientInfo> {
  const maxScripts = options.maxScripts ?? 20;
  const origin = "https://voice.google.com";
  const html = await (await fetch(`${origin}/`, { redirect: "follow" })).text();

  const candidates = new Set<string>();
  for (const m of html.matchAll(/<script[^>]+src="([^"]+)"/g)) {
    const src = m[1];
    if (!src) continue;
    try {
      candidates.add(new URL(src, origin).toString());
    } catch {
      /* ignore malformed */
    }
  }
  // Historic layout also inlined an absolute gstatic bundle URL.
  for (const m of html.matchAll(/https:\/\/www\.gstatic\.com\/_\/voice\/[^"'\\]+/g)) {
    candidates.add(m[0]);
  }

  // exactOptionalPropertyTypes: allow explicit undefined while we fill these in
  const found: DiscoveredClientInfo = {};
  let checked = 0;
  const queue = [...candidates];

  while (queue.length > 0 && checked < maxScripts) {
    if (found.client && found.engine) break;
    const url = queue.shift();
    if (!url) break;
    checked++;
    let js: string;
    try {
      js = await (await fetch(url)).text();
    } catch {
      continue;
    }

    const client = /voice\.web-frontend_(\d{8}\.\d+)_p\d+/.exec(js);
    if (client && !found.client) found.client = `GoogleVoice voice.web-frontend_${client[1]}_p0`;

    const engine = /Birdsong\s+v([\d.]+)/.exec(js);
    if (engine && !found.engine) found.engine = `Birdsong v${engine[1]}`;

    const browser = /"(Chrome\s+\d+\.\d+\.\d+\.\d+)"/.exec(js);
    if (browser && !found.browser) found.browser = browser[1];

    // The /assets entry points are thin loaders; the real chunk arrives via static or
    // dynamic import. Follow same-origin asset imports one level to find it.
    if (!found.client || !found.engine) {
      for (const m of js.matchAll(/(?:from|import)\s*\(?\s*["'](\/assets\/[^"']+\.js)["']/g)) {
        const next = new URL(m[1] ?? "", origin).toString();
        if (!candidates.has(next)) {
          candidates.add(next);
          queue.push(next);
        }
      }
    }
  }

  if (found.client || found.engine) {
    console.error(
      `discovered from ${checked} script(s): client=${found.client ?? "?"} ` +
        `engine=${found.engine ?? "?"} browser=${found.browser ?? "?"}`,
    );
  }
  return found;
}

/** Convenience: base64 header value for the resolved parts. */
export function clientInfoHeader(overrides: DiscoveredClientInfo = {}): string {
  return encodeClientInfo(resolveClientInfo(overrides));
}

/** Stable fingerprint of the resolved parts — handy for logging without leaking detail. */
export function clientInfoFingerprint(overrides: DiscoveredClientInfo = {}): string {
  return createHash("sha256").update(clientInfoHeader(overrides)).digest("hex").slice(0, 12);
}