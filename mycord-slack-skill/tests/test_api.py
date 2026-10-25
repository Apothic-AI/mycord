"""The passthrough: transport shape, verbatim errors, and no scope curation."""

from __future__ import annotations

import json

import pytest
from fake_slack import FakeSlack

from mycord_slack import api
from mycord_slack.session import Session

pytestmark = pytest.mark.unit


@pytest.fixture()
def fake() -> FakeSlack:
    with FakeSlack() as server:
        yield server  # type: ignore[misc]


def _call(fake: FakeSlack, session: Session, method: str, payload: dict[str, object]):  # type: ignore[no-untyped-def]
    return api.call(session, method, payload, base_url=fake.base_url)


def test_the_cookie_rides_along_with_the_token(fake: FakeSlack, session: Session) -> None:
    """The single fact the whole component rests on: token alone is invalid_auth."""
    _call(fake, session, "auth.test", {})
    recorded = fake.last()
    assert recorded.headers.get("cookie") == f"d={session.cookie}"
    assert recorded.headers.get("authorization", "").startswith("Bearer ")


def test_payload_is_sent_as_form_fields_not_a_json_body(fake: FakeSlack, session: Session) -> None:
    """`search.messages` rejects a JSON body with invalid_arguments; form encoding works."""
    _call(fake, session, "search.messages", {"query": "deploy failed", "count": 5})
    recorded = fake.last()
    assert recorded.form["query"] == "deploy failed"
    assert recorded.form["count"] == "5"


def test_nested_arguments_are_json_encoded_before_the_wire(
    fake: FakeSlack, session: Session
) -> None:
    """slack_sdk `str()`s nested values, which Slack reads as invalid_blocks_format.

    The structure must arrive as JSON — `true`, not `True`; double quotes, not single.
    """
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*bold*", "emoji": True}},
        {"type": "divider"},
    ]
    _call(fake, session, "chat.postMessage", {"channel": "C0123", "blocks": blocks})
    raw = fake.last().form["blocks"]
    assert json.loads(raw) == blocks
    assert "True" not in raw and "'" not in raw


def test_an_error_code_is_returned_verbatim(fake: FakeSlack, session: Session) -> None:
    """No translation: Slack's word for it, and `ok` false so a caller can branch."""
    fake.responses["admin.users.list"] = {"ok": False, "error": "admin_unauthorized"}
    result = _call(fake, session, "admin.users.list", {"team_id": "T0123"})
    assert result.ok is False
    assert result.error == "admin_unauthorized"


def test_slack_explanations_are_carried_too(fake: FakeSlack, session: Session) -> None:
    """`response_metadata.messages` often names the real problem; it must not be dropped."""
    fake.responses["search.messages"] = {
        "ok": False,
        "error": "invalid_arguments",
        "response_metadata": {"messages": ["[ERROR] missing required field: query"]},
    }
    result = _call(fake, session, "search.messages", {})
    assert result.error == "invalid_arguments"
    assert result.detail == ["[ERROR] missing required field: query"]


@pytest.mark.parametrize(
    "method",
    [
        "conversations.list",
        "conversations.replies",
        "chat.postMessage",
        "chat.update",
        "files.upload",
        "usergroups.list",
        "admin.users.list",
        "client.counts",
        "some.method.slack.has.not.published",
    ],
)
def test_any_dotted_method_is_callable(fake: FakeSlack, session: Session, method: str) -> None:
    """A curated verb list would re-impose the scope limits xoxc exists to avoid."""
    assert _call(fake, session, method, {}).ok is True
    assert fake.last().method == method


def test_parse_payload_accepts_an_object() -> None:
    assert api.parse_payload('{"channel":"C1"}') == {"channel": "C1"}


def test_parse_payload_defaults_to_empty() -> None:
    assert api.parse_payload("") == {}
    assert api.parse_payload("  ") == {}


@pytest.mark.parametrize("raw", ["{bad", "[1,2]", '"text"'])
def test_parse_payload_rejects_everything_but_an_object(raw: str) -> None:
    """A JSON typo is a usage error, and must not be reported as a session problem."""
    with pytest.raises(ValueError):
        api.parse_payload(raw)


def test_verify_uses_auth_test(fake: FakeSlack, session: Session) -> None:
    fake.responses["auth.test"] = {
        "ok": True,
        "team": "Test Workspace",
        "user": "tester",
        "team_id": "T0TEST",
    }
    result = api.verify(session, base_url=fake.base_url)
    assert result.ok
    assert result.data["team"] == "Test Workspace"
    assert fake.last().method == "auth.test"


def test_no_base_url_means_slacks_own_default(session: Session, monkeypatch) -> None:
    """Leaving base_url unset is what routes an xoxc token to its own workspace."""
    monkeypatch.delenv("MYCORD_SLACK_API_BASE", raising=False)
    client = api.client_for(session)
    assert "slack.com/api" in client.base_url


def test_base_url_can_be_overridden_for_tests(session: Session, monkeypatch) -> None:
    monkeypatch.setenv("MYCORD_SLACK_API_BASE", "http://127.0.0.1:9/api/")
    client = api.client_for(session)
    assert client.base_url == "http://127.0.0.1:9/api/"
