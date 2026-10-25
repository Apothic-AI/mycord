"""Session capture over the Chrome DevTools Protocol.

The two values a Slack client-token session needs are in two different places:

* **`d`** (the `xoxd-` cookie) is httpOnly, so `document.cookie` cannot see it, and it
  lives on `.slack.com` in the browser's cookie jar.
* **`xoxc-`** is *not* a cookie. The web client keeps it in `localStorage` as part of
  `localConfig_v2`, keyed by workspace — one entry per signed-in workspace, with the
  user id embedded in other keys of the same object. So reading cookies alone gets you
  half a session, which is the trap this module exists to avoid.

Everything else is worse than CDP: reading Chrome's SQLite cookie file needs the OS
keyring and misses localStorage entirely, and an extension needs a signed CRX. CDP is
the narrowest mechanism that sees both, so it is what `login` uses.

Only the standard library is used here: a minimal RFC 6455 client (the four frame types
CDP needs), a profile copy, and JSON. There is no new dependency for any of it — this is
adapted from `google-voice-skill/src/gvoice_skill/cdp.py`, which solved the same
profile-copy and cookie-jar problems for Google Voice.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redact import token_fingerprint

_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
)

#: Landing page for the capture. The Slack web client is what mints `xoxc` into
#: localStorage and what keeps `d` fresh, so the capture has to load the *app*, not
#: slack.com's marketing page. It touches no messaging state.
PROBE_URL = "https://app.slack.com/client"

#: Shape of a Slack client token. Used to find one inside `localConfig_v2` without
#: assuming the surrounding object's exact field names, which Slack has changed before.
_TOKEN_RE = re.compile(r"xoxc-[0-9]+-[0-9]+-[0-9]+-[0-9a-z]{64}")

#: Reads every signed-in workspace's token and identity out of the web client's own
#: localStorage. Returns names/shapes, and the tokens themselves (the caller never
#: prints them).
_LOCAL_CONFIG_JS = r"""
(() => {
  const raw = localStorage.getItem('localConfig_v2');
  if (!raw) return {present: false, teams: [], currentTeamId: null};
  let cfg;
  try { cfg = JSON.parse(raw); } catch (e) { return {present: true, parseError: String(e)}; }
  const teams = [];
  const entries = Object.entries(cfg.teams || {});
  for (const [id, t] of entries) {
    teams.push({
      id,
      name: t.name || '',
      domain: t.domain || '',
      url: t.url || '',
      token: t.token || '',
      enterpriseId: t.enterprise_id || null,
    });
  }
  return {present: true, teams, currentTeamId: cfg.currentTeamId || null};
})()
"""

#: Fallback: a page-level scan for token-shaped strings, used only if `localConfig_v2`
#: is missing. Slack has moved this value at least once, so a token-shaped scan is more
#: robust than a hard failure on one key name.
_SCAN_JS = r"""
(() => {
  const re = /xoxc-[0-9]+-[0-9]+-[0-9]+-[0-9a-z]{64}/g;
  const hits = [];
  const scan = (src, text) => {
    const found = text.match(re);
    if (found) for (const f of found) hits.push({src, token: f});
  };
  scan('html', document.documentElement.outerHTML);
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    scan('localStorage:' + k, localStorage.getItem(k) || '');
  }
  for (let i = 0; i < sessionStorage.length; i++) {
    const k = sessionStorage.key(i);
    scan('sessionStorage:' + k, sessionStorage.getItem(k) || '');
  }
  return hits;
})()
"""

#: Cookie name carrying the `xoxd-` session value on `.slack.com`.
SLACK_SESSION_COOKIE = "d"


class CdpError(RuntimeError):
    """Chrome could not be driven far enough to produce a session."""


class SessionCaptureError(RuntimeError):
    """The browser was reachable, but no usable Slack session was in it."""


@dataclass
class ChromeProfile:
    """A Chrome user-data directory, plus the profile inside it to use."""

    user_data_dir: Path
    profile_dir: str = "Default"

    @property
    def exists(self) -> bool:
        return (self.user_data_dir / self.profile_dir).is_dir()


@dataclass
class CapturedWorkspace:
    """One workspace's token, plus enough identity to name it without leaking the token."""

    team_id: str
    token: str
    name: str = ""
    domain: str = ""
    url: str = ""
    enterprise_id: str | None = None

    @property
    def fingerprint(self) -> str:
        return token_fingerprint(self.token)

    def to_public_dict(self) -> dict[str, Any]:
        """Everything except the token, for printing and for `session` output."""
        return {
            "team_id": self.team_id,
            "name": self.name,
            "domain": self.domain,
            "url": self.url,
            "enterprise_id": self.enterprise_id,
            "token": self.fingerprint,
        }


