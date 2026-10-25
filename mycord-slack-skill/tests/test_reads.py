"""Read verbs: channel resolution, slackdump invocation, and the search store.

slackdump is not run here. What is tested is the wrapper's contract — reference
resolution, argument shape, output parsing, and the failure messages a caller will hit.
The `live` tests in `test_live.py` exercise the real binary.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mycord_slack import reads

pytestmark = pytest.mark.unit

CHANNELS: list[dict[str, Any]] = [
    {
        "id": "C0PUBLIC",
        "name": "general",
        "is_im": False,
        "is_mpim": False,
        "is_private": False,
        "num_members": 12,
        "is_archived": False,
        "topic": {"value": "company wide"},
    },
    {
        "id": "C0PRIVATE",
        "name": "leadership",
        "is_im": False,
        "is_mpim": False,
        "is_private": True,
        "num_members": 3,
        "is_archived": False,
        "topic": {"value": ""},
    },
    {
        "id": "D0DIRECT",
        "name": "",
        "user": "U0ALICE",
        "is_im": True,
        "is_mpim": False,
        "is_archived": False,
    },
]


def _listing() -> reads.Channels:
    return reads.Channels(raw=CHANNELS, fetched_at=0.0)


class Completed:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture()
def slackdump(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture slackdump invocations and answer with a canned channel listing."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> Completed:
        calls.append(args)
        if args[:2] == ["list", "channels"]:
            return Completed(json.dumps(CHANNELS))
        if args[:2] == ["list", "users"]:
            return Completed(
                json.dumps(
                    [
                        {"id": "U0ALICE", "name": "alice", "real_name": "Alice", "is_bot": False},
                        {"id": "U0BOT", "name": "bot", "real_name": "Bot", "is_bot": True},
                    ]
                )
            )
        return Completed("")

    monkeypatch.setattr(reads, "_run", fake_run)
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    return calls


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("C0PUBLIC", "C0PUBLIC"),
        ("#general", "C0PUBLIC"),
        ("general", "C0PUBLIC"),
        ("GENERAL", "C0PUBLIC"),
        ("D0DIRECT", "D0DIRECT"),
        ("https://test.slack.com/archives/C0PRIVATE", "C0PRIVATE"),
        ("https://test.slack.com/archives/C0PUBLIC/p1792958460122659", "C0PUBLIC"),
        ("https://test.slack.com/messages/D0DIRECT", "D0DIRECT"),
    ],
)
def test_resolve_channel_accepts_every_form_a_caller_will_use(
    reference: str, expected: str
) -> None:
    assert reads.resolve_channel(reference, _listing()) == expected


def test_resolve_channel_lists_near_misses_rather_than_only_raising() -> None:
    """The usual failure is a DM with no name; the hint names the DMs."""
    with pytest.raises(reads.ReadError) as caught:
        reads.resolve_channel("#gener", _listing())
    message = str(caught.value)
    assert "close matches: general" in message


def test_resolve_channel_suggests_dm_ids_when_nothing_matches() -> None:
    with pytest.raises(reads.ReadError) as caught:
        reads.resolve_channel("#nowhere", _listing())
    assert "D0DIRECT" in str(caught.value)


def test_resolve_channel_refuses_an_empty_reference() -> None:
    with pytest.raises(reads.ReadError, match="empty channel reference"):
        reads.resolve_channel("   ", _listing())


def test_channels_requests_json_on_stdout(slackdump: list[list[str]]) -> None:
    listing = reads.channels()
    assert len(listing.raw) == 3
    assert slackdump[-1][:2] == ["list", "channels"]
    assert "-no-json" in slackdump[-1], "the file copy must be disabled to get stdout JSON"
    assert "-format" in slackdump[-1]


def test_channels_fresh_drops_the_cache_flag(slackdump: list[list[str]]) -> None:
    reads.channels(fresh=True)
    assert "-no-chan-cache" in slackdump[-1]


def test_channels_empty_output_mentions_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The realistic cause of an empty listing is a dead session, so say so."""
    monkeypatch.setattr(reads, "_run", lambda *a, **k: Completed(""))
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    with pytest.raises(reads.ReadError, match="session --check"):
        reads.channels()


def test_a_slackdump_failure_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """slackdump echoes the file it read on failure; that file holds the token."""
    secret = "xoxc-111111111111-222222222222-333333333333-" + "a" * 64
    monkeypatch.setattr(
        reads,
        "_run",
        lambda *a, **k: Completed(stderr=f"failed to parse {secret}", returncode=1),
    )
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    with pytest.raises(reads.ReadError) as caught:
        reads.channels()
    assert secret not in str(caught.value)


def test_users_flattens_the_fields_a_caller_needs(slackdump: list[list[str]]) -> None:
    users = reads.users()
    assert [u["name"] for u in users] == ["alice", "bot"]


def test_the_Channel_describe_label_distinguishes_a_dm() -> None:
    listing = _listing()
    by_id = listing.by_id()
    assert listing.describe(by_id["C0PUBLIC"]) == "#general"
    assert listing.describe(by_id["D0DIRECT"]) == "DM U0ALICE"


MESSAGES = {
    "channel_id": "C0PUBLIC",
    "name": "general",
    "messages": [
        {"type": "message", "user": "U0ALICE", "text": "second", "ts": "1792951000.000200"},
        {"type": "message", "user": "U0BOB", "text": "first", "ts": "1792950000.000100"},
        {
            "type": "message",
            "user": "U0ALICE",
            "text": "thread reply",
            "ts": "1792952000.000400",
            "thread_ts": "1792950000.000100",
        },
    ],
}


