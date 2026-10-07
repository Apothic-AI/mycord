# mycord-irc-repl — IRC as a Python library

A persistent REPL-style IRC session using
[`pydle`](https://codeberg.org/shiz/pydle). One connection, many small steps:
each snippet sees the same live client and Python globals.

## Why pydle?

`pydle` is the cleanest fit for this REPL because its client API is native
`asyncio`, uses coroutines for connect/join/message operations, and implements
IRCv3 features including SASL. That matches the event-loop and top-level-await
model used by the neighboring Discord and Telegram skills.

The comparison was:

| Package | Fit |
| --- | --- |
| **`pydle`** | Selected: async-first client, IRCv3 support, maintained upstream, simple direct API |
| `irc` (`python-irc`) | Actively maintained and full-featured, but its traditional event/reactor model is less direct for a persistent asyncio REPL |
| `irc3` | Asyncio and SASL, but older plugin-centric API and slower-moving project |

The package pins to the current `pydle` major line (`pydle[sasl]>=1.2.0,<2.0`)
and installs the SASL extra for networks requiring account authentication.

## Install and use

```bash
uv sync --all-extras
cp .env.example .env
# Set IRC_SERVER and IRC_NICK in .env.
uv run mycord-irc-repl start
uv run mycord-irc-repl status

uv run mycord-irc-repl eval "await client.join('#channel')"
uv run mycord-irc-repl eval "await client.message('#channel', 'hello')"
uv run mycord-irc-repl eval "[(e['nick'], e['text']) for e in events]"

uv run mycord-irc-repl stop
```

TLS is on by default. The default port is `6697` for TLS and `6667` without it.
Use `--no-connect` to start an offline session. Optional server PASS and SASL
credentials come from the environment or `.env`; credentials are never
forwarded as command-line arguments or printed by `status`.

For full agent instructions and recipes, see [`SKILL.md`](SKILL.md).

To install the agent skill globally:

```bash
npx skills add Apothic-AI/mycord --skill mycord-irc-skill -g
```

## Layout

```
mycord-irc-skill/
├── src/mycord_irc_repl/
│   ├── config.py     # environment, .env, and CLI connection settings
│   ├── protocol.py   # newline-delimited JSON over a Unix socket
│   ├── session.py    # pydle client, event buffer, persistent namespace, daemon
│   ├── client.py     # thin socket client
│   └── cli.py        # start / eval / status / reset / stop
├── tests/
├── SKILL.md
└── pyproject.toml
```

## Development

```bash
make setup && make lint && make typecheck && make test
```

Tests use an offline REPL and a local Unix socket. They do not contact an IRC
network or need IRC credentials.

## Security and limits

* IRC traffic is plaintext unless TLS is enabled; this skill defaults to TLS
  and refuses to send configured credentials without it.
* Use SASL or network passwords only through protected environment variables or
  a local `.env` that is excluded from version control.
* The session records only live events after it connects, in a bounded buffer
  of 500 events; it does not provide general server history.
* Sending messages and issuing channel commands affect other people. Confirm
  before bulk or destructive actions.
* The REPL runs arbitrary local Python, at the same trust level as the shell.

## License

MIT.
