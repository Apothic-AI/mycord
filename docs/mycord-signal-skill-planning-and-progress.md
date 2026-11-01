# mycord-signal-skill — planning and progress

**Updated:** 2026-11-01 (integrated into master and re-gated there; the live facts below are
still the 2026-10-27 post-reboot verification)

Scope came from `docs/determine/signal.md` (the determination) and
`docs/implementation-decision.md` (which mediums get built now), expanded once the account
went live. Verified host facts live in `docs/determine/signal-live-state.md`.

---

## What shipped

| Piece | State |
| --- | --- |
| `signal-cli` **0.14.9** at `/opt/signal-cli-0.14.9` | installed, GPG-verified, `--version` checked |
| `signal-call-tunnel` @ **5a963d1** | checkout switched, rebuilt via `./build.sh`, installed to `~/.cargo/bin`, pipe boot verified |
| `src/mycord_signal/{deps,rpc,reads,send,calls,cli}.py` | written; `mypy --strict` clean |
| `tests/` (offline + live) | 122 offline passing; 11 live marked |
| `scripts/provision.sh` | detect + `--apply`; exit 3 when stale |
| `Makefile`, `SKILL.md`, `README.md` | matching `mycord-slack-skill` conventions |

Gates re-run **inside the integrated tree** on 2026-11-01, not the source worktree: ruff
check clean, `mypy --strict` clean across 8 source files, **122 passed** offline, and the 11
live-marked tests skipped as designed (they are env-gated on a running daemon and a linked
account). The `11 live-marked tests pass` line under *Verified live* is the 2026-10-27
observation with that gate open, and was not re-run here.

## The dependency problem, solved

The user's framing was that Signal's "dependencies are scattered around". They were, and the
scatter was load-bearing:

* **signal-cli 0.14.6 → 0.14.9.** Below 0.14.9 the binary hard-codes `local_device_id: 1` into
  the tunnel config (upstream PR #2128). A linked device is id ≥ 2, and RingRTC keys its
  connection map by that id, so the wrong value is a lookup miss during ICE.
* **tunnel 2f92426 → 5a963d1.** 2f92426 has the pipe backend but no `sender_device_id` on
  `receivedIce`; 5a963d1 adds it. The branch is now an ancestor of the fork's `main`, so
  building `main` is a strict superset — which is what `provision.sh` pins.

Both are detected by `deps.py`, refused by `require_call_ready`, and fixed by
`provision.sh --apply`. The refusal names the measured version, the required version, and the
command — because the failure it prevents happens *inside a call*.

## Corrections made to the determination

1. `signal-call-tunnel` is not `--help` friendly. It reads config from stdin and rejects a
   string `call_id`. Fields are `call_id` u64, `is_outgoing` bool, `local_device_id` u32,
   `audio_mode`. Recorded in the determination and pinned by
   `test_probe_pipe_sends_integer_call_id`.
2. `signal-cli listDevices` can hang indefinitely. Every subprocess in this package goes
   through `deps.run()`, which carries a timeout; `test_signal_cli_hanging_command_times_out`
   pins it.
3. **New finding, not in the determination:** `listAccounts` is **not implemented** over the
   single-account JSON-RPC daemon — it answers `-32601` because `ListAccountsCommand` is a
   `JsonRpcMultiLocalCommand` and the single-account handler only dispatches single/multi
   commands. `doctor` therefore reads `accounts.json` directly. Pinned by a live test, so if
   upstream ever changes it, the workaround gets revisited.
4. **New finding:** on this host the tunnel resolves through a **mise shim**, which is a
   symlink to the `mise` binary. Hashing it against the checkout build produced a permanent
   false "installed binary differs" warning. `deps.resolve_binary` now asks `mise which`.

## Verified live

Re-verified 2026-10-27 after a reboot killed the daemon and it was restarted
(`signal-cli -a … daemon --socket`).

`signal-cli 0.14.9`; tunnel `5a963d1` sha256 `f52c39c0…`; RingRTC `v2.65.1`; pipe `ready` at
`/tmp/user/1000/signal-call-<id>.sock`; daemon answers `version` on
`/run/user/1000/signal-cli/socket`; `doctor` exits 0 with all six checks `ok`; `call-guard`
exits 0; `listContacts` returns two real contacts (both with null numbers, profile names only —
which is why `contact_label` falls back to uuid); `listGroups`/`listCalls` empty; send dry-run
prints the exact request without contacting the daemon; 11 live-marked tests pass.

## Stale detection, demonstrated end to end

Not just covered by unit tests. Each case below was run through the real CLI against the live
daemon, using a throwaway `signal-cli` stand-in and a throwaway pre-fix git checkout (both
removed afterwards); nothing was installed and PATH was never modified.

| Case | Result |
| --- | --- |
| `doctor --signal-cli <0.14.6>` | `[STALE] signal-cli: found 0.14.6, need >=0.14.9` + fix, **exit 3** |
| `call-guard --signal-cli <0.14.6>` | `refusing the call path: …`, **exit 3** |
| `call start … --confirm --signal-cli <0.14.6>` | refused **before the daemon was contacted**, **exit 3** |
| `call-guard --tunnel-src <pre-fix checkout>` | `does not contain 5a963d1, so it lacks the receivedIce sender_device_id fix`, **exit 3** |
| `provision.sh` with stale signal-cli | `STALE signal-cli: 0.14.6 … need >= 0.14.9`, **exit 3** |
| `provision.sh --tunnel-src <pre-fix checkout>` | `STALE signal-call-tunnel checkout: 841f325`, **exit 3** |

Proof the refusal happens before call setup rather than during it: after a refused
`call start --confirm`, `calls` still returned `[]`, and the daemon log contained **0**
`startOutgoingCall` and **0** `Spawned signal-call-tunnel` lines.

## Verified offline only

The whole 960-byte PCM framing contract, the call state machine, the send confirm gate, the
exit codes, and every stale-detection path. These run against a fake daemon on a real Unix
socket, so framing and demultiplexing are genuine, not simulated.

## Not verified

* **Media on a live call.** No call was placed. This needs an operator decision, because
  placing one reaches another person's phone.
* **End-to-end send.** Nothing was sent.
* Group calls / video — not implemented by this stack at all (`CallType::Audio` only; the
  tunnel's `NullHttpClient` no-ops group SFU requests).
* Message history: there is no API for it. `history` can only serve what the skill was running
  to receive.

## Next, when someone picks this up

1. Place one real 1:1 call with a cooperative human and confirm the PCM socket carries audio
   in both directions (the last unverified link).
2. Decide the resampling boundary to `gvoice`'s ASR/TTS (48 kHz mono → 16 kHz mono).
3. If messaging verbs become the priority: `send` needs a real confirm path exercised, and
   `receive` needs a run against traffic.
