# Signal live state (verified 2026-10-26 13:11)

Everything below was checked directly, not inferred.

## Account

* Number: **+14153356728** — this IS the correct number (operator confirmed).
* `signal-cli listAccounts` → registered, no warning.
* Devices: **2**
  * Device 1, created `2026-10-26T01:54:13Z` — the operator's sign-in revived the account.
  * Device 2 "Linux", created `2026-10-26T01:57:46Z` — the `signal-cli link` QR.
* `listContacts` returns real contacts with ACIs and profile names → **authenticated reads work**.

## Versions — both at required pins

| Component | Version | Verified by |
| --- | --- | --- |
| `signal-cli` | **0.14.9** (`/opt/signal-cli-0.14.9`) | `--version`, JSON-RPC `version` |
| `signal-call-tunnel` | **`5a963d1`** | `git rev-parse` in `~/.local/src/signal-call-tunnel` |

## JSON-RPC daemon WORKS

```
signal-cli -a +14153356728 daemon --socket
```

* Socket: `/run/user/1000/signal-cli/socket`
* `{"jsonrpc":"2.0","id":1,"method":"version","params":{}}` → `{"version":"0.14.9"}`
* Use the Unix socket, **not** `--http`: call subscriptions exist only on the socket.

## Tunnel pipe mode WORKS — and its config is NOT `--help` friendly

The determination doc is wrong on this point. `--help` fails with
`failed to parse config JSON`. It reads config from **stdin** and requires three fields:

```json
{"call_id": 1, "is_outgoing": false, "local_device_id": 1, "audio_mode": "pipe"}
```

Types matter: `call_id` is **u64**, `local_device_id` is **u32** (a string is rejected).
Verified boot output:

```
Audio mode: pipe (socket /tmp/user/1000/signal-call-1.sock)
{"type":"ready","inputDeviceName":"unix:/tmp/user/1000/signal-call-1.sock", ...}
RingRTC v2.65.1
CallManager initialized, entering event loop
```

Struct is at `~/.local/src/signal-call-tunnel/signal-call-tunnel/src/config.rs`.

## Trap

`signal-cli ... listDevices` can **hang indefinitely** waiting on a sync. Wrap every
signal-cli invocation in `timeout`. A hung one sat for 15 hours.

## Scope note

No implementation code exists yet. The dependency work is DONE; the skill is not.
