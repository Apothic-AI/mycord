---
name: mycord-irc-skill
description: Use IRC from Python through pydle in a persistent REPL-style session (mycord-irc-repl). Use when asked to read live channel or private messages, inspect IRC users and channels, join or leave channels, send messages, or monitor IRC activity. Supports TLS and optional SASL authentication.
---

# mycord-irc — IRC as a Python library

Operate an IRC connection by writing Python against
[`pydle`](https://codeberg.org/shiz/pydle) in a **persistent session**. One
connection, many small steps: each `eval` sees the globals and the live client
from previous calls.

## The loop

```bash
cd mycord-irc-skill
uv sync --all-extras
cp .env.example .env       # set IRC_SERVER and IRC_NICK
uv run mycord-irc-repl start
uv run mycord-irc-repl status
uv run mycord-irc-repl eval "await client.join('#channel')"
uv run mycord-irc-repl stop
```

The default connection uses TLS on port `6697`. Use `--no-connect` to start an
offline REPL for introspection without opening a network connection.

## Connection and authentication

Set connection values in `.env` or the environment. CLI options can override
the server, port, nickname, username, real name, and TLS setting.

| Variable | Purpose | Secret? |
| --- | --- | --- |
| `IRC_SERVER` | Hostname, for example `irc.libera.chat` | No |
| `IRC_PORT` | Port; defaults to `6697` with TLS or `6667` without | No |
| `IRC_NICK` | Nickname to register | No |
| `IRC_USERNAME` | IRC username; defaults to lower-cased nickname | No |
| `IRC_REALNAME` | IRC real name; defaults to nickname | No |
| `IRC_TLS` | Enable TLS; defaults to `true` | No |
| `IRC_SERVER_PASSWORD` | Optional server `PASS` credential | **Yes** |
| `IRC_SASL_USERNAME` | Optional SASL account name | **Yes** |
| `IRC_SASL_PASSWORD` | Optional SASL password | **Yes** |

Use TLS whenever sending credentials. The CLI refuses a server or SASL password
when TLS is disabled. Prefer SASL for registered accounts when the network
supports it. The `pydle[sasl]` extra is installed for SASL PLAIN support.

Do not put passwords in command-line arguments: process listings and shell
history can expose them. Use a mode-restricted `.env` file or environment
injection. The daemon does not print credentials or include them in `status`.

## What is already in scope

Every snippet runs with these names bound:

| Name | What it is |
| --- | --- |
| `client` | The live `pydle.Client` with recent events recorded in memory |
| `pydle` | The imported `pydle` module |
| `session` | The `ReplSession` object |
| `events` | A bounded deque of the last 500 connection, message, join, and part events |
| `wait_ready` | Async helper that waits for IRC registration |
| `wait_event` | Async helper that waits for a new event |
| `_` | The previous snippet's result |

IRC does not generally provide chat history to a newly connected client. This
skill records **live events received after it starts**, up to 500 events. It
does not silently backfill old messages.

## Recipes

### Check the connection and current nickname

```bash
uv run mycord-irc-repl status
uv run mycord-irc-repl eval "(client.nickname, client.connected, client.registered)"
```

### List joined channels and their current users

```bash
uv run mycord-irc-repl eval "list(client.channels)"
uv run mycord-irc-repl eval "sorted(client.channels['#channel']['users'])"
```

The channel and user state is only available after the server has sent it.
Channel names are case-insensitive according to the network's IRC casemapping.

### Read live messages

```bash
uv run mycord-irc-repl eval "\
[(e['at'], e['channel'], e['nick'], e['text'])
 for e in events if e['type'] == 'channel_message'][-20:]
"
```

Filter locally after one fetch:

```bash
uv run mycord-irc-repl eval "\
matches = [e for e in events
           if e['type'] == 'channel_message' and 'deploy' in e['text'].lower()]
"
uv run mycord-irc-repl eval "[(e['channel'], e['nick'], e['text']) for e in matches]"
```

### Join or leave a channel

```bash
uv run mycord-irc-repl eval "await client.join('#channel')"
uv run mycord-irc-repl eval "await client.part('#channel')"
```

### Send a channel or private message

```bash
uv run mycord-irc-repl eval "await client.message('#channel', 'hello')"
uv run mycord-irc-repl eval "await client.message('Nickname', 'hello')"
```

### Wait for the next live message

```bash
uv run mycord-irc-repl eval "\
event = await wait_event('channel_message', timeout=60)
(event['channel'], event['nick'], event['text'])
"
```

`wait_event()` only matches events newer than the call (or newer than the
provided `after` sequence number). `TimeoutError` means no matching event
arrived before the timeout.

### Inspect raw library state or use another pydle method

```bash
uv run mycord-irc-repl eval "[name for name in dir(client) if 'topic' in name]"
uv run mycord-irc-repl eval "client.whois"
uv run mycord-irc-repl eval "[name for name in dir(pydle.Client) if not name.startswith('_')]"
```

Use Python introspection for network-specific features rather than guessing
method names.

## Working style

**Prefer several small `eval` calls over one large script.** Bind useful values
and reuse them; every successful call keeps its globals.

```bash
uv run mycord-irc-repl eval "await client.join('#channel')"
uv run mycord-irc-repl eval "channel = client.channels['#channel']"
uv run mycord-irc-repl eval "sorted(channel['users'])"
```

Other useful commands:

| Command | Effect |
| --- | --- |
| `mycord-irc-repl start` | Start the session daemon and connect |
| `mycord-irc-repl start --no-connect` | Start an offline REPL |
| `mycord-irc-repl status` | Report connection, server, channels, and uptime |
| `mycord-irc-repl eval CODE` | Evaluate Python (`--file PATH`, `--timeout SEC`) |
| `mycord-irc-repl reset` | Clear agent-defined globals and keep the connection |
| `mycord-irc-repl stop` | Disconnect and remove the session socket |

Use `--socket PATH` before or after a subcommand to select a non-default
socket. `MYCORD_IRC_REPL_SOCKET` also works. Daemon logs go to
`~/.cache/mycord-irc-repl/session.log`.

## Safety rails

* Stop the session when the task ends: `mycord-irc-repl stop`.
* IRC messages, joins, parts, and other commands are visible to people on the
  network. Send only what the user asked you to send. Confirm before bulk
  messages, kicks, bans, or channel mode changes.
* Do not connect to a network or channel the user did not specify when account
  or network access is consequential.
* Keep SASL and server passwords out of snippets, logs, command lines, and
  version control. Confirm identity with `client.nickname`, never by printing
  a credential.
* The daemon executes whatever Python you send it, at the same trust level as
  your shell. It is a local automation tool, not a sandbox.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `no mycord IRC session` | Not started or already stopped; run `start` |
| `IRC_SERVER` / `IRC_NICK` configuration error | Set the server and nickname in `.env` or pass CLI options |
| `IRC registration did not complete` | Check network, port, TLS, nickname collision, SASL, and server logs |
| `refusing to send IRC credentials without TLS` | Enable TLS or remove the password; do not send credentials in plaintext |
| no old messages appear | IRC history is not available by default; only messages received after connecting are buffered |
| `wait_event()` times out | No new matching event arrived before the timeout |
| channel user list is empty | Wait for the server's channel state or join the channel first |
| session hangs | A snippet is blocked; raise `--timeout` or split it into smaller calls |

## Relationship to the Discord and Telegram skills

This is a standalone Python package and daemon. It shares only the persistent
REPL convention with `mycord-discord-skill/` and `mycord-telegram-skill/`.
It uses `pydle`, separate IRC credentials, and a separate socket. Do not import
across the skills or start another skill's daemon to use IRC.
