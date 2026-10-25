"""Cookie-jar shaping, using shapes captured from a real Chrome profile via CDP."""

from __future__ import annotations

import pytest

from gvoice_skill import cookies as C


def _cookie(name: str, domain: str, value: str = "v") -> dict:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "secure": True,
        "httpOnly": name in {"SID", "SSID", "HSID", "SAPISID"},
    }


# The real jar carries cookies for a dozen Google properties; Voice needs a subset and
# breaks entirely if that subset is incomplete.
CAPTURED = [
    _cookie("SID", ".google.com"),
    _cookie("SSID", ".google.com"),
    _cookie("HSID", ".google.com"),
    _cookie("APISID", ".google.com"),
    _cookie("SAPISID", ".google.com", "sapisid-value"),
    _cookie("__Secure-1PSID", ".google.com"),
    _cookie("__Secure-3PSID", ".google.com"),
    _cookie("__Secure-1PSIDTS", ".google.com"),
    _cookie("__Secure-3PSIDTS", ".google.com"),
    _cookie("NID", ".google.com"),
    _cookie("AEC", ".google.com"),
    _cookie("COMPASS", "voice.google.com", "voice-web-frontend=abc"),
    _cookie("COMPASS", "clients6.google.com", "voice-api=abc"),
    _cookie("COMPASS", ".voice.google.com", "voice-web-frontend=abc"),
    _cookie("SEARCH_SAMESITE", "accounts.google.com"),
    _cookie("unrelated", "example.com"),
    _cookie("empty-value", ".google.com", value=""),
]


def test_keeps_google_domains_and_drops_others() -> None:
    kept = C.select_cookies(CAPTURED)
    domains = {c["domain"] for c in kept}
    assert "example.com" not in domains
    assert ".google.com" in domains
    assert "accounts.google.com" in domains


def test_drops_cookies_without_a_value() -> None:
    assert all(c["value"] for c in C.select_cookies(CAPTURED))


def test_keeps_duplicate_names_across_domains() -> None:
    """COMPASS exists on three hosts with different values; name-keying picks wrong."""
    kept = C.select_cookies(CAPTURED)
    compass = [c["value"] for c in kept if c["name"] == "COMPASS"]
    assert len(compass) == 3
    assert "voice-api=abc" in compass


def test_domain_matching_handles_leading_dot() -> None:
    assert C.keep_domain(".google.com")
    assert C.keep_domain("voice.google.com")
    assert C.keep_domain(".voice.google.com")
    assert C.keep_domain("clients6.google.com")
    assert not C.keep_domain("notgoogle.com")
    assert not C.keep_domain("example.com")


def test_sapisid_preferred_over_secure_variant() -> None:
    assert C.find_sapisid(C.select_cookies(CAPTURED)) == "sapisid-value"


def test_falls_back_to_secure_3papisid() -> None:
    jar = [_cookie("__Secure-3PAPISID", ".google.com", "fallback")]
    assert C.find_sapisid(jar) == "fallback"


def test_missing_sapisid_is_an_explicit_error() -> None:
    jar = [_cookie("NID", ".google.com")]
    with pytest.raises(C.CookieError, match="not signed in"):
        C.find_sapisid(jar)


def test_build_session_shape_matches_the_library_contract() -> None:
    payload = C.build_session(CAPTURED)
    assert set(payload) == {"cookies", "sapisid"}
    assert payload["sapisid"] == "sapisid-value"
    for cookie in payload["cookies"]:
        assert set(cookie) == {"name", "value", "domain", "path", "secure", "httpOnly"}


def test_build_session_rejects_an_empty_jar() -> None:
    with pytest.raises(C.CookieError, match="none of the captured cookies"):
        C.build_session([_cookie("x", "example.com")])


def test_summary_never_includes_values() -> None:
    summary = C.summarize(C.build_session(CAPTURED)["cookies"])
    assert "sapisid-value" not in repr(summary)
    assert summary["count"] == len(C.build_session(CAPTURED)["cookies"])
    assert summary["has_sapisid"] is True
    assert summary["duplicate_names"] == 2  # three COMPASS entries, one unique name
