"""Unit tests for the authentication helpers.

These cover parsing and error paths only. None of them contacts Telegram, and
none requires a real account, credentials, or network access.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from mycord_telegram_repl.auth import (
    API_HASH_ENV,
    API_ID_ENV,
    STRING_SESSION_ENV,
    AuthError,
    find_desktop_client,
    load_credentials,
    load_string_session,
    require_tdl,
    save_session_string,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test from whatever the developer has exported."""
    for name in (API_ID_ENV, API_HASH_ENV, STRING_SESSION_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.unit
def test_credentials_parse_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_ID_ENV, "12345")
    monkeypatch.setenv(API_HASH_ENV, "deadbeef")
    assert load_credentials() == (12345, "deadbeef")


@pytest.mark.unit
def test_missing_both_names_both_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AuthError, match=API_ID_ENV) as excinfo:
        load_credentials()
    assert API_HASH_ENV in str(excinfo.value)


@pytest.mark.unit
def test_missing_hash_only_names_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_ID_ENV, "12345")
    with pytest.raises(AuthError, match=API_HASH_ENV):
        load_credentials()


@pytest.mark.unit
def test_non_integer_api_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_ID_ENV, "not-a-number")
    monkeypatch.setenv(API_HASH_ENV, "deadbeef")
    with pytest.raises(AuthError, match="must be an integer"):
        load_credentials()


@pytest.mark.unit
def test_whitespace_only_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_ID_ENV, "   ")
    monkeypatch.setenv(API_HASH_ENV, "deadbeef")
    with pytest.raises(AuthError):
        load_credentials()


@pytest.mark.unit
def test_error_points_at_my_telegram_org(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AuthError, match=r"my\.telegram\.org"):
        load_credentials()


@pytest.mark.unit
def test_string_session_absent_returns_none() -> None:
    assert load_string_session() is None


@pytest.mark.unit
def test_string_session_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STRING_SESSION_ENV, "  session-value  ")
    assert load_string_session() == "session-value"


@pytest.mark.unit
def test_string_session_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STRING_SESSION_ENV, "   ")
    assert load_string_session() is None


@pytest.mark.unit
def test_no_desktop_client_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_telegram_repl.auth.DESKTOP_SEARCH_PATHS", ())
    assert find_desktop_client() is None


@pytest.mark.unit
def test_desktop_client_found_when_tdata_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = tmp_path / "TelegramDesktop"
    (candidate / "tdata").mkdir(parents=True)
    monkeypatch.setattr("mycord_telegram_repl.auth.DESKTOP_SEARCH_PATHS", (candidate,))
    assert find_desktop_client() == candidate


@pytest.mark.unit
def test_desktop_path_without_tdata_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory that exists but has no tdata/ must not be selected.
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "tdata").mkdir()
    monkeypatch.setattr("mycord_telegram_repl.auth.DESKTOP_SEARCH_PATHS", (empty,))
    assert find_desktop_client() == empty

    no_tdata = tmp_path / "not-desktop"
    no_tdata.mkdir()
    monkeypatch.setattr("mycord_telegram_repl.auth.DESKTOP_SEARCH_PATHS", (no_tdata,))
    assert find_desktop_client() is None


@pytest.mark.unit
def test_require_tdl_reports_install_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_telegram_repl.auth.shutil.which", lambda _: None)
    with pytest.raises(AuthError, match="iyear/tdl"):
        require_tdl()


@pytest.mark.unit
def test_require_tdl_returns_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_telegram_repl.auth.shutil.which", lambda _: "/usr/bin/tdl")
    assert require_tdl() == "/usr/bin/tdl"


@pytest.mark.unit
def test_import_requires_desktop_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mycord_telegram_repl.auth import import_desktop_session

    monkeypatch.setattr("mycord_telegram_repl.auth.require_tdl", lambda: "/usr/bin/tdl")
    monkeypatch.setattr("mycord_telegram_repl.auth.DESKTOP_SEARCH_PATHS", ())
    with pytest.raises(AuthError, match="no Telegram Desktop"):
        import_desktop_session(tmp_path / "storage")


@pytest.mark.unit
def test_import_rejects_path_without_tdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mycord_telegram_repl.auth import import_desktop_session

    monkeypatch.setattr("mycord_telegram_repl.auth.require_tdl", lambda: "/usr/bin/tdl")
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    with pytest.raises(AuthError, match="no tdata"):
        import_desktop_session(tmp_path / "storage", desktop_path=bogus)


