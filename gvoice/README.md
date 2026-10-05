# gvoice — unofficial Google Voice client (no browser required)

Reverse-engineered programmatic access to Google Voice: SMS/control over plain HTTP, and
call signaling over SIP-on-WebSocket. **Everything here runs outside the browser.**

> Unofficial and unsupported. Google can change or withdraw any of this at any time.
> Automating a Google account may violate Google's Terms of Service — that is your call
> to make, not this library's.

## Status

| Capability | State |
|---|---|
| `account/get`, `sipregisterinfo/get`, `thread/list`, … over HTTP | ✅ working |
| SIP REGISTER against the live registrar (`200 OK`) | ✅ working |
| SIP digest auth (RFC 2069 style, MD5, no qop) | ✅ working |
| `api2thread/sendsms` | ✅ works, but needs a server-issued token (see below) |
| SDP offer generation + answer parsing | ✅ implemented (`src/call.ts`) |
| Full `INVITE` dialog (100/183/PRACK/180) | ✅ **working — the call rings** |
| GV-proprietary INVITE headers | ✅ **not required** — verified unnecessary |
| werift media plane (ICE + DTLS-SRTP + RTP) | ✅ **DTLS handshake completes** |
| Inbound audio via werift | ❌ GV sends no RTP to the werift peer (see below) |
| Inbound audio via headless-Chrome media host | ✅ proven: 5.28 M samples extracted |
| Opus encode/decode, TX injection, RX decode | ✅ implemented and verified locally |

## How it works

Google Voice has no public API. Two independent surfaces are reachable:

**1. Control plane — HTTP.** `ccc_grand_central_api.VoiceClientService` on
`clients6.google.com`, proto3-JSON encoded.

```
POST https://clients6.google.com/voice/v1/voiceclient/<method>?alt=protojson&key=AIzaSyDTYc1N4xiODyrQYK0Kl6g_y279LjYkrBg
Authorization: SAPISIDHASH <ts>_<sha1(ts + " " + SAPISID + " " + origin)>   (× 1P/3P variants)
Content-Type:  application/json+protobuf
Referer:       https://voice.google.com/
Cookie:        the Google cookie jar
```

**2. Media plane — SIP over WebSocket (RFC 7118).** Not proprietary:

```
wss://web.voice.telephony.goog:443/websocket      subprotocol: sip
Origin: https://voice.google.com/                 (required — 403 without it)
registrar: web.c.pbx.voice.sip.google.com
REGISTER → 401 (digest) → REGISTER → 200 OK
INVITE   → 100 Trying / 183 Session Progress / 180 Ringing / 200 OK
```

Media itself is ordinary WebRTC — `DTLS-SRTP` with `opus/48000/2`, standard trickle ICE,
and Google's media peer is a directly routable public IP (no TURN relay involved). So once
signaling works, an off-the-browser WebRTC stack (`werift`, `pion/webrtc`, `aiortc`) can own
the media plane outright.

## Getting a session

The HTTP control plane needs the Google cookie jar, including **HttpOnly** cookies that
`document.cookie` cannot read. Export it once from a signed-in Chrome.

In a Playwright-driven Chrome with the profile already signed in to `voice.google.com`:

```js
const cookies = await page.context().cookies();
const wanted = cookies.filter(c => c.domain.endsWith('google.com')
  || c.domain.endsWith('clients6.google.com')
  || c.domain.endsWith('voice.google.com'));
// POST { cookies: [{ name, value, domain, path, secure, httpOnly }] } to tools/receive-session.mjs
```

`tools/receive-session.mjs` writes `gv-session.json` (gitignored, mode 0600) and never logs
values. Start it with `node tools/receive-session.mjs`, POST to it, and it exits.

Cookies expire; re-export when things start returning 401.

## Usage

```bash
pnpm install

pnpm typecheck               # tsc --noEmit (typecheck only; no build step)
pnpm probe:sip               # fetch SIP creds, REGISTER, report
pnpm probe:sip GV_VERBOSE=1  # dump raw SIP messages both directions
pnpm probe:discover          # best-effort refresh of client build/version strings
pnpm probe:invite <e164>                # place a call, report the SIP dialog
pnpm probe:media <e164> [secs] [--tx]   # place a call and verify media
pnpm probe:sms <e164> "<text>" ./sendsms-capture.json   # replay a captured send
```

Requires Node >= 22.18. Sources run directly through Node's native TypeScript stripping, so
there is no compile step and no `tsx`/`esbuild` dependency — hence the import specifiers use
`.ts` extensions and `tsconfig` is `noEmit` with `erasableSyntaxOnly`.

Programmatic use:

```ts
import { VoiceClient, METHODS } from "./src/gv-api.js";
import { openSipTransport, buildDigest, buildMessage, parseChallenge } from "./src/sip.js";

const client = new VoiceClient();                 // reads ./gv-session.json
const account = await client.call(METHODS.accountGet, [null, 1]);
const creds   = await client.call(METHODS.sipRegisterInfoGet, [3, "…"]);
```

## Traps that look like something else

These each cost real debugging time, so they are worth stating plainly:

