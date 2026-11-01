"""Tests for the call path: state machine, PCM framing, and the dependency gate.

The PCM tests are the ones with real content. The socket contract (48 kHz mono s16le, 10 ms
windows = 960 bytes, both directions on one stream) is transcribed from
``signal-call-tunnel/src/pipe_audio.rs`` at 5a963d1, so these tests pin the framing that the
built binary actually speaks.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fake_daemon import CALL_EVENT_CONNECTED, default_handlers, fake_daemon

from mycord_signal import calls, rpc

# --------------------------------------------------------------------------- constants


def test_pcm_contract_constants() -> None:
    """Pin the numbers to the source, so a refactor cannot quietly change them.

    ``pipe_audio.rs`` at 5a963d1: SAMPLE_FREQUENCY 48_000, NUM_CHANNELS 1, WINDOW_MS 10,
    WEBRTC_WINDOW = SAMPLE_FREQUENCY / 100 = 480, BYTES_PER_SAMPLE 2.
    """
    assert calls.SAMPLE_RATE == 48_000
    assert calls.CHANNELS == 1
    assert calls.WINDOW_MS == 10
    assert calls.SAMPLES_PER_WINDOW == 480
    assert calls.BYTES_PER_SAMPLE == 2
    assert calls.WINDOW_BYTES == 960


def test_contract_description_mentions_every_constant() -> None:
    text = calls.describe_contract()
    assert "48000 Hz" in text
    assert "1 channel" in text
    assert "10 ms" in text
    assert "960 bytes" in text


def test_silence_is_exactly_one_window() -> None:
    payload = calls.silence()
    assert len(payload) == calls.WINDOW_BYTES
    assert payload == b"\x00" * calls.WINDOW_BYTES


# --------------------------------------------------------------------------- CallEvent


def test_call_event_fields() -> None:
    event = calls.CallEvent(raw=CALL_EVENT_CONNECTED)
    assert event.call_id == 1234567890123456789
    assert event.state == "CONNECTED"
    assert event.connected is True
    assert event.ended is False
    assert event.input_device is not None
    assert event.input_device.startswith("unix:")
    assert event.is_outgoing is True


def test_call_event_missing_fields_are_none() -> None:
    event = calls.CallEvent(raw={})
    assert event.call_id is None
    assert event.state is None
    assert event.input_device is None
    assert event.connected is False
    assert event.ended is False


def test_call_event_wrong_types_do_not_leak() -> None:
    event = calls.CallEvent(raw={"callId": "123", "state": 5, "inputDeviceName": 7})
    assert event.call_id is None
    assert event.state is None
    assert event.input_device is None


def test_call_event_ended() -> None:
    assert calls.CallEvent(raw={"state": "ENDED"}).ended is True


# --------------------------------------------------------------------------- signalling


def test_start_returns_call_info(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["startCall"] = lambda request: {
        "callId": 42,
        "state": "RINGING_OUTGOING",
        "inputDeviceName": None,
        "outputDeviceName": None,
    }
    with fake_daemon(socket_path, handlers) as server:
        with rpc.RpcClient(socket_path) as client:
            info = calls.start(client, "+15550100")
    assert info["callId"] == 42
    # Live behaviour: device names are null until the tunnel reports ready.
    assert info["inputDeviceName"] is None
    assert server.requests[0]["params"] == {"recipient": ["+15550100"]}


def test_start_rejects_unexpected_shape(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["startCall"] = lambda _request: "OK"
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(calls.CallError):
                calls.start(client, "+15550100")


def test_accept_and_hangup_send_call_id(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["acceptCall"] = lambda request: {"state": "CONNECTING"}
    handlers["hangupCall"] = lambda request: {}
    with fake_daemon(socket_path, handlers) as server:
        with rpc.RpcClient(socket_path) as client:
            calls.accept(client, 42)
            calls.hangup(client, 42)
    assert server.requests[0]["params"] == {"callId": 42}
    assert server.requests[1]["params"] == {"callId": 42}


def test_wait_for_state_filters_by_call_id(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path, timeout=10) as client:
            client.subscribe_call_events()

            def push() -> None:
                # A different call reaching CONNECTED must not satisfy this wait.
                server.push_call_event(1, {**CALL_EVENT_CONNECTED, "callId": 999})
                server.push_call_event(1, CALL_EVENT_CONNECTED)

            threading.Thread(target=push, daemon=True).start()
            event = calls.wait_for_state(
                client, calls.STATE_CONNECTED, timeout=5, call_id=1234567890123456789
            )
    assert event is not None
    assert event.call_id == 1234567890123456789


def test_wait_for_state_returns_on_end(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path, timeout=10) as client:
            client.subscribe_call_events()
            threading.Thread(
                target=lambda: server.push_call_event(1, {"callId": 1, "state": "ENDED"}),
                daemon=True,
            ).start()
            event = calls.wait_for_state(client, calls.STATE_CONNECTED, timeout=5, call_id=1)
    assert event is not None
    assert event.ended is True


def test_wait_for_state_times_out(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path, timeout=5) as client:
            client.subscribe_call_events()
            assert calls.wait_for_state(client, calls.STATE_CONNECTED, timeout=0.2) is None


# --------------------------------------------------------------------------- audio socket


@pytest.fixture()
def audio_pair(tmp_path: Path) -> tuple[Path, Any]:
    """A listening Unix socket standing in for the tunnel's pipe, plus its path."""
    import socket

    path = tmp_path / "signal-call-1.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    yield path, server
    server.close()


def test_audio_connect_requires_unix_prefix(tmp_path: Path) -> None:
    with pytest.raises(calls.CallError) as excinfo:
        calls.AudioSocket.connect("signal_input_1")
    assert "not a pipe device" in str(excinfo.value)


