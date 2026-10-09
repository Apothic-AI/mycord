# mycord-telegram-repl — Telegram as a Python library

A persistent, REPL-style session over [`telethon`][telethon]. One login, many
small steps: every snippet sees the globals and the live client from the
previous one.

> **No selfbot warning.** Telegram publishes MTProto for third-party clients
> and every official client uses it, so automating a user account here is
> supported. The Discord component in this repo does not have that property.

This package depends **only** on `telethon` and `python-dotenv`. It shares no
code with `mycord-discord-skill/`, and never authenticates with a bot token.

## Install

```bash
uv sync --all-extras
cp .env.example .env     # fill in TELEGRAM_API_ID / TELEGRAM_API_HASH
```

`api_id` / `api_hash` come from [my.telegram.org](https://my.telegram.org) →
API development tools. They belong to your *application*, not your account,
and are not secret in any meaningful sense.

For the recommended login method you also need
[`tdl`](https://github.com/iyear/tdl/releases) — a single static Go binary:

```bash
curl -fsSL -o /tmp/tdl.tar.gz \
  https://github.com/iyear/tdl/releases/latest/download/tdl_Linux_64bit.tar.gz
mkdir -p ~/.local/opt/tdl ~/.local/bin
tar -xzf /tmp/tdl.tar.gz -C ~/.local/opt/tdl tdl
ln -sf ~/.local/opt/tdl/tdl ~/.local/bin/tdl
```

Verify the published `sha256` in `tdl_checksums.txt` before trusting the
binary.

To install the agent skill globally from GitHub:

```bash
npx skills add Apothic-AI/mycord --skill mycord-telegram-skill -g
```

## Use

```bash
uv run mycord-telegram-repl login --method desktop
uv run mycord-telegram-repl start
uv run mycord-telegram-repl status

uv run mycord-telegram-repl eval "(await client.get_me()).username"
uv run mycord-telegram-repl eval "dialogs = await client.get_dialogs(limit=10)"
uv run mycord-telegram-repl eval "[d.name for d in dialogs]"
uv run mycord-telegram-repl eval "msgs = await client.get_messages(dialogs[0], limit=5)"

uv run mycord-telegram-repl stop
```

Instructions for agents live in [`SKILL.md`](SKILL.md).

## Authentication

Three non-bot methods. `auth.py` implements all three.

| Method | Needs phone | Needs `api_id` | Secret held by the skill |
| --- | --- | --- | --- |
| `desktop` | no | no | none |
| `qr` | no | yes | session string |
| `phone` | yes | yes | session string |

`desktop` shells out to `tdl login -T desktop`, which imports an existing
Telegram Desktop session. No phone number, no code, no 2FA, and no credential
in this package's custody. It prompts for an account and never logs the desktop
client out.

`qr` prints a `tg://login` URL to scan from the Telegram app. `phone` asks for
a login code and, if needed, a 2FA password.

Whichever runs first persists a `StringSession` to
`~/.cache/mycord-telegram-repl/session.txt` (mode 0600); later starts read it
and skip authentication entirely.

> A `StringSession` is a full-account credential. Never log it, echo it, or
> commit it. Confirm identity with `client.get_me()`.

## Layout

```
mycord-telegram-skill/
├── src/mycord_telegram_repl/
│   ├── protocol.py   # newline-delimited JSON over a Unix socket
│   ├── session.py    # daemon: owns one TelegramClient + a persistent namespace
│   ├── client.py     # thin socket client
│   ├── cli.py        # login / start / eval / status / reset / stop
│   └── auth.py       # desktop-import, QR, and phone login
├── tests/
├── SKILL.md
└── pyproject.toml
```

## Development

```bash
make setup && make lint && make test
```

64 tests cover the protocol, REPL semantics, CLI wiring, credential
resolution, and a live socket round trip. None require a Telegram account or
network access — the auth tests exercise parsing and error paths only.

## Security

* `api_id`/`api_hash` are read from the environment or `.env`, never committed.
* `auth.py` never logs a session string, `api_hash`, or 2FA password.
* The session file is written with mode 0600.
* Sending, editing, deleting, and forwarding are **visible to other humans**.
  Confirm before bulk actions.
* The REPL executes whatever Python you send it, at the same trust level as
  your shell. It is a local automation tool, not a sandbox.

## License

MIT.

[telethon]: https://github.com/Lonami/Telethon