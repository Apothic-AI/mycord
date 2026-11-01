# mycord

Messaging automation, split into **independent** components: one for Discord
on [`discord.py-self`][dps], one for Telegram on [`telethon`][telethon], one for
IRC on [`pydle`][pydle], one for email on [`himalaya`][himalaya], one for
Google Voice on [`werift`][werift], one for Signal on [`signal-cli`][signalcli]
plus the `signal-call-tunnel` PCM socket, and one for Zoom, which needs no library
at all — it drives the desktop client and carries call audio through the system mixer.

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
>
> **`mycord-zoom-skill/` drives the Zoom desktop client** through the `zoommtg://`
> URL scheme Zoom registers for itself, which Zoom documents as changeable at any
> time and markets RTMS *instead of*. It is a local automation tool acting as the
> account's own human, not a bot with scopes.

## Components

| Directory | What it is | Depends on the other? |
| --- | --- | --- |
| [`mycord-discord-skill/`](mycord-discord-skill/) | Agent skill: Discord as a Python library via a persistent REPL | no |
| [`mycord-telegram-skill/`](mycord-telegram-skill/) | Agent skill: Telegram as a Python library via a persistent REPL | no |
| [`mycord-irc-skill/`](mycord-irc-skill/) | Agent skill: IRC as a Python library via a persistent REPL | no |
| [`mycord-email-skill/`](mycord-email-skill/) | Agent skill: email from any provider through the `himalaya` CLI | no |
| [`google-voice-skill/`](google-voice-skill/) | Agent skill: Google Voice reading and calls over the `gvoice` library | no |
| [`mycord-skill/`](mycord-skill/) | **Start here** — routing skill: which medium, which component to install, whether voice works | no |
| [`ferdium-skill/`](ferdium-skill/) | Agent skill: drive a Ferdium install over CDP (headless by default) | no |
| [`mycord-slack-skill/`](mycord-slack-skill/) | Agent skill: Slack with no app registration, full same-as-you access | no |
| [`mycord-signal-skill/`](mycord-signal-skill/) | Agent skill: Signal as a linked device over `signal-cli`'s JSON-RPC daemon, with 1:1 call audio over a Unix PCM socket | no |
| [`mycord-zoom-skill/`](mycord-zoom-skill/) | Agent skill: join/leave/observe the Zoom desktop client via `zoommtg://`, with full-duplex call audio through PipeWire virtual devices | no |
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

### `mycord-email-skill/` — email agent skill

For agents and operators. Wraps the [`himalaya`][himalaya] CLI in an account
layer, because himalaya deliberately keeps configuration and credentials out of
the binary — which is correct, and leaves every new mailbox as a hand-edited
config plus another set of invented environment variables.

Himalaya does all the mail work: IMAP, SMTP, JMAP, Microsoft Graph, Gmail,
Maildir, MIME, TLS, and one shared query DSL. The wrapper answers *which
account*, *where its secret lives*, and *how to connect*, then passes your
arguments through verbatim — so `mycord-email envelope search subject foo` is
just himalaya, with an account applied.

