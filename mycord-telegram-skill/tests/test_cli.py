"""Tests for the CLI surface: argument wiring and session-string resolution."""

import os
from pathlib import Path

import pytest

from mycord_telegram_repl.cli import EXIT_ERROR, EXIT_NO_SESSION, EXIT_OK, build_parser, main


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
def test_login_methods_parse() -> None:
    for method in ("desktop", "qr", "phone"):
        args = build_parser().parse_args(["login", "--method", method])
        assert args.method == method


@pytest.mark.unit
def test_login_defaults_to_desktop_import() -> None:
    args = build_parser().parse_args(["login"])
    assert args.method == "desktop"
    assert args.print_only is False


@pytest.mark.unit
def test_login_rejects_unknown_method() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["login", "--method", "bot"])


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
def test_reads_session_string_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_telegram_repl.cli import _load_session_string

    monkeypatch.delenv("TELEGRAM_STRING_SESSION", raising=False)
    monkeypatch.setenv("MYCORD_TELEGRAM_SESSION_FILE", "/nonexistent/session.txt")
    monkeypatch.setenv("TELEGRAM_STRING_SESSION", "from-env")
    assert _load_session_string(None) == "from-env"


@pytest.mark.unit
def test_explicit_session_file_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mycord_telegram_repl.cli import _load_session_string

    target = tmp_path / "session.txt"
    target.write_text("from-file\n")
    monkeypatch.setenv("TELEGRAM_STRING_SESSION", "from-env")
    assert _load_session_string(target) == "from-file"


@pytest.mark.unit
def test_session_file_is_trimmed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mycord_telegram_repl.cli import _load_session_string

    target = tmp_path / "session.txt"
    target.write_text("  padded-value \n\n")
    assert _load_session_string(target) == "padded-value"


@pytest.mark.unit
def test_missing_session_file_returns_none(tmp_path: Path) -> None:
    from mycord_telegram_repl.cli import _load_session_string

    assert _load_session_string(tmp_path / "absent.txt") is None


@pytest.mark.unit
def test_default_session_file_honours_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_telegram_repl.cli import default_session_file

    monkeypatch.setenv("TELEGRAM_STRING_SESSION_FILE", "/tmp/custom-session.txt")
    assert str(default_session_file()) == "/tmp/custom-session.txt"


@pytest.mark.unit
def test_default_socket_path_honours_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_telegram_repl.session import default_socket_path

    monkeypatch.setenv("MYCORD_TELEGRAM_REPL_SOCKET", "/tmp/custom-repl.sock")
    assert str(default_socket_path()) == "/tmp/custom-repl.sock"


@pytest.mark.unit
def test_default_socket_path_prefers_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_telegram_repl.session import default_socket_path

    monkeypatch.delenv("MYCORD_TELEGRAM_REPL_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert str(default_socket_path()) == "/run/user/1000/mycord-telegram-repl.sock"


@pytest.mark.unit
def test_exit_codes_are_distinct() -> None:
    assert len({EXIT_OK, EXIT_ERROR, EXIT_NO_SESSION}) == 3
    assert os.getpid() > 0
