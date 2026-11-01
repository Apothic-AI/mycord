"""An in-process fake signal-cli JSON-RPC daemon on a real Unix socket.

It is a real socket server, not a mock object, because the thing under test *is* the
transport: line framing, notification demultiplexing, and error unwrapping. A fake
``RpcClient`` subclass would test nothing but itself.

The wire shapes below are copied from signal-cli v0.14.9's own sources — a response is
``{"jsonrpc":"2.0","result":…,"id":N}``, a notification is
``{"jsonrpc":"2.0","method":"callEvent","params":{"subscription":N,"result":{…}}}`` — so a
test that passes here is evidence about the real framing, not about a convenient fiction.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Everything a handler may return: a plain result, an error tuple, or a callable that
#: receives the whole request (used by tests that need to inspect params).
Handler = Callable[[dict[str, Any]], Any]


@dataclass
class FakeSignalDaemon:
    """A minimal JSON-RPC server speaking the signal-cli dialect."""

    handlers: dict[str, Handler] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    connection_closed: threading.Event = field(default_factory=threading.Event)
    _server: socket.socket | None = None
    _thread: threading.Thread | None = None
    _client: socket.socket | None = None
    _path: Path | None = None

    # ------------------------------------------------------------------ lifecycle

    def listen(self, socket_path: Path) -> Path:
        self._path = socket_path
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        server.listen(1)
        self._server = server
        self._thread = threading.Thread(target=self._serve, name="fake-signal", daemon=True)
        self._thread.start()
        return socket_path

    def _serve(self) -> None:
        server = self._server
        assert server is not None
        try:
            client, _ = server.accept()
        except OSError:
            return
        self._client = client
        buffer = b""
        with client:
            while True:
                try:
                    chunk = client.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    self._handle(client, line)
        self.connection_closed.set()

    def _handle(self, client: socket.socket, raw: bytes) -> None:
        request = json.loads(raw.decode("utf-8"))
        self.requests.append(request)
        method = request.get("method")
        handler = self.handlers.get(method) if isinstance(method, str) else None
        identifier = request.get("id")

        if handler is None:
            self.send(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": -32601, "message": f"Method not implemented: {method}"},
                },
            )
            return

        try:
            outcome = handler(request)
        except FakeError as exc:
            self.send(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": exc.code, "message": exc.message, "data": exc.data},
                },
            )
            return

        self.send(client, {"jsonrpc": "2.0", "id": identifier, "result": outcome})

    @staticmethod
    def send(client: socket.socket, message: dict[str, Any]) -> None:
        client.sendall((json.dumps(message) + "\n").encode("utf-8"))

    def notify(self, method: str, subscription: int, result: Any) -> None:
        """Push a notification to the connected client (queues it for that connection)."""
        client = self._client
        if client is None:
            raise RuntimeError("no client connected")
        self.send(
            client,
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": {"subscription": subscription, "result": result},
            },
        )

    def push_call_event(self, subscription: int, event: dict[str, Any]) -> None:
        self.notify("callEvent", subscription, event)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._client.close()
            self._client = None
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._path is not None and self._path.exists():
            self._path.unlink()


class FakeError(Exception):
    """Raise inside a handler to emit a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@contextmanager
def fake_daemon(
    socket_path: Path, handlers: dict[str, Handler] | None = None
) -> Iterator[FakeSignalDaemon]:
    daemon = FakeSignalDaemon(handlers=handlers or {})
    daemon.listen(socket_path)
    try:
        yield daemon
    finally:
        daemon.close()


#: The handlers a real *single-account* daemon answers. Kept as a factory so each test gets
#: fresh mutable state.
#:
#: Notable omission, and it is deliberate: ``listAccounts`` is **not** here. On a real
#: single-account daemon it answers ``-32601 Method not implemented`` (verified live), because
#: ``ListAccountsCommand`` is a ``JsonRpcMultiLocalCommand`` and the single-account handler
#: only dispatches single/multi commands. A fake that answered it would hide the reason
#: ``doctor`` reads ``accounts.json`` instead.
def default_handlers(version: str = "0.14.9") -> dict[str, Handler]:
    subscription_ids = {"receive": 0, "callEvent": 1}

    def version_handler(_request: dict[str, Any]) -> dict[str, str]:
        return {"version": version}

    def subscribe_receive(_request: dict[str, Any]) -> int:
        return subscription_ids["receive"]

    def subscribe_call_events(_request: dict[str, Any]) -> int:
        return subscription_ids["callEvent"]

    def empty_list(_request: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def receive(_request: dict[str, Any]) -> list[dict[str, Any]]:
        # A daemon that is not already receiving: answers with the envelopes it got.
        return []

    return {
        "version": version_handler,
        "subscribeReceive": subscribe_receive,
        "subscribeCallEvents": subscribe_call_events,
        "listCalls": empty_list,
        "listContacts": empty_list,
        "listGroups": empty_list,
        "receive": receive,
    }


#: A ``callEvent`` payload shaped exactly like ``JsonCallEvent`` in v0.14.9, including the
#: ``inputDeviceName``/``outputDeviceName`` that only appear once the tunnel is ready.
CALL_EVENT_RINGING: dict[str, Any] = {
    "callId": 1234567890123456789,
    "state": "RINGING_OUTGOING",
    "number": "+15550100",
    "isOutgoing": True,
}

CALL_EVENT_CONNECTED: dict[str, Any] = {
    "callId": 1234567890123456789,
    "state": "CONNECTED",
    "number": "+15550100",
    "isOutgoing": True,
    "inputDeviceName": "unix:/tmp/user/1000/signal-call-1234567890123456789.sock",
    "outputDeviceName": "unix:/tmp/user/1000/signal-call-1234567890123456789.sock",
}
