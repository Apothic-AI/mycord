"""Page primitives every Ferdium service shares.

The DOM shape differs per service — that is exactly why the medium-specific skills
(``mycord-whatsapp-skill`` and friends) exist — but the *mechanics* of driving a
webview are identical, and two of them are non-obvious enough to live here:

* **Typing** must go through ``document.execCommand('insertText')``. Assigning
  ``innerText``/``value`` updates the DOM but not the framework's state, so the
  composer shows text that the app will not send.
* **Pressing Enter** must go through CDP ``Input.dispatchKeyEvent``. A synthetic
  ``KeyboardEvent`` dispatched from page JS is ignored by these apps' key handlers.

Both were established empirically against WhatsApp Web; assuming either would have
produced a silent no-op send.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .cdp import CdpError, TargetSession

__all__ = ["Page", "js"]


def js(value: Any) -> str:
    """Serialise a Python value into a JS literal, safely for inline scripts."""
    return json.dumps(value)


class Page:
    """Convenience wrapper over one webview session."""

    def __init__(self, session: TargetSession) -> None:
        self.session = session

    # -- reading ---------------------------------------------------------------

    def count(self, selector: str) -> int:
        return int(self.session.eval(f"document.querySelectorAll({js(selector)}).length") or 0)

    def texts(self, selector: str, *, limit: int = 0, scoped_to: str | None = None) -> list[str]:
        """Text of every match, whitespace-collapsed.

        ``scoped_to`` narrows the search to a container — essential in apps that
        render secondary panels (status trays, sidebars) into the same document and
        would otherwise leak unrelated rows into a conversation read.
        """
        root = f"document.querySelector({js(scoped_to)})" if scoped_to else "document"
        script = (
            f"(() => {{ const root = {root}; if (!root) return [];"
            f" return [...root.querySelectorAll({js(selector)})]"
            ".map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())"
            f"{f'.slice(0, {limit})' if limit else ''}; }})()"
        )
        return list(self.session.eval(script) or [])

    def text(self, selector: str) -> str | None:
        value = self.session.eval(
            f"(() => {{ const e = document.querySelector({js(selector)});"
            " return e ? (e.innerText || '').replace(/\\s+/g, ' ').trim() : null; }})()"
        )
        return None if value is None else str(value)

    def exists(self, selector: str) -> bool:
        return bool(self.session.eval(f"!!document.querySelector({js(selector)})"))

    # -- interaction -----------------------------------------------------------

    def click(self, selector: str, *, index: int = 0) -> bool:
        """Click a match with a full pointer/mouse sequence.

        Element-handle ``click()`` alone is often ignored by these apps because
        their handlers listen for pointer events; dispatching the sequence is what
        actually opens a conversation.
        """
        opened = self.session.eval(
            f"""(() => {{
              const els = [...document.querySelectorAll({js(selector)})];
              const el = els[{index}];
              if (!el) return false;
              const r = el.getBoundingClientRect();
              const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
              for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
                el.dispatchEvent(new MouseEvent(type, {{bubbles: true, cancelable: true,
                  clientX: cx, clientY: cy, view: window}}));
              }}
              return true;
            }})()"""
        )
        return bool(opened)

    def click_text(self, selector: str, needle: str) -> bool:
        """Click the first match whose text contains ``needle``."""
        opened = self.session.eval(
            f"""(() => {{
              const needle = {js(needle)}.toLowerCase();
              const el = [...document.querySelectorAll({js(selector)})]
                .find(e => (e.innerText || '').toLowerCase().includes(needle));
              if (!el) return false;
              const r = el.getBoundingClientRect();
              const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
              for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
                el.dispatchEvent(new MouseEvent(type, {{bubbles: true, cancelable: true,
                  clientX: cx, clientY: cy, view: window}}));
              }}
              return true;
            }})()"""
        )
        return bool(opened)

    def type(self, selector: str, text: str) -> str | None:
        """Insert text into a contenteditable/input; returns what landed.

        Returns the composer's readback so callers can verify the text before
        sending — never send on faith.
        """
        value = self.session.eval(
            f"""(() => {{
              const el = document.querySelector({js(selector)});
              if (!el) return null;
              el.focus();
              if (el.isContentEditable) {{
                document.execCommand('insertText', false, {js(text)});
              }} else {{
                el.value = {js(text)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
              }}
              return (el.innerText || el.value || '').trim();
            }})()"""
        )
        return None if value is None else str(value)

    def press_enter(self) -> None:
        """Send Enter through the input pipeline (not a synthetic DOM event)."""
        for kind in ("keyDown", "char", "keyUp"):
            params: dict[str, Any] = {
                "type": kind,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            }
            if kind == "char":
                params["text"] = "\r"
            self.session.send("Input.dispatchKeyEvent", params)

    def send_message(
        self, composer_selector: str, text: str, *, settle: float = 2.5
    ) -> dict[str, Any]:
        """Type, verify, send, and report the composer state afterwards.

        Refuses to press Enter if the composer readback does not match exactly —
        a guard against sending half-typed or concatenated text.
        """
        typed = self.type(composer_selector, text)
        if typed != text:
            return {"sent": False, "reason": "composer readback mismatch", "composer": typed}
        time.sleep(0.4)
        self.press_enter()
        time.sleep(settle)
        remaining = self.text(composer_selector)
        return {"sent": not remaining, "composer": remaining}

    # -- waiting ---------------------------------------------------------------

    def wait_for(self, selector: str, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exists(selector):
                return True
            time.sleep(0.35)
        return False

    def wait_for_gone(self, selector: str, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.exists(selector):
                return True
            time.sleep(0.35)
        return False

    def scroll(self, selector: str, *, delta: int = -1200) -> None:
        self.session.eval(
            f"(() => {{ const e = document.querySelector({js(selector)});"
            f" if (e) e.scrollBy(0, {int(delta)}); }})()"
        )


def attach(browser: Any, host_contains: str) -> Page:
    """Attach to a service webview by host, or raise a useful error."""
    target = browser.find_webview(host_contains)
    if target is None:
        visible = ", ".join(sorted({t.host or t.type for t in browser.webviews()})) or "none"
        raise CdpError(
            f"no webview matching {host_contains!r}. Live webviews: {visible}. "
            "A hibernating service has no webview until Ferdium wakes it."
        )
    return Page(browser.attach(target))
