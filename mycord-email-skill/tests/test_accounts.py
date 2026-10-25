"""Tests for account storage and metadata round-tripping.

These are the contracts the skill depends on: names cannot escape the config
root, secrets never live in `meta.toml`, and a credential file that is readable
by other users is an error rather than a silent success.
"""

import os
import stat
from pathlib import Path

import pytest

from mycord_email.accounts import (
    CREDENTIALS_FILENAME,
    META_FILENAME,
    AccountError,
    AccountMeta,
    account_dir,
    accounts_root,
    config_home,
    list_accounts,
    load_account,
    read_credentials,
    save_meta,
    write_credentials,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point MYCORD_EMAIL_HOME at an isolated directory."""
    monkeypatch.setenv("MYCORD_EMAIL_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


def make_account(name: str = "work", **overrides: object) -> AccountMeta:
    """Build a valid account, overriding fields as needed."""
    defaults: dict[str, object] = {
        "name": name,
        "email": f"{name}@example.com",
        "provider": "imap",
        "imap_server": "imaps://imap.example.com:993",
        "smtp_server": "smtps://smtp.example.com:465",
    }
    defaults.update(overrides)
    return AccountMeta(**defaults)  # type: ignore[arg-type]


def test_config_home_honours_env(home: Path) -> None:
    assert config_home() == home


def test_config_home_defaults_to_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MYCORD_EMAIL_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_home() == tmp_path / "xdg" / "mycord-email"


@pytest.mark.parametrize(
    "bad",
    ["../escape", "with/slash", "Uppercase", "", "space name", ".hidden", "-lead"],
)
def test_account_dir_rejects_unsafe_names(home: Path, bad: str) -> None:
    with pytest.raises(AccountError):
        account_dir(bad)


def test_account_dir_accepts_safe_names(home: Path) -> None:
    assert account_dir("work.2-x_y") == accounts_root() / "work.2-x_y"


def test_meta_round_trip(home: Path) -> None:
    account = make_account(
        display_name="Work Mail",
        starttls=True,
        tls_cert="/etc/ssl/bridge.crt",
        page_size=50,
        aliases={"inbox": "Inbox", "archive": "Archive"},
        default=True,
        notes="hand-written note",
    )
    path = save_meta(account)
    assert path.name == META_FILENAME

    loaded = load_account("work")
    assert loaded == account


def test_meta_omits_optional_keys(home: Path) -> None:
    save_meta(make_account())
    text = (accounts_root() / "work" / META_FILENAME).read_text()
    assert "tls-cert" not in text
    assert "starttls = false" in text


def test_meta_rejects_missing_required_key(home: Path) -> None:
    directory = accounts_root() / "broken"
    directory.mkdir(parents=True)
    (directory / META_FILENAME).write_text('email = "x@y.z"\n')
    with pytest.raises(AccountError, match="provider"):
        load_account("broken")


def test_load_missing_account_names_the_command(home: Path) -> None:
    with pytest.raises(AccountError, match="accounts add"):
        load_account("nope")


def test_list_accounts_skips_directories_without_meta(home: Path) -> None:
    save_meta(make_account("a"))
    save_meta(make_account("b"))
    (accounts_root() / "stray").mkdir(parents=True)
    assert [a.name for a in list_accounts()] == ["a", "b"]


def test_list_accounts_empty_when_root_absent(home: Path) -> None:
    assert list_accounts() == []


def test_write_and_read_credentials(home: Path) -> None:
    account = make_account()
    save_meta(account)
    write_credentials(account, "user@example.com", "s3cr3t value")

    path = account.credentials_path
    assert path.name == CREDENTIALS_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    creds = read_credentials(account)
    assert creds["MYCORD_EMAIL_USERNAME"] == "user@example.com"
    assert creds["MYCORD_EMAIL_PASSWORD"] == "s3cr3t value"


def test_credentials_honour_custom_env_names(home: Path) -> None:
    account = make_account(username_env="BRIDGE_USER", password_env="BRIDGE_PASS")
    save_meta(account)
    write_credentials(account, "bridge", "pw")

    creds = read_credentials(account)
    assert creds == {"BRIDGE_USER": "bridge", "BRIDGE_PASS": "pw"}


def test_write_credentials_is_atomic_about_permissions(home: Path) -> None:
    account = make_account()
    save_meta(account)
    write_credentials(account, "u", "p")
    # Rewriting must not widen the mode.
    write_credentials(account, "u", "p2")
    assert stat.S_IMODE(account.credentials_path.stat().st_mode) == 0o600


def test_read_credentials_rejects_loose_permissions(home: Path) -> None:
    account = make_account()
    save_meta(account)
    write_credentials(account, "u", "p")
    os.chmod(account.credentials_path, 0o644)

    with pytest.raises(AccountError, match="chmod 600"):
        read_credentials(account)


def test_read_credentials_missing(home: Path) -> None:
    account = make_account()
    save_meta(account)
    with pytest.raises(AccountError, match="set-secret"):
        read_credentials(account)


def test_read_credentials_parses_comments_and_quotes(home: Path) -> None:
    account = make_account()
    save_meta(account)
    account.credentials_path.write_text(
        '# comment\n\nUSER=plain\nPASS="has space and \\"quote\\""\n'
    )
    account.credentials_path.chmod(0o600)
    creds = read_credentials(account)
    assert creds["USER"] == "plain"
    assert creds["PASS"] == 'has space and "quote"'


def test_read_credentials_rejects_malformed_line(home: Path) -> None:
    account = make_account()
    save_meta(account)
    account.credentials_path.write_text("NOT_A_PAIR\n")
    account.credentials_path.chmod(0o600)
    with pytest.raises(AccountError, match="KEY=VALUE"):
        read_credentials(account)


def test_secret_never_lands_in_meta(home: Path) -> None:
    account = make_account()
    save_meta(account)
    write_credentials(account, "user@example.com", "topsecretpw")
    text = (accounts_root() / "work" / META_FILENAME).read_text()
    assert "topsecretpw" not in text
