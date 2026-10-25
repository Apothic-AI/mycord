"""Cookie capture over the Chrome DevTools Protocol.

`document.cookie` cannot see HttpOnly cookies, and the Voice API needs them. Every
other route to them is worse: reading Chrome's SQLite `Cookies` file requires
decrypting with the keyring, yanking the Extensions API needs a signed CRX, and
Playwright's `context.cookies()` needs a Playwright-launched browser.

CDP is the narrowest thing that works: start Chrome against a profile directory,
connect to one page target, call `Network.getAllCookies`, done. One dependency worth
of code, no crypto, no extension install.

Only the standard library is used here — the WebSocket client is a minimal
implementation of the four frames this needs (text, ping, pong, close).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
)

# Two of Google's own endpoints are enough to prove the jar is live; neither is the
# Voice API, so this probe does not touch the account's messaging state.
PROBE_URL = "https://voice.google.com/"


class CdpError(RuntimeError):
    """Chrome could not be driven far enough to produce cookies."""


@dataclass
class ChromeProfile:
    """A Chrome user-data directory, plus the profile inside it to use."""

    user_data_dir: Path
    profile_dir: str = "Default"

    @property
    def exists(self) -> bool:
        return (self.user_data_dir / self.profile_dir).is_dir()


def find_chrome() -> str:
    for name in _CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    raise CdpError("no Chrome/Chromium binary found on PATH; set GVOICE_CHROME to a browser binary")


def default_profile() -> ChromeProfile:
    """The browser profile a human is actually signed in to.

    Deliberately reads the *real* user-data dir rather than a scratch one: the entire
    problem is that the session already exists somewhere a human logged in.
    """
    override = os.environ.get("GVOICE_CHROME_PROFILE")
    if override:
        return ChromeProfile(Path(override).expanduser().parent, Path(override).expanduser().name)
    base = Path(os.environ.get("CHROME_USER_DATA", "~/.config/google-chrome")).expanduser()
    return ChromeProfile(base, os.environ.get("GVOICE_CHROME_PROFILE_DIR", "Default"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_cdp(port: int, proc: subprocess.Popen[bytes], timeout: float) -> str:
    """Poll the /json/version endpoint until Chrome is listening."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    version_url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise CdpError(
                f"Chrome exited early (code {proc.returncode}); is another instance "
                f"already using that profile directory?"
            )
        try:
            with urllib.request.urlopen(version_url, timeout=1) as res:
                data = json.load(res)
            return str(data["webSocketDebuggerUrl"])
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            last = exc
            time.sleep(0.25)
    raise CdpError(f"Chrome did not expose CDP on port {port} within {timeout:.0f}s: {last}")


