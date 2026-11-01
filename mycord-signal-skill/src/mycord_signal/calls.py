"""The call path: signal-cli's call verbs, plus the tunnel's PCM socket.

Signal calls are not SIP and not plain WebRTC from this skill's point of view. signal-cli
spawns ``signal-call-tunnel`` per call; the tunnel owns RingRTC, negotiates media keys from
the paired Signal identity keys, and — in pipe mode — hands this process one bidirectional
Unix stream socket carrying raw PCM. So there are two surfaces here:

1. signalling/control, over the daemon's JSON-RPC socket (``startCall`` &c.);
2. media, over the per-call Unix socket named in the tunnel's ``ready`` event.

The PCM contract, read from ``signal-call-tunnel/src/pipe_audio.rs`` at 5a963d1 and
confirmed against the built binary:

* 48 000 Hz, mono, signed 16-bit little-endian
* 10 ms windows -> 480 samples -> **960 bytes per window, each direction**
* the tunnel is the *listener*; the client connects
* one socket carries both directions; ``inputDeviceName == outputDeviceName``
* read = playout (audio from the remote peer), write = recording (audio to the peer)

Ordering rules that are not optional:

* ``subscribeCallEvents`` before any call, or an incoming call is dropped with no ring.
* ``inputDeviceName`` only exists once the tunnel reports ``ready``, which arrives as a
  ``callEvent`` — never in the ``startCall`` response.
* do not open the PCM socket until the state is ``CONNECTED``; before that the tunnel
  buffers or drops playout and pads recording with silence.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from . import rpc

#: PCM contract constants, transcribed from ``pipe_audio.rs``.
SAMPLE_RATE: Final = 48_000
CHANNELS: Final = 1
WINDOW_MS: Final = 10
SAMPLES_PER_WINDOW: Final = SAMPLE_RATE * WINDOW_MS // 1000  # 480
BYTES_PER_SAMPLE: Final = 2
WINDOW_BYTES: Final = SAMPLES_PER_WINDOW * BYTES_PER_SAMPLE  # 960

#: Call states reported by ``JsonCallEvent.state`` (from ``CallInfo.State``).
STATE_IDLE: Final = "IDLE"
STATE_RINGING_INCOMING: Final = "RINGING_INCOMING"
STATE_RINGING_OUTGOING: Final = "RINGING_OUTGOING"
STATE_CONNECTING: Final = "CONNECTING"
STATE_CONNECTED: Final = "CONNECTED"
STATE_RECONNECTING: Final = "RECONNECTING"
STATE_ENDED: Final = "ENDED"


@dataclass
class CallEvent:
    """One ``callEvent`` payload, with the fields the skill actually branches on."""

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def call_id(self) -> int | None:
        value = self.raw.get("callId")
        return value if isinstance(value, int) else None

    @property
    def state(self) -> str | None:
        value = self.raw.get("state")
        return value if isinstance(value, str) else None

    @property
    def input_device(self) -> str | None:
        value = self.raw.get("inputDeviceName")
        return value if isinstance(value, str) else None

    @property
    def output_device(self) -> str | None:
        value = self.raw.get("outputDeviceName")
        return value if isinstance(value, str) else None

    @property
    def is_outgoing(self) -> bool:
        return bool(self.raw.get("isOutgoing"))

    @property
    def connected(self) -> bool:
        return self.state == STATE_CONNECTED

    @property
    def ended(self) -> bool:
        return self.state == STATE_ENDED


class CallError(Exception):
    """A call-path failure with a human explanation attached."""


# --------------------------------------------------------------------------- signalling


def start(client: rpc.RpcClient, recipient: str) -> dict[str, Any]:
    """Start an outgoing call. Returns the daemon's ``callInfo``.

    The returned device names are usually ``null``: they are only known once the tunnel
    reports ``ready``, which arrives as a ``callEvent``. Callers must wait for that event
    instead of reading this dict.
    """
    result = client.call(rpc.METHOD_START_CALL, {"recipient": [recipient]}, timeout=60.0)
    if not isinstance(result, dict):
        raise CallError(f"startCall returned {result!r}, expected a call info object")
    return result


def accept(client: rpc.RpcClient, call_id: int) -> dict[str, Any]:
    result = client.call(rpc.METHOD_ACCEPT_CALL, {"callId": call_id}, timeout=60.0)
    return result if isinstance(result, dict) else {}


def hangup(client: rpc.RpcClient, call_id: int) -> dict[str, Any]:
    result = client.call(rpc.METHOD_HANGUP_CALL, {"callId": call_id}, timeout=60.0)
    return result if isinstance(result, dict) else {}


def reject(client: rpc.RpcClient, call_id: int) -> dict[str, Any]:
    result = client.call(rpc.METHOD_REJECT_CALL, {"callId": call_id}, timeout=60.0)
    return result if isinstance(result, dict) else {}


def wait_for_state(
    client: rpc.RpcClient, state: str, timeout: float = 60.0, call_id: int | None = None
) -> CallEvent | None:
    """Wait for a specific call state, returning ``None`` on timeout.

    Filters by ``callId`` when one is given, so a second call cannot satisfy a wait meant for
    the first.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        notification = client.poll(timeout=min(0.5, max(deadline - time.monotonic(), 0.0)))
        if notification is None or notification.method != "callEvent":
            continue
        result = notification.result
        if not isinstance(result, dict):
            continue
        event = CallEvent(raw=result)
        if call_id is not None and event.call_id != call_id:
            continue
        if event.state == state:
            return event
        if event.ended:
            return event
    return None


