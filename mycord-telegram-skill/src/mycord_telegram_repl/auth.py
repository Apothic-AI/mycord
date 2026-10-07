"""Authentication for a Telethon user account, by every non-bot method.

Telegram differs from Discord in a way that matters here: there is no
selfbot. MTProto is a published API and every official client uses it, so
driving a user account is supported rather than forbidden.

What that does *not* give you is a single bearer token. A Telethon user
session needs three distinct values:

``api_id`` / ``api_hash``
    Credentials for your *application*, not your account. Issued at
    my.telegram.org after a manual signup. Every client bundles the same
    public pair, so these are not secret in any meaningful sense - but you
    still have to apply for them.

The authorization key
    The thing that actually grants account access. Persisted as a
    ``*.session`` SQLite file, or as a ``StringSession`` string.

This module implements the three ways to obtain that key without a bot
token, cheapest first:

1. :func:`import_desktop_session` - reuse an existing Telegram Desktop
   login. No phone number, no code, no 2FA, and no secret is ever handed
   to the skill. Requires the ``tdl`` binary.
2. :func:`qr_login` - scan a QR code from the Telegram app. No phone
   number entry, no 2FA prompt. Needs ``api_id``/``api_hash``.
3. :func:`phone_login` - phone number, login code, and 2FA password.
   The fallback when nothing else is available.

Bot-token auth is deliberately absent from this module.
"""

import asyncio
import base64
import ipaddress
import logging
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger("mycord_telegram_repl.auth")

#: Where ``tdl`` and Telethon look for an existing Telegram Desktop install.
DESKTOP_SEARCH_PATHS: tuple[Path, ...] = (
    Path.home() / ".local/share/TelegramDesktop",
    Path.home() / ".local/share/Telegram Desktop",
    Path.home() / ".var/app/org.telegram.desktop/data/TelegramDesktop",
    Path("/usr/share/telegram-desktop"),
    Path.home() / "snap/telegram/common/.local/share/TelegramDesktop",
)

#: Credential names read from the environment or a ``.env`` file.
API_ID_ENV: str = "TELEGRAM_API_ID"
API_HASH_ENV: str = "TELEGRAM_API_HASH"
STRING_SESSION_ENV: str = "TELEGRAM_STRING_SESSION"


class AuthError(RuntimeError):
    """Raised when a login method fails or its prerequisites are missing."""


# Placeholder app credentials. Only valid alongside an existing auth key,
# where no login handshake occurs and Telegram never checks them. See
# Method 1 in SKILL.md.
PLACEHOLDER_API_ID = 1
PLACEHOLDER_API_HASH = "0" * 32


def load_credentials() -> tuple[int, str]:
    """Read ``api_id`` and ``api_hash`` from the environment.

    Falls back to placeholders when neither is set. Those are only ever
    meaningful alongside an existing auth key (see Method 1 in SKILL.md); a
    fresh QR or phone login still needs real values, because it performs a
    handshake that Telegram validates.

    Returns:
        Tuple of (api_id, api_hash).

    Raises:
        AuthError: If only one value is missing, or ``api_id`` is not an integer.
    """
    api_id_raw = os.environ.get(API_ID_ENV, "").strip()
    api_hash = os.environ.get(API_HASH_ENV, "").strip()

    if not api_id_raw and not api_hash:
        # A session that already carries an auth key never runs a login
        # handshake, so Telegram never validates the app credentials - any
        # well-formed placeholder works. That makes the desktop path usable
        # with no .env at all, instead of demanding a my.telegram.org signup
        # for a value that goes unused.
        return PLACEHOLDER_API_ID, PLACEHOLDER_API_HASH

    candidates = ((API_ID_ENV, api_id_raw), (API_HASH_ENV, api_hash))
    missing = [name for name, value in candidates if not value]
    if missing:
        message = (
            f"missing {', '.join(missing)}; register an app at "
            "https://my.telegram.org and put the values in your .env"
        )
        raise AuthError(message)

    try:
        return int(api_id_raw), api_hash
    except ValueError as exc:
        message = f"{API_ID_ENV} must be an integer, got {api_id_raw!r}"
        raise AuthError(message) from exc


def load_string_session() -> str | None:
    """Return the configured ``StringSession`` value, if any."""
    return os.environ.get(STRING_SESSION_ENV, "").strip() or None


def find_desktop_client() -> Path | None:
    """Locate a Telegram Desktop data directory containing a ``tdata`` folder."""
    for candidate in DESKTOP_SEARCH_PATHS:
        if (candidate / "tdata").is_dir():
            return candidate
    return None


