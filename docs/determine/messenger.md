# Facebook Messenger — library determination

**Date:** 2026-10-25
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a `mycord-messenger-skill`.

## Decision

**Drive the real Messenger web client (`facebook.com/messages`) in a dedicated Chrome
profile over CDP, and read/send through the page's own client — with the Lightspeed/DGW
wire protocol (`messagix`, from `mautrix/meta`) held as a deliberate second phase for
high-volume reads and history backfill.** The runner-up is `messagix`: it is the only
actively maintained, feature-complete protocol implementation, and it lost on fit rather
than quality — Go, AGPL-3.0, Matrix-shaped glue, and no call support.

The candidate the brief pointed at first, `fbchat-muqit` (the `fbchat` lineage), was
rejected on a defect not fixable from the outside: since Messenger's default E2EE cutover
it can no longer send to a person at all — its own README says so — and it carries no
crypto stack, so encrypted DMs are both unsendable and unreadable. Node's `fca-unofficial`
family *is* actively maintained and *does* ship Signal, but it is a 34 MB installed
per-fork reimplementation with a native binary and is not a foundation to own; it is
evidence that the E2EE work is possible, not a dependency to adopt.

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
|---|---|---|---|---|---|---|---|
| **Web client over CDP** (`facebook.com/messages`) | 4 (web client) | yes — DOM/AX tree of the rendered thread | yes, by scrolling; live via `Network` → `webSocketFrameReceived` | yes — composer, real client, real E2EE | **yes** — WebRTC in-page, both PCM directions proven locally | yes (cookies only) | **chosen** |
| `messagix` (`mautrix/meta`, Go) | 2 (reverse-engineered protocol) | yes | yes, incl. E2EE backfill | yes, incl. E2EE | no | yes (cookies `datr`/`c_user`/`sb`/`xs`) | **runner-up** — best protocol library that exists, but Go, AGPL, and Matrix-shaped glue |
| `fbchat-muqit` (PyPI 1.2.2) | 2 | yes (GraphQL) | yes (`fetch_thread_messages`) | **group/room/page only** — not person-to-person | no | yes | rejected — E2EE cutover breaks DMs; no crypto at all |
| `fbchat` (PyPI 1.9.7, `fbchat-dev`) | 2 | legacy | legacy | legacy | no | yes | rejected — unmaintained since 2020, repo says "unmaintained notice" |
| `facebook-chat-api` (npm 1.8.0) | 2 | — | — | — | no | yes | rejected — repo archived, last publish 2020-04-03, last push 2021-12-24; MQTT v3 + `request` |
| `fca-unofficial` / `ws3-fca` / `@dongdev` / `@eryxenx` family | 2 | yes | yes | yes incl. E2EE | no | yes | rejected as a base (see below); useful as an E2EE reference |
| `fb-messenger-e2ee` (npm 0.1.8) | 2 | yes | yes | yes (E2EE) | no | yes | rejected — 14★, one author, GitHub-dep on a dead fork, 340 downloads/mo |
| Ferdium (`messenger` recipe) | 5 | yes | yes | yes | yes (same webview) | yes | **fallback** — recipe v1.8.6 already points at `facebook.com/messages`; use only when a session must be shared with other Ferdium services |
| Messenger Platform API | 1 | yes | no | yes | yes (WebRTC over Graph) | **no — needs an app + Page** | excluded by the hard bar |

Nothing in this table is inferred from a README alone where I could check it; the evidence
for each row is in the sections below, with the exact version, date, or probe.

## Why this one

### 1. The protocol inside the browser is no longer MQTT, and that matters

The brief's first preference was MQTT-over-WebSocket. That is now the *legacy* path, and
the brief's own hint — "check whether it still works" — has a negative answer.

- `fbchat-muqit` still speaks MQTT-over-WebSocket to `edge-chat.facebook.com:443` with
  `transport="websockets"`, `ProtocolVersion.V31`, client id `mqttwsclient`, publishing
  `/ls_req` payloads (`fbchat_muqit/muqit.py`, lines 70–221 in the 1.2.2 sdist).
- The current web client does not. `mautrix/meta` v0.2610.0 builds its socket URL from
  `dgw_lightspeed = https://gateway.<host>/ws/lightspeed` and appends `x-dgw-*` query
  parameters (`pkg/messagix/endpoints/facebook.go`, `pkg/messagix/dgw/socket.go:500–544`).
