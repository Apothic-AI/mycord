"""mycord-email — an account-optional wrapper around the Himalaya mail CLI.

Himalaya already does the mail work: IMAP, SMTP, JMAP, Microsoft Graph, Gmail,
Maildir, plus a shared query DSL over all of them. What it does *not* do is
carry credentials or account definitions, so every new mailbox means hand-editing
``~/.config/himalaya/config.toml`` and inventing another set of environment
variable names.

This package supplies exactly that missing layer:

* **Accounts are files.** Each account is a directory under the config root with
  a ``meta.toml`` (non-secret: address, provider, servers, TLS mode) and a
  ``credentials.env`` (secret: ``0600``, loaded per call and never exported to
  child processes or printed).
* **A generator writes the fragment.** Himalaya's own config file stays the
  source of truth for *how to connect*; it is generated from account metadata so
  no agent has to remember the TLS forms for Proton Bridge, Fastmail, Gmail, and
  Microsoft 365.
* **One binary, one selection.** ``mycord-email`` assembles himalaya's
  multi-file ``-c a:b:c`` merge on every call, so ``-a work`` and ``-a personal``
  work from any directory without touching a shared file.
* **The parser stays himalaya's.** This package never parses mail. It builds
  configuration, resolves credentials, and delegates.

Nothing here is Proton-specific. Proton Mail Bridge is one provider among the
supported set, and the only one that needs its own certificate trusted.
"""

from mycord_email.accounts import (
    AccountError,
    AccountMeta,
    account_dir,
    list_accounts,
    load_account,
    read_credentials,
    save_meta,
    write_credentials,
)
from mycord_email.config import generate_config, write_config
from mycord_email.providers import PROVIDERS, Provider
from mycord_email.run import build_invocation, run

__all__ = [
    "PROVIDERS",
    "AccountError",
    "AccountMeta",
    "Provider",
    "account_dir",
    "build_invocation",
    "generate_config",
    "list_accounts",
    "load_account",
    "read_credentials",
    "run",
    "save_meta",
    "write_config",
    "write_credentials",
]

__version__ = "0.1.0"
