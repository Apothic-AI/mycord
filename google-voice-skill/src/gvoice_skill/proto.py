"""Proto3-JSON field decoding for the Voice API's positional responses.

The API returns nested positional arrays, not objects. Field positions are documented
here once so no other module has to remember them. Three of them are worth the comment
they get below, because the obvious guesses are wrong:

* `thread/list` takes `[syncToken, pageSize]`. With `[pageSize]` alone it returns a token
  and no threads, which looks like an empty inbox rather than a wired-backwards request.
* `f2` is always the account's own number, so it cannot tell you who sent anything.

**There is no direction field here, and this module does not pretend otherwise.** Two
attempts to infer one were disproved against a real two-way conversation:

* `f5` looked like a direction flag, but an all-inbound automated thread produced
  `f5 = 1, 1, 0, 1` across four messages that were all received.
* `f4`/`f12` (kind/type) split rows into exactly two clusters across 69 messages in 12
  threads — SMS (10/5) and MMS or chat-style (11/6) — and both clusters appear on both
  sides of a conversation. `f17` is set on every MMS row and null on every SMS row, so
  it tracks the message type rather than the sender. `f2` equals the account's own
  number in all 69 rows, `f13` was `1` in all of them.

So a message carries `kind` ("sms" / "mms") and not a direction. Reporting
`direction: "in"` for everything renders perfectly plausibly and is wrong, which is
exactly the failure this comment exists to stop someone repeating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# --- thread/list response ------------------------------------------------------------
LIST_THREADS = 0
LIST_SYNC_TOKEN = 1

# --- thread/list and thread/get thread tuple -----------------------------------------
THREAD_ID = 0
THREAD_UNREAD = 1
THREAD_MESSAGES = 2

# --- message tuple (api2thread/get) --------------------------------------------------
MSG_ID = 0
MSG_TIMESTAMP_MS = 1
MSG_OWN_NUMBER = 2
MSG_PARTICIPANTS = 3
MSG_KIND = 4  # 10 = SMS, 11 = MMS / chat-style message
MSG_FLAG = 5  # varies within a single inbound thread; meaning unknown, NOT direction
MSG_TEXT = 9
MSG_TYPE = 12  # 5 pairs with kind 10 (SMS), 6 pairs with kind 11 (MMS)
MSG_MODE = 13  # 1 in every message observed; carries nothing usable
MSG_PEER_NUMBER = 15
MSG_THREAD_ID = 29

# --- thread/search response ----------------------------------------------------------
# Each match is its own group object at result[0]. Its message rows live at group[2], and
# the *thread* id is group[6] — group[0] is the matched message's own id ("s.<sha1>"), so
# using it as a thread id yields ids that `thread/get` rejects.
SEARCH_GROUPS = 0
SEARCH_GROUP_MESSAGE_ID = 0
SEARCH_GROUP_MESSAGES = 2
SEARCH_GROUP_THREAD_ID = 6
SEARCH_GROUP_UNREAD = 7

# kind/type as observed, for labelling only. Deliberately not a direction claim.
KINDS: dict[int, str] = {10: "sms", 11: "mms"}


@dataclass
class Message:
    id: str | None
    thread_id: str | None
    timestamp_ms: int | None
    kind: str  # "sms" | "mms" | "kind-<n>"; not a direction
    peer: str | None
    text: str | None

    @property
    def when(self) -> str:
        if not self.timestamp_ms:
            return "?"
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=UTC).isoformat(
            timespec="seconds"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "threadId": self.thread_id,
            "at": self.when,
            "kind": self.kind,
            "peer": self.peer,
            "text": self.text,
        }


@dataclass
class Thread:
    id: str | None
    unread: bool
    messages: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "unread": self.unread,
            "messages": [m.to_dict() for m in self.messages],
        }


def _at(row: Any, index: int) -> Any:
    if isinstance(row, list) and 0 <= index < len(row):
        return row[index]
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def message_kind(row: Any) -> str:
    """Label the message type. Says nothing about who sent it — see the module docstring."""
    kind = _at(row, MSG_KIND)
    if isinstance(kind, int):
        return KINDS.get(kind, f"kind-{kind}")
    return "unknown"


def parse_message(row: Any) -> Message | None:
    """Decode one positional message tuple. Returns None for unparseable rows."""
    if not isinstance(row, list) or len(row) <= MSG_TEXT:
        return None
    return Message(
        id=_text(_at(row, MSG_ID)),
        thread_id=_text(_at(row, MSG_THREAD_ID)),
        timestamp_ms=(
            _at(row, MSG_TIMESTAMP_MS) if isinstance(_at(row, MSG_TIMESTAMP_MS), int) else None
        ),
        kind=message_kind(row),
        peer=_text(_at(row, MSG_PEER_NUMBER)),
        text=_text(_at(row, MSG_TEXT)),
    )


def parse_thread(row: Any, *, with_messages: list[Message] | None = None) -> Thread | None:
    """Decode a thread tuple from `thread/list` or `thread/get`.

    `thread/list` rows stop before the message list; `thread/get` returns the same head
    with message rows at index 2. Callers pass the decoded messages in.
    """
    if not isinstance(row, list) or not row:
        return None
    raw_messages = _at(row, THREAD_MESSAGES)
    messages = with_messages
    if messages is None and isinstance(raw_messages, list):
        messages = [m for m in (parse_message(r) for r in raw_messages) if m]
    return Thread(
        id=_text(_at(row, THREAD_ID)),
        unread=bool(_at(row, THREAD_UNREAD)),
        messages=messages or [],
    )


def parse_thread_list(result: Any) -> tuple[list[Thread], str | None]:
    """Decode `api2thread/list` into (threads, next sync token)."""
    rows = _at(result, LIST_THREADS)
    token = _at(result, LIST_SYNC_TOKEN)
    threads = []
    if isinstance(rows, list):
        threads = [t for t in (parse_thread(r) for r in rows if isinstance(r, list)) if t]
    return threads, token if isinstance(token, str) else None


def parse_thread_get(result: Any) -> Thread | None:
    """Decode `api2thread/get`, whose envelope is a one-element list holding the thread."""
    if isinstance(result, list) and result and isinstance(result[0], list):
        return parse_thread(result[0])
    return parse_thread(result)


def parse_search(result: Any) -> list[Thread]:
    """Decode `api2thread/search` into one Thread per matched message.

    Each group object is `[messageId, ?, [message, ...], …, threadId, unread, …]`.
    `group[0]` is the *message* id, not the thread id; `group[6]` is the thread. Getting
    that backwards produces ids that look right and fail on `thread/get`.
    """
    groups = _at(result, SEARCH_GROUPS)
    if not isinstance(groups, list):
        return []
    threads: list[Thread] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        raw_messages = _at(group, SEARCH_GROUP_MESSAGES)
        messages = []
        if isinstance(raw_messages, list):
            messages = [m for m in (parse_message(r) for r in raw_messages) if m]
        thread_id = _text(_at(group, SEARCH_GROUP_THREAD_ID))
        if thread_id is None and messages:
            thread_id = messages[0].thread_id
        if thread_id is None:
            continue
        threads.append(
            Thread(
                id=thread_id,
                unread=bool(_at(group, SEARCH_GROUP_UNREAD)),
                messages=messages,
            )
        )
    return threads


def warmsync_token(result: Any) -> int | str | None:
    """Pull the sync token out of an `inbox/warmsync` result.

    The response is `[null, [token]]`; the token is a small integer, and `thread/list`
    needs it rather than the epoch-ms token it returns itself.
    """
    inner = _at(result, 1)
    if isinstance(inner, list) and inner:
        token = inner[0]
        return token if isinstance(token, int | str) else None
    return None
