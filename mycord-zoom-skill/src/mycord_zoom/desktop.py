"""Driving the Zoom desktop client: deep links, launch, status, leave.

Zoom publishes no local API for the ordinary client — no socket, no CLI verbs, and the
`cefIpcChannel` FIFOs are proprietary and unframed (see the determination). The one
scriptable surface is the URL scheme the client already registers for itself, which is
also the most no-app-friendly surface imaginable: a join needs nothing but the meeting
URL a human would have clicked.

Verified on this host (Zoom 6.5.11): `/usr/bin/zoom <url>` hands the URL verbatim to
`/opt/zoom/zoom`, and the client logs it back as ``cmd line: zoommtg://…``. So this module
builds the deep link, launches the client the same way a browser would, and reads state
back out of the process table and the window list.

What is *not* verified here: how a real meeting window titles itself, and therefore
whether `status` can reliably say "in a meeting" rather than "a Zoom window exists". Now
that the client is installed and signed in, that is the single remaining runtime check
(see README "Unverified"). The detection code is written to report what it actually sees
rather than to assert a meeting it cannot prove.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .errors import DesktopError
from .tools import Runner, run_command, which_optional

#: Schemes the desktop client registers (`/usr/share/applications/Zoom.desktop`).
SCHEMES = ("zoommtg", "zoomus")

#: Binary names that mean "the Zoom client is running". The Mac/Windows names are listed
#: so `status` does not lie on another platform, and because the Flatpak wrapper shows up
#: as `/app/extra/zoom/zoom`.
PROCESS_NAMES = ("zoom", "zoom.us", "Zoom.exe", "zoom.real")

_MEETING_ID = re.compile(r"^(?P<id>\d{9,11})$")
#: Anything that looks like a Zoom web link. Hosts are checked because a *non*-Zoom host
#: with /j/<digits> must not be silently turned into a Zoom launch.
_ZOOM_HOST = re.compile(r"(^|\.)zoom(\.us|gov\.com|\.com\.cn|\.de|\.fr|\.co\.uk)$", re.I)


@dataclass(frozen=True)
class MeetingLink:
    """A parsed join/start target, plus the deep link built from it."""

    meeting_id: str
    action: str
    password: str | None
    host: str
    deep_link: str

    @property
    def redacted_deep_link(self) -> str:
        """The deep link with any password masked.

        A meeting password is a credential: whoever has it can sit in the room. It is needed
        to build the link, so it is held here, but it must not appear in anything printed,
        logged or handed to an agent as JSON. Use `deep_link` only where the real value is
        actually required (the launch argv).
        """
        return re.sub(r"(pwd=)[^&\s]+", r"\1***", self.deep_link)

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_id": self.meeting_id,
            "action": self.action,
            "password_present": self.password is not None,
            "host": self.host,
            "deep_link": self.redacted_deep_link,
        }


@dataclass(frozen=True)
class Window:
    """A top-level window as `wmctrl` reports it."""

    window_id: str
    desktop: int
    host: str
    title: str


@dataclass(frozen=True)
class DesktopStatus:
    """What `mycord-zoom status` reports. Every field is observed, none inferred."""

    client_running: bool
    client_paths: tuple[str, ...]
    client_pids: tuple[int, ...]
    windows: tuple[Window, ...]
    meeting_window: Window | None
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "client_running": self.client_running,
            "client_paths": list(self.client_paths),
            "client_pids": list(self.client_pids),
            "windows": [
                {"id": w.window_id, "desktop": w.desktop, "title": w.title} for w in self.windows
            ],
            "meeting_window": (
                {"id": self.meeting_window.window_id, "title": self.meeting_window.title}
                if self.meeting_window
                else None
            ),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- link parsing


def find_zoom_binary() -> str | None:
    """The client launcher, if one is installed.

    `zoom` on Debian is a symlink to `/opt/zoom/ZoomLauncher`; the Flatpak build shows up
    elsewhere. Either is fine — both accept a URL argument.
    """
    for candidate in ("zoom", "zoom.us"):
        found = which_optional(candidate)
        if found:
            return found
    extra = Path("/app/extra/zoom/zoom")  # Flatpak layout seen on this host
    if extra.exists():
        return str(extra)
    return None


def parse_meeting(target: str, *, action: str = "join") -> MeetingLink:
    """Turn a meeting URL, a bare meeting id, or a deep link into a `MeetingLink`.

    Accepted forms, all of which a human could paste:

    * ``https://zoom.us/j/1234567890?pwd=abc``
    * ``https://us05web.zoom.us/j/1234567890?pwd=abc``
    * ``https://zoom.us/wc/1234567890/join``
    * ``https://zoom.us/s/1234567890`` (start)
    * ``zoommtg://zoom.us/join?confno=1234567890&pwd=abc``
    * ``1234567890``

    An unrecognised host raises rather than being coerced, because launching the client
    with a made-up meeting id is worse than refusing.
    """
    text = target.strip()
    if not text:
        raise DesktopError("empty meeting target")

    if text.lower().startswith(("http://", "https://")):
        return _parse_web_url(text, action=action)
    if text.lower().startswith(tuple(f"{scheme}://" for scheme in SCHEMES)):
        return _parse_deep_link(text)
    if _MEETING_ID.match(text):
        return _build(text, action, None, "zoom.us")
    raise DesktopError(
        f"cannot read {target!r} as a Zoom meeting: expected a zoom.us meeting URL, a "
        f"zoommtg:// link, or a 9-11 digit meeting id"
    )


def _parse_web_url(text: str, *, action: str) -> MeetingLink:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(text)
    host = parsed.hostname or ""
    if not _ZOOM_HOST.search(host):
        raise DesktopError(f"{host or text!r} is not a Zoom host")
    parts = [p for p in parsed.path.split("/") if p]
    # /j/<id>, /wc/<id>/join, /s/<id>, /my/<id>
    meeting_id: str | None = None
    route = parts[0].lower() if parts else ""
    if route in {"j", "s", "wc", "my", "w"} and len(parts) >= 2:
        meeting_id = parts[1]
    if meeting_id is None or not _MEETING_ID.match(meeting_id):
        raise DesktopError(f"no meeting id in {text!r}")
    resolved_action = "start" if route in {"s"} else action
    query = parse_qs(parsed.query)
    raw_password = query.get("pwd")
    password = raw_password[0] if raw_password else None
    return _build(meeting_id, resolved_action, password, host)


def _parse_deep_link(text: str) -> MeetingLink:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    raw_confno = query.get("confno")
    path_parts = [p for p in parsed.path.split("/") if p]
    meeting_id = (raw_confno[0] if raw_confno else None) or (path_parts[-1] if path_parts else None)
    if not meeting_id or not _MEETING_ID.match(meeting_id):
        raise DesktopError(f"no confno in {text!r}")
    raw_action = query.get("action")
    action = raw_action[0] if raw_action else "join"
    if action not in {"join", "start"}:
        action = "join"
    raw_password = query.get("pwd")
    password = raw_password[0] if raw_password else None
    host = parsed.hostname or "zoom.us"
    return _build(meeting_id, action, password, host)


def _build(meeting_id: str, action: str, password: str | None, host: str) -> MeetingLink:
    """Assemble the deep link. Query order follows the links Zoom itself emits."""
    query = [f"action={action}", f"confno={meeting_id}"]
    if password:
        query.append(f"pwd={password}")
    deep_link = f"zoommtg://{host}/{action}?" + "&".join(query)
    return MeetingLink(
        meeting_id=meeting_id,
        action=action,
        password=password,
        host=host,
        deep_link=deep_link,
    )


# --------------------------------------------------------------------------- launch


@dataclass(frozen=True)
class LaunchPlan:
    """The argv that will be run, so it can be inspected (and tested) without running it."""

    argv: tuple[str, ...]
    deep_link: str

    def to_dict(self) -> dict[str, object]:
        # The argv is what a caller inspects to debug a launch; its password-bearing link
        # is masked for the same reason as `MeetingLink.to_dict`.
        masked = tuple(
            re.sub(r"(pwd=)[^&\s]+", r"\1***", arg) if "pwd=" in arg else arg for arg in self.argv
        )
        return {
            "argv": list(masked),
            "deep_link": re.sub(r"(pwd=)[^&\s]+", r"\1***", self.deep_link),
        }


def launch_plan(link: MeetingLink, *, binary: str | None = None) -> LaunchPlan:
    """Decide how to hand `link` to the client.

    Preference order is deliberate: running the client binary directly is what was
    verified end to end here, and it avoids a browser or `xdg-open` in the middle. A
    browser is only a fallback for platforms where the binary is not on `PATH`.
    """
    resolved = binary or find_zoom_binary()
    if resolved:
        return LaunchPlan(argv=(resolved, link.deep_link), deep_link=link.deep_link)
    opener = which_optional("xdg-open")
    if opener:
        return LaunchPlan(argv=(opener, link.deep_link), deep_link=link.deep_link)
    raise DesktopError(
        "neither the Zoom client nor xdg-open is on PATH; install Zoom or open the link "
        "yourself: " + shlex.quote(link.deep_link)
    )


def launch(link: MeetingLink, *, runner: Runner = run_command) -> LaunchPlan:
    """Start the client with the deep link and return what was run.

    The client detaches and keeps running, so this returns as soon as the launcher has
    accepted the URL. It deliberately does not wait for a meeting window: joining is
    asynchronous and a slow join is not a failure.
    """
    plan = launch_plan(link)
    result = runner(list(plan.argv), timeout=60.0)
    if not result.ok:
        raise DesktopError(f"{plan.argv[0]} rejected the link: {result.message}")
    return plan


# --------------------------------------------------------------------------- status


def _zoom_pids() -> tuple[int, ...]:
    """PIDs whose executable or command name looks like the Zoom client."""
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():  # not Linux: no /proc to read, so no claim is made
        return ()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(errors="replace").strip()
        except OSError:
            continue
        if comm in PROCESS_NAMES:
            pids.append(int(entry.name))
    return tuple(sorted(pids))


def _client_paths(pids: tuple[int, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    for pid in pids:
        try:
            target = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            continue
        if target not in paths:
            paths.append(target)
    return tuple(paths)


def _windows(runner: Runner) -> tuple[Window, ...]:
    wmctrl = which_optional("wmctrl")
    if wmctrl is None:
        return ()
    result = runner([wmctrl, "-l"])
    if not result.ok:
        return ()
    windows: list[Window] = []
    for line in result.stdout.splitlines():
        # id desktop host title...
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        window_id, desktop, host, title = parts
        try:
            desktop_index = int(desktop)
        except ValueError:
            desktop_index = -1
        windows.append(
            Window(
                window_id=window_id,
                desktop=desktop_index,
                host=host,
                title=title.strip(),
            )
        )
    return tuple(windows)


def _looks_like_meeting(window: Window) -> bool:
    """Title-based meeting detection.

    Zoom's home window is titled "Zoom Workplace"; a meeting window is titled with the
    meeting topic and normally contains "Zoom Meeting" or "Meeting". This is a *heuristic
    that reports its finding*, not an assertion: `status` says which windows it saw so a
    caller can disagree. Verified against the running client's home window only — the
    meeting-title form is the documented unverified item.
    """
    title = window.title
    if "zoom" not in title.lower():
        return False
    return "meeting" in title.lower() or "webinar" in title.lower()


def status(*, runner: Runner = run_command) -> DesktopStatus:
    """Report what is running and what windows exist. Never launches anything."""
    pids = _zoom_pids()
    windows = _windows(runner)
    zoom_windows = tuple(w for w in windows if "zoom" in w.title.lower())
    meeting = next((w for w in zoom_windows if _looks_like_meeting(w)), None)
    notes: list[str] = []
    if not pids and not zoom_windows:
        notes.append("the Zoom client does not appear to be running")
    if pids and not zoom_windows:
        notes.append("the client is running but has no visible Zoom window")
    if zoom_windows and meeting is None:
        notes.append(
            "no window title looks like a meeting; meeting detection is title-based and "
            "its meeting-window form is unverified (see README)"
        )
    return DesktopStatus(
        client_running=bool(pids) or bool(zoom_windows),
        client_paths=_client_paths(pids),
        client_pids=pids,
        windows=zoom_windows,
        meeting_window=meeting,
        notes=tuple(notes),
    )


def leave(*, runner: Runner = run_command, window: str | None = None) -> dict[str, object]:
    """Close the meeting window, which is how a human leaves a meeting.

    Closing the window is a graceful leave — the client sends its own leave to the
    meeting, the same as clicking the X — so no protocol knowledge is needed. If no
    meeting window can be identified, nothing is closed and the caller is told why,
    because closing the *home* window would be harmless but closing the wrong thing is
    not.
    """
    wmctrl = which_optional("wmctrl")
    if wmctrl is None:
        raise DesktopError("wmctrl is not on PATH; cannot close a window")
    state = status(runner=runner)
    target: Window | None
    if window:
        match = [w for w in state.windows if w.title == window or w.window_id == window]
        target = match[0] if match else None
        if target is None:
            raise DesktopError(f"no Zoom window matching {window!r}")
    else:
        target = state.meeting_window
    if target is None:
        return {
            "left": False,
            "reason": "no meeting window identified",
            "windows": [w.title for w in state.windows],
        }
    result = runner([wmctrl, "-ic", target.window_id])
    if not result.ok:
        raise DesktopError(f"wmctrl could not close {target.window_id}: {result.message}")
    return {"left": True, "window": target.window_id, "title": target.title}
