# mycord

Chat-platform automation, split into **independent** components: two for
Discord on [`discord.py-self`][dps], one for Telegram on
[`telethon`][telethon].

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

The two Discord components share this repository and the `discord.py-self`
dependency, and nothing else. Neither imports the other, and you can use either
one without touching the other. `mycord-telegram-skill/` is a separate platform
and shares no code with either — only the persistent-REPL shape.

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

## Which one should I use?

* A **MCP client** (Claude Desktop, an MCP host, another agent runtime) wants
  `mycord-discord-mcp/` — it speaks the protocol over stdio.
* You are **writing code or exploring interactively** wants `mycord-discord-skill/` —
  it is a Python library and REPL, with no protocol layer in the way.
* Wanting tool-level control over *exact* calls, or reading code you can edit,
  both point at `mycord-discord-skill/`.
* You want **Telegram**, or you want a chat platform that is not a selfbot —
  `mycord-telegram-skill/`.

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
```

All three are standalone Python projects with their own `pyproject.toml`,
`uv.lock`, and Python pin (3.13). Install and run them independently:

```bash
cd mycord-discord-mcp     && uv sync --all-extras && uv run pytest
cd mycord-discord-skill   && uv sync --all-extras && uv run pytest
cd mycord-telegram-skill  && uv sync --all-extras && uv run pytest
```

## Development

```bash
# mycord-discord-mcp
cd mycord-discord-mcp
make setup && make lint && make typecheck && make test

# mycord-discord-skill
cd mycord-discord-skill
make setup && make lint && make test
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

## Archived research

[`docs/archive/`](docs/archive/) holds the `selfcord.py` migration evaluation
and the Python 3.10 compatibility study that preceded the current
discord.py-self approach. Superseded — kept for history.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self
[telethon]: https://github.com/Lonami/Telethon