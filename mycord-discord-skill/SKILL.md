---
name: mycord-discord-skill
description: Drive a real Discord user account from Python using discord.py-self through a persistent REPL-style session (mycord-repl). Use when asked to read, send, edit, react to, search, or monitor Discord messages, channels, guilds, DMs, or members - as a library. Relies on selfbot automation, which violates Discord's ToS and can get an account banned.
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

For **large** channel or DM histories destined for disk, do not page through
`history()` by hand — use the optional
[DiscordChatExporter](#bulk-export-to-disk-discordchatexporter) dependency
instead.

---

## The loop

```bash
cd mycord-discord-skill
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
msgs = [m async for m in channel.history(limit=20)]
[(m.id, m.author, m.content) for m in msgs]
"
```

`history()` is an async iterator in current `discord.py-self` releases. Materialise
it with `[m async for m in channel.history(limit=N)]`; do not use the removed
`.flatten()`/`.get()` helpers. Use a bounded `limit` for interactive reads.

### Search backwards from a known point

```bash
uv run mycord-repl eval "\
channel = client.get_channel(CHANNEL_ID)
msgs = [m async for m in channel.history(limit=500)]
[m.content for m in msgs if 'deploy' in m.content.lower()]
"
```

State persists, so fetch once and then filter repeatedly:

```bash
uv run mycord-repl eval "msgs = [m async for m in channel.history(limit=500)]"
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
# Short wait: the CLI request can stay open when the timeout fits the eval budget.
uv run mycord-repl eval --timeout 40 "m = await client.wait_for('message', timeout=30); (m.author, m.content)"
```

For a long-lived wait, do not hold an `eval` request open. The daemon's default
eval timeout is 30 seconds even if `client.wait_for()` has a longer timeout.
Start a background task, then poll it from later evals:

```bash
uv run mycord-repl eval "pending = asyncio.create_task(client.wait_for('message', check=lambda m: m.author.id == USER_ID))"
uv run mycord-repl eval "(pending.done(), pending.result().content if pending.done() else None)"
```

The `asyncio` module is preloaded in the REPL namespace. Cancel a pending task
when the wait is no longer needed: `uv run mycord-repl eval "pending.cancel()"`.

### Read a DM

```bash
uv run mycord-repl eval "\
dm = next(c for c in client.private_channels if getattr(c, 'recipient', None) is not None and c.recipient.id == USER_ID)
msgs = [m async for m in dm.history(limit=10)]
[(m.author, m.content) for m in msgs]
"
```

`client.private_channels` also contains group DMs, which have `recipients` and
no `.recipient`; always guard the attribute when selecting a one-to-one DM.

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
* When reading history, use `[m async for m in channel.history(limit=N)]` and
  bind the result before filtering it repeatedly.
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
cd mycord-discord-skill
uv sync --all-extras
cp .env.example .env      # then fill in DISCORD_TOKEN
```

---

## Bulk export to disk (DiscordChatExporter)

**If you are asked to save a large channel or DM history to disk, do not page
through `history()` in a loop.** Install and use the
[DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter) CLI
instead. It is much faster, respects rate limits, preserves attachments, and
reads the same `DISCORD_TOKEN` you already have.

Split of responsibilities: **the REPL is for reading and reasoning, the exporter
is for persistence.** Export first, then analyse the file.

> Still a user token, so still ToS-risky — same exposure as the selfbot, no
> better. The upside is only that it is the supported API surface and does not
> trip the gateway path that bans come from.

### Install (optional dependency)

The asset name embeds the platform; check the releases page for other targets.

```bash
cd /tmp
curl -fsSL -o dce.zip \
  https://github.com/Tyrrrz/DiscordChatExporter/releases/latest/download/DiscordChatExporter.Cli.linux-x64.zip
mkdir -p ~/.local/opt/discordchatexporter ~/.local/bin
unzip -oq dce.zip -d ~/.local/opt/discordchatexporter
ln -sf ~/.local/opt/discordchatexporter/DiscordChatExporter.Cli ~/.local/bin/discordchatexporter
chmod +x ~/.local/opt/discordchatexporter/DiscordChatExporter.Cli
hash -r && discordchatexporter --version
```

Skip whatever already exists and is current — check `--version` against the
releases page before reinstalling. If `~/.local/bin` is not on `PATH`, put the
symlink in a directory that is.

### Export

`--token` falls back to the `DISCORD_TOKEN` environment variable, so load it from
`.env` instead of passing `-t`. A flag would leak the token into shell history
and `ps` output.

```bash
cd mycord-discord-skill && set -a && . ./.env && set +a

discordchatexporter export      -c CHANNEL_ID  -o out/ -f Json     # one channel
discordchatexporter exportguild --guild GUILD_ID -o out/ -f HtmlDark # whole server
discordchatexporter exportdm    -o out/ -f Json                      # all DMs
discordchatexporter exportall   -o out/ -f Csv                       # everything
```

| Flag | Notes |
| --- | --- |
| `-o` | defaults to the **current directory**; a directory path must end in `/` or it is read as a filename |
| `-f` | `PlainText`, `HtmlDark` (default), `HtmlLight`, `Csv`, `Json` |
| `--after` / `--before` | accepts a date *or* a message ID |
| `--media` | downloads attachments, avatars, embeds; add `--reuse-media` to skip re-fetching |
| `-p` | partition output, e.g. `-p 1000` or `-p 10mb` |
| `--include-threads` | `None` (default), `Active`, `All` |
| `--respect-rate-limits` | on by default; leave it on for bulk jobs |

Do not set `DISCORD_TOKEN_BOT` — `-b|--bot` is a backwards-compat no-op, and it
can shadow the user token you want.

Prefer `Json` when you intend to analyse or re-query the data; `HtmlDark` when a
human is going to read it.

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
| history returns empty | use `[m async for m in channel.history(limit=N)]`; current releases do not provide `.flatten()` |
| `AttributeError` on a channel method | you may be holding a `ForumChannel`/partial; re-fetch with `get_channel` |
| session hangs | a snippet is blocked; the default eval timeout is 30s, raise with `--timeout` |
| `timed out` | the awaited call outran its budget; split it or raise `--timeout` |
