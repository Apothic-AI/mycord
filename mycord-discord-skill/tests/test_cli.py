"""Tests for the CLI surface: argument wiring and token resolution."""

import os
from pathlib import Path

import pytest

from mycord_repl.cli import EXIT_ERROR, EXIT_NO_SESSION, EXIT_OK, build_parser, main


@pytest.mark.unit
def test_socket_flag_accepted_before_subcommand() -> None:
    args = build_parser().parse_args(["--socket", "/tmp/x.sock", "status"])
    assert args.command == "status"
    assert args.socket == Path("/tmp/x.sock")


@pytest.mark.unit
def test_socket_flag_accepted_after_subcommand() -> None:
    args = build_parser().parse_args(["status", "--socket", "/tmp/x.sock"])
    assert args.socket == Path("/tmp/x.sock")


@pytest.mark.unit
def test_eval_code_is_optional() -> None:
    args = build_parser().parse_args(["eval", "--file", "/tmp/s.py"])
    assert args.code is None
    assert args.file == Path("/tmp/s.py")


@pytest.mark.unit
def test_start_flags_parse() -> None:
    args = build_parser().parse_args(["start", "--no-connect", "--wait", "5"])
    assert args.no_connect is True
    assert args.wait == 5.0


@pytest.mark.unit
def test_eval_without_code_or_file_fails(tmp_path: Path) -> None:
    code = main(["eval", "--socket", str(tmp_path / "absent.sock")])
    assert code == EXIT_ERROR


@pytest.mark.unit
def test_eval_without_session_reports_no_session(tmp_path: Path) -> None:
    code = main(["eval", "1 + 1", "--socket", str(tmp_path / "absent.sock")])
    assert code == EXIT_NO_SESSION


@pytest.mark.unit
def test_status_without_session_reports_no_session(tmp_path: Path) -> None:
    code = main(["status", "--socket", str(tmp_path / "absent.sock")])
    assert code == EXIT_NO_SESSION


@pytest.mark.unit
def test_stop_without_session_reports_no_session(tmp_path: Path) -> None:
    code = main(["stop", "--socket", str(tmp_path / "absent.sock")])
    assert code == EXIT_NO_SESSION


@pytest.mark.unit
def test_reads_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_repl.cli import _load_token

    monkeypatch.setenv("DISCORD_TOKEN", "env-token")
    assert _load_token(None) == "env-token"


@pytest.mark.unit
def test_explicit_token_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_repl.cli import _load_token

    monkeypatch.setenv("DISCORD_TOKEN", "env-token")
    assert _load_token("flag-token") == "flag-token"


@pytest.mark.unit
def test_token_read_from_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mycord_repl.cli import _read_dotenv_token

    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DISCORD_TOKEN=from-dotenv\n")
    assert _read_dotenv_token() == "from-dotenv"


@pytest.mark.unit
def test_no_dotenv_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mycord_repl.cli import _read_dotenv_token

    monkeypatch.chdir(tmp_path)
    assert _read_dotenv_token() is None


@pytest.mark.unit
def test_default_socket_path_honours_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_repl.session import default_socket_path

    monkeypatch.setenv("MYCORD_REPL_SOCKET", "/tmp/custom-repl.sock")
    assert str(default_socket_path()) == "/tmp/custom-repl.sock"


@pytest.mark.unit
def test_default_socket_path_prefers_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_repl.session import default_socket_path

    monkeypatch.delenv("MYCORD_REPL_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert str(default_socket_path()) == "/run/user/1000/mycord-repl.sock"


@pytest.mark.unit
def test_exit_codes_are_distinct() -> None:
    assert len({EXIT_OK, EXIT_ERROR, EXIT_NO_SESSION}) == 3
    assert os.getpid() > 0
