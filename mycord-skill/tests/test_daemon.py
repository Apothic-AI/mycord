"""End-to-end tests over a real Unix socket against a live daemon.

The daemon is started offline (no token) so these stay hermetic while still
exercising the real socket, framing, and CLI paths.
"""

import asyncio
import json
from pathlib import Path

import pytest

from mycord_repl.cli import EXIT_ERROR, EXIT_OK, main
from mycord_repl.client import SessionClient, SessionNotRunning, is_running
from mycord_repl.protocol import decode, encode
from mycord_repl.session import ReplSession, serve


@pytest.fixture
async def socket_path(tmp_path: Path):
    """Run a daemon on a temporary socket and yield its path."""
    path = tmp_path / "repl.sock"
    session = ReplSession(None)

    server_task = asyncio.create_task(serve(session, path))
    for _ in range(100):
        if path.exists():
            break
        await asyncio.sleep(0.02)
    else:  # pragma: no cover - defensive
        server_task.cancel()
        pytest.fail("daemon did not bind its socket")

    try:
        yield path
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
        await session.close()
        if path.exists():
            path.unlink()


@pytest.mark.integration
async def test_socket_round_trip(socket_path: Path) -> None:
    reply = await SessionClient(socket_path).request({"op": "ping"})
    assert reply == {"ok": True, "result": "pong", "stdout": "", "error": None}


@pytest.mark.integration
async def test_eval_over_socket_persists_state(socket_path: Path) -> None:
    client = SessionClient(socket_path)
    assert (await client.request({"op": "eval", "code": "shared = 3"}))["ok"] is True
    reply = await client.request({"op": "eval", "code": "shared * 14"})
    assert reply["result"] == "42"


@pytest.mark.integration
async def test_status_over_socket_reports_offline(socket_path: Path) -> None:
    reply = await SessionClient(socket_path).request({"op": "status"})
    assert reply["result"]["connected"] is False


@pytest.mark.integration
async def test_socket_has_restrictive_permissions(socket_path: Path) -> None:
    assert socket_path.exists()
    assert socket_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.integration
async def test_raw_frame_is_valid_json(socket_path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(encode({"op": "eval", "code": "2 + 2"}))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5)
    finally:
        writer.close()

    assert json.loads(line)["result"] == "4"


@pytest.mark.integration
async def test_malformed_frame_does_not_kill_daemon(socket_path: Path) -> None:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(b"not json\n")
        await writer.drain()
        bad = decode(await asyncio.wait_for(reader.readline(), timeout=5))
        assert bad["ok"] is False

        # daemon survives and still answers
        writer.write(encode({"op": "ping"}))
        await writer.drain()
        good = decode(await asyncio.wait_for(reader.readline(), timeout=5))
        assert good["ok"] is True
    finally:
        writer.close()


@pytest.mark.integration
async def test_client_raises_when_socket_absent(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sock"
    with pytest.raises(SessionNotRunning):
        await SessionClient(missing).request({"op": "ping"})


@pytest.mark.unit
def test_is_running_false_for_absent_socket(tmp_path: Path) -> None:
    assert is_running(tmp_path / "missing.sock") is False


@pytest.mark.unit
def test_is_running_false_for_stale_socket(tmp_path: Path) -> None:
    # Socket file exists but nothing is listening behind it.
    stale = tmp_path / "stale.sock"
    stale.write_text("")
    assert is_running(stale) is False


@pytest.mark.integration
def test_cli_start_status_eval_stop_round_trip(tmp_path: Path) -> None:
    """Drive the real CLI the way an agent would.

    This runs the daemon as a detached subprocess, so `is_running` can use its
    blocking socket path without deadlocking an event loop.
    """
    socket_path = tmp_path / "cli.sock"
    base = ["--socket", str(socket_path)]

    assert main([*base, "start", "--no-connect"]) == EXIT_OK
    try:
        assert is_running(socket_path) is True
        assert main([*base, "status"]) == EXIT_OK
        assert main([*base, "eval", "6 * 7"]) == EXIT_OK
        assert main([*base, "eval", "kept = 'yes'"]) == EXIT_OK
        assert main([*base, "reset"]) == EXIT_OK
    finally:
        assert main([*base, "stop"]) == EXIT_OK

    assert not socket_path.exists()


@pytest.mark.integration
def test_start_is_idempotent_and_reports_failure(tmp_path: Path) -> None:
    socket_path = tmp_path / "dup.sock"
    base = ["--socket", str(socket_path)]

    assert main([*base, "start", "--no-connect"]) == EXIT_OK
    try:
        assert main([*base, "start", "--no-connect"]) == EXIT_ERROR
    finally:
        main([*base, "stop"])
