"""Exit codes and the exception hierarchy that maps onto them.

The codes are the contract a script branches on, so they are coarse and stable:

======  ==========================================================
``0``   success
``1``   runtime failure (bad input, audio server refused, no window,
        unrecognised meeting link — anything that is not 2 or 3)
``2``   a required external program or model is not installed
``3``   nothing is configured yet (no audio pair, no meeting given)
======  ==========================================================

Exit code ``2`` is deliberately reserved for *missing software*, because that is the one
failure a caller can fix by installing something, and it must not be confused with a
usage mistake. ``argparse`` exits ``2`` on a usage error as a matter of course, so the CLI
remaps that to ``1`` to keep the meaning of ``2`` unambiguous.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_NOT_INSTALLED = 2
EXIT_UNCONFIGURED = 3


class ZoomError(Exception):
    """Base class for everything this package raises on purpose."""

    exit_code: int = EXIT_RUNTIME


class NotInstalled(ZoomError):
    """An external program or model this operation needs is absent.

    Carries a remedy in the message: the point of the distinction is that the caller can
    act on it, so a bare "not found" would be a wasted exit code.
    """

    exit_code = EXIT_NOT_INSTALLED


class Unconfigured(ZoomError):
    """The operation needs setup that has not been done yet (e.g. the audio pair)."""

    exit_code = EXIT_UNCONFIGURED


class AudioError(ZoomError):
    """The audio server rejected something, or refused to give a usable answer."""


class DesktopError(ZoomError):
    """The desktop client, its launcher, or a window operation failed."""


class SpeechError(NotInstalled):
    """No speech model is available, or synthesis/transcription failed."""
