"""Command line interface for the mycord-telegram REPL session.

Commands:
    login    Authenticate a user account (desktop / qr / phone).
    start    Launch the session daemon (detached).
    eval     Run a snippet against the running session.
    status   Report connection state.
    reset    Clear agent-defined globals.
    stop     Shut the session down.

Typical agent loop::

    mycord-telegram-repl login --method desktop
    mycord-telegram-repl start
    mycord-telegram-repl eval "me = await client.get_me()"
    mycord-telegram-repl eval "await client.get_dialogs(limit=10)"
    mycord-telegram-repl stop
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mycord_telegram_repl.client import (
    SessionClient,
    SessionNotRunning,
    default_log_path,
    is_running,
)
from mycord_telegram_repl.protocol import OP_EVAL, OP_PING, OP_RESET, OP_SHUTDOWN, OP_STATUS
from mycord_telegram_repl.session import configure_logging, default_socket_path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_SESSION = 2

#: Where the session string written by ``login`` is stored by default.
SESSION_FILE_ENV = "TELEGRAM_STRING_SESSION_FILE"


def default_session_file() -> Path:
    """Return the default path for the persisted session string."""
    override = os.environ.get(SESSION_FILE_ENV, "").strip()
    if override:
        return Path(override)
    return Path.home() / ".cache" / "mycord-telegram-repl" / "session.txt"


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    # Shared options are attached to the top level and to every subcommand so
    # that `--socket` works on either side of the subcommand name. The subcommand
    # copies use SUPPRESS so that omitting the flag there does not clobber a
    # value already parsed at the top level.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="session socket path (default: XDG runtime dir or ~/.cache/mycord-telegram-repl)",
    )

    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--socket",
        type=Path,
        default=argparse.SUPPRESS,
        help="session socket path (default: XDG runtime dir or ~/.cache/mycord-telegram-repl)",
    )

    parser = argparse.ArgumentParser(
        prog="mycord-telegram-repl",
        description="Persistent REPL-style session over Telethon.",
        parents=[common],
    )

    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="authenticate a Telegram user account")
    login.add_argument(
        "--method",
        choices=("desktop", "qr", "phone"),
        default="desktop",
        help="desktop = import Telegram Desktop session via tdl; "
        "qr = scan a QR code; phone = phone number + code + 2FA",
    )
    login.add_argument(
        "--session-file",
        type=Path,
        default=None,
        help=f"where to save the session string (default: {default_session_file()})",
    )
    login.add_argument(
        "--desktop-path",
        type=Path,
        default=None,
        help="Telegram Desktop data directory (default: auto-detect)",
    )
    login.add_argument(
        "--print-only",
        action="store_true",
        help="print the session string instead of saving it to a file",
    )

    start = sub.add_parser("start", help="launch the session daemon", parents=[sub_common])
    start.add_argument(
        "--session-file",
        type=Path,
        default=None,
        help="session string file written by `login`",
    )
    start.add_argument("--no-connect", action="store_true", help="start offline, do not log in")
    start.add_argument("--wait", type=float, default=30.0, help="seconds to wait for READY")
    start.add_argument("--foreground", action="store_true", help="do not detach")

    ev = sub.add_parser("eval", help="evaluate a snippet in the session", parents=[sub_common])
    ev.add_argument(
        "code",
        nargs="?",
        default=None,
        help="Python source; top-level await is supported (omit when using --file)",
    )
    ev.add_argument("--timeout", type=float, default=30.0)
    ev.add_argument("--file", type=Path, default=None, help="read code from a file")

    sub.add_parser("status", help="report session status", parents=[sub_common])
    sub.add_parser("reset", help="clear agent-defined globals", parents=[sub_common])
    sub.add_parser("stop", help="shut the session down", parents=[sub_common])

    return parser


def _load_session_string(path: Path | None) -> str | None:
    """Read a session string from a file, the environment, or the default path."""
    if path is not None:
        return _read_session_file(path)

    env_value = os.environ.get("TELEGRAM_STRING_SESSION", "").strip()
    if env_value:
        return env_value

    default = default_session_file()
    return _read_session_file(default) if default.is_file() else None


def _read_session_file(path: Path) -> str | None:
    """Return the trimmed contents of a session string file."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _load_dotenv() -> None:
    """Load a nearby .env into the environment, if python-dotenv is present."""
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / ".env"
        if not candidate.is_file():
            continue
        try:
            from dotenv import load_dotenv

            load_dotenv(candidate, override=False)
        except Exception:
            continue
        return


