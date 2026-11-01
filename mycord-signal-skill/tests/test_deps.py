"""Tests for dependency detection — the deliverable that matters most.

Pure/local: no real signal-cli, no real tunnel, no network. The property under test is that a
stale pin is *detected and refused*, because a mismatch that slips through is only discovered
during call setup.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import write_script

from mycord_signal import deps

# --------------------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("have", "want", "expected"),
    [
        ((0, 14, 9), (0, 14, 9), True),
        ((0, 14, 10), (0, 14, 9), True),
        ((0, 14, 8), (0, 14, 9), False),
        ((0, 14, 4, 1), (0, 14, 4), True),
        ((0, 15), (0, 14, 9), True),
        ((1, 0), (0, 14, 9), True),
        ((0, 14), (0, 14, 9), False),
    ],
)
def test_version_at_least(have: tuple[int, ...], want: tuple[int, ...], expected: bool) -> None:
    assert deps.version_at_least(have, want) is expected


def test_parse_version_from_real_output() -> None:
    # Exactly what `signal-cli --version` prints on this host.
    assert deps.parse_version("signal-cli 0.14.9") == (0, 14, 9)
    assert deps.parse_version("0.14.9") == (0, 14, 9)
    assert deps.parse_version("v0.14.4.1") == (0, 14, 4, 1)


def test_parse_version_returns_none_for_noise() -> None:
    assert deps.parse_version("not a version") is None
    assert deps.parse_version("") is None


# --------------------------------------------------------------------------- signal-cli


def test_signal_cli_current_passes(fake_signal_cli: Path) -> None:
    check = deps.check_signal_cli(str(fake_signal_cli))
    assert check.status == "ok"
    assert "0.14.9" in check.detail
    assert check.fix is None


def test_signal_cli_stale_is_refused_with_fix(tmp_path: Path) -> None:
    old = write_script(tmp_path / "signal-cli", "print('signal-cli 0.14.6')\n")
    check = deps.check_signal_cli(str(old))
    assert check.status == "stale"
    assert "0.14.6" in check.detail
    assert "0.14.9" in check.detail
    assert check.fix is not None and "provision.sh" in check.fix
    assert check.blocking is True


def test_signal_cli_missing_is_reported(tmp_path: Path) -> None:
    check = deps.check_signal_cli(str(tmp_path / "definitely-not-here"))
    assert check.status == "missing"
    assert check.blocking is True
    assert check.fix is not None


def test_signal_cli_hanging_command_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung binary must be reported, never waited on.

    ``signal-cli listDevices`` hung for 15 hours on this host waiting for a device sync. The
    defense is that every probe goes through `run()` with a timeout, so this test pins the
    behaviour rather than the implementation.
    """
    slow = write_script(tmp_path / "signal-cli", "import time\ntime.sleep(60)\n")
    monkeypatch.setattr(deps, "CLI_TIMEOUT", 0.5)
    version, raw = deps.signal_cli_version(str(slow))
    assert version is None
    assert "TimeoutExpired" in raw


# --------------------------------------------------------------------------- tunnel


def test_tunnel_info_without_checkout_is_unknown(tmp_path: Path, probe_dir: Path) -> None:
    info = deps.tunnel_info(tmp_path / "no-such-src", str(tmp_path / "no-tunnel"))
    check = deps.check_tunnel_commit(info)
    assert check.status == "unknown"
    # Unknown is not blocking: a binary installed another way can still be correct. What we
    # must not do is claim provenance we could not verify.
    assert check.blocking is False


def test_tunnel_binary_missing_is_blocking(tmp_path: Path, probe_dir: Path) -> None:
    info = deps.tunnel_info(tmp_path / "no-such-src", str(tmp_path / "no-tunnel"))
    check = deps.check_tunnel_binary(info)
    assert check.status == "missing"
    assert check.blocking is True


def _checkout_with_build(root: Path, payload: bytes) -> Path:
    """Lay out a checkout whose release build has known content.

    The directory needs a `.git` marker because ``tunnel_info`` only inspects a path it can
    treat as a checkout; the marker is enough, since these tests are about the hash comparison
    and not about git.
    """
    src = root / "src"
    (src / ".git").mkdir(parents=True)
    built = src / "signal-call-tunnel" / "target" / "release" / "signal-call-tunnel"
    built.parent.mkdir(parents=True)
    built.write_bytes(payload)
    # `tunnel_info` resolves the binary with `shutil.which`, which requires the executable bit
    # even for an absolute path — as it should, since a non-executable tunnel is not a tunnel.
    built.chmod(0o755)
    return src


def test_tunnel_binary_sha_mismatch_is_stale(tmp_path: Path, probe_dir: Path) -> None:
    src = _checkout_with_build(tmp_path, b"new build")
    installed = tmp_path / "installed-tunnel"
    installed.write_bytes(b"old build")
    installed.chmod(0o755)

    check = deps.check_tunnel_binary(deps.tunnel_info(src, str(installed)))
    assert check.status == "stale"
    assert "not from this checkout" in check.detail
    assert check.blocking is True


def test_tunnel_binary_sha_match_is_ok(tmp_path: Path, probe_dir: Path) -> None:
    src = _checkout_with_build(tmp_path, b"same build")
    built = src / "signal-call-tunnel" / "target" / "release" / "signal-call-tunnel"
    check = deps.check_tunnel_binary(deps.tunnel_info(src, str(built)))
    assert check.status == "ok"


def test_tunnel_commit_equal_is_ok(tmp_path: Path) -> None:
    info = deps.TunnelInfo(
        binary="/usr/local/bin/signal-call-tunnel",
        binary_sha256="a" * 64,
        checkout=str(tmp_path),
        commit=deps.REQUIRED_TUNNEL_COMMIT + "0" * 33,
        built_sha256="a" * 64,
        at_required_commit=True,
        contains_required=True,
        subject="control: Add sender_device_id to ReceivedIce",
    )
    check = deps.check_tunnel_commit(info)
    assert check.status == "ok"
    assert "5a963d1" in check.detail


def test_tunnel_commit_behind_is_stale(tmp_path: Path) -> None:
    """The exact state before this session's fix: 2f92426 lacks the receivedIce device id."""
    info = deps.TunnelInfo(
        binary="/usr/local/bin/signal-call-tunnel",
        binary_sha256="a" * 64,
        checkout=str(tmp_path),
        commit="2f924268e1d3083eef70dd82718f1e7e760b13d1",
        built_sha256="a" * 64,
        at_required_commit=False,
        contains_required=False,
        subject="Add pipe audio backend via ringrtc CustomAudioDevice hook",
    )
    check = deps.check_tunnel_commit(info)
    assert check.status == "stale"
    assert "2f92426" in check.detail
    assert "sender_device_id" in check.detail
    assert check.fix is not None


# --------------------------------------------------------------------------- pipe probe


def test_probe_pipe_accepts_pipe_ready(fake_tunnel: Path, probe_dir: Path) -> None:
    result = deps.probe_pipe(str(fake_tunnel))
    assert result.ok is True
    assert result.socket_path is not None
    assert result.socket_path.startswith("/tmp/x/signal-call-")


def test_probe_pipe_rejects_device_mode_tunnel(tmp_path: Path, probe_dir: Path) -> None:
    """A virtual-device-only build reports PulseAudio names, not `unix:` paths.

    This is exactly what a pre-PR-#5 binary produces, and it must be caught here rather than
    when a call is already ringing.
    """
    script = write_script(
        tmp_path / "device-tunnel",
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type': 'ready', 'inputDeviceName': 'signal_input_1',"
        " 'outputDeviceName': 'signal_output_1'}), flush=True)\n",
    )
    result = deps.probe_pipe(str(script))
    assert result.ok is False
    assert "virtual-device only" in result.detail


def test_probe_pipe_rejects_split_socket_names(tmp_path: Path, probe_dir: Path) -> None:
    script = write_script(
        tmp_path / "split-tunnel",
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "print(json.dumps({'type': 'ready', 'inputDeviceName': 'unix:/tmp/a.sock',"
        " 'outputDeviceName': 'unix:/tmp/b.sock'}), flush=True)\n",
    )
    result = deps.probe_pipe(str(script))
    assert result.ok is False
    assert "differ" in result.detail


def test_probe_pipe_reports_no_ready_line(tmp_path: Path, probe_dir: Path) -> None:
    script = write_script(
        tmp_path / "broken-tunnel",
        "import sys\n"
        "sys.stdin.readline()\n"
        "print('Error: failed to create pipe audio device', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
    )
    result = deps.probe_pipe(str(script))
    assert result.ok is False
    assert "no ready line" in result.detail
    assert "failed to create" in result.stderr


def test_probe_pipe_handles_missing_binary(tmp_path: Path, probe_dir: Path) -> None:
    result = deps.probe_pipe(str(tmp_path / "absent"))
    assert result.ok is False
    assert "could not execute" in result.detail


def test_probe_pipe_sends_integer_call_id(tmp_path: Path, probe_dir: Path) -> None:
    """The documented trap: a *string* call_id is rejected by the tunnel's config parser.

    Verified by reading ``config.rs`` (``call_id: u64``) and by the live tunnel rejecting
    ``--help`` with ``failed to parse config JSON``. This test pins the payload type so a
    future refactor cannot quietly reintroduce a string.
    """
    captured = tmp_path / "captured.json"
    script = write_script(
        tmp_path / "recording-tunnel",
        "import json, pathlib, sys\n"
        "config = json.loads(sys.stdin.readline())\n"
        f"pathlib.Path({str(captured)!r}).write_text(json.dumps(config))\n"
        "path = f'unix:/tmp/x/signal-call-{config[\"call_id\"]}.sock'\n"
        "print(json.dumps({'type': 'ready', 'inputDeviceName': path,"
        " 'outputDeviceName': path}), flush=True)\n",
    )
    assert deps.probe_pipe(str(script)).ok is True

    payload = json.loads(captured.read_text())
    assert isinstance(payload["call_id"], int), "call_id must be an integer (u64)"
    assert isinstance(payload["local_device_id"], int), "local_device_id must be an integer (u32)"
    assert isinstance(payload["is_outgoing"], bool)
    assert payload["audio_mode"] == "pipe"


# --------------------------------------------------------------------------- account


def test_read_accounts_from_store(tmp_path: Path) -> None:
    """The store is the only reliable source: `listAccounts` is -32601 over the RPC daemon."""
    data = tmp_path / "signal-cli"
    (data / "data").mkdir(parents=True)
    (data / "data" / "accounts.json").write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": [
                    {
                        "path": "238674",
                        "environment": "LIVE",
                        "number": "+15550100",
                        "uuid": "22f3dea9-34b2-4288-8921-efbedc3d3e42",
                    }
                ],
            }
        )
    )
    accounts, diagnostic = deps.read_accounts(data)
    assert diagnostic == ""
    assert len(accounts) == 1
    assert accounts[0].number == "+15550100"
    assert accounts[0].label() == "+15550100"

    check = deps.check_account(data)
    assert check.status == "ok"
    assert "+15550100" in check.detail


def test_read_accounts_missing_store(tmp_path: Path) -> None:
    accounts, diagnostic = deps.read_accounts(tmp_path / "nope")
    assert accounts == []
    assert "could not read" in diagnostic
    check = deps.check_account(tmp_path / "nope")
    assert check.status == "missing"
    assert check.fix is not None and "link" in check.fix


def test_read_accounts_corrupt_json(tmp_path: Path) -> None:
    data = tmp_path / "signal-cli"
    (data / "data").mkdir(parents=True)
    (data / "data" / "accounts.json").write_text("{not json")
    accounts, diagnostic = deps.read_accounts(data)
    assert accounts == []
    assert "not valid JSON" in diagnostic


def test_account_without_number_uses_uuid(tmp_path: Path) -> None:
    data = tmp_path / "signal-cli"
    (data / "data").mkdir(parents=True)
    (data / "data" / "accounts.json").write_text(
        json.dumps({"version": 2, "accounts": [{"path": "x", "uuid": "abc-123"}]})
    )
    accounts, _ = deps.read_accounts(data)
    assert accounts[0].label() == "abc-123"


# --------------------------------------------------------------------------- gates


def test_require_call_ready_refuses_on_stale_cli() -> None:
    report = deps.Report()
    report.add(
        deps.Check(
            name="signal-cli", status="stale", detail="found 0.14.6, need >=0.14.9", fix="run it"
        )
    )
    refusal = deps.require_call_ready(report)
    assert refusal is not None
    assert "refusing the call path" in refusal
    assert "0.14.6" in refusal
    assert "fix: run it" in refusal


def test_require_call_ready_refuses_on_missing_tunnel() -> None:
    report = deps.Report()
    report.add(deps.Check(name="signal-call-tunnel:binary", status="missing", detail="not found"))
    assert deps.require_call_ready(report) is not None


def test_require_call_ready_refuses_without_daemon() -> None:
    report = deps.Report()
    report.add(deps.Check(name="daemon", status="missing", detail="no socket"))
    assert deps.require_call_ready(report) is not None


def test_require_call_ready_allows_ready_deps() -> None:
    report = deps.Report()
    for name in (
        "signal-cli",
        "signal-call-tunnel:binary",
        "signal-call-tunnel:checkout",
        "signal-call-tunnel:pipe",
        "daemon",
    ):
        report.add(deps.Check(name=name, status="ok", detail="fine"))
    assert deps.require_call_ready(report) is None


def test_require_call_ready_ignores_account_gap() -> None:
    """No account is a human problem, not a media problem.

    Conflating "nobody linked a phone" with "the binaries are wrong" would make dep-only CI
    permanently red for the wrong reason.
    """
    report = deps.Report()
    report.add(deps.Check(name="signal-cli", status="ok", detail="0.14.9"))
    report.add(deps.Check(name="account", status="missing", detail="no account"))
    assert deps.require_call_ready(report) is None


def test_dependency_problems_excludes_account() -> None:
    report = deps.Report()
    report.add(deps.Check(name="account", status="missing", detail="none"))
    report.add(deps.Check(name="signal-cli", status="stale", detail="old"))
    names = [check.name for check in report.dependency_problems]
    assert names == ["signal-cli"]


def test_unknown_checks_do_not_block() -> None:
    report = deps.Report()
    report.add(deps.Check(name="signal-call-tunnel:checkout", status="unknown", detail="none"))
    assert deps.require_call_ready(report) is None


# --------------------------------------------------------------------------- misc


def test_file_sha256_matches_hashlib(tmp_path: Path) -> None:
    target = tmp_path / "blob"
    target.write_bytes(b"payload")
    assert deps.file_sha256(target) == hashlib.sha256(b"payload").hexdigest()


def test_file_sha256_missing_returns_none(tmp_path: Path) -> None:
    assert deps.file_sha256(tmp_path / "absent") is None


def test_report_json_roundtrips() -> None:
    report = deps.Report()
    report.add(deps.Check(name="a", status="ok", detail="fine"))
    report.add(deps.Check(name="b", status="stale", detail="old", fix="upgrade"))
    payload = report.as_dict()
    assert payload["checks"][1]["fix"] == "upgrade"
    assert payload["dependency_ready"] is False
    assert json.loads(json.dumps(payload))["checks"][0]["status"] == "ok"


def test_run_uses_a_timeout(tmp_path: Path) -> None:
    """`run` must never leave a hung subprocess behind.

    This is the guard against the 15-hour `listDevices` hang; the timeout has to raise rather
    than return, because a caller that got a "result" from a still-running probe would treat
    silence as success.
    """
    slow = write_script(tmp_path / "slow", "import time\ntime.sleep(30)\n")
    with pytest.raises(subprocess.TimeoutExpired):
        deps.run([str(slow)], timeout=0.3)


def test_probe_pipe_timeout_is_reported(tmp_path: Path, probe_dir: Path) -> None:
    slow = write_script(tmp_path / "slow-tunnel", "import time\ntime.sleep(30)\n")
    result = deps.probe_pipe(str(slow), timeout=0.5)
    assert result.ok is False
    assert "did not exit" in result.detail


def test_python_interpreter_is_used_for_fixtures() -> None:
    """The fixtures rely on a real interpreter; assert the assumption rather than assume it."""
    assert Path(sys.executable).exists()


# --------------------------------------------------------------------------- shim resolution


def test_resolve_binary_falls_back_to_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain binary on PATH resolves to itself, with no mise involvement."""
    plain = write_script(tmp_path / "plain-tool", "print('hi')\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert deps.resolve_binary("plain-tool") == str(plain)


def test_resolve_binary_missing_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert deps.resolve_binary("nothing-here") is None


def test_resolve_binary_dereferences_a_mise_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mise shim is a symlink to `mise`, so its hash is not the tool's hash.

    This was a real false positive on this host: ``signal-call-tunnel`` resolved through
    ``~/.local/share/mise/shims/`` and hashing the shim made every run report "installed binary
    differs from the checkout build". Resolution has to ask mise, not hash the shim.
    """
    # Layout mirrors the real host: `shims/` holds symlinks to the `mise` binary, and `mise`
    # itself is on PATH so it can answer `mise which <name>`.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shims = tmp_path / "shims"
    shims.mkdir()
    real_tool = write_script(tmp_path / "real-tunnel", "print('real')\n")
    fake_mise = write_script(bin_dir / "mise", f"print({str(real_tool)!r})\n")
    (shims / "signal-call-tunnel").symlink_to(fake_mise)

    monkeypatch.setenv("PATH", f"{shims}:{bin_dir}")
    assert deps.resolve_binary("signal-call-tunnel") == str(real_tool)
