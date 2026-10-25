"""mycord-ferdium — drive a Ferdium install over the Chrome DevTools Protocol.

Ferdium wraps every configured service in its own Chromium ``<webview>``. That makes
it the one place where an agent can reach *already-authenticated* sessions for many
chat platforms without a bot registration, an OAuth app, or a second browser.

This package owns only the parts that are the same for every service: launching the
app (headless or headed), discovering webviews, and the page primitives that are
easy to get subtly wrong. Service-specific DOM work belongs in a dependent skill such
as ``mycord-whatsapp-skill``.
"""

from __future__ import annotations

from .cdp import Browser, CdpError, Target, TargetSession, discover_targets
from .launcher import (
    APP_ID,
    DEFAULT_PORT,
    FerdiumError,
    Running,
    Service,
    hosted_services,
    installed,
    launch,
    services,
    wait_for_service,
)
from .page import Page, attach

__all__ = [
    "APP_ID",
    "DEFAULT_PORT",
    "Browser",
    "CdpError",
    "FerdiumError",
    "Page",
    "Running",
    "Service",
    "Target",
    "TargetSession",
    "attach",
    "discover_targets",
    "hosted_services",
    "installed",
    "launch",
    "services",
    "wait_for_service",
]
