"""The CLI: exit codes, --json on every verb, and the redaction of secrets.

Exit codes are the contract other scripts branch on, so each one gets a test that would
fail if the mapping drifted — especially 2 (not installed) versus 1 (runtime), which is the
distinction that tells an operator "install something" instead of "something broke".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fake_pactl import OPERATOR_SINK, OPERATOR_SOURCE, FakePactl

from mycord_zoom.cli import main

pytestmark = pytest.mark.unit


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------- exit codes


def test_audio_up_down_round_trip_json(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    code, out, _ = run_cli(capsys, "--json", "audio", "up")
    assert code == 0
    payload = json.loads(out)
    assert payload["status"]["running"] is True

    code, out, _ = run_cli(capsys, "--json", "audio", "down")
    assert code == 0
    assert json.loads(out)["changed"] is True
    # The operator's audio is back, which is the whole point.
    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert fake_pactl.state["default_source"] == OPERATOR_SOURCE


def test_route_to_agent_and_back(capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl) -> None:
    run_cli(capsys, "audio", "up")
    code, out, _ = run_cli(capsys, "--json", "audio", "route", "to-agent")
    assert code == 0
    assert json.loads(out)["direction"] == "to-agent"
    run_cli(capsys, "--json", "audio", "route", "to-mixer")
    assert fake_pactl.state["default_sink"] == OPERATOR_SINK


def test_route_before_up_is_unconfigured(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    code, _, err = run_cli(capsys, "audio", "route", "to-agent")
    assert code == 3, "exit 3 means 'run audio up', which is a configuration, not a bug"
    assert "audio up" in err


def test_missing_piper_is_not_installed(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    run_cli(capsys, "audio", "up")
    code, _, err = run_cli(capsys, "audio", "say", "hello")
    assert code == 2, "a missing model is exit 2 (install something), not 1"
    assert "piper" in err.lower()


def test_say_without_a_plane_is_unconfigured(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    code, _, err = run_cli(capsys, "audio", "say", "hello")
    assert code == 3
    assert "audio up" in err


def test_bad_meeting_url_is_a_runtime_error(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    code, _, err = run_cli(capsys, "join", "https://example.com/not-a-meeting")
    assert code == 1
    assert "cannot read" in err or "not a Zoom host" in err


def test_usage_error_is_not_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    # argparse exits 2 on a usage error; 2 here means "not installed", so it must be remapped.
    code, _, _ = run_cli(capsys, "audio", "route", "sideways")
    assert code == 1


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys)
    assert code == 1
    assert "usage:" in out


# --------------------------------------------------------------------------- json shape


def test_audio_status_json_has_the_measured_fields(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    run_cli(capsys, "audio", "up")
    code, out, _ = run_cli(capsys, "--json", "audio", "status")
    payload = json.loads(out)
    assert code == 0
    for key in (
        "running",
        "speaker_sink",
        "mic_sink",
        "mic_source",
        "modules",
        "current_default_sink",
        "previous_default_sink",
        "routed",
    ):
        assert key in payload
    assert payload["previous_default_sink"] == OPERATOR_SINK


def test_listen_reports_peak_and_capture_without_asr(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`listen --no-transcribe` works with no model installed, and reports what it heard."""
    run_cli(capsys, "audio", "up")
    code, out, _ = run_cli(
        capsys, "--json", "audio", "listen", "--seconds", "0.2", "--no-transcribe"
    )
    payload = json.loads(out)
    assert code == 0
    assert payload["capture"]["peak"] > 0, "the stub recorder emitted samples"
    assert "transcripts" not in payload


def test_listen_reports_a_transcription_error_without_failing_the_capture(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    run_cli(capsys, "audio", "up")
    code, out, _ = run_cli(capsys, "--json", "audio", "listen", "--seconds", "0.2")
    payload = json.loads(out)
    # The capture is what was asked for and it succeeded; ASR being absent is reported.
    assert code == 0
    assert payload["capture"]["peak"] > 0
    assert payload.get("transcripts") is None
    assert "transcription_error" in payload


def test_join_launches_and_redacts_the_password(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mycord_zoom.desktop.find_zoom_binary", lambda: "/opt/zoom/ZoomLauncher")
    launched: list[list[str]] = []

    def fake_run(argv: object, **_: object) -> object:
        from mycord_zoom.tools import CommandResult

        args = [str(a) for a in argv]  # type: ignore[union-attr]
        launched.append(args)
        return CommandResult(argv=tuple(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mycord_zoom.cli.run_command", fake_run)

    code, out, _ = run_cli(capsys, "--json", "join", "https://zoom.us/j/1234567890?pwd=hunter2")
    assert code == 0
    assert launched and launched[0][1].endswith("confno=1234567890&pwd=hunter2")
    # The password is a credential: it goes to the client, never into the report.
    assert "hunter2" not in out
    assert json.loads(out)["meeting"]["password_present"] is True


def test_status_json_with_audio(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mycord_zoom.desktop._zoom_pids", lambda: ())
    run_cli(capsys, "audio", "up")
    code, out, _ = run_cli(capsys, "--json", "status", "--with-audio")
    payload = json.loads(out)
    assert code == 0
    assert payload["audio"]["running"] is True
    assert "client_running" in payload


def test_leave_reports_no_meeting(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mycord_zoom.tools import CommandResult

    monkeypatch.setattr("mycord_zoom.desktop._zoom_pids", lambda: ())
    monkeypatch.setattr(
        "mycord_zoom.desktop._windows",
        lambda runner: (),
    )
    code, out, _ = run_cli(capsys, "--json", "leave")
    assert code == 0
    assert json.loads(out)["left"] is False
    assert CommandResult  # silence unused-import lint in the one place it is not needed


def test_down_when_nothing_is_up_is_not_an_error(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    code, out, _ = run_cli(capsys, "--json", "audio", "down")
    assert code == 0
    assert json.loads(out)["changed"] is False


def test_custom_device_names_are_honoured(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl
) -> None:
    code, _, _ = run_cli(
        capsys,
        "--speaker-sink",
        "z_a",
        "--mic-sink",
        "z_b",
        "--mic-source",
        "z_c",
        "audio",
        "up",
    )
    assert code == 0
    assert "z_a" in fake_pactl.state["sinks"]
    assert "z_c" in fake_pactl.state["sources"]


def test_state_flag_isolates_installs(
    capsys: pytest.CaptureFixture[str], fake_pactl: FakePactl, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    code, _, _ = run_cli(capsys, "--state", str(other), "audio", "up")
    assert code == 0
    assert (other / "audio-session.json").is_file()
