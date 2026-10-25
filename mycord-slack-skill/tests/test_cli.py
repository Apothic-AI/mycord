"""CLI behaviour: routing, exit codes, and what lands on stdout vs stderr.

These drive `cli.main` in-process. The API is faked at the transport level, so a routing
mistake (a method forwarded to the wrong place, a payload parsed as an output flag) is
caught here rather than in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import COOKIE, TOKEN
from fake_slack import FakeSlack

from mycord_slack import cli, reads
from mycord_slack.session import Session

pytestmark = pytest.mark.unit


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    """A fake API that the CLI reaches through `MYCORD_SLACK_API_BASE`."""
    from mycord_slack import api

    real_call = api.call
    with FakeSlack() as server:

        def fake_call(
            session: Session,
            method: str,
            payload: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            # Route the CLI at the fake server, but keep the real transport — the tests
            # that matter are about what actually goes over the wire.
            return real_call(session, method, payload, base_url=server.base_url)

        monkeypatch.setattr(cli.api, "call", fake_call)
        yield server  # type: ignore[misc]


def _state_with_session(tmp_path: Path, session: Session) -> str:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    path = state / "session.json"
    path.write_text(json.dumps(session.to_dict()))
    path.chmod(0o600)
    return str(state)


def test_a_dotted_method_is_routed_to_the_passthrough(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state_with_session(tmp_path, session)
    code = cli.main(["conversations.list", "--json", '{"limit":3}', "--state", state])
    assert code == cli.EXIT_OK
    assert fake.last().method == "conversations.list"
    assert fake.last().form["limit"] == "3"
    body = json.loads(capsys.readouterr().out)
    assert "method" not in body, "the body is unwrapped unless --raw"


def test_a_method_with_no_payload_is_allowed(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state_with_session(tmp_path, session)
    assert cli.main(["auth.test", "--state", state]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out) == {}


def test_an_unknown_method_is_not_a_usage_error(
    tmp_path: Path, session: Session, fake: FakeSlack
) -> None:
    """No verb registry: a method Slack has and this CLI has never heard of still routes."""
    state = _state_with_session(tmp_path, session)
    assert cli.main(["brand.new.method", "--state", state]) == cli.EXIT_OK
    assert fake.last().method == "brand.new.method"


def test_a_slack_error_exits_one_and_says_why_on_stderr(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.responses["admin.users.list"] = {"ok": False, "error": "admin_unauthorized"}
    state = _state_with_session(tmp_path, session)
    code = cli.main(["admin.users.list", "--json", '{"team_id":"T1"}', "--state", state])
    assert code == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "admin_unauthorized" in captured.err
    assert json.loads(captured.out)["error"] == "admin_unauthorized"


def test_an_error_verbatim_means_verbatim(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    """No translation layer: the code is Slack's, not a friendly paraphrase."""
    fake.responses["chat.postMessage"] = {"ok": False, "error": "not_in_channel"}
    state = _state_with_session(tmp_path, session)
    cli.main(["chat.postMessage", "--json", '{"channel":"C1"}', "--state", state])
    assert "not_in_channel" in capsys.readouterr().err


