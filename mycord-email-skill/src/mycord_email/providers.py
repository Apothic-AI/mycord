"""Provider table: how to reach a mailbox family, without any secret.

The table exists because the *connection shape* is what agents get wrong. Proton
Bridge needs STARTTLS on localhost plus a trusted self-signed certificate;
Fastmail and Migadu use implicit TLS on the standard ports; Gmail and Microsoft
365 reject passwords and need an app password or (for M365) OAuth through
Graph. Encoding those shapes once removes a whole class of "it authenticated
yesterday" failures.

A provider entry is deliberately data, not code: ``mycord-email providers``
prints the table, and :mod:`mycord_email.config` renders it into the TOML that
Himalaya reads. Adding a provider means adding a row here, and nothing else.
"""

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class Provider:
    """How to connect to one mail provider family.

    Attributes:
        name: The value to pass as ``--provider``.
        summary: One line for ``mycord-email providers``.
        imap_server: Server URL in Himalaya's ``scheme://host:port`` form.
        smtp_server: Server URL in Himalaya's ``scheme://host:port`` form.
        starttls: Whether the scheme is ``imap://``/``smtp://`` plus STARTTLS
            rather than implicit TLS. Bridge-style servers need True.
        needs_cert: Whether a TLS certificate must be supplied out of band.
            Only Proton Bridge sets this.
        needs_app_password: Whether the provider rejects the account password
            and requires a generated app password.
        notes: Free-form caveats surfaced in the CLI as provider help.
    """

    name: str
    summary: str
    imap_server: str
    smtp_server: str
    starttls: bool = False
    needs_cert: bool = False
    needs_app_password: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


#: Sentinel for Proton Bridge, whose ports and certificate come from the local
#: Bridge instance rather than from a fixed public hostname.
BRIDGE_IMAP_DEFAULT: Final = "imap://127.0.0.1:1143"
BRIDGE_SMTP_DEFAULT: Final = "smtp://127.0.0.1:1025"


def _bridge(imap: str = BRIDGE_IMAP_DEFAULT, smtp: str = BRIDGE_SMTP_DEFAULT) -> Provider:
    """Build the Proton Bridge provider row.

    Bridge listens on loopback with a certificate for ``CN=127.0.0.1`` that no
    public CA signs, so the config must pin that certificate or every connection
    fails certificate verification. Ports are overridable because Bridge assigns
    them per installation.

    Args:
        imap: IMAP server URL for this Bridge installation.
        smtp: SMTP server URL for this Bridge installation.

    Returns:
        The Provider row.
    """
    return Provider(
        name="proton-bridge",
        summary="Proton Mail through the local Bridge daemon (STARTTLS on loopback)",
        imap_server=imap,
        smtp_server=smtp,
        starttls=True,
        needs_cert=True,
        notes=(
            "Requires Proton Mail Bridge running and logged in.",
            "The Bridge certificate must be pinned; it is self-signed for 127.0.0.1.",
            "Credentials are the per-Bridge password, not the Proton account password.",
        ),
    )


#: Every supported provider, keyed by the ``--provider`` value.
PROVIDERS: Final[dict[str, Provider]] = {
    p.name: p
    for p in (
        _bridge(),
        Provider(
            name="proton-bridge-unix",
            summary="Proton Bridge variant for a Bridge on a custom port",
            imap_server=BRIDGE_IMAP_DEFAULT,
            smtp_server=BRIDGE_SMTP_DEFAULT,
            starttls=True,
            needs_cert=True,
            notes=("Same as proton-bridge; override --imap/--smtp for custom ports.",),
        ),
        Provider(
            name="imap",
            summary="Plain IMAP + SMTP with implicit TLS (Fastmail, Migadu, Dovecot, ...)",
            imap_server="imaps://imap.example.com:993",
            smtp_server="smtps://smtp.example.com:465",
            notes=(
                "Override --imap/--smtp with the provider's published hosts.",
                "Use after <pattern> / order by ... search as documented for each server.",
            ),
        ),
        Provider(
            name="imap-starttls",
            summary="Plain IMAP + SMTP on port 143/587 with STARTTLS",
            imap_server="imap://imap.example.com:143",
            smtp_server="smtp://smtp.example.com:587",
            starttls=True,
            notes=("For servers that expose STARTTLS instead of implicit TLS.",),
        ),
        Provider(
            name="gmail",
            summary="Gmail over IMAP + SMTP (app password required)",
            imap_server="imaps://imap.gmail.com:993",
            smtp_server="smtps://smtp.gmail.com:465",
            needs_app_password=True,
            notes=(
                "Himalaya also ships a native Gmail backend; this row uses IMAP",
                "so the shared message/flag/envelope commands all work.",
                "Google rejects the account password: create an app password.",
            ),
        ),
        Provider(
            name="microsoft365",
            summary="Microsoft 365 / Outlook over IMAP + SMTP (app password or OAuth)",
            imap_server="imaps://outlook.office365.com:993",
            smtp_server="smtps://smtp.office365.com:465",
            needs_app_password=True,
            notes=(
                "Microsoft disables IMAP basic auth on many tenants.",
                "If basic auth is refused, use Himalaya's msgraph backend instead.",
            ),
        ),
    )
}


def get_provider(name: str) -> Provider:
    """Return the provider row for ``name``.

    Args:
        name: A key of :data:`PROVIDERS`.

    Returns:
        The matching Provider.

    Raises:
        KeyError: When the name is unknown. Callers surface this as a
            user-facing error listing the valid choices.
    """
    return PROVIDERS[name]


def provider_names() -> list[str]:
    """Return every provider name, sorted, for CLI choices and error messages."""
    return sorted(PROVIDERS)
