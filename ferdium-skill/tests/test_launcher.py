"""Filesystem and config-root behaviour — no app, no browser, no display."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycord_ferdium import launcher


def _fake_config(root: Path, services: dict[str, list[str]]) -> Path:
    """Build a Ferdium-shaped config tree: one partition per service."""
    parts = root / "Partitions"
    for svc_id, hosts in services.items():
        idb = parts / f"service-{svc_id}" / "IndexedDB"
        idb.mkdir(parents=True)
        for host in hosts:
            (idb / f"https_{host}_0.indexeddb.leveldb").mkdir()
    return root


def test_services_empty_when_nothing_configured(tmp_path: Path) -> None:
    assert launcher.services(tmp_path) == []


def test_services_discovers_partitions(tmp_path: Path) -> None:
    root = _fake_config(
        tmp_path,
        {
            "aaaa-1111": ["web.whatsapp.com"],
            "bbbb-2222": ["app.slack.com"],
        },
    )
    found = launcher.services(root)
    assert len(found) == 2
    assert {s.partition for s in found} == {"service-aaaa-1111", "service-bbbb-2222"}


def test_service_id_strips_the_prefix(tmp_path: Path) -> None:
    root = _fake_config(tmp_path, {"abcd-1234": ["web.whatsapp.com"]})
    assert launcher.services(root)[0].id == "abcd-1234"


def test_indexeddb_hosts_parsed_from_chromium_names(tmp_path: Path) -> None:
    """Chromium encodes the origin in the directory name; that is the only signal."""
    root = _fake_config(tmp_path, {"abcd-1234": ["web.whatsapp.com", "web.telegram.org"]})
    hosts = launcher.services(root)[0].indexeddb_hosts()
    assert "web.whatsapp.com" in hosts
    assert "web.telegram.org" in hosts


def test_indexeddb_hosts_ignores_non_origin_dirs(tmp_path: Path) -> None:
    root = _fake_config(tmp_path, {"abcd-1234": ["web.whatsapp.com"]})
    svc = launcher.services(root)[0]
    (svc.path / "IndexedDB" / "random-dir").mkdir()
    assert svc.indexeddb_hosts() == ["web.whatsapp.com"]


def test_service_without_indexeddb_reports_no_hosts(tmp_path: Path) -> None:
    parts = tmp_path / "Partitions" / "service-new-0001"
    parts.mkdir(parents=True)
    svc = launcher.services(tmp_path)[0]
    assert svc.indexeddb_hosts() == []


def test_hosted_services_filters_by_host(tmp_path: Path) -> None:
    root = _fake_config(
        tmp_path,
        {"aaaa": ["web.whatsapp.com"], "bbbb": ["app.slack.com"]},
    )
    assert [s.partition for s in launcher.hosted_services("whatsapp", root)] == ["service-aaaa"]
    assert launcher.hosted_services("telegram", root) == []


def test_hosted_services_is_case_insensitive(tmp_path: Path) -> None:
    root = _fake_config(tmp_path, {"aaaa": ["web.whatsapp.com"]})
    assert len(launcher.hosted_services("WhatsApp", root)) == 1


def test_launch_refuses_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing install must be a named error, not a confusing popen failure."""
    monkeypatch.setattr(launcher, "installed", lambda: False)
    with pytest.raises(launcher.FerdiumError, match="not installed"):
        launcher.launch()


def test_launch_requires_xvfb_for_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "installed", lambda: True)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with pytest.raises(launcher.FerdiumError, match="Xvfb is required"):
        launcher.launch(headless=True)


def test_config_root_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The config root is env-overridable so tests never read the real profile."""
    import importlib

    monkeypatch.setenv("MYCORD_FERDIUM_CONFIG", str(tmp_path))
    reloaded = importlib.reload(launcher)
    try:
        assert tmp_path == reloaded.CONFIG_ROOT
    finally:
        monkeypatch.delenv("MYCORD_FERDIUM_CONFIG")
        importlib.reload(launcher)


def test_indexeddb_hosts_are_deduped(tmp_path: Path) -> None:
    """One service can hold two origins; the list should not repeat a host."""
    root = _fake_config(tmp_path, {"abcd-1234": ["web.whatsapp.com"]})
    idb = root / "Partitions" / "service-abcd-1234" / "IndexedDB"
    (idb / "https_web.whatsapp.com_1.indexeddb.leveldb").mkdir()
    assert launcher.services(root)[0].indexeddb_hosts() == ["web.whatsapp.com"]