class _TinyWebSocket:
    """Just enough of RFC 6455 to talk to one CDP target.

    Deliberately small: CDP traffic here is one JSON request/response per call plus a
    stream of events we ignore. A full WebSocket library would add a dependency to a
    skill that otherwise needs none.
    """

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        match = re.match(r"^ws://([^:/]+):(\d+)(/.*)$", url)
        if not match:
            raise CdpError(f"unusable CDP websocket url: {url}")
        host, port, path = match.group(1), int(match.group(2)), match.group(3)
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode())
        header = self._read_until(b"\r\n\r\n").decode("latin-1")
        if "101" not in header.split("\r\n")[0]:
            raise CdpError(f"CDP websocket upgrade failed: {header.splitlines()[0]}")
        self._id = 0

    def _read_until(self, marker: bytes) -> bytes:
        buf = b""
        while marker not in buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CdpError("CDP socket closed during handshake")
            buf += chunk
        return buf

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise CdpError("CDP socket closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        masked = second & 0x80
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def request(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """Send one CDP command and wait for its matching response."""
        self._id += 1
        want = self._id
        self._send_frame(
            0x1, json.dumps({"id": want, "method": method, "params": params or {}}).encode()
        )
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:  # close
                raise CdpError("CDP closed the connection before responding")
            if opcode != 0x1:
                continue
            try:
                message = json.loads(payload)
            except ValueError:
                continue
            if message.get("id") != want:
                continue  # an event, not our response
            if "error" in message:
                raise CdpError(f"{method} failed: {message['error']}")
            return message.get("result") or {}

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send_frame(0x8, b"")
        with contextlib.suppress(OSError):
            self._sock.close()


def capture_cookies(
    profile: ChromeProfile | None = None,
    *,
    chrome: str | None = None,
    timeout: float = 45.0,
    copy_profile: bool = True,
    headless: bool = True,
) -> list[dict[str, object]]:
    """Start Chrome on `profile` and return every cookie it holds.

    Chrome refuses to enable CDP on the default profile while another instance is
    running, and mutating a live profile is rude regardless, so the default path copies
    the profile to a scratch directory first. `copy_profile=False` drives the real
    directory, which requires no other Chrome instance to be running.
    """
    profile = profile or default_profile()
    if not profile.exists:
        raise CdpError(
            f"no Chrome profile at {profile.user_data_dir / profile.profile_dir}; "
            "pass --profile-dir or set GVOICE_CHROME_PROFILE_DIR"
        )
    binary = chrome or os.environ.get("GVOICE_CHROME") or find_chrome()

    scratch: tempfile.TemporaryDirectory[str] | None = None
    user_data = profile.user_data_dir
    if copy_profile:
        # Local State holds the cookie-encryption key reference; the profile dir holds
        # the session. Copying both keeps the jar readable by the new instance.
        scratch = tempfile.TemporaryDirectory(prefix="gvoice-profile-")
        staged = Path(scratch.name) / "profile"
        shutil.copytree(
            profile.user_data_dir / profile.profile_dir,
            staged / profile.profile_dir,
            ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache"),
            symlinks=True,
        )
        local_state = profile.user_data_dir / "Local State"
        if local_state.exists():
            shutil.copy2(local_state, staged / "Local State")
        user_data = staged
    else:
        if _chrome_running():
            raise CdpError(
                "Chrome is already running on that profile directory; quit it or let the "
                "capture copy the profile (drop --no-copy-profile)"
            )

    port = _free_port()
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        PROBE_URL,
    ]
    if headless:
        args.insert(1, "--headless=new")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    ws_url: str | None = None
    try:
        browser_ws = _wait_for_cdp(port, proc, timeout)
        ws_url = _pick_page_target(port, browser_ws, timeout)
        socket_ws = _TinyWebSocket(ws_url)
        try:
            socket_ws.request("Network.enable")
            result = socket_ws.request("Network.getAllCookies")
            raw_cookies = result.get("cookies")
            if not isinstance(raw_cookies, list):
                raise CdpError("Network.getAllCookies returned no cookie list")
            return [c for c in raw_cookies if isinstance(c, dict)]
        finally:
            socket_ws.close()
    finally:
        _terminate(proc)
        if scratch is not None:
            scratch.cleanup()


def _pick_page_target(port: int, browser_ws: str, timeout: float) -> str:
    """Find a page target's websocket url, creating one if Chrome opened none."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as res:
                targets = json.load(res)
        except (urllib.error.URLError, OSError, ValueError):
            targets = []
        for target in targets:
            ws_url = target.get("webSocketDebuggerUrl")
            if target.get("type") == "page" and ws_url:
                return str(ws_url)
        # Headless Chrome with a restored session can be slow to open the first tab;
        # ask the browser target to create one rather than sleeping indefinitely.
        try:
            control = _TinyWebSocket(browser_ws)
            try:
                control.request("Target.createTarget", {"url": PROBE_URL})
            finally:
                control.close()
        except CdpError:
            pass
        time.sleep(0.5)
    raise CdpError("Chrome opened no page target to attach to")


def _chrome_running() -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (
                (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
            )
        except OSError:
            continue
        if (
            "google-chrome" in cmdline or "/chrome " in cmdline or "chromium" in cmdline
        ) and "--remote-debugging-port" not in cmdline:
            return True
    return False


def _terminate(proc: subprocess.Popen[bytes], grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("warning: Chrome did not exit", file=sys.stderr)
