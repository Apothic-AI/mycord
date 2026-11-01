"""Tests for the JSON-RPC client, against a real fake server on a real Unix socket.

Nothing here is mocked at the Python level: the fake daemon accepts a connection, reads
newline-delimited JSON and writes responses and notifications back on the same socket. That is
the part that breaks in practice — framing and demultiplexing — so it is worth exercising for
real. The wire shapes are copied from signal-cli v0.14.9's sources and confirmed against the
daemon running on this host.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fake_daemon import (
    CALL_EVENT_CONNECTED,
    CALL_EVENT_RINGING,
    FakeError,
    default_handlers,
    fake_daemon,
)

from mycord_signal import rpc


def test_connect_and_version(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers("0.14.9")) as server:
        with rpc.RpcClient(socket_path) as client:
            assert client.version() == "0.14.9"
    assert server.requests[0]["method"] == "version"
    assert server.requests[0]["jsonrpc"] == "2.0"
    assert isinstance(server.requests[0]["id"], int)


def test_version_matches_live_envelope_shape() -> None:
    """The live daemon answers `{"result": {"version": "0.14.9"}}` — verified on this host."""
    assert rpc.RpcClient._unwrap({"result": {"version": "0.14.9"}, "id": 1}) == {
        "version": "0.14.9"
    }


def test_version_tolerates_bare_string(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["version"] = lambda _request: "0.14.9"
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            assert client.version() == "0.14.9"


def test_connect_fails_cleanly_when_no_daemon(tmp_path: Path) -> None:
    with pytest.raises(rpc.TransportError) as excinfo:
        rpc.RpcClient(tmp_path / "nothing-here.sock").connect()
    assert "cannot connect" in str(excinfo.value)


def test_rpc_error_carries_code_and_message(socket_path: Path) -> None:
    """signal-cli's application error codes must survive verbatim.

    ``-1`` is USER_ERROR in signal-cli. A client that flattened it into a string would lose
    the only reliable way to tell "bad arguments" from "IO failed" — and
    ``listAccounts`` returning ``-32601`` on a single-account daemon is exactly the kind of
    thing this distinction is needed for (verified live).
    """

    def boom(_request: dict[str, Any]) -> Any:
        raise FakeError(rpc.ERROR_USER, "No recipients given")

    handlers = default_handlers()
    handlers["listCalls"] = boom
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(rpc.RpcError) as excinfo:
                client.call("listCalls")
    assert excinfo.value.code == rpc.ERROR_USER
    assert "No recipients given" in excinfo.value.message


def test_list_accounts_is_method_not_found_on_single_account_daemon(socket_path: Path) -> None:
    """The exact live behaviour that forced `doctor` to read accounts.json instead.

    A single-account daemon does not dispatch `listAccounts` (it is a
    JsonRpcMultiLocalCommand, and the single-account handler only runs single/multi
    commands), so the daemon answers -32601 even though the CLI has the command.
    """
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(rpc.RpcError) as excinfo:
                client.call("listAccounts")
    assert excinfo.value.code == rpc.ERROR_METHOD_NOT_FOUND


def test_subscribe_call_events_returns_subscription_id(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            assert client.subscribe_call_events() == 1
            assert client.subscribe_receive() == 0


def test_subscribe_rejects_non_integer_result(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["subscribeCallEvents"] = lambda _request: {"weird": True}
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(rpc.RpcError) as excinfo:
                client.subscribe_call_events()
    assert "expected a subscription id" in excinfo.value.message


def test_notifications_do_not_break_pending_responses(socket_path: Path) -> None:
    """The deadlock this design exists to avoid.

    A notification can arrive between a request and its response. A client that read one
    blocking loop and treated the first line as the answer would return the wrong value, or
    hang forever.
    """
    subscribed = threading.Event()

    def subscribe(_request: dict[str, Any]) -> int:
        subscribed.set()
        return 7

    handlers = default_handlers()
    handlers["subscribeCallEvents"] = subscribe
    with fake_daemon(socket_path, handlers) as server:
        with rpc.RpcClient(socket_path, timeout=10) as client:
            assert client.subscribe_call_events() == 7
            assert subscribed.wait(timeout=5)
            server.push_call_event(7, CALL_EVENT_RINGING)
            result = client.call("version")
    assert result == {"version": "0.14.9"}


def test_call_events_stream_in_order(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path, timeout=10) as client:
            subscription = client.subscribe_call_events()
            server.push_call_event(subscription, CALL_EVENT_RINGING)
            server.push_call_event(subscription, CALL_EVENT_CONNECTED)

            events: list[dict[str, Any]] = []
            for event in client.call_events(timeout=1.0):
                events.append(event)
                if len(events) == 2:
                    break

    assert [event["state"] for event in events] == ["RINGING_OUTGOING", "CONNECTED"]
    # Device names only exist once the tunnel reports ready — the reason the skill must wait
    # for an event instead of reading the startCall response.
    assert "inputDeviceName" not in events[0]
    assert events[1]["inputDeviceName"].startswith("unix:")


def test_receive_events_filter_call_events_out(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path, timeout=10) as client:
            subscription = client.subscribe_receive()
            server.notify(
                "receive",
                subscription,
                {"envelope": {"sourceNumber": "+15550100", "timestamp": 42}},
            )
            server.push_call_event(1, CALL_EVENT_RINGING)
            received: list[dict[str, Any]] = []
            for event in client.receive_events(timeout=1.0):
                received.append(event)
                break
    assert received == [{"envelope": {"sourceNumber": "+15550100", "timestamp": 42}}]


def test_poll_returns_none_when_idle(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path, timeout=5) as client:
            assert client.poll(timeout=0.05) is None


def test_transport_error_after_server_close(socket_path: Path) -> None:
    """A daemon that dies mid-session must raise, not hang forever."""
    with fake_daemon(socket_path, default_handlers()) as server:
        client = rpc.RpcClient(socket_path, timeout=5)
        client.connect()
        assert client.version() == "0.14.9"
        server.close()
        with pytest.raises(rpc.TransportError):
            client.call("receive", timeout=2)
        client.close()


def test_call_without_connect_is_a_clear_error(socket_path: Path) -> None:
    client = rpc.RpcClient(socket_path)
    with pytest.raises(rpc.TransportError) as excinfo:
        client.call("version")
    assert "not connected" in str(excinfo.value)


def test_params_are_sent_verbatim(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path) as client:
            client.call("version", {"account": "+15550100"})
    assert server.requests[0]["params"] == {"account": "+15550100"}


def test_notification_subscription_property() -> None:
    notification = rpc.Notification(
        method="callEvent", params={"subscription": 3, "result": {"a": 1}}
    )
    assert notification.subscription == 3
    assert notification.result == {"a": 1}
    assert rpc.Notification(method="callEvent", params={"subscription": "x"}).subscription is None


def test_garbage_lines_are_ignored(socket_path: Path) -> None:
    """signal-cli logs to stderr, but a stray stdout line must not corrupt the stream."""
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            assert client.version() == "0.14.9"
            client._dispatch(b"not json at all")
            client._dispatch(b"")
            client._dispatch(b"[1,2,3]")
            assert client.version() == "0.14.9"


def test_request_framing_is_one_json_per_line(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path) as client:
            client.version()
            client.subscribe_call_events()
    assert all(request["jsonrpc"] == "2.0" for request in server.requests)
    assert len(server.requests) == 2


def test_json_line_roundtrip_matches_server_expectation() -> None:
    payload = json.dumps({"jsonrpc": "2.0", "method": "version", "id": 1}) + "\n"
    assert payload.endswith("\n")
    assert json.loads(payload)["method"] == "version"
