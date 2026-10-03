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
import logging
import os
import shutil
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


def load_credentials() -> tuple[int, str]:
    """Read ``api_id`` and ``api_hash`` from the environment.

    Returns:
        Tuple of (api_id, api_hash).

    Raises:
        AuthError: If either value is missing or ``api_id`` is not an integer.
    """
    api_id_raw = os.environ.get(API_ID_ENV, "").strip()
    api_hash = os.environ.get(API_HASH_ENV, "").strip()

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

    ``tdl`` prompts interactively to pick a user id and to optionally log the
    desktop client out. This function runs it with stdin closed, so it
    requires the caller to have already run the import once by hand, or to
    accept ``tdl``'s defaults. It never passes ``--logout``; the desktop
    session is left intact.

    Args:
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
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        message = (
            "tdl timed out. It prompts for a user id interactively - run "
            "`tdl login -T desktop -d <desktop-path>` yourself once to pick an account."
        )
        raise AuthError(message) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        # tdl's account picker is a TUI: with stdin closed it reads EOF and
        # exits. That is expected non-interactive behaviour, not a real fault,
        # so point the user at the manual command instead of the raw traceback.
        if "EOF" in detail:
            message = (
                "tdl needs an interactive account pick, and this skill runs it with stdin "
                "closed. Import once by hand, then retry:\n"
                f"  tdl login -T desktop -d {desktop}\n"
                "Answer N when asked whether to log the desktop session out."
            )
        else:
            message = f"tdl desktop import failed (exit {result.returncode}): {detail}"
        raise AuthError(message)

    logger.info("desktop session imported into %s", storage)
    return storage


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
