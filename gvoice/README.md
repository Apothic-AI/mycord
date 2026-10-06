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
| Inbound audio, pure Node | ✅ **1852 opus frames decoded, peak 0.66** |
| Outbound synthesized speech | ✅ **211 opus frames, real-time paced** |
| Two-way speech, no audio device | ✅ `node src/probe-voice.ts +18003569377` |
| Local speech-to-text (parakeet-redux) | ✅ 0.55 s / utterance, streaming partials |
| Local neural TTS (Piper) | ✅ ~6x faster than real time on CPU |
| Full agent loop (VAD → STT → brain → TTS) | ✅ 3-turn live conversation |
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

### The werift inbound bug, and the fix

Google Voice **does** send audio, and werift's SRTP keys were always correct — werift was
throwing the packets away before decrypting them.

**Root cause.** werift classifies an inbound datagram as RTP only if its first byte is in
`(127, 192)`, the range of an RTP version-2 header. Google Voice's datagrams arrive with a
first byte of **`0xF7`**, which fails that test, so werift routes them into its RTCP handler
where the RTCP parse fails and the packet is discarded — no exception, no log, no counter.
That is why `SrtpSession.decrypt` was never called even though 98 KB had arrived.

**Proof the keys were fine.** Feeding those same datagrams to werift's own `SrtpSession`
produced `80 6f 00 10 00003c00 5776e382` — version 2, **payload type 111 (opus)**, sequence
16, timestamp 15360 (320 samples = 20 ms at 48 kHz), with a valid SSRC.

**Fix.** Take the raw datagrams off the ICE connection, decrypt with the DTLS transport's
existing SRTP session, and hand the RTP to subscribers directly. Inbound path in
`MediaPlane.watchRawInbound`, which counts every stage:

```
raw ICE dgrams: 443 (32,723 bytes, pre-SRTP)
  srtp decrypt ok=439 fail=2 | non-rtp=0 rtp-parse-fail=0
inbound pkts  : 439 (opus 439, other 0)
peak amplitude: 0.1605    mean rms: 0.02272
```

Two `SrtpAuthenticationError`s out of 443 are normal (a stray or truncated packet mid-flight).

A neater long-term fix is to patch werift's `isMedia` to accept these packets, but routing
them ourselves is explicit and does not depend on werift internals.

Earlier findings that still matter, since each cost real time:

- **ICE address family** — Google offered both IPv4 and IPv6, werift gathered both, and a
  capture showed 1615 of 1623 inbound packets on IPv6 while DTLS ran on IPv4. `useIpv6` pins
  one family; `dropIPv6Candidates()` strips the other from the answer.
- **`a=ssrc` in the answer** — Google omits it, and werift creates no receiver track at all
  without one. `ensureSsrcLines()` covers that for werift's own path.
- **opus encoding takes Int16, not Float32** — see the section above.
- **The three Birdsong headers are not required** for signalling or media.

### Two-way speech (`src/probe-voice.ts`)

`src/tts.ts` turns text into 48 kHz mono Float32 via a swappable backend
(`speak(text) -> Float32Array @ 48 kHz`), and `SpeechSender` streams it into a live call as
opus frames paced at real time.

Two details that mattered:

- **Real-time pacing is not optional.** Dumping a whole utterance as fast as possible
  overruns the far end's jitter buffer and the opening words never arrive. `SpeechSender`
  emits one 20 ms frame every 20 ms.
- **"Quiet" means quiet, not "no packets".** The first version waited for a gap in inbound
  packets before speaking. It never fired: the far end streams frames continuously, so the
  gap never appears. The trigger now watches inbound *loudness* (rolling RMS) instead.

Default backend is `espeak-ng` — local, offline, no credentials, and robotic. It is there to
prove the loop; swap in a neural voice without touching the call path:

```ts
new CommandTtsBackend("edge-tts", ["--voice", "en-US-Aria", "--text"])
```

espeak-ng emits 22.05 kHz mono s16 WAV, so `ffmpeg` resamples to the 48 kHz that
`opus/48000/2` needs.

Run it and watch the far end answer:

```
$ node src/probe-voice.ts +18003569377 "Please say the digit one."

inbound frames  : 1852 (37.0s)
peak amplitude  : 0.6114
outbound pkts   : 211
raw ICE dgrams  : 1862 (decrypt ok=1852 fail=8)

far-end audio timeline (1s buckets, bar = peak amplitude):
  t+ 2s |#################                       | 0.412   <- far-end greeting
  t+ 5s |                                          | 0.000  <-- we start speaking
  t+ 6s |##################                        | 0.455
  t+10s |##################                        | 0.452  <-- we stop speaking
  t+30s |#######################                   | 0.580   <- far end replies
```

