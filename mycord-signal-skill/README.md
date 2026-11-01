# mycord-signal-skill

Signal from a shell, as the linked human, with **no bot, app registration or OAuth client** —
plus the one thing no other mycord skill has: real **1:1 voice calls with programmatic PCM**.

Instructions for agents live in [`SKILL.md`](SKILL.md). This file is for whoever has to
install, debug, or maintain it.

> **Unofficial.** Signal publishes no third-party client API; `signalapp/libsignal` is a crypto
> library and its README says use outside Signal is unsupported. This drives a real account as
> that account, through `signal-cli`, which is a linked device — the same shape as Signal
> Desktop.

---

## Why this shape

Signal's call path is split across two independently-versioned components, and **both** are
load-bearing:

| Component | What it does | Pinned because |
| --- | --- | --- |
| `signal-cli` ≥ **0.14.9** | registration, messaging, signalling relay | before 0.14.9 it hard-codes `local_device_id: 1` into the tunnel config — wrong for a *linked* device, and it breaks multi-device ICE |
| `signal-call-tunnel` @ **5a963d1** | RingRTC, media negotiation, PCM | needs PR #5's pipe audio backend **and** the `receivedIce` `sender_device_id` fix; upstream `visigoth` has neither |

A mismatch does not fail at startup. It fails *inside a call*, which is the worst possible
moment — so every verb on the call path runs a dependency gate first and refuses with the
measured version and the exact remediation. That gate is the most important code here.

`scripts/provision.sh` detects and reports; `scripts/provision.sh --apply` performs the work.

## Install

```bash
uv sync --all-extras
scripts/provision.sh              # detect only, exits non-zero if stale
scripts/provision.sh --apply      # install/build what is stale (needs network + sudo)
```

`--apply` is GPG-verifies the signal-cli release against AsamK's published key before
installing, and rebuilds the tunnel from the pinned commit with `./build.sh` (which patches the
ringrtc submodule *before* compiling; a bare `cargo build` needs two passes because ringrtc is
compiled before `build.rs` runs). Neither step is run by the tests.

Verify:

```bash
uv run mycord-signal doctor        # exit 0 = call-ready, 2 = usage, 3 = not ready
uv run mycord-signal versions      # measured versions as JSON
```

## Use

```bash
uv run mycord-signal doctor
uv run mycord-signal contacts --json
uv run mycord-signal receive --stream --seconds 10

# send: dry run by default, --confirm to actually send
uv run mycord-signal send --to +15550100 -m "hello"
uv run mycord-signal send --to +15550100 -m "hello" --confirm

# any method the daemon exposes, not just the curated verbs
uv run mycord-signal rpc listGroups
uv run mycord-signal rpc send --params '{"recipient":["+15550100"],"message":"hi"}'
```

### The daemon

The skill talks to a **running** `signal-cli daemon --socket`, default
`$XDG_RUNTIME_DIR/signal-cli/socket`. Start one yourself:

```bash
signal-cli -a +15550100 daemon --socket
```

**Do not start a second one.** The account file is exclusively locked (`SignalAccount` uses
`tryLock()` then blocks on `lock()`), so a second instance does not fail — it *hangs* waiting.
Reuse the running daemon.

Use the socket, not `--http`: `subscribeCallEvents` and `subscribeReceive` are inner classes of
`SignalJsonRpcDispatcherHandler`, which only the socket transport instantiates. Over HTTP those
methods do not exist, so a call subscription is impossible.

## Voice

1:1 audio only. The tunnel owns RingRTC; the skill gets raw PCM:

| Property | Value | Source |
| --- | --- | --- |
| sample rate | 48 000 Hz | `pipe_audio.rs` `SAMPLE_FREQUENCY` |
| channels | 1 | `NUM_CHANNELS` |
| format | signed 16-bit little-endian | `BYTES_PER_SAMPLE = 2` |
| window | 10 ms = 480 samples = **960 bytes**, each direction | `WINDOW_MS`, `WEBRTC_WINDOW` |
| transport | one bidirectional `SOCK_STREAM` Unix socket; **tunnel is the listener** | `PipeAudioDevice::new` |
| device name | `unix:/path/signal-call-<id>.sock`, identical for input and output | `main.rs` |

Read = remote audio (playout). Write = your mic (recording). Buffers are capped at ~80 ms each
way; recording is zero-padded when nobody is attached, playout is dropped rather than blocking.

```bash
export SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe     # inherited by every tunnel child
signal-cli -a +15550100 daemon --socket
uv run mycord-signal call start +15550100 --confirm --wait 30
uv run mycord-signal call audio <call_id> --capture /tmp/remote.raw --seconds 5
uv run mycord-signal call hangup <call_id>
```

`signal-call-tunnel` is **not** `--help`-friendly: with no config on stdin it exits with
`failed to parse config JSON`. That is expected. It reads one JSON line from stdin and requires
`call_id` (**u64**), `is_outgoing` (bool), `local_device_id` (**u32**), `audio_mode`.
A string `call_id` is rejected — see `config.rs`.

## Layout

```
src/mycord_signal/
  deps.py     version pins, tunnel provenance, pipe probe, account store, call gate
  rpc.py      newline-JSON JSON-RPC 2.0 client over the Unix socket
  reads.py    contacts, groups, calls, message draining
  send.py     SendPlan + dry-run preview + confirm gate
  calls.py    call verbs, CallEvent, the PCM AudioSocket
  cli.py      argparse verbs and exit codes
tests/        offline suite + fake daemon; live tests are marked
scripts/      provision.sh
```

## Files and secrets

| Path | Holds | Care |
| --- | --- | --- |
| `~/.local/share/signal-cli/` | the linked-device keys — **full account access** | never commit, never copy off-host, never log |
| `$XDG_RUNTIME_DIR/signal-cli/socket` | the daemon socket, 0600-ish, user-owned | filesystem permissions are the only auth |
| `~/.local/src/signal-call-tunnel` | 718 MB build checkout | not needed at runtime once the binary exists |

## Traps

| Symptom | Cause |
| --- | --- |
| `signal-cli … listDevices` never returns | it waits on a device sync; one hung for 15 hours. Every invocation must be wrapped in `timeout` — this package routes all of them through `deps.run()` |
| incoming call rings nowhere | nobody called `subscribeCallEvents`; `CallManager.handleIncomingOffer` returns early when the listener list is empty |
| `inputDeviceName` is `null` right after `startCall` | it only exists after the tunnel reports `ready`, which arrives as a `callEvent` — wait for the event |
| `receive` fails with "already being received" | default `--receive-mode on-start`; use `--stream` instead |
| `listAccounts` returns `-32601` over RPC | it is a multi-account command; the single-account daemon does not dispatch it. `doctor` reads `accounts.json` instead |
| tunnel exits with `failed to parse config JSON` | it reads stdin, it has no `--help` |
| `No such file or directory` creating the pipe socket | `SIGNAL_CALL_TUNNEL_SOCKET_DIR` must exist; the tunnel does not create it |
| a second CLI invocation hangs | account file lock; reuse the running daemon |

## Verification status

Measured on this host: `signal-cli 0.14.9`, tunnel `5a963d1` (sha256 `f52c39c0…`), RingRTC
`v2.65.1`, pipe `ready` at `/tmp/user/1000/signal-call-<id>.sock`.

**Verified live:** authenticated JSON-RPC reads (contacts, groups, call list), version
negotiation, call-event subscription, tunnel pipe boot, PCM socket framing.
**Not verified:** media on a real call (no call placed), end-to-end send (nothing sent to a
person), group calls or video (not implemented by this stack).
