"""CDP client for an Electron app, stdlib only.

Two details in here are load-bearing and cost real debugging time; both are
documented at their call sites:

1. ``/json/list`` does **not** show ``<webview>`` guests. The service pages inside
   Ferdium only appear through the ``Target`` domain.
2. With ``Target.attachToTarget {flatten: true}`` the resulting ``sessionId`` must
   ride on the **message envelope**. Passing it inside ``params`` yields
   ``Runtime.evaluate wasn't found``, which reads like a missing domain rather than
   a misplaced field.

The WebSocket implementation is deliberately minimal: CDP traffic here is one JSON
request/response per call plus events we mostly ignore.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import socket
import struct
import time
import urllib.error
import urllib.request
from typing import Any

__all__ = ["CdpError", "Browser", "Target", "discover_targets", "http_json"]


class CdpError(RuntimeError):
    """The app could not be driven over CDP."""


def http_json(url: str, timeout: float = 5.0) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return json.load(res)  # type: ignore[no-any-return]


def wait_for_cdp(port: int, timeout: float = 60.0) -> dict[str, object]:
    """Block until Chrome's DevTools endpoint answers, then return /json/version."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return http_json(f"http://127.0.0.1:{port}/json/version", timeout=1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            time.sleep(0.25)
    raise CdpError(f"no CDP endpoint on port {port} after {timeout:.0f}s: {last}")


class _WebSocket:
    """Minimal RFC 6455 client — enough for CDP, no third-party dependency."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        m = re.match(r"^ws://([^:/]+):(\d+)(/.*)$", url)
        if not m:
            raise CdpError(f"unusable websocket url: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self._sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        header = self._read_until(b"\r\n\r\n").decode("latin-1")
        if "101" not in header.split("\r\n")[0]:
            raise CdpError(f"websocket upgrade failed: {header.splitlines()[0]}")
        self._id = 0

    def _read_until(self, marker: bytes) -> bytes:
        buf = b""
        while marker not in buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CdpError("socket closed during handshake")
            buf += chunk
        return buf

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise CdpError("socket closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self._sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        masked = second & 0x80
        n = second & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(n)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self._id += 1
        want = self._id
        message: dict[str, object] = {"id": want, "method": method, "params": params or {}}
        # Detail #2: flatten-mode sessions travel on the envelope.
        if session_id:
            message["sessionId"] = session_id
        self._send_frame(0x1, json.dumps(message).encode())
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise CdpError("CDP closed the connection before responding")
            if opcode != 0x1:
                continue
            try:
                reply = json.loads(payload)
            except ValueError:
                continue
            if reply.get("id") != want:
                continue
            if "error" in reply:
                raise CdpError(f"{method}: {reply['error']}")
            return dict(reply.get("result") or {})

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send_frame(0x8, b"")
        with contextlib.suppress(OSError):
            self._sock.close()


class Target:
    """A CDP target (page or webview) as reported by ``Target.getTargets``."""

    def __init__(self, info: dict[str, object]) -> None:
        self.target_id = str(info["targetId"])
        self.type = str(info.get("type", ""))
        self.title = str(info.get("title") or "")
        self.url = str(info.get("url") or "")

    @property
    def host(self) -> str:
        m = re.match(r"^https?://([^/]+)", self.url)
        return m.group(1).lower() if m else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Target {self.type} {self.host or self.url[:40]}>"


class Browser:
    """Browser-level connection; owns target discovery and worker sessions."""

    def __init__(self, port: int, timeout: float = 60.0) -> None:
        info = wait_for_cdp(port, timeout)
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CdpError("/json/version had no webSocketDebuggerUrl")
        self.info = info
        self._ws = _WebSocket(str(ws_url))
        self._ws.request("Target.setDiscoverTargets", {"discover": True})
        self._sessions: dict[str, str] = {}
        self._closed = False

    def targets(self, kind: str | None = None) -> list[Target]:
        """Live targets. Detail #1: webviews exist only here, not in /json/list."""
        found = self._ws.request("Target.getTargets").get("targetInfos")
        if not isinstance(found, list):
            return []
        targets = [Target(t) for t in found if isinstance(t, dict)]
        return [t for t in targets if kind is None or t.type == kind]

    def webviews(self) -> list[Target]:
        return self.targets("webview")

    def find_webview(self, host_contains: str) -> Target | None:
        needle = host_contains.lower()
        for target in self.webviews():
            if needle in target.host or needle in target.url.lower():
                return target
        return None

    def attach(self, target: Target) -> TargetSession:
        sid = self._sessions.get(target.target_id)
        if sid is None:
            result = self._ws.request(
                "Target.attachToTarget", {"targetId": target.target_id, "flatten": True}
            )
            attached = result.get("sessionId")
            if not isinstance(attached, str):
                raise CdpError("attachToTarget returned no sessionId")
            sid = attached
            self._sessions[target.target_id] = sid
        return TargetSession(self._ws, sid, target)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._ws.close()


class TargetSession:
    """A flattened session pinned to one target."""

    def __init__(self, ws: _WebSocket, session_id: str, target: Target) -> None:
        self._ws = ws
        self.session_id = session_id
        self.target = target

    def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        return self._ws.request(method, params, session_id=self.session_id)

    def eval(self, expression: str, *, await_promise: bool = True, timeout_ms: int = 30_000) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "timeout": timeout_ms,
            },
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("text") if isinstance(details, dict) else None
            raise CdpError(f"page exception: {text}")
        inner = result.get("result")
        return inner.get("value") if isinstance(inner, dict) else None


def discover_targets(port: int) -> list[Target]:
    """One-shot target discovery without holding a browser connection."""
    browser = Browser(port)
    try:
        return browser.targets()
    finally:
        browser.close()
