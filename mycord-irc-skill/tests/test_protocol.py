"""Tests for the daemon wire protocol."""

from mycord_irc_repl.protocol import decode, encode, failure, success


def test_encode_decode_round_trip() -> None:
    message = {"op": "eval", "code": "1 + 1", "timeout": 5}

    assert decode(encode(message)) == message


def test_success_includes_optional_fields() -> None:
    assert success(result="pong", extra={"connected": True}) == {
        "ok": True,
        "result": "pong",
        "stdout": "",
        "error": None,
        "connected": True,
    }


def test_failure_includes_captured_stdout() -> None:
    assert failure(error="bad request", stdout="before error") == {
        "ok": False,
        "result": None,
        "stdout": "before error",
        "error": "bad request",
    }