@pytest.mark.unit
def test_import_never_passes_logout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The user's Telegram Desktop login must survive an import."""
    monkeypatch.setattr("mycord_telegram_repl.auth.require_tdl", lambda: "/usr/bin/tdl")
    desktop = tmp_path / "TelegramDesktop"
    (desktop / "tdata").mkdir(parents=True)

    captured: dict[str, Any] = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: Any) -> Result:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("mycord_telegram_repl.auth.subprocess.run", fake_run)

    from mycord_telegram_repl.auth import import_desktop_session

    storage = import_desktop_session(tmp_path / "storage", desktop_path=desktop)

    assert "--logout" not in captured["cmd"]
    assert storage == tmp_path / "storage"
    # subprocess.DEVNULL is the int sentinel -3, not an enum member.
    import subprocess as sp

    assert captured["kwargs"]["stdin"] == sp.DEVNULL


@pytest.mark.unit
def test_import_explains_tui_eof_instead_of_dumping_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tdl's picker is a TUI; with stdin closed it exits on EOF, not a real fault."""
    monkeypatch.setattr("mycord_telegram_repl.auth.require_tdl", lambda: "/usr/bin/tdl")
    desktop = tmp_path / "TelegramDesktop"
    (desktop / "tdata").mkdir(parents=True)

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Choose a user id:\nError: EOF"

    monkeypatch.setattr("mycord_telegram_repl.auth.subprocess.run", lambda cmd, **kw: Result())

    from mycord_telegram_repl.auth import import_desktop_session

    with pytest.raises(AuthError) as excinfo:
        import_desktop_session(tmp_path / "storage", desktop_path=desktop)

    message = str(excinfo.value)
    assert "interactive" in message
    assert "Answer N" in message
    assert str(desktop) in message


@pytest.mark.unit
def test_import_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("mycord_telegram_repl.auth.require_tdl", lambda: "/usr/bin/tdl")
    desktop = tmp_path / "TelegramDesktop"
    (desktop / "tdata").mkdir(parents=True)

    class Result:
        returncode = 3
        stdout = ""
        stderr = "no accounts found"

    monkeypatch.setattr("mycord_telegram_repl.auth.subprocess.run", lambda cmd, **kw: Result())

    from mycord_telegram_repl.auth import import_desktop_session

    with pytest.raises(AuthError, match="no accounts found"):
        import_desktop_session(tmp_path / "storage", desktop_path=desktop)


@pytest.mark.unit
def test_import_timeout_explains_interactive_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess as sp

    monkeypatch.setattr("mycord_telegram_repl.auth.require_tdl", lambda: "/usr/bin/tdl")
    desktop = tmp_path / "TelegramDesktop"
    (desktop / "tdata").mkdir(parents=True)

    def timeout(cmd: list[str], **kw: Any) -> None:
        raise sp.TimeoutExpired(cmd, 120)

    monkeypatch.setattr("mycord_telegram_repl.auth.subprocess.run", timeout)

    from mycord_telegram_repl.auth import import_desktop_session

    with pytest.raises(AuthError, match="interactively"):
        import_desktop_session(tmp_path / "storage", desktop_path=desktop)


@pytest.mark.unit
async def test_qr_login_returns_existing_session_when_authorized() -> None:
    """An already-authorized client yields its session without prompting."""

    class FakeSession:
        def save(self) -> str:
            return "existing-session"

    class FakeClient:
        session = FakeSession()

        async def connect(self) -> None:
            return None

        async def is_user_authorized(self) -> bool:
            return True

    from mycord_telegram_repl.auth import qr_login

    assert os.environ.get(STRING_SESSION_ENV) is None
    assert await qr_login(FakeClient()) == "existing-session"


@pytest.mark.unit
async def test_phone_login_requires_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_PHONE", raising=False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def is_user_authorized(self) -> bool:
            return False

    from mycord_telegram_repl.auth import phone_login

    with pytest.raises(AuthError, match="TELEGRAM_PHONE"):
        await phone_login(FakeClient())


@pytest.mark.unit
async def test_phone_login_returns_session_when_already_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")

    class FakeSession:
        def save(self) -> str:
            return "already-authed"

    class FakeClient:
        session = FakeSession()

        async def connect(self) -> None:
            return None

        async def is_user_authorized(self) -> bool:
            return True

    from mycord_telegram_repl.auth import phone_login

    assert await phone_login(FakeClient()) == "already-authed"


@pytest.mark.unit
def test_session_file_is_written_with_restrictive_permissions(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "session.txt"
    save_session_string("the-session", target)
    assert target.read_text() == "the-session"
    assert oct(target.stat().st_mode)[-3:] == "600"


@pytest.mark.unit
def test_save_session_reports_write_failure(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    with pytest.raises(AuthError, match="could not write"):
        save_session_string("value", blocker / "sub" / "session.txt")
