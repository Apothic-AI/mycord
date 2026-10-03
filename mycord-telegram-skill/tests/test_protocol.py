"""Tests for the newline-delimited JSON wire protocol."""

import json

import pytest

from mycord_telegram_repl.protocol import (
    OP_EVAL,
    PROTOCOL_VERSION,
    decode,
    encode,
    failure,
    success,
)


@pytest.mark.unit
def test_encode_produces_single_newline_frame() -> None:
    frame = encode({"op": OP_EVAL, "code": "1 + 1"})
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1


@pytest.mark.unit
def test_round_trip_preserves_payload() -> None:
    payload = {"op": "eval", "code": "print('hi')", "timeout": 12.5}
    assert decode(encode(payload)) == payload


@pytest.mark.unit
def test_decode_accepts_str_and_bytes() -> None:
    raw = json.dumps({"op": "ping"})
    assert decode(raw) == {"op": "ping"}
    assert decode(raw.encode()) == {"op": "ping"}


@pytest.mark.unit
def test_success_frame_shape() -> None:
    frame = success(result="42", stdout="noise")
    assert frame["ok"] is True
    assert frame["result"] == "42"
    assert frame["stdout"] == "noise"
    assert frame["error"] is None


@pytest.mark.unit
def test_success_extra_fields_are_merged() -> None:
    frame = success(result="x", extra={"connected": True})
    assert frame["connected"] is True


@pytest.mark.unit
def test_failure_frame_shape() -> None:
    frame = failure(error="boom")
    assert frame["ok"] is False
    assert frame["error"] == "boom"
    assert frame["result"] is None


@pytest.mark.unit
def test_encode_falls_back_to_str_for_unserialisable() -> None:
    frame = encode({"when": object()})
    assert json.loads(frame)["when"].startswith("<object object")


@pytest.mark.unit
def test_protocol_version_is_declared() -> None:
    assert isinstance(PROTOCOL_VERSION, int)
