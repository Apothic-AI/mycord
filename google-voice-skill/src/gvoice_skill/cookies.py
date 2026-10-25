"""Cookie-jar shaping for the Google Voice session file.

Pure functions only: no browser, no network, no filesystem. The CDP client and the
file writer both depend on these, and they are what the tests exercise.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Domains whose cookies can matter for the Voice API. The client sends a per-URL
# Cookie header, so keeping a superset is correct and losing one is fatal: a partial
# jar yields `401 CREDENTIALS_MISSING` even with a perfectly valid SAPISIDHASH.
KEEP_SUFFIXES = (".google.com", ".clients6.google.com", ".voice.google.com")

# Both spellings exist depending on account age; either can stand in for the other.
SAPISID_NAMES = ("SAPISID", "__Secure-3PAPISID")


class CookieError(RuntimeError):
    """The jar cannot be used to authenticate."""


def _domain_matches(domain: str, suffix: str) -> bool:
    """True if `domain` is `suffix` or a subdomain of it.

    Google sets some cookies on the bare host (`voice.google.com`) and others on the
    leading-dot form (`.google.com`), so both spellings of the same name must be kept.
    """
    d = domain.lstrip(".").lower()
    return d == suffix.lstrip(".") or d.endswith(suffix)


def keep_domain(domain: str) -> bool:
    return any(_domain_matches(domain, s) for s in KEEP_SUFFIXES)


def select_cookies(raw: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter a CDP cookie list down to the ones the Voice client can use.

    Duplicate names across domains are *kept*, not deduplicated by name: COMPASS exists
    on three domains with different values and the one that matters is the
    `voice-api=…` cookie. Name-keyed maps pick the wrong one.
    """
    kept: list[dict[str, Any]] = []
    for cookie in raw:
        name = cookie.get("name")
        value = cookie.get("value")
        domain = cookie.get("domain") or ""
        if not name or not value:
            continue
        if not keep_domain(domain):
            continue
        kept.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cookie.get("path") or "/",
                "secure": bool(cookie.get("secure", True)),
                "httpOnly": bool(cookie.get("httpOnly", False)),
            }
        )
    return kept


def find_sapisid(cookies: Iterable[dict[str, Any]]) -> str:
    """Return the SAPISID value used to build the Authorization header.

    Preferred order follows the library: `SAPISID` first, then `__Secure-3PAPISID`.
    """
    by_name: dict[str, str] = {str(c["name"]): str(c["value"]) for c in cookies}
    for name in SAPISID_NAMES:
        value = by_name.get(name)
        if value:
            return value
    raise CookieError(
        "no SAPISID or __Secure-3PAPISID cookie in the jar — the browser profile is "
        "not signed in to Google, or you captured before the sign-in completed"
    )


def build_session(raw: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build the `gv-session.json` payload the gvoice library reads."""
    cookies = select_cookies(raw)
    if not cookies:
        raise CookieError(
            "none of the captured cookies were Google cookies — point the capture at a "
            "profile that has visited voice.google.com while signed in"
        )
    return {"cookies": cookies, "sapisid": find_sapisid(cookies)}


def summarize(cookies: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Non-secret summary for logging. Never includes a cookie value."""
    cookies = list(cookies)
    hosts: dict[str, int] = {}
    for c in cookies:
        hosts[c["domain"]] = hosts.get(c["domain"], 0) + 1
    names = sorted({c["name"] for c in cookies})
    return {
        "count": len(cookies),
        "hosts": dict(sorted(hosts.items())),
        "names": names,
        "has_sapisid": any(n in names for n in SAPISID_NAMES),
        "duplicate_names": len(cookies) - len(names),
    }
