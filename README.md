# mycord

Chat-platform automation, split into **independent** components: two for
Discord on [`discord.py-self`][dps], one for Telegram on
[`telethon`][telethon], and one for IRC on [`pydle`][pydle].

> **The Discord components are selfbots.** They log in as a Discord *user*,
> not a bot. That violates Discord's Terms of Service and **can get the account
> permanently banned**. Use a throwaway personal account only — never a work,
> shared, or customer-facing one.
>
> The Telegram component has no such problem: Telegram publishes MTProto for
> third-party clients, so user-account automation there is supported rather
> than forbidden.

## Components

| Directory | What it is | Depends on the other? |
| --- | --- | --- |
| [`mycord-discord-mcp/`](mycord-discord-mcp/) | FastMCP server exposing Discord tools over the Model Context Protocol | no |
| [`mycord-discord-skill/`](mycord-discord-skill/) | Agent skill: Discord as a Python library via a persistent REPL | no |
| [`mycord-telegram-skill/`](mycord-telegram-skill/) | Agent skill: Telegram as a Python library via a persistent REPL | no |
| [`mycord-irc-skill/`](mycord-irc-skill/) | Agent skill: IRC as a Python library via a persistent REPL | no |

The two Discord components share this repository and the `discord.py-self`
dependency, and nothing else. Neither imports the other, and you can use either
one without touching the other. Telegram and IRC are separate platforms and
share no code with the Discord components or each other, only the
persistent-REPL shape.

### `mycord-discord-mcp/` — MCP server

For MCP clients. Exposes tools (currently a health check) over a stdio
transport, with connection state reported through a Pydantic contract.

```bash
cd mycord-discord-mcp
uv sync --all-extras
cp .env.example .env      # fill in DISCORD_TOKEN
uv run python -m mycord.app.server
```

See [`mycord-discord-mcp/README.md`](mycord-discord-mcp/README.md).

### `mycord-discord-skill/` — agent skill

For agents and operators. Drives the same library interactively: one login,
then many small Python steps that share state, with top-level `await` and
discord.py-self passed straight through.

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

## Which one should I use?

* A **MCP client** (Claude Desktop, an MCP host, another agent runtime) wants
  `mycord-discord-mcp/` — it speaks the protocol over stdio.
* You are **writing code or exploring interactively** wants `mycord-discord-skill/` —
  it is a Python library and REPL, with no protocol layer in the way.
* Wanting tool-level control over *exact* calls, or reading code you can edit,
  both point at `mycord-discord-skill/`.
* You want **Telegram**, or you want a chat platform that is not a selfbot —
  `mycord-telegram-skill/`.
* You want **IRC**, with a live asynchronous client and no MCP layer —
  `mycord-irc-skill/`.

## Layout

```
mycord/
├── mycord-discord-mcp/           # FastMCP server component
│   ├── mycord/
│   │   ├── core/         # Pydantic contracts (no external deps)
│   │   ├── adapters/     # discord.py-self wrapper
│   │   └── app/          # FastMCP orchestration
│   ├── tests/
│   └── pyproject.toml
├── mycord-discord-skill/         # Discord agent skill component
│   ├── src/mycord_repl/  # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
├── mycord-telegram-skill/        # Telegram agent skill component
│   ├── src/mycord_telegram_repl/ # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
└── docs/archive/         # historical research notes
├── mycord-irc-skill/             # IRC agent skill
│   ├── src/mycord_irc_repl/      # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
```

All three are standalone Python projects with their own `pyproject.toml`,
All four are standalone Python projects with their own `pyproject.toml`,

```bash
cd mycord-discord-mcp     && uv sync --all-extras && uv run pytest
cd mycord-discord-skill   && uv sync --all-extras && uv run pytest
cd mycord-telegram-skill  && uv sync --all-extras && uv run pytest
cd mycord-irc-skill       && uv sync --all-extras && uv run pytest
```

## Development

```bash
# mycord-discord-mcp
cd mycord-discord-mcp
make setup && make lint && make typecheck && make test

# mycord-discord-skill
cd mycord-discord-skill
make setup && make lint && make test

# mycord-irc-skill
cd mycord-irc-skill
make setup && make lint && make typecheck && make test
```

`mycord-discord-skill` must never gain a `fastmcp` dependency; `mycord-discord-mcp` must never
be imported from it. That separation is the point of the split.

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

## Archived research

[`docs/archive/`](docs/archive/) holds the `selfcord.py` migration evaluation
and the Python 3.10 compatibility study that preceded the current
discord.py-self approach. Superseded — kept for history.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self
[telethon]: https://github.com/Lonami/Telethon
[pydle]: https://codeberg.org/shiz/pydle