The `t+30s` burst after we stop talking is the IVR responding — the loop is genuinely
bidirectional, with no audio device, no browser, and no virtual sound card anywhere.

### A working voice agent

`src/agent.ts` closes the loop: far-end speech → VAD → Whisper → brain → Piper → opus, with
barge-in. The brain is a callback, so the same media path serves IVR navigation, scripted
prompts, or a hosted LLM.

A real 3-turn conversation against the 1-800-FLOWERS virtual agent, all local:

```
1. heard : "Hey there! I'm the 1-800-Flowers virtual agent. Do you want to track and
           order, review delivery info, or ask about something else today?"
   said  : "Track and order."
2. heard : "Let me check."
   said  : "Track an order, please."
3. heard : "Are you the person who placed the order, or are you the recipient?"
   said  : "I am the recipient of the order."
   -> IVR: "Got it."
```

```
$ node src/probe-agent.ts +18003569377 --seconds 65 --brain script
media: in 3267 pkts | out 342 | decrypt ok=3267 fail=13
agent: 3 turns | mean latency 5.58s
```

Brains: `script` (rule-based IVR navigator), `echo` (repeats what it heard, the clearest
proof the round trip works), `http` (any OpenAI-compatible endpoint via `GV_LLM_URL`).

#### The outbound encoder was broken for the entire project

Worth stating plainly, because it hid inside "everything works": **`opusscript` produced
packets no conformant decoder could read.** Outbound speech was unintelligible noise on the
wire the whole time — inbound audio decoded perfectly the entire while, which made the bug
look like a far-end problem.

It was nearly undetectable locally. The packets looked right: ~120 bytes each, a legal TOC
byte (`0x78`), plausible sizes, and passing them through our own decoder gave audio of the
right length. The giveaway was that the decoded audio bore no relationship to the input —
a sine at amplitude 0.1 came back as noise at peak 1.53.

Isolating it took a known-good reference:

1. Encoded a 1 s 440 Hz sine at amplitude 0.5 with `opusenc` (libopus, opus-tools).
2. Extracted its packets from the Ogg container and decoded them with `opus-decoder`.
3. Got peak 0.5039, rms 0.35129 — expected 0.5 / 0.3536. **The decoder is correct.**
4. Ran the same signal through `opusscript`: noise.

So encoding moved to `opusenc` in `src/opusenc.ts`, and `opusscript` is gone. Packets went
from 119 B to 58 B — correct for 24 kbps, and about half of what the broken encoder was
emitting. Round-tripping Piper → opus → decode → Whisper now returns the sentence verbatim.

Encoding whole utterances rather than frame-by-frame is also the correct shape for opus:
variable bitrate and lookahead need a sequence, which is exactly how `SpeechSender` plays.

#### Echo: Google returns your own voice to you

The first barge-in implementation was unusable — it cancelled itself mid-sentence,
repeatedly, logging dozens of `barge-in: stopped speaking` events. Cause: Google Voice sends
back the audio we transmit, so the VAD heard us interrupting us. Timeline from an early run
showed far-end "speech" at exactly the seconds we were speaking.

Fixed in `VoiceAgent` by gating on level: while transmitting, frames quieter than
`ourTX × bargeInGuard` are treated as echo and never reach the VAD. The far end talking over
us is measurably louder than our own loopback, so genuine interruption still registers:

```
[+44.4s] heard: "Okay."
[+45.9s] barge-in: far end is louder than our own echo
[+45.9s] barge-in: stopped speaking
```

A short settle window after we stop speaking (`echoSettleMs`) keeps the VAD from
re-triggering on the decaying echo tail.

#### Latency, and why the model choice matters

| stage | latency | notes |
|---|---|---|
| Piper synthesis | 0.6 s for 3.5 s of audio | ~6x faster than real time, CPU only |
| faster-whisper (small.en, int8) | 1.8 s | persistent worker, model loaded once |
| opus encode | 0.04 s | |
| end-to-end turn | ~2.5–5.5 s | dominated by the far end's own pauses |

`openai-whisper` measured **~15 s for a 5 s utterance** on CPU-only torch — unusable for
conversation. `faster-whisper` (CTranslate2, int8) is roughly 4x faster and adds VAD
filtering, which phone audio needs because it is mostly silence between turns. `turbo`
also works but is slower still than `small.en` on short utterances; `small.en` transcribes
IVR prompts verbatim.

Two implementation traps hit along the way, both silent:

- **faster-whisper assumes 16 kHz.** Handing it a 22.05 kHz array reinterpreted at the wrong
  speed returned fluent, completely wrong text ("Ha, ha, ha."). `stt.ts` now resamples to
  16 kHz before writing the WAV.
