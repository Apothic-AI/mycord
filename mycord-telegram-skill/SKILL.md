---
name: mycord-telegram-skill
description: Drive a real Telegram user account from Python using Telethon through a persistent REPL-style session (mycord-telegram-repl). Use when asked to read, send, edit, search, or monitor Telegram messages, chats, groups, channels, or members. Documents three non-bot login methods (Telegram Desktop session import, QR login, and phone login). Unlike the Discord half of this repo, Telegram user automation is supported rather than a ToS violation.
---

# mycord-telegram — Telegram as a Python library

Operate a Telegram user account by writing Python against `telethon` in a
**persistent session**. One login, many small steps: every `eval` sees the
globals and the live client from the previous one, so you explore in stages
instead of writing one giant script.

> **No selfbot warning here.** Telegram publishes MTProto for third-party
> clients and every official client uses it, so driving a user account is
> supported. Contrast the Discord components in this repo, which do carry
> real ban risk.

This component shares no code with `mycord-discord-skill/` or
`mycord-discord-mcp/` — only the persistent-REPL shape.

---

## The loop

```bash
cd mycord-telegram-skill
uv run mycord-telegram-repl login --method desktop   # one-time auth
uv run mycord-telegram-repl start                    # daemon; waits for auth
uv run mycord-telegram-repl status                   # who am I connected as
uv run mycord-telegram-repl eval "<python>"          # run a snippet; state persists
uv run mycord-telegram-repl stop                     # disconnect cleanly
```

The first `start` blocks until authorized. Subsequent `eval` calls are fast.

---

## Authentication

Three methods, **none of which use a bot token**. Cheapest first.

Unlike Discord there is no single bearer token. A Telethon user session needs:

| Value | What it is | Secret? |
| --- | --- | --- |
| `api_id` / `api_hash` | Credentials for *your app*, from my.telegram.org | No — every client bundles the same pair |
| the auth key | The actual account credential | **Yes — full account access** |

The auth key persists as a `StringSession`. Once `login` writes it, every
later `start` reuses it and never re-authenticates.

### Method 1: Telegram Desktop session import (recommended)

Reuses a login the user already has. No phone number, no code, no 2FA, and
**no secret is ever handed to this skill**. Requires the `tdl` binary.

```bash
uv run mycord-telegram-repl login --method desktop   # headless, then:
uv run mycord-telegram-repl start                    # daemon; waits for auth
uv run mycord-telegram-repl status                   # who am I connected as
```

This is fully headless — **no TTY, no interactive picker, no manual step**:

- `tdl`'s account picker accepts its default on a bare newline, so the skill
  feeds `\n\nn\n` on stdin instead of closing it. The first newline accepts the
  highlighted account and the second answers `N` to "log out of the desktop
  client?", so **the user's Telegram Desktop login survives the import**.
- `tdl` writes the session *before* asking that logout question, and the
  question queries cursor position, so under a pipe it always EOFs and `tdl`
  exits non-zero **after a perfectly good import**. The skill detects the
  successful import and continues; a non-zero exit on its own is not a failure.
- `tdl` stores the auth key in its own Bolt file, which Telethon cannot read, so
  the skill converts it into a `StringSession` (`string_session_from_tdl_storage`)
  and writes it where the daemon expects, mode `0600`. You do **not** need to run
  `tdl` by hand, and you do **not** need QR or phone login as a fallback.

Binary: https://github.com/iyear/tdl/releases (single static Go binary,
install to `~/.local/opt/tdl` and symlink into `~/.local/bin`).

> [!IMPORTANT]
> `tdl` supplies its own `api_id`/`api_hash` for the import step. **You do not
> need a `.env` at all for the desktop path.** `load_credentials()` falls back to
> inert placeholders when neither variable is set, because a session that already
> carries an auth key never runs a login handshake and Telegram never validates
> app credentials. Half-configured credentials (only one of the two set) are
> still an error. QR and phone logins do need real values, since they do
> handshake.

### Reading vs sending — pick the right client

`tdl` and Telethon are not interchangeable:

| Need | Use |
| --- | --- |
| List chats, export history, browse | `tdl chat ls` / `tdl chat export` — works with no Telethon session and no credentials |
| **Send messages, read replies, click inline buttons** | the REPL over Telethon (`client.send_message`, `client.get_messages`) |

