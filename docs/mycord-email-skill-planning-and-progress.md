# mycord-email-skill — planning and progress

**Last Updated:** 2026-10-25
**Status:** COMPLETE — shipped, installed, and verified against a live mailbox.

Scope: add `mycord-email-skill/` to this repo and reorganise the two
system-installed email skills around it.

---

## Why the component exists

`himalaya` is the mail engine: IMAP, SMTP, JMAP, Microsoft Graph, Gmail,
Maildir, MIME, TLS, and one shared search DSL. It deliberately keeps
configuration and credentials out of the binary. That is the right boundary, and
it leaves two recurring failure modes unaddressed by himalaya itself:

1. **Connection shape.** Implicit TLS vs STARTTLS, which port, and whether a
   certificate must be pinned. Proton Bridge needs STARTTLS on loopback plus an
   explicitly trusted self-signed certificate; getting that wrong produces an
   error indistinguishable from a bad password.
2. **Secret handling.** A password written into a world-readable TOML, or
   exported into the shell for convenience, outlives the task that needed it.

The component answers exactly those two questions and nothing else.

## Design decisions

**Passthrough, not a second dialect.** `mycord-email <verb> ...` applies an
account and hands the arguments to himalaya verbatim. Wrapping `envelope`,
`message`, or `flag` in our own verbs would create a parallel CLI to keep in
sync, and himalaya's CLI is what documentation and muscle memory already target.
The router therefore keys off the first command token, and anything that is not
one of our four subcommands is forwarded.

**Accounts are directories with a deliberate file split.**

```
accounts/<name>/meta.toml        0644  non-secret, safe to print/diff/paste
accounts/<name>/credentials.env  0600  secret, read per call
```

`meta.toml` carries address, provider, servers, and cert path. `credentials.env`
is never rendered into generated config and never exported into the parent
shell — only into the child process running himalaya. Insecure permissions are a
hard error rather than a warning, because a silently-used 0644 secret is worse
than a failed command.

**Provider is a data table.** `providers.py` holds one row per family. Adding a
provider is adding a row; there is no per-provider code path. The generated TOML
names an environment variable for the username and a `printenv` command for the
password, so no secret can appear in a file another user can read.

**Generated fragment merges LAST.** See "Defects found" below — this was wrong
on the first pass and the fix is load-bearing.

## Defects found during implementation

Both were found by running against a live mailbox, not by the hermetic tests,
and both now have regression tests.

1. **Config merge order inverted the precedence.** himalaya lets the *last* file
   in a `-c a:b:c` list win on a key collision. The first implementation put the
   generated fragment first, so a stale hand-written
   `[accounts.apothic]` in `~/.config/himalaya/config.toml` silently redirected
   the connection to a dead port. Demonstrated by pointing the operator config's
   account at port 19999 and watching the wrapper connect there. Fixed by
   ordering the fragment last; the operator config is still merged, so their own
   accounts and global defaults keep working, because the fragment only ever
   writes `accounts.<name>.*` keys.

2. **himalaya's own leading global flags broke routing.** `mycord-email --json
   envelope list` was rejected with `invalid choice: 'envelope'`, because the
   router treated any leading `-` token as a wrapper flag. `--json` is essential
   for machine-readable output, so this made the most common agent usage
   impossible. Fixed by stripping only *our* known global flags and then deciding
   on the first command token.

A third, smaller trap is documented rather than fixed, because it is himalaya's
semantics: a multi-word search value stops at the first space, so
`subject verification code` is a parse error. The skill documents the `and`
form.

## Verification

Hermetic suite (no network): 79 tests. Covers name validation against path
escape, secret/env round-tripping, certificate-required providers failing at
generate time, exactly-one-default rendering, router decisions including
himalaya's global flags, and that no secret value can reach `meta.toml` or the
generated TOML.

Live suite (`MYCORD_EMAIL_LIVE=1`): mailbox listing, search with the shared DSL,
`message read`, `--json`, provider table, and the certificate re-export command
from the docs — all run against the real Proton Bridge.

Gates: `ruff check`, `ruff format --check`, `mypy --strict` (7 modules), pytest.

## Install shape

One canonical copy, at `~/.agents/skills/mycord-email-skill`. That is the skill
store every agent on this machine reads (pi's primary root; the others symlink
into it — `mycord-telegram-skill` and `mycord-discord-skill` are installed the
same way).

`npx skills add ... --copy` was tried first and was wrong on two counts: it
placed real directory copies into seven agent paths, and each copy took the
whole component with it. With a local `.venv` present that was 240 MB × 7. It
was removed with `npx skills remove --global` and reinstalled by hand into the
one root. Two rules follow:

* **No committed virtualenv.** Whatever the install method, the directory is
  replicated, so the venv stays out of the repo. `uv tool install
  mycord-email-skill` puts the binary on PATH; `uv sync` is only for tests.
* **`--copy` is the wrong flag here.** Plain `npx skills add` symlinks agent
directories back to the canonical store, so one edit updates every agent. Copies
drift and have to be manually re-synced.

## System skill reorganisation

* `~/.agents/skills/himalaya-email` → renamed `apothic-ai-email`. Same
  instruction: it was always an Apothic-mailbox runbook, not a himalaya runbook.
* `apothic-ai-email` slimmed to account-specific facts only — account name,
  address, provider, cert path, the Bridge prerequisite, and the mailbox's
  capitalised folder names — and now delegates all mechanism to
  `mycord-email-skill`.
* Two other skills referenced the old name or the old env vars
  (`flyio-account-migration`, `add-google-account-to-android-device`) and were
  updated to point at the new skill and the new command.

## Follow-ups, deliberately not done

* `~/.config/himalaya/config.toml` and the `APOTHIC_PROTONMAIL_BRIDGE_*`
  exports in `~/.bashrc` are untouched. They still work, they cost nothing, and
  removing them is the operator's call once they have stopped using the old
  path.
* The provider table has no OAuth flow for Microsoft 365; the row documents the
  msgraph fallback instead. Adding OAuth is a separate piece of work with a
  browser component.
