"""Tests for the daemon's Unix-socket request lifecycle."""

import asyncio
from pathlib import Path

import pytest

from mycord_irc_repl.client import SessionClient
from mycord_irc_repl.config import IRCConfig
from mycord_irc_repl.protocol import OP_SHUTDOWN
from mycord_irc_repl.session import ReplSession, serve


@pytest.mark.asyncio
async def test_socket_eval_status_reset_and_shutdown(tmp_path: Path) -> None:
    socket_path = tmp_path / "session.sock"
    session = ReplSession(IRCConfig(nickname="test-agent"))
    daemon = asyncio.create_task(serve(session, socket_path))

    deadline = asyncio.get_running_loop().time() + 1.0
    while not socket_path.exists() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert socket_path.exists()
    client = SessionClient(socket_path)

    first = await client.request({"op": "eval", "code": "counter = 40\ncounter + 2"})
    second = await client.request({"op": "eval", "code": "counter + 1"})
    status = await client.request({"op": "status"})
    reset = await client.request({"op": "reset"})
    shutdown = await client.request({"op": OP_SHUTDOWN})
    await asyncio.wait_for(daemon, timeout=1.0)

    assert first["result"] == "42"
    assert second["result"] == "41"
    assert status["result"]["nickname"] == "test-agent"
    assert reset["result"] == "namespace reset"
    assert shutdown["result"] == "shutting down"
    assert not socket_path.exists()
