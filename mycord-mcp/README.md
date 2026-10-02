# mycord-mcp — Discord MCP server

FastMCP server exposing Discord over the Model Context Protocol, backed by
[`discord.py-self`][dps].

> **Selfbot warning.** This logs in as a Discord *user*, not a bot. That
> violates Discord's Terms of Service and can get the account permanently
> banned. Use a throwaway personal account only.

This is the MCP half of the repo. It does **not** depend on `mycord-skill/`; the
two components are independent. If you want a Python library instead of an MCP
server, use [`../mycord-skill/`](../mycord-skill/).

## Architecture

Orthogonal layers, dependencies pointing one way: `app → adapters → core`.

```
mycord/
├── core/         # Pure domain logic (Pydantic models, no external deps)
├── adapters/     # External service integrations (discord.py-self wrapper)
└── app/          # Application orchestration (FastMCP server)
```

## Install

```bash
cd mycord-mcp
uv sync --all-extras
cp .env.example .env     # fill in DISCORD_TOKEN
```

Python 3.13 is pinned via `.python-version`; 3.10+ is supported.

## Run

```bash
uv run python -m mycord.app.server     # stdio transport
```

The server loads `DISCORD_TOKEN`, starts the Discord client in the background,
and then serves MCP over stdio.

## Tools

### `mycord.health`

Reports server and Discord connection state.

```json
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00Z",
  "discord_connected": true,
  "user_id": "123456789012345678",
  "username": "YourUsername#1234"
}
```

`status` is `ok` when serving (with or without Discord), `degraded` when the
adapter errors while being queried, and `error` when the contract itself cannot
be built.

## Develop

```bash
make setup         # uv sync --all-extras
make fmt           # black
make lint          # ruff
make typecheck     # mypy --strict
make test          # pytest
make test-unit     # -m unit
make test-integration
```

Coverage gating is currently disabled (`make test-cov` is a stub) because the
suite does not yet cover `app/server.py` or `adapters/discord_client.py`; it
passes `--no-cov` deliberately.

## Known gaps

* `[project.scripts]` points at `mycord.app.server:main`, but no `main` exists —
  the console script is broken; use `python -m mycord.app.server` until fixed.
* `__main__` calls `asyncio.run(initialize_discord())`, which tears its loop
  down before `mcp.run()` starts, so the background Discord task is unlikely to
  survive. Migrating this to FastMCP's `lifespan=` is the proper fix.
* Message fetch/send tools are not implemented — only `health` exists.

## Notes on the dependency stack

* `fastmcp` 4.x is pinned. Its `@mcp.tool()` returns the **original function**,
  not a `FunctionTool` wrapper; nothing here relies on the old behaviour.
* `discord.py-self` 2.1.0 no longer has the `flatten_user` /
  `inspect.signature` bug that previously forced Python 3.10. The import-order
  workaround in `server.py` is retained out of caution but is no longer
  required, and `self_bot=True` on `discord.Client` was never meaningful —
  selfbot mode is detected from the token.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self