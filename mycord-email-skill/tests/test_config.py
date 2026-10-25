"""Tests for the generated himalaya config fragments.

The invariants that matter: no secret is ever rendered into TOML, STARTTLS and
certificate pinning survive the round trip, and asking for a bridge provider
without a certificate fails at generate time rather than at connect time.
"""

import tomllib
from pathlib import Path

import pytest

from mycord_email.accounts import AccountError, AccountMeta
from mycord_email.config import generate_config, write_config


def make_account(**overrides: object) -> AccountMeta:
    """Build a plain IMAP account, overriding fields as needed."""
    defaults: dict[str, object] = {
        "name": "work",
        "email": "work@example.com",
        "provider": "imap",
        "imap_server": "imaps://imap.example.com:993",
        "smtp_server": "smtps://smtp.example.com:465",
    }
    defaults.update(overrides)
    return AccountMeta(**defaults)  # type: ignore[arg-type]


def bridge_account(**overrides: object) -> AccountMeta:
    """Build a Proton Bridge account with the certificate pinned."""
    defaults: dict[str, object] = {
        "name": "apothic",
        "email": "tom@apothic.ai",
        "provider": "proton-bridge",
        "imap_server": "imap://127.0.0.1:1143",
        "smtp_server": "smtp://127.0.0.1:1025",
        "starttls": True,
        "tls_cert": "/home/example/.config/mycord-email/proton-bridge.crt",
        "display_name": "Tom",
        "default": True,
        "username_env": "BRIDGE_USER",
        "password_env": "BRIDGE_PASS",
        "page_size": 50,
        "aliases": {"inbox": "Inbox", "archive": "Archive"},
    }
    defaults.update(overrides)
    return AccountMeta(**defaults)  # type: ignore[arg-type]


def test_fragment_parses_as_toml() -> None:
    parsed = tomllib.loads(generate_config(make_account()))
    account = parsed["accounts"]["work"]
    assert account["email"] == "work@example.com"
    assert account["imap"]["server"] == "imaps://imap.example.com:993"
    assert account["smtp"]["server"] == "smtps://smtp.example.com:465"


def test_implicit_tls_omits_starttls() -> None:
    text = generate_config(make_account())
    assert "starttls" not in text


def test_bridge_fragment_pins_certificate_and_starttls() -> None:
    parsed = tomllib.loads(generate_config(bridge_account()))
    account = parsed["accounts"]["apothic"]
    assert account["imap"]["starttls"] is True
    assert account["smtp"]["starttls"] is True
    assert account["imap"]["tls"]["cert"] == (
        "/home/example/.config/mycord-email/proton-bridge.crt"
    )
    assert account["default"] is True


def test_password_is_a_command_not_a_literal() -> None:
    text = generate_config(bridge_account())
    assert "$BRIDGE_USER" not in text
    assert '"${BRIDGE_USER}"' in text
    assert "printenv BRIDGE_PASS" in text
    assert "password.raw" not in text


def test_no_secret_value_is_rendered() -> None:
    # Nothing in AccountMeta is secret by construction; assert the generated
    # text cannot contain a password field at all.
    text = generate_config(bridge_account())
    assert "password =" not in text


def test_unknown_provider_is_an_error() -> None:
    with pytest.raises(AccountError, match="unknown provider"):
        generate_config(make_account(provider="carrier-pigeon"))


def test_bridge_without_certificate_is_an_error() -> None:
    with pytest.raises(AccountError, match="requires a pinned TLS"):
        generate_config(bridge_account(tls_cert=None))


def test_default_override() -> None:
    assert "default = true" in generate_config(make_account(), default=True)
    assert "default = false" in generate_config(make_account(default=True), default=False)


def test_page_size_and_aliases_are_per_account() -> None:
    parsed = tomllib.loads(generate_config(bridge_account()))
    account = parsed["accounts"]["apothic"]
    assert account["envelope"]["list"]["page-size"] == 50
    assert account["mailbox"]["alias"]["inbox"] == "Inbox"
    assert account["mailbox"]["alias"]["archive"] == "Archive"


def test_display_name_rendered_only_when_set() -> None:
    assert "display-name" not in generate_config(make_account())
    assert 'display-name = "Tom"' in generate_config(bridge_account())


def test_write_config_marks_exactly_one_default(tmp_path: Path) -> None:
    path = tmp_path / "merged.toml"
    write_config(
        [make_account(name="a"), make_account(name="b", default=True)],
        path,
        default_name="b",
    )
    parsed = tomllib.loads(path.read_text())
    assert parsed["accounts"]["a"]["default"] is False
    assert parsed["accounts"]["b"]["default"] is True


def test_write_config_defaults_to_first(tmp_path: Path) -> None:
    path = tmp_path / "merged.toml"
    write_config([make_account(name="a"), make_account(name="b")], path)
    parsed = tomllib.loads(path.read_text())
    assert parsed["accounts"]["a"]["default"] is True
    assert parsed["accounts"]["b"]["default"] is False


def test_write_config_rejects_unknown_default(tmp_path: Path) -> None:
    with pytest.raises(AccountError, match="not among"):
        write_config([make_account(name="a")], tmp_path / "x.toml", default_name="zzz")


def test_write_config_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(AccountError, match="no accounts"):
        write_config([], tmp_path / "x.toml")


def test_write_config_is_private(tmp_path: Path) -> None:
    # The fragment names env vars rather than values, but it still describes a
    # mailbox; keep it 0600 out of habit, matching credentials handling.
    path = write_config([make_account()], tmp_path / "merged.toml")
    assert (path.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("provider", ["gmail", "microsoft365", "imap", "imap-starttls"])
def test_non_bridge_providers_need_no_certificate(provider: str) -> None:
    from mycord_email.providers import get_provider

    template = get_provider(provider)
    account = make_account(
        provider=provider,
        imap_server=template.imap_server,
        smtp_server=template.smtp_server,
        starttls=template.starttls,
    )
    text = generate_config(account)
    assert "tls.cert" not in text
