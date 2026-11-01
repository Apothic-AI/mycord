"""CLI-level tests: exit codes, the dry-run default, and the refusal path.

These drive ``main()`` the way a shell does, so the contract that matters — the exit code —
is asserted on the real code path rather than on a helper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycord_signal import cli, deps


def test_doctor_reports_stale_signal_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stale pin exits 3 and names both the measured and required version."""
    old = tmp_path / "signal-cli"
    old.write_text("#!/bin/sh\necho 'signal-cli 0.14.6'\n")
    old.chmod(0o755)

    exit_code = cli.main(
        [
            "doctor",
            "--signal-cli",
            str(old),
            "--tunnel-bin",
            str(tmp_path / "no-tunnel"),
            "--tunnel-src",
            str(tmp_path / "no-src"),
            "--signal-data",
            str(tmp_path / "no-data"),
            "--socket",
            str(tmp_path / "no.sock"),
            "--no-probe",
        ]
    )
    assert exit_code == cli.EXIT_NOT_READY


def test_doctor_json_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    old = tmp_path / "signal-cli"
    old.write_text("#!/bin/sh\necho 'signal-cli 0.14.6'\n")
    old.chmod(0o755)

    cli.main(
        [
            "doctor",
            "--json",
            "--signal-cli",
            str(old),
            "--tunnel-bin",
            str(tmp_path / "no-tunnel"),
            "--tunnel-src",
            str(tmp_path / "no-src"),
            "--signal-data",
            str(tmp_path / "no-data"),
            "--socket",
            str(tmp_path / "no.sock"),
            "--no-probe",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["dependency_ready"] is False
    names = [check["name"] for check in payload["checks"]]
    assert "signal-cli" in names
    assert payload["versions"]["required.signal-cli"] == "0.14.9"
    assert payload["versions"]["required.tunnel-commit"] == "5a963d1"
    stale = next(check for check in payload["checks"] if check["name"] == "signal-cli")
    assert stale["status"] == "stale"
    assert "0.14.6" in stale["detail"]


def test_doctor_exit_zero_when_deps_ok_and_account_not_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_signal_cli: Path, fake_tunnel: Path
) -> None:
    """Dep-only CI must be able to go green without a linked phone."""
    # Not `tmp_path / "signal-cli"` — the fake_signal_cli fixture already owns that name as a
    # file, and a file cannot also be the account store directory.
    data = tmp_path / "signal-data"
    (data / "data").mkdir(parents=True)
    (data / "data" / "accounts.json").write_text(
        json.dumps({"version": 2, "accounts": [{"number": "+15550100", "uuid": "abc"}]})
    )
    # A live daemon is required for the call path; stub that check so this test isolates deps.
    monkeypatch.setattr(deps, "check_daemon", lambda _socket: deps.Check("daemon", "ok", "stub"))

    exit_code = cli.main(
        [
            "doctor",
            "--signal-cli",
            str(fake_signal_cli),
            "--tunnel-bin",
            str(fake_tunnel),
            "--tunnel-src",
            str(tmp_path / "no-src"),
            "--signal-data",
            str(data),
            "--socket",
            str(tmp_path / "no.sock"),
        ]
    )
    assert exit_code == cli.EXIT_OK


def test_doctor_require_account_exits_three(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_signal_cli: Path, fake_tunnel: Path
) -> None:
    monkeypatch.setattr(deps, "check_daemon", lambda _socket: deps.Check("daemon", "ok", "stub"))
    exit_code = cli.main(
        [
            "doctor",
            "--require-account",
            "--signal-cli",
            str(fake_signal_cli),
            "--tunnel-bin",
            str(fake_tunnel),
            "--tunnel-src",
            str(tmp_path / "no-src"),
            "--signal-data",
            str(tmp_path / "no-data"),
            "--socket",
            str(tmp_path / "no.sock"),
        ]
    )
    assert exit_code == cli.EXIT_NOT_READY


def test_call_guard_refuses_on_stale_deps(tmp_path: Path) -> None:
    old = tmp_path / "signal-cli"
    old.write_text("#!/bin/sh\necho 'signal-cli 0.14.6'\n")
    old.chmod(0o755)
    exit_code = cli.main(
        [
            "call-guard",
            "--signal-cli",
            str(old),
            "--tunnel-bin",
            str(tmp_path / "no-tunnel"),
            "--tunnel-src",
            str(tmp_path / "no-src"),
            "--signal-data",
            str(tmp_path / "no-data"),
            "--socket",
            str(tmp_path / "no.sock"),
        ]
    )
    assert exit_code == cli.EXIT_NOT_READY


def test_send_without_confirm_is_a_dry_run(tmp_path: Path, capsys) -> None:
    exit_code = cli.main(["send", "--to", "+15550100", "-m", "hello"])
    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_OK
    assert "dry run" in captured.out
    # The preview is the literal request, so a reviewer sees exactly what a confirm would do.
    assert '"method": "send"' in captured.out
    assert "+15550100" in captured.out


def test_send_without_recipient_is_a_usage_error(capsys) -> None:
    exit_code = cli.main(["send", "-m", "hello"])
    assert exit_code == cli.EXIT_USAGE
    assert "no recipient" in capsys.readouterr().err


def test_send_with_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("piped body"))
    exit_code = cli.main(["send", "--to", "+15550100", "--stdin"])
    assert exit_code == cli.EXIT_OK
    assert "piped body" in capsys.readouterr().out


def test_send_confirm_with_stale_deps_refuses(tmp_path: Path, capsys) -> None:
    """A confirmed send must pass the gate, because a stale client can corrupt delivery."""
    old = tmp_path / "signal-cli"
    old.write_text("#!/bin/sh\necho 'signal-cli 0.14.6'\n")
    old.chmod(0o755)
    exit_code = cli.main(
        [
            "send",
            "--to",
            "+15550100",
            "-m",
            "hello",
            "--confirm",
            "--signal-cli",
            str(old),
            "--tunnel-bin",
            str(tmp_path / "no-tunnel"),
            "--tunnel-src",
            str(tmp_path / "no-src"),
            "--signal-data",
            str(tmp_path / "no-data"),
            "--socket",
            str(tmp_path / "no.sock"),
        ]
    )
    assert exit_code == cli.EXIT_NOT_READY
    assert "refusing the call path" in capsys.readouterr().err


def test_parser_rejects_unknown_verb(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(["not-a-verb"])
    assert excinfo.value.code == cli.EXIT_USAGE


def test_call_subcommands_parse() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["call", "start", "+15550100"]).recipient == "+15550100"
    assert parser.parse_args(["call", "hangup", "42"]).call_id == 42
    assert (
        parser.parse_args(["call", "audio", "42", "--capture", "/tmp/x.raw"]).capture
        == "/tmp/x.raw"
    )


def test_exit_code_constants_match_repo_convention() -> None:
    # mycord-slack: 0 ok, 1 runtime, 2 usage, 3 auth/not-ready.
    assert (cli.EXIT_OK, cli.EXIT_ERROR, cli.EXIT_USAGE, cli.EXIT_NOT_READY) == (0, 1, 2, 3)