- **Referrer restriction.** The API key only accepts `Referer: https://voice.google.com/`.
  Sending `clients6.google.com`, the app's own `proxy.html`, or omitting it yields
  `403 API_KEY_HTTP_REFERRERR_BLOCKED`, which looks exactly like an auth failure.
- **A partial cookie jar yields `401 CREDENTIALS_MISSING`**, even with a perfectly valid
  SAPISIDHASH. The full set for the target host is required, including
  `__Secure-*PSIDTS`, `AEC`, `NID`. Do not hand-filter the cookie list.
- **`COMPASS` exists under several domains** with different values; the one that matters
  for Voice is the `clients6.google.com` `voice-api=…` cookie. Name-keyed maps pick the
  wrong one. This module stores cookies as records and builds the header per target URL.
- **The WebSocket upgrade needs `Origin: https://voice.google.com`.** Omit it and you get
  `403` on the upgrade, which surfaces as an opaque `socket hang up` / close code 1006.
- **The SIP identity is the opaque credential, not the phone number.** `GetSipRegisterInfo`
  returns something like `AfVgt1F30eQ…=`; that string is the SIP user in `From`/`To`/
  `Contact`. Using `+1415…` there gets `400 Bad Request`.
- **Digest is RFC 2069 style** — `algorithm=MD5`, **no `qop`**, so no `cnonce`/`nc`. The
  first REGISTER is sent with an empty `nonce=""`/`response=""` probe.
- **The registrar validates client headers.** `Allow`, `Supported`, `User-Agent`,
  `X-Google-Client-Info` and `Via: …;rport;keep` are all sent by the real client.

### Calling a number works without a browser

```bash
node src/probe-invite.ts +18002758777
```

Verified dialog from a plain Node process:

```
100 Trying -> 183 Session Progress (+SDP) -> 200 OK -> 180 Ringing -> 504
```

Google answers ICE-lite with a directly routable candidate (`74.125.39.43:26500`) and
`setup:passive`, so the media stack only has to act as the DTLS client. No candidate
trickling is needed — our address is learned from outbound STUN.

Three Birdsong headers that a real INVITE carries (`Route` `uri-econt`,
`P-Preferred-Identity`, `X-GV-PlaceCallContext`) turned out **not to be enforced**; the
probe omits them and still rings. The `504` is expected because no DTLS follows.

If you extend the dialog, note two things: in-dialog requests must echo the `Record-Route`
list from the 183 and carry a `Contact`; and parse headers with `parseHeaders()` rather
than regexes, because a regex that captures the leading CRLF yields a malformed message and
Google replies `400` with an empty `To:`.

### Media plane (werift)

`src/media.ts` wraps werift and covers the whole media stack:

```bash
node src/probe-media.ts +18002758777 20 [--tx]
```

Measured against the live registrar:

```
ice           : connected      iceGatheringState: complete
dtlsState     : connected      iceRole: controlling
outbound pkts : 4              (DTMF, written over the encrypted transport)
inbound pkts  : 0
```

**DTLS completes**, so the self-signed certificate is accepted, SRTP keys are derived, and
the transport carries RTP both ways. Zero inbound packets is not a transport fault — the
800 line rang out (`504`), and Google only sends media to an answered call.

Two interop details that are easy to get wrong:

- **Payload type must be 111.** werift's default audio offer advertises PT 96/0, and Google's
  answer picks from what we offer, so the codec list has to be overridden or nothing
  negotiates.
- **werift needs a real track to send on.** `addTransceiver('audio')` leaves
  `sender.track` undefined and there is nothing to write RTP to; create one with
  `MediaStreamTrackFactory.rtpSource({ kind: 'audio' })` and attach that.

### Opus (`src/audio.ts`)

`opus/48000/2` at payload type 111, 20 ms frames of 960 samples, mono internally and
duplicated stereo by the RTP layer.

Two packages because neither does both jobs well: **encode** via `opusscript`, **decode**
via `opus-decoder` (wasm). `opus-decoder`'s `decodeFrame` resolves to an object with
`channelData`, not a bare channel array.

**The trap:** opusscript's encoder takes **Int16** PCM. Passing Float32 in the usual ±1.0
range silently encodes near-silence — the packet comes out 57 B instead of 120 B and decodes
to a peak of 0.00006. Scale by 32767 and clamp. Also note `encoderCTL` is unimplemented
there, so a requested bitrate is honoured only in the sense that the frame size you pass to
`encode()` is what actually matters.

Measured round-trip of a 440 Hz tone: 119-byte packet, decoded peak 1.29, rms 0.32.

### Known werift interop gap (inbound)

werift does not currently receive audio from Google Voice. A packet capture pins down how
far the media path actually gets.

**What reaches us, verified with tcpdump during a live call:**

```
udp port 26500  →  1647 inbound packets
  all IPv4 after pinning (see below)
  payload first bytes: 01 01 00 44 21 12 a4 42 …   <- STUN magic cookie 0x2112A442
```

Every inbound packet is a **STUN Binding Success Response** (message type `0x0101`), not RTP.
So Google completes ICE and keeps answering our checks, but sends **zero media**.

