"""Persistent REPL-style Telegram automation for agents.

This package deliberately depends only on ``telethon``. It does not depend on
``mycord-discord-skill/`` in any way, and it never authenticates with a bot
token - see :mod:`mycord_telegram_repl.auth` for the three supported user
login methods.
"""

from mycord_telegram_repl.client import SessionClient, SessionNotRunning
from mycord_telegram_repl.protocol import PROTOCOL_VERSION
from mycord_telegram_repl.session import ReplSession, default_socket_path

__version__ = "0.1.0"

__all__ = [
    "PROTOCOL_VERSION",
    "ReplSession",
    "SessionClient",
    "SessionNotRunning",
    "__version__",
    "default_socket_path",
]
