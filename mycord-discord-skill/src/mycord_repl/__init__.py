"""Persistent REPL-style Discord automation for agents.

This package deliberately depends only on ``discord.py-self``. It is the
supported Discord path in this repo: drive it directly, with nothing in front.
"""

from mycord_repl.client import SessionClient, SessionNotRunning
from mycord_repl.protocol import PROTOCOL_VERSION
from mycord_repl.session import ReplSession, default_socket_path

__version__ = "0.1.0"

__all__ = [
    "PROTOCOL_VERSION",
    "ReplSession",
    "SessionClient",
    "SessionNotRunning",
    "__version__",
    "default_socket_path",
]