- Independently reported, and unanswered by the maintainer, in `fbchat-muqit` issue **#39**
  ("Where does Facebook still use MQTT + standard JSON (vs Lightspeed?)", opened
  2026-02-06): browser traffic shows "only Lightspeed's binary protocol and `/ls_resp`
  with Thrift-encoded payloads", while the library expects JSON on `/t_ms`. The reporter
  closed with "No, given it up" (2026-03-02); the issue is still open.
- Both hosts answer without credentials, so the endpoints are live: `gateway.facebook.com
  /ws/lightspeed` → `HTTP/2 200` (`server: proxygen-bolt`); `edge-chat.facebook.com/chat`
  → `502` for a plain GET, as expected for an upgrade-only endpoint.

So the two live options are the *web* client (Lightspeed + Thrift, whatever the page will
render) and a library that speaks Lightspeed. `fbchat-muqit` speaks the old one and
publishes into a socket type the current client no longer opens.

### 2. `fbchat-muqit` cannot message a person, by its own admission

From the 1.2.2 sdist `README.md` and the PyPI description:

> Due to `end-to-end` encryption sending messages to other Users is not supported
> anymore. You can send messages to Group Chat, Room Chat and to pages.

Verified in the code, not just the README: the package has **no cryptography dependency and
no decrypt path**. `requires_dist` is `aenum, aiofiles, aiohttp-socks, aiohttp, aiomqtt,
brotli, filetype, msgspec, puremagic, yarl` — no libsignal, no Noise, no curve25519. A grep
for `decrypt|cipher` across the package returns nothing; the only E2EE token in the tree is
the string `E2EE_CUTOVER` in `models/thread.py`, a folder enum. This is a plaintext client
in a product where the default chat is now encrypted. It also pins protocol constants in
source — 26 `version_id` literals in `messenger/client.py`, four of them annotated "EXACT
from capture" — precisely the kind of thing the Lightspeed migration invalidates.

Its maintenance signals are otherwise reasonable — PyPI 1.2.2 uploaded 2026-04-30, GitHub
pushed 2026-09-22, 83★, GPL-3.0 — which is exactly why the E2EE hole is decisive rather
than "it looks abandoned". I installed it in a throwaway venv (Python 3.13) and confirmed
90 public `Client` methods including `send_message`, `fetch_thread_messages`,
`search_message`, `send_files_from_path`, `react`, `unsend`; the surface is real. It is
simply the wrong half of the product now. On a cookie jar with no login it fails cleanly
and immediately (`AuthenticationError: … 'async_get_token' not found`), so its failure mode
is legible — that is a point in its favour, just not enough.

### 3. The Node FCA family is alive, ships Signal, and is still not the base

This was the strongest surprise and the reason the brief's option 3 is not a footnote.

- `@eryxenx/fca` 1.2.0 (2026-10-23), `@dongdev/fca-unofficial` 4.0.3 (2026-04-11),
  `sagor-fca` 2.1.0 (2026-10-10), `ws3-fca` 3.5.2 (npm 2025-08-31, registry `modified`
  2026-05-15) — all actively published, all descendants of `fca-unofficial` 1.3.10 (2020).
