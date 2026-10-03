"""Thin client for talking to a running mycord-telegram REPL session daemon."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mycord_telegram_repl.protocol import decode, encode


class SessionNotRunning(RuntimeError):
    """Raised when no session daemon is listening on the socket."""


class SessionClient:
    """Send requests to the daemon over its Unix domain socket."""

    def __init__(self, socket_path: Path | None = None) -> None:
        """Create a client.

        Args:
            socket_path: Socket to connect to. Defaults to the daemon default.
        """
        from mycord_telegram_repl.session import default_socket_path

        self.socket_path = socket_path or default_socket_path()

    async def request(self, payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
        """Send one request frame and return the decoded response.

        Args:
            payload: Request object, e.g. ``{"op": "eval", "code": "1 + 1"}``.
            timeout: Seconds to wait for a reply.

        Returns:
            The decoded response object.

        Raises:
            SessionNotRunning: If the daemon is not listening.
        """
        if not self.socket_path.exists():
            message = (
                f"no mycord-telegram session at {self.socket_path}; "
                f"run `mycord-telegram-repl start` first"
            )
            raise SessionNotRunning(message)

        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        try:
            writer.write(encode(payload))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            message = f"session socket exists but nothing is listening: {exc}"
            raise SessionNotRunning(message) from exc
        finally:
            writer.close()
            with_suppress = getattr(writer, "wait_closed", None)
            if with_suppress is not None:
                try:
                    await writer.wait_closed()
                except (ConnectionResetError, BrokenPipeError):
                    pass

        if not line:
            message = "session closed the connection without replying"
            raise SessionNotRunning(message)
        return decode(line)


def is_running(socket_path: Path | None = None) -> bool:
    """Return True if a daemon socket exists and accepts a ping."""
    from mycord_telegram_repl.session import default_socket_path

    path = socket_path or default_socket_path()
    if not path.exists():
        return False
    try:
        reply = client_sync_request(path, {"op": "ping"}, timeout=5.0)
    except (SessionNotRunning, OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(reply.get("ok"))


def client_sync_request(path: Path, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Perform a blocking request against a socket path.

    Args:
        path: Socket to connect to.
        payload: Request object.
        timeout: Seconds to wait for a reply.

    Returns:
        The decoded response object.
    """
    import socket

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
    cache = (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mycord-telegram-repl"
    )
    return cache / "session.log"
