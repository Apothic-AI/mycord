"""Read verbs: contacts, groups, calls, and message draining.

Every one of these returns whatever the daemon returned, structurally — dicts and lists with
the field names signal-cli produces. There is no re-shaping layer, because a shape-mapping
layer is exactly where a protocol change silently turns into wrong data instead of a loud
error.

One rule that is easy to get wrong: ``receive`` **fails** when the daemon is already
receiving (``Receive command cannot be used if messages are already being received``,
code ``-1``). A daemon started with the default ``--receive-mode on-start`` is always in
that state, so :func:`drain_messages` treats that specific error as "use the existing
subscription instead" rather than as a failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import rpc

#: The daemon refuses `receive` while already receiving. Matched on the error *code* first
#: (USER_ERROR) and only then on the message, so a wording change cannot flip the branch to
#: something wrong — the worst outcome there is a clear error, not a silent success.
_ALREADY_RECEIVING = "already being received"


@dataclass
class ReadResult:
    """What a read verb produced, plus why it produced nothing when it produced nothing."""

    items: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None

    def __len__(self) -> int:
        return len(self.items)


def contacts(client: rpc.RpcClient) -> ReadResult:
    """Return the address book.

    Verified live against a linked account: ``listContacts`` returns entries with ``uuid``,
    ``number`` (often null for privacy-preserving contacts), ``name``, ``nickName`` and a
    nested ``profile``. Real output on this host had two contacts with populated profile
    given names and null phone numbers, so callers must not assume ``number`` is present.
    """
    result = client.call(rpc.METHOD_LIST_CONTACTS) or []
    items = (
        [entry for entry in result if isinstance(entry, dict)] if isinstance(result, list) else []
    )
    note = None if items else "no contacts returned (or none shared yet)"
    return ReadResult(items=items, note=note)


def groups(client: rpc.RpcClient) -> ReadResult:
    """Return group v2 records. ``listGroups`` includes members when the daemon reports them."""
    result = client.call(rpc.METHOD_LIST_GROUPS) or []
    items = (
        [entry for entry in result if isinstance(entry, dict)] if isinstance(result, list) else []
    )
    return ReadResult(items=items, note=None if items else "no groups")


def calls(client: rpc.RpcClient) -> ReadResult:
    """Return active calls (empty when idle)."""
    result = client.call(rpc.METHOD_LIST_CALLS) or []
    items = (
        [entry for entry in result if isinstance(entry, dict)] if isinstance(result, list) else []
    )
    return ReadResult(items=items)


def drain_messages(
    client: rpc.RpcClient, timeout: float = 3.0, max_messages: int | None = None
) -> ReadResult:
    """Ask the server for new messages and return the envelopes it hands back.

    ``receive`` is synchronous on the server: it blocks for ``timeout`` seconds and returns
    a list. That is why the timeout is a parameter and not a client-side deadline — passing a
    client-side timeout smaller than the server-side one would abandon a request the server
    is still happily working on.

    When the daemon is already receiving (the default), the server rejects the call. That is
    reported as a ``note`` rather than raised, and the fix is ``subscribeReceive`` + drain
    notifications, which is what :func:`stream_messages` does.
    """
    params: dict[str, Any] = {"timeout": timeout}
    if max_messages is not None:
        params["maxMessages"] = max_messages
    try:
        result = client.call(rpc.METHOD_RECEIVE, params, timeout=timeout + 30.0)
    except rpc.RpcError as exc:
        if exc.code == rpc.ERROR_USER and _ALREADY_RECEIVING in exc.message:
            return ReadResult(
                note=(
                    "the daemon is already receiving (default --receive-mode on-start), so a "
                    "second receive is refused; use the live stream instead"
                )
            )
        raise
    items = (
        [entry for entry in result if isinstance(entry, dict)] if isinstance(result, list) else []
    )
    return ReadResult(items=items, note=None if items else "no new messages in that window")


def stream_messages(client: rpc.RpcClient, limit: int = 0, seconds: float = 30.0) -> ReadResult:
    """Subscribe (if needed) and collect ``receive`` notifications for ``seconds``.

    This is the path that works alongside a running daemon. ``limit`` stops early. The
    deadline is wall-clock because ``events()`` polls in short slices and would otherwise
    never expire while the socket stays open.
    """
    client.subscribe_receive()
    items: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(seconds, 0.0)
    while time.monotonic() < deadline:
        notification = client.poll(timeout=min(0.5, max(deadline - time.monotonic(), 0.0)))
        if notification is None or notification.method != "receive":
            continue
        result = notification.result
        if isinstance(result, dict):
            items.append(result)
            if limit and len(items) >= limit:
                break
    return ReadResult(items=items, note=None if items else "no messages arrived in that window")


def summarise(envelope: dict[str, Any]) -> str:
    """One-line rendering of an envelope for human output.

    Reads fields, never string-matches them: a message with no text (a reaction, a typing
    indicator, a call log) renders as ``(no text)`` rather than an empty line.
    """
    source = envelope.get("sourceNumber") or envelope.get("sourceUuid") or envelope.get("source")
    data = envelope.get("dataMessage") or envelope.get("editMessage") or {}
    text = data.get("message") if isinstance(data, dict) else None
    timestamp = envelope.get("timestamp")
    parts = [str(source or "?")]
    if timestamp is not None:
        parts.append(f"@{timestamp}")
    if isinstance(text, str) and text:
        parts.append(text.replace("\n", " ")[:200])
    else:
        parts.append("(no text)")
    return " ".join(parts)


def contact_label(entry: dict[str, Any]) -> str:
    """Render one contact as ``name <identifier>``.

    Both halves can be missing in practice: a contact can have a null ``number`` (verified on
    this host — both real contacts had null numbers and only profile names), and an unnamed
    contact still has a uuid. Falling back rather than assuming is what keeps the output
    truthful.
    """
    profile = entry.get("profile")
    name = entry.get("name") or entry.get("nickName")
    if not name and isinstance(profile, dict):
        given = profile.get("givenName")
        family = profile.get("familyName")
        if given or family:
            name = " ".join(part for part in (given, family) if part)
    identifier = entry.get("number") or entry.get("uuid") or entry.get("username")
    if name and identifier:
        return f"{name} <{identifier}>"
    return str(name or identifier or "?")


def summarise_contact(entry: dict[str, Any]) -> str:
    """Alias kept for the CLI's human output path."""
    return contact_label(entry)
