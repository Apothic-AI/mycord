"""Library discovery, session handling, and CLI wiring — no network, no browser."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from gvoice_skill import cli
from gvoice_skill.bridge import GvoiceError, find_library


def _fake_library(root: Path, *, installed: bool = True) -> Path:
    """A directory shaped like gvoice/ as far as discovery and the bridge care."""
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"name":"gvoice"}')
    (root / "src" / "gv-api.ts").write_text("export const METHODS = {};\n")
    if installed:
        (root / "node_modules").mkdir()
    return root


def test_find_library_prefers_the_explicit_path(tmp_path: Path) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    found = find_library(lib)
    assert found.root == lib.resolve()
    assert found.session == (lib / "gv-session.json").resolve()


def test_find_library_honours_env_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lib = _fake_library(tmp_path / "elsewhere")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    assert find_library().root == lib.resolve()


def test_find_library_honours_session_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    monkeypatch.setenv("GVOICE_SESSION", str(tmp_path / "s.json"))
    assert find_library().session == (tmp_path / "s.json").resolve()


def test_find_library_resolves_the_sibling_layout() -> None:
    """The real repo layout: skill and library are siblings under tooling/mycord."""
    found = find_library()
    assert found.root.name == "gvoice"
    assert found.api_module.is_file()


def test_an_explicit_invalid_library_errors_instead_of_falling_back(tmp_path: Path) -> None:
    """Naming a library and silently getting a different one is worse than failing."""
    with pytest.raises(GvoiceError, match="not the gvoice library"):
        find_library(tmp_path / "nope")


def test_an_invalid_env_root_errors_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GVOICE_ROOT", str(tmp_path / "nope"))
    with pytest.raises(GvoiceError, match="GVOICE_ROOT points at"):
        find_library()


def test_install_flag_reflects_node_modules(tmp_path: Path) -> None:
    assert find_library(_fake_library(tmp_path / "a", installed=True)).install_ok
    assert not find_library(_fake_library(tmp_path / "b", installed=False)).install_ok


def _session_file(path: Path, mode: int = 0o600) -> Path:
    payload = {
        "cookies": [
            {
                "name": "SAPISID",
                "value": "secret",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            }
        ],
        "sapisid": "secret",
    }
    path.write_text(json.dumps(payload))
    path.chmod(mode)
    return path


def test_missing_session_is_named_with_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    with pytest.raises(GvoiceError, match="run `gvoice-skill login`"):
        cli._require_session(find_library())


def test_world_readable_session_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json", mode=0o644)
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    with pytest.raises(GvoiceError, match="chmod 600"):
        cli._require_session(find_library())


def test_0600_session_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    cli._require_session(find_library())


def test_session_command_reports_status_without_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    args = cli.build_parser().parse_args(["session"])
    assert args.func(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "SAPISID" in out
    assert "secret" not in out


def test_session_command_flags_loose_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json", mode=0o644)
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    args = cli.build_parser().parse_args(["session"])
    assert args.func(args) == cli.EXIT_ERROR
    assert "WARNING" in capsys.readouterr().out


def test_thread_command_accepts_a_bare_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))

    captured: dict[str, object] = {}

    def fake_call(library, method, payload=None, *, timeout=60.0):  # type: ignore[no-untyped-def]
        captured["method"] = method
        captured["payload"] = payload
        return [[]]

    monkeypatch.setattr(cli, "api_call", fake_call)
    args = cli.build_parser().parse_args(["thread", "+19542701292"])
    cli.cmd_thread(args)
    assert captured["method"] == "api2thread/get"
    assert captured["payload"] == ["t.+19542701292", 20]


def test_threads_wires_the_warmsync_token_not_the_page_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[pageSize] alone silently returns no threads; the token must come first."""
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    calls: list[tuple[str, object]] = []

    def fake_call(library, method, payload=None, *, timeout=60.0):  # type: ignore[no-untyped-def]
        calls.append((method, payload))
        if method == "inbox/warmsync":
            return [None, [2]]
        return [[["t.+1955", 0]], "next"]

    monkeypatch.setattr(cli, "api_call", fake_call)
    args = cli.build_parser().parse_args(["threads", "--limit", "7"])
    cli.cmd_threads(args)
    assert calls == [("inbox/warmsync", []), ("api2thread/list", [2, 7])]


