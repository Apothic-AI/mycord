"""Command-line interface for the mycord IRC REPL session daemon."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mycord_irc_repl.client import (
    SessionClient,
    SessionNotRunning,
    default_log_path,
    is_running,
)
from mycord_irc_repl.config import IRCConfig, load_config
from mycord_irc_repl.protocol import OP_EVAL, OP_RESET, OP_SHUTDOWN, OP_STATUS
from mycord_irc_repl.session import configure_logging, default_socket_path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_SESSION = 2


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="session socket path (default: XDG runtime dir or ~/.cache/mycord-irc-repl)",
    )
    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--socket",
        type=Path,
        default=argparse.SUPPRESS,
        help="session socket path (default: XDG runtime dir or ~/.cache/mycord-irc-repl)",
    )

    parser = argparse.ArgumentParser(
        prog="mycord-irc-repl",
        description="Persistent REPL-style session over pydle.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="launch the session daemon", parents=[sub_common])
    start.add_argument("--server", default=None, help="IRC server (default: $IRC_SERVER)")
    start.add_argument("--port", type=int, default=None, help="IRC port")
    start.add_argument("--nick", default=None, help="nickname (default: $IRC_NICK)")
    start.add_argument("--username", default=None, help="IRC username")
    start.add_argument("--realname", default=None, help="IRC real name")
    tls = start.add_mutually_exclusive_group()
    tls.add_argument("--tls", dest="tls", action="store_true", help="use TLS (default)")
    tls.add_argument("--no-tls", dest="tls", action="store_false", help="disable TLS")
    start.set_defaults(tls=None)
    start.add_argument("--no-connect", action="store_true", help="start without connecting")
    start.add_argument("--wait", type=float, default=30.0, help="seconds to wait for registration")
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


def _config_from_args(args: argparse.Namespace) -> IRCConfig:
    """Load the session configuration with command-line overrides."""
    overrides: dict[str, object] = {
        name: value
        for name, value in (
            ("server", args.server),
            ("port", args.port),
            ("nickname", args.nick),
            ("username", args.username),
            ("realname", args.realname),
            ("tls", args.tls),
        )
        if value is not None
    }
    config = load_config(overrides=overrides)
    if args.no_connect:
        config = IRCConfig(
            nickname=config.nickname,
            username=config.username,
            realname=config.realname,
            tls=config.tls,
        )
    return config


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
        "mycord_irc_repl",
        "--socket",
        str(socket_path),
        "start",
        "--foreground",
        "--wait",
        str(args.wait),
    ]
    for flag, value in (
        ("--server", args.server),
        ("--port", args.port),
        ("--nick", args.nick),
        ("--username", args.username),
        ("--realname", args.realname),
    ):
        if value is not None:
            command.extend((flag, str(value)))
    if args.tls is True:
        command.append("--tls")
    elif args.tls is False:
        command.append("--no-tls")
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
        )

    if not _wait_for_socket(socket_path, args.wait + 5.0):
        print(f"session failed to start; see {log_path}", file=sys.stderr)
        return EXIT_ERROR
    return _report_status(socket_path)


def _run_foreground(args: argparse.Namespace, socket_path: Path) -> int:
    """Run the daemon in this process (used for debugging and tests)."""
    configure_logging(None)
    try:
        config = _config_from_args(args)
        if config.server:
            config.validate_for_connect()
    except ValueError as exc:
        print(f"invalid IRC configuration: {exc}", file=sys.stderr)
        return EXIT_ERROR

    from mycord_irc_repl.session import ReplSession, serve

    session = ReplSession(config)

    async def main() -> None:
        if config.server:
            await session.start(wait=args.wait)
        else:
            print("starting offline (no IRC server configured)", file=sys.stderr)
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
    """Poll until the daemon answers a ping or timeout elapses."""
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
    print(f"session:  {'connected' if connected else 'disconnected'}")
    print(f"nickname: {status.get('nickname')}")
    server = status.get("server")
    endpoint = f"{server}:{status.get('port')}" if server else "offline"
    print(f"server:   {endpoint}")
    print(f"TLS:      {status.get('tls')}")
    print(f"channels: {status.get('channels')}")
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
    """Run one non-eval opcode and print its result."""
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
    """Ask the daemon to stop and wait for its socket to disappear."""
    if not socket_path.exists():
        print("no session running", file=sys.stderr)
        return EXIT_NO_SESSION

    client = SessionClient(socket_path)
    try:
        asyncio.run(client.request({"op": OP_SHUTDOWN}, timeout=10.0))
    except SessionNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SESSION

    deadline = time.monotonic() + 5.0
    while socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if socket_path.exists():
        print(f"session did not stop; socket remains at {socket_path}", file=sys.stderr)
        return EXIT_ERROR
    print("session stopped")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = build_parser().parse_args(argv)
    socket_path = args.socket or default_socket_path()
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
    return EXIT_ERROR  # pragma: no cover - argparse enforces subcommands


__all__ = ["build_parser", "main"]
