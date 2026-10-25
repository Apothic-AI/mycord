"""Read verbs: everything slackdump does, wrapped so an agent can call it in one step.

slackdump is the read half because it already solves the problems a from-scratch reader
gets wrong: pagination and Slack's per-method rate limits, a users/channels cache, DM and
MPIM resolution, thread fetching, and an archive format. It also has no send capability,
which is why writes go through `api.py` instead.

The wrapper's job is narrow:

* resolve human-friendly channel references (`#general`, a name, an id, a Slack URL) to
  ids, because every slackdump command wants an id and every human has a name;
* keep slackdump's chatter off the caller's stdout, so JSON output stays parseable;
* redact anything a failure echoes back.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import slackdump_binary
from .redact import redact

EXIT_OK = 0
EXIT_ERROR = 1

#: slackdump caches channels for 20 minutes by default. `session` and repeated ad-hoc
#: reads benefit from that cache, so it is left on except where freshness is the point.
DEFAULT_TIMEOUT = 180.0


class ReadError(RuntimeError):
    """slackdump could not answer, or answered with something that is not a read."""


@dataclass
class Channels:
    """One page of a workspace's channel list, as slackdump reports it."""

    raw: list[dict[str, Any]]
    fetched_at: float

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {str(c.get("id")): c for c in self.raw if c.get("id")}

    def by_name(self) -> dict[str, dict[str, Any]]:
        return {str(c.get("name")): c for c in self.raw if c.get("name")}

    def describe(self, channel: dict[str, Any]) -> str:
        """A one-line label: `#name` for channels, `DM user` for direct messages."""
        if channel.get("is_im"):
            return f"DM {channel.get('user') or channel.get('id')}"
        if channel.get("is_mpim"):
            return f"group {channel.get('name') or channel.get('id')}"
        name = channel.get("name") or channel.get("id")
        return f"#{name}"