def test_slack_explanations_reach_stderr(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.responses["search.messages"] = {
        "ok": False,
        "error": "invalid_arguments",
        "response_metadata": {"messages": ["[ERROR] missing required field: query"]},
    }
    state = _state_with_session(tmp_path, session)
    cli.main(["search.messages", "--json", '{"count":1}', "--state", state])
    assert "missing required field: query" in capsys.readouterr().err


def test_raw_keeps_the_envelope(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state_with_session(tmp_path, session)
    cli.main(["auth.test", "--raw", "--state", state])
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["method"] == "auth.test"
    assert "data" in body


def test_a_malformed_payload_is_a_usage_error_not_a_session_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reported before the session is read, so the caller fixes the right thing."""
    code = cli.main(["chat.postMessage", "--json", "{bad", "--state", str(tmp_path / "none")])
    assert code == cli.EXIT_USAGE
    assert "not valid JSON" in capsys.readouterr().err


def test_no_session_exits_three_and_names_the_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["auth.test", "--state", str(tmp_path / "none")])
    assert code == cli.EXIT_AUTH
    assert "mycord-slack login" in capsys.readouterr().err


def test_giving_the_payload_twice_is_a_usage_error(tmp_path: Path, session: Session) -> None:
    state = _state_with_session(tmp_path, session)
    code = cli.main(["chat.postMessage", "--json", "{}", "{}", "--state", state])
    assert code == cli.EXIT_USAGE


def test_a_named_verb_is_not_routed_to_the_passthrough(
    tmp_path: Path, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`session` has no dot; neither does any other local verb. They must stay local."""
    state = _state_with_session(tmp_path, session)
    assert cli.main(["session", "--state", state]) == cli.EXIT_OK


def test_state_flag_overrides_the_default_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYCORD_SLACK_STATE", str(tmp_path / "env-state"))
    args = cli.build_parser().parse_args(["session", "--state", str(tmp_path / "flag-state")])
    assert cli._paths(args).state == tmp_path / "flag-state"


def test_state_env_is_used_when_no_flag_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYCORD_SLACK_STATE", str(tmp_path / "env-state"))
    args = cli.build_parser().parse_args(["session"])
    assert cli._paths(args).state == tmp_path / "env-state"


def test_no_arguments_prints_help_and_exits_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_USAGE
    assert "Slack for agents" in capsys.readouterr().out


def test_help_documents_the_passthrough_contract(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["help"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "passthrough" in out.lower()
    assert "exit" in out.lower()


def test_channels_command_renders_a_listing(
    tmp_path: Path,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    listing = reads.Channels(
        raw=[
            {
                "id": "C0PUBLIC",
                "name": "general",
                "is_im": False,
                "is_mpim": False,
                "is_private": False,
                "num_members": 4,
                "is_archived": False,
            },
            {"id": "D0DIRECT", "name": "", "user": "U0ALICE", "is_im": True},
            {"id": "C0OLD", "name": "old", "is_archived": True, "num_members": 1},
        ],
        fetched_at=0.0,
    )
    monkeypatch.setattr(reads, "channels", lambda **_: listing)
    state = _state_with_session(tmp_path, session)
    assert cli.main(["channels", "--state", state]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "#general" in out
    assert "DM U0ALICE" in out
    assert "#old" not in out, "archived channels are hidden unless asked for"


def test_channels_json_includes_the_kind(
    tmp_path: Path,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    listing = reads.Channels(
        raw=[
            {"id": "C0PRIVATE", "name": "leadership", "is_private": True, "num_members": 2},
            {"id": "C0PUBLIC", "name": "general", "num_members": 4},
        ],
        fetched_at=0.0,
    )
    monkeypatch.setattr(reads, "channels", lambda **_: listing)
    state = _state_with_session(tmp_path, session)
    cli.main(["channels", "--json", "--state", state])
    body = json.loads(capsys.readouterr().out)
    kinds = {row["name"]: row["kind"] for row in body["channels"]}
    assert kinds == {"leadership": "private", "general": "public"}


def test_read_resolves_a_name_before_dumping(
    tmp_path: Path,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []

    def fake_dump(target: str, **_: object) -> list[reads.Message]:
        seen.append(target)
        return [reads.Message(ts="1792950000.000100", text="hello", user="U1", kind="message")]

    monkeypatch.setattr(reads, "resolve_channel", lambda ref, *_: f"ID({ref})")
    monkeypatch.setattr(reads, "dump", fake_dump)
    state = _state_with_session(tmp_path, session)
    assert cli.main(["read", "#general", "--state", state]) == cli.EXIT_OK
    assert seen == ["ID(#general)"]
    assert "hello" in capsys.readouterr().out


def test_read_thread_uses_slackdumps_colon_notation(
    tmp_path: Path, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(reads, "resolve_channel", lambda ref, *_: "C0PUBLIC")
    monkeypatch.setattr(reads, "dump", lambda target, **_: seen.append(target) or [])
    state = _state_with_session(tmp_path, session)
    cli.main(["read", "C0PUBLIC", "--thread", "1792950000.000100", "--state", state])
    assert seen == ["C0PUBLIC:1792950000.000100"]


def test_session_command_reports_status_without_secrets(
    tmp_path: Path, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state_with_session(tmp_path, session)
    assert cli.main(["session", "--state", state]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert COOKIE not in out
    assert "d on .slack.com" in out


def test_session_without_a_file_exits_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["session", "--state", str(tmp_path / "none")]) == cli.EXIT_AUTH
    assert "no usable session" in capsys.readouterr().out


def test_session_check_calls_auth_test(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.responses["auth.test"] = {"ok": True, "team": "Test Workspace", "user": "tester"}
    state = _state_with_session(tmp_path, session)
    assert cli.main(["session", "--check", "--state", state]) == cli.EXIT_OK
    assert "auth.test  : ok" in capsys.readouterr().out


def test_session_check_fails_loudly_on_a_dead_session(
    tmp_path: Path, session: Session, fake: FakeSlack, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.responses["auth.test"] = {"ok": False, "error": "invalid_auth"}
    state = _state_with_session(tmp_path, session)
    assert cli.main(["session", "--check", "--state", state]) == cli.EXIT_AUTH
    assert "invalid_auth" in capsys.readouterr().out


def test_logout_removes_the_session_and_forgets_it_in_slackdump(
    tmp_path: Path,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Deleting only session.json would be a lie: slackdump holds its own copy."""
    from pathlib import Path as _Path

    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        output = ""

    monkeypatch.setattr(cli, "slackdump_binary", lambda: _Path("/usr/bin/slackdump"))
    monkeypatch.setattr(
        cli.session_ops, "_run", lambda command: calls.append(command) or Completed()
    )
    state = _state_with_session(tmp_path, session)
    assert cli.main(["logout", "--state", state]) == cli.EXIT_OK
    assert not (Path(state) / "session.json").exists()
    assert calls and calls[0][1:] == ["workspace", "del", "-y"]
    out = capsys.readouterr().out
    assert "removed" in out
    assert "slackdump : ok" in out


def test_logout_can_keep_slackdumps_copy(
    tmp_path: Path,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.session_ops, "_run", lambda command: calls.append(command) or None)
    state = _state_with_session(tmp_path, session)
    assert cli.main(["logout", "--keep-slackdump", "--state", state]) == cli.EXIT_OK
    assert calls == []
    assert "slackdump : skipped" in capsys.readouterr().out


def test_the_local_session_file_is_table(
    tmp_path: Path, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    """`session` is the truth about *our* credential; it does not imply slackdump is dead."""
    state = _state_with_session(tmp_path, session)
    cli.main(["session", "--state", state])
    assert "session    :" in capsys.readouterr().out


def test_a_world_readable_session_is_refused_by_the_cli(
    tmp_path: Path, session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state_with_session(tmp_path, session)
    (Path(state) / "session.json").chmod(0o644)
    assert cli.main(["auth.test", "--state", state]) == cli.EXIT_AUTH
    assert "chmod 600" in capsys.readouterr().err


def test_a_read_failure_exits_one(
    tmp_path: Path,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise reads.ReadError("slackdump is not installed")

    monkeypatch.setattr(reads, "channels", boom)
    state = _state_with_session(tmp_path, session)
    assert cli.main(["channels", "--state", state]) == cli.EXIT_ERROR
    assert "slackdump is not installed" in capsys.readouterr().err
