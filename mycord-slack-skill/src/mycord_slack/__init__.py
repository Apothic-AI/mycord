"""mycord-slack — Slack for agents, with no app registration.

Two halves, because no single tool does both:

* **Reads** go through `slackdump`, which speaks the client-token protocol properly and
  keeps an archive/cache of channels and users. It has no send capability at all.
* **Writes and everything else** go through `slack_sdk.WebClient` with the same
  `xoxc-` token plus the httpOnly `d` cookie. That pair operates exactly like the owning
  human's browser session, so the CLI is a *passthrough* to any Slack API method rather
  than a curated verb list.

`login` is the bridge: it pulls both values out of a signed-in Chrome profile over CDP
and imports them into slackdump.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