@dataclass
class CapturedSession:
    """What a capture produced: one or more workspaces, plus the shared `d` cookie."""

    cookie: str
    workspaces: list[CapturedWorkspace] = field(default_factory=list)
    cookie_expires: float | None = None
    cookie_host: str = ".slack.com"
    source: str = "localConfig_v2"

    def public(self) -> dict[str, Any]:
        return {
            "cookie": {
                "name": SLACK_SESSION_COOKIE,
                "host": self.cookie_host,
                "present": bool(self.cookie),
                "length": len(self.cookie),
                "expires": self.cookie_expires,
            },
            "workspaces": [w.to_public_dict() for w in self.workspaces],
            "source": self.source,
        }


def find_chrome() -> str:
    override = os.environ.get("MYCORD_SLACK_CHROME", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise CdpError(f"{override} (from MYCORD_SLACK_CHROME) is not an executable file")
    for name in _CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    raise CdpError(
        "no Chrome/Chromium binary found on PATH; set MYCORD_SLACK_CHROME to a browser binary"
    )


def default_profile() -> ChromeProfile:
    """The browser profile a human is actually signed in to Slack with.

    Deliberately reads the *real* user-data dir rather than a scratch one: the entire
    problem is that the session already exists somewhere a human logged in.
    """
    override = os.environ.get("MYCORD_SLACK_CHROME_PROFILE", "").strip()
    if override:
        path = Path(override).expanduser()
        return ChromeProfile(path.parent, path.name)
    base = Path(os.environ.get("CHROME_USER_DATA", "~/.config/google-chrome")).expanduser()
    return ChromeProfile(base, os.environ.get("MYCORD_SLACK_CHROME_PROFILE_DIR", "Default"))


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
                "already using that profile directory?"
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

    CDP traffic here is one JSON request/response per call plus a stream of events that
    are ignored. A full WebSocket library would add a dependency to a skill that
    otherwise needs none for capture.
    """

    def __init__(self, url: str, timeout: float = 60.0) -> None:
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

    def request(self, method: str, params: dict[str, object] | None = None) -> dict[str, Any]:
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
            result: dict[str, Any] = message.get("result") or {}
            return result

    def evaluate(self, expression: str) -> Any:
        """Run JS in the attached page and return its value."""
        result = self.request(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        details = result.get("exceptionDetails")
        if details:
            raise CdpError(f"page JS threw: {details}")
        inner = result.get("result") or {}
        return inner.get("value")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send_frame(0x8, b"")
        with contextlib.suppress(OSError):
            self._sock.close()


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
        # Headless Chrome with a restored session can be slow to open its first tab; ask
        # the browser target to create one rather than sleeping indefinitely.
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
    except subprocess.TimeoutExpired:  # pragma: no cover - stubborn browser
        print("warning: Chrome did not exit", file=sys.stderr)


def _stage_profile(profile: ChromeProfile, scratch: str) -> Path:
    """Copy the profile into `scratch` so the real one is never written to.

    Chrome refuses to enable CDP on a profile that another instance has locked, and
    mutating a live profile is rude regardless. `Local State` carries the cookie
    encryption key reference that makes the copied jar readable, so it comes along.

    Some cache directories churn while the copy runs and can leave a temporary tree
    behind; cleanup at the end is tolerant for that reason.
    """
    staged = Path(scratch) / "profile"
    shutil.copytree(
        profile.user_data_dir / profile.profile_dir,
        staged / profile.profile_dir,
        ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache"),
        symlinks=True,
    )
    local_state = profile.user_data_dir / "Local State"
    if local_state.exists():
        shutil.copy2(local_state, staged / "Local State")
    return staged


def _collect(
    ws: _TinyWebSocket, *, timeout: float, want_token: bool
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    """Poll the page until the cookie is present and, when wanted, a token is too.

    Slack's client is a single-page app that boots asynchronously: the cookie jar can be
    warm while `localConfig_v2` has not been written yet, and vice versa. Waiting on both
    is the difference between "a page loaded" and "the app is ready" — capturing early
    yields a jar that looks complete and fails with `invalid_auth`.
    """
    deadline = time.monotonic() + timeout
    cookies: list[dict[str, Any]] = []
    config: dict[str, Any] | None = None
    source = "localConfig_v2"
    navigated = False
    while time.monotonic() < deadline:
        if not navigated:
            with contextlib.suppress(CdpError, OSError):
                ws.request("Page.navigate", {"url": PROBE_URL})
            navigated = True
        raw = ws.request("Network.getAllCookies").get("cookies")
        if isinstance(raw, list):
            cookies = [c for c in raw if isinstance(c, dict)]
        if want_token:
            value = ws.evaluate(_LOCAL_CONFIG_JS)
            if isinstance(value, dict) and value.get("teams"):
                config = value
            elif isinstance(value, dict) and value.get("present"):
                # `localConfig_v2` exists but has no token yet; keep polling.
                config = None
        has_cookie = any(c.get("name") == SLACK_SESSION_COOKIE for c in cookies)
        has_token = bool(config and config.get("teams")) or not want_token
        if has_cookie and has_token:
            return cookies, config, source
        time.sleep(0.5)
    return cookies, config, source


def capture_session(
    profile: ChromeProfile | None = None,
    *,
    chrome: str | None = None,
    timeout: float = 60.0,
    copy_profile: bool = True,
    headless: bool = True,
) -> CapturedSession:
    """Start Chrome on `profile` and return the Slack session it holds.

    Raises `SessionCaptureError` when the browser is fine but no Slack session is
    present (the human has not signed in, or signed in to a different Chrome profile),
    and `CdpError` when the browser itself could not be driven.
    """
    profile = profile or default_profile()
    if not profile.exists:
        raise CdpError(
            f"no Chrome profile at {profile.user_data_dir / profile.profile_dir}; "
            "set MYCORD_SLACK_CHROME_PROFILE to the profile you use for Slack"
        )
    binary = chrome or find_chrome()

    scratch: str | None = None
    user_data = profile.user_data_dir
    if copy_profile:
        scratch = tempfile.mkdtemp(prefix="mycord-slack-profile-")
        user_data = _stage_profile(profile, scratch)
    elif _chrome_running():
        raise CdpError(
            "Chrome is already running on that profile directory; quit it, or let the "
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
    try:
        browser_ws = _wait_for_cdp(port, proc, timeout)
        page_ws = _pick_page_target(port, browser_ws, timeout)
        ws = _TinyWebSocket(page_ws)
        try:
            ws.request("Network.enable")
            ws.request("Page.enable")
            cookies, config, source = _collect(ws, timeout=timeout, want_token=True)
            if config is None:
                # The key may be absent (Slack moved it) rather than merely empty; a
                # token-shaped scan of the page is the fallback, and it records which
                # route found it so a stale assumption is visible in `session` output.
                hits = ws.evaluate(_SCAN_JS)
                if isinstance(hits, list) and hits:
                    tokens = [h for h in hits if isinstance(h, dict) and h.get("token")]
                    if tokens:
                        first = str(tokens[0]["token"])
                        config = {
                            "teams": [
                                {
                                    "id": "",
                                    "name": "",
                                    "domain": "",
                                    "url": "",
                                    "token": first,
                                    "enterpriseId": None,
                                }
                            ],
                            "currentTeamId": None,
                        }
                        source = str(tokens[0].get("src") or "page-scan")
        finally:
            ws.close()
    finally:
        _terminate(proc)
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    cookie_entry = next((c for c in cookies if c.get("name") == SLACK_SESSION_COOKIE), None)
    if cookie_entry is None:
        raise SessionCaptureError(
            "no `d` cookie on .slack.com in that Chrome profile — it is not signed in to "
            "Slack. Open Slack in that profile, sign in, and re-run `mycord-slack login`."
        )
    cookie = str(cookie_entry.get("value") or "")
    if not cookie.startswith("xoxd-"):
        raise SessionCaptureError(
            "the `d` cookie is present but is not an xoxd- session value (length "
            f"{len(cookie)}); the Slack client may not have finished booting. Re-run login, "
            "or use --headed to watch it happen."
        )
    expires_raw = cookie_entry.get("expires")
    expires = (
        float(expires_raw)
        if isinstance(expires_raw, (int, float)) and float(expires_raw) > 0
        else None
    )

    workspaces: list[CapturedWorkspace] = []
    if config:
        for team in config.get("teams") or []:
            if not isinstance(team, dict):
                continue
            token = str(team.get("token") or "")
            if not _TOKEN_RE.fullmatch(token):
                continue
            workspaces.append(
                CapturedWorkspace(
                    team_id=str(team.get("id") or ""),
                    token=token,
                    name=str(team.get("name") or ""),
                    domain=str(team.get("domain") or ""),
                    url=str(team.get("url") or ""),
                    enterprise_id=(str(team["enterpriseId"]) if team.get("enterpriseId") else None),
                )
            )
    if not workspaces:
        raise SessionCaptureError(
            "the browser has a Slack session cookie but no client token in "
            "localStorage; the Slack client did not finish booting. Re-run with --headed, "
            "or open a channel in that browser and try again."
        )
    return CapturedSession(
        cookie=cookie,
        workspaces=workspaces,
        cookie_expires=expires,
        source=source,
    )