def _run(
    args: list[str], *, timeout: float = DEFAULT_TIMEOUT, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run slackdump with stderr captured, so a chatty run cannot corrupt JSON output."""
    binary = slackdump_binary()
    try:
        return subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReadError(
            f"slackdump {args[0]} timed out after {timeout:.0f}s; large workspaces are slow "
            "on purpose because Slack throttles pagination"
        ) from exc


def _require_success(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode == 0:
        return
    detail = redact((proc.stderr or proc.stdout or "").strip())
    tail = "\n".join(detail.splitlines()[-12:]) if detail else f"exit {proc.returncode}"
    raise ReadError(f"slackdump {what} failed:\n{tail}")


def channels(*, fresh: bool = False, timeout: float = DEFAULT_TIMEOUT) -> Channels:
    """All visible channels, groups, and DMs.

    Kept as one call because slackdump answers in one payload, and the wrapper resolves
    names from the same list it resolves ids from.
    """
    args = ["list", "channels", "-format", "JSON", "-no-json"]
    if fresh:
        args.append("-no-chan-cache")
    proc = _run(args, timeout=timeout)
    _require_success(proc, "list channels")
    text = (proc.stdout or "").strip()
    if not text:
        raise ReadError(
            "slackdump returned no channel data. This usually means the workspace "
            "credentials are no longer valid; run `mycord-slack session --check`."
        )
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ReadError(f"slackdump channel output was not JSON ({exc})") from exc
    if not isinstance(parsed, list):
        raise ReadError("slackdump channel output was not a JSON list")
    return Channels(
        raw=[c for c in parsed if isinstance(c, dict)],
        fetched_at=time.time(),
    )


def users(*, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    proc = _run(["list", "users", "-format", "JSON", "-no-json"], timeout=timeout)
    _require_success(proc, "list users")
    try:
        parsed = json.loads((proc.stdout or "").strip() or "[]")
    except ValueError as exc:
        raise ReadError(f"slackdump user output was not JSON ({exc})") from exc
    return [u for u in parsed if isinstance(u, dict)] if isinstance(parsed, list) else []


_ARCHIVE_URL = re.compile(r"/archives/([A-Z][A-Z0-9]+)(?:/p(\d+))?")
_DM_URL = re.compile(r"/messages/([A-Z][A-Z0-9]+)")


def resolve_channel(reference: str, listing: Channels | None = None) -> str:
    """Turn `#name`, `name`, `C0123`, or a Slack URL into a channel id.

    Resolution order is id-shape, then name (with or without `#`), then a URL. A failed
    resolution lists the near misses rather than only saying "not found", because the
    usual cause is a DM whose display name is empty and whose id nobody knows.
    """
    ref = reference.strip()
    if not ref:
        raise ReadError("empty channel reference")
    known = listing or channels()

    url_match = _ARCHIVE_URL.search(ref) or _DM_URL.search(ref)
    if url_match:
        return url_match.group(1)

    # A known id wins over shape: Slack's ids vary in length (and the ``new'' ones vary
    # in case), so trusting the listing is more reliable than trusting a regex.
    bare = ref.lstrip("#")
    if bare in known.by_id():
        return bare

    # Otherwise accept the obvious shape without listing the workspace: C channel,
    # D direct message, G private/MPIM.
    if re.fullmatch(r"[A-Z][A-Z0-9]{7,}", bare):
        return bare

    by_name = known.by_name()
    if bare in by_name:
        return str(by_name[bare]["id"])
    lowered = bare.lower()
    for name, channel in by_name.items():
        if name.lower() == lowered:
            return str(channel["id"])

    candidates = [name for name in by_name if lowered in name.lower() or name.lower() in lowered]
    hint = f" close matches: {', '.join(sorted(candidates)[:8])}" if candidates else ""
    dms = [f"{c.get('id')} (user {c.get('user')})" for c in known.raw if c.get("is_im")]
    dm_hint = f"; DMs: {', '.join(dms[:5])}" if dms else ""
    raise ReadError(f"no channel matching {reference!r}{hint}{dm_hint}")


@dataclass
class Message:
    """One message, reduced to what an agent reads, with the raw record kept."""

    ts: str
    text: str
    user: str = ""
    kind: str = ""
    thread_ts: str = ""
    reply_count: int = 0
    raw: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.raw is None:
            self.raw = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "text": self.text,
            "user": self.user,
            "kind": self.kind,
            "thread_ts": self.thread_ts,
            "reply_count": self.reply_count,
        }


def _to_message(raw: dict[str, Any]) -> Message:
    return Message(
        ts=str(raw.get("ts") or ""),
        text=str(raw.get("text") or ""),
        user=str(raw.get("user") or raw.get("bot_id") or ""),
        kind=str(raw.get("subtype") or raw.get("type") or "message"),
        thread_ts=str(raw.get("thread_ts") or ""),
        reply_count=int(raw.get("reply_count") or 0),
        raw=raw,
    )


def dump(
    target: str,
    *,
    limit: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Message]:
    """Fetch one conversation (or one thread) from Slack, newest-last.

    `target` is a channel id, or `channel:thread_ts` for a single thread — slackdump's
    own notation. Messages come back in the order slackdump wrote them, which is
    chronological; `limit` keeps the *most recent* `limit` messages, which is what a
    caller asking "what's new" means.
    """
    with tempfile.TemporaryDirectory(prefix="mycord-slack-dump-") as scratch:
        out = Path(scratch)
        args = ["dump", "-files=false", "-o", str(out), target]
        proc = _run(args, timeout=timeout)
        _require_success(proc, "dump")
        files = sorted(out.glob("*.json"))
        if not files:
            raise ReadError(
                f"slackdump dumped nothing for {target!r}. That can mean the channel id is "
                "wrong, or that this account is not a member of it."
            )
        messages: list[dict[str, Any]] = []
        for path in files:
            try:
                payload = json.loads(path.read_text())
            except ValueError as exc:
                raise ReadError(f"slackdump dump file {path.name} was not JSON ({exc})") from exc
            if not isinstance(payload, dict):
                continue
            batch = payload.get("messages")
            if isinstance(batch, list):
                messages.extend(m for m in batch if isinstance(m, dict))
    ordered = sorted(messages, key=_ts_key)
    if limit is not None and limit > 0:
        ordered = ordered[-limit:]
    return [_to_message(m) for m in ordered]


def _ts_key(raw: dict[str, Any]) -> float:
    try:
        return float(str(raw.get("ts") or 0))
    except ValueError:
        return 0.0


@dataclass
class SearchHit:
    """One `search.messages` match, flattened."""

    ts: str
    text: str
    channel_id: str = ""
    channel_name: str = ""
    user: str = ""
    username: str = ""
    permalink: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "text": self.text,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "user": self.user,
            "username": self.username,
            "permalink": self.permalink,
        }


def _search_via_sqlite(directory: Path) -> list[dict[str, Any]]:
    """Read slackdump's own search store.

    `slackdump search messages` writes `slackdump.sqlite` and cannot convert search
    results to the export/dump formats (both fail with "no data found" / "empty users
    data base" — verified against a real result). So its SEARCH_MESSAGE table *is* the
    output format, and reading it is the supported route rather than a workaround.
    """
    import sqlite3

    database = directory / "slackdump.sqlite"
    if not database.is_file():
        return []
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT CHANNEL_ID, CHANNEL_NAME, TS, TXT, DATA FROM SEARCH_MESSAGE ORDER BY TS"
        ).fetchall()
    finally:
        connection.close()
    hits: list[dict[str, Any]] = []
    for channel_id, channel_name, ts, text, data in rows:
        record: dict[str, Any] = {}
        if isinstance(data, str) and data:
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    record = parsed
            except ValueError:
                record = {}
        raw_channel = record.get("channel")
        channel: dict[str, Any] = raw_channel if isinstance(raw_channel, dict) else {}
        hits.append(
            {
                "ts": str(ts or ""),
                "text": str(text or record.get("text") or ""),
                "channel_id": str(channel_id or channel.get("id") or ""),
                "channel_name": str(channel_name or channel.get("name") or ""),
                "user": str(record.get("user") or ""),
                "username": str(record.get("username") or ""),
                "permalink": str(record.get("permalink") or ""),
            }
        )
    return hits


def _search_via_api(
    session: Any, query: str, *, count: int, sort: str
) -> tuple[list[dict[str, Any]], int]:
    """`search.messages` through the passthrough.

    Used as the fallback when slackdump is unavailable or produced nothing, and as the
    primary when a caller explicitly asks for the *indexed* search Slack's own UI uses.
    Returns the hits plus Slack's reported total, which is often larger than `count`.
    """
    from .api import call

    params: dict[str, Any] = {"query": query, "count": count}
    if sort:
        params["sort"] = sort
    result = call(session, "search.messages", params)
    if not result.ok:
        raise ReadError(f"search.messages failed: {result.error}")
    messages = result.data.get("messages") or {}
    matches = messages.get("matches") or []
    hits: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        raw_channel = match.get("channel")
        channel: dict[str, Any] = raw_channel if isinstance(raw_channel, dict) else {}
        hits.append(
            {
                "ts": str(match.get("ts") or ""),
                "text": str(match.get("text") or ""),
                "channel_id": str(channel.get("id") or ""),
                "channel_name": str(channel.get("name") or ""),
                "user": str(match.get("user") or ""),
                "username": str(match.get("username") or ""),
                "permalink": str(match.get("permalink") or ""),
            }
        )
    return hits, int(messages.get("total") or 0)


def search(
    session: Any,
    query: str,
    *,
    count: int = 20,
    sort: str = "",
    via: str = "slackdump",
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[SearchHit], int]:
    """Search messages, through slackdump or directly.

    `via="slackdump"` is the default because it paginates and applies Slack's rate limits
    for you. It is worth knowing that slackdump's search is a *chunk* export: it lands in
    sqlite and cannot be converted to dump/export, so the rows are read from there.

    `via="api"` returns one page of `search.messages`, which is the same index Slack's
    own search box uses — handy when you want the result an agent would see in the UI,
    complete with permalinks.
    """
    if via == "api":
        raw_hits, total = _search_via_api(session, query, count=count, sort=sort)
        return [SearchHit(**hit) for hit in raw_hits], total

    with tempfile.TemporaryDirectory(prefix="mycord-slack-search-") as scratch:
        out = Path(scratch)
        args = ["search", "messages", "-no-channel-users", "-o", str(out), query]
        proc = _run(args, timeout=timeout)
        _require_success(proc, "search messages")
        raw_hits = _search_via_sqlite(out)
    hits = [SearchHit(**hit) for hit in raw_hits]
    return hits, len(hits)