`tdl chat` only offers `export`, `ls`, and `users` — it **cannot send text**. Any
task that sends a message or taps an inline keyboard button must go through the
REPL, which is why the desktop import has to produce a `StringSession` at all.

```bash
# list chats with no credentials at all
tdl chat ls

# send through the REPL
uv run mycord-telegram-repl eval "
chat = await client.get_entity('SupremeLeaderShopBot')
await client.send_message(chat, '/wallet')"
```

Read an inline keyboard (shop menus are buttons, not text):

```bash
uv run mycord-telegram-repl eval "
chat = await client.get_entity('SupremeLeaderShopBot')
m = await client.get_messages(chat, limit=1)
[[b.text for b in row] for row in (m.buttons or [])]"
```

> [!TIP]
> Confirm who you are acting as before anything consequential:
> `(await client.get_me()).username`.

### Method 2: QR login

Scan from the Telegram app. No phone-number entry, no 2FA prompt. Needs
`api_id`/`api_hash`.

```bash
uv run mycord-telegram-repl login --method qr
```

Prints a `tg://login` URL, then blocks ~120s waiting for the scan.

### Method 3: phone login

Phone number → login code (Telegram message or SMS) → 2FA password if the
account has one. The fallback.

```bash
TELEGRAM_PHONE=+15551234567 uv run mycord-telegram-repl login --method phone
```

`TELEGRAM_2FA_PASSWORD` is only read when the account actually has 2FA.

### Which should I use?

Reach for **desktop import** when the user has Telegram Desktop logged in —
it is the only method with zero credential handling on this side. Use **QR**
when they do not, or when they want to avoid touching the desktop session at
all. Use **phone** only as a last resort.

| Method | Needs phone | Needs `api_id` | Secret in skill | Interactive |
| --- | --- | --- | --- | --- |
| desktop | no | import only | none | no |
| qr | no | yes | session string | scan |
| phone | yes | yes | session string | code + 2FA |

> A `StringSession` is more sensitive than a Discord token. Telethon's own docs:
> *"Anyone with this string can use it to login into your account and do
> anything they want."* Never log it, never echo it, never commit it. Confirm
> identity with `client.get_me()`, never by printing the credential.

---

## What is already in scope

Every snippet runs with these bound:

| Name | What it is |
| --- | --- |
| `client` | the live `telethon.TelegramClient` |
| `telethon` | the `telethon` module |
| `wait_ready` | `async` helper that blocks until authorized |
| `session` | the `ReplSession` object |
| `_` | the previous snippet's result |

## Output you get back

* The **value** of a trailing expression (REPL style). Anything you `print()` is
  captured and echoed first.
* Errors come back as a traceback and a non-zero exit code.

Both are truncated at 20 000 characters.

---

## Recipes

### Who am I?

```bash
uv run mycord-telegram-repl eval "(await client.get_me()).username"
```

### List dialogs

```bash
uv run mycord-telegram-repl eval "\
dialogs = await client.get_dialogs(limit=20)
[(d.id, d.name, d.unread_count) for d in dialogs]
"
```

### Read recent messages

```bash
uv run mycord-telegram-repl eval "\
chat = await client.get_entity('some_username')
[(m.id, m.date, m.sender_id, m.text) for m in await client.get_messages(chat, limit=20)]
"
```

`get_messages()` returns a lazy `TotalList` — await it before iterating, or you
get an empty result rather than an error.

### Search backwards from a known point

```bash
uv run mycord-telegram-repl eval "\
chat = await client.get_entity(-1001234567890)
msgs = await client.get_messages(chat, limit=500)
[m.text for m in msgs if 'deploy' in (m.text or '').lower()]
"
```

`limit=0` fetches the **entire** history in one call. Telegram rate-limits hard
on that — page with `offset_id` instead, e.g. `offset_id=msgs[-1].id`.

### Send a message

```bash
uv run mycord-telegram-repl eval "await client.send_message('me', 'hello from an agent')"
```

### Download media

```bash
uv run mycord-telegram-repl eval "\
path = await client.download_media(m, file='out.jpg')
"
```

### Wait for the next message

```bash
uv run mycord-telegram-repl eval "\
m = await client.get_messages('some_chat', limit=1)
(m.sender_id, m.text)
"
```

---

## Working style

**Prefer several small evals over one big script.** If a step fails you keep
the earlier bindings, so you can inspect and retry just the broken line.

