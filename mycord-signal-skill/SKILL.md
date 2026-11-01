---
name: mycord-signal-skill
description: Drive Signal as the linked human — no bot, app registration or OAuth client — through signal-cli's JSON-RPC daemon, and its 1:1 voice calls as raw PCM over a per-call Unix socket. Use when asked to read Signal contacts, groups or messages, send or react as the account, check whether the pinned signal-cli/signal-call-tunnel versions are call-ready, start/accept/hang up a call, or hand call audio to an ASR/TTS layer. Refuses the call path loudly on a stale version pin rather than failing mid-call.
---

# mycord-signal — Signal as a library, with the one real voice path

Operate a Signal account the way Signal Desktop does: as a **linked device**. No bot
registration, no OAuth app, no second identity the platform can rate-limit separately.

Two components must both be at pinned versions, and **a mismatch fails inside a call** — so
`doctor` and `call-guard` refuse before anything rings. That refusal is the most important
behaviour in this skill, not an inconvenience.

---

## Start here

```bash
cd mycord-signal-skill
uv sync --all-extras
uv run mycord-signal doctor        # 0 = call-ready, 3 = stale/missing/no account
uv run mycord-signal versions      # measured numbers, JSON
```

`doctor` exits `3` when any pin is stale or the daemon/account is absent, and prints the exact
remediation. Do not work around it: the pins exist because 0.14.6 sends the wrong device id
into the tunnel and a pre-`5a963d1` tunnel relays ICE under the wrong device.

## The pins, and why

| Component | Required | Consequence of being behind |
| --- | --- | --- |
| `signal-cli` | **≥ 0.14.9** | hard-codes `local_device_id: 1` into the tunnel config; a *linked* device has id ≥ 2, so RingRTC keys ICE under the wrong device |
| `signal-call-tunnel` | **commit `5a963d1`** | without it there is no pipe audio backend, or `receivedIce` arrives with no `sender_device_id` |

```bash
scripts/provision.sh            # detect only; exit 3 when stale
scripts/provision.sh --apply    # GPG-verified install + ./build.sh
```

`--apply` verifies the release signature before `sudo install`, and verifies the freshly built
tunnel boots in pipe mode **before** replacing the working binary.

## Sessions

Talk to a **running** daemon:

```bash
signal-cli -a +15550100 daemon --socket     # default $XDG_RUNTIME_DIR/signal-cli/socket
```

* **Never start a second one.** The account file is exclusively locked, so a second instance
  does not fail — it hangs.
* **Use the socket, not `--http`.** `subscribeCallEvents` exists only on the socket transport.
* The HTTP endpoint has **no authentication** (Host-header pinning only). Do not expose it.

## Verbs

```bash
uv run mycord-signal doctor [--json] [--require-account]
uv run mycord-signal versions
uv run mycord-signal call-guard          # exit 3 unless call-ready

uv run mycord-signal contacts [--json]
uv run mycord-signal groups [--json]
uv run mycord-signal calls [--json]
uv run mycord-signal receive [--stream --seconds 10] [--limit N]
uv run mycord-signal events [--limit N]   # stream callEvent

uv run mycord-signal send --to +1555… -m "…"            # DRY RUN
uv run mycord-signal send --to +1555… -m "…" --confirm  # real

uv run mycord-signal call start +1555… [--confirm] [--wait 30]
uv run mycord-signal call accept|reject|hangup <call_id>
uv run mycord-signal call audio <call_id> [--play f.raw] [--capture f.raw] [--seconds N]

uv run mycord-signal rpc <method> [--params '{…}']       # full passthrough
```

`send` and `call start` are **dry runs** without `--confirm`. That is deliberate: an agent
exploring the surface cannot reach a person, and a human reviewing a plan sees the literal
JSON-RPC body that would be sent. Sending to a real person is a decision for the operator, not
a default an agent inherits.

## Voice

1:1 audio only (no groups, no video — `CallManager` creates `CallType::Audio` and the tunnel
no-ops group SFU requests).

| Property | Value |
| --- | --- |
| rate / channels | 48 000 Hz, mono, s16le |
| window | 10 ms = 480 samples = **960 bytes**, each direction |
| transport | one bidirectional `SOCK_STREAM` Unix socket; **the tunnel listens, you connect** |
| names | `inputDeviceName == outputDeviceName == unix:<socket dir>/signal-call-<id>.sock` |