- `@eryxenx/fca`'s tarball contains a genuine E2EE stack: `src/api/socket/e2ee/{crypto,
  ratchet,protocol,store,index}.js` plus a native engine under `e2ee/native/`, and
  `@signalapp/libsignal-client 0.70.0` in its dependencies. `fb-messenger-e2ee` packages
  the same idea (libsignal-clients 0.70.0, Noise frames, WA-binary, sender keys).
- Download volume is small but non-zero: `@eryxenx/fca` 4,388/month, `sagor-fca` 1,259,
  `fb-messenger-e2ee` 340 (npm downloads API, 30 days to 2026-10-22).

Why not adopt it: `@eryxenx/fca` is 34 MB installed, carries `sqlite3`, `sequelize`,
`koffi`, `totp-generator` and a **precompiled Linux/Windows native binary** that its own
README says fails on macOS/ARM, plus a `github:` dependency on a fork in `fb-messenger-e2ee`'s
case. Every one of these forks pins specific `version_id`/`doc_id` constants that break when
Meta ships. That is a maintenance relationship with a stranger's reverse-engineering, at
higher risk than reading the page the vendor already maintains. npm registry metadata and
tarball contents for all four are verified; **GitHub stars for `@eryxenx/fca`, `sagor-fca`
and `ws3-fca` are unverified** — GitHub's API rate-limited those lookups mid-run. Only
`fb-messenger-e2ee`'s GitHub metadata came back (14★, via the search API), which is itself
the least-used of the four.

`fb-messenger-e2ee`'s GitHub repo is small — 14★, 8 forks, created 2026-04-29, AGPL-3.0,
pushed 2026-09-24 — and it declares `fca-unofficial: github:VangBanLaNhat/fca-unofficial`
while npm's `fca-unofficial` 1.3.10 is from 2020. One author, one quarter old. Not a base.

### 4. `messagix` is the best protocol library in existence for this, and still phase 2

`mautrix/meta` is the only actively maintained, feature-complete Messenger *protocol*
implementation: v0.2610.0 released 2026-10-16, pushed 2026-10-25, 456★, AGPL-3.0, monthly
releases, and its changelog is a list of live protocol repairs ("Fixed connecting after a
breaking change on the Messenger servers", v26.08.1). It is importable as a library through
the Go module proxy (`go.mau.fi/mautrix-meta`, latest `v0.2610.0`; Go toolchain 1.27.1 is
installed here), and `pkg/messagix` is a clean, re-usable core: `messagix.NewClient(cookies,
logger, cfg)`, `Client.Connect`, `Client.LoadMessagesPage`, `ExecuteTasks` with typed tasks
(`SendMessageTask` label `46`, `FetchMessagesTask` `228`, `SendReactionTask` `29`,
`EditMessageTask` `742`, 34 task labels in `socket/task.go`), E2EE via `whatsmeow` +
`libsignal`, and encrypted-history backfill. `cmd/lscli/lscli.go` is a standalone
decoder for Lightspeed payloads — direct evidence the wire format is understood.

Caveats that keep it at runner-up: it is **AGPL-3.0** with a `LICENSE.exceptions` file
granting only Beeper and Element relief, so a hypothetical product that embeds it inherits
the AGPL; `pkg/messagix/messengerlite.go` and `loginerrors` import
`maunium.net/go/mautrix/bridgev2`, so even the "core" drags a Matrix bridge dependency; and
everything about its shape (portals, ghosts, backfill) is Matrix glue, not CLI verbs. It
also has **no call support** — grep across `pkg/` for `webrtc|/onevc|rtc_multi` finds only
unrelated words. Its real value to us is as **the reference implementation**: when a
protocol question comes up, `messagix` has the answer, and `lscli` can decode any payload a
`Network.webSocketFrameReceived` dump produces.

### 5. Why the web client wins anyway

The page is the vendor's own client. It speaks Lightspeed, it does E2EE, it renders threads
and attachments, and — the deciding factor — **it can make calls**. Everything the protocol
route would need to be rebuilt by hand is already running.

The engineering cost is DOM fragility, and that is a real cost: message rows are
`role=grid` / `role=row` / `role=gridcell` with `aria-label="Messages in conversation with
…"`, and the conversation list is `a[href^='/t/']` (Ferdium's own recipe counts unread that
way; third-party exporters key off the same structures). Meta renames classes constantly,
which is why the skill must read **roles and accessible names, never hashed classes** — and
why it should prefer the CDP `Accessibility` domain (`getFullAXTree`, verified present in
the protocol JSON) over CSS.

The existing `ferdium-skill` CDP driver (471 lines of stdlib-only Python: `Browser`,
`Target`, `TargetSession.eval`, `Page.type/click/wait_for/scroll`) is exactly the primitive
needed and has no dependencies. But Ferdium is not required: `google-chrome 156.0.8078.47`
is installed, and `mycord-slack-skill`'s `capture_session()` already implements the
profile-copy + `--headless=new` + `Network.getAllCookies` pattern this skill should reuse
verbatim.

One date-driven fact changes the brief's wording: **messenger.com is gone.** Meta announced
the standalone site's retirement in February 2026 for April 2026 and now redirects to
`facebook.com/messages` (TechCrunch 2026-02-19; Facebook Help 804132271957789). Verified
live: `messenger.com` → `301` → `www.messenger.com` (200, interstitial), and
`www.facebook.com/messages` without a session → `302` to `/login.php?next=…`. Ferdium's
recipe was updated to `https://facebook.com/messages` in recipe v1.8.6 (PR ferdium-recipes#669,
merged 2026-02-24), and that update broke messenger-only accounts (ferdium-app#2397,
2026-04-29). So the skill targets `facebook.com/messages` and should key its webview/target
match on `facebook.com`, not `messenger.com`.

