"""Tests that talk to the real account. Skipped unless a usable session exists.

Run with `make test-live`. These are the only tests that can catch Google changing the
API, which is the whole risk of an unofficial client — so they exist, and they are kept
deliberately shallow: read-only, no calls, no sends.
"""

from __future__ import annotations

import json

import pytest

from gvoice_skill import cli, proto
from gvoice_skill.bridge import GvoiceError, find_library

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def library():  # type: ignore[no-untyped-def]
    lib = find_library()
    if not lib.session.is_file():
        pytest.skip(f"no session at {lib.session}; run `gvoice-skill login`")
    if not lib.install_ok:
        pytest.skip(f"dependencies missing in {lib.root}; run `pnpm install`")
    return lib


def test_account_get_returns_an_e164_number(library) -> None:  # type: ignore[no-untyped-def]
    result = cli.api_call(library, cli.METHOD_ACCOUNT_GET, [None, 1])
    outer = result[0]
    number = outer[0] if isinstance(outer, list) else outer
    assert isinstance(number, str) and number.startswith("+")


def test_warmsync_yields_an_integer_token(library) -> None:  # type: ignore[no-untyped-def]
    token = proto.warmsync_token(cli.api_call(library, cli.METHOD_WARMSYNC, []))
    assert isinstance(token, int)


def test_thread_list_returns_threads_when_the_token_is_passed(library) -> None:  # type: ignore[no-untyped-def]
    token = proto.warmsync_token(cli.api_call(library, cli.METHOD_WARMSYNC, []))
    result = cli.api_call(library, cli.METHOD_THREAD_LIST, [token, 3])
    threads, next_token = proto.parse_thread_list(result)
    assert len(threads) >= 1
    assert all(t.id and t.id.startswith("t.") for t in threads)
    assert next_token


def test_thread_list_without_the_token_returns_no_threads(library) -> None:  # type: ignore[no-untyped-def]
    """Guards the ordering trap: [pageSize] must keep failing loudly if it changes."""
    result = cli.api_call(library, cli.METHOD_THREAD_LIST, [3])
    threads, _ = proto.parse_thread_list(result)
    assert threads == []


def test_thread_get_parses_messages_with_text_and_direction(library) -> None:  # type: ignore[no-untyped-def]
    token = proto.warmsync_token(cli.api_call(library, cli.METHOD_WARMSYNC, []))
    threads, _ = proto.parse_thread_list(cli.api_call(library, cli.METHOD_THREAD_LIST, [token, 1]))
    thread_id = threads[0].id
    thread = proto.parse_thread_get(cli.api_call(library, cli.METHOD_THREAD_GET, [thread_id, 5]))
    assert thread is not None
    assert thread.messages, f"{thread_id} had no decodable messages"
    for message in thread.messages:
        assert message.kind in {"sms", "mms"} or message.kind.startswith("kind-")
        assert message.text is not None


def test_search_filters(library) -> None:  # type: ignore[no-untyped-def]
    nonsense = proto.parse_search(
        cli.api_call(library, cli.METHOD_SEARCH, ["zzzznotpresentanywhere", 5])
    )
    assert nonsense == []


def test_sip_register_reaches_200(library) -> None:  # type: ignore[no-untyped-def]
    """The control plane and the SIP plane fail independently — prove both."""
    import subprocess

    proc = subprocess.run(
        [library.node(), str(library.root / "src" / "probe-sip.ts")],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=library.root,
    )
    assert "200 OK" in proc.stdout, proc.stdout[-800:] + proc.stderr[-400:]


def test_cli_thread_command_end_to_end(library, capsys) -> None:  # type: ignore[no-untyped-def]
    token = proto.warmsync_token(cli.api_call(library, cli.METHOD_WARMSYNC, []))
    threads, _ = proto.parse_thread_list(cli.api_call(library, cli.METHOD_THREAD_LIST, [token, 1]))
    args = cli.build_parser().parse_args(["thread", threads[0].id, "--limit", "3"])
    assert args.func(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert threads[0].id in out
    assert "sms" in out or "mms" in out


def test_bad_cookie_jar_fails_as_an_auth_error(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A deliberately broken jar must not look like a network or parse failure."""
    bogus = tmp_path / "gv-session.json"
    bogus.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": "not-a-real-session",
                        "domain": ".google.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                    }
                ],
                "sapisid": "not-a-real-session",
            }
        )
    )
    bogus.chmod(0o600)
    monkeypatch.setenv("GVOICE_SESSION", str(bogus))
    library = find_library()
    with pytest.raises(GvoiceError):
        cli.api_call(library, cli.METHOD_ACCOUNT_GET, [None, 1])