**What is proven working browser-free:** SIP REGISTER, the full INVITE dialog
(100 / 183 / PRACK / 180 / 200 / ACK / BYE), ICE connected, DTLS connected with negotiated
SRTP profiles (`[7, 1]`), and 1000+ SRTP-protected opus packets delivered to Google's media
address.

**Ruled out along the way:**

- werift's own pipeline — a local loopback receives fine in both roles, offerer included.
- Google's answer lacking `a=ssrc:` — worked around with `ensureSsrcLines()`; werift's
  `trackBySSRC[ssrc]` miss silently dropping packets is worked around by
  `wireWildcardReceive()` plus a `handleRtpBySsrc` wrapper.
- The ICE address family — Google offered both, werift gathered both, and a capture showed
  1615 of 1623 inbound packets on **IPv6** while the DTLS handshake ran on IPv4, so the two
  sides disagreed about which pair carried media. `MediaPlaneOptions.useIpv6` now pins one
  family (IPv4 by default) and `dropIPv6Candidates()` strips the other from the answer.
- The three Birdsong headers, at the SIP layer — a call rings and is answered without them,
  and replaying captured values changed nothing.

**What remains:** Google answers STUN but never bridges media. Either these test calls are
never actually answered at the PSTN leg (they end `504`), or Google's media service needs
something beyond SIP signalling that the web client sends — plausibly an authenticated step
through the proprietary `X-Google-*` RPC surface rather than a SIP header.

werift's `getStats()` returns an empty array until `collectStats()` runs, so packet capture
(`tcpdump -i wlan0 -n 'udp port 26500'`) is the reliable way to tell media from keepalive.

### SMS send needs a server-issued token

`api2thread/sendsms` takes a **positional** protojson array (braces are rejected), and the
field map is now known — see `src/sms.ts` for the full annotated version:

| field | meaning |
|---|---|
| f5 | `sms_message` — the text |
| f6 | `thread_id` — `t.<E164>` |
| f7 | recipient E164 list — **null when replying in an existing thread** |
| f9 | `attachment` — carries a **server-issued, single-use message id** |
| f11 | `envelope` — ~1.6 KB encrypted blob |

Two constraints that are easy to trip over:

- **Text alone is rejected.** `f9` alone suffices, `f11` alone suffices, but omitting both
  fails even with `sms_message` set.
- **f9 cannot be synthesised.** Values captured from real sends replay fine; `0`, `1`, `42`,
  random large numbers, and `base+1`/`base*2`-style derivations are all rejected. Replaying
  a used id returns the *same* `messageId`, i.e. f9 is an idempotency key the server
  validates as server-issued and single-use.

So `sendSms` requires either a captured `messageId`/`envelope` or a real thread context.
Making SMS fully browser-free means either reverse-engineering whatever issues that id or
generating a valid f11 envelope — a bigger piece of work than the SIP path.

### About `X-Google-Client-Info`

Decoded, it is a 4-field protobuf of pure client telemetry — no account, session, or device
identifier:

| field | meaning | example |
|---|---|---|
| 1 | client name + build | `GoogleVoice voice.web-frontend_20260928.07_p0` |
| 2 | media engine + version | `Birdsong v2.2.74` |
| 3 | platform enum | `5` (web) |
| 5 | browser + version | `Chrome 154.0.0.0` |

It is privacy-safe but brittle — it pins a build date and browser version. So it is
**derived, not hardcoded**: `src/client-info.ts` encodes it from parts, and each part can be
overridden by environment variable:

```bash
export GV_CLIENT_BUILD="GoogleVoice voice.web-frontend_…"
export GV_ENGINE_VERSION="Birdsong v…"
export GV_CLIENT_BROWSER="Chrome 154.0.0.0"
```

`pnpm probe:discover` tries to read current values from the live app. It is best-effort:
the identifiers live in a chunk resolved at runtime, so plain HTML/module scraping does not
always find them. When it cannot, update the defaults in `src/client-info.ts`.

## Layout

```
src/gv-api.ts         VoiceClientService HTTP client (SAPISIDHASH, referrer rules, method list)
src/session.ts        cookie jar loading + per-URL Cookie header construction
src/sip.ts            SIP message building, digest auth, WSS transport
src/sms.ts            api2thread/sendsms schema + request/response helpers
src/probe-sms.ts      send via a captured client exchange
src/client-info.ts    X-Google-Client-Info encoding + env overrides
src/probe-sip.ts      end-to-end REGISTER probe
src/probe-discover.ts best-effort client-info discovery
tools/receive-session.mjs  dev helper to capture the cookie jar
pnpm-workspace.yaml        makes this dir its own pnpm root (see comment in file)
```

`pnpm-workspace.yaml` exists so pnpm treats this directory as its own workspace root.
Without it, pnpm walks up into the monorepo and writes our dependencies into the monorepo
root `pnpm-lock.yaml` — which does not travel with this git subrepo, leaving it
non-reproducible.

## References

Background research and captured traffic: see the working notes. Upstream context:
`mautrix/gvoice` (Matrix bridge, calls unimplemented) is the closest maintained
reverse-engineered client.