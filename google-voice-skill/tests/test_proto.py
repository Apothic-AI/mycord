"""Positional proto3-JSON decoding.

The fixtures are trimmed but structurally real responses, captured from a live account.
Each test names the mistake it prevents, because that is why the field indices are
documented at all: `f2` is your own number, `f5` is *not* direction, and `thread/list`
takes the sync token first.

The negative tests are the important ones here. Two separate attempts to infer message
direction were disproved against the live account, so the module reports a message *kind*
and the tests pin the measurements that ruled direction out: `f5` varies inside an
all-inbound thread, and kind/type clusters appear on both sides of a conversation.
"""

from __future__ import annotations

from gvoice_skill import proto


def _message(
    text: str,
    *,
    outbound: bool = False,
    peer: str = "+19542701292",
    when: int = 1788556817104,
    msg_id: str = "abc",
    thread_id: str = "t.+19542701292",
    kind: int = 10,
    type_: int = 5,
    f5: int = 1,
) -> list:
    """Build a message row. `f5` defaults to 1 because most real rows have it."""
    row = [None] * 30
    row[proto.MSG_ID] = msg_id
    row[proto.MSG_TIMESTAMP_MS] = when
    row[proto.MSG_OWN_NUMBER] = "+14153356728"
    row[proto.MSG_PARTICIPANTS] = [peer, peer, None, None, None, None, 0]
    row[proto.MSG_KIND] = kind
    row[proto.MSG_FLAG] = f5
    row[proto.MSG_TEXT] = text
    row[proto.MSG_TYPE] = type_
    row[proto.MSG_MODE] = 0 if outbound else 1
    row[proto.MSG_PEER_NUMBER] = peer
    row[proto.MSG_THREAD_ID] = thread_id
    return row


THREAD_HEAD = ["t.+19542701292", 1]


def test_f2_is_never_the_sender() -> None:
    """f2 held the account's own number in all 69 rows sampled across 12 threads."""
    for outbound in (False, True):
        message = proto.parse_message(_message("x", outbound=outbound))
        assert message is not None
        assert message.peer == "+19542701292"  # from f15, not f2


def test_no_direction_is_invented() -> None:
    """Reporting "in" for everything is plausible-looking and wrong; report kind instead."""
    message = proto.parse_message(_message("hello"))
    assert message is not None
    assert not hasattr(message, "direction")
    assert "direction" not in message.to_dict()
    assert message.kind == "sms"


def test_f5_does_not_affect_the_message() -> None:
    """An all-inbound automated thread produced f5 = 1,1,0,1 — so f5 is not direction."""
    decoded = [proto.parse_message(_message("same", f5=f5)) for f5 in (0, 1)]
    assert decoded[0] is not None and decoded[1] is not None
    assert decoded[0].to_dict() == decoded[1].to_dict()


def test_mms_is_labelled_by_kind_not_direction() -> None:
    """kind 11 / type 6 appears on both sides of a real conversation."""
    message = proto.parse_message(_message("kid's text", kind=11, type_=6))
    assert message is not None
    assert message.kind == "mms"


def test_unknown_kind_is_reported_verbatim() -> None:
    row = _message("x")
    row[proto.MSG_KIND] = 42
    assert proto.message_kind(row) == "kind-42"
    row[proto.MSG_KIND] = None
    assert proto.message_kind(row) == "unknown"


def test_parse_message_reads_text_and_timestamp() -> None:
    message = proto.parse_message(_message("hello"))
    assert message is not None
    assert message.text == "hello"
    assert message.thread_id == "t.+19542701292"
    assert message.when.startswith("2026-09-04")


def test_parse_message_rejects_short_rows() -> None:
    assert proto.parse_message(["only", "three"]) is None
    assert proto.parse_message("not a row") is None


def test_thread_list_returns_threads_and_next_token() -> None:
    result = [[THREAD_HEAD, ["t.+16822191443", 0]], "1792541283722"]
    threads, token = proto.parse_thread_list(result)
    assert [t.id for t in threads] == ["t.+19542701292", "t.+16822191443"]
    assert token == "1792541283722"


def test_thread_list_with_a_bare_page_size_yields_no_threads() -> None:
    """The real failure mode: [pageSize] returns [null, null, "v1-10-…"], not an error."""
    result = [None, None, "v1-1010-1792876007297910"]
    threads, token = proto.parse_thread_list(result)
    assert threads == []
    assert token is None


def test_thread_get_reads_messages_under_the_envelope() -> None:
    result = [[THREAD_HEAD[0], 1, [_message("hi"), _message("there", outbound=True)]]]
    thread = proto.parse_thread_get(result)
    assert thread is not None
    assert thread.id == "t.+19542701292"
    assert thread.unread is True
    assert [m.text for m in thread.messages] == ["hi", "there"]


def test_thread_get_tolerates_a_bare_thread_tuple() -> None:
    thread = proto.parse_thread_get([THREAD_HEAD[0], 0, [_message("x")]])
    assert thread is not None
    assert len(thread.messages) == 1


def test_search_takes_the_thread_id_from_group_6_not_group_0() -> None:
    """group[0] is the matched message's id ("s.<sha1>"); group[6] is the thread id."""
    group = [
        "s.3c0a4ab90cda3643f6d0044f61da32306b3c0905",
        1,
        [_message("696850 is your Link code", thread_id="t.+18335191811")],
        None,
        None,
        [1, 2],
        "t.+18335191811",
        1,
    ]
    threads = proto.parse_search([[group]])
    assert len(threads) == 1
    assert threads[0].id == "t.+18335191811"
    assert threads[0].unread is True
    assert threads[0].messages[0].text == "696850 is your Link code"


def test_search_falls_back_to_the_message_thread_id() -> None:
    """If a group omits its thread id, the decoded message still carries one."""
    group = ["s.abc", 1, [_message("x", thread_id="t.+1555")], None, None, [1, 2]]
    threads = proto.parse_search([[group]])
    assert threads[0].id == "t.+1555"


def test_search_group_per_match_not_per_conversation() -> None:
    """One group object per matched message; two matches can share a thread id."""

    def group(text: str) -> list:
        return ["s.x", 1, [_message(text, thread_id="t.+1833")], None, None, [1, 2], "t.+1833"]

    threads = proto.parse_search([[group("one"), group("two")]])
    assert [t.id for t in threads] == ["t.+1833", "t.+1833"]
    assert sum(len(t.messages) for t in threads) == 2


def test_search_with_no_matches_is_an_empty_list() -> None:
    assert proto.parse_search([]) == []
    assert proto.parse_search([[]]) == []


def test_warmsync_token_is_the_integer_not_the_list() -> None:
    assert proto.warmsync_token([None, [2]]) == 2


def test_warmsync_token_absent() -> None:
    assert proto.warmsync_token([None, None]) is None


def test_message_to_dict_is_json_ready() -> None:
    message = proto.parse_message(_message("plain"))
    assert message is not None
    payload = message.to_dict()
    assert set(payload) == {"id", "threadId", "at", "kind", "peer", "text"}
    assert payload["kind"] == "sms"
