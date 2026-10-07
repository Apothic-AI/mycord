"""Tests for IRC configuration handling."""

from pathlib import Path

import pytest

from mycord_irc_repl.config import IRCConfig, IRCConfigurationError, load_config


def test_defaults_use_tls_and_conventional_port(tmp_path: Path) -> None:
    config = load_config(env={}, cwd=tmp_path)

    assert config.tls is True
    assert config.effective_port == 6697
    assert config.nickname == "mycord-agent"


def test_plain_irc_uses_conventional_port() -> None:
    config = load_config(env={"IRC_TLS": "false"})

    assert config.tls is False
    assert config.effective_port == 6667


def test_environment_overrides_dotenv_and_cli_overrides_environment(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("IRC_SERVER=dotenv.example\nIRC_NICK=dotenv-bot\n")

    config = load_config(
        cwd=tmp_path,
        env={"IRC_SERVER": "environment.example", "IRC_NICK": "environment-bot"},
        overrides={"server": "cli.example"},
    )

    assert config.server == "cli.example"
    assert config.nickname == "environment-bot"


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_parse_truthy_tls_values(value: str) -> None:
    assert load_config(env={"IRC_TLS": value}).tls is True


@pytest.mark.parametrize("value", ["0", "false", "NO", "off"])
def test_parse_false_tls_values(value: str) -> None:
    assert load_config(env={"IRC_TLS": value}).tls is False


def test_rejects_invalid_port() -> None:
    with pytest.raises(IRCConfigurationError, match="IRC_PORT must be an integer"):
        load_config(env={"IRC_PORT": "not-a-port"})


@pytest.mark.parametrize("port", [0, 65536])
def test_rejects_out_of_range_port(port: int) -> None:
    config = IRCConfig(server="irc.example", nickname="agent", port=port)

    with pytest.raises(IRCConfigurationError, match="between 1 and 65535"):
        config.validate_for_connect()


def test_requires_server_and_nickname_to_connect() -> None:
    with pytest.raises(IRCConfigurationError, match="IRC_SERVER"):
        IRCConfig(nickname="agent").validate_for_connect()

    with pytest.raises(IRCConfigurationError, match="IRC_NICK"):
        IRCConfig(server="irc.example").validate_for_connect()


def test_rejects_partial_sasl_credentials() -> None:
    config = IRCConfig(
        server="irc.example",
        nickname="agent",
        sasl_username="registered-user",
    )

    with pytest.raises(IRCConfigurationError, match="both IRC_SASL_USERNAME"):
        config.validate_for_connect()


def test_refuses_to_send_password_without_tls() -> None:
    config = IRCConfig(
        server="irc.example",
        nickname="agent",
        tls=False,
        server_password="secret",
    )

    with pytest.raises(IRCConfigurationError, match="without TLS"):
        config.validate_for_connect()
