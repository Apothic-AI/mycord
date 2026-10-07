"""Thin client for talking to a running mycord IRC REPL daemon."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from pathlib import Path
from typing import Any

from mycord_irc_repl.protocol import decode, encode


class SessionNotRunning(RuntimeError):
    """Raised when no session daemon is listening on the socket."""


class SessionClient:
    """Send requests to the daemon over its Unix domain socket."""

    def __init__(self, socket_path: Path | None = None) -> None:
        """Create a client that uses the daemon's default socket by default."""
        from mycord_irc_repl.session import default_socket_path

        self.socket_path = socket_path or default_socket_path()

    async def request(self, payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
        """Send one request frame and return the decoded response."""
        if not self.socket_path.exists():
            message = (
                f"no mycord IRC session at {self.socket_path}; "
                "run `mycord-irc-repl start` first"
            )
            raise SessionNotRunning(
                message
            )

        try:
            reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            message = f"session socket exists but nothing is listening: {exc}"
            raise SessionNotRunning(message) from exc

        try:
            writer.write(encode(payload))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        finally:
            writer.close()
            with contextlib.suppress(ConnectionResetError, BrokenPipeError):
                await writer.wait_closed()

        if not line:
            message = "session closed the connection without replying"
            raise SessionNotRunning(message)
        return decode(line)

def is_running(socket_path: Path | None = None) -> bool:
    """Return True if a daemon socket exists and accepts a ping."""
    from mycord_irc_repl.session import default_socket_path

    path = socket_path or default_socket_path()
    if not path.exists():
        return False
    try:
        reply = client_sync_request(path, {"op": "ping"}, timeout=5.0)
    except (SessionNotRunning, OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(reply.get("ok"))


def client_sync_request(path: Path, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Perform a blocking request against a socket path."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(encode(payload))

        buffer = b""
        while not buffer.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk

    return decode(buffer)


def default_log_path() -> Path:
    """Return the daemon log file path."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "mycord-irc-repl" / "session.log"
