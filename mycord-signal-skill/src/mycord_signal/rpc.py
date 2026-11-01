"""Newline-delimited JSON-RPC 2.0 client for signal-cli's Unix-socket daemon.

**Use the socket, not ``--http``.** ``subscribeCallEvents`` and ``subscribeReceive`` are
inner classes of ``SignalJsonRpcDispatcherHandler``, which only the socket-channel handler
instantiates. The HTTP handler builds a plain ``SignalJsonRpcCommandHandler`` over
``Commands::getCommand``, where those methods do not exist — so call events cannot be
subscribed to over HTTP at all. Verified against the v0.14.9 sources.

Wire format, read from those sources and confirmed against the running daemon:

* request      ``{"jsonrpc":"2.0","method":"…","id":N,"params":{…}}``, one per line
* response     ``{"jsonrpc":"2.0","result":…,"id":N}`` or ``{"…","error":{"code":…}}``
* notification ``{"jsonrpc":"2.0","method":"receive"|"callEvent",
                   "params":{"subscription":N,"result":…}}`` — no ``id``

Because responses and notifications share one connection, this client runs a reader
thread and demultiplexes by id. A client that reads one blocking loop and treats the first
line as the answer deadlocks the moment an asynchronous event arrives mid-request.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

#: Application-level error codes signal-cli defines on top of JSON-RPC 2.0. Read from
#: ``SignalJsonRpcCommandHandler`` v0.14.9, not guessed.
ERROR_USER: Final = -1
ERROR_IO: Final = -3
ERROR_UNTRUSTED_KEY: Final = -4
ERROR_RATE_LIMIT: Final = -5
ERROR_CAPTCHA_REJECTED: Final = -6
ERROR_METHOD_NOT_FOUND: Final = -32601

#: Methods this client names explicitly. A typo then fails at import, not mid-session.
METHOD_VERSION: Final = "version"
METHOD_SUBSCRIBE_RECEIVE: Final = "subscribeReceive"
METHOD_SUBSCRIBE_CALL_EVENTS: Final = "subscribeCallEvents"
METHOD_RECEIVE: Final = "receive"
METHOD_SEND: Final = "send"
METHOD_LIST_CONTACTS: Final = "listContacts"
METHOD_LIST_GROUPS: Final = "listGroups"
METHOD_LIST_CALLS: Final = "listCalls"
METHOD_START_CALL: Final = "startCall"
METHOD_ACCEPT_CALL: Final = "acceptCall"
METHOD_HANGUP_CALL: Final = "hangupCall"
METHOD_REJECT_CALL: Final = "rejectCall"


class RpcError(Exception):
    """A JSON-RPC error response, carrying the server's code and message verbatim."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class TransportError(Exception):
    """The socket is gone, closed, or was never opened."""


@dataclass(frozen=True)
class Notification:
    """A server-initiated message: ``method`` plus its ``params``."""

    method: str
    params: dict[str, Any]

    @property
    def subscription(self) -> int | None:
        value = self.params.get("subscription")
        return value if isinstance(value, int) else None

    @property
    def result(self) -> Any:
        return self.params.get("result")