def test_api_rejects_a_non_array_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validated before the session is touched, so it holds with or without a jar."""
    lib = _fake_library(tmp_path / "gvoice")
    _session_file(lib / "gv-session.json")
    monkeypatch.setenv("GVOICE_ROOT", str(lib))
    args = cli.build_parser().parse_args(["api", "account/get", '{"not":"positional"}'])
    assert cli.cmd_api(args) == cli.EXIT_USAGE
    assert "positional protojson" in capsys.readouterr().err


def test_sms_send_without_a_capture_explains_why(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.build_parser().parse_args(["sms", "send", "+15551234567", "hello"])
    assert cli.cmd_sms(args) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "synthesised" in err


def test_session_written_0600_regardless_of_umask(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "gv-session.json"
    cli._write_session({"cookies": [], "sapisid": "x"}, target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["sapisid"] == "x"


def test_session_staging_file_name_stays_under_the_ignore_rule(tmp_path: Path) -> None:
    """The atomic write stages at `<name>.tmp`, which must not be a new commit risk."""
    target = tmp_path / "gv-session.json"
    staging: list[str] = []
    real_replace = os.replace

    def spy(src, dst):  # type: ignore[no-untyped-def]
        staging.append(Path(src).name)
        return real_replace(src, dst)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", spy)
        cli._write_session({"cookies": [], "sapisid": "x"}, target)
    assert staging == ["gv-session.json.tmp"]
    assert not list(tmp_path.glob("*.tmp")), "staging file survived the write"


def test_failed_session_write_leaves_no_staging_file(tmp_path: Path) -> None:
    """A crash mid-write must not leave a readable partial jar behind."""
    target = tmp_path / "gv-session.json"

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            cli._write_session({"cookies": [], "sapisid": "secret"}, target)
    assert not target.exists()
    assert not list(tmp_path.glob("*")), list(tmp_path.iterdir())


def test_login_parser_defaults_are_safe() -> None:
    args = cli.build_parser().parse_args(["login"])
    assert args.no_copy_profile is False  # copies the profile by default
    assert args.headed is False


def test_main_surfaces_gvoice_errors_as_a_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(args):  # type: ignore[no-untyped-def]
        raise GvoiceError("nope")

    monkeypatch.setattr(cli, "cmd_account", boom)
    assert cli.main(["account"]) == cli.EXIT_ERROR
    assert "error: nope" in capsys.readouterr().err


def test_every_subcommand_has_a_handler() -> None:
    parser = cli.build_parser()
    for args in (
        ["login"],
        ["session"],
        ["account"],
        ["threads"],
        ["thread", "+1"],
        ["search", "x"],
        ["api", "m"],
        ["sip"],
        ["call", "+1"],
        ["sms", "send", "+1", "t"],
    ):
        parsed = parser.parse_args(args)
        assert callable(parsed.func)


def test_env_example_documents_every_variable_the_code_reads() -> None:
    """A stale .env.example is how a documented knob quietly stops existing."""
    code = "\n".join(
        (Path(cli.__file__).parent / name).read_text() for name in ("cli.py", "bridge.py", "cdp.py")
    )
    example = (Path(cli.__file__).parents[2] / ".env.example").read_text()
    documented = {
        line.split("=", 1)[0].lstrip("# ")
        for line in example.splitlines()
        if "=" in line and "GVOICE_" in line
    }
    referenced = {token for token in os.listdir("/") if False}  # keep mypy quiet
    referenced = {
        part.split('"')[1] for part in code.split("environ.get(")[1:] if part.startswith('"GVOICE_')
    }
    assert documented == referenced
