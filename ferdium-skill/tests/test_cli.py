"""CLI behaviour: the exit codes are a contract other skills depend on."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycord_ferdium import cli, launcher


def _config(tmp_path: Path, services: dict[str, list[str]]) -> Path:
    parts = tmp_path / "Partitions"
    for svc_id, hosts in services.items():
        idb = parts / f"service-{svc_id}" / "IndexedDB"
        idb.mkdir(parents=True, exist_ok=True)
        for host in hosts:
            (idb / f"https_{host}_0.indexeddb.leveldb").mkdir(exist_ok=True)
    return tmp_path


def test_doctor_reports_not_installed(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(launcher, "installed", lambda: False)
    monkeypatch.setattr(launcher, "services", lambda root=None: [])
    args = cli.build_parser().parse_args(["doctor"])
    assert args.func(args) == cli.EXIT_NOT_INSTALLED
    out = capsys.readouterr().out
    assert "install" in out.lower()


def test_doctor_reports_not_configured(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Installed but empty is its own state: a human must add an account."""
    monkeypatch.setattr(launcher, "installed", lambda: True)
    monkeypatch.setattr(launcher, "services", lambda root=None: [])
    args = cli.build_parser().parse_args(["doctor"])
    assert args.func(args) == cli.EXIT_NOT_CONFIGURED
    out = capsys.readouterr().out
    assert "--headed" in out


def test_doctor_ready(monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path) -> None:
    real_services = launcher.services  # bind before patching, or this recurses
    found = real_services(_config(tmp_path, {"aa": ["web.whatsapp.com"]}))
    monkeypatch.setattr(launcher, "installed", lambda: True)
    monkeypatch.setattr(launcher, "services", lambda root=None: found)
    args = cli.build_parser().parse_args(["doctor", "--json"])
    assert args.func(args) == cli.EXIT_OK
    assert "web.whatsapp.com" in capsys.readouterr().out


def test_launch_refuses_headless_when_unconfigured(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """First run needs a visible window so the human can add their account."""
    monkeypatch.setattr(launcher, "installed", lambda: True)
    monkeypatch.setattr(launcher, "services", lambda root=None: [])
    args = cli.build_parser().parse_args(["launch", "--headless"])
    assert args.func(args) == cli.EXIT_NOT_CONFIGURED
    assert "--headed" in capsys.readouterr().err


def test_launch_allows_headed_when_unconfigured(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The debug port must still be enabled so no restart is needed after adding."""
    monkeypatch.setattr(launcher, "installed", lambda: True)
    monkeypatch.setattr(launcher, "services", lambda root=None: [])
    calls: dict = {}

    class FakeRunning:
        port = 9411

        class proc:
            pid = 1234

            @staticmethod
            def wait() -> None:
                return None

        def stop(self, grace: float = 10.0) -> None:
            calls["stopped"] = True

    def fake_launch(**kwargs):
        calls.update(kwargs)
        return FakeRunning()

    monkeypatch.setattr(launcher, "launch", fake_launch)
    args = cli.build_parser().parse_args(["launch", "--headed"])
    assert args.func(args) == cli.EXIT_OK
    assert calls["headless"] is False
    assert calls["stopped"] is True  # always cleaned up
    assert "add your account" in capsys.readouterr().err


def test_launch_headless_when_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_services = launcher.services
    found = real_services(_config(tmp_path, {"aa": ["x.com"]}))
    monkeypatch.setattr(launcher, "installed", lambda: True)
    monkeypatch.setattr(launcher, "services", lambda root=None: found)
    seen: dict = {}

    class FakeRunning:
        port = 9411

        class proc:
            pid = 5

            @staticmethod
            def wait() -> None:
                return None

        def stop(self, grace: float = 10.0) -> None:
            return None

    monkeypatch.setattr(launcher, "launch", lambda **kw: (seen.update(kw), FakeRunning())[1])
    args = cli.build_parser().parse_args(["launch"])
    assert args.func(args) == cli.EXIT_OK
    assert seen["headless"] is True


def test_services_reports_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "services", lambda root=None: [])
    args = cli.build_parser().parse_args(["services"])
    assert args.func(args) == cli.EXIT_NOT_CONFIGURED


def test_targets_explains_missing_port(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def boom(port: int, timeout: float = 60.0):
        raise cli.CdpError("connection refused")

    # cmd_targets uses the name imported into cli, not launcher's re-export
    monkeypatch.setattr(cli, "Browser", boom)
    args = cli.build_parser().parse_args(["targets", "--port", "9999"])
    assert args.func(args) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "mycord-ferdium launch" in err


def test_every_subcommand_has_a_handler() -> None:
    parser = cli.build_parser()
    for argv in (["doctor"], ["launch"], ["services"], ["targets"]):
        assert callable(parser.parse_args(argv).func)