class RpcClient:
    """A synchronous JSON-RPC client over signal-cli's Unix socket.

    Thread model: one daemon reader thread, callers block on a condition. ``call`` is the
    only place that waits, and it waits on a predicate (``id in responses or closed``), so a
    notification arriving first just gets queued and the wait continues.
    """

    def __init__(self, socket_path: Path | str, timeout: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._responses: dict[int, dict[str, Any]] = {}
        self._response_ready = threading.Condition()
        self._notifications: queue.Queue[Notification] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()
        self._failure: str | None = None

    # ------------------------------------------------------------------ lifecycle

    def connect(self) -> None:
        """Open the socket and start the reader. Raises :class:`TransportError`."""
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.socket_path))
        except OSError as exc:
            sock.close()
            raise TransportError(f"cannot connect to {self.socket_path}: {exc}") from exc
        self._sock = sock
        self._reader = threading.Thread(
            target=self._read_loop, name="signal-rpc-reader", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        with self._response_ready:
            self._response_ready.notify_all()

    def __enter__(self) -> RpcClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self._sock is not None and not self._closed.is_set()

    # ------------------------------------------------------------------ transport

    def _read_loop(self) -> None:
        sock = self._sock
        assert sock is not None
        buffer = b""
        while not self._closed.is_set():
            try:
                chunk = sock.recv(65536)
            except OSError as exc:
                self._failure = f"socket read failed: {exc}"
                break
            if not chunk:
                self._failure = "server closed the connection"
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._dispatch(line)
        self._closed.set()
        with self._response_ready:
            self._response_ready.notify_all()

    def _dispatch(self, raw: bytes) -> None:
        line = raw.strip()
        if not line:
            return
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # signal-cli logs to stderr, so stdout should be pure JSON. If it is not,
            # dropping the line beats guessing at its meaning.
            return
        if not isinstance(message, dict):
            return
        if "id" in message and "method" not in message:
            identifier = message.get("id")
            if isinstance(identifier, int):
                with self._response_ready:
                    self._responses[identifier] = message
                    self._response_ready.notify_all()
                return
        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params")
            self._notifications.put(
                Notification(method=method, params=params if isinstance(params, dict) else {})
            )

    def _allocate_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        """Send one request and wait for its response. Raises :class:`RpcError`."""
        sock = self._sock
        if sock is None:
            raise TransportError("client is not connected; call connect() first")
        identifier = self._allocate_id()
        request: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": identifier}
        if params is not None:
            request["params"] = params
        try:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        except OSError as exc:
            raise TransportError(f"send failed: {exc}") from exc

        deadline = timeout if timeout is not None else self.timeout
        with self._response_ready:
            message = self._responses.pop(identifier, None)
            if message is None and not self._closed.is_set():
                self._response_ready.wait_for(
                    lambda: identifier in self._responses or self._closed.is_set(),
                    timeout=deadline,
                )
                message = self._responses.pop(identifier, None)
        if message is not None:
            return self._unwrap(message)
        if self._closed.is_set():
            raise TransportError(f"no response to {method}: {self._failure or 'transport closed'}")
        raise TransportError(f"timed out after {deadline:.0f}s waiting for {method}")

    @staticmethod
    def _unwrap(message: dict[str, Any]) -> Any:
        error = message.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            text = error.get("message")
            raise RpcError(
                code if isinstance(code, int) else -32603,
                text if isinstance(text, str) else "unknown error",
                error.get("data"),
            )
        return message.get("result")

    # ------------------------------------------------------------------ session

    def version(self) -> str:
        """Return the daemon's version.

        ``version`` answers ``{"version":"0.14.9"}``; a bare string is tolerated because the
        CLI form prints one.
        """
        result = self.call(METHOD_VERSION)
        if isinstance(result, dict):
            value = result.get("version")
            return value if isinstance(value, str) else json.dumps(result)
        return str(result)

    def subscribe_receive(self) -> int:
        return self._subscribe(METHOD_SUBSCRIBE_RECEIVE)

    def subscribe_call_events(self) -> int:
        """Subscribe to call events. **Must be called before any call is started.**

        ``CallManager.handleIncomingOffer`` returns early when no call-event listener is
        registered, so an incoming call is dropped outright — no ring, no tunnel — if nobody
        subscribed. This is the single most important ordering rule in the skill.
        """
        return self._subscribe(METHOD_SUBSCRIBE_CALL_EVENTS)

    def _subscribe(self, method: str) -> int:
        result = self.call(method)
        if not isinstance(result, int):
            raise RpcError(-32603, f"{method} returned {result!r}, expected a subscription id")
        return result

    # ------------------------------------------------------------------ events

    def poll(self, timeout: float = 0.0) -> Notification | None:
        """Return the next notification, or ``None`` if none arrives within ``timeout``."""
        try:
            if timeout <= 0:
                return self._notifications.get_nowait()
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def events(self, timeout: float = 0.5) -> Iterator[Notification]:
        """Yield notifications until the transport closes."""
        while not self._closed.is_set():
            notification = self.poll(timeout=timeout)
            if notification is not None:
                yield notification

    def call_events(self, timeout: float = 0.5) -> Iterator[dict[str, Any]]:
        """Yield only ``callEvent`` payloads."""
        for notification in self.events(timeout=timeout):
            if notification.method != "callEvent":
                continue
            result = notification.result
            if isinstance(result, dict):
                yield result

    def receive_events(self, timeout: float = 0.5) -> Iterator[dict[str, Any]]:
        """Yield only ``receive`` payloads (message envelopes)."""
        for notification in self.events(timeout=timeout):
            if notification.method != "receive":
                continue
            result = notification.result
            if isinstance(result, dict):
                yield result
