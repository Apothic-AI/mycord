# mycord-email — email as a CLI, one account at a time

A thin account layer over the [`himalaya`][himalaya] mail CLI. Himalaya does the
mail: IMAP, SMTP, JMAP, Microsoft Graph, Gmail, Maildir, MIME, TLS, and one shared
query DSL. This package supplies what Himalaya deliberately leaves out —
*which* account, where its secret lives, and how to connect — and then gets out of
the way.

```bash
mycord-email envelope list --page-size 10
mycord-email --json message read 1234
mycord-email envelope search subject verification order by date desc
```

Those are unmodified himalaya subcommands. The wrapper applies an account and
passes the rest through verbatim.

## Why this exists

Himalaya keeps configuration and credentials out of the binary, which is the
right call and leaves every new mailbox as a hand-edited
`~/.config/himalaya/config.toml` plus another set of invented environment
variable names. Two failure modes follow, and both are common enough to be worth
designing away:

* **Connection shape.** Implicit TLS versus STARTTLS, which port, and whether a
  certificate must be pinned. Proton Mail Bridge, for example, needs STARTTLS on
  loopback plus a self-signed certificate trusted explicitly.
* **Secret handling.** A password written into a world-readable TOML, or
  exported into the shell for convenience, outlives the task that needed it.

`mycord-email` encodes the first as a provider table and the second as a
file split.

## Account layout

```
$MYCORD_EMAIL_HOME/accounts/apothic/     # default root: ~/.config/mycord-email
  meta.toml          # 0644 — address, provider, servers, cert path
  credentials.env    # 0600 — username and password
```

`meta.toml` is safe to print, diff, or paste into a bug report. `credentials.env`
is read per call and passed to himalaya through that child process's environment
only; it is never rendered into generated config and never exported into the
parent shell.

```bash
mycord-email accounts add work you@example.com --provider imap \
  --imap imaps://imap.fastmail.com:993 --smtp smtps://smtp.fastmail.com:465
mycord-email accounts set-secret work
mycord-email accounts list
```

## Providers

`mycord-email providers` prints the table. It is data, not code: adding a
provider is adding a row.

| Provider | Purpose |
| --- | --- |
| `imap` | Implicit TLS on 993/465 — Fastmail, Migadu, Dovecot, most hosts |
| `imap-starttls` | STARTTLS on 143/587 |
| `gmail` | Gmail over IMAP, needs an app password |
| `microsoft365` | Outlook/M365 over IMAP, app password or the msgraph backend |
| `proton-bridge` | Proton Mail via the local Bridge daemon |
| `proton-bridge-unix` | Same, non-default Bridge ports |

Proton Bridge is supported and optional. It needs three things the others do
not: a running logged-in Bridge, a pinned self-signed certificate, and the
per-Bridge password rather than the Proton account password.

```bash
openssl s_client -connect 127.0.0.1:1143 -starttls imap -showcerts </dev/null \
  2>/dev/null | openssl x509 > ~/.config/mycord-email/proton-bridge.crt
chmod 600 ~/.config/mycord-email/proton-bridge.crt

mycord-email accounts add proton you@example.com --provider proton-bridge \
  --tls-cert ~/.config/mycord-email/proton-bridge.crt --default
mycord-email accounts set-secret proton
mycord-email mailbox list
```

## Config assembly

Every call writes one fragment to `$MYCORD_EMAIL_HOME/himalaya.generated.toml`
and passes it through himalaya's multi-file `-c a:b:c` merge.

The fragment goes **last**, because himalaya lets the last file win on a key
collision. Ordering it last means a stale hand-written
`[accounts.<name>]` for the same mailbox cannot silently redirect the connection
to an old port. An existing `~/.config/himalaya/config.toml` is still merged, so
other accounts and global defaults keep working — the fragment only ever writes
`accounts.<name>.*` keys.

The generated TOML names an environment variable for the username and a
`printenv` command for the password, so no secret is ever in a file that can be
read by another user:

```toml
imap.sasl.plain.username = "${MYCORD_EMAIL_USERNAME}"
imap.sasl.plain.password.command = ["sh", "-c", "printenv MYCORD_EMAIL_PASSWORD"]
```

Inspect both halves without running mail:

```bash
mycord-email config show apothic     # the TOML himalaya reads
mycord-email exec envelope list      # the argv, merge order, env keys
```

## Extract a verification code

```bash
mycord-email envelope search subject verification order by date desc
mycord-email message read --raw 1234 | grep -oE '\b[0-9]{6}\b' | head -1
```

Two traps worth knowing. A multi-word search value stops at the first space, so
`subject verification code` is a parse error — combine clauses with `and`
instead. And `--json message read` reports `text_body` as part *offsets*, not
text, so `--raw` plus grep is the reliable path.

## Install

```bash
uv tool install mycord-email-skill     # puts `mycord-email` on PATH
```

No runtime Python dependencies: parsing, TLS, and protocol work belong to
himalaya. Requires himalaya v2 with the backends you intend to use.

No virtualenv is committed: installs replicate the skill directory, so a
checked-in venv would be duplicated per agent at 240 MB each.
`uv sync --all-extras` is only for running the tests.

To install the agent skill globally from GitHub:

```bash
npx skills add Apothic-AI/mycord --skill mycord-email-skill -g
```

Instructions for agents live in [`SKILL.md`](SKILL.md).

## Environment variables

| Variable | Effect |
| --- | --- |
| `MYCORD_EMAIL_HOME` | Config root (default `$XDG_CONFIG_HOME/mycord-email`) |
| `MYCORD_EMAIL_ACCOUNT` | Default account when `-a` is absent |
| `MYCORD_EMAIL_BINARY` | himalaya binary to run |
| `MYCORD_EMAIL_EXTRA_CONFIG` | Extra config files, `:`-separated |

## Development

```bash
make setup && make lint && make typecheck && make test
```

Live checks against a real mailbox are opt-in via `MYCORD_EMAIL_LIVE=1`; the
default suite is hermetic and touches no network.

## Relationship to the other mycord components

Independent, like the rest of the repo. It shares the repository and nothing
else: `mycord-discord-skill/` drives Discord, `mycord-telegram-skill/` drives
Telegram, `mycord-irc-skill/` drives IRC, and `gvoice/` drives Google Voice.
None import each other.

## License

MIT.

[himalaya]: https://github.com/pimalaya/himalaya
