"""gvoice-skill — a CLI over the `gvoice/` TypeScript library.

Two jobs only:

1. **Session capture.** Get the Google cookie jar (HttpOnly included) out of a browser
   and into `gv-session.json`, because nothing works without it and every alternative
   route is worse.
2. **Reading.** Threads, conversations, and search over the Voice HTTP control plane,
   decoded out of the API's positional proto3-JSON into something an agent can read.

Calling, speaking, and the agent loop are *not* reimplemented here. Those live in the
library's probes, and this CLI delegates to them so there is exactly one implementation
of the SIP dialog and the media plane.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import cookies as cookie_ops
from . import proto
from .bridge import GvoiceError, LibraryPaths, api_call, find_library
from .cdp import CdpError, capture_cookies, default_profile

# Methods that read account state. Kept as names because the library owns the mapping.
METHOD_ACCOUNT_GET = "account/get"
METHOD_WARMSYNC = "inbox/warmsync"
METHOD_THREAD_LIST = "api2thread/list"
METHOD_THREAD_GET = "api2thread/get"
METHOD_SEARCH = "api2thread/search"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    if isinstance(payload, str):
        print(payload)
    elif isinstance(payload, list):
        for item in payload:
            print(item)
    elif isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key:>14}: {value}")
    else:
        print(payload)


def _write_session(payload: dict[str, Any], path: Path) -> None:
    """Write the jar 0600, atomically. It authenticates the whole Google account.

    The file is created 0600 *before* it holds anything secret, rather than written and
    then chmodded — otherwise the cookie values sit in a world-readable file for the
    duration of the write. The staging name is derived from the target so it stays under
    the same `gv-session.json*` ignore rule.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "w") as fh:
            fh.write(json.dumps(payload, indent=1) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def cmd_login(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    profile = None
    if args.profile_dir:
        base = Path(args.profile_dir).expanduser()
        profile = type(default_profile())(base.parent, base.name)
    elif not args.default_profile:
        profile = default_profile()

    target = Path(args.session).expanduser() if args.session else library.session
    try:
        raw = capture_cookies(
            profile,
            chrome=args.chrome,
            timeout=args.timeout,
            copy_profile=not args.no_copy_profile,
            headless=not args.headed,
        )
        payload = cookie_ops.build_session(raw)
    except (CdpError, cookie_ops.CookieError) as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        if args.verbose:
            raise
        return EXIT_AUTH

    _write_session(payload, target)
    summary = cookie_ops.summarize(payload["cookies"])
    if args.json:
        _emit({"session": str(target), **summary}, True)
    else:
        print(f"wrote {target} (mode 0600)")
        print(f"  cookies : {summary['count']} across {len(summary['hosts'])} hosts")
        print(f"  sapisid : {'present' if summary['has_sapisid'] else 'MISSING'}")
        print(f"  dupes   : {summary['duplicate_names']} names set on more than one domain")
    return EXIT_OK


def cmd_session(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    path = Path(args.session).expanduser() if args.session else library.session
    if not path.is_file():
        info = {"session": str(path), "present": False}
        _emit(info if args.json else f"no session at {path} — run `gvoice-skill login`", args.json)
        return EXIT_AUTH
    payload = json.loads(path.read_text())
    summary = cookie_ops.summarize(
        dict(c, name=c.get("name"), value=c.get("value", "x"), domain=c.get("domain", ""))
        for c in payload.get("cookies", [])
    )
    mode = stat.S_IMODE(path.stat().st_mode)
    info = {
        "session": str(path),
        "present": True,
        "mode": oct(mode),
        "mode_ok": mode & 0o077 == 0,
        **summary,
        "sapisid_present": bool(payload.get("sapisid")),
    }
    if args.json:
        _emit(info, True)
    else:
        hosts = summary["hosts"]
        names = summary["names"]
        assert isinstance(hosts, dict) and isinstance(names, list)  # from summarize()
        print(f"session    : {path}")
        mode_ok = "" if info["mode_ok"] else "  (WARNING: readable by others)"
        print(f"mode       : {oct(mode)}{mode_ok}")
        print(f"cookies    : {info['count']} across {len(hosts)} hosts")
        print(f"sapisid    : {'present' if info['sapisid_present'] else 'MISSING'}")
        print(f"names      : {', '.join(str(n) for n in names[:12])}")
    return EXIT_OK if info["mode_ok"] else EXIT_ERROR


def _require_session(library: LibraryPaths) -> None:
    if not library.session.is_file():
        raise GvoiceError(
            f"no session at {library.session} — run `gvoice-skill login` against a browser "
            "profile that is signed in to voice.google.com"
        )
    mode = stat.S_IMODE(library.session.stat().st_mode)
    if mode & 0o077:
        raise GvoiceError(
            f"{library.session} is mode {oct(mode)}; it holds the whole account, so "
            f"chmod 600 it before continuing"
        )


def _warmsync_token(library: LibraryPaths) -> int | str | None:
    return proto.warmsync_token(api_call(library, METHOD_WARMSYNC, []))


def cmd_account(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    _require_session(library)
    result = api_call(library, METHOD_ACCOUNT_GET, [None, 1])
    number = None
    outer = result[0] if isinstance(result, list) and result else None
    if isinstance(outer, list) and outer:
        number = outer[0]
    elif isinstance(outer, str):
        number = outer
    if args.json:
        _emit({"number": number, "raw": result}, True)
    else:
        print(number or "(could not read own number)")
    return EXIT_OK if number else EXIT_ERROR


def cmd_threads(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    _require_session(library)
    token = _warmsync_token(library)
    result = api_call(library, METHOD_THREAD_LIST, [token, args.limit])
    threads, next_token = proto.parse_thread_list(result)
    if args.json:
        _emit({"threads": [t.to_dict() for t in threads], "nextSyncToken": next_token}, True)
        return EXIT_OK
    if not threads:
        print("no threads returned (an empty list can also mean the sync token was rejected)")
        return EXIT_OK
    for thread in threads:
        flag = "*" if thread.unread else " "
        latest = thread.messages[0] if thread.messages else None
        preview = (latest.text or "").replace("\n", " ")[:60] if latest else ""
        print(f"{flag} {thread.id:<20} {preview}".rstrip())
    print(
        "(thread/list carries ids and unread flags, not message text — "
        "run `gvoice-skill thread <id>` for a conversation)",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_thread(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    _require_session(library)
    thread_id = args.thread if args.thread.startswith("t.") else f"t.{args.thread}"
    result = api_call(library, METHOD_THREAD_GET, [thread_id, args.limit])
    thread = proto.parse_thread_get(result)
    if thread is None:
        print(f"no messages in {thread_id}", file=sys.stderr)
        return EXIT_ERROR
    ordered = sorted(thread.messages, key=lambda m: m.timestamp_ms or 0)
    if args.json:
        _emit(thread.to_dict(), True)
        return EXIT_OK
    print(f"{thread.id}  ({len(ordered)} messages)")
    for message in ordered:
        print(f"{message.when} [{message.kind}] {(message.text or '')}")
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    _require_session(library)
    result = api_call(library, METHOD_SEARCH, [args.query, args.limit])
    threads = proto.parse_search(result)
    if args.json:
        _emit({"query": args.query, "groups": [t.to_dict() for t in threads]}, True)
        return EXIT_OK
    total = sum(len(t.messages) for t in threads)
    if not total:
        print(f"no matches for {args.query!r}")
        return EXIT_OK
    print(f"{total} match(es) for {args.query!r}")
    for thread in threads:
        for message in thread.messages:
            print(f"{message.when} {thread.id}  {(message.text or '')[:100]}")
    return EXIT_OK


def cmd_api(args: argparse.Namespace) -> int:
    """Raw escape hatch: any VoiceClientService method with a positional body."""
    library = find_library(args.library)
    _require_session(library)
    body = json.loads(args.body) if args.body else []
    if not isinstance(body, list):
        print("body must be a JSON array (the API takes positional protojson)", file=sys.stderr)
        return EXIT_USAGE
    result = api_call(library, args.method, body, timeout=args.timeout)
    _emit(result, True)
    return EXIT_OK


def _run_probe(library: LibraryPaths, script: str, argv: list[str]) -> int:
    """Run one of the library's probes as a subprocess, inheriting stdio."""
    probe = library.root / "src" / script
    if not probe.is_file():
        print(f"probe not found: {probe}", file=sys.stderr)
        return EXIT_ERROR
    if not library.install_ok:
        print(f"dependencies missing in {library.root}; run `pnpm install` there", file=sys.stderr)
        return EXIT_ERROR
    if not library.session.is_file():
        print(
            f"no session at {library.session} — run `gvoice-skill login` first",
            file=sys.stderr,
        )
        return EXIT_AUTH
    proc = subprocess.run([library.node(), str(probe), *argv], cwd=library.root)
    return proc.returncode


def cmd_sip(args: argparse.Namespace) -> int:
    library = find_library(args.library)
    return _run_probe(library, "probe-sip.ts", [])


def cmd_call(args: argparse.Namespace) -> int:
    """Place a call. Delegates to the library's probe — no second SIP implementation."""
    library = find_library(args.library)
    number = args.number if args.number.startswith("+") else f"+{args.number}"
    if args.mode == "control":
        argv = [number, "--seconds", str(args.seconds), "--brain", args.brain]
        if args.token:
            argv += ["--token", args.token]
        if args.port:
            argv += ["--port", str(args.port)]
        if args.greeting:
            argv += ["--greeting", args.greeting]
        return _run_probe(library, "probe-control.ts", argv)
    if args.mode == "agent":
        argv = [number, "--seconds", str(args.seconds), "--brain", args.brain]
        if args.greeting:
            argv += ["--greeting", args.greeting]
        return _run_probe(library, "probe-agent.ts", argv)
    if args.mode == "say":
        if not args.greeting:
            print('--mode say needs --greeting "..."', file=sys.stderr)
            return EXIT_USAGE
        return _run_probe(library, "probe-voice.ts", [number, args.greeting, str(args.seconds)])
    return _run_probe(library, "probe-media.ts", [number, str(args.seconds)])


def cmd_sms(args: argparse.Namespace) -> int:
    """Replay a captured send. The server rejects synthesised ids, so there is no more."""
    library = find_library(args.library)
    if not args.capture:
        print(
            "api2thread/sendsms needs a server-issued single-use message id (f9) or the\n"
            "encrypted envelope (f11); neither can be synthesised, and the server rejects\n"
            "replayed ids. Capture one real send from the web client and pass it:\n"
            '  gvoice-skill sms send +15551234567 "text" --capture sendsms-capture.json\n'
            "See gvoice/src/sms.ts for the field map.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    return _run_probe(library, "probe-sms.ts", [args.number, args.text, args.capture])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gvoice-skill",
        description="Google Voice over the unofficial gvoice library: session, reading, calls.",
    )
    parser.add_argument("--library", help="path to the gvoice library (default: sibling dir)")
    parser.add_argument("--session", help="session json path (default: <library>/gv-session.json)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--verbose", action="store_true", help="show tracebacks")
    sub = parser.add_subparsers(dest="command", required=True)

    # Repeat the output flags on each subcommand so `search --json` works as well as
    # `--json search`; SUPPRESS keeps the subparser from clobbering an earlier value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    _add = sub.add_parser
    sub.add_parser = lambda name, **kw: _add(name, parents=[common], **kw)  # type: ignore[method-assign]

    login = sub.add_parser("login", help="capture the cookie jar from a browser profile")
    login.add_argument(
        "--profile-dir", help="Chrome profile directory, e.g. ~/.config/google-chrome/Default"
    )
    login.add_argument(
        "--default-profile", action="store_true", help="use the standard Chrome profile"
    )
    login.add_argument("--chrome", help="browser binary")
    login.add_argument("--headed", action="store_true", help="run the browser with a window")
    login.add_argument("--no-copy-profile", action="store_true", help="drive the profile in place")
    login.add_argument("--timeout", type=float, default=45.0)
    login.set_defaults(func=cmd_login)

    session = sub.add_parser("session", help="show session status without printing secrets")
    session.set_defaults(func=cmd_session)

    account = sub.add_parser("account", help="read the account's own number")
    account.set_defaults(func=cmd_account)

    threads = sub.add_parser("threads", help="list recent conversations")
    threads.add_argument("--limit", type=int, default=10)
    threads.set_defaults(func=cmd_threads)

    thread = sub.add_parser("thread", help="read one conversation")
    thread.add_argument("thread", help="thread id (t.+1555...) or a phone number")
    thread.add_argument("--limit", type=int, default=20)
    thread.set_defaults(func=cmd_thread)

    search = sub.add_parser("search", help="search messages")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=cmd_search)

    api = sub.add_parser("api", help="call any VoiceClientService method (raw JSON)")
    api.add_argument("method", help="e.g. account/get")
    api.add_argument("body", nargs="?", default="[]", help="positional JSON array")
    api.add_argument("--timeout", type=float, default=60.0)
    api.set_defaults(func=cmd_api)

    sip = sub.add_parser("sip", help="REGISTER against the live registrar and report")
    sip.set_defaults(func=cmd_sip)

    call = sub.add_parser("call", help="place a call (delegates to the library probes)")
    call.add_argument("number", help="E.164 number")
    call.add_argument(
        "--mode",
        choices=("media", "say", "agent", "control"),
        default="media",
        help="media: attach and report; say: speak a phrase; agent: local brain; control: HTTP API",
    )
    call.add_argument("--seconds", type=int, default=30)
    call.add_argument("--brain", default="script", help="echo|script|http|none")
    call.add_argument("--greeting")
    call.add_argument("--token", help="control API token")
    call.add_argument("--port", type=int)
    call.set_defaults(func=cmd_call)

    sms = sub.add_parser("sms", help="SMS operations (send requires a captured exchange)")
    sms_sub = sms.add_subparsers(dest="sms_command", required=True)
    sms_send = sms_sub.add_parser("send")
    sms_send.add_argument("number")
    sms_send.add_argument("text")
    sms_send.add_argument("--capture", help="captured sendsms body json")
    sms_send.set_defaults(func=cmd_sms)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except GvoiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            raise
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
