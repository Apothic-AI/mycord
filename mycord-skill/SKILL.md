---
name: mycord-repl
description: Drive a real Discord user account from Python using discord.py-self through a persistent REPL-style session (mycord-repl). Use when asked to read, send, edit, react to, search, or monitor Discord messages, channels, guilds, DMs, or members - as a library, not an MCP server. Relies on selfbot automation, which violates Discord's ToS and can get an account banned.
---

# mycord — Discord as a Python library

Operate a Discord user account by writing Python against `discord.py-self` in a
**persistent session**. One login, many small steps: every `eval` sees the
globals and the live client from the previous one, so you explore in stages
instead of writing one giant script.

> **This is a selfbot.** It logs in as a *user*, not a bot. That violates
> Discord's Terms of Service and **can get the account permanently banned**.
> Only use it on a personal account you are willing to lose. Never point it at
> a shared, work, or customer-facing account.

This skill is **independent of the MCP server** in `mycord-mcp/`. If you were
handed this skill, you do not need to start, install, or talk to any MCP server.

---

## The loop

```bash
cd mycord-skill
uv run mycord-repl start            # logs in; waits for READY
uv run mycord-repl status           # confirm who you are connected as
uv run mycord-repl eval "<python>"  # run a snippet; state persists
uv run mycord-repl stop             # log out cleanly
```

The first `start` blocks until the gateway is ready. Subsequent `eval` calls are
fast — the client is already connected.

## What is already in scope

Every snippet runs with these bound:

| Name | What it is |
| --- | --- |
| `client` | the live `discord.Client` |
| `discord` | the `discord.py-self` module |
| `wait_ready` | `async` helper that blocks until READY |
| `session` | the `ReplSession` object |
| `_` | the previous snippet's result |

You do not need to `import discord` — though doing so is harmless.

## Output you get back

* The **value** of a trailing expression (REPL style). `client.guilds` prints
  the object; `print(x)` prints `x` and nothing else is returned.
* Anything you `print()` is captured and echoed first.
* Errors come back as a traceback and a non-zero exit code.

Both are truncated at 20 000 characters.

---

## Recipes

### Where am I?

```bash
uv run mycord-repl eval "[(g.id, g.name, len(g.channels)) for g in client.guilds]"
uv run mycord-repl eval "client.user.id"
```

### Read recent messages

```bash
uv run mycord-repl eval "\
guild = client.get_guild(GUILD_ID)
channel = next(c for c in guild.channels if c.name == 'general')
[(m.id, m.author, m.content) for m in await channel.history(limit=20).flatten()]
"
```

Note `history()` is a **lazy iterator** — you must `await ....flatten()` or
`await ....get()` to materialise it. Forgetting `flatten()` returns an empty
`LazyFlatMap`, not an error, which is the single most common mistake here.

### Search backwards from a known point

```bash
uv run mycord-repl eval "\
channel = client.get_channel(CHANNEL_ID)
msgs = await channel.history(limit=500).flatten()
[m.content for m in msgs if 'deploy' in m.content.lower()]
"
```

State persists, so fetch once and then filter repeatedly:

```bash
uv run mycord-repl eval "msgs = await channel.history(limit=500).flatten()"
uv run mycord-repl eval "[m.author.name for m in msgs][:10]"
uv run mycord-repl eval "sum(1 for m in msgs if m.mentions)"
```

### Send a message

```bash
uv run mycord-repl eval "await channel.send('hello from an agent')"
```

### React, edit, delete

```bash
uv run mycord-repl eval "await msg.add_reaction('\N{THUMBS UP SIGN}')"
uv run mycord-repl eval "await msg.edit(content='corrected')"
uv run mycord-repl eval "await msg.delete()"
```

### Wait for the next message

```bash
uv run mycord-repl eval "m = await client.wait_for('message', timeout=30); (m.author, m.content)"
```

### Read a DM

```bash
uv run mycord-repl eval "\
dm = next(c for c in client.private_channels if c.recipient.id == USER_ID)
[(m.author, m.content) for m in await dm.history(limit=10).flatten()]
"
```

---

## Working style

**Prefer several small evals over one big script.** If a step fails you keep
the earlier bindings, so you can inspect and retry just the broken line.

```bash
# good: incremental, each step checkable
uv run mycord-repl eval "guild = client.get_guild(123)"
uv run mycord-repl eval "guild"
uv run mycord-repl eval "channels = [c for c in guild.text_channels]"
uv run mycord-repl eval "[(c.id, c.name, c.last_message_id) for c in channels]"

# worse: one shot; a typo loses all the work
uv run mycord-repl eval "print([(c.name) for c in client.get_guild(123).text_channels])"
```

Other habits that pay off:

* Bind a name (`channel = ...`) and reuse it next call instead of re-resolving.
* When a lazy iterator surprises you, wrap it in `list(...)` or check `len()`.
* Use `--file snippet.py` for anything longer than a couple of lines — easier
  to edit and re-run than a giant quoted string.
* `reset` clears your globals but keeps the connection, handy when names get
  tangled. `stop` then `start` re-logs in from scratch.
* `repr()` is truncated for huge collections; slice or aggregate first.

## Inspect the library

Because it is a straight passthrough, you can always introspect instead of
guessing:

```bash
uv run mycord-repl eval "[m for m in dir(discord.TextChannel) if 'histor' in m]"
uv run mycord-repl eval "discord.Embed"
uv run mycord-repl eval "client.get_channel"
```

## Commands

| Command | Effect |
| --- | --- |
| `mycord-repl start` | launch the session; `--no-connect` for an offline session |
| `mycord-repl status` | connection state, user id, uptime |
| `mycord-repl eval CODE` | run a snippet (`--file PATH`, `--timeout SEC`) |
| `mycord-repl reset` | drop agent-defined globals, keep the connection |
| `mycord-repl stop` | disconnect and remove the socket |

Add `--socket PATH` on either side of the subcommand to use a non-default
socket. `MYCORD_REPL_SOCKET` also works. Daemon logs go to
`~/.cache/mycord-repl/session.log`.

## Credential

`DISCORD_TOKEN` is read from the flag, the environment, or the nearest `.env`.
Never print it, never echo it back, never paste it into a snippet — if you need
to confirm identity, print `client.user.id` instead.

## Setup

```bash
cd mycord-skill
uv sync --all-extras
cp .env.example .env      # then fill in DISCORD_TOKEN
```

## Safety rails

* Stop the session when a task ends: `mycord-repl stop`. A live daemon holds a
  real user session open.
* Sending, editing, deleting, and reacting are **visible to other humans**.
  Confirm before bulk actions.
* Do not run `stop` mid-task expecting bindings to survive — they do not; the
  next `start` is a fresh namespace.
* The daemon executes whatever Python you send it. Treat it exactly like a
  shell you already have.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `no mycord session at ...` | not started, or already stopped — run `start` |
| `LoginFailure` | token is wrong or revoked; re-extract it |
| history returns empty | forgot `await ....flatten()` |
| `AttributeError` on a channel method | you may be holding a `ForumChannel`/partial; re-fetch with `get_channel` |
| session hangs | a snippet is blocked; the default eval timeout is 30s, raise with `--timeout` |
| `timed out` | the awaited call outran its budget; split it or raise `--timeout` |

## Relationship to `mycord-mcp/`

Independent. `mycord-mcp/` exposes a FastMCP server for MCP clients;
`mycord-skill/` is this Python-library REPL. They share the upstream repo and
the `discord.py-self` dependency, but neither imports the other. Use whichever
fits the caller — and never start the MCP server just to use this skill.