"""Optional live checks against a real mailbox.

Skipped unless ``MYCORD_EMAIL_LIVE=1``. These exist because the hermetic suite
can prove the config is well formed but cannot prove himalaya accepts it — a
wrong TLS mode or an unparseable search value only fails against a server. Run
them after changing provider rows or the generated fragment::

    MYCORD_EMAIL_LIVE=1 uv run pytest tests/test_live.py
"""

import os

import pytest

from mycord_email.accounts import AccountError, list_accounts, load_account, read_credentials
from mycord_email.run import build_invocation

pytestmark = pytest.mark.skipif(
    os.environ.get("MYCORD_EMAIL_LIVE") != "1",
    reason="set MYCORD_EMAIL_LIVE=1 to run live mailbox checks",
)


def default_account_name() -> str:
    """Return the account to test against, honouring MYCORD_EMAIL_ACCOUNT."""
    override = os.environ.get("MYCORD_EMAIL_ACCOUNT", "").strip()
    if override:
        return override
    accounts = list_accounts()
    if not accounts:
        pytest.skip("no accounts configured")
    marked = [a for a in accounts if a.default] or accounts
    return marked[0].name


def test_account_is_usable() -> None:
    account = load_account(default_account_name())
    assert read_credentials(account)
    invocation = build_invocation(account, ["mailbox", "list"])
    assert invocation.config_path.is_file()


def test_credentials_are_private_for_every_account() -> None:
    for account in list_accounts():
        try:
            read_credentials(account)
        except AccountError as exc:
            pytest.fail(f"{account.name}: unusable credentials: {exc}")


def test_generated_fragment_parses() -> None:
    import tomllib

    account = load_account(default_account_name())
    from mycord_email.config import generate_config

    parsed = tomllib.loads(generate_config(account))
    assert account.name in parsed["accounts"]
