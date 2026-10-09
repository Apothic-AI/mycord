# mycord

Chat-platform automation, split into **independent** components: one for
Discord on [`discord.py-self`][dps], one for Telegram on
[`telethon`][telethon], one for IRC on [`pydle`][pydle], and one for Google
Voice on [`werift`][werift].

> **The Discord component is a selfbot.** It logs in as a Discord *user*,
> not a bot. That violates Discord's Terms of Service and **can get the account
> permanently banned**. Use a throwaway personal account only — never a work,
> shared, or customer-facing one.
>
> The Telegram component has no such problem: Telegram publishes MTProto for
> third-party clients, so user-account automation there is supported rather
> than forbidden.
>
> **`gvoice/` is an unofficial Google Voice client**, reverse-engineered from
> traffic. Automating a Google account may violate Google's Terms of Service.

## Components

| Directory | What it is | Depends on the other? |
| --- | --- | --- |
| [`mycord-discord-skill/`](mycord-discord-skill/) | Agent skill: Discord as a Python library via a persistent REPL | no |
| [`mycord-telegram-skill/`](mycord-telegram-skill/) | Agent skill: Telegram as a Python library via a persistent REPL | no |
| [`mycord-irc-skill/`](mycord-irc-skill/) | Agent skill: IRC as a Python library via a persistent REPL | no |
| [`gvoice/`](gvoice/) | Unofficial Google Voice client in TypeScript: SMS over HTTP, calls over SIP-on-WebSocket | no |

