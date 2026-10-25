# mycord-slack-skill

Slack from a shell, as the signed-in human, with **no Slack app registration** — a
passthrough to the whole API plus slackdump-backed reads.

Instructions for agents live in [`SKILL.md`](SKILL.md). This file is for whoever has to
install, debug, or maintain it.

> **Unofficial.** Slack does not support using `xoxc-` client tokens this way, and a
> workspace admin can restrict them or require app approval at any time. This drives a
> real account as that account.

---

## Why this shape

Slack's web client authenticates with two things:

| Piece | Where it lives | What it is |
| --- | --- | --- |
| `xoxc-…` | `localStorage.localConfig_v2`, per workspace | the client token |
| `d` (`xoxd-…`) | cookie on `.slack.com` | an httpOnly **session** cookie |

Together they operate exactly like the browser session they came from, with **no OAuth
scope limits** — so the account's own permissions are the boundary. That is why the CLI
is a passthrough (`mycord-slack <method> --json '{...}'`) and not a curated verb list: a
fixed verb list would re-impose exactly the limits this approach avoids.

Two tools, because no single one covers both halves:

* **`slackdump`** reads. It speaks the client-token protocol properly, paginates,
  respects rate limits, caches users/channels, and handles DMs and threads. It has **no
  send capability at all** — all 16 subcommands were checked.
* **`slack_sdk`** writes and does everything else: `WebClient(token=xoxc, headers={"Cookie": "d=<xoxd>"})`.

```text
                    ┌────────────────────────────┐
Chrome profile ──►  │ login (CDP)                │
  cookie jar        │  Network.getAllCookies → d  │
  localStorage      │  localConfig_v2      → xoxc │
                    └──────────┬─────────────────┘
                               │
              session.json (0600)  ──►  slackdump workspace import
                     │                          │
                     ▼                          ▼
        slack_sdk.WebClient          slackdump read verbs
        (passthrough, any method)    channels / read / search / users
```

## Install

```bash
cd mycord-slack-skill
uv sync --all-extras
```

Prerequisites, both external:

```bash
# 1. slackdump v3 (the read half)
GOBIN=$HOME/.local/bin go install github.com/rusq/slackdump/v3/cmd/slackdump@latest
slackdump version     # prints "unknown" for an @latest build with no VCS stamp

# 2. a Chrome profile signed in to Slack, with the web client loaded at least once
sqlite3 ~/.config/google-chrome/Default/Cookies \
  "select host_key, name from cookies where name='d' and host_key like '%slack%';"
# → .slack.com|d
```

Then:

```bash
uv run mycord-slack login        # capture + import
uv run mycord-slack session --check
```

`login` copies the profile rather than writing to it, runs Chrome headless against the
copy, and waits for both halves of the session before capturing. It writes
`~/.local/share/mycord-slack/session.json` (0600 in a 0700 directory) and imports a
short-lived env file into slackdump's encrypted credential store.

## Use

```bash
# passthrough — any Slack API method
uv run mycord-slack auth.test
uv run mycord-slack conversations.list --json '{"limit":5}'
uv run mycord-slack chat.postMessage --json '{"channel":"C0123","text":"hi"}'

# reads (slackdump)
uv run mycord-slack channels
uv run mycord-slack read '#general' --limit 20
uv run mycord-slack search "deploy" --via api
```

Decisions worth knowing before changing this code:

* **Payloads go as form fields (`params`), not a JSON body.** `api_call(..., json={...})`
  sends JSON, which some methods accept and others reject (`search.messages` →
  `invalid_arguments: missing required field: query`). Form encoding works for both, and
  slack_sdk serialises nested values, so `blocks` works either as a structure or as a
  JSON-encoded string.
* **No retries, no error translation.** `ratelimited` and `invalid_auth` are surfaced
  verbatim. A hidden retry loop makes a throttled batch look like a slow one, and
  translating `admin_unauthorized` into "bad token" is how a role limit gets
  misdiagnosed.
* **`channels`/`read`/`search` shell out to slackdump; the passthrough does not.** Keep
  it that way — slackdump's caches are better than anything written from scratch, and the
  passthrough needs to stay a thin, honest call into Slack.
* **A success in `login` means the credentials were accepted by Slack**, because
  `slackdump workspace import` validates before saving. There is no separate probe.
* **The `d` cookie expiry is only a lower bound.** Sessions are revoked independently of
  it, so `session --check` (`auth.test`) is the real validity test.

## Layout

```text
mycord-slack-skill/
├── src/mycord_slack/
│   ├── cli.py       # verbs, passthrough routing, exit codes
│   ├── cdp.py       # stdlib WebSocket + profile copy; cookie jar and localStorage capture
│   ├── session.py   # session.json (0600, atomic), slackdump import
│   ├── api.py       # the passthrough: slack_sdk WebClient, verbatim errors
│   ├── reads.py     # slackdump wrappers: channels, dump, search, user list
│   ├── config.py    # state dir, binary lookup, env names
│   └── redact.py    # token/cookie redaction, non-reversible fingerprints
├── tests/           # offline suite + a few `live` tests
├── SKILL.md         # agent-facing
└── README.md        # this file
```

`cdp.py` is adapted from [`google-voice-skill/src/gvoice_skill/cdp.py`](../google-voice-skill/src/gvoice_skill/cdp.py):
the minimal RFC 6455 client and the profile-copy trick are the same problem solved once.
What differs is what gets read — cookies *and* the web client's `localStorage`, since
Slack splits the session across both.

## Test

```bash
uv run ruff check src/ tests/           # lint
uv run mypy src/                        # strict
uv run pytest -m "not live"             # offline: no credentials, no network
uv run pytest -m live                   # needs a real session; skips without one
```

The offline suite mocks the transport, so it runs anywhere. The `live` tests are
read-only except one opt-in post to a channel you name, and they skip (not fail) when no
session exists or the session has died — an expired cookie is not a code regression.

## Security

* `session.json` holds the token **and** the session cookie: the whole account. 0600,
  0700 directory, gitignored, never printed. A group- or world-readable file is a hard
  error, not a warning.
* Every subprocess failure and API error body is passed through `redact.py` before it is
  printed, so slackdump echoing the file it read cannot leak the token.
* **There are two credential stores**, not one: `session.json` (ours) and slackdump's
  encrypted workspace file. `logout` clears both (`--keep-slackdump` keeps the second),
  and `channels`/`read`/`users` keep working while only the second is present.
* `login` never writes to the real Chrome profile. `--no-copy-profile` exists, is
  documented as requiring Chrome to be closed, and is not the default.
* Writes are attributed to the human. Prefer a test channel you own for the first post.

## License

MIT.
