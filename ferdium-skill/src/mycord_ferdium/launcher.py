"""Launching, discovering and supervising Ferdium.

Ferdium is a Flatpak Electron app wrapping every configured service in its own
``<webview>``. Two facts shape this module:

* **A debug port can only be enabled at launch.** Ferdium never calls
  ``appendSwitch('remote-debugging-port')``, so ``ELECTRON_ARGS`` does nothing —
  the flag has to arrive as a raw argument. Every agent session therefore begins
  with a (re)launch of the app.
* **Headless means Xvfb, not a native headless mode.** Electron's headless
  switches do not keep webviews working, so a virtual display is the mechanism.

The launcher must keep the virtual display alive for as long as the app runs, so
the Xvfb lifespan is owned by this module rather than by a wrapping shell.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .cdp import Browser, CdpError, wait_for_cdp

APP_ID = "org.ferdium.Ferdium"
DEFAULT_PORT = 9411

#: Flatpak config root. Overridable for tests and for non-Flatpak installs.
CONFIG_ROOT = Path(
    os.environ.get(
        "MYCORD_FERDIUM_CONFIG", Path.home() / ".var/app" / APP_ID / "config" / "Ferdium"
    )
)


class FerdiumError(RuntimeError):
    """Ferdium is missing, unconfigured, or refused to start."""


@dataclass
class Service:
    """One configured service, as Ferdium lays it out on disk."""

    partition: str
    path: Path

    @property
    def id(self) -> str:
        return self.partition.removeprefix("service-")

    def indexeddb_hosts(self) -> list[str]:
        """Hosts whose IndexedDB this service owns, e.g. web.whatsapp.com."""
        out: list[str] = []
        idb = self.path / "IndexedDB"
        if not idb.is_dir():
            return out
        for entry in idb.iterdir():
            # Chromium names these "https_<host>_<n>.indexeddb.leveldb"
            name = entry.name
            if name.startswith(("https_", "http_")):
                host = name.split("_", 1)[1].rsplit("_", 2)[0]
                if host not in out:
                    out.append(host)
        return out


def installed() -> bool:
    return shutil.which("flatpak") is not None and _flatpak_has_app()


def _flatpak_has_app() -> bool:
    try:
        out = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return APP_ID in out.stdout.split()


def services(config_root: Path | None = None) -> list[Service]:
    """Configured services, discovered from the partition directories.

    Ferdium keeps no accounts/service registry in ``settings.json`` (it holds only
    preferences), so the partition directories are the honest signal. They are
    created when a service is added and survive restarts.
    """
    root = config_root or CONFIG_ROOT
    parts = root / "Partitions"
    if not parts.is_dir():
        return []
    found = []
    for entry in sorted(parts.glob("service-*")):
        if entry.is_dir():
            found.append(Service(partition=entry.name, path=entry))
    return found


def hosted_services(host_contains: str, config_root: Path | None = None) -> list[Service]:
    """Services whose IndexedDB mentions a host, e.g. ``whatsapp``."""
    needle = host_contains.lower()
    return [
        s for s in services(config_root) if any(needle in h.lower() for h in s.indexeddb_hosts())
    ]


class Running:
    """A supervised Ferdium process plus its display."""

    def __init__(
        self, port: int, proc: subprocess.Popen[bytes], display: subprocess.Popen[bytes] | None
    ):
        self.port = port
        self.proc = proc
        self.display = display

    @property
    def headed(self) -> bool:
        return self.display is None

    def stop(self, grace: float = 10.0) -> None:
        """Terminate the app, then the virtual display it was drawn on."""
        _terminate(self.proc, grace)
        if self.display is not None:
            _terminate(self.display, grace=5.0)

    def __enter__(self) -> Running:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def browser(self, timeout: float = 60.0) -> Browser:
        return Browser(self.port, timeout)


def _terminate(proc: subprocess.Popen[bytes], grace: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def launch(
    *,
    port: int = DEFAULT_PORT,
    headless: bool = True,
    wait: float = 60.0,
    extra_args: list[str] | None = None,
) -> Running:
    """Start Ferdium with a debug port and return a handle to it.

    ``headless=True`` draws the app on a virtual display. The webviews, CDP surface
    and page behaviour are identical either way; only the visibility differs.
    """
    if not installed():
        raise FerdiumError(
            f"{APP_ID} is not installed. Install it with `flatpak install flathub {APP_ID}`."
        )

    argv = [
        "flatpak",
        "run",
        APP_ID,
        f"--remote-debugging-port={port}",
        *(extra_args or []),
    ]

    display_proc: subprocess.Popen[bytes] | None = None
    env = dict(os.environ)
    if headless:
        if shutil.which("Xvfb") is None:
            raise FerdiumError(
                "Xvfb is required for headless mode (apt install xvfb), or pass headed=True."
            )
        display_proc = subprocess.Popen(
            ["Xvfb", "-displayfd", "1", "-screen", "0", "1600x1000x24"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert display_proc.stdout is not None
        number = display_proc.stdout.readline().decode().strip()
        if not number:
            _terminate(display_proc, 5)
            raise FerdiumError("Xvfb did not report a display number")
        env["DISPLAY"] = f":{number}"

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    running = Running(port, proc, display_proc)

    try:
        wait_for_cdp(port, timeout=wait)
    except CdpError as exc:
        running.stop()
        raise FerdiumError(f"Ferdium started but never exposed a debug port: {exc}") from exc
    return running


def is_running(port: int) -> bool:
    try:
        wait_for_cdp(port, timeout=1)
    except CdpError:
        return False
    return True


def wait_for_service(port: int, host_contains: str, timeout: float = 90.0) -> Browser:
    """Poll until a service webview for ``host_contains`` exists.

    Services hibernate: a sleeping service has no webview target until Ferdium
    wakes it. Callers that need a page must open the service in the UI first, or
    wait here for one that is already awake.
    """
    deadline = time.monotonic() + timeout
    browser = Browser(port)
    while time.monotonic() < deadline:
        if browser.find_webview(host_contains):
            return browser
        time.sleep(0.5)
    browser.close()
    raise FerdiumError(
        f"no {host_contains} webview appeared within {timeout:.0f}s. The service may be "
        "hibernating: open it in Ferdium once (or disable hibernation) and retry."
    )
