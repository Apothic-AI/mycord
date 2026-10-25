"""Tests that talk to the real Slack workspace. Skipped unless a usable session exists.

Run with `make test-live`, or `uv run pytest -m live`.

Deliberately shallow, because these are the only tests that can catch Slack changing
something — the whole risk of a client-token session. Every read here is read-only; the
one write test is **opt-in** via `MYCORD_SLACK_LIVE_CHANNEL` and it deletes what it posts,
because posting as the human into an arbitrary channel is not something a test suite
should decide on its own.

A present-but-dead session skips rather than fails: sessions are revoked independently of
their cookie expiry, and an expired credential is not a code regression. `auth.test`
decides which it is.
"""

from __future__ import annotations

import os
import time

import pytest

from mycord_slack import api, cli, reads
from mycord_slack.config import paths
from mycord_slack.session import Session, SessionError, load_session

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def session() -> Session:
    path = paths().session
    try:
        record = load_session(path)
    except SessionError as exc:
        pytest.skip(f"no usable session: {exc}")
    result = api.verify(record)
    if not result.ok:
        pytest.skip(
            f"session at {path} is no longer accepted by Slack ({result.error}). "
            "Run `mycord-slack login` against a browser still signed in to Slack."
        )
    return record


def test_auth_test_identifies_the_account(session: Session) -> None:
    result = api.verify(session)
    assert result.ok, result.error
    assert result.data["user_id"]
    assert result.data["team_id"]


def test_a_client_token_cannot_call_app_only_methods(session: Session) -> None:
    """`not_allowed_token_type` is a token-kind fact, not a broken session.

    `apps.connections.open` needs a bot/app token, so it must fail *this* way — if it
    returned `invalid_auth`, the cookie would not be riding along.
    """
    result = api.call(session, "apps.connections.open", {})
    assert result.ok is False
    assert result.error in {"not_allowed_token_type", "missing_scope", "invalid_auth"}
    if result.error == "invalid_auth":
        pytest.fail("the d cookie was not sent: apps.connections.open should reject the kind")


def test_conversations_list_returns_channels(session: Session) -> None:
    result = api.call(session, "conversations.list", {"limit": 5})
    assert result.ok, result.error
    assert isinstance(result.data.get("channels"), list)


def test_nested_arguments_reach_slack_as_json(session: Session) -> None:
    """Guards the encoding trap: a python-repr'd structure is `invalid_blocks_format`."""
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*live test*", "emoji": True}},
        {"type": "divider"},
    ]
    # Validate the structure without posting: chat.postMessage is the only validator, so
    # this asserts on the encoding path by round-tripping through the same helper.
    encoded = api._encode({"blocks": blocks})
    assert isinstance(encoded["blocks"], str)
    assert '"emoji": true' in encoded["blocks"]


def test_slackdump_lists_channels(session: Session) -> None:
    listing = reads.channels()
    assert listing.raw, "slackdump returned no channels for a live workspace"
    assert any(c.get("id") for c in listing.raw)


def test_slackdump_resolves_a_real_channel_name(session: Session) -> None:
    listing = reads.channels()
    named = next((c for c in listing.raw if c.get("name")), None)
    if named is None:
        pytest.skip("workspace has no named channel")
    resolved = reads.resolve_channel(f"#{named['name']}", listing)
    assert resolved == named["id"]


def test_slackdump_reads_a_channel_it_can_see(session: Session) -> None:
    listing = reads.channels()
    channel = next((c for c in listing.raw if c.get("name")), None)
    if channel is None:
        pytest.skip("workspace has no named channel")
    messages = reads.dump(str(channel["id"]), limit=5)
    for message in messages:
        assert message.ts


def test_search_returns_a_list(session: Session) -> None:
    hits, _total = reads.search(session, "slack", via="api", count=3)
    assert isinstance(hits, list)


def test_the_cli_reads_without_a_tty(session: Session, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["auth.test"]) == cli.EXIT_OK
    assert "team" in capsys.readouterr().out


# ----------------------------------------------------------------------------- write


def test_a_posted_message_appears_in_the_channel(session: Session) -> None:
    """Opt-in: set `MYCORD_SLACK_LIVE_CHANNEL` to a channel you own.

    Posting as the human is a real, visible act, so the test refuses to pick a channel
    itself. It deletes its own message, and leaves a marker in the text so a failed
    cleanup is obvious.
    """
    channel = os.environ.get("MYCORD_SLACK_LIVE_CHANNEL", "").strip()
    if not channel:
        pytest.skip("set MYCORD_SLACK_LIVE_CHANNEL to a test channel you own")
    marker = f"mycord-slack-skill live test {time.time():.0f}"
    posted = api.call(session, "chat.postMessage", {"channel": channel, "text": marker})
    if not posted.ok:
        pytest.fail(f"chat.postMessage failed: {posted.error}")
    ts = str(posted.data.get("ts") or "")
    assert ts
    try:
        history = api.call(
            session,
            "conversations.history",
            {"channel": channel, "limit": 5},
        )
        assert history.ok, history.error
        texts = [m.get("text") for m in history.data.get("messages") or []]
        assert marker in texts
    finally:
        api.call(session, "chat.delete", {"channel": channel, "ts": ts})
