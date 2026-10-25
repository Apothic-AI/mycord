"""Session storage: permissions, redaction, and the failure modes that must be loud."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from conftest import COOKIE, TOKEN

from mycord_slack import session as session_ops
from mycord_slack.redact import MASK, redact, token_fingerprint
from mycord_slack.session import Session

pytestmark = pytest.mark.unit


def test_write_session_is_0600_and_round_trips(tmp_path: Path, session: Session) -> None:
    target = tmp_path / "state" / "session.json"
    session_ops.write_session(session, target)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"session file is {oct(mode)}; it holds the whole account"
    loaded = session_ops.load_session(target)
    assert loaded.token == session.token
    assert loaded.cookie == session.cookie
    assert loaded.team_id == session.team_id


def test_write_session_leaves_no_staging_file(tmp_path: Path, session: Session) -> None:
    target = tmp_path / "session.json"
    session_ops.write_session(session, target)
    assert [p.name for p in target.parent.iterdir()] == ["session.json"]


def test_load_refuses_a_world_readable_session(tmp_path: Path, session: Session) -> None:
    """A group/world-readable credential file is a hard error, not a warning."""
    target = tmp_path / "session.json"
    session_ops.write_session(session, target)
    target.chmod(0o644)
    with pytest.raises(session_ops.SessionError, match="chmod 600"):
        session_ops.load_session(target)


def test_load_refuses_a_group_readable_session(tmp_path: Path, session: Session) -> None:
    target = tmp_path / "session.json"
    session_ops.write_session(session, target)
    target.chmod(0o640)
    with pytest.raises(session_ops.SessionError, match="chmod 600"):
        session_ops.load_session(target)


def test_load_reports_a_missing_session_with_the_fix(tmp_path: Path) -> None:
    with pytest.raises(session_ops.SessionError, match="mycord-slack login"):
        session_ops.load_session(tmp_path / "nope.json")


def test_load_rejects_a_truncated_file(tmp_path: Path, session: Session) -> None:
    target = tmp_path / "session.json"
    session_ops.write_session(session, target)
    target.write_text('{"token": ')
    target.chmod(0o600)
    with pytest.raises(session_ops.SessionError, match="re-run login"):
        session_ops.load_session(target)


def test_load_rejects_a_session_missing_half_of_itself(tmp_path: Path, session: Session) -> None:
    """A token with no cookie is the exact half-session trap; fail loudly."""
    target = tmp_path / "session.json"
    payload = session.to_dict()
    payload.pop("cookie")
    target.write_text(json.dumps(payload))
    target.chmod(0o600)
    with pytest.raises(session_ops.SessionError, match="token or cookie"):
        session_ops.load_session(target)


def test_public_view_contains_no_secret(session: Session) -> None:
    public = session.public()
    for rendered in (json.dumps(public), json.dumps(public, ensure_ascii=False)):
        assert session.token not in rendered
        assert session.cookie not in rendered
        assert TOKEN not in rendered
        assert COOKIE not in rendered
    assert public["token"] == f"xoxc-…({len(TOKEN)})"


def test_env_format_is_what_slackdump_imports(session: Session) -> None:
    lines = session.to_env().strip().splitlines()
    assert lines[0] == f"SLACK_TOKEN={session.token}"
    assert lines[1] == f"SLACK_COOKIE={session.cookie}"


def test_expiry_is_derived_from_the_cookie_not_the_capture(session: Session) -> None:
    assert not session.is_expired(now=1_800_000_000.0)
    assert session.is_expired(now=1_900_000_001.0)


def test_a_session_with_no_cookie_expiry_is_not_considered_expired(session: Session) -> None:
    """Slack can omit `expires` for a session cookie; claiming expiry would be a lie.

    Unknown is not expired — `session --check` is the only honest answer in that case.
    """
    session.cookie_expires = None
    assert not session.is_expired(now=2_000_000_000.0)
    assert session.expires_in_days() is None


def test_redact_masks_tokens_and_cookies() -> None:
    text = f"token={TOKEN} cookie={COOKIE}"
    cleaned = redact(text)
    assert TOKEN not in cleaned
    assert COOKIE not in cleaned
    assert cleaned.count(MASK) == 2


def test_redact_masks_a_cookie_header() -> None:
    cleaned = redact(f"Cookie: d={COOKIE}")
    assert COOKIE not in cleaned


def test_redact_leaves_ordinary_text_alone() -> None:
    assert redact("slackdump workspace import failed: exit 1") == (
        "slackdump workspace import failed: exit 1"
    )


def test_token_fingerprint_shows_kind_and_length_only() -> None:
    fingerprint = token_fingerprint(TOKEN)
    assert fingerprint == f"xoxc-…({len(TOKEN)})"
    assert TOKEN[:20] not in fingerprint


def test_import_into_slackdump_writes_no_env_file_behind(
    tmp_path: Path, session: Session, monkeypatch
) -> None:
    """The env file slackdump requires must not outlive the call."""
    seen: list[Path] = []

    def fake_run(command: list[str], **_: object) -> object:
        env_path = Path(command[-1])
        seen.append(env_path)
        assert env_path.exists(), "slackdump was pointed at a file that did not exist"
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
        assert "xoxc-" in env_path.read_text()

        class Completed:
            returncode = 0
            output = "Workspace added and selected"

        return Completed()

    monkeypatch.setattr(session_ops, "_run", fake_run)
    result = session_ops.import_into_slackdump(session, Path("/usr/bin/slackdump"))
    assert result.ok
    assert seen and not seen[0].exists(), "the import env file was left on disk"


def test_import_failure_is_reported_not_raised(
    tmp_path: Path, session: Session, monkeypatch
) -> None:
    class Completed:
        returncode = 1
        output = "invalid credentials"

    monkeypatch.setattr(session_ops, "_run", lambda *a, **k: Completed())
    result = session_ops.import_into_slackdump(session, Path("/usr/bin/slackdump"))
    assert result.ok is False
    assert "invalid credentials" in result.detail
