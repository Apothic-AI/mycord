"""Tests for persistent REPL semantics and session status."""

import asyncio

import pytest

from mycord_irc_repl.config import IRCConfig
from mycord_irc_repl.session import ReplSession


@pytest.fixture
def session() -> ReplSession:
    return ReplSession(IRCConfig(nickname="test-agent"))


@pytest.mark.asyncio
async def test_execute_persists_globals_and_returns_trailing_expression(
    session: ReplSession,
) -> None:
    value, stdout = await session.execute("answer = 6 * 7\nanswer")
    next_value, _ = await session.execute("answer + 1")

    assert value == 42
    assert next_value == 43
    assert stdout == ""
    assert session.namespace["_"] == 43


@pytest.mark.asyncio
async def test_execute_supports_top_level_await(session: ReplSession) -> None:
    value, _ = await session.execute("import asyncio\nawait asyncio.sleep(0)\n'connected'")

    assert value == "connected"


@pytest.mark.asyncio
async def test_execute_captures_print(session: ReplSession) -> None:
    value, stdout = await session.execute("print('hello')\n5")

    assert value == 5
    assert stdout == "hello\n"


def test_reset_keeps_irc_bindings(session: ReplSession) -> None:
    session.namespace["agent_value"] = "temporary"

    session.reset()

    assert "agent_value" not in session.namespace
    assert session.namespace["client"] is session.client
    assert session.namespace["events"] is session.client.recent_events


@pytest.mark.asyncio
async def test_wait_event_returns_matching_new_event(session: ReplSession) -> None:
    waiter = asyncio.create_task(
        session.wait_event("channel_message", timeout=1.0, after=0)
    )
    await asyncio.sleep(0)
    session.client._record_event(
        "channel_message", channel="#test", nick="someone", text="hello"
    )

    event = await waiter

    assert event["channel"] == "#test"
    assert event["text"] == "hello"


@pytest.mark.asyncio
async def test_wait_event_times_out(session: ReplSession) -> None:
    with pytest.raises(TimeoutError, match="no IRC event"):
        await session.wait_event(timeout=0.001, after=0)


@pytest.mark.asyncio
async def test_dispatch_reports_status_without_secrets() -> None:
    session = ReplSession(
        IRCConfig(
            server="irc.example",
            nickname="test-agent",
            sasl_username="registered-user",
            sasl_password="must-not-be-returned",
        )
    )

    response = await session.dispatch({"op": "status"})

    assert response["ok"] is True
    assert response["result"]["server"] == "irc.example"
    assert "must-not-be-returned" not in repr(response)
    assert "registered-user" not in repr(response)


@pytest.mark.asyncio
async def test_dispatch_evaluates_code_and_rejects_unknown_operation(
    session: ReplSession,
) -> None:
    response = await session.dispatch({"op": "eval", "code": "1 + 1"})
    unknown = await session.dispatch({"op": "not-an-op"})

    assert response["result"] == "2"
    assert unknown["ok"] is False
    assert "unknown op" in unknown["error"]