```bash
# good: incremental, each step checkable
uv run mycord-telegram-repl eval "dialogs = await client.get_dialogs(limit=50)"
uv run mycord-telegram-repl eval "[d.name for d in dialogs]"
uv run mycord-telegram-repl eval "chat = await client.get_entity('target')"

# worse: one shot; a typo loses all the work
uv run mycord-telegram-repl eval "print([(d.name, await client.get_messages(await client.get_entity(d.name), limit=5)) for d in await client.get_dialogs()])"
```

Other habits that pay off:

* Bind a name (`chat = ...`) and reuse it next call instead of re-resolving.
* `get_messages()` returns a lazy `TotalList`; `await` it or use `list(...)`.
* Use `--file snippet.py` for anything longer than a couple of lines.
* `reset` clears your globals but keeps the connection. `stop` then `start`
  re-reads the saved session string.
* `repr()` is truncated for huge collections; slice or aggregate first.

## Inspect the library

```bash
uv run mycord-telegram-repl eval "[m for m in dir(client) if 'message' in m]"
uv run mycord-telegram-repl eval "telethon.tl.types.Message"
uv run mycord-telegram-repl eval "client.get_messages"
```

## Commands

| Command | Effect |
| --- | --- |
| `login` | authenticate: `--method desktop\|qr\|phone` |
| `start` | launch the daemon; `--no-connect` for an offline session |
| `status` | connection state, user id, uptime |
| `eval CODE` | run a snippet (`--file PATH`, `--timeout SEC`) |
| `reset` | drop agent-defined globals, keep the connection |
| `stop` | disconnect and remove the socket |

Add `--socket PATH` on either side of the subcommand. `MYCORD_TELEGRAM_REPL_SOCKET`
also works. Daemon logs go to `~/.cache/mycord-telegram-repl/session.log`.

## Setup

```bash
cd mycord-telegram-skill
uv sync --all-extras
cp .env.example .env      # fill in TELEGRAM_API_ID / TELEGRAM_API_HASH
```

## Safety rails

* Stop the session when a task ends: `mycord-telegram-repl stop`.
* **Sending is visible to real people.** Confirm before bulk sends, edits,
  deletes, or forwards. Deleting a message for everyone is not reversible.
* The daemon executes whatever Python you send it — same trust level as a shell.
* `auth.py` never logs or prints a session string, `api_hash`, or 2FA password.
* `import_desktop_session()` runs `tdl` with `stdin` closed and never passes
  `--logout`; Telegram Desktop stays logged in.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `no mycord-telegram session at ...` | not started — run `start` |
| `missing TELEGRAM_API_ID, TELEGRAM_API_HASH` | register an app at my.telegram.org |
| `tdl not found on PATH` | install from iyear/tdl releases, or use `--method qr` |
| `Error: EOF` from `login --method desktop` | should no longer happen — the skill feeds newlines rather than closing stdin. If you see it, you are on an old checkout; update so `import_desktop_session` passes `input=`, not `stdin=` |
| desktop login succeeds but `status` says `not authorized` | fixed: the daemon sets the authorized flag once `get_me()` succeeds. If you see this, you are on an old checkout |
| `get_me()` works but status says unauthorized | same as above — verify with `(await client.get_me()).username`, which is the ground truth |
| `tdl timed out` | the picker blocked; run the import once by hand |
| `FloodWaitError` | rate-limited; wait the interval it reports, page with `offset_id` |
| history returns empty | forgot to `await` the `TotalList` from `get_messages()` |
| `SessionPasswordNeededError` | 2FA — set `TELEGRAM_2FA_PASSWORD` |
| session hangs | a snippet is blocked; default eval timeout is 30s, raise with `--timeout` |

## Relationship to the Discord components

Independent. `mycord-discord-skill/` and `mycord-discord-mcp/` drive Discord
via `discord.py-self`; this drives Telegram via `telethon`. They share the
repo and the REPL architecture, and **nothing else** — different platform,
different library, different credentials, separate sockets. Do not import
across them, and never start one expecting the other's daemon.

## Two Telegram-specific constraints

**Flood waits.** Telegram rate-limits large reads aggressively. The first sync
of a big chat should be bounded by `--since`-style limits or paged with
`offset_id`, not a bare `limit=0`.

**24-hour export lockout.** Telegram blocks data export for a day after a
new-device login. If the skill re-auths, the first bulk export may fail for a
day. This does not affect ordinary message reads.

**Secret chats** are device-local and never appear through MTProto. If a user
asks for one, say so rather than returning empty results as if it were empty.