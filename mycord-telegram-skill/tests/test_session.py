"""Tests for REPL execution semantics.

These exercise the namespace, the trailing-expression rule, top-level await,
stdout capture, and error reporting. None of them touch the network: the
session is constructed without a token, so the client never connects.
"""

from types import SimpleNamespace

import pytest

from mycord_telegram_repl.session import ReplSession


@pytest.fixture
def session() -> ReplSession:
    return ReplSession(None)


@pytest.mark.unit
async def test_trailing_expression_value_is_returned(session: ReplSession) -> None:
    value, _ = await session.execute("1 + 1")
    assert value == 2


@pytest.mark.unit
async def test_statement_only_returns_none(session: ReplSession) -> None:
    value, _ = await session.execute("x = 5")
    assert value is None


@pytest.mark.unit
async def test_globals_persist_between_executions(session: ReplSession) -> None:
    await session.execute("counter = 10")
    value, _ = await session.execute("counter * 2")
    assert value == 20


@pytest.mark.unit
async def test_multiple_statements_run_in_order(session: ReplSession) -> None:
    await session.execute("a = 1\nb = 2\nc = a + b")
    value, _ = await session.execute("c")
    assert value == 3


@pytest.mark.unit
async def test_last_value_is_bound_to_underscore(session: ReplSession) -> None:
    await session.execute("99")
    value, _ = await session.execute("_")
    assert value == 99


@pytest.mark.unit
async def test_print_output_is_captured(session: ReplSession) -> None:
    value, captured = await session.execute("print('hello')")
    assert captured == "hello\n"
    assert value is None


@pytest.mark.unit
async def test_print_and_trailing_value_both_returned(session: ReplSession) -> None:
    value, captured = await session.execute("print('side effect')\n7")
    assert captured == "side effect\n"
    assert value == 7


@pytest.mark.unit
async def test_top_level_await_is_supported(session: ReplSession) -> None:
    value, _ = await session.execute("import asyncio\nawait asyncio.sleep(0)\n'ok'")
    assert value == "ok"


@pytest.mark.unit
async def test_comprehension_spanning_lines(session: ReplSession) -> None:
    value, _ = await session.execute("total = sum(\n    i for i in range(5)\n)\ntotal")
    assert value == 10


@pytest.mark.unit
async def test_telethon_is_passthrough_available(session: ReplSession) -> None:
    import telethon

    value, _ = await session.execute("telethon.__name__")
    assert value == "telethon"
    assert telethon is not None


@pytest.mark.unit
async def test_client_and_helpers_are_preloaded(session: ReplSession) -> None:
    value, _ = await session.execute("[callable(wait_ready), hasattr(client, 'get_me')]")
    assert value == [True, True]


@pytest.mark.unit
async def test_awaitable_helper_detects_coroutines(session: ReplSession) -> None:
    import asyncio

    from mycord_telegram_repl.session import awaitable

    assert awaitable(5) is False

    pending = asyncio.sleep(0)
    try:
        assert awaitable(pending) is True
    finally:
        pending.close()


@pytest.mark.unit
async def test_reset_keeps_bindings_and_drops_globals(session: ReplSession) -> None:
    await session.execute("scratch = 1")
    session.reset()
    assert "scratch" not in session.namespace
    assert "client" in session.namespace
    assert "telethon" in session.namespace


@pytest.mark.unit
def test_offline_session_reports_disconnected(session: ReplSession) -> None:
    status = session.status()
    assert status["connected"] is False
    assert status["user"] is None
    assert status["authorized"] is False
    assert "client" in status["globals"]


@pytest.mark.unit
def test_authorized_requires_a_cached_identity(session: ReplSession) -> None:
    """An auth key alone must not read as authorized.

    is_user_authorized() is a coroutine, so a sync status() cannot ask
    Telegram; it used to call it anyway and treat the un-awaited coroutine as a
    truthy answer, reporting every session as authorized.
    """
    assert session.authorized is False

    session.client.session.auth_key = b"\x01" * 256
    assert session.authorized is False


@pytest.mark.unit
def test_authorized_once_identity_is_cached(session: ReplSession) -> None:
    """get_me() succeeding is what makes a session authorized."""
    session.client.session.auth_key = b"\x01" * 256
    session._ready = True
    session._me = SimpleNamespace(username="someone", id=4242)

    assert session.authorized is True
    status = session.status()
    assert status["authorized"] is True
    assert status["user"] == "@someone (id=4242)"


@pytest.mark.unit
async def test_eval_dispatch_reports_expression(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "6 * 7"})
    assert reply["ok"] is True
    assert reply["result"] == "42"


@pytest.mark.unit
async def test_eval_dispatch_reports_syntax_error(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "def ("})
    assert reply["ok"] is False
    assert "SyntaxError" in reply["error"]


@pytest.mark.unit
async def test_eval_dispatch_reports_runtime_error(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "1 / 0"})
    assert reply["ok"] is False
    assert "ZeroDivisionError" in reply["error"]


@pytest.mark.unit
async def test_eval_dispatch_times_out(session: ReplSession) -> None:
    reply = await session.dispatch(
        {"op": "eval", "code": "import asyncio\nawait asyncio.sleep(5)", "timeout": 0.05}
    )
    assert reply["ok"] is False
    assert "timed out" in reply["error"]


@pytest.mark.unit
async def test_eval_dispatch_rejects_empty_code(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "   "})
    assert reply["ok"] is False
    assert "missing" in reply["error"]


@pytest.mark.unit
async def test_eval_dispatch_rejects_unknown_op(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "nope"})
    assert reply["ok"] is False
    assert "unknown op" in reply["error"]


@pytest.mark.unit
async def test_status_and_ping_ops(session: ReplSession) -> None:
    assert (await session.dispatch({"op": "ping"}))["result"] == "pong"
    assert (await session.dispatch({"op": "status"}))["result"]["connected"] is False


@pytest.mark.unit
async def test_shutdown_op_acknowledges(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "shutdown"})
    assert reply["ok"] is True


@pytest.mark.unit
async def test_truncates_very_large_result(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "'x' * 50000"})
    assert reply["ok"] is True
    assert len(reply["result"]) < 30000
    assert "truncated" in reply["result"]


@pytest.mark.unit
async def test_truncates_very_large_stdout(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "print('y' * 50000)"})
    assert reply["ok"] is True
    assert "truncated" in reply["stdout"]


@pytest.mark.unit
async def test_short_output_is_not_truncated(session: ReplSession) -> None:
    reply = await session.dispatch({"op": "eval", "code": "'small'"})
    assert reply["result"] == "'small'"


@pytest.mark.unit
async def test_unawaitable_coroutine_warning_is_avoided(session: ReplSession) -> None:
    # Awaiting must happen exactly once; a double-await would warn.
    reply = await session.dispatch(
        {"op": "eval", "code": "import asyncio\nawait asyncio.sleep(0)\n'done'"}
    )
    assert reply["result"] == "'done'"
