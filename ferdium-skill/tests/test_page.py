"""Page primitives, tested against a fake CDP session.

These tests exist because the two non-obvious mechanics (execCommand typing, CDP
Enter) are the difference between a working send and a silent no-op. The fake
records what was asked of it so the *shape* of the interaction is pinned even
though no browser is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from mycord_ferdium.page import Page, js


class FakeSession:
    """Records CDP calls and replays canned evaluate results."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.evaluations: list[str] = []
        self.sent: list[tuple[str, dict]] = []

    def eval(self, expression: str, **_kwargs: Any) -> Any:
        self.evaluations.append(expression)
        return self.responses.pop(0) if self.responses else None

    def send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, params or {}))
        return {}


def test_js_serialises_safely() -> None:
    """Values are interpolated into page scripts, so quoting must be JSON."""
    assert js("hi") == '"hi"'
    assert js('quote"inside') == '"quote\\"inside"'
    assert js(["a", "b"]) == '["a", "b"]'


def test_type_uses_execcommand_for_contenteditable() -> None:
    """Assigning innerText does not update the framework's state — execCommand does."""
    session = FakeSession(["Hi"])
    page = Page(session)  # type: ignore[arg-type]
    assert page.type("#box", "Hi") == "Hi"
    script = session.evaluations[0]
    assert "execCommand('insertText'" in script
    assert "isContentEditable" in script


def test_type_falls_back_to_value_for_inputs() -> None:
    session = FakeSession(["text"])
    page = Page(session)  # type: ignore[arg-type]
    page.type("input", "text")
    script = session.evaluations[0]
    assert "el.value =" in script
    assert "dispatchEvent(new Event('input'" in script


def test_press_enter_uses_cdp_input_not_dom_events() -> None:
    """A synthetic KeyboardEvent is ignored by these apps; CDP input is not."""
    session = FakeSession()
    page = Page(session)  # type: ignore[arg-type]
    page.press_enter()
    methods = [m for m, _ in session.sent]
    assert methods == ["Input.dispatchKeyEvent"] * 3
    kinds = [p["type"] for _, p in session.sent]
    assert kinds == ["keyDown", "char", "keyUp"]
    assert all(p["windowsVirtualKeyCode"] == 13 for _, p in session.sent)
    assert session.sent[1][1]["text"] == "\r"


def test_send_message_refuses_on_readback_mismatch() -> None:
    """Never send text we did not verify — this guards against half-typed sends."""
    session = FakeSession(["H"])  # only "H" landed
    page = Page(session)  # type: ignore[arg-type]
    result = page.send_message("#box", "Hi")
    assert result["sent"] is False
    assert "mismatch" in result["reason"]
    assert session.sent == []  # Enter was never pressed


def test_send_message_sends_when_readback_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_ferdium.page.time.sleep", lambda _s: None)
    session = FakeSession(["Hi", None])  # typed ok, composer empty afterwards
    page = Page(session)  # type: ignore[arg-type]
    result = page.send_message("#box", "Hi")
    assert result["sent"] is True
    assert [m for m, _ in session.sent] == ["Input.dispatchKeyEvent"] * 3


def test_texts_scopes_to_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unscoped reads leak status trays into conversation output."""
    session = FakeSession([["a", "b"]])
    page = Page(session)  # type: ignore[arg-type]
    page.texts("div[role=row]", scoped_to="#main")
    script = session.evaluations[0]
    assert 'document.querySelector("#main")' in script
    assert "root.querySelectorAll" in script


def test_texts_limits_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([["a"]])
    page = Page(session)  # type: ignore[arg-type]
    page.texts("div", limit=5)
    assert ".slice(0, 5)" in session.evaluations[0]


def test_texts_collapses_whitespace() -> None:
    session = FakeSession([["x"]])
    page = Page(session)  # type: ignore[arg-type]
    page.texts("div")
    assert "replace(/\\s+/g, ' ')" in session.evaluations[0]


def test_click_dispatches_full_pointer_sequence() -> None:
    """A bare .click() is ignored by webviews listening for pointer events."""
    session = FakeSession([True])
    page = Page(session)  # type: ignore[arg-type]
    assert page.click("div") is True
    script = session.evaluations[0]
    for event in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert event in script


def test_click_text_matches_case_insensitively() -> None:
    session = FakeSession([True])
    page = Page(session)  # type: ignore[arg-type]
    page.click_text("div", "violet")
    script = session.evaluations[0]
    assert "toLowerCase()" in script
    assert '"violet"' in script


def test_click_reports_false_when_absent() -> None:
    session = FakeSession([False])
    page = Page(session)  # type: ignore[arg-type]
    assert page.click("div") is False


def test_exists_and_count() -> None:
    session = FakeSession([True, 3])
    page = Page(session)  # type: ignore[arg-type]
    assert page.exists("#a") is True
    assert page.count(".b") == 3


def test_wait_for_times_out_without_looping_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mycord_ferdium.page.time.sleep", lambda _s: None)
    clock = {"t": 0.0}

    def fake_monotonic() -> float:
        clock["t"] += 1.0
        return clock["t"]

    monkeypatch.setattr("mycord_ferdium.page.time.monotonic", fake_monotonic)
    session = FakeSession()  # always None => never appears
    page = Page(session)  # type: ignore[arg-type]
    assert page.wait_for("#nope", timeout=3) is False


def test_scroll_targets_the_container() -> None:
    session = FakeSession([None])
    page = Page(session)  # type: ignore[arg-type]
    page.scroll("#main", delta=-800)
    assert "scrollBy(0, -800)" in session.evaluations[0]
