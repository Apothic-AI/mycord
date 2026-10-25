"""Shared fixtures: a session file, a fake API, and a temporary state directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mycord_slack.session import Session

# Shaped like a real pair: a live capture on this machine was xoxc-…(114) and 253 chars
# of cookie. The lengths are asserted in tests, so they have to be the real ones.
TOKEN = "xoxc-12403112345678901-604451271234-3473161557912-" + "a" * 64
COOKIE = "xoxd-" + "b" * 248


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture()
def session() -> Session:
    return Session(
        token=TOKEN,
        cookie=COOKIE,
        team_id="T0TEST",
        team_name="Test Workspace",
        domain="test-workspace",
        url="https://test-workspace.slack.com/",
        captured_at=1_700_000_000.0,
        cookie_expires=1_900_000_000.0,
        source="localConfig_v2",
    )


@pytest.fixture()
def session_file(state_dir: Path, session: Session) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    path = state_dir / "session.json"
    path.write_text(json.dumps(session.to_dict()))
    path.chmod(0o600)
    return path


def slackdump_output(payload: Any) -> str:
    """Render a JSON payload the way slackdump's `-format JSON -no-json` does."""
    return json.dumps(payload)