Accounts are directories with a deliberate file split: `meta.toml` (0644,
non-secret, safe to print) and `credentials.env` (0600, read per call and passed
to himalaya only through that child process's environment). Providers are a data
table, not code — plain IMAP/SMTP, Gmail, Microsoft 365, and optionally Proton
Mail Bridge with its self-signed certificate pinned.

```bash
uv tool install mycord-email-skill     # puts `mycord-email` on PATH
mycord-email accounts add work you@example.com --provider imap
mycord-email accounts set-secret work
mycord-email envelope list --page-size 10
```

Instructions for agents live in
[`mycord-email-skill/SKILL.md`](mycord-email-skill/SKILL.md).

### `mycord-signal-skill/` — Signal agent skill

For agents and operators. Signal has no bot API, but a *linked device* is a first-class
client — which is exactly what `signal-cli` becomes. Reads, sends and reactions go through
its JSON-RPC daemon; 1:1 call audio goes through `signal-call-tunnel`, which owns the
RingRTC peer connection and exposes each call as raw PCM on a per-call Unix socket
(48 kHz mono s16le, 960-byte windows, read = remote audio, write = your mic).

Both components must be at pinned versions and a mismatch only bites *inside* a call, so
`doctor` and `call-guard` refuse up front with exit 3 and the exact remediation — the
refusal is the feature. `send` and `call start` are dry runs without `--confirm`, so an
agent exploring the surface cannot reach a person.

```bash
cd mycord-signal-skill
uv sync --all-extras
uv run mycord-signal doctor        # 0 = call-ready, 3 = stale pin / no daemon / no account
uv run mycord-signal contacts --json
uv run mycord-signal send --to +1555… -m "…"            # dry run
```

Instructions for agents live in
[`mycord-signal-skill/SKILL.md`](mycord-signal-skill/SKILL.md).

### `mycord-zoom-skill/` — Zoom agent skill

Zoom publishes no local API for the ordinary desktop client, and every developer surface
(Meeting SDK, RTMS) needs a registered Marketplace app. Both halves of this skill avoid
that: join/leave/observe uses the `zoommtg://` URL scheme the client registers for itself —
the same link a human clicks — and call audio uses the fact that Zoom is an ordinary client
of the system mixer.

The audio plane is three nodes rather than two: Zoom plays into a sink the agent monitors,
and records from a source fed by a separate sink the agent writes to. One sink cannot serve
both directions without feeding Zoom's own voice back into Zoom's microphone, which is why
this needs no echo guard. Teardown restores your default sink and source **first**, then
unloads, so a failed unload still leaves you hearing your own audio; `audio recover`
finishes an interrupted teardown. TTS and ASR reuse `gvoice`'s Piper and
parakeet/whisper workers — nothing is reimplemented and no model is downloaded.

```bash
cd mycord-zoom-skill
uv sync --all-extras
uv run mycord-zoom audio up                      # inert: creates devices, moves nothing
uv run mycord-zoom join "https://zoom.us/j/1234567890"
uv run mycord-zoom audio route --to-agent
uv run mycord-zoom audio say "Hello, I'm an agent."
uv run mycord-zoom audio down                    # ALWAYS
```

Instructions for agents live in
[`mycord-zoom-skill/SKILL.md`](mycord-zoom-skill/SKILL.md).

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
* You want **email**, from an inbox to a verification code — `mycord-email-skill/`.
* You want **Google Voice texts, or a real phone call, from an agent** —
  `google-voice-skill/`, which wraps `gvoice/` and carries the unofficial-client
  caveat instead of the selfbot one.
* You want the **raw Google Voice protocol** to build on — `gvoice/` itself.
* You want **Slack**, with the same access you have in the browser — `mycord-slack-skill/`.
* You want **Signal**, or a real two-way call with a Signal contact — `mycord-signal-skill/`.
  It refuses the call path loudly rather than failing mid-call when the pins are stale.
* You want to **join, listen to or speak into a Zoom meeting** — `mycord-zoom-skill/`, which
  carries full-duplex audio without a Marketplace app.
* You do not yet know **which medium or component** you need — start at
  `mycord-skill/`, which routes to all of them and says what is shipped vs. planned.
* You want to drive **any service already configured in Ferdium** (WhatsApp, and any
  other chat medium) without a bot registration — `ferdium-skill/` is the base; a
  medium-specific skill layers the selectors on top.

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
├── mycord-skill/                 # routing skill: pick a medium + install it
├── ferdium-skill/                # CDP driver for a Ferdium install
│   ├── src/mycord_ferdium/ # launch, webview discovery, page primitives
│   ├── tests/
│   └── SKILL.md
├── mycord-slack-skill/           # Slack: slackdump reads + slack_sdk passthrough
│   ├── src/mycord_slack/ # session capture, api passthrough, read verbs
│   ├── tests/
│   └── SKILL.md
├── mycord-signal-skill/          # Signal: JSON-RPC daemon + 1:1 call PCM socket
│   ├── src/mycord_signal/ # deps/pin detection, rpc client, reads, send, calls
│   ├── scripts/provision.sh # GPG-verified signal-cli install + tunnel build
│   ├── tests/
│   └── SKILL.md
├── mycord-zoom-skill/            # Zoom: zoommtg:// deep links + PipeWire audio plane
│   ├── src/mycord_zoom/ # audio plane, desktop control, speech, CLI
│   ├── tests/
│   └── SKILL.md
├── google-voice-skill/           # Google Voice agent skill over gvoice/
│   ├── src/gvoice_skill/ # session capture, proto decoding, CLI
│   ├── tests/
│   └── SKILL.md
├── gvoice/                       # Google Voice client (TypeScript)
│   ├── src/             # HTTP control plane, SIP signalling, media plane
│   ├── tools/           # cookie-jar capture, external-agent example
│   └── package.json
└── docs/                 # determinations, roadmap, planning records
```

The Python components are standalone projects with their own
`pyproject.toml`; `gvoice/` is a standalone pnpm workspace with its own
`package.json` and lockfile.

```bash
cd mycord-discord-skill   && uv sync --all-extras && uv run pytest
cd mycord-telegram-skill  && uv sync --all-extras && uv run pytest
cd mycord-irc-skill       && uv sync --all-extras && uv run pytest
cd mycord-email-skill     && uv sync --all-extras && uv run pytest
cd google-voice-skill     && uv sync --all-extras && uv run pytest -m "not live"
cd ferdium-skill          && uv sync --all-extras && uv run pytest -m "not live"
cd mycord-slack-skill     && uv sync --all-extras && uv run pytest -m "not live"
cd mycord-signal-skill    && uv sync --all-extras && uv run pytest -m "not live"
cd mycord-zoom-skill      && uv sync --all-extras && uv run pytest -m "not live"
cd gvoice                 && pnpm install && pnpm typecheck
```

`-m "not live"` is the offline suite. Both new skills also carry live tests that touch the
real audio server, the real daemon or a real meeting; those create and destroy real devices,
so they are opt-in (`make test-live`).

## Development

```bash
# mycord-discord-skill
cd mycord-discord-skill
make setup && make lint && make test

# mycord-irc-skill
cd mycord-irc-skill
make setup && make lint && make typecheck && make test

# mycord-email-skill
cd mycord-email-skill
make setup && make lint && make typecheck && make test

# google-voice-skill
cd google-voice-skill
make setup && make lint && make typecheck && make test

# ferdium-skill
cd ferdium-skill
make setup && make lint && make typecheck && make test

# mycord-slack-skill
cd mycord-slack-skill
make setup && make lint && make typecheck && make test

# mycord-signal-skill
cd mycord-signal-skill
make setup && make lint && make typecheck && make test

# mycord-zoom-skill
cd mycord-zoom-skill
make setup && make lint && make typecheck && make test

# gvoice
cd gvoice
pnpm install && pnpm typecheck
```

`mycord-discord-skill/` is the supported Discord path: no protocol layer sits in
front of it, and none should. `gvoice/` must never gain a dependency on any of
the Python components — it is independent on the same terms, and a shared
dependency would end that. The dependency runs one way only:
`google-voice-skill/` calls into `gvoice/` through Node, and `gvoice/` knows
nothing about it.

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
* `mycord-email-skill` keeps credentials in a per-account
  `credentials.env` (mode 0600) that is read per call and passed to himalaya
  only through that child process's environment. It is never rendered into
  generated config and never exported into your shell. A credential file that is
  group- or world-readable is a hard error, not a warning.
* `google-voice-skill` prints cookie *names* and host counts, never values, and
  its capture path copies the browser profile rather than writing to it.
* `gvoice` holds your Google cookie jar in `gv-session.json` — gitignored, mode
  0600, values never logged. Treat it like a password: it authenticates the
  whole account, it expires, and it must be re-exported from a signed-in
  browser.

## Library determinations

`docs/determine/` holds a per-medium determination — the optimal no-app-registration
path, the ranked alternatives, and a voice verdict — for Signal, X, Facebook Messenger,
Microsoft Teams, Zoom, Skype and WeChat. Signal and Zoom have since shipped as skills;
Skype is retired upstream and gets no skill; WeChat is closed on policy. X is answered but
structurally fragile, and its only voice implementation is AGPL-3.0 — no skill yet.

## Roadmap

Committed future work — including voice (ASR + TTS) for WhatsApp and Slack calls — is
tracked in [`docs/roadmap.md`](docs/roadmap.md). R3/R3a (WeChat) are closed rather than
deferred; the rest is not in progress.

## Archived research

[`docs/archive/`](docs/archive/) holds the `selfcord.py` migration evaluation, the
Python 3.10 compatibility study that preceded the current discord.py-self approach, and
the 2026-10-25 reboot handoff. Superseded — kept for history, except the audio-teardown
discipline and verified host facts in the handoff, which still apply.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self
[telethon]: https://github.com/Lonami/Telethon
[pydle]: https://codeberg.org/shiz/pydle
[himalaya]: https://github.com/pimalaya/himalaya
[werift]: https://github.com/shinyoshiaki/werift-webrtc
[signalcli]: https://github.com/AsamK/signal-cli