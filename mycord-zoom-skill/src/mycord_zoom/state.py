"""Persisted record of the audio plane, including the operator's original defaults.

This file exists for one reason: **a leftover null sink silently mutes the operator.** The
original default sink/source cannot be recovered by inspecting the running server once it
has been changed, so they are written down *before* anything is touched, atomically, and
kept until teardown has provably succeeded. The next command can then finish a restore that
a SIGKILL or a reboot interrupted — the case no in-process ``finally`` can cover.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Bumped on incompatible changes. A file from another version is refused, not guessed at,
#: because guessing risks the operator's defaults.
SCHEMA_VERSION = 2


class StateError(Exception):
    """The recorded session cannot be trusted."""


def default_state_dir() -> Path:
    """`$XDG_DATA_HOME/mycord-zoom`, overridable with `MYCORD_ZOOM_STATE` for tests."""
    override = os.environ.get("MYCORD_ZOOM_STATE")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "mycord-zoom"


@dataclass
class AudioSession:
    """The plane that is loaded, and what to put back when it goes away."""

    speaker_sink: str
    mic_sink: str
    mic_source: str
    modules: dict[str, int] = field(default_factory=dict)
    previous_default_sink: str | None = None
    previous_default_source: str | None = None
    routed: str | None = None
    description: str = "mycord-zoom"
    created_at: float = field(default_factory=time.time)
    pid: int = field(default_factory=os.getpid)
    version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class StateStore:
    """Reads and writes `audio-session.json` under the state directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "audio-session.json"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def load(self) -> AudioSession | None:
        """The recorded session, or None. Raises `StateError` rather than guessing."""
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError(f"{self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise StateError(f"{self.path} does not contain an object")
        if data.get("version") != SCHEMA_VERSION:
            raise StateError(
                f"{self.path} was written by another version "
                f"(found {data.get('version')!r}, expected {SCHEMA_VERSION}); "
                "run `mycord-zoom audio recover` after inspecting it"
            )
        known = {f for f in AudioSession.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise StateError(f"{self.path} has unknown fields: {sorted(unknown)}")
        try:
            return AudioSession(**data)
        except TypeError as exc:
            raise StateError(f"{self.path} is missing fields: {exc}") from exc

    def save(self, session: AudioSession) -> None:
        """Write atomically, so a crash cannot destroy the recorded defaults."""
        self._ensure_root()
        payload = json.dumps(session.to_dict(), indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=".audio-session-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