Read = remote audio. Write = your mic. Do not open the socket before `CONNECTED`.

Ordering rules that are not optional:

1. **`subscribeCallEvents` before any call.** `handleIncomingOffer` returns early when no
   listener is registered — an incoming call is dropped with no ring, no tunnel.
2. **Wait for a `callEvent` to learn the device names.** The `startCall` response carries
   `null`; the names arrive on the tunnel's `ready`, surfaced through the first event.
3. **Stream only while `CONNECTED`.** Before that the tunnel buffers playout and zero-pads
   recording, so audio "sent" early is silence.

```bash
uv run mycord-signal call start +1555… --confirm --wait 30
uv run mycord-signal call audio <call_id> --capture /tmp/remote.raw --seconds 5
uv run mycord-signal call hangup <call_id>
```

Feeding this to speech: `calls.AudioSocket` yields 960-byte windows at 48 kHz mono s16le;
`gvoice/src/stt.ts` and `gvoice/src/tts.ts` take 16 kHz mono, so a resample sits between them.
Reuse the *speech layer*, not `gvoice`'s `werift`/SIP code — RingRTC owns the peer connection
here, inside the tunnel process.

## Reading, and the history gap

`receive` asks the server for new messages; a daemon started with the default
`--receive-mode on-start` is already receiving, so the server refuses a second call with
`-1 …already being received`. That is answerable: `--stream` drains the existing subscription
instead. The refusal is reported as a note, never raised — the daemon is working correctly,
it just cannot be asked twice.

**There is no message history API.** No `history`/`search`/`messages` subcommand, no
`HistorySync` handling, and `msg-cache/` is a pending-envelope cache whose entries are deleted
after successful handling. `sendSyncRequest` fetches contacts and groups, not conversations. A
REPL can only answer "what did I see" for messages it was running to receive. Do not pretend
otherwise.

## Traps

| Symptom | Cause / fix |
| --- | --- |
| `signal-cli … listDevices` never returns | waits on a device sync; one hung for **15 hours**. Wrap every invocation in `timeout` — this package routes all of them through `deps.run()` |
| incoming call rings nowhere | no `subscribeCallEvents` |
| `inputDeviceName` is `null` | read too early; wait for `ready` via `callEvent` |
| `receive` → "already being received" | use `--stream` |
| `listAccounts` → `-32601` over RPC | it is a multi-account command; the single-account daemon does not dispatch it. `doctor` reads `accounts.json` |
| `signal-call-tunnel --help` → `failed to parse config JSON` | it reads config from stdin and has no `--help`. Fields: `call_id` **u64**, `is_outgoing` bool, `local_device_id` **u32**, `audio_mode`. A string `call_id` is rejected |
| pipe socket: `No such file or directory` | `SIGNAL_CALL_TUNNEL_SOCKET_DIR` must already exist; the tunnel does not create it |
| second CLI invocation hangs | account file lock; reuse the running daemon |
| "installed tunnel differs from checkout build" | the shim trap: a mise shim is a symlink to `mise`, so hashing it is meaningless. `deps.resolve_binary` asks mise |
| client expires | signal-cli must stay within ~3 months of upstream; older releases stop working when Signal-Server changes |

## Link a device (needs a human)

Linking is the one step no agent can complete alone: it needs a phone with the Signal app.

```bash
signal-cli link -n "mycord"      # prints sgnl://linkdevice?…, then blocks
```

Scan it with **Signal → Settings → Linked devices → Link new device**, then
`signal-cli listAccounts`. The URI is short-lived (~60 s). Never pipe `signal-cli link` output
into a short-lived consumer — closing the provisioning websocket yields
`Link request error: Connection closed!`.

## Secrets

`~/.local/share/signal-cli/` holds the linked-device keys: equivalent to full account access.
Never commit it, never copy it off-host, never log it. Use `--scrub-log` on signal-cli when
capturing logs.

## Exit codes

`0` ok · `1` runtime failure · `2` usage · `3` not ready (stale pin, no daemon, or no account).
Branch on `3`: it always means "provision or link", never "retry".
