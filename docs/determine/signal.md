# Signal — library determination

**Date:** 2026-10-25
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a mycord-signal-skill.

All claims below were checked on this host or against the publishing registry /
upstream source on the same day. Anything I could not verify is labelled
**UNVERIFIED**. Nothing was installed, no device was linked, no message or call
was placed to any real person.

---

## Decision

Drive Signal through **`signal-cli` as a long-running JSON-RPC daemon** (the account's
own linked-device credential, no bot/app/registration), and get call audio from
**`signal-call-tunnel` built from `michaelburch/signal-call-tunnel` `main` — PR #5's
pipe-audio backend plus its ICE device-id follow-up** — the same
`SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe` Unix-socket PCM shape the existing
`~/.agents/skills/signal-cli/SKILL.md` documents. The runner-up, **presage**
(the Rust `libsignal-service` client used by Whisperfish), loses on exactly one axis
that matters most here: it has no call stack at all, so voice would be a research
project instead of a socket. A CDP/Ferdium web client is not a candidate at all —
Signal has no web client (verified below), so there is no page to drive.

Voice is **possible, and already proven on this machine**: I booted the installed
tunnel in pipe mode just now, got `{"type":"ready","inputDeviceName":"unix:/tmp/user/1000/signal-call-42.sock",…}`,
connected to that Unix socket, and pushed 50 × 960-byte 440 Hz PCM windows into it.
What I could **not** verify is media on a live call, because the only account on this
host is dead (`+14153356728 … NotRegisteredException`) and re-linking needs a phone.