- **PyAV does not build on Python 3.14** (`open() got an unexpected keyword argument
  'metadata_errors'`), and faster-whisper uses it for decoding. The worker decodes with the
  stdlib `wave` module instead and passes a NumPy array straight to `transcribe`.

#### Setup

```bash
# STT (optional but recommended): faster-whisper into a venv
python3 -m venv ~/.local/share/gvoice/fw-venv
~/.local/share/gvoice/fw-venv/bin/pip install faster-whisper

# TTS: Piper binary + a voice
mkdir -p ~/.local/share/piper/voices
curl -LO <rhasspy/piper-voices>/en/en_US/amy/medium/en_US-amy-medium.onnx{,json}

# opus encoding
brew install opus-tools

pnpm add -D typescript && pnpm run typecheck
```

`WhisperStt` auto-detects the venv, then `python3`, then the interpreter behind the `whisper`
CLI, preferring faster-whisper and falling back to `openai-whisper`.

### Driving the call from another process

This is the shape the whole thing exists for: an external agent — a model, a script, anything
that can speak HTTP — drives the call without linking against this codebase.

```
$ node src/probe-control.ts +18003569377 --brain none --token s3cret
control API: http://127.0.0.1:8787
  GET  /health
  GET  /events            (SSE transcript stream)
  POST /say {"text":..}
  POST /press {"digit":..}
  POST /shutup
  POST /hangup
```

`tools/agent-example.mjs` is a complete external agent. What it sees while the far end
speaks, over one SSE connection:

```
[8.9s] …
[11.5s] Stay there.
[14.0s] Hey there, I'm the oneie.
[15.2s] Hey there, I'm the 1800 slide.
[17.6s] Hey there, I'm the 1800 Flowers virtual agent.
[21.1s] Hey there, I'm the 1800 Flowers virtual agent. Do you want to track an order?
[30.3s] Hey there, I'm the 1800 Flowers virtual agent. Do you want to track an order, re
[32.7s] …

[32.9s] heard: "Hey there, I'm the 1800 Flowers virtual agent. Do you want to track an
         order, review delivery info, or ask about something else today?"
[32.9s] saying: "Track and order."
```

The partials are the point. Waiting for a settled transcript after a whole turn means the
agent cannot react to the first clause, and dead air makes the far end think the call
dropped. Partials arrive while the far end is still talking, with `speech-start`,
`partial` and `final` events sharing one `utteranceId`, so a caller can start generating on
the first clause and discard the stub if it is revised.

SSE rather than WebSocket because the traffic is one-directional and `curl -N` can read it,
which makes the whole thing debuggable from a terminal.

Two things worth knowing:

- **Streaming needs a backend that accepts growing audio.** Whisper only makes sense on a
  settled utterance, so it emits no partials and the stream degrades to `final` only. This
  is why parakeet-redux is the default rather than merely a faster alternative.
- **Double-talk is still hard.** The far end fires a new prompt within a couple of seconds
  of ours, and a barge-in that is correct for a human is wrong for an impatient IVR: the
  far end heard our reply ("Did you say track in order?") but then said we cut out. The
  honest fix is to answer from a partial rather than a final, which the stream now makes
  possible; the agent is currently conservative and answers on `final`.

### Speech recognition: parakeet-redux

`moondream/parakeet-redux` is NVIDIA's parakeet-tdt-0.6b-v3 quantised to 1.58-bit ternary
weights — every encoder weight is -1, 0 or +1, so the hot loop is multiply-free. 178 MB,
CC-BY-4.0.

Measured here, on one 3.5 s utterance, AVX2 only (no AVX-512 VNNI, so well short of the
advertised 113x):

| backend | turn latency | transcript |
|---|---|---|
| **parakeet-redux** | **0.55 s** | `Are you the person who placed the order, or are you the recipient?` |
| faster-whisper (small.en, int8) | 3.22 s | identical |
| openai-whisper (turbo, CPU) | ~15 s | identical |

5.9x faster than faster-whisper on this box, and identical output. On AVX-512 VNNI the
model card reports 113x realtime; this machine got 12x, which is still ~0.25 s for a phone
utterance.

It is not strictly better than faster-whisper at everything. The ternary encoder's acoustic
margin is thinner, and the model card is candid that it loses on noisy audio (9.04 vs 6.72
WER on MUSAN at 0 dB SNR). Telephone audio is noisy. So faster-whisper stays installed as a
fallback, and `createStt()` picks parakeet-redux when its venv is present.

Also, unlike Whisper, parakeet output is lowercased and unpunctuated — it inherits the
original's conventions. Fine for a model, less pleasant to read in a log.

```
$ python3 -m venv ~/.local/share/gvoice/asr-venv
$ uv pip install --python ~/.local/share/gvoice/asr-venv/bin/python moondream
```

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