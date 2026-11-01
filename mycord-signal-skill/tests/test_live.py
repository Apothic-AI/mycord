"""Live tests: they need the running daemon and the linked account on this host.

Skipped unless a daemon socket exists. They never send a message, never place a call, and
never link a device — they only read. The `-m "not live"` gate in the Makefile keeps the
offline suite hermetic; these exist so the verified state is checkable later.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mycord_signal import calls, deps, reads, rpc

DEFAULT_SOCKET = Path("/run/user/1000/signal-cli/socket")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not DEFAULT_SOCKET.exists(), reason=f"no live daemon socket at {DEFAULT_SOCKET}"
    ),
]


@pytest.fixture()
def live_client() -> rpc.RpcClient:
    client = rpc.RpcClient(DEFAULT_SOCKET, timeout=30)
    client.connect()
    try:
        yield client
    finally:
        client.close()


def test_live_version_is_at_least_required() -> None:
    client = rpc.RpcClient(DEFAULT_SOCKET, timeout=30)
    client.connect()
    try:
        version = client.version()
    finally:
        client.close()
    parsed = deps.parse_version(version)
    assert parsed is not None
    assert deps.version_at_least(parsed, deps.REQUIRED_SIGNAL_CLI)


def test_live_daemon_check_ok() -> None:
    check = deps.check_daemon(DEFAULT_SOCKET)
    assert check.status == "ok"


def test_live_signal_cli_check_ok() -> None:
    check = deps.check_signal_cli("signal-cli")
    assert check.status == "ok", check.detail


def test_live_tunnel_at_required_commit() -> None:
    info = deps.tunnel_info(deps.DEFAULT_TUNNEL_SRC, "signal-call-tunnel")
    check = deps.check_tunnel_commit(info)
    assert check.status == "ok", check.detail


def test_live_pipe_probe() -> None:
    result = deps.probe_pipe("signal-call-tunnel")
    assert result.ok is True, result.detail
    assert result.socket_path is not None
    assert result.socket_path.endswith(".sock")


def test_live_account_present() -> None:
    check = deps.check_account(deps.DEFAULT_SIGNAL_DATA)
    assert check.status == "ok", check.detail


def test_live_contacts_read(live_client: rpc.RpcClient) -> None:
    """Authenticated read. Verified on this host: two contacts, both with null numbers."""
    result = reads.contacts(live_client)
    assert isinstance(result.items, list)
    for entry in result.items:
        assert "uuid" in entry


def test_live_calls_is_a_list(live_client: rpc.RpcClient) -> None:
    assert isinstance(reads.calls(live_client).items, list)


def test_live_list_accounts_is_not_implemented_over_rpc(live_client: rpc.RpcClient) -> None:
    """Pins the finding that forced `doctor` to read accounts.json instead.

    A single-account daemon does not dispatch `listAccounts` (it is a multi-account-only
    command), so this is -32601 even though the CLI has the verb. If upstream ever adds it,
    this test fails and the workaround can be reconsidered.
    """
    with pytest.raises(rpc.RpcError) as excinfo:
        live_client.call("listAccounts")
    assert excinfo.value.code == rpc.ERROR_METHOD_NOT_FOUND


def test_live_call_subscription_works(live_client: rpc.RpcClient) -> None:
    """Subscribe, then unsubscribe by simply closing: no call is placed."""
    subscription = live_client.subscribe_call_events()
    assert isinstance(subscription, int)


def test_live_contract_constants_match_the_binary() -> None:
    """The built binary's PCM constants, as documented, in one place."""
    assert (calls.SAMPLE_RATE, calls.CHANNELS, calls.WINDOW_MS, calls.WINDOW_BYTES) == (
        48_000,
        1,
        10,
        960,
    )
