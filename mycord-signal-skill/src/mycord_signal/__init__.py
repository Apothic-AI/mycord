"""Dependency detection and JSON-RPC client for Signal, with no bot registration.

The package is deliberately small: it exists to answer "are the two pinned components at
the versions the call path needs?" and to speak signal-cli's JSON-RPC socket. It does not
implement messaging verbs yet, because there is no live account on this host to verify
them against — see ``docs/determine/signal.md`` and ``docs/implementation-decision.md``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
