# mycord-repl — Discord as a Python library

A persistent, REPL-style session over [`discord.py-self`][dps]. One login, many
small steps: every snippet sees the globals and the live client from the
previous one.

> **Selfbot warning.** This logs in as a Discord *user*, not a bot. That
> violates Discord's Terms of Service and can get the account permanently
> banned. Use a throwaway personal account only.

This package depends **only** on `discord.py-self` and `python-dotenv`. It does
not depend on the `mycord-mcp/` FastMCP server in any way.

## Install

```bash
uv sync --all-extras
cp .env.example .env     # fill in DISCORD_TOKEN
```

To install the agent skill globally from GitHub:

```bash
npx skills add Apothic-AI/mycord --skill mycord-repl -g
```

## Use

```bash
uv run mycord-repl start
uv run mycord-repl status

uv run mycord-repl eval "client.user.id"
uv run mycord-repl eval "guild = client.get_guild(GUILD_ID)"
uv run mycord-repl eval "[(c.id, c.name) for c in guild.text_channels]"
uv run mycord-repl eval "[m.content for m in await guild.text_channels[0].history(limit=10).flatten()]"

uv run mycord-repl stop
```

Top-level `await` works, trailing expressions return their value, and `print()`
output is echoed back. Errors surface as tracebacks with a non-zero exit.

## How it works

`mycord-repl start` launches a detached daemon that owns one `discord.Client`
and listens on a Unix domain socket. Each `eval` sends a JSON frame, the daemon
executes it in a persistent namespace with `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT`, and
returns the value plus captured stdout.

| Component | Role |
| --- | --- |
| `session.py` | the daemon: client lifecycle, namespace, REPL execution |
| `client.py` | socket client used by the CLI and by tests |
| `cli.py` | `start` / `eval` / `status` / `reset` / `stop` |
| `protocol.py` | newline-delimited JSON wire format |

The socket lands in `$XDG_RUNTIME_DIR/mycord-repl.sock` (mode `0600`) or
`~/.cache/mycord-repl/s.sock`. Override with `--socket` or `MYCORD_REPL_SOCKET`.
Daemon logs go to `~/.cache/mycord-repl/session.log`.

## Develop

```bash
make setup
make test
make lint
```

60 tests cover the protocol, REPL semantics (trailing-expression values,
top-level await, state persistence, truncation, error mapping), the live socket
round trip, and the CLI. None require a token or network access.

## Agent instructions

See [`SKILL.md`](SKILL.md) — the operator-facing guide to working style,
recipes, and troubleshooting.

## License

MIT.

[dps]: https://github.com/dolfies/discord.py-self