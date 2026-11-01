"""Shared fixtures. Everything here is offline: no account, no network, no real binary."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_daemon import FakeSignalDaemon, default_handlers, fake_daemon


@pytest.fixture()
def socket_path(tmp_path: Path) -> Path:
    return tmp_path / "signal-cli.sock"


@pytest.fixture()
def daemon(socket_path: Path) -> Iterator[FakeSignalDaemon]:
    with fake_daemon(socket_path, default_handlers()) as server:
        yield server


def write_script(path: Path, body: str) -> Path:
    """Write an executable Python script and return its path."""
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)
    return path


@pytest.fixture()
def fake_signal_cli(tmp_path: Path) -> Path:
    """A stand-in `signal-cli` reporting a fixed version and an empty account list.

    A script is enough: the code under test only runs the binary and reads its output, and
    the parsing rules (``signal-cli 0.14.9`` on stdout, a JSON array for ``-o json``) are
    exactly what the real binary produces — checked against 0.14.9 on this host.
    """
    return write_script(
        tmp_path / "signal-cli",
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if '--version' in args:\n"
        "    print('signal-cli 0.14.9')\n"
        "    raise SystemExit(0)\n"
        "if '-o' in args and 'listAccounts' in args:\n"
        "    print('[]')\n"
        "    raise SystemExit(0)\n"
        "print('unexpected args: ' + repr(args), file=sys.stderr)\n"
        "raise SystemExit(64)\n",
    )


@pytest.fixture()
def fake_tunnel(tmp_path: Path) -> Path:
    """A stand-in tunnel that speaks the pipe-mode ``ready`` protocol and exits.

    It reproduces the line the real 5a963d1 binary prints (verified on this host), which is
    the whole contract this skill depends on: a first-line config on stdin, then one JSON
    object on stdout whose ``inputDeviceName`` and ``outputDeviceName`` are the *same*
    ``unix:`` path.
    """
    return write_script(
        tmp_path / "signal-call-tunnel",
        "import json, sys\n"
        "config = sys.stdin.readline()\n"
        "try:\n"
        "    parsed = json.loads(config)\n"
        "except json.JSONDecodeError:\n"
        "    print('failed to parse config JSON', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "socket_dir = '/tmp/x'\n"
        "path = f'unix:{socket_dir}/signal-call-{parsed[\"call_id\"]}.sock'\n"
        "print(json.dumps({'type': 'ready', 'inputDeviceName': path, 'outputDeviceName': path}),\n"
        "      flush=True)\n",
    )


@pytest.fixture()
def probe_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep probe sockets out of the real /tmp and clear inherited tunnel env."""
    for name in (
        "SIGNAL_CALL_TUNNEL_AUDIO_MODE",
        "SIGNAL_CALL_TUNNEL_SOCKET_DIR",
        "SIGNAL_CALL_TUNNEL_BIN",
    ):
        monkeypatch.delenv(name, raising=False)
    directory = tmp_path / "probe"
    directory.mkdir()
    monkeypatch.setenv("MYCORD_SIGNAL_PROBE_DIR", str(directory))
    return directory