def require_tdl() -> str:
    """Return the path to the ``tdl`` binary.

    Raises:
        AuthError: If ``tdl`` is not installed or not on PATH.
    """
    found = shutil.which("tdl")
    if not found:
        message = (
            "tdl not found on PATH. Install it from https://github.com/iyear/tdl/releases "
            "(a single static Go binary), or use QR login instead."
        )
        raise AuthError(message)
    return found


def import_desktop_session(
    storage_path: Path | None = None,
    *,
    desktop_path: Path | None = None,
    namespace: str = "default",
) -> Path:
    """Import an existing Telegram Desktop session via the ``tdl`` binary.

    This is the cheapest non-bot method: it reuses a login the user already
    has, so no phone number, login code, 2FA password, or secret is ever
    handled by this skill. ``tdl`` embeds its own ``api_id``/``api_hash``.

    ``tdl`` prompts to pick a user id and to optionally log the desktop client
    out. Both prompts accept their default on a bare newline, so this feeds
    ``b"\\n\\nn\\n"`` on stdin instead of closing it: the first newline accepts
    the highlighted account, the second accepts the default answer (``N``) to the
    logout question. That keeps the desktop session signed in and makes the
    import work headlessly, with no TTY and no manual step.

    It never passes ``--logout``. Args:
        storage_path: Bolt storage directory for the imported session.
            Defaults to ``~/.tdl/data``.
        desktop_path: Telegram Desktop data directory. Auto-detected if omitted.
        namespace: ``tdl`` namespace to import into.

    Returns:
        Path to the storage directory holding the imported session.

    Raises:
        AuthError: If ``tdl`` is missing, no desktop client is found, or the
            import fails.
    """
    binary = require_tdl()
    desktop = desktop_path or find_desktop_client()
    if desktop is None:
        message = (
            "no Telegram Desktop data directory found; pass desktop_path explicitly, "
            "or use QR login instead"
        )
        raise AuthError(message)
    if not (desktop / "tdata").is_dir():
        message = f"{desktop} has no tdata/ directory; is that a Desktop install?"
        raise AuthError(message)

    storage = storage_path or (Path.home() / ".tdl" / "data")
    storage.parent.mkdir(parents=True, exist_ok=True)

    command = [
        binary,
        "login",
        "-T",
        "desktop",
        "-d",
        str(desktop),
        "--storage",
        f"path={storage},type=bolt",
    ]
    logger.info("importing Telegram Desktop session from %s", desktop)

    try:
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            # tdl's account picker and logout question are prompts, not a TUI
            # that strictly needs a terminal: each accepts its default on a bare
            # newline. Feeding newlines keeps this headless. Closing stdin (the
            # previous behaviour) made the picker read EOF and always fail.
            input="\n\nn\n",
        )
    except subprocess.TimeoutExpired as exc:
        message = (
            "tdl timed out. Pass --desktop-path explicitly if the client is in a "
            "non-standard location."
        )
        raise AuthError(message) from exc

    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        # tdl writes the session before asking whether to log the desktop client
        # out. That final question is a full-screen prompt that queries the
        # cursor position, so under a pipe it always EOFs and tdl exits non-zero
        # *after* a perfectly good import. Answering it is also irrelevant: we
        # never pass --logout, so the desktop session stays signed in regardless.
        if _TDL_IMPORT_OK_RE.search(combined):
            logger.warning(
                "tdl exited %s after a successful import; its desktop-logout "
                "prompt cannot be answered without a TTY. The desktop session "
                "is left signed in.",
                result.returncode,
            )
        else:
            message = (
                f"tdl desktop import failed (exit {result.returncode}): {combined.strip()}"
            )
            raise AuthError(message)

    logger.info("desktop session imported into %s", storage)
    return storage


_TDL_IMPORT_OK_RE = re.compile(r"Import\s+\d+\s+successfully")

_TDL_SESSION_RE = re.compile(
    rb'"DC":(\d+),"Addr":"([0-9.]+)","AuthKey":"([A-Za-z0-9+/=]+)"'
)


def string_session_from_tdl_storage(storage_path: Path | None = None) -> str:
    """Build a Telethon ``StringSession`` from a session ``tdl`` already imported.

    ``tdl`` persists the Telegram Desktop auth key in its own Bolt file, which
    Telethon cannot read. Without this bridge the desktop method stops at the
    import: the daemon has no session string, so it starts "not authorized" and
    there is no way forward without a fresh QR or phone login.

    The Bolt value is JSON holding the DC id, the DC address, and the base64
    auth key. Those map onto Telethon's ``StringSession`` layout, which is a
    leading version char followed by ``base64url(struct.pack('>B4sH256s',
    dc_id, ipv4, port, auth_key))``.

    Args:
        storage_path: Bolt file written by ``tdl``. Defaults to the ``default``
            namespace under ``~/.tdl/data``.

    Returns:
        The session string. This grants full account access - never log it.

    Raises:
        AuthError: If the file is missing, holds no session, or the auth key is
            the wrong size.
    """
    storage = storage_path or (Path.home() / ".tdl" / "data" / "default")
    try:
        raw = storage.read_bytes()
    except OSError as exc:
        message = f"cannot read tdl storage at {storage}: {exc}"
        raise AuthError(message) from exc

    match = _TDL_SESSION_RE.search(raw)
    if match is None:
        message = (
            f"no Telegram session found in {storage}. Run the desktop import "
            "first (mycord-telegram-repl login --method desktop)."
        )
        raise AuthError(message)

    dc_id = int(match.group(1))
    address = match.group(2).decode()
    auth_key = base64.b64decode(match.group(3))
    if len(auth_key) != 256:
        message = f"unexpected auth key length {len(auth_key)} in {storage}"
        raise AuthError(message)

    packed = struct.pack(
        ">B4sH256s",
        dc_id,
        ipaddress.ip_address(address).packed,
        443,
        auth_key,
    )
    session_string = "1" + base64.urlsafe_b64encode(packed).decode()

    # Verify it against Telethon's own parser before handing it back, so a
    # malformed session fails here rather than as a silent "not authorized".
    try:
        from telethon.sessions import StringSession

        parsed = StringSession(session_string)
    except Exception as exc:
        message = f"constructed session failed Telethon validation: {exc}"
        raise AuthError(message) from exc
    if parsed.dc_id != dc_id or not parsed.auth_key:
        message = "constructed session did not round-trip through Telethon"
        raise AuthError(message)

    logger.info("built StringSession for dc %s (%s)", dc_id, parsed.server_address)
    return session_string


async def qr_login(client: Any) -> str:
    """Log in by scanning a QR code, and return the new session string.

    Requires ``api_id``/``api_hash``. No phone-number entry and no 2FA prompt.

    Args:
        client: An unstarted :class:`telethon.TelegramClient`.

    Returns:
        The session string, suitable for saving via ``.session.save()``.

    Raises:
        AuthError: If the QR flow is refused, times out, or is already used.
    """
    await client.connect()
    if await client.is_user_authorized():
        return cast("str", client.session.save())

    qr_login_obj = await client.qr_login()
    print("Scan this with Telegram (Settings > Devices > Link Desktop Device):")
    print(f"  {qr_login_obj.url}")
    print("Waiting for scan...")

    try:
        await qr_login_obj.wait(timeout=120)
    except asyncio.TimeoutError as exc:
        message = "QR login timed out after 120s"
        raise AuthError(message) from exc
    except Exception as exc:
        message = f"QR login failed: {exc}"
        raise AuthError(message) from exc

    return cast("str", client.session.save())


async def phone_login(client: Any) -> str:
    """Log in with a phone number, login code, and 2FA password.

    The fallback method. Interactive: the login code arrives by Telegram
    message (or SMS) and the 2FA password is asked for only if the account
    has one.

    Args:
        client: An unstarted :class:`telethon.TelegramClient`.

    Returns:
        The session string, suitable for saving via ``.session.save()``.

    Raises:
        AuthError: If the code or password is wrong, or login is refused.
    """
    phone = os.environ.get("TELEGRAM_PHONE", "").strip()
    if not phone:
        message = "set TELEGRAM_PHONE (with country code, e.g. +15551234567) to log in"
        raise AuthError(message)

    await client.connect()
    if await client.is_user_authorized():
        return cast("str", client.session.save())

    try:
        sent = await client.send_code_request(phone)
        code = input("Enter the login code Telegram sent you: ").strip()
        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=sent.phone_code_hash
            )
        except Exception as exc:
            # SessionPasswordNeededError surfaces here as a generic exception;
            # only then is 2FA worth asking for.
            if "password" not in type(exc).__name__.lower() and "2fa" not in str(exc).lower():
                raise
            password = os.environ.get("TELEGRAM_2FA_PASSWORD", "").strip()
            if not password:
                password = input("Enter your 2FA password: ").strip()
            await client.sign_in(password=password)
    except AuthError:
        raise
    except Exception as exc:
        message = f"phone login failed: {exc}"
        raise AuthError(message) from exc

    return cast("str", client.session.save())


def save_session_string(session_string: str, path: Path) -> None:
    """Write a ``StringSession`` to disk with restrictive permissions.

    Args:
        session_string: The value from ``client.session.save()``.
        path: Destination file.

    Raises:
        AuthError: If the write fails.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(session_string, encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        message = f"could not write session to {path}: {exc}"
        raise AuthError(message) from exc
    logger.info("session string saved to %s", path)
