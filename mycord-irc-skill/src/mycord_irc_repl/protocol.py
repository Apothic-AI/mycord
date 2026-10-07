"""Wire protocol shared by the REPL session daemon and its CLI client.

The protocol is deliberately tiny: newline-delimited JSON over a Unix domain
socket. One request object in, one response object out.
"""

import json
from typing import Any, Final

PROTOCOL_VERSION: Final = 1

OP_EVAL: Final = "eval"
OP_PING: Final = "ping"
OP_STATUS: Final = "status"
OP_RESET: Final = "reset"
OP_SHUTDOWN: Final = "shutdown"


def encode(message: dict[str, Any]) -> bytes:
    """Serialize a message as a single newline-terminated JSON frame."""
    return json.dumps(message, default=str).encode("utf-8") + b"\n"


def decode(line: bytes | str) -> dict[str, Any]:
    """Parse one newline-terminated JSON frame into a dict."""
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    decoded: dict[str, Any] = json.loads(line)
    return decoded


def success(
    *,
    result: Any = None,
    stdout: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a successful response frame."""
    payload: dict[str, Any] = {"ok": True, "result": result, "stdout": stdout, "error": None}
    if extra:
        payload.update(extra)
    return payload


def failure(*, error: str, stdout: str = "") -> dict[str, Any]:
    """Build a failed response frame."""
    return {"ok": False, "result": None, "stdout": stdout, "error": error}