def test_audio_connect_fails_cleanly_on_missing_socket(tmp_path: Path) -> None:
    with pytest.raises(calls.CallError) as excinfo:
        calls.AudioSocket.connect(f"unix:{tmp_path / 'absent.sock'}")
    assert "cannot connect" in str(excinfo.value)


def test_audio_read_window_returns_exactly_960_bytes(audio_pair: tuple[Path, Any]) -> None:
    path, server = audio_pair
    accepted = threading.Event()
    conn_holder: list[Any] = []

    def accept() -> None:
        conn, _ = server.accept()
        conn_holder.append(conn)
        accepted.set()

    threading.Thread(target=accept, daemon=True).start()
    audio = calls.AudioSocket.connect(f"unix:{path}")
    try:
        assert accepted.wait(timeout=5), "server thread never accepted the connection"
        # Partial writes must be reassembled into one full window.
        conn_holder[0].sendall(b"\x01\x02" * 300)
        conn_holder[0].sendall(b"\x03\x04" * 180)
        window = audio.read_window()
    finally:
        audio.close()
        for conn in conn_holder:
            conn.close()
    assert window is not None
    assert len(window) == 960
    assert window[:4] == b"\x01\x02\x01\x02"


def test_audio_read_window_returns_none_at_eof(audio_pair: tuple[Path, Any]) -> None:
    path, server = audio_pair
    conn_holder: list[Any] = []

    def accept_then_close() -> None:
        conn, _ = server.accept()
        conn_holder.append(conn)
        conn.close()

    threading.Thread(target=accept_then_close, daemon=True).start()
    audio = calls.AudioSocket.connect(f"unix:{path}")
    try:
        assert audio.read_window() is None
    finally:
        audio.close()


def test_audio_write_window_pads_to_whole_windows(audio_pair: tuple[Path, Any]) -> None:
    """A short tail is zero-padded, not rejected: the peer expects 10 ms windows."""
    path, server = audio_pair
    received: list[bytes] = []

    ready = threading.Event()

    def accept_and_read() -> None:
        conn, _ = server.accept()
        ready.set()
        with conn:
            buffer = b""
            while len(buffer) < 960:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
            received.append(buffer)

    threading.Thread(target=accept_and_read, daemon=True).start()
    audio = calls.AudioSocket.connect(f"unix:{path}")
    try:
        assert ready.wait(timeout=5), "server thread never accepted the connection"
        written = audio.write_window(b"\x10\x20" * 250)  # 500 bytes
        # Wait for the peer to finish reading before closing, or the close can cut the frame.
        for _ in range(150):
            if received:
                break
            threading.Event().wait(0.02)
    finally:
        audio.close()
    assert written == 960
    assert len(received[0]) == 960
    assert received[0][500:] == b"\x00" * 460


def test_audio_write_window_handles_empty_input(audio_pair: tuple[Path, Any]) -> None:
    path, server = audio_pair
    received: list[bytes] = []
    ready = threading.Event()

    def accept_and_read() -> None:
        conn, _ = server.accept()
        ready.set()
        with conn:
            chunk = conn.recv(4096)
            received.append(chunk)

    threading.Thread(target=accept_and_read, daemon=True).start()
    audio = calls.AudioSocket.connect(f"unix:{path}")
    try:
        assert ready.wait(timeout=5), "server thread never accepted the connection"
        assert audio.write_window(b"") == 960
    finally:
        audio.close()
    for _ in range(100):
        if received:
            break
        threading.Event().wait(0.02)
    assert received and received[0] == b"\x00" * 960


def test_audio_windows_yields_until_eof(audio_pair: tuple[Path, Any]) -> None:
    path, server = audio_pair
    conn_holder: list[Any] = []

    def push() -> None:
        conn, _ = server.accept()
        conn_holder.append(conn)
        conn.sendall(b"\xaa" * 960)
        conn.sendall(b"\xbb" * 960)
        conn.close()

    threading.Thread(target=push, daemon=True).start()
    audio = calls.AudioSocket.connect(f"unix:{path}")
    try:
        collected = list(audio.windows())
    finally:
        audio.close()
    assert len(collected) == 2
    assert collected[0][:1] == b"\xaa"
    assert collected[1][:1] == b"\xbb"


# --------------------------------------------------------------------------- gate wiring


def test_call_verbs_refuse_when_gate_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A stale pin must stop the call *before* the daemon is contacted.

    This is the whole point of the gate: the failure it prevents is a broken media stream
    halfway through a ringing call.
    """
    from mycord_signal import cli

    contacted = False

    def fake_client(_paths: Any) -> Any:
        nonlocal contacted
        contacted = True
        raise AssertionError("the daemon must not be contacted when the gate fails")

    monkeypatch.setattr(cli, "_client", fake_client)
    args = cli.build_parser().parse_args(
        [
            "call",
            "start",
            "+15550100",
            "--confirm",
            "--socket",
            str(tmp_path / "nope.sock"),
            "--tunnel-bin",
            str(tmp_path / "no-tunnel"),
        ]
    )
    exit_code = cli.cmd_call_start(args)
    assert exit_code == cli.EXIT_NOT_READY
    assert contacted is False


def test_call_start_without_confirm_is_a_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No ``--confirm`` means no call, even when the deps are fine."""
    from mycord_signal import cli

    monkeypatch.setattr(cli, "_gate", lambda _paths: None)
    monkeypatch.setattr(cli, "_client", lambda _paths: pytest.fail("must not connect in a dry run"))
    args = cli.build_parser().parse_args(["call", "start", "+15550100"])
    assert cli.cmd_call_start(args) == cli.EXIT_OK