---

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **signal-cli ≥ 0.14.9** + **signal-call-tunnel** (fork `main` = PR #5 + ICE fix) | 3 — native/daemon client driven by CLI + JSON-RPC | yes (`receive`, `daemon`, JSON-RPC `subscribeReceive`) | **live only, no server-side history** (see below) | yes (`send`, attachments, quote, edit, reactions, polls, stories, typing, receipts, groups, contacts, profile, username, sticker packs) | **yes, 1:1 bidirectional PCM over a Unix socket** (verified boot + socket I/O; live-call path **UNVERIFIED**) | **yes** — linked device, same identity | **CHOSEN** |
| signal-cli + `bbernhard/signal-cli-rest-api` | 3 — HTTP wrapper over the same binary | yes | same as above | yes | **no** call endpoints found in the REST API source | yes | no — adds Docker for nothing we don't already get |
| presage (whisperfish) | 2 — maintained reverse-engineered Rust lib | yes | local sqlx store, `Manager::clear_thread`, no message search API seen | yes | **no** — no call support in presage or libsignal-service-rs | yes (secondary-device link implemented) | runner-up; loses voice |
| `libsignal-client` / `@signalapp/libsignal-client` 0.106.0 | 1/2 — crypto primitives only | no | no | no | no | n/a | not a client; "Use outside of Signal is unsupported" per upstream README |
| Signal Desktop driven over CDP | 4 | UI only | UI only | UI only | UI only | yes | brittle, and worse than a JSON-RPC socket on every axis |
| Ferdium webview | 5 | UI only | UI only | UI only | UI only | yes | **impossible** — Signal has no web client; `ferdium/ferdium-app#1908` ("Add Signal Messenger") was closed `not_planned` the day it was filed (2024-10-02), and `ferdium/ferdium-recipes` ships no signal recipe |
| `signalbot` (PyPI 2.2.1) | 3 — framework over signal-cli-rest-api | yes | same | yes | no | yes (over REST API) | wrapper over a wrapper; rejected |
| `pysignalclirestapi` (PyPI 0.3.25) | 3 — thin REST client | yes | same | yes | no | yes | same |
| Official Signal API / cloud API with account credentials | — | — | — | — | — | **does not exist** | Signal publishes no third-party client API; `signalapp/libsignal` is crypto libs only |

Notes that belong in the table's footnotes:

* `neonize`/Baileys-class equivalents do not exist for Signal. There is no large
  maintained JS/Python full-client library other than wrappers around signal-cli
  or REST wrappers around it.
* crates.io **`presage` 0.3.0 (2023-05-17) is an unrelated crate** ("lightweight
  library for designing event-based systems", repo `alightgoesout/presage`). The
  Signal library is only on GitHub (`whisperfish/presage`, 272★, AGPL-3.0, pushed
  2026-10-25) and must be consumed as a git dependency.
* `libsignal-protocol` on crates.io is 2019 vintage (2.8k downloads) — not a client.

---

## Why this one

### 1. It is the only candidate with a call stack

The deciding evidence is structural, not opinion. Signal calling is not SIP and not
plain WebRTC: media keys come from an x25519 DH over the *paired Signal identity
keys*, then HKDF-SHA256 with the literal info string
`Signal_Calling_20200807_SignallingDH_SRTPKey_KDF` (read in
`third-party/ringrtc/src/rust/src/core/connection.rs:2240-2270` inside the tunnel's
submodule). RingRTC — Signal's own calling library — is what implements that, and
`signal-call-tunnel` is a thin Rust wrapper around it. Nothing else on the candidate
list wraps RingRTC:

* `presage` depends on `libsignal-service` (git rev `72a0987…`) for *messages*; its
  README feature list ends at attachments, and `gh search code` for call handling in
  `whisperfish/libsignal-service-rs` returns only `content.rs` and the protobuf,
  i.e. the `CallMessage` envelope, not media.
* `signal-cli` itself does not implement media either. It **spawns**
  `signal-call-tunnel` per call — one process per call, JSON-lines over stdin/stdout
  (`docs/CALL_TUNNEL.md` in signal-cli, and `CallManager.spawnMediaTunnel()` which
  constructs `new ProcessBuilder(List.of(findTunnelBinary()))` with no
  `.environment(...)` override, so the child inherits signal-cli's environment —
  that is *how* `SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe` reaches the tunnel).

So the real choice is "RingRTC via the tunnel" or "no voice".

### 2. Voice support is upstream in signal-cli, not a personal fork

Verified chain:

* `signal-cli` v0.14.2 (2026-04-04) release notes: *"Add voice calling support by
  @visigoth in #1932"* (merged 2026-04-01, +3123/−1, 25 files).
* `StartCallCommand`, `AcceptCallCommand`, `HangupCallCommand`, `RejectCallCommand`,
  `ListCallsCommand` all implement `JsonRpcLocalCommand`, so they exist both as CLI
  verbs and JSON-RPC methods (checked in `Commands.java` and per-class source at
  v0.14.6 and v0.14.9).
* `subscribeCallEvents` / `unsubscribeCallEvents` are implemented in
  `SignalJsonRpcDispatcherHandler` (the socket/stdin transport) as notifications
  named `callEvent` carrying `JsonCallEvent{callId, state, number, uuid, isOutgoing,
  inputDeviceName, outputDeviceName, reason}` — full source read.
* `CallInfo.State` enum: `IDLE, RINGING_INCOMING, RINGING_OUTGOING, CONNECTING,
  CONNECTED, RECONNECTING, ENDED`.

Two consequences the skill must encode and the existing SKILL.md already warns about:

1. **`subscribeCallEvents` is mandatory.** `CallManager.handleIncomingOffer()` begins
   with `if (callEventListeners.isEmpty()) { … return; }` — an incoming call is
   silently dropped (no tunnel spawned) unless a client subscribed.
2. **`inputDeviceName` / `outputDeviceName` only exist after `ready`.** The tunnel
   binds its socket *before* initialising RingRTC and emits `ready` immediately
   (`main.rs`, `PipeAudioDevice::new(...)` then `start_control_channel(...)`);
   `CallManager.readControlEvents()` stores them into `CallState` on `ready` and
   passes them through on every subsequent `fireCallEvent()`. A client that starts
   a call and reads the *response* may see `null` device names — it must wait for the
   first `callEvent`.

### 3. The pipe backend is verifiably this host's binary, and it is small

Verified:

* Local tunnel source: `~/.local/src/signal-call-tunnel`, branch
  `pipe-audio-backend` @ `2f92426`, which is **byte-identical to the head SHA of
  `visigoth/signal-call-tunnel#5`** (`michaelburch:pipe-audio-backend`,
  `2f924268e1d3083eef70dd82718f1e7e760b13d1`). The fork's `main` also carries a
  follow-up `5a963d1 control: Add sender_device_id to ReceivedIce`.
* Installed `/home/bitnom/.cargo/bin/signal-call-tunnel` sha256
  `aa3353c6…07eb2768` is identical to
  `signal-call-tunnel/target/release/signal-call-tunnel`, and both
  `/usr/local/bin/signal-call-tunnel` and `/opt/bin/signal-call-tunnel` are symlinks
  to it. `strings` shows `src/pipe_audio.rs`, `SIGNAL_CALL_TUNNEL_AUDIO_MODE`,
  `SIGNAL_CALL_TUNNEL_SOCKET_DIR`, and RingRTC logs `RingRTC v2.65.1` at startup
  (submodule describes as `v2.65.1`).
* Pipe contract read from `src/pipe_audio.rs`: 48 kHz, mono, s16le, 10 ms windows =
  480 samples = **960 bytes each way**, one bidirectional `SOCK_STREAM` Unix socket.
  The tunnel is the *listener*; the client connects. Buffers are capped at ~80 ms
  each direction; recording is silence-padded so WebRTC keeps running when nobody is
  attached; playout is dropped, not blocked, when full.
* Socket path: `SIGNAL_CALL_TUNNEL_SOCKET_DIR` or `std::env::temp_dir()` +
  `signal-call-<call_id>.sock` → on this host `$TMPDIR=/tmp/user/1000`, i.e.
  `/tmp/user/1000/signal-call-<id>.sock`, and the reported device name is
  `unix:/tmp/user/1000/signal-call-<id>.sock` for **both** directions (same socket).
* Live probe I ran (not a hypothetical):

```
$ printf '%s\n' '{"call_id":42,"is_outgoing":true,"local_device_id":1,"audio_mode":"pipe"}' \
    | SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe signal-call-tunnel
[INFO  signal_call_tunnel] Audio mode: pipe (socket /tmp/user/1000/signal-call-42.sock)
[INFO  signal_call_tunnel::pipe_audio] pipe audio: listening on /tmp/user/1000/signal-call-42.sock
{"type":"ready","inputDeviceName":"unix:/tmp/user/1000/signal-call-42.sock","outputDeviceName":"unix:/tmp/user/1000/signal-call-42.sock"}
[INFO  ringrtc::core::call_manager] RingRTC v2.65.1
```

  and then a Python client connected, injected 50 × 960-byte 440 Hz windows
  (`pipe audio: peer connected` in the tunnel log) with no PulseAudio involvement —
  I proved this by re-running with `PULSE_SERVER=/nonexistent`, which succeeds in
  pipe mode and **fails** in device mode:
  `pa_context_connect() failed: Connection refused`.

### 4. What the split-brain pin costs (this is the part that will bite)

The branch is now *behind* its own fork's `main`:

* `pipe-audio-backend` (`2f92426`) has no `sender_device_id` on `ReceivedIce`, and
  `main.rs` hard-codes `sender_device_id: 1` when relaying ICE.
* `5a963d1` on the fork's `main` adds and uses it.
* signal-cli v0.14.6 **and** v0.14.9 both send `senderDeviceId` on `receivedIce`
  (verified in `CallManager.handleIncomingIceCandidate`), and RingRTC keys its
  `connection_map` by `sender_device_id` (`core/call.rs:656-733`) — a wrong device id
  is `RingRtcError::ConnectionNotFound(sender_device_id)`.

So the currently installed branch build is at risk on any call where the remote's
device id ≠ 1. This is a **code-read finding, not an observed failure** — I could not
place a call to test it (**UNVERIFIED** in practice). The safe move is to build from
the fork's `main` (`5a963d1`), which is a strict superset: `git merge-base
--is-ancestor pipe-audio-backend main` returns true, and PR #5's state is
`mergeable: true, mergeable_state: clean, comments: 0` but **not merged** upstream.

Additional device-id detail from upstream 0.14.9 (PR #2128, merged 2026-10-06):
`CallManager.buildConfig()` now passes `account.getDeviceId()` instead of the
hard-coded `1` it used in 0.14.6. For a *linked* device (what this skill is) the local
device id is ≥ 2, and the tunnel feeds that value into RingRTC as
`local_device_id` (`create_outgoing_call(...)`) and `receiver_device_id`
(`received_offer`). The regression test is explicit:

```java
assertEquals(7, config.get("local_device_id").intValue());   // buildConfig_usesProvidedLinkedDeviceId
@CsvSource({"1,true","1,false","2,true","2,false","127,true","127,false"})  // buildConfig_readsAccountDeviceId
```

and PR #2128's body states: *"the four linked-device cases fail against unmodified
upstream"*. **Therefore the installed 0.14.6 is the wrong version to pin for voice on
a linked device.** Pin ≥ 0.14.9.

### 5. Version hygiene is not optional

`signal-cli` README, verbatim: *"signal-cli needs to be kept up-to-date… The official
Signal clients expire after three months and then the Signal-Server can make
incompatible changes."* Installed 0.14.6 (2026-07-13) is 3+ months old; latest is
**0.14.9 (2026-10-07)**; repo is alive (5006★, GPL-3.0, pushed 2026-10-24, not
archived). The release asset we need exists: `signal-cli-0.14.9-Linux-native.tar.gz`
(plus `.asc`).

### 6. Rejections, stated plainly

* **Official API with the account's own credentials:** does not exist. Signal's docs
  are protocol specs; `signalapp/libsignal` is the crypto library and its README says
  use outside Signal is unsupported. Instagram-style "user token" has no counterpart.
* **CDP / Ferdium:** Signal never shipped a web client (2017 blog: the Chrome App was
  deprecated in favour of standalone Desktop), so there is no page to drive.
  `ferdium/ferdium-app#1908` ("Add Signal Messenger") was opened and closed
  `not_planned` on 2024-10-02, and `ferdium/ferdium-recipes` has no signal recipe.
  The `ferdium-skill/` SKILL.md also says service pages appear only via webviews and
  that hibernation kills the target — for a platform with no web page at all, this is
  not a fallback, it's a dead end. Signal Desktop over CDP would work in principle but
  gives strictly less than a JSON-RPC socket and cannot produce PCM.
* **presage:** genuinely attractive (native Rust, local SQLite, secondary-device
  link, contacts/groups/attachments, actively pushed 2026-10-25, AGPL-3.0) and it is
  the right answer *if* voice were off the table. It is the runner-up precisely
  because the brief ranks feature breadth first and voice is the differentiator.
  Caveats if it is ever reconsidered: crates.io `presage` is a different crate; the
  Signal one is git-only; `cdsi` pulls `libsignal-net`/boringSSL and collides with
  sqlcipher unless you also patch `libsqlite3-sys` (per its README).
* **`signalbot` / `pysignalclirestapi` / `bbernhard/signal-cli-rest-api`:**
  all rest on the same signal-cli binary. `signal-cli-rest-api` is Docker-first
  (2892★, MIT, pushed 2026-10-22) and I found no call endpoints in `src/api/api.go`
  (`grep -i call` hits only an AUTO_RECEIVE comment and a link-flow description).
  GitHub-wide searches for Python/JS Signal client libraries surface nothing
  maintained either — the only hits are tiny wrappers around the same binary
  (`signal-cli-python-api`, 20★, last pushed 2020).
  Adding a container to reach a binary we already have is negative value, and it
  would *lose* the tunnel's device names, because the REST layer never surfaces them.

---

## Voice assessment

**Verified on this host (no account needed):**

* `signal-call-tunnel` boots in pipe mode, binds
  `/tmp/user/1000/signal-call-<id>.sock`, prints `ready` with both device names,
  initialises `RingRTC v2.65.1`, accepts a client connection
  (`pipe audio: peer connected`), and exits cleanly when stdin closes.
* The PCM contract is real code, not documentation: `SAMPLE_FREQUENCY = 48_000`,
  `NUM_CHANNELS = 1`, `WINDOW_MS = 10`, `WEBRTC_WINDOW = 480`, `BYTES_PER_SAMPLE = 2`
  (`src/pipe_audio.rs`), bidirectional on one `SOCK_STREAM`, tunnel-as-listener.
* Pipe mode needs **no** PulseAudio/PipeWire (`PULSE_SERVER=/nonexistent` still
  reaches `ready`; device mode fails with `pa_context_connect() failed`).
* Agent-controllability: the tunnel is spawned by signal-cli with an inherited
  environment, so exporting `SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe` on the daemon is
  sufficient for every subsequent call. `SIGNAL_CALL_TUNNEL_BIN` overrides discovery
  (checked in `CallManager.findTunnelBinary()`: env → `<install>/bin/` → `PATH`).
* Both directions are agent-driven by construction: read the socket for remote audio
  (playout) and write it for mic audio (recording). That is exactly the shape
  `signal-cli` + `signal-call-tunnel` was built for.

**Not verified (blocked on the account):**

* Media actually flowing on a live call: no `callEvent` `CONNECTED`, no playout bytes
  observed, no ASR/TTS round trip. I did see "no playout" in my probe, which is
  expected because no call existed.
* Whether the *installed* build (branch `2f92426`, which hard-codes
  `sender_device_id: 1` on `receivedIce`) survives multi-device ICE — the
  `sender_device_id` gap above. Building the fork's `main` (`5a963d1`) removes the
  question.
* Group calls and video: `CallManager` creates `CallMediaType::Audio` only, and the
  tunnel's `NullHttpClient` no-ops group-call SFU requests
  (`fn send_request(...) {}`). **Voice is 1:1 audio only.** (This matches the
  existing SKILL.md claim and is now sourced.)
* Screen share / video upgrade: not implemented in this path.

**Shape it follows:** the `signal-cli` + `signal-call-tunnel` PCM-socket shape — the
second of the two working precedents named in the brief — **not** the `gvoice/`
`werift` shape. There is no WebRTC peer connection for us to own; RingRTC owns it
inside the tunnel process and hands us resampled PCM. So `gvoice/src/stt.ts` and
`gvoice/src/tts.ts` are reusable *as a speech layer*, but `werift`, `sip.ts`,
`call.ts` and `registrar.ts` are irrelevant here.

**Nothing goes on `docs/roadmap.md`** — this medium does not need an ASR/TTS roadmap
item. The remaining work is wiring, not transport discovery. (Optional follow-up for
the roadmap, if the speech layer gets extracted per R1/R2: point the shared ASR/TTS
service at this socket instead of `gvoice`'s `werift` tracks.)

---

## Implementation shape

### Language and layout

Python, matching `mycord-telegram-skill/` (uv, `src/` layout, `[project.scripts]`,
`tests/` with fakes, `SKILL.md` next to `README.md`):

```
mycord-signal-skill/
  pyproject.toml                 # mycord-signal-repl = "mycord_signal_repl.cli:main"
  README.md
  SKILL.md
  src/mycord_signal_repl/
    cli.py          # argparse verbs, JSON out, --account
    daemon.py       # spawn/adopt `signal-cli … daemon --socket`, ready-wait, keepalive
    rpc.py          # newline-delimited JSON-RPC 2.0 client (one request, one response,
                    #   plus notification queue for `receive` and `callEvent`)
    store.py        # local SQLite: captured messages, contacts cache, call log
    events.py       # subscribeReceive + subscribeCallEvents demux
    audio.py        # Unix-socket PCM plane: 960-byte windows, ring buffers
    session.py      # persistent REPL state (like telegram's `eval`)
  tests/            # fake RPC server over a socketpair; no real account
```

Why Python and not Rust: the transport is a newline-JSON socket plus a Unix stream
socket. Nothing here needs a type system to be safe, and Python matches every other
`mycord-*` REPL in this repo. Presage would justify Rust; signal-cli does not.

### Daemon: long-running, not per-call

Long-running JSON-RPC daemon, and the reasoning is evidentiary:

* `subscribeCallEvents` state lives on the *connection*: `handleConnection()`'s
  `finally` block calls `unsubscribeAllCallEvents()`, and `CallManager.close()` clears
  `callEventListeners`. A one-shot process cannot hold a call subscription.
* `handleIncomingOffer` drops calls with no listener, so an absent daemon means
  missed calls — not delayed calls.
* The account file is exclusively locked (`SignalAccount.openFileChannel` uses
  `tryLock()` then blocks on `lock()` when `waitForLock` is true), so a second
  concurrent CLI invocation against the same account *hangs waiting* rather than
  failing fast. One daemon per account, deliberately.

Invocation the skill should produce (default socket path is
`$XDG_RUNTIME_DIR/signal-cli/socket`):

```bash
export SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe      # inherited by every tunnel child
export SIGNAL_CALL_TUNNEL_BIN=/usr/local/bin/signal-call-tunnel   # be explicit
exec signal-cli -a "$ACCOUNT" --log-file "$LOG" daemon \
     --socket "$RUNTIME/signal-cli/socket" --receive-mode on-start
```

`--receive-mode` matters: `on-start` (default) begins receiving immediately;
`on-connection` starts on the first RPC connection; `manual` never starts on its own.
Prefer `on-start` for a skill, because an idle agent that has disconnected must still
have its session alive enough to answer.

For per-verb convenience the CLI transport also exists (`signal-cli -a … send`,
`receive -t N`), and it is fine for one-shot reads. It is *not* the call path.

### HTTP/SSE caveat — do not build on it

`daemon --http` exposes `POST /api/v1/rpc`, `GET /api/v1/events` (SSE),
`GET /api/v1/check`. But the HTTP handler constructs a plain
`SignalJsonRpcCommandHandler` with `Commands::getCommand`; `subscribeCallEvents`
and `subscribeReceive` are **not** in `Commands` — they are inner classes of
`SignalJsonRpcDispatcherHandler`, which only the socket-channel path instantiates
(`SocketHandler`, used for both `--socket` and `--tcp`). Grep confirms:
`subscribeCallEvents` count = 0 in `SignalJsonRpcCommandHandler.java`
(v0.14.6 and v0.14.9). The SSE stream carries `receive` events only
(`SseEventBuffer` registers a `ReceiveMessageHandler`). So **calls are reachable over
the socket transport (`--socket` / `--tcp`) only, never `--http`**. The skill should
speak the Unix socket.

Second caveat: the HTTP server has **no authentication**. Its only guard is Host-header
pinning to localhost unless bound to `0.0.0.0` (`HttpServerHandler.isHostAllowed`), and
`init()` logs *"HTTP server has no authentication"*. Never expose `--http` beyond
loopback, and prefer `--socket` with filesystem permissions.

### The 10 verbs

Reads:

1. `accounts` — `listAccounts`; surfaces number/uuid; refuses to guess when there are
   several.
2. `read --limit N [--since TS]` — subscribe + drain, or `receive -t N`; emits
   envelopes as JSON. Also `import` a dump into `store.py` for offline querying.
3. `history --with +E164 [--limit N]` — **local only.** Serves from `store.py`.
   There is no server-side history fetch (see below) and the skill must say so
   instead of pretending.
4. `search --text/--from/--since` — local SQL over `store.py`. Signal has no search
   API.
5. `contacts`, `groups [--detailed]`, `identities`, `devices`.

Writes:

6. `send --to … | --group … [--attachment …] [--quote-timestamp …] [--edit-timestamp …]`
   — `send` verb, with `--message-from-stdin` for pipes and `--notify-self` semantics
   made explicit.
7. `react | edit | delete | typing | receipt` — `sendReaction`, `send`
   `--edit-timestamp`, `remoteDelete`, `sendTyping`, `sendReceipt --type read|viewed`.
8. `media get <attachment-id> [--out DIR]` — `getAttachment --id --recipient/-g`.

Calls:

9. `call start|accept|reject|hangup|list` — JSON-RPC `startCall` / `acceptCall` /
   `rejectCall` / `hangupCall` / `listCalls`, printing `callId` + device names.
10. `call audio --call-id ID [--in FILE] [--out FILE]` — connect to the `unix:` device
    name, read 960-byte windows to `--out`, write `--in`; or hand the fd to the shared
    ASR/TTS layer. Plus `call events` (SSE-less: drain `callEvent` notifications).

(That's a grouped list of ten surfaces; the skill can expose them as verbs or as one
`eval` passthrough plus these conveniences. The `mycord-slack` precedent argues for
offering both: named verbs for the common path, and a raw-JSON-RPC escape hatch for
everything else — `sendPollCreate`, `sendStory`, `sendPinMessage`, `setPin`,
`updateProfile`, `submitRateLimitChallenge` are all reachable that way.)

### Auth / session capture

Nothing to scrape: the credential *is* the account directory written by `link`.
Skill flow:

1. `signal-cli link -n "mycord"` and **keep the process alive** — do not pipe it into
   a short-lived consumer (closing the provisioning websocket yields
   `Link request error: Connection closed!`). Print the whole `sgnl://linkdevice?...`
   URI to a file, render a QR only from a *second* process.
2. Human scans with **Signal → Settings → Linked devices → Link new device**.
3. `signal-cli listAccounts` prints the number. Store it in the skill's config, never
   the data dir.
4. Data lives in `~/.local/share/signal-cli/` (`accounts.json` →
   `data/<id>.d/account.db`); `--data-dir` overrides. `$XDG_DATA_HOME` honored
   (verified: `XDG_DATA_HOME=/home/bitnom/.local/share`).

The link URI is short-lived (~60 s per the existing SKILL.md; **UNVERIFIED** by me —
I did not attempt a link).

### Failure modes that will actually bite

| Failure | Evidence | Skill behaviour |
| --- | --- | --- |
| Client expiry after ~3 months | signal-cli README, verbatim | `doctor` verb prints installed vs latest (`gh api releases/latest`), warns at >60 days, refuses nothing |
| Missed calls with no subscriber | `handleIncomingOffer` early-returns on empty listener list | subscribe on daemon start, always; `call list` fallback |
| `null` device names right after `startCall` | set from the `ready` control event, not from the RPC response | wait for first `callEvent` before opening audio |
| Concurrent instance hangs instead of erroring | `SignalAccount`/`AccountsStore` use `tryLock()` → blocking `lock()` | single-flight lock file in the skill; clear message |
| `sender_device_id` gap in the pinned tunnel | branch lacks it; fork `main` has it; RingRTC keys `connection_map` by it | **build from fork `main` (`5a963d1`)**; do not pin `2f92426` |
| `local_device_id` hard-coded to 1 on 0.14.6 | PR #2128 diff + regression test | require signal-cli ≥ 0.14.9 |
| Tunnel binary not found | `findTunnelBinary()`: env → `<install>/bin/` → `PATH`; native image install means `<install>/bin` resolves oddly (`/opt/bin` exists here and is already symlinked) | set `SIGNAL_CALL_TUNNEL_BIN` explicitly |
| Rate limit / captcha on send | CLI has `submitRateLimitChallenge`; `CallManager.logSendMessageResult` surfaces `ProofRequiredFailure` | surface the token/retry-after verbatim |
| Hibernation / "no messages arrive" | must run `receive` or `daemon`; messages are cached to `msg-cache` if unattributable then retried (`ReceiveHelper`, 14-day age guard) | document: a stopped daemon means the phone/other devices get the message and this device syncs later |
| Breaking protocol change in RingRTC/signal-cli | control protocol type list is identical in 0.14.6 and 0.14.9 (diffed `docs/CALL_TUNNEL.md`), but `senderDeviceId` handling changed | smoke-test the tunnel's `ready` after every upgrade |
| Audio not delivered because it's not connected yet | `PipeAudioDevice` pads recording with silence and buffers/drops playout pre-connect | treat `CONNECTED` `callEvent` as the gate to start streaming |

### History — the one real functional gap

There is **no message history retrieval** in signal-cli:

* No `history`/`search`/`messages` subcommand in the CLI (full subcommand list read).
* No `HistorySync`/`chatHistory` handling anywhere in the v0.14.9 tree
  (`gh search code` returns nothing).
* `MessageCache` (`msg-cache/`) is the *pending-envelope* cache used by
  `ReceiveHelper` for unattributable/untrusted messages, and its entries are
  **deleted after successful handling** (`cachedMessage[0].delete()`); it is not an
  archive.
* `message_send_log` only stores recently *sent* payloads for resend/dedup, and its
  store prunes outdated entries.

So `history` must be accumulated locally from live traffic — the same conclusion the
WhatsApp section of `docs/platform-library-determination.md` reached, and for the same
architectural reason. `sendSyncRequest` asks the primary device for contact/group
sync — *not* conversations. **UNVERIFIED / likely but unconfirmed:** whether a fresh
link replays any conversation data through normal `receive` (Signal's own clients do
"sync the last 45 days of media" per Signal support, but I found no code path in
signal-cli that ingests a history blob). Treat "import history on link" as a **no**
until someone with a live account measures it.

### What a portable skill must pin

| Item | Pin | Why |
| --- | --- | --- |
| `signal-cli` | **≥ 0.14.9**, native build, ≤ 90 days old | calls exist from 0.14.2; correct `local_device_id` for linked devices from 0.14.9 (#2128); 3-month server tolerance |
| `signal-call-tunnel` | build from `michaelburch/signal-call-tunnel` `main` (`5a963d1`), i.e. PR #5 + the ICE device-id fix; **not** the currently installed `2f92426` | pipe backend is unmerged upstream; device id needed for multi-device peers |
| build recipe | `./build.sh` (applies `ringrtc-disable-vpio.patch` then `ringrtc-custom-audio-backend.patch` to the ringrtc submodule, then `cargo build --release`) | `build.rs`-only builds compile ringrtc *before* patching and need two passes |
| RingRTC submodule | whatever the branch pins; here `v2.65.1`, describe `v2.65.0-2-ga86f8a68` | protocol-coupled |
| env | `SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe`, optional `SIGNAL_CALL_TUNNEL_SOCKET_DIR`, explicit `SIGNAL_CALL_TUNNEL_BIN` | the only way to reach PCM without PulseAudio |
| data | `~/.local/share/signal-cli/` (do not vendor), `$XDG_RUNTIME_DIR/signal-cli/socket` | credential + lock semantics |
| transport | Unix socket JSON-RPC, **not** `--http` | call subscriptions and call commands exist only on the socket transport |

Note the build cost honestly: the tunnel checkout is **718 MB** on disk
(`third-party/ringrtc` + prebuilt WebRTC), there is no crates.io `signal-call-tunnel`
(`{"errors":[{"detail":"crate `signal-call-tunnel` does not exist"}]}`), and **neither
`visigoth/signal-call-tunnel` nor `michaelburch/signal-call-tunnel` publishes any
GitHub release** (`gh api …/releases | length` → `0` for both). There is no prebuilt
binary to download. Rust 1.99.0 and 476 GB free are available here, so a rebuild is
possible, but the skill must either ship an artifact it built itself or compile on
first use.

---

## Open questions

1. **A Signal account on a phone.** Linking needs a human with the Signal app; the
   only account on this host is dead (`+14153356728` → `NotRegisteredException`, and
   `listAccounts -o json` returns `[]`). Nothing about live calls — media, ASR, TTS,
   the ICE device-id risk — can be closed without it.
2. **Which number.** Should the skill link the operator's own number as a secondary
   device, or a dedicated number? Secondary-device linking means the skill sees (and
   can send as) the same identity as the phone — that is the mycord requirement, but
   it also means every action is attributable to the human. Confirm that is intended
   before any device is linked.
3. **Consent posture for calls.** Messaging already has the repo-wide rule
   ("confirm with a human before anything that reaches another person"). Calls need an
   explicit extra rule: does the agent ever *place* a call unattended, and does it
   announce that it is an agent? Say it in the skill or the skill will guess.
4. **Rebuild decision.** Ship a prebuilt `signal-call-tunnel` artifact (and where),
   or require the consumer to run `./build.sh` against a 718 MB checkout? I recommend
   deciding this before the implementation, because it changes the install story.
5. **Account custody.** `~/.local/share/signal-cli` holds the linked-device keys —
   equivalent to full account access. Confirm the skill treats it as a secret
   (no backups to the repo, no logging, `--scrub-log` on by default).

---

## Addendum — installed state checked independently

The orchestrator verified the pins above against what is actually on this host. Both
components are **behind** the required versions, which is the concrete form of the
"dependencies scattered around" problem:

| Component | Installed | Required by this determination | Status |
| --- | --- | --- | --- |
| `signal-cli` | `0.14.6` (`signal-cli --version`) | ≥ `0.14.9` (linked-device `local_device_id` fix, #2128) | **behind** |
| `signal-call-tunnel` | `2f92426` (`git rev-parse --short HEAD` in `~/.local/src/signal-call-tunnel`) | `5a963d1` — PR #5 **plus** the ICE device-id follow-up | **behind** |
| tunnel checkout size | 718 MB | — | no prebuilt binary exists; must build |

Consequence for whoever implements this skill: a fresh `mycord-signal-skill` cannot
simply call the binaries that exist today. It must either pin and build both, or detect
the stale versions and refuse with a clear message rather than failing mid-call. Calling
`startCall` on 0.14.6 will surface the linked-device bug, and calling into the old tunnel
will miss the ICE device id on multi-device peers — both are silent-ish failures at call
setup, which is the worst time to discover them.

`signal-call-tunnel --help` without a config JSON exits with
`Error: failed to parse config JSON` — it reads config from stdin, so it is not a
`--help`-friendly binary. Probe it with `--version` or by feeding it config, not `--help`.
