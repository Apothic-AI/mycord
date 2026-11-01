"""Subprocess plumbing, kept in one place so it can be faked at the process boundary.

Everything this package drives — `pactl`, `pacat`, `parec`, `ffmpeg`, `piper`, `wmctrl`,
the Zoom launcher — is a program on `PATH`. Nothing is imported as a Python library,
because the whole design depends on the *user's own* system audio server and the *user's
own* installed Zoom client, and a Python binding would paper over the case where either
is missing (which is exactly the case that must fail loudly, with exit code 2).

Tests replace these programs with scripts earlier on `PATH`, so they exercise the real
subprocess boundary: argument construction, exit codes, and output parsing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import NotInstalled, ZoomError

#: Default ceiling for any single helper invocation. Long enough for `pactl` and a short
#: `ffmpeg` run, short enough that a wedged audio server cannot hang an agent forever.
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class CommandResult:
    """What a helper process said. `argv[0]` is the resolved binary path."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def message(self) -> str:
        """The most useful single line to put in an error, preferring stderr."""
        for stream in (self.stderr, self.stdout):
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped
        return f"{self.argv[0]} exited {self.returncode}"


class Runner(Protocol):
    """Callable that runs one command to completion and captures its text output."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


class CaptureRunner(Protocol):
    """Callable that runs a recorder and captures **binary** stdout.

    Separate from `Runner` on purpose: a recorder's output is PCM, and a text-mode capture of
    PCM is a decode error waiting for the first high-bit sample.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes, str]: ...


class Spawner(Protocol):
    """Callable that starts a long-lived process, for streaming PCM in or out."""

    def __call__(self, argv: Sequence[str]) -> subprocess.Popen[bytes]: ...


def run_command(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run `argv` and capture stdout/stderr as text.

    A missing binary raises `NotInstalled` rather than surfacing `FileNotFoundError`,
    because the distinction is what exit code 2 means. `env` is layered over the current
    environment (used by Piper, which needs `LD_LIBRARY_PATH` for its bundled espeak).
    """
    full_env = None if env is None else {**os.environ, **env}
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=full_env,
        )
    except FileNotFoundError as exc:
        raise NotInstalled(f"{argv[0]}: not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ZoomError(f"{argv[0]} timed out after {timeout:g}s") from exc
    return CommandResult(
        argv=tuple(str(a) for a in argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def run_binary(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> tuple[int, bytes, str]:
    """Run `argv` and capture **binary** stdout, for PCM and WAV payloads.

    Returns ``(returncode, stdout_bytes, stderr_text)``. Text-decoding stdout would
    corrupt PCM, so it is never attempted.
    """
    full_env = None if env is None else {**os.environ, **env}
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=full_env,
        )
    except FileNotFoundError as exc:
        raise NotInstalled(f"{argv[0]}: not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ZoomError(f"{argv[0]} timed out after {timeout:g}s") from exc
    return (
        completed.returncode,
        completed.stdout or b"",
        (completed.stderr or b"").decode("utf-8", errors="replace"),
    )


def spawn(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start a long-lived process with binary pipes, for PCM streaming."""
    try:
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise NotInstalled(f"{argv[0]}: not found on PATH") from exc


def which(name: str, *, purpose: str = "") -> str:
    """Resolve `name` on `PATH`, or raise `NotInstalled` naming what it was for."""
    found = shutil.which(name)
    if found is None:
        suffix = f" (needed for {purpose})" if purpose else ""
        raise NotInstalled(f"{name}: not found on PATH{suffix}")
    return found


def which_optional(name: str) -> str | None:
    """Resolve `name` if present. Used for capabilities that only unlock extra verbs."""
    return shutil.which(name)
