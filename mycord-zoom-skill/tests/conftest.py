"""Shared fixtures: a fake `pactl` on `PATH`, a temp state dir, and a hard safety net.

Two layers keep the offline suite away from the real audio server, because an earlier
version of this file had only the first and two tests silently ran live `pactl` — creating
two real null sinks and re-pointing the operator's audio into them:

1. every fixture that can produce a plane depends on `isolation`, so the fake `pactl` is
   always first on `PATH`;
2. `isolation` also sets `MYCORD_ZOOM_STATE` to a temp directory *and* plants guard
   binaries in a directory ahead of the fake, so any accidental reference to a
   non-existent command fails instead of reaching the system.

There is also an explicit autouse assertion: if a test leaves a session file behind in the
operator's real state directory, the suite fails.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_pactl import OPERATOR_SINK, OPERATOR_SOURCE, FakePactl

from mycord_zoom.audio import AudioPlane
from mycord_zoom.state import StateStore

#: Commands the plane shells out to for PCM. In the offline suite they are replaced with
#: scripts that write a recognisable byte pattern, so a "capture" test can assert plumbing
#: without an audio server.
#: A recorder that emits a loud tone rather than silence, so the VAD finds an utterance and
#: the "no ASR installed" path is actually exercised. It ignores `--seconds` and finishes
#: immediately: this is a plumbing stub, not a real-time recorder.
RECORDING_STUB = """#!/usr/bin/env python3
import math, struct, sys
rate = 48000
frames = b"".join(
    struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate)
)
sys.stdout.buffer.write(frames)
"""


def _stub(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def no_operator_state() -> Iterator[None]:
    """Fail the suite if a test writes into the operator's real state directory.

    This is deliberately autouse: the leak that motivated it was a *fixture-orphaned* test,
    so nothing about the individual test gave it away.
    """
    real = Path.home() / ".local" / "share" / "mycord-zoom"
    existed = real.exists()
    yield
    if not existed and real.exists():
        pytest.fail(
            f"a test created {real}: the offline suite must never touch the operator's "
            "audio state (check that the test depends on `isolation`)"
        )


@pytest.fixture()
def isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePactl]:
    """A fake `pactl`, a temp state directory, and stub PCM commands."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = FakePactl(
        bin_dir=bin_dir,
        state_path=tmp_path / "pactl-state.json",
        log_path=tmp_path / "pactl-calls.log",
    )
    fake.install()
    _stub(bin_dir, "parec", RECORDING_STUB)
    _stub(bin_dir, "pacat", "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")

    monkeypatch.setenv("FAKE_PACTL_STATE", str(fake.state_path))
    monkeypatch.setenv("FAKE_PACTL_LOG", str(fake.log_path))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("MYCORD_ZOOM_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("FAKE_PACTL_FAIL", raising=False)
    # No speech model, no gvoice checkout: speech tests opt in explicitly.
    monkeypatch.setenv("PIPER_DIR", str(tmp_path / "no-piper"))
    monkeypatch.setenv("GVOICE_HOME", str(tmp_path / "no-gvoice"))
    yield fake


@pytest.fixture()
def fake_pactl(isolation: FakePactl) -> FakePactl:
    return isolation


@pytest.fixture()
def state_dir(isolation: FakePactl, tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture()
def store(state_dir: Path) -> StateStore:
    return StateStore(state_dir)


@pytest.fixture()
def plane(store: StateStore, isolation: FakePactl) -> AudioPlane:
    """A plane wired to the fake server. Depends on `isolation`, always."""
    return AudioPlane(store)


@pytest.fixture()
def operator_defaults() -> tuple[str, str]:
    """What the fake server starts with, i.e. what teardown must restore."""
    return OPERATOR_SINK, OPERATOR_SOURCE


def module_args(fake: FakePactl) -> list[list[str]]:
    """`[module_name, *args]` for every `load-module` call, in order."""
    return [call[1:] for call in fake.called if call and call[0] == "load-module"]


def dumped_state(fake: FakePactl) -> str:
    return json.dumps(fake.state, sort_keys=True)
