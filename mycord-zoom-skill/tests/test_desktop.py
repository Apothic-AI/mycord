"""Deep-link parsing, launch planning, status parsing, and leave.

The parsing tests carry the weight here: a meeting URL that is coerced into the wrong
meeting id launches the client into a room full of strangers, so every rejected form is
tested as carefully as every accepted one.
"""

from __future__ import annotations

import pytest

from mycord_zoom.desktop import (
    MeetingLink,
    launch_plan,
    leave,
    parse_meeting,
    status,
)
from mycord_zoom.errors import DesktopError
from mycord_zoom.tools import CommandResult

pytestmark = pytest.mark.unit


def expect_result(
    argv: object, *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CommandResult:
    return CommandResult(
        argv=tuple(str(a) for a in argv),  # type: ignore[union-attr]
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# --------------------------------------------------------------------------- parsing


def test_web_join_url() -> None:
    link = parse_meeting("https://zoom.us/j/1234567890?pwd=secret")
    assert link.meeting_id == "1234567890"
    assert link.action == "join"
    assert link.password == "secret"
    assert link.deep_link == "zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=secret"


def test_subdomain_url_keeps_its_host() -> None:
    link = parse_meeting("https://us05web.zoom.us/j/1234567890")
    assert link.host == "us05web.zoom.us"
    assert link.deep_link.startswith("zoommtg://us05web.zoom.us/join?")


def test_web_client_join_url() -> None:
    link = parse_meeting("https://zoom.us/wc/1234567890/join")
    assert link.meeting_id == "1234567890"


def test_start_url_implies_start_action() -> None:
    link = parse_meeting("https://zoom.us/s/1234567890")
    assert link.action == "start"


def test_bare_meeting_id() -> None:
    link = parse_meeting("1234567890")
    assert link.meeting_id == "1234567890"
    assert link.action == "join"


def test_deep_link_round_trips() -> None:
    link = parse_meeting("zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=abc")
    assert link.meeting_id == "1234567890"
    assert link.password == "abc"


def test_zoomus_scheme_is_accepted() -> None:
    link = parse_meeting("zoomus://zoom.us/join?confno=1234567890")
    assert link.meeting_id == "1234567890"


def test_government_host_is_accepted() -> None:
    link = parse_meeting("https://zoomgov.com/j/1234567890")
    assert link.host == "zoomgov.com"


@pytest.mark.parametrize(
    "target",
    [
        "",
        "   ",
        "https://evil.example.com/j/1234567890",  # non-Zoom host with a Zoom-shaped path
        "https://zoom.us/j/123",  # too short to be a meeting id
        "https://zoom.us/j/abcdefghij",
        "https://zoom.us/",
        "meeting tomorrow at noon",
    ],
)
def test_unrecognised_targets_are_refused(target: str) -> None:
    with pytest.raises(DesktopError):
        parse_meeting(target)


def test_password_is_not_leaked_in_serialisation() -> None:
    link = parse_meeting("https://zoom.us/j/1234567890?pwd=secret")
    payload = link.to_dict()
    assert payload["password_present"] is True
    assert "secret" not in str(payload)


# --------------------------------------------------------------------------- launch


def test_launch_plan_uses_the_client_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_zoom.desktop.find_zoom_binary", lambda: "/opt/zoom/ZoomLauncher")
    plan = launch_plan(parse_meeting("1234567890"))
    assert plan.argv[0] == "/opt/zoom/ZoomLauncher"
    assert plan.argv[1].startswith("zoommtg://")


def test_launch_plan_falls_back_to_xdg_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_zoom.desktop.find_zoom_binary", lambda: None)
    monkeypatch.setattr(
        "mycord_zoom.desktop.which_optional",
        lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
    )
    plan = launch_plan(parse_meeting("1234567890"))
    assert plan.argv[0] == "/usr/bin/xdg-open"


def test_launch_plan_without_any_opener_fails_with_the_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mycord_zoom.desktop.find_zoom_binary", lambda: None)
    monkeypatch.setattr("mycord_zoom.desktop.which_optional", lambda name: None)
    with pytest.raises(DesktopError, match="install Zoom"):
        launch_plan(parse_meeting("1234567890"))


# --------------------------------------------------------------------------- status


# Exactly the shape `wmctrl -l` prints: id, desktop, host, title. Coordinates are only
# printed by `-lG`, which `status` does not use.
WMCTRL_OUTPUT = """0x06200017  0 vici Zoom Workplace
0x06200018  0 vici Zoom Meeting - Standup
0x01c00017  0 vici Some Other Window
"""


def test_status_parses_wmctrl_and_finds_a_meeting() -> None:
    def runner(argv: object, **_: object) -> CommandResult:
        return expect_result(argv, stdout=WMCTRL_OUTPUT)

    state = status(runner=runner)
    titles = [w.title for w in state.windows]
    assert "Zoom Workplace" in titles
    assert "Some Other Window" not in titles
    assert state.meeting_window is not None
    assert state.meeting_window.title == "Zoom Meeting - Standup"


def test_status_with_only_the_home_window_reports_no_meeting() -> None:
    def runner(argv: object, **_: object) -> CommandResult:
        return expect_result(argv, stdout="0x01  0 vici Zoom Workplace\n")

    state = status(runner=runner)
    assert state.meeting_window is None
    assert any("unverified" in note for note in state.notes)


def test_status_with_no_windows_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    # The process table is the host's, not the test's: pin it so the assertion is about the
    # code rather than about whether Zoom happens to be running on this machine.
    monkeypatch.setattr("mycord_zoom.desktop._zoom_pids", lambda: ())

    def runner(argv: object, **_: object) -> CommandResult:
        return expect_result(argv, stdout="")

    state = status(runner=runner)
    assert state.windows == ()
    assert any("does not appear to be running" in note for note in state.notes)


def test_status_tolerates_a_failing_wmctrl() -> None:
    def runner(argv: object, **_: object) -> CommandResult:
        return expect_result(argv, returncode=1, stderr="cannot open display")

    state = status(runner=runner)
    assert state.windows == ()


# --------------------------------------------------------------------------- leave


def test_leave_closes_only_the_meeting_window() -> None:
    calls: list[list[str]] = []

    def runner(argv: object, **_: object) -> CommandResult:
        args = [str(a) for a in argv]  # type: ignore[union-attr]
        calls.append(args)
        if args[0].endswith("wmctrl") and args[1] == "-l":
            return expect_result(argv, stdout=WMCTRL_OUTPUT)
        return expect_result(argv)

    result = leave(runner=runner)
    assert result["left"] is True
    assert result["window"] == "0x06200018"
    assert ["-ic", "0x06200018"] in [call[1:] for call in calls if len(call) > 1]


def test_leave_refuses_when_no_meeting_is_identified() -> None:
    def runner(argv: object, **_: object) -> CommandResult:
        return expect_result(argv, stdout="0x01 0 vici Zoom Workplace\n")

    result = leave(runner=runner)
    assert result["left"] is False
    assert "no meeting window" in str(result["reason"])


def test_leave_by_explicit_title() -> None:
    def runner(argv: object, **_: object) -> CommandResult:
        if str(argv[0]).endswith("wmctrl") and str(argv[1]) == "-l":
            return expect_result(argv, stdout=WMCTRL_OUTPUT)
        return expect_result(argv)

    result = leave(runner=runner, window="Zoom Workplace")
    assert result["left"] is True
    assert result["window"] == "0x06200017"


def test_leave_by_unknown_title_is_an_error() -> None:
    def runner(argv: object, **_: object) -> CommandResult:
        return expect_result(argv, stdout=WMCTRL_OUTPUT)

    with pytest.raises(DesktopError, match="no Zoom window matching"):
        leave(runner=runner, window="Nonexistent")


def test_meeting_link_serialisation_shape() -> None:
    link = MeetingLink(
        meeting_id="1234567890",
        action="join",
        password=None,
        host="zoom.us",
        deep_link="zoommtg://zoom.us/join?action=join&confno=1234567890",
    )
    assert link.to_dict()["password_present"] is False
