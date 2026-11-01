"""Tests for the read verbs and the send gate.

Everything runs against the fake daemon. The send tests are the important ones: the property
under test is that *nothing leaves* without an explicit confirm, and that the dry-run preview
is byte-for-byte the request that would have been sent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_daemon import FakeError, default_handlers, fake_daemon

from mycord_signal import reads, rpc
from mycord_signal import send as send_mod

# --------------------------------------------------------------------------- reads


def test_contacts_returns_entries(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["listContacts"] = lambda _request: [
        {
            "uuid": "5d87468c-2512-4a0a-9a64-5e1066f5fc22",
            "number": None,
            "name": "",
            "profile": {"givenName": "Aurora", "familyName": None},
            "isBlocked": False,
        }
    ]
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            result = reads.contacts(client)
    assert len(result) == 1
    assert result.items[0]["uuid"].startswith("5d87468c")
    assert result.note is None


def test_contacts_empty_has_a_note(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            result = reads.contacts(client)
    assert result.items == []
    assert result.note is not None


def test_contact_label_prefers_name_then_profile() -> None:
    # Shape verified live on this host: both real contacts had null numbers.
    with_name = {"name": "Alex", "number": "+15550100"}
    assert reads.contact_label(with_name) == "Alex <+15550100>"

    profile_only = {
        "name": "",
        "number": None,
        "uuid": "bbbb700c-97d8-4d33-a93f-58378cbb2510",
        "profile": {"givenName": "z", "familyName": None},
    }
    assert reads.contact_label(profile_only) == "z <bbbb700c-97d8-4d33-a93f-58378cbb2510>"

    bare = {"uuid": "abc"}
    assert reads.contact_label(bare) == "abc"
    assert reads.contact_label({}) == "?"


def test_groups_empty(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            result = reads.groups(client)
    assert result.items == []
    assert result.note == "no groups"


def test_calls_empty(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            assert reads.calls(client).items == []


def test_drain_messages_returns_envelopes(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["receive"] = lambda _request: [
        {
            "envelope": {
                "sourceNumber": "+15550100",
                "timestamp": 1,
                "dataMessage": {"message": "hi"},
            }
        }
    ]
    with fake_daemon(socket_path, handlers) as server:
        with rpc.RpcClient(socket_path) as client:
            result = reads.drain_messages(client, timeout=0.1)
    assert len(result.items) == 1
    assert server.requests[0]["params"]["timeout"] == 0.1


def test_drain_messages_treats_already_receiving_as_a_note(socket_path: Path) -> None:
    """Verified live: a daemon started with the default receive mode refuses `receive`.

    The message was exactly "Receive command cannot be used if messages are already being
    received." with code -1. It must become a note pointing at the stream path, not an
    exception — the daemon is working correctly, it just cannot be asked twice.
    """

    def already(_request: dict[str, Any]) -> Any:
        raise FakeError(
            rpc.ERROR_USER,
            "Receive command cannot be used if messages are already being received.",
        )

    handlers = default_handlers()
    handlers["receive"] = already
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            result = reads.drain_messages(client, timeout=0.1)
    assert result.items == []
    assert result.note is not None
    assert "already receiving" in result.note


def test_drain_messages_reraises_other_errors(socket_path: Path) -> None:
    def broken(_request: dict[str, Any]) -> Any:
        raise FakeError(rpc.ERROR_IO, "Error while receiving messages: socket closed")

    handlers = default_handlers()
    handlers["receive"] = broken
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(rpc.RpcError) as excinfo:
                reads.drain_messages(client, timeout=0.1)
    assert excinfo.value.code == rpc.ERROR_IO


def test_drain_messages_forwards_max_messages(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path) as client:
            reads.drain_messages(client, timeout=0.1, max_messages=5)
    assert server.requests[0]["params"]["maxMessages"] == 5


def test_stream_messages_collects_notifications(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path, timeout=10) as client:
            import threading

            def push() -> None:
                server.notify("receive", 0, {"envelope": {"timestamp": 1}})
                server.notify("receive", 0, {"envelope": {"timestamp": 2}})

            threading.Thread(target=push, daemon=True).start()
            result = reads.stream_messages(client, limit=2, seconds=5.0)
    assert [entry["envelope"]["timestamp"] for entry in result.items] == [1, 2]


def test_stream_messages_times_out_with_a_note(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()):
        with rpc.RpcClient(socket_path) as client:
            result = reads.stream_messages(client, limit=0, seconds=0.1)
    assert result.items == []
    assert result.note is not None


def test_summarise_envelope_reads_fields() -> None:
    envelope = {
        "sourceNumber": "+15550100",
        "timestamp": 42,
        "dataMessage": {"message": "hello\nthere"},
    }
    assert reads.summarise(envelope) == "+15550100 @42 hello there"
    assert "(no text)" in reads.summarise({"sourceUuid": "abc", "timestamp": 1})


# --------------------------------------------------------------------------- send


def test_send_plan_params_use_daemon_field_names() -> None:
    plan = send_mod.SendPlan(
        recipients=["+15550100"],
        message="hi",
        quote_timestamp=7,
        edit_timestamp=8,
    )
    assert plan.params() == {
        "recipient": ["+15550100"],
        "message": "hi",
        "quoteTimestamp": 7,
        "editTimestamp": 8,
    }


def test_send_plan_rejects_missing_recipient() -> None:
    plan = send_mod.SendPlan(message="hi")
    assert any("no recipient" in issue for issue in plan.problems())


def test_send_plan_rejects_empty_payload() -> None:
    plan = send_mod.SendPlan(recipients=["+15550100"])
    assert any("nothing to send" in issue for issue in plan.problems())


def test_send_plan_rejects_overlong_message() -> None:
    plan = send_mod.SendPlan(recipients=["+15550100"], message="x" * 2001)
    assert any("over the" in issue for issue in plan.problems())


def test_send_plan_accepts_attachment_without_text() -> None:
    plan = send_mod.SendPlan(recipients=["+15550100"], attachments=["/tmp/a.png"])
    assert plan.problems() == []


def test_send_dry_run_never_touches_the_daemon(socket_path: Path) -> None:
    """The dry run must not issue a request at all, not merely skip its result."""
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path) as client:
            outcome = send_mod.send(
                send_mod.SendPlan(recipients=["+15550100"], message="hi"), client, dry_run=True
            )
    assert outcome["dry_run"] is True
    assert server.requests == []


def test_send_dry_run_preview_matches_the_real_request(socket_path: Path) -> None:
    plan = send_mod.SendPlan(recipients=["+15550100"], message="hi")
    handlers = default_handlers()
    handlers["send"] = lambda _request: {"timestamp": 1700000000000}

    with fake_daemon(socket_path, handlers) as server:
        with rpc.RpcClient(socket_path) as client:
            preview = send_mod.send(plan, client, dry_run=True)["request"]
            send_mod.send(plan, client, dry_run=False)

    assert preview["method"] == "send"
    assert server.requests[0]["method"] == preview["method"]
    assert server.requests[0]["params"] == preview["params"]


def test_send_confirmed_calls_the_daemon(socket_path: Path) -> None:
    handlers = default_handlers()
    handlers["send"] = lambda _request: {"timestamp": 1700000000000}
    with fake_daemon(socket_path, handlers) as server:
        with rpc.RpcClient(socket_path) as client:
            outcome = send_mod.send(
                send_mod.SendPlan(recipients=["+15550100"], message="hi"), client, dry_run=False
            )
    assert outcome["dry_run"] is False
    assert server.requests[0]["method"] == "send"
    assert "1700000000000" in send_mod.result_summary(outcome["result"])


def test_send_refuses_an_invalid_plan_before_the_wire(socket_path: Path) -> None:
    with fake_daemon(socket_path, default_handlers()) as server:
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(ValueError):
                send_mod.send(send_mod.SendPlan(message="hi"), client, dry_run=False)
    assert server.requests == []


def test_send_surfaces_daemon_errors(socket_path: Path) -> None:
    """A send that the daemon rejects must raise, never look like success.

    Live probe: a send with no recipient answers ``-1 "No recipients given"``. Reporting that
    as sent would be the worst possible failure for this verb.
    """

    def refuse(_request: dict[str, Any]) -> Any:
        raise FakeError(rpc.ERROR_USER, "No recipients given")

    handlers = default_handlers()
    handlers["send"] = refuse
    with fake_daemon(socket_path, handlers):
        with rpc.RpcClient(socket_path) as client:
            with pytest.raises(rpc.RpcError) as excinfo:
                send_mod.send(
                    send_mod.SendPlan(recipients=["+15550100"], message="hi"),
                    client,
                    dry_run=False,
                )
    assert excinfo.value.code == rpc.ERROR_USER
    assert "No recipients" in excinfo.value.message


def test_result_summary_report_shape() -> None:
    assert send_mod.result_summary({"timestamp": 1}) == "sent (timestamp 1)"
    assert send_mod.result_summary({"timestamp": 1}) == "sent (timestamp 1)"
    assert "weird" in send_mod.result_summary({"weird": "shape"})


def test_preview_is_json_serialisable() -> None:
    payload = send_mod.preview(send_mod.SendPlan(recipients=["+1"], message="x"))
    assert json.loads(json.dumps(payload))["params"]["message"] == "x"