def _fake_dump(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> Completed:
        calls.append(args)
        out = Path(args[args.index("-o") + 1])
        (out / f"{payload['channel_id']}.json").write_text(json.dumps(payload))
        return Completed("")

    monkeypatch.setattr(reads, "_run", fake_run)
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    return calls


def test_dump_returns_messages_chronologically(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_dump(monkeypatch, MESSAGES)
    messages = reads.dump("C0PUBLIC")
    assert [m.text for m in messages] == ["first", "second", "thread reply"]


def test_dump_limit_keeps_the_most_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--limit 2` means "the newest two", which is what a caller asking what's new wants."""
    _fake_dump(monkeypatch, MESSAGES)
    assert [m.text for m in reads.dump("C0PUBLIC", limit=2)] == ["second", "thread reply"]


def test_dump_carries_thread_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_dump(monkeypatch, MESSAGES)
    messages = reads.dump("C0PUBLIC")
    reply = next(m for m in messages if m.text == "thread reply")
    assert reply.thread_ts == "1792950000.000100"


def test_dump_passes_the_thread_notation_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_dump(monkeypatch, MESSAGES)
    reads.dump("C0PUBLIC:1792950000.000100")
    assert "C0PUBLIC:1792950000.000100" in calls[-1]


def test_dump_disables_file_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetching attachments by default would turn a read into a large download."""
    calls = _fake_dump(monkeypatch, MESSAGES)
    reads.dump("C0PUBLIC")
    assert "-files=false" in calls[-1]


def test_dump_with_no_output_file_explains_the_likely_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reads, "_run", lambda *a, **k: Completed(""))
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    with pytest.raises(reads.ReadError, match="not a member"):
        reads.dump("C0PUBLIC")


def _search_db(directory: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Build the SEARCH_MESSAGE table the way slackdump's search leaves it."""
    connection = sqlite3.connect(directory / "slackdump.sqlite")
    connection.execute(
        "CREATE TABLE SEARCH_MESSAGE (ID INTEGER PRIMARY KEY, CHANNEL_ID TEXT, "
        "CHANNEL_NAME TEXT, TS TEXT, TXT TEXT, DATA TEXT)"
    )
    connection.executemany(
        "INSERT INTO SEARCH_MESSAGE (CHANNEL_ID, CHANNEL_NAME, TS, TXT, DATA) VALUES (?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()


def test_search_reads_slackdumps_own_search_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """`slackdump search` writes sqlite and cannot convert to dump/export; the table IS
    the output format, so reading it is the supported route."""
    payload = {
        "text": "deploy failed",
        "user": "U0ALICE",
        "username": "alice",
        "permalink": "https://test.slack.com/archives/C0PUBLIC/p1792951000000200",
        "channel": {"id": "C0PUBLIC", "name": "general"},
    }

    def fake_run(args: list[str], **_: object) -> Completed:
        out = Path(args[args.index("-o") + 1])
        _search_db(
            out,
            [("C0PUBLIC", "general", "1792951000.000200", "deploy failed", json.dumps(payload))],
        )
        return Completed("")

    monkeypatch.setattr(reads, "_run", fake_run)
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    hits, total = reads.search(object(), "deploy")
    assert total == 1
    assert hits[0].text == "deploy failed"
    assert hits[0].channel_name == "general"
    assert hits[0].permalink.endswith("p1792951000000200")


def test_search_asks_slackdump_for_messages_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> Completed:
        calls.append(args)
        _search_db(Path(args[args.index("-o") + 1]), [])
        return Completed("")

    monkeypatch.setattr(reads, "_run", fake_run)
    monkeypatch.setattr(reads, "slackdump_binary", lambda: Path("/usr/bin/slackdump"))
    reads.search(object(), "anything")
    assert calls[-1][:3] == ["search", "messages", "-no-channel-users"]


def test_search_via_api_uses_search_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--via api` is one page of Slack's own index, complete with permalinks."""
    from mycord_slack import api

    captured: dict[str, Any] = {}

    def fake_call(session: object, method: str, payload: dict[str, Any], **_: object) -> Any:
        captured.update(method=method, payload=payload)
        result = api.ApiResult(ok=True, method=method)
        result.data = {
            "messages": {
                "total": 2,
                "matches": [
                    {
                        "ts": "1792951000.000200",
                        "text": "deploy failed",
                        "user": "U0ALICE",
                        "username": "alice",
                        "permalink": "https://x/p1",
                        "channel": {"id": "C0PUBLIC", "name": "general"},
                    }
                ],
            }
        }
        return result

    monkeypatch.setattr(api, "call", fake_call)
    hits, total = reads.search(object(), "deploy", via="api", count=5, sort="timestamp")
    assert captured["method"] == "search.messages"
    assert captured["payload"] == {"query": "deploy", "count": 5, "sort": "timestamp"}
    assert total == 2
    assert len(hits) == 1
    assert hits[0].permalink == "https://x/p1"


def test_search_via_api_raises_on_a_slack_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from mycord_slack import api

    def fake_call(*_: object, **__: object) -> Any:
        return api.ApiResult(ok=False, method="search.messages", error="not_authed")

    monkeypatch.setattr(api, "call", fake_call)
    with pytest.raises(reads.ReadError, match="not_authed"):
        reads.search(object(), "deploy", via="api")
