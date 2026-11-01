"""mycord-zoom — Zoom for agents, with no app registration.

Two halves, both of which exist because Zoom publishes no local API for the ordinary
desktop client:

* **The desktop client** is launched and observed through the URL scheme it registers for
  itself (``zoommtg://``), which is what a human clicking a meeting link uses. Joining
  needs nothing but the meeting URL: no account, no token, no registered app.
* **Call audio** goes through a PipeWire/PulseAudio plane of virtual devices. Zoom is an
  ordinary client of the system mixer, so presenting a speaker and a microphone to it is
  enough to carry a conversation in both directions, without terminating Zoom's WebRTC and
  without the Meeting SDK.

See ``SKILL.md`` for the verb contract and ``README.md`` for the measured audio numbers and
the one link that remains unverified (whether Zoom lists the virtual microphone, which
needs a real meeting to check).
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