# --------------------------------------------------------------------------- media


@dataclass
class AudioSocket:
    """The per-call PCM socket, one window at a time.

    ``read_window`` returns exactly :data:`WINDOW_BYTES` or ``None`` at end of stream;
    ``write_window`` accepts any number of bytes and splits them into aligned windows. Keeping
    both at window granularity is what makes the pacing explicit: the peer expects one window
    every 10 ms, so a caller that writes faster is writing into an 80 ms buffer that drops
    the oldest audio rather than growing.
    """

    sock: socket.socket
    path: Path

    @classmethod
    def connect(cls, device_name: str, timeout: float = 10.0) -> AudioSocket:
        """Connect to the ``unix:<path>`` device name the tunnel reported."""
        if not device_name.startswith("unix:"):
            raise CallError(
                f"device {device_name!r} is not a pipe device; the tunnel is not in pipe mode"
            )
        path = Path(device_name.removeprefix("unix:"))
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except OSError as exc:
            sock.close()
            raise CallError(f"cannot connect to the call audio socket {path}: {exc}") from exc
        return cls(sock=sock, path=path)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def __enter__(self) -> AudioSocket:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_window(self) -> bytes | None:
        """Read one 10 ms window of remote audio, or ``None`` at end of stream."""
        chunks: list[bytes] = []
        remaining = WINDOW_BYTES
        while remaining > 0:
            try:
                chunk = self.sock.recv(remaining)
            except OSError as exc:
                raise CallError(f"audio read failed: {exc}") from exc
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def write_window(self, pcm: bytes) -> int:
        """Write PCM, padded/truncated to whole windows. Returns bytes written.

        Padding instead of rejecting a short tail matters for the last frame of a synthesised
        utterance: sending 500 bytes and leaving the peer's buffer half-full just adds jitter,
        while a zero-padded window is silence the peer already expects.
        """
        if not pcm:
            pcm = b"\x00" * WINDOW_BYTES
        remainder = len(pcm) % WINDOW_BYTES
        if remainder:
            pcm += b"\x00" * (WINDOW_BYTES - remainder)
        try:
            self.sock.sendall(pcm)
        except OSError as exc:
            raise CallError(f"audio write failed: {exc}") from exc
        return len(pcm)

    def windows(self) -> Iterator[bytes]:
        """Yield remote audio windows until the peer closes."""
        while True:
            window = self.read_window()
            if window is None:
                return
            yield window


def silence() -> bytes:
    """One window of silence, the canonical keep-alive payload."""
    return b"\x00" * WINDOW_BYTES


def describe_contract() -> str:
    """Human-readable PCM contract, used by ``doctor --verbose`` and the tests."""
    return (
        f"{SAMPLE_RATE} Hz, {CHANNELS} channel(s), s16le, {WINDOW_MS} ms windows "
        f"({SAMPLES_PER_WINDOW} samples = {WINDOW_BYTES} bytes each way)"
    )
