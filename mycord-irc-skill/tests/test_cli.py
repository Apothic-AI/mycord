"""Tests for CLI parsing and offline configuration."""

from pathlib import Path

from mycord_irc_repl.cli import _config_from_args, build_parser


def test_start_parser_accepts_options_before_or_after_subcommand() -> None:
    parser = build_parser()
    before = parser.parse_args(
        ["--socket", "/tmp/mycord.sock", "start", "--server", "irc.example"]
    )
    after = parser.parse_args(
        ["start", "--socket", "/tmp/mycord.sock", "--server", "irc.example"]
    )

    assert before.socket == Path("/tmp/mycord.sock")
    assert after.socket == Path("/tmp/mycord.sock")
    assert before.server == "irc.example"


def test_start_defaults_to_tls_without_cli_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["start"])

    assert args.tls is None
    assert _config_from_args(args).tls is True


def test_start_no_connect_omits_server_and_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IRC_SERVER", "irc.example")
    monkeypatch.setenv("IRC_NICK", "agent")
    monkeypatch.setenv("IRC_SASL_USERNAME", "registered-user")
    monkeypatch.setenv("IRC_SASL_PASSWORD", "secret")
    args = build_parser().parse_args(["start", "--no-connect"])

    config = _config_from_args(args)

    assert config.server is None
    assert config.sasl_username is None
    assert config.sasl_password is None


def test_start_accepts_tls_override() -> None:
    args = build_parser().parse_args(["start", "--server", "irc.example", "--no-tls"])

    assert args.tls is False
    assert _config_from_args(args).tls is False
