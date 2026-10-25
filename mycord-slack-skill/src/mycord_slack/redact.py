"""Strip secret-shaped strings out of text before it is printed or logged.

Subprocess stderr and Slack error payloads are the two places a credential can leak by
accident: slackdump echoes what it was given on a bad import, and an API error body can
carry the request that produced it. Both go through `redact()` on the way out.

The regexes are deliberately generic (token *shape*, not this machine's token) so they
also catch a token that was rotated mid-run.
"""

from __future__ import annotations

import re

# Any Slack token kind: xoxc/xoxp/xoxb/xoxe/xapp/xoxa, and the xoxd cookie value.
_TOKEN = re.compile(r"\b(xox[abceopr]-[A-Za-z0-9-]{6,}|xapp-[A-Za-z0-9-]{6,})")
_COOKIE = re.compile(r"\bxoxd-[A-Za-z0-9%._-]{6,}")
# `d=xoxd-...` or `Cookie: d=...` inside a header dump.
_COOKIE_HEADER = re.compile(r"(?i)\b(d\s*[=:]\s*)[^\s;,]+")

MASK = "<redacted>"


def redact(text: str) -> str:
    """Replace every token/cookie-shaped substring with `<redacted>`.

    Called on anything that came from a subprocess or an API error body. It is cheap and
    idempotent, so callers do not need to decide whether a given string is sensitive.
    """
    if not text:
        return text
    text = _TOKEN.sub(MASK, text)
    text = _COOKIE.sub(MASK, text)
    return _COOKIE_HEADER.sub(lambda m: f"{m.group(1)}{MASK}", text)


def token_fingerprint(token: str) -> str:
    """A non-reversible label for a token: its kind and length, never any of its value.

    `xoxc-…(114)` is enough to tell two sessions apart in output and enough for a human
    to confirm they captured a client token and not, say, a bot token.
    """
    kind = token.split("-", 1)[0] if "-" in token else "unknown"
    return f"{kind}-…({len(token)})"
