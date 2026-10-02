# mycord

Discord automation built on [`discord.py-self`][dps], split into two
**independent** components.

> **This is a selfbot.** Both components log in as a Discord *user*, not a bot.
> That violates Discord's Terms of Service and **can get the account
> permanently banned**. Use a throwaway personal account only — never a work,
> shared, or customer-facing one.

## Components

| Directory | What it is | Depends on the other? |
| --- | --- | --- |
| [`mycord-mcp/`](mycord-mcp/) | FastMCP server exposing Discord tools over the Model Context Protocol | no |
| [`mycord-skill/`](mycord-skill/) | Agent skill: Discord as a Python library via a persistent REPL | no |

They share this repository and the `discord.py-self` dependency, and nothing
else. Neither imports the other, and you can use either one without touching
the other.

### `mycord-mcp/` — MCP server

For MCP clients. Exposes tools (currently a health check) over a stdio
transport, with connection state reported through a Pydantic contract.

```bash
cd mycord-mcp
uv sync --all-extras
cp .env.example .env      # fill in DISCORD_TOKEN
uv run python -m mycord.app.server
```

See [`mycord-mcp/README.md`](mycord-mcp/README.md).

### `mycord-skill/` — agent skill

For agents and operators. Drives the same library interactively: one login,
then many small Python steps that share state, with top-level `await` and
discord.py-self passed straight through.

```bash
cd mycord-skill
uv sync --all-extras
cp .env.example .env
uv run mycord-repl start
uv run mycord-repl eval "client.user.id"
uv run mycord-repl stop
```

Instructions for agents live in [`mycord-skill/SKILL.md`](mycord-skill/SKILL.md);
implementation notes in [`mycord-skill/README.md`](mycord-skill/README.md).

## Which one should I use?

* A **MCP client** (Claude Desktop, an MCP host, another agent runtime) wants
  `mycord-mcp/` — it speaks the protocol over stdio.
* You are **writing code or exploring interactively** wants `mycord-skill/` —
  it is a Python library and REPL, with no protocol layer in the way.
* Wanting tool-level control over *exact* calls, or reading code you can edit,
  both point at `mycord-skill/`.

## Layout

```
mycord/
├── mycord-mcp/           # FastMCP server component
│   ├── mycord/
│   │   ├── core/         # Pydantic contracts (no external deps)
│   │   ├── adapters/     # discord.py-self wrapper
│   │   └── app/          # FastMCP orchestration
│   ├── tests/
│   └── pyproject.toml
├── mycord-skill/         # agent skill component
│   ├── src/mycord_repl/  # session daemon + CLI
│   ├── tests/
│   └── SKILL.md
└── docs/archive/         # historical research notes
```

Both components are standalone Python projects with their own
`pyproject.toml`, `uv.lock`, and Python pin (3.13). Install and run them
independently:

```bash
cd mycord-mcp  && uv sync --all-extras && uv run pytest
cd mycord-skill && uv sync --all-extras && uv run pytest
```

## Development

```bash
# mycord-mcp
cd mycord-mcp
make setup && make lint && make typecheck && make test

# mycord-skill
cd mycord-skill
make setup && make lint && make test
```

`mycord-skill` must never gain a `fastmcp` dependency; `mycord-mcp` must never
be imported from it. That separation is the point of the split.

## Security

* Tokens are read from the environment or a `.env` file, never committed, and
  never logged.
* Confirm before bulk sends, edits, deletes, or reactions — other humans see
  them.
* `mycord-skill` executes whatever Python you send it, at the same trust level
  as your shell. It is a local automation tool, not a sandbox.

## Archived research

[`docs/archive/`](docs/archive/) holds the `selfcord.py` migration evaluation
and the Python 3.10 compatibility study that preceded the current
discord.py-self approach. Superseded — kept for history.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self