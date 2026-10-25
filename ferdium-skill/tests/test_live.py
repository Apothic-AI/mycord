"""Live tests: need a running Ferdium with a debug port. Skipped by default."""

from __future__ import annotations

import os

import pytest

from mycord_ferdium import launcher
from mycord_ferdium.cdp import CdpError
from mycord_ferdium.page import attach

pytestmark = pytest.mark.live


def _port() -> int:
    return int(os.environ.get("MYCORD_FERDIUM_PORT", str(launcher.DEFAULT_PORT)))


@pytest.fixture(scope="module")
def browser():  # type: ignore[no-untyped-def]
    if not launcher.is_running(_port()):
        pytest.skip(f"no CDP port on {_port()}; run `mycord-ferdium launch --detach`")
    b = launcher.Browser(_port())
    yield b
    b.close()


def test_webviews_are_discoverable(browser) -> None:  # type: ignore[no-untyped-def]
    """The whole driver rests on webviews appearing in the Target domain."""
    views = browser.webviews()
    assert views, "no webview targets; services may all be hibernating"
    for view in views:
        assert view.type == "webview"


def test_attach_and_evaluate(browser) -> None:  # type: ignore[no-untyped-def]
    """A flattened session must be able to run JS in a service page."""
    views = browser.webviews()
    if not views:
        pytest.skip("no webviews to attach to")
    session = browser.attach(views[0])
    value = session.eval("document.readyState")
    assert value in {"loading", "interactive", "complete"}


def test_attach_unknown_host_is_a_named_error(browser) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(CdpError, match="no webview matching"):
        attach(browser, "definitely-not-a-real-host.invalid")


def test_whatsapp_chat_list_reads(browser) -> None:  # type: ignore[no-untyped-def]
    """End-to-end proof: the WhatsApp chat list is readable in headless mode."""
    target = browser.find_webview("web.whatsapp.com")
    if target is None:
        pytest.skip("WhatsApp service is not awake")
    page = attach(browser, "web.whatsapp.com")
    assert page.wait_for('[data-testid="cell-frame-container"]', timeout=45)
    assert page.count('[data-testid="cell-frame-container"]') > 0