def cmd_login(args: argparse.Namespace) -> int:
    """Authenticate a user account and persist the resulting session string."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from mycord_telegram_repl.auth import (
        AuthError,
        import_desktop_session,
        load_credentials,
        phone_login,
        qr_login,
        save_session_string,
    )

    _load_dotenv()

    try:
        if args.method == "desktop":
            storage = import_desktop_session(desktop_path=args.desktop_path)
            print(
                "Desktop session imported. Note: tdl stores its own session and\n"
                "this skill reads it via TELEGRAM_TDL_STORAGE, or run\n"
                "`tdl` directly for reads. To get a Telethon StringSession instead,\n"
                "re-run with --method qr or --method phone.",
                file=sys.stderr,
            )
            print(f"storage: {storage}")
            return EXIT_OK

        api_id, api_hash = load_credentials()
        client = TelegramClient(StringSession(), api_id, api_hash)
        if args.method == "qr":
            session_string = asyncio.run(qr_login(client))
        else:
            session_string = asyncio.run(phone_login(client))
    except AuthError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR

    if args.print_only:
        print(session_string)
    else:
        target = args.session_file or default_session_file()
        save_session_string(session_string, target)
        print(f"session saved to {target}")
    return EXIT_OK


def cmd_start(args: argparse.Namespace, socket_path: Path) -> int:
    """Launch the daemon and wait for it to accept requests."""
    if is_running(socket_path):
        print(f"session already running at {socket_path}", file=sys.stderr)
        return EXIT_ERROR

    if args.foreground:
        return _run_foreground(args, socket_path)

    log_path = default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "mycord_telegram_repl",
        "--socket",
        str(socket_path),
        "start",
        "--foreground",
        "--wait",
        str(args.wait),
    ]
    session_string = _load_session_string(args.session_file)
    if session_string:
        # Passed via the environment, never argv: the session string is a
        # full-account credential and would otherwise be visible in `ps`.
        os.environ["TELEGRAM_STRING_SESSION"] = session_string
    if args.no_connect:
        command.append("--no-connect")

    with log_path.open("ab") as log_file:
        log_file.write(f"\n--- session start {time.ctime()} ---\n".encode())
        log_file.flush()
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )

    if not _wait_for_socket(socket_path, args.wait + 5.0):
        print(f"session failed to start; see {log_path}", file=sys.stderr)
        return EXIT_ERROR

    return _report_status(socket_path)


def _run_foreground(args: argparse.Namespace, socket_path: Path) -> int:
    """Run the daemon in this process (used for debugging and tests)."""
    from mycord_telegram_repl.session import ReplSession, serve

    configure_logging(None)
    _load_dotenv()
    session_string = None if args.no_connect else _load_session_string(args.session_file)

    session = ReplSession(session_string)

    async def main() -> None:
        if session_string:
            await session.start(wait=args.wait)
        else:
            print("starting offline (no session string)", file=sys.stderr)
        await serve(session, socket_path)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as exc:  # pragma: no cover - defensive
        print(f"session error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _wait_for_socket(socket_path: Path, timeout: float) -> bool:
    """Poll until the daemon answers a ping or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running(socket_path):
            return True
        time.sleep(0.15)
    return False


def _report_status(socket_path: Path) -> int:
    """Print the session status and translate it into an exit code."""
    client = SessionClient(socket_path)
    try:
        reply = asyncio.run(client.request({"op": OP_STATUS}))
    except SessionNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SESSION

    status = reply.get("result") or {}
    connected = bool(status.get("connected"))
    who = status.get("user") or "not authorized"
    print(f"session:  {'connected' if connected else 'disconnected'} as {who}")
    print(f"user_id:  {status.get('user_id')}")
    print(f"uptime:   {status.get('uptime_seconds')}s")
    print(f"socket:   {socket_path}")
    return EXIT_OK


def cmd_eval(args: argparse.Namespace, socket_path: Path) -> int:
    """Send one snippet to the running session and print the reply."""
    if args.file:
        code = args.file.read_text()
    elif args.code is not None:
        code = args.code
    else:
        print("provide code to evaluate, or --file PATH", file=sys.stderr)
        return EXIT_ERROR

    client = SessionClient(socket_path)
    try:
        reply = asyncio.run(
            client.request({"op": OP_EVAL, "code": code, "timeout": args.timeout})
        )
    except SessionNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SESSION

    stdout = reply.get("stdout") or ""
    if stdout:
        sys.stdout.write(stdout if stdout.endswith("\n") else stdout + "\n")

    if reply.get("ok"):
        result = reply.get("result")
        if result:
            print(result)
        return EXIT_OK

    error = reply.get("error") or "unknown error"
    sys.stderr.write(error if error.endswith("\n") else error + "\n")
    return EXIT_ERROR


def cmd_simple(op: str, socket_path: Path) -> int:
    """Run a single non-eval opcode and print its result."""
    client = SessionClient(socket_path)
    try:
        reply = asyncio.run(client.request({"op": op}))
    except SessionNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SESSION

    if not reply.get("ok"):
        print(reply.get("error") or "unknown error", file=sys.stderr)
        return EXIT_ERROR

    result: Any = reply.get("result")
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result or "")
    return EXIT_OK


def cmd_stop(socket_path: Path) -> int:
    """Ask the daemon to shut down and remove its socket."""
    if not socket_path.exists():
        print("no session running", file=sys.stderr)
        return EXIT_NO_SESSION

    client = SessionClient(socket_path)
    try:
        asyncio.run(client.request({"op": OP_SHUTDOWN}, timeout=10.0))
    except SessionNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SESSION

    for _ in range(50):
        if not socket_path.exists():
            break
        time.sleep(0.1)
    if socket_path.exists():
        socket_path.unlink()

    print("session stopped")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    socket_path = args.socket or default_socket_path()

    if args.command == "login":
        return cmd_login(args)
    if args.command == "start":
        return cmd_start(args, socket_path)
    if args.command == "eval":
        return cmd_eval(args, socket_path)
    if args.command == "status":
        return _report_status(socket_path)
    if args.command == "reset":
        return cmd_simple(OP_RESET, socket_path)
    if args.command == "stop":
        return cmd_stop(socket_path)

    return EXIT_ERROR  # pragma: no cover - argparse enforces the choices


__all__ = ["OP_EVAL", "OP_PING", "build_parser", "main"]