Every component stands alone. Telegram and IRC are separate platforms and
share no code with the Discord component or each other, only the
persistent-REPL shape. `gvoice/` shares the repository and nothing else: it is
TypeScript, it is a library rather than a REPL, and it carries its own runtime
(`werift`, `opus-decoder`, Node's native TypeScript stripping).

### `mycord-discord-skill/` — agent skill

For agents and operators, and the supported path for Discord. Drives
`discord.py-self` interactively: one login, then many small Python steps that
share state, with top-level `await` and discord.py-self passed straight
through.

```bash
cd mycord-discord-skill
uv sync --all-extras
cp .env.example .env
uv run mycord-repl start
uv run mycord-repl eval "client.user.id"
uv run mycord-repl stop
```

Instructions for agents live in [`mycord-discord-skill/SKILL.md`](mycord-discord-skill/SKILL.md);
implementation notes in [`mycord-discord-skill/README.md`](mycord-discord-skill/README.md).

### `mycord-telegram-skill/` — Telegram agent skill

Same persistent-REPL shape, different platform. Telegram publishes MTProto for
third-party clients, so this drives a real user account without the selfbot
exposure the Discord half carries. Three ways to authenticate, all
documented in the skill:

* **`tdl` desktop-session import** — no phone, no code, no secret in the repo
* **QR login** — scan from the Telegram app, no phone-number entry
* **`StringSession`** — a pre-generated auth-key string

Instructions for agents live in
[`mycord-telegram-skill/SKILL.md`](mycord-telegram-skill/SKILL.md).

### `mycord-irc-skill/` — IRC agent skill

For agents and operators. Drives IRC through `pydle` in the same persistent
Python REPL shape. TLS is on by default; SASL is available for registered IRC
accounts. The session buffers live messages and membership events received
after it connects.

```bash
cd mycord-irc-skill
uv sync --all-extras
cp .env.example .env      # set IRC_SERVER and IRC_NICK
uv run mycord-irc-repl start
uv run mycord-irc-repl eval "await client.join('#channel')"
uv run mycord-irc-repl stop
```

Instructions for agents live in
[`mycord-irc-skill/SKILL.md`](mycord-irc-skill/SKILL.md).

### `gvoice/` — Google Voice client

The odd one out, and independent on the same terms: TypeScript, its own `pnpm`
workspace, no shared dependency and no shared code, and not a REPL. Google
Voice has no public API, so this is reverse-engineered from traffic — account
control and SMS over HTTP, call signalling over SIP on WebSocket, and a
`werift` media plane doing ICE and DTLS-SRTP. Everything runs outside a
browser.

What works: REGISTER against the live registrar, the full `INVITE` dialog (the
call rings), and inbound audio — 1852 opus frames decoded in pure Node, with no
audio device and no virtual sound card. Outbound speech is real-time paced, and
the agent loop (VAD → STT → brain → TTS) has held a 3-turn conversation with a
live IVR. Sources run straight through Node's native TypeScript stripping, so
there is no build step.

```bash
cd gvoice
pnpm install
pnpm typecheck          # tsc --noEmit
node src/probe-sip.ts   # REGISTER against the live registrar and report
```

An external agent drives a live call over plain HTTP — `GET /events` for a
streaming transcript, `POST /say`, `/press`, `/shutup`, `/hangup` — so nothing
outside this directory has to link against it. Full capability table and the
traps that cost real debugging time in [`gvoice/README.md`](gvoice/README.md).

## Which one should I use?

* You want **Discord** — `mycord-discord-skill/`, whether you are writing code
  or exploring interactively. It is a Python library and a REPL, so you get
  tool-level control over *exact* calls and code you can edit, with no protocol
  layer in the way.
* You want **Telegram**, or you want a chat platform that is not a selfbot —
  `mycord-telegram-skill/`.
* You want **IRC**, with a live asynchronous client and no protocol layer —
  `mycord-irc-skill/`.
* You want **real voice calls or SMS without a browser** — `gvoice/`, which
  carries the unofficial-client caveat instead of the selfbot one.

## Layout

```
mycord/
├── mycord-discord-skill/         # Discord agent skill component
│   ├── src/mycord_repl/  # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
├── mycord-telegram-skill/        # Telegram agent skill component
│   ├── src/mycord_telegram_repl/ # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
├── mycord-irc-skill/             # IRC agent skill
│   ├── src/mycord_irc_repl/      # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
├── gvoice/                       # Google Voice client (TypeScript)
│   ├── src/             # HTTP control plane, SIP signalling, media plane
│   ├── tools/           # cookie-jar capture, external-agent example
│   └── package.json
└── docs/archive/         # historical research notes
```

The three Python components are standalone projects with their own
`pyproject.toml`; `gvoice/` is a standalone pnpm workspace with its own
`package.json` and lockfile.

```bash
cd mycord-discord-skill   && uv sync --all-extras && uv run pytest
cd mycord-telegram-skill  && uv sync --all-extras && uv run pytest
cd mycord-irc-skill       && uv sync --all-extras && uv run pytest
cd gvoice                 && pnpm install && pnpm typecheck
```

## Development

```bash
# mycord-discord-skill
cd mycord-discord-skill
make setup && make lint && make test

# mycord-irc-skill
cd mycord-irc-skill
make setup && make lint && make typecheck && make test

# gvoice
cd gvoice
pnpm install && pnpm typecheck
```

`mycord-discord-skill/` is the supported Discord path: no protocol layer sits in
front of it, and none should. `gvoice/` must never gain a dependency on any of
the Python components — it is independent on the same terms, and a shared
dependency would end that.

## Security

* Tokens are read from the environment or a `.env` file, never committed, and
  never logged.
* Confirm before bulk sends, edits, deletes, or reactions — other humans see
  them.
* `mycord-discord-skill` executes whatever Python you send it, at the same trust level
  as your shell. It is a local automation tool, not a sandbox.
* IRC credentials are sent only over TLS; the IRC REPL refuses password-based
  authentication when TLS is disabled. The IRC REPL also executes arbitrary
  Python at the same trust level as your shell.
* `gvoice` holds your Google cookie jar in `gv-session.json` — gitignored, mode
  0600, values never logged. Treat it like a password: it authenticates the
  whole account, it expires, and it must be re-exported from a signed-in
  browser.

## Archived research

[`docs/archive/`](docs/archive/) holds the `selfcord.py` migration evaluation
and the Python 3.10 compatibility study that preceded the current
discord.py-self approach. Superseded — kept for history.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self
[telethon]: https://github.com/Lonami/Telethon
[pydle]: https://codeberg.org/shiz/pydle
[werift]: https://github.com/shinyoshiaki/werift-webrtc