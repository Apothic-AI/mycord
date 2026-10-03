"""Persistent REPL session daemon for driving Telethon interactively.

The daemon owns a single long-lived :class:`telethon.TelegramClient` and
executes agent-supplied Python in a persistent namespace. Because the client
and the globals survive between evaluations, an agent can do this::

    eval "me = await client.get_me()"
    eval "dialogs = await client.get_dialogs(limit=10)"
    eval "[(d.name, d.unread_count) for d in dialogs]"

...and the second call sees the objects the first one created. That is the
"REPL-like" property: one login, many small steps.

Authentication is deliberately separate: see :mod:`mycord_telegram_repl.auth`
for the three non-bot login methods. Whichever one runs first persists a
``StringSession``, and every later start reads that string instead of
re-authenticating.

Communication is newline-delimited JSON over a Unix domain socket; see
:mod:`mycord_telegram_repl.protocol`.
"""

import ast
import asyncio
import contextlib
import io
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, cast

import telethon
from telethon import TelegramClient

from mycord_telegram_repl.auth import (
    AuthError,
    load_credentials,
)
from mycord_telegram_repl.protocol import (
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

logger = logging.getLogger("mycord_telegram_repl.session")

#: Default number of seconds a single ``eval`` may run before being abandoned.
DEFAULT_EVAL_TIMEOUT = 30.0

#: Bytes of response stdout echoed back to the agent.
MAX_ECHO_BYTES = 20000

#: Names bound into every snippet's namespace and preserved across ``reset``.
BASE_GLOBALS = frozenset({"telethon", "client", "session", "wait_ready"})

#: Inert placeholders so an offline session can still build a client object.
#: TelegramClient raises on empty credentials, and these are never sent: start()
#: replaces the client with a real one before any network call.
OFFLINE_API_ID = 1
OFFLINE_API_HASH = "0" * 32


def default_socket_path() -> Path:
    """Return the per-user socket path for the session daemon.

    Unix socket paths are limited to ~104 bytes, so the path is kept short by
    preferring ``XDG_RUNTIME_DIR`` and falling back to a hashed cache dir.
    """
    override = os.environ.get("MYCORD_TELEGRAM_REPL_SOCKET")
    if override:
        return Path(override)

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "mycord-telegram-repl.sock"

    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mycord-telegram-repl"
    return cache / "s.sock"


class ReplSession:
    """A persistent Telegram REPL bound to one connected client.

    Attributes:
        client: The connected :class:`telethon.TelegramClient`.
        namespace: Globals shared across every evaluated snippet.
        authorized: Whether Telegram reports the session as logged in.
    """

    def __init__(self, session_string: str | None = None, api_id: int | None = None,
                 api_hash: str | None = None) -> None:
        """Create a session.

        A session string and credentials are optional so the daemon can start
        offline and an agent can still inspect library objects. When a session
        string is absent the client is constructed on an in-memory session, so
        no ``*.session`` file is ever written behind the user's back.

        Args:
            session_string: A ``StringSession`` value, or ``None`` to start
                unauthorized.
            api_id: Telegram application id. Loaded from the environment when
                omitted.
            api_hash: Telegram application hash. Loaded from the environment
                when omitted.
        """
        self._session_string = session_string
        self._api_id = api_id
        self._api_hash = api_hash
        self._resolved_api_id: int | None = None
        self._resolved_api_hash: str | None = None
        self._ready = False
        self._connect_task: asyncio.Task[None] | None = None
        self._started_at = time.time()

        # MemorySession keeps the auth key out of the filesystem; the caller
        # owns persistence via the session string.
        from telethon.sessions import MemorySession, StringSession

        raw_session = StringSession(session_string) if session_string else MemorySession()
        # TelegramClient rejects empty credentials at construction, so offline
        # sessions get inert placeholders. They are never sent anywhere because
        # start() rebuilds the client with real credentials before connecting.
        self.client = TelegramClient(
            raw_session, api_id or OFFLINE_API_ID, api_hash or OFFLINE_API_HASH
        )

        self.namespace: dict[str, Any] = {
            "telethon": telethon,
            "client": self.client,
            "session": self,
            "wait_ready": self.wait_ready,
        }

    @property
    def authorized(self) -> bool:
        """Whether Telegram currently reports this session as logged in."""
        return bool(getattr(self.client, "is_user_authorized", None)) and bool(
            self.client.session.auth_key
        )

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        """Block until the client is connected and authorized.

        Args:
            timeout: Seconds to wait before giving up.

        Returns:
            True if the client became ready within ``timeout``.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready:
                return True
            await asyncio.sleep(0.05)
        return self._ready

    async def start(self, *, wait: float = 30.0) -> bool:
        """Connect to Telegram in the background.

        Missing credentials are non-fatal: the REPL stays usable so an agent
        can still inspect library objects offline.

        Args:
            wait: Seconds to wait for authorization before returning.

        Returns:
            True if the client reached an authorized state.
        """
        if not self._session_string:
            logger.warning("no session string supplied; session started offline")
            return False

        try:
            api_id, api_hash = self._resolve_credentials()
        except AuthError as exc:
            logger.warning("cannot authenticate: %s", exc)
            return False

        # Rebuild the client with real credentials now that they are known.
        from telethon.sessions import StringSession

        self._resolved_api_id, self._resolved_api_hash = api_id, api_hash
        self.client = TelegramClient(
            StringSession(self._session_string), api_id, api_hash
        )
        self.namespace["client"] = self.client

        self._connect_task = asyncio.create_task(self._run_client())
        ready = await self.wait_ready(wait)
        if not ready:
            logger.warning("telegram did not become ready within %ss", wait)
        return ready

    def _resolve_credentials(self) -> tuple[int, str]:
        """Return explicit credentials if given, else fall back to the environment."""
        if self._api_id and self._api_hash:
            return self._api_id, self._api_hash
        return load_credentials()

    async def _run_client(self) -> None:
        """Drive ``client.start`` and swallow its terminal exception."""
        session_string = self._session_string
        assert session_string is not None  # guarded by start()
        try:
            await self.client.start()
            self._ready = True
            me = await self.client.get_me()
            logger.info("telegram ready: user=%s id=%s", me, getattr(me, "id", None))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("telegram client stopped:\n%s", traceback.format_exc())

    async def close(self) -> None:
        """Disconnect the Telegram client and cancel any pending work."""
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connect_task
        if self.client.is_connected():
            await self.client.disconnect()
        self._ready = False

    def session_string(self) -> str | None:
        """Return the live session string, if the client holds an auth key."""
        if not getattr(self.client.session, "auth_key", None):
            return None
        try:
            return cast("str", self.client.session.save())
        except Exception:  # pragma: no cover - defensive
            return None

    def status(self) -> dict[str, Any]:
        """Summarise session health for the ``status`` opcode."""
        me = getattr(self.client, "_self_user", None) if self._ready else None
        return {
            "connected": self._ready,
            "authorized": self.authorized,
            "user": str(me) if me else None,
            "user_id": str(getattr(me, "id", None)) if me else None,
            "uptime_seconds": round(time.time() - self._started_at, 3),
            "globals": sorted(k for k in self.namespace if not k.startswith("__")),
        }

    def reset(self) -> None:
        """Clear agent-defined globals, keeping the Telegram bindings."""
        for key in [k for k in self.namespace if k not in BASE_GLOBALS]:
            del self.namespace[key]

    async def execute(self, code: str) -> tuple[Any, str]:
        """Execute a snippet in the persistent namespace.

        Mirrors a real REPL: if the final statement is an expression, its value
        is returned rather than discarded. Top-level ``await`` is supported, and
        anything the snippet prints is captured instead of leaking into the
        daemon's own stdout.

        Args:
            code: Python source to execute.

        Returns:
            Tuple of (display value, captured stdout).
        """
        buffer = io.StringIO()
        value: Any = None

        # `mode="exec"` throws away the value of a trailing expression, so split
        # a bare trailing expression off and evaluate it separately.
        tree = ast.parse(code, filename="<mycord-telegram-repl>", mode="exec")
        body = tree.body
        trailing: ast.expr | None = None

        if body and isinstance(body[-1], ast.Expr):
            trailing = body[-1].value
            body = body[:-1]

        with contextlib.redirect_stdout(buffer):
            for node in body:
                compiled = compile(
                    ast.Module(body=[node], type_ignores=[]),
                    "<mycord-telegram-repl>",
                    "exec",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                )
                # S307: eval is deliberate. This tool's entire purpose is to run
                # operator/agent-supplied Python against a live Telegram client;
                # the code is trusted at the same level as the operator's shell.
                outcome = eval(compiled, self.namespace)  # noqa: S307
                if awaitable(outcome):
                    await outcome

            if trailing is not None:
                compiled = compile(
                    ast.Expression(body=trailing),
                    "<mycord-telegram-repl>",
                    "eval",
                    flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                )
                value = eval(compiled, self.namespace)  # noqa: S307
                if awaitable(value):
                    value = await value

        if value is not None:
            self.namespace["_"] = value

        return value, buffer.getvalue()

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Route one decoded request frame to its handler.

        Args:
            request: The decoded request object.

        Returns:
            A response frame ready to be encoded.
        """
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
    session: ReplSession, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Serve newline-delimited requests from one connected client."""
    try:
        while line := await reader.readline():
            if not line.strip():
                continue
            try:
                request = decode(line)
            except Exception as exc:
                response: dict[str, Any] = failure(error=f"bad request: {exc}")
            else:
                response = await session.dispatch(request)
                if request.get("op") == OP_SHUTDOWN:
                    writer.write(encode(response))
                    await writer.drain()
                    await session.close()
                    raise SystemExit(0)

            writer.write(encode(response))
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        logger.debug("client disconnected")
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def serve(session: ReplSession, socket_path: Path) -> None:
    """Run the daemon until ``shutdown`` or a termination signal arrives.

    Args:
        session: The session to expose.
        socket_path: Unix socket to bind.
    """
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    server = await asyncio.start_unix_server(
        lambda r, w: _handle_connection(session, r, w), path=str(socket_path)
    )
    socket_path.chmod(0o600)
    logger.info("listening on %s", socket_path)

    async with server:
        await server.serve_forever()


def configure_logging(log_path: Path | None) -> None:
    """Send daemon logging to ``log_path`` so stdout stays clean.

    Args:
        log_path: Destination file, or ``None`` to log to stderr.
    """
    handlers: list[logging.Handler] = []
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
