"""Where session material lives, and how to find the two collaborators.

Nothing here reads secrets. It resolves paths and binaries, and it fails early and
loudly when a prerequisite is missing, because the alternative — a Slack call that
fails with `invalid_auth` — looks like a bad session when the real problem is a missing
binary or a state directory in the wrong place.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Override the whole state directory. Used by tests and by anyone who keeps state on
#: another volume.
STATE_ENV = "MYCORD_SLACK_STATE"
SLACKDUMP_ENV = "MYCORD_SLACK_SLACKDUMP"
API_BASE_ENV = "MYCORD_SLACK_API_BASE"
CHROME_ENV = "MYCORD_SLACK_CHROME"
CHROME_PROFILE_ENV = "MYCORD_SLACK_CHROME_PROFILE"

APP_DIR = "mycord-slack"


class ConfigError(RuntimeError):
    """A prerequisite is missing; the message says exactly which one and how to fix it."""


def state_dir() -> Path:
    """`$MYCORD_SLACK_STATE`, else `$XDG_DATA_HOME/mycord-slack`, else `~/.local/share`."""
    override = os.environ.get(STATE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home).expanduser() if data_home else Path("~/.local/share").expanduser()
    return base / APP_DIR


@dataclass(frozen=True)
class Paths:
    """Every on-disk path this package touches, derived from one root."""

    state: Path

    @property
    def session(self) -> Path:
        return self.state / "session.json"

    def ensure(self) -> Path:
        """Create the state directory 0700 and return it.

        The session file inside holds an `xoxc-` token *and* the `d` cookie value, which
        together are the whole account. Keeping the directory private is the first of
        two permissions that matter; the file itself is written 0600.
        """
        self.state.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):  # unusual filesystems
            self.state.chmod(0o700)
        return self.state


def paths() -> Paths:
    return Paths(state=state_dir())


def slackdump_binary() -> Path:
    """Locate `slackdump`, which owns every read this package performs."""
    override = os.environ.get(SLACKDUMP_ENV, "").strip()
    candidates = [override] if override else []
    found = shutil.which("slackdump")
    if found:
        candidates.append(found)
    candidates.append(str(Path("~/.local/bin/slackdump").expanduser()))
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise ConfigError(
        "slackdump not found. Reads go through slackdump (it is the only tool here that "
        "speaks the client-token protocol). Install it with:\n"
        "  GOBIN=$HOME/.local/bin go install github.com/rusq/slackdump/v3/cmd/slackdump@v3.1.11\n"
        f"or set {SLACKDUMP_ENV} to the binary."
    )


def api_base() -> str | None:
    """Slack API base URL, or None for slack_sdk's default.

    The default (`https://slack.com/api/`) is what routes an `xoxc-` token to its own
    workspace, so this stays unset in normal use. Tests pin it at a local fixture server;
    that is the only reason the knob exists.
    """
    value = os.environ.get(API_BASE_ENV, "").strip()
    return value or None