## Voice assessment

**Verdict: possible — and this is the only medium in the fan-out whose calls are reachable
end-to-end without a vendor API.** Everything below was proven on this machine today, in
local loopback, with no account and no network peers. Which real Messenger call transport
(Meta's own SFU) will look like is **expected, not verified** — that needs a signed-in
account, which I do not have.

Messenger calls in the web client are ordinary WebRTC (`RTCPeerConnection`, SDP offer/answer,
opus). Signalling rides the same socket as messaging — every Messenger client's topic list
includes `/webrtc`, `/webrtc_response`, `/rtc_multi` and `/onevc` (`fbchat_muqit/muqit.py`
lines 168–186; `fbchat` 2.0.0a4's `_listen.TOPICS`; `rustybook-messenger` 0.2.1's
`CONNECT_TOPICS` — all three read directly today, same four topics). The signalling
payloads themselves are **not documented anywhere I could verify** — fbchat's own source
tags its call topic `/t_rtc` with "Data about user-to-user calls … TODO: Investigate the
response from this! (A bunch of binary data)", and that topic is commented out in current
clients. A protocol-only client would therefore have to reverse-engineer call signalling
from scratch. **The page does not** — it has already done it.

### What I proved locally (executed, reproducible)

Two independent capture paths and one injection path, all inside a CDP-driven Chrome:

| Shape | Probe | Result |
|---|---|---|
| **Inject TTS as microphone** | `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=/tmp/fake-mic.wav%noloop --use-fake-ui-for-media-stream`; page calls `getUserMedia` | `trackLabel:"Fake Default Audio Input"`, AudioWorklet tap saw `meanAbs 0.11639 / peak 0.2919` — real, non-silent PCM |
| **Inject arbitrary PCM (TTS) with no file** | AudioWorklet generator fed Float32 chunks pushed from JS → `createMediaStreamDestination()` → replaced `navigator.mediaDevices.getUserMedia` | RTP carried it: receiver `audioLevel 0.4539`; sink readback `peak 0.4564 rms 0.3169` |
| **Capture far-end audio in-page** | AudioWorklet `process()` tap on the receiving peer's `receiver.track`, connected to a muted gain → `destination` so it is pulled | `workletChunks=120`, `workletMean 0.2781 peak 0.5025` while `audioLevel 0.5072` |
| **Capture far-end audio out-of-page** | page plays the remote track in an `<audio>` element into a PipeWire `module-null-sink`; agent reads `parec --device=<sink>.monitor --format=s16le --rate=48000` | `65536 samples peak 0.5082 rms 0.3529` |
| **Full call loop** | synthetic mic → `RTCPeerConnection` → remote playout → null sink → `parec` | `pc=connected/connected`, `in pkts=123`, sink `peak 0.5082 rms 0.3529` |

Two traps that cost me several probes and will cost the implementer the same, so they are
recorded as findings rather than as prose:

1. **An analyser/Worklet on a receiver's track reads 0 unless something pulls the track to
   an audio destination.** With `src.connect(worklet)` alone the worklet still ran and
   `getStats` showed `packetsReceived=125, bytes=8965` while `audioLevel=0` — media arriving,
   nothing decoding it. Adding `node.connect(mutedGain); mutedGain.connect(ctx.destination)`
   (gain 0) fixed it. Connect the tap to `destination` through a zero gain, always.
2. **Headless is not the problem.** I first measured silence in `--headless=new` and suspected
   headless audio; re-running the same page headed on `DISPLAY=:1` with `PULSE_SINK` set to a
   null sink gave byte-identical results, and the headless run against the same sink also
   produced real PCM. The variable was **whether a sink existed and whether the graph was
   pulled** — not headed vs headless. Do not "fix" this by going headed.

Also verified: `navigator.webdriver === false` in a plain `--headless=new` launch, so the
CDP path is not self-identifying (Blink sets it only for the automation override or the
automation-controlled feature, `third_party/blink/renderer/core/frame/navigator.cc:81–87`).
CDP can inject before the app boots (`Page.addScriptToEvaluateOnNewDocument`), evaluate
(`Runtime.evaluate`), expose a channel back to the driver (`Runtime.addBinding` — "bindings
survive reloads"), read the DOM (`DOM.*`, `DOM.setFileInputFiles`), read accessibility roles
(`Accessibility.getFullAXTree`), drive input (`Input.insertText`, `dispatchKeyEvent`), and
observe/replay socket frames (`Network.webSocketFrameReceived` / `webSocketFrameSent`,
`Network.getResponseBody`). All of those names were read out of the live
`devtools-protocol` JSON, not recalled.

Note what CDP **cannot** do: there is no "give me decoded audio" domain. `WebAudio` exposes
`getRealtimeData` (per-context meter values, not samples), `Media` exposes player metadata,
and `Cast` can mirror a tab. So capture is either an in-page tap or a sink monitor — the two
paths above.

**Comparison to the two existing precedents.** `gvoice/` owns the media plane off-browser in
`werift` because Google Voice hands out routable WebRTC. Messenger cannot follow that shape:
signalling is undocumented and E2EE-adjacent, so an off-browser stack would need both
signalling and the Signal layer before the first RTP packet. `signal-cli`'s Unix PCM socket
is the *right* contract but the wrong layer — there is no native Messenger daemon to expose
it. What Messenger *can* match is `gvoice`'s **control surface**: `control.ts` serves
`/events` (SSE) plus `/say`, `/press`, `/hangup`. The Messenger equivalent is a small
supervisor that holds the CDP page, exposes `/events` (transcripts as they arrive),
`/say` (write PCM into the generator worklet), and `/hangup`. Same design, different
transport — exactly the "reuse the design, not the code" note already in `docs/roadmap.md`.

### Roadmap consequence

Because voice is reachable, Messenger does **not** need an ASR/TTS entry on
`docs/roadmap.md` today. What it needs instead is an entry for the *shared speech layer*
`docs/roadmap.md` already argues for (R1/R2's sequencing note): Messenger would be the second
medium wanting Piper + Parakeet, which is the trigger that note describes. If a roadmap line
is wanted before phase 3 starts, it should be that shared service — not a Messenger-specific
"voice someday" item that implies the transport is still unknown.

## Implementation shape

**Language:** Python 3.11+, stdlib only at runtime for the CDP half (the existing
`ferdium-skill` driver is dependency-free; `mycord-slack-skill` adds only `slack-sdk`). A
second process — TypeScript, or Python with a WebSocket — owns the voice plane. Voice is
where Node is unavoidable-ish, but not actually required: the page does the WebRTC work, so
the voice supervisor only needs a PCM socket and an HTTP surface.

**Package layout** (mirroring `mycord-slack-skill`, which is the closest sibling):

```
mycord-messenger-skill/
├── SKILL.md                     # agent-facing, name + verbs + safety rails
├── README.md                    # install/debug notes
├── pyproject.toml               # requires-python >=3.11, no runtime deps
├── src/mycord_messenger/
│   ├── cli.py                   # argparse verbs, JSON out, exit-code contract
│   ├── session.py               # cookie capture + validity, 0600/0700
│   ├── cdp.py                   # profile staging, launch, Runtime/A11y helpers
│   ├── threads.py               # conversation list -> ids, names, unread
│   ├── reads.py                 # thread transcript extraction (roles, not classes)
│   ├── sends.py                 # composer typing + Enter, attachment upload
│   └── voice.py                 # bridge to the call supervisor (phase 3)
└── voice/                       # separate supervisor: WebRTC + PCM
    ├── call-host.ts             # injects gum override, taps receiver.track
    └── control.ts               # /events, /say, /hangup  (gvoice control.ts shape)
```

**Verbs** (the 5–10 that matter; anything else is a passthrough to `eval`-style JS, the
way Slack's CLI is a passthrough rather than a curated list):

| Verb | What it does |
|---|---|
| `login` | launch headless Chrome on a **copy** of the signed-in profile, wait for `facebook.com/messages`, read the cookie jar (`datr`, `c_user`, `sb`, `xs`, `fr`, `presence`), write `session.json` 0600 in a 0700 dir. Print kinds and lengths only. |
| `doctor` | Chrome present? profile present? cookies present? session still accepted (`/messages` does not redirect to `/login.php`)? Exit codes `2`/`3` for "no Chrome"/"no session, needs a human". |
| `threads` | conversation list with stable thread ids, names, unread counts, last-message preview. |
| `read <thread> [--limit N] [--since T]` | transcript, oldest→newest, as JSON lines. Scroll-to-load for older. |
| `search <query>` | page's own search, or local scan of cached transcripts (prefer the page's search box). |
| `send <thread> "<text>"` | type into the composer, **verify readback matches exactly**, press Enter, confirm the message appears. Refuse on mismatch (`ferdium-skill`'s `send_message` already does exactly this dance — copy it). |
| `attach <thread> <path> [--caption …]` | `DOM.setFileInputFiles` on the composer's file input, then send. |
| `react <thread> <message> <emoji>` / `unsend <message>` | hover → "More" → menu item, by accessible name. |
| `watch` | tail new messages by polling the DOM, or subscribe via `Network.webSocketFrame*` for lower latency. |
| `call <thread> [--say …]`, `voice status` | phase 3; refuses unless a voice supervisor is running. |

**Auth/session capture.** Reuse `mycord-slack-skill/src/mycord_slack/cdp.py::capture_session`
almost verbatim: copy the profile (never write to the live one), launch with
`--remote-debugging-port=0`-style free port + `--user-data-dir=<copy>`, poll `/json/version`,
attach, `Network.getAllCookies`, filter `.facebook.com`, persist. Important: `xs` is the
session cookie and is **HttpOnly** — `document.cookie` cannot see it — so cookie-jar access
over CDP is mandatory, exactly as `google-voice-skill/src/gvoice_skill/cdp.py` documents for
Google Voice. No password login anywhere: `facebook.com/messages` without a session
redirects to `/login.php?next=…` (verified), and driving password login would be the single
most likely trigger for an account checkpoint.

**Failure modes that will bite**

- **Account lock / checkpoint.** This is the dominant risk and it is not a rate-limit
  problem — Meta's own bridge documentation warns that it "may decide your account has
  suspicious activity and block you until you do some tasks like completing a captcha,
  adding a phone number or resetting your password", and recommends 2FA to reduce it.
  A brand-new datacenter-IP login is the classic trigger. Mitigations, in order of value:
  (1) **never log in** — copy an existing signed-in session from a residential machine;
  (2) drive at human pace — no loop of sends, no bulk reads, one thread at a time, jittered
  delays; (3) keep the page open so the client maintains its own socket instead of
  re-authenticating per command; (4) fail fast and loudly on a `/login.php` redirect rather
  than retrying; (5) let the human re-authenticate. The Slack skill has the right precedent:
  `session --check` is the only honest validity test, and the repair is a human in a browser.
- **E2EE cutover.** Messenger force-enables encryption per chat, and `messagix` had to add
  *detection* for that ("support for automatically detecting when Messenger force-enables
  encryption in a chat", changelog v0.3.0) because the API surface differs. In the page this
  is invisible — another reason to read the page. Do not build anything that assumes
  plaintext MQTT deltas.
- **DOM drift.** Roles and accessible names survive redesigns better than classes, but
  neither is a contract. Every read must return "found nothing, here is the accessible-name
  inventory I did see" rather than an empty success — silent empty results are how a
  scraper rots undetected.
- **Hibernation / webview death.** In Ferdium a hibernating service has no webview
  (`mycord-ferdium`'s own error message says so). With a dedicated Chrome profile there is
  no hibernation, which is a reason to prefer Chrome over Ferdium as primary.
- **Attachment upload.** The composer's file input is often `display:none`; use
  `DOM.setFileInputFiles` with a node id, not a click-then-dialog. Verified as available in
  the protocol; not yet exercised against Facebook's DOM.
- **Re-authentication is not refreshable.** Cookie lifetime is long (`datr` especially) but
  revocation is independent. Repair = another `login` on a machine where a human is signed in.

**Low-traffic rules for this skill specifically** (the brief asked for this explicitly):
read-only by default; every write requires an explicit `--yes`-class acknowledgement naming
thread and text; a per-hour send cap enforced in the CLI, not by convention; never enumerate
the whole inbox in a loop when one thread was asked for; never touch `marketplace`, friend
requests, or profile surfaces; and no speculative background polling — `watch` is opt-in and
prints its own polling cadence.

## Open questions

1. **Account.** This needs a real Facebook account whose session can be exported from a
   signed-in browser profile on a residential connection. Decide which account (a throwaway
   is strongly preferred; the account is one checkpoint away from being lost) and whether
   `xs` export from an existing Chrome profile on this machine is acceptable, or a dedicated
   profile must be created interactively first.
2. **Consent for the voice work.** Calls are real, attributed calls to a real person.
   `docs/roadmap.md`'s constraint — "confirm with a human before anything that reaches
   another person" — applies twice here (dialling *and* speaking). Decide whether the first
   live call is to a number the operator controls (e.g. a second handset on speaker) or to a
   consenting human.
3. **Whether phase 2 (`messagix`) is wanted at all.** It buys reliable history/backfill and
   survives DOM changes, at the cost of AGPL and a Go module in a Python repo. My
   recommendation is to defer it until the CDP reader proves insufficient — but if
   high-volume history is a near-term need, say so before the skill is built, because it
   changes the language split.
4. **Which ASR/TTS backend for calls.** `gvoice/` already has parakeet-redux and Piper
   working, and `docs/roadmap.md`'s sequencing note asks for that layer to be extracted
   once into a small local service rather than wired per-skill. Messenger is the second
   medium that needs it, so this may be the point to extract it.
5. **Ferdium or not.** Ferdium is only worth it if the same operator wants Messenger sharing
   a session with other Ferdium services. Otherwise a dedicated profile is simpler and has no
   hibernation. Needs a preference, not research.

---

## Appendix — evidence log

Registry / repo facts, checked 2026-10-25:

| Claim | Source |
|---|---|
| `fbchat-muqit` 1.2.2, `requires_python >=3.10`, uploaded 2026-04-30, GPL-3.0-or-later, no crypto deps | `pypi.org/pypi/fbchat-muqit/json`; installed into `/tmp/det-venv` (Python 3.13) and imported |
| `fbchat` latest 1.9.7 (2020-06-08); repo `fbchat-dev/fbchat` last commit 2020-09-23 "Add unmaintained notice" | PyPI JSON; GitHub API (via `carpedm20/fbchat` 301 → repo id 34165498) |
| npm `facebook-chat-api` 1.8.0 (2020-04-03), repo archived, `mqtt ^3` + `request ^2` | `registry.npmjs.org/facebook-chat-api`; GitHub API `archived=true` |
| `mautrix/meta` v0.2610.0 (2026-10-16), 456★, AGPL-3.0, pushed 2026-10-25; importable as `go.mau.fi/mautrix-meta` | GitHub API; `proxy.golang.org/…/@latest`; tag tarball downloaded and read |
| `messagix` dials `https://gateway.<host>/ws/lightspeed` with `x-dgw-*` params; MQTT topics `/ls_req`, `/ls_resp`, `/t_ms` are legacy | `pkg/messagix/endpoints/facebook.go`, `pkg/messagix/dgw/socket.go`, `pkg/messagix/socket.go` in v0.2610.0 |
| `messagix` has no call support | grep `webrtc|/onevc|rtc_multi` over `pkg/` — no matches |
| Messenger web client is now `facebook.com/messages`; messenger.com retired April 2026 | live `301`/`302` probes; TechCrunch 2026-02-19; Facebook Help 804132271957789; ferdium-recipes#669 (merged 2026-02-24), ferdium-app#2397 |
| Ferdium ships `messenger` recipe 1.8.6 with `serviceURL: https://facebook.com/messages` | extracted `messenger.tar.gz` from the local 7.2.3 flatpak |
| `@eryxenx/fca` 1.2.0 ships real E2EE + native binary; `fb-messenger-e2ee` 0.1.8 ships libsignal-based E2EE; download volumes | npm registry JSON; tarballs downloaded and inspected |
| CDP has no decoded-audio surface; `Runtime.addBinding`, `Page.addScriptToEvaluateOnNewDocument`, `DOM.setFileInputFiles`, `Accessibility.getFullAXTree`, `Network.webSocketFrameReceived` all exist | `ChromeDevTools/devtools-protocol` `browser_protocol.json` + `js_protocol.json` |
| Chrome 156.0.8078.47; fake-media switches present; `navigator.webdriver === false` under `--headless=new` | local binary + live probe |
| Four local loopback probes proving TTS-injection and far-end capture | scripts `/tmp/probe_mic2.py`, `/tmp/probe_call.py`, `/tmp/probe_call2.py`, `/tmp/probe_inject.py`; full command output in `/tmp/det-messenger.log` |

**Not verified:** the shape of Meta's real call signalling payloads; whether the production
Messenger call is a single peer connection or an SFU; whether `facebook.com/messages`
tolerates a headless session for long periods without a checkpoint; GitHub star counts for
the three FCA forks (API rate-limited mid-run); everything that needs a signed-in account.
