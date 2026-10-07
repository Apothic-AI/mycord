"""Persistent REPL session daemon for driving an IRC client with pydle."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import logging
import os
import time
import traceback
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pydle

from mycord_irc_repl.config import DEFAULT_NICKNAME, IRCConfig, load_config
from mycord_irc_repl.protocol import (
    OP_EVAL,
    OP_PING,
    OP_RESET,
    OP_SHUTDOWN,
    OP_STATUS,
    decode,
    encode,
    failure,
    success,
)

logger = logging.getLogger("mycord_irc_repl.session")

DEFAULT_EVAL_TIMEOUT = 30.0
MAX_ECHO_BYTES = 20000
MAX_RECORDED_EVENTS = 500


def default_socket_path() -> Path:
    """Return the per-user socket path for the session daemon."""
    override = os.environ.get("MYCORD_IRC_REPL_SOCKET")
    if override:
        return Path(override)

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "mycord-irc.sock"

    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "mycord-irc-repl" / "s.sock"


class IRCClient(pydle.Client):  # type: ignore[misc]
    """pydle client that retains a bounded, in-memory event stream."""

    def __init__(self, config: IRCConfig, nickname: str) -> None:
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_RECORDED_EVENTS)
        self._event_sequence = 0
        self._event_callback: Callable[[], None] | None = None
        self._connected_event_recorded = False
        super().__init__(
            nickname,
            username=config.username or nickname.lower(),
            realname=config.realname or nickname,
            sasl_username=config.sasl_username,
            sasl_password=config.sasl_password,
        )

    @property
    def latest_event_sequence(self) -> int:
        """Return the sequence number of the newest recorded event."""
        return self._event_sequence

    def set_event_callback(self, callback: Callable[[], None]) -> None:
        """Set a callback used to wake REPL code waiting for a new event."""
        self._event_callback = callback

    def record_connected(self) -> None:
        """Record a connection transition once."""
        if not self._connected_event_recorded:
            self._record_event("connected")
            self._connected_event_recorded = True

    def _record_event(self, event_type: str, **fields: Any) -> None:
        self._event_sequence += 1
        event: dict[str, Any] = {
            "seq": self._event_sequence,
            "type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        event.update(fields)
        self.recent_events.append(event)
        if self._event_callback is not None:
            self._event_callback()

    async def on_connect(self) -> None:
        await super().on_connect()
        self.record_connected()

    async def on_disconnect(self, expected: bool) -> None:
        await super().on_disconnect(expected)
        self._connected_event_recorded = False
        self._record_event("disconnected", expected=expected)

    async def on_channel_message(self, target: str, by: str, message: str) -> None:
        await super().on_channel_message(target, by, message)
        self._record_event("channel_message", channel=target, nick=by, text=message)

    async def on_private_message(self, target: str, by: str, message: str) -> None:
        await super().on_private_message(target, by, message)
        self._record_event("private_message", target=target, nick=by, text=message)

    async def on_join(self, channel: str, user: str) -> None:
        await super().on_join(channel, user)
        self._record_event("join", channel=channel, nick=user)

    async def on_part(self, channel: str, user: str, message: str | None = None) -> None:
        await super().on_part(channel, user, message)
        self._record_event("part", channel=channel, nick=user, message=message)


class ReplSession:
    """A persistent Python REPL bound to one pydle client."""

    def __init__(self, config: IRCConfig | None = None) -> None:
        self.config = config or load_config()
        nickname = self.config.nickname or DEFAULT_NICKNAME
        self.client = IRCClient(self.config, nickname)
        self._ready = False
        self._connect_task: asyncio.Task[None] | None = None
        self._started_at = time.time()
        self._event_changed = asyncio.Event()
        self._eval_lock = asyncio.Lock()
        self.client.set_event_callback(self._event_changed.set)

        self.namespace: dict[str, Any] = {
            "pydle": pydle,
            "client": self.client,
            "session": self,
            "events": self.client.recent_events,
            "wait_ready": self.wait_ready,
            "wait_event": self.wait_event,
        }

    @property
    def connected(self) -> bool:
        """Whether pydle is connected and has completed IRC registration."""
        return bool(self.client.connected and self.client.registered)

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        """Wait for the IRC server to complete registration."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self.connected:
                self._ready = True
                self.client.record_connected()
                return True

            task = self._connect_task
            if task is not None and task.done() and not bool(self.client.connected):
                return False

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.05, remaining))

    async def start(self, *, wait: float = 30.0) -> bool:
        """Connect in the background; leave the REPL usable if offline."""
        if not self.config.server:
            logger.warning("no IRC server configured; session started offline")
            return False

        self.config.validate_for_connect()
        self._connect_task = asyncio.create_task(self._run_client())
        ready = await self.wait_ready(wait)
        if not ready:
            logger.warning("IRC registration did not complete within %ss", wait)
        return ready

    async def _run_client(self) -> None:
        """Open the server connection; pydle handles reads in its own task."""
        try:
            kwargs: dict[str, Any] = {
                "port": self.config.effective_port,
                "tls": self.config.tls,
            }
            if self.config.server_password:
                kwargs["password"] = self.config.server_password
            await self.client.connect(self.config.server, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("IRC client stopped:\n%s", traceback.format_exc())

    async def close(self) -> None:
        """Disconnect from IRC and cancel any pending connection attempt."""
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connect_task
        if bool(self.client.connected):
            await self.client.disconnect()
        self._ready = False

    def status(self) -> dict[str, Any]:
        """Summarize session health without exposing credentials."""
        registered = bool(self.client.registered)
        channels = sorted(str(channel) for channel in self.client.channels)
        return {
            "connected": self.connected,
            "registered": registered,
            "nickname": self.client.nickname
            if registered
            else (self.config.nickname or DEFAULT_NICKNAME),
            "server": self.config.server,
            "port": self.config.effective_port if self.config.server else None,
            "tls": self.config.tls,
            "channels": channels,
            "recent_events": len(self.client.recent_events),
            "uptime_seconds": round(time.time() - self._started_at, 3),
            "globals": sorted(key for key in self.namespace if not key.startswith("__")),
        }

    def reset(self) -> None:
        """Clear agent-defined globals while keeping the IRC bindings."""
        keep = {"pydle", "client", "session", "events", "wait_ready", "wait_event"}
        for key in [name for name in self.namespace if name not in keep]:
            del self.namespace[key]

    async def wait_event(
        self,
        event_type: str | None = None,
        *,
        timeout: float = 30.0,
        after: int | None = None,
    ) -> dict[str, Any]:
        """Wait for a new event, optionally filtering by its ``type``."""
        cursor = self.client.latest_event_sequence if after is None else after
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            self._event_changed.clear()
            for event in self.client.recent_events:
                if event["seq"] > cursor and (event_type is None or event["type"] == event_type):
                    return event

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                message = f"no IRC event received within {timeout}s"
                raise TimeoutError(message)
            try:
                await asyncio.wait_for(self._event_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                message = f"no IRC event received within {timeout}s"
                raise TimeoutError(message) from exc

    async def execute(self, code: str) -> tuple[Any, str]:
        """Execute Python in the persistent namespace with top-level await."""
        buffer = io.StringIO()
        value: Any = None
        tree = ast.parse(code, filename="<mycord-irc-repl>", mode="exec")
        body = tree.body
        trailing: ast.expr | None = None

        if body and isinstance(body[-1], ast.Expr):
            trailing = body[-1].value
            body = body[:-1]

        with contextlib.redirect_stdout(buffer):
            for node in body:
                compiled = compile(
                    ast.Module(body=[node], type_ignores=[]),
                    "<mycord-irc-repl>",
                    "exec",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                )
                # S307: running operator-supplied code against a live IRC
                # client is the explicit purpose of this local REPL.
                outcome = eval(compiled, self.namespace)
                if awaitable(outcome):
                    await outcome

            if trailing is not None:
                compiled = compile(
                    ast.Expression(body=trailing),
                    "<mycord-irc-repl>",
                    "eval",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                )
                value = eval(compiled, self.namespace)
                if awaitable(value):
                    value = await value

        if value is not None:
            self.namespace["_"] = value
        return value, buffer.getvalue()

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Route one decoded request frame to its handler."""
        op = request.get("op", OP_EVAL)

        if op == OP_PING:
            return success(result="pong")
        if op == OP_STATUS:
            return success(result=self.status())
        if op == OP_RESET:
            self.reset()
            return success(result="namespace reset")
        if op == OP_SHUTDOWN:
            return success(result="shutting down")
        if op != OP_EVAL:
            return failure(error=f"unknown op: {op!r}")

        code = request.get("code")
        if not isinstance(code, str) or not code.strip():
            return failure(error="missing 'code' field")

        timeout = float(request.get("timeout") or DEFAULT_EVAL_TIMEOUT)
        try:
            async with self._eval_lock:
                value, captured = await asyncio.wait_for(self.execute(code), timeout=timeout)
        except asyncio.TimeoutError:
            return failure(error=f"timed out after {timeout}s")
        except SyntaxError as exc:
            return failure(error=f"SyntaxError: {exc}")
        except Exception:
            return failure(error=traceback.format_exc())

        display = "" if value is None else _safe_repr(value)
        if len(display) > MAX_ECHO_BYTES:
            display = display[:MAX_ECHO_BYTES] + "\n... (truncated)"
        if len(captured) > MAX_ECHO_BYTES:
            captured = captured[:MAX_ECHO_BYTES] + "\n... (truncated)"
        return success(result=display, stdout=captured)


def awaitable(value: Any) -> bool:
    """Return True if ``value`` should be awaited before display."""
    return asyncio.iscoroutine(value) or isinstance(value, asyncio.Future)


def _safe_repr(value: Any) -> str:
    """Build a repr that never raises, falling back to the type name."""
    try:
        return repr(value)
    except Exception:  # pragma: no cover - defensive
        return f"<unrepresentable {type(value).__name__}>"


async def _handle_connection(
    session: ReplSession,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    shutdown: asyncio.Event,
) -> None:
    """Serve newline-delimited requests from one connected CLI client."""
    try:
        while line := await reader.readline():
            if not line.strip():
                continue
            shutdown_requested = False
            try:
                request = decode(line)
            except Exception as exc:
                response: dict[str, Any] = failure(error=f"bad request: {exc}")
            else:
                response = await session.dispatch(request)
                shutdown_requested = request.get("op") == OP_SHUTDOWN

            writer.write(encode(response))
            await writer.drain()
            if shutdown_requested:
                shutdown.set()
                break
    except (ConnectionResetError, BrokenPipeError):
        logger.debug("CLI disconnected")
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def serve(session: ReplSession, socket_path: Path) -> None:
    """Run the daemon until a shutdown request or termination signal."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    shutdown = asyncio.Event()
    server = await asyncio.start_unix_server(
        lambda reader, writer: _handle_connection(session, reader, writer, shutdown),
        path=str(socket_path),
    )
    socket_path.chmod(0o600)
    logger.info("listening on %s", socket_path)

    try:
        async with server:
            await shutdown.wait()
    finally:
        server.close()
        await server.wait_closed()
        await session.close()
        with contextlib.suppress(FileNotFoundError):
            socket_path.unlink()


def configure_logging(log_path: Path | None) -> None:
    """Configure daemon logging without writing IRC message contents."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.FileHandler(log_path, encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
