"""The session file: what it holds, how it is written, and how slackdump gets it.

Slack gives no way to refresh an `xoxc-` token, and no way to renew the `d` cookie
short of using the browser. So the session is a *snapshot* with an expiry, and the only
repair is another capture. This module stores it that way — with the capture time, the
cookie's own expiry, and the workspace it belongs to — so `session` can say *why* it is
unhappy instead of only *that* it is.

Two representations exist on disk, and they are not redundant:

* `session.json` — the durable one, 0600, read by the passthrough on every call.
* a short-lived `.env` file in a 0700 temp dir — the only format
  `slackdump workspace import` accepts. It is deleted in a `finally`, but if the process
  is killed mid-import it can survive, which is why it lives in its own temp directory
  rather than in the state dir.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cdp import CapturedWorkspace
from .redact import token_fingerprint

#: slackdump's own credential store. Not ours, and not something we should move: the
#: `slackdump` binary resolves it from the cache dir it was built to use.
SLACKDUMP_CACHE = Path("~/.cache/slackdump").expanduser()


class SessionError(RuntimeError):
    """No usable session, or one that is present but unsafe/unreadable."""


@dataclass
class Session:
    """A captured client-token session."""

    token: str
    cookie: str
    team_id: str
    team_name: str = ""
    domain: str = ""
    url: str = ""
    enterprise_id: str | None = None
    captured_at: float = 0.0
    cookie_expires: float | None = None
    source: str = "localConfig_v2"

    @property
    def token_fingerprint(self) -> str:
        return token_fingerprint(self.token)

    def is_expired(self, *, now: float | None = None) -> bool:
        """Whether the cookie's own expiry has passed.

        This is a *lower* bound on validity, not a guarantee: a cookie can be revoked
        (sign-out elsewhere, password change, workspace policy) long before it expires.
        Only `auth.test` answers the real question, which is why `session --check` exists.
        """
        if self.cookie_expires is None:
            return False
        return self.cookie_expires <= (now if now is not None else time.time())

    def expires_in_days(self, *, now: float | None = None) -> float | None:
        if self.cookie_expires is None:
            return None
        return (self.cookie_expires - (now if now is not None else time.time())) / 86400.0

    def public(self) -> dict[str, Any]:
        """Status without secrets: kinds, lengths, timestamps, names."""
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "domain": self.domain,
            "url": self.url,
            "enterprise_id": self.enterprise_id,
            "token": self.token_fingerprint,
            "cookie": {"name": "d", "present": bool(self.cookie), "length": len(self.cookie)},
            "captured_at": self.captured_at,
            "cookie_expires": self.cookie_expires,
            "cookie_expires_in_days": (
                round(days, 1) if (days := self.expires_in_days()) is not None else None
            ),
            "source": self.source,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "token": self.token,
            "cookie": self.cookie,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "domain": self.domain,
            "url": self.url,
            "enterprise_id": self.enterprise_id,
            "captured_at": self.captured_at,
            "cookie_expires": self.cookie_expires,
            "source": self.source,
        }

    def to_env(self) -> str:
        """The two lines `slackdump workspace import` understands."""
        return f"SLACK_TOKEN={self.token}\nSLACK_COOKIE={self.cookie}\n"


def from_capture(
    workspace: CapturedWorkspace,
    cookie: str,
    captured_at: float,
    source: str,
    cookie_expires: float | None,
) -> Session:
    return Session(
        token=workspace.token,
        cookie=cookie,
        team_id=workspace.team_id,
        team_name=workspace.name,
        domain=workspace.domain,
        url=workspace.url,
        enterprise_id=workspace.enterprise_id,
        captured_at=captured_at,
        cookie_expires=cookie_expires,
        source=source,
    )


def write_session(session: Session, path: Path) -> None:
    """Write the session 0600, atomically.

    The file is created 0600 *before* it holds anything secret, rather than written and
    then chmodded — otherwise the token and cookie sit in a world-readable file for the
    duration of the write. The staging name is derived from the target so it stays under
    the same `session.json*` ignore rule.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "w") as fh:
            fh.write(json.dumps(session.to_dict(), indent=1) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_session(path: Path) -> Session:
    """Read the session, refusing one that is group- or world-readable.

    The mode check is a hard error rather than a warning: this file authenticates the
    whole account, and silently continuing would mean every later call depends on a
    credential anyone on the box can read.
    """
    if not path.is_file():
        raise SessionError(
            f"no session at {path} — run `mycord-slack login` in a browser profile that is "
            "signed in to Slack"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SessionError(
            f"{path} is mode {oct(mode)}; it holds the account's client token and session "
            f"cookie, so run: chmod 600 {path}"
        )
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SessionError(f"{path} is unreadable or not JSON ({exc}); re-run login") from exc
    if not isinstance(raw, dict):
        raise SessionError(f"{path} does not contain a session object; re-run login")
    token = str(raw.get("token") or "")
    cookie = str(raw.get("cookie") or "")
    if not token or not cookie:
        raise SessionError(f"{path} is missing its token or cookie; re-run login")
    expires_raw = raw.get("cookie_expires")
    captured_raw = raw.get("captured_at")
    return Session(
        token=token,
        cookie=cookie,
        team_id=str(raw.get("team_id") or ""),
        team_name=str(raw.get("team_name") or ""),
        domain=str(raw.get("domain") or ""),
        url=str(raw.get("url") or ""),
        enterprise_id=(str(raw["enterprise_id"]) if raw.get("enterprise_id") else None),
        captured_at=float(captured_raw) if isinstance(captured_raw, (int, float)) else 0.0,
        cookie_expires=(float(expires_raw) if isinstance(expires_raw, (int, float)) else None),
        source=str(raw.get("source") or "localConfig_v2"),
    )


@dataclass
class ImportResult:
    ok: bool
    detail: str


def import_into_slackdump(
    session: Session, slackdump: Path, *, cache_dir: Path | None = None
) -> ImportResult:
    """Hand the session to slackdump's encrypted credential store.

    `workspace import` validates the credentials against Slack before saving them, so a
    green result here is also live proof that the capture is good — which is why `login`
    reports it instead of a separate check.

    The env file is written into a fresh 0700 temp dir and removed in a `finally`. A
    crash between write and delete leaves it behind in `/tmp` rather than in the state
    directory, which is the lesser of the two exposures.
    """
    payload = session.to_env()
    with tempfile.TemporaryDirectory(prefix="mycord-slack-import-") as scratch:
        env_file = Path(scratch) / "slack.env"
        handle = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w") as fh:
            fh.write(payload)
        command = [str(slackdump), "workspace", "import", str(env_file)]
        if cache_dir is not None:
            command[2:2] = ["-cache-dir", str(cache_dir)]
        completed = _run(command)
    if completed.returncode != 0:
        return ImportResult(
            ok=False, detail=completed.output.strip() or f"exit {completed.returncode}"
        )
    return ImportResult(ok=True, detail=completed.output.strip())


@dataclass
class _Completed:
    returncode: int
    output: str


def _run(command: list[str]) -> _Completed:
    import subprocess

    proc = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    # Both streams are redacted before anything is returned: slackdump echoes the file it
    # read on failure, and that file contains the token and cookie.
    from .redact import redact

    combined = redact((proc.stdout or "") + (proc.stderr or ""))
    return _Completed(returncode=proc.returncode, output=combined)


def slackdump_workspaces(slackdump: Path) -> str:
    """`slackdump workspace list -a`, redacted. Used only for reporting."""
    return _run([str(slackdump), "workspace", "list", "-a"]).output
