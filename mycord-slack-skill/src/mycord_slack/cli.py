"""The `mycord-slack` command.

Shape:

* **Passthrough is the default verb.** `mycord-slack conversations.list --json '{...}'`
  forwards any Slack API method. It is *not* a curated command list, because an `xoxc-`
  session carries no scopes and a fixed verb list would put the artificial limits back.
* **Named verbs exist only where slackdump is the right tool** (list/read/search
  conversations), plus the three session verbs that make the rest work.

Exit codes are deliberately coarse and stable, so a script can branch without parsing:
`0` success, `1` runtime failure (including a Slack error), `2` usage error, `3` no
usable session.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import api, cdp, reads
from . import session as session_ops
from .config import ConfigError, Paths, api_base, paths, slackdump_binary
from .redact import redact

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3

#: Verbs that are implemented locally rather than forwarded to Slack. Everything else
#: that looks like `a.b` is treated as an API method.
RESERVED = {
    "login",
    "logout",
    "session",
    "channels",
    "read",
    "search",
    "users",
    "help",
}


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if isinstance(payload, str):
        print(payload)
    elif isinstance(payload, list):
        for item in payload:
            print(item)
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                print(f"{key:>14}: {json.dumps(value, default=str)}")
            else:
                print(f"{key:>14}: {value}")
    else:
        print(payload)


def _paths(args: argparse.Namespace) -> Paths:
    """Paths, honouring `--state` so tests and alternate installs stay isolated."""
    override = getattr(args, "state", None)
    if override:
        return Paths(state=Path(override).expanduser())
    return paths()


def _load(args: argparse.Namespace) -> session_ops.Session:
    return session_ops.load_session(_paths(args).session)


# --------------------------------------------------------------------------- session


def cmd_login(args: argparse.Namespace) -> int:
    """Capture the session from a signed-in browser and import it into slackdump.

    Both halves are reported separately, because they fail for different reasons: the
    capture fails when nobody is signed in, and the import fails when slackdump rejects
    what was captured. A capture without an import still leaves the passthrough working,
    so it is not an error — it is a warning that `channels`/`read`/`search` will not run
    until slackdump has credentials.
    """
    target = _paths(args)
    profile: cdp.ChromeProfile | None = None
    if args.profile:
        path = Path(args.profile).expanduser()
        profile = cdp.ChromeProfile(path.parent, path.name)

    try:
        captured = cdp.capture_session(
            profile,
            chrome=args.chrome,
            timeout=args.timeout,
            copy_profile=not args.no_copy_profile,
            headless=not args.headed,
        )
    except (cdp.CdpError, cdp.SessionCaptureError) as exc:
        print(f"capture failed: {ascii_safe(str(exc))}", file=sys.stderr)
        if args.verbose:
            raise
        return EXIT_AUTH

    chosen = captured.workspaces[0]
    if args.team:
        match = [w for w in captured.workspaces if w.team_id == args.team]
        if not match:
            known = ", ".join(f"{w.team_id} ({w.name})" for w in captured.workspaces)
            print(f"no workspace {args.team} in this browser; found: {known}", file=sys.stderr)
            return EXIT_USAGE
        chosen = match[0]
    if len(captured.workspaces) > 1 and not args.team:
        others = ", ".join(f"{w.team_id} ({w.name})" for w in captured.workspaces[1:])
        print(
            f"note: this browser has {len(captured.workspaces)} workspaces; using "
            f"{chosen.team_id} ({chosen.name}). Pass --team to pick another: {others}",
            file=sys.stderr,
        )

    captured_at = time.time()
    record = session_ops.from_capture(
        chosen, captured.cookie, captured_at, captured.source, captured.cookie_expires
    )
    target.ensure()
    session_ops.write_session(record, target.session)

    import_detail = ""
    try:
        slackdump = slackdump_binary()
    except ConfigError as exc:
        import_detail = f"skipped: {exc}"
    else:
        result = session_ops.import_into_slackdump(record, slackdump)
        last = result.detail.splitlines()[-1] if result.detail.strip() else "no detail"
        import_detail = "ok" if result.ok else f"failed: {last}"

    summary = {
        "session": str(target.session),
        "mode": "0600",
        **record.public(),
        "slackdump": import_detail,
    }
    if args.json:
        _emit(summary, True)
        return EXIT_OK
    print(f"wrote {target.session} (mode 0600, directory 0700)")
    print(f"  workspace : {record.team_name or '(unnamed)'}  {record.team_id}")
    print(f"  token     : {record.token_fingerprint}")
    print(f"  cookie    : d on .slack.com, {len(record.cookie)} chars")
    if record.cookie_expires:
        days = record.expires_in_days()
        print(
            f"  expires   : {_iso(record.cookie_expires)}  ({days:.0f} days)"
            if days is not None
            else ""
        )
    print(f"  captured  : {_iso(record.captured_at)} via {record.source}")
    print(f"  slackdump : {import_detail}")
    if not import_detail.startswith("ok"):
        print(
            "  (the passthrough works without slackdump; `channels`/`read`/`search` need it)",
            file=sys.stderr,
        )
    return EXIT_OK


def ascii_safe(text: str) -> str:
    """Redact, then strip ANSI escapes. slackdump colourises its stderr even when piped."""
    import re

    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", redact(text))
    return cleaned


def _iso(epoch: float | None) -> str:
    if not epoch:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(epoch))


def cmd_session(args: argparse.Namespace) -> int:
    """Report what is present, without printing a single secret byte.

    `--check` adds a live `auth.test`, which is the only way to know a non-expired cookie
    is still *accepted*: Slack revokes sessions on sign-out elsewhere, password change, or
    a workspace policy change, and none of that touches the cookie's expiry date.
    """
    target = _paths(args)
    info: dict[str, Any] = {"session": str(target.session), "present": False}
    try:
        record = session_ops.load_session(target.session)
    except session_ops.SessionError as exc:
        info["error"] = ascii_safe(str(exc))
        if args.json:
            _emit(info, True)
        else:
            print(f"no usable session: {info['error']}")
        return EXIT_AUTH

    info.update({"present": True, "mode": "0600", **record.public()})
    if record.is_expired():
        info["note"] = "the cookie's own expiry has passed; re-run login"

    if args.check:
        result = api.verify(record)
        info["auth_test"] = {
            "ok": result.ok,
            "error": result.error,
            "team": result.data.get("team"),
            "team_id": result.data.get("team_id"),
            "user": result.data.get("user"),
            "user_id": result.data.get("user_id"),
            "url": result.data.get("url"),
        }
        if result.ok and not record.team_id:
            record.team_id = str(result.data.get("team_id") or "")
            record.team_name = str(result.data.get("team") or "")
            session_ops.write_session(record, target.session)

    if args.json:
        _emit(info, True)
        return EXIT_OK if info.get("auth_test", {}).get("ok", True) else EXIT_AUTH

    print(f"session    : {target.session}")
    print(f"workspace  : {info.get('team_name') or '(unknown)'}  {info.get('team_id') or ''}")
    print(f"token      : {info.get('token')}")
    cookie = info.get("cookie") or {}
    print(f"cookie     : d on .slack.com, {cookie.get('length')} chars")
    if info.get("cookie_expires"):
        days = info.get("cookie_expires_in_days")
        print(f"expires    : {_iso(info['cookie_expires'])}  ({days:.0f} days)" if days else "")
    print(f"captured   : {_iso(info.get('captured_at'))} via {info.get('source')}")
    check = info.get("auth_test")
    if isinstance(check, dict):
        if check["ok"]:
            print(f"auth.test  : ok — {check.get('team')} as {check.get('user')}")
            return EXIT_OK
        print(f"auth.test  : FAILED — {check.get('error')}")
        # The cookie may look unexpired and still be revoked; a failed check is exactly
        # the "re-run login" case, and the exit code is how a script sees it.
        return EXIT_AUTH
    return EXIT_OK


def cmd_logout(args: argparse.Namespace) -> int:
    """Delete the local session, and ask slackdump to forget the workspace too.

    Both halves have to go, or `logout` is a lie: slackdump keeps its own encrypted copy
    of the same token and cookie, which is why `channels`/`read`/`search` keep working
    after only `session.json` is deleted (verified). `slackdump workspace del -y` forgets
    it; `--keep-slackdump` skips that for someone who wants to keep reading.
    """
    target = _paths(args)
    removed = False
    if target.session.exists():
        target.session.unlink()
        removed = True

    slackdump_detail = "skipped"
    if not args.keep_slackdump:
        try:
            slackdump = slackdump_binary()
        except ConfigError as exc:
            slackdump_detail = f"skipped: {exc}"
        else:
            completed = session_ops._run([str(slackdump), "workspace", "del", "-y"])
            slackdump_detail = "ok" if completed.returncode == 0 else "failed"

    if args.json:
        _emit(
            {
                "session": str(target.session),
                "removed": removed,
                "slackdump": slackdump_detail,
            },
            True,
        )
    else:
        print(f"{'removed' if removed else 'nothing to remove at'} {target.session}")
        print(f"slackdump : {slackdump_detail}")
    return EXIT_OK


# ---------------------------------------------------------------------------- reads


def cmd_channels(args: argparse.Namespace) -> int:
    """List channels, groups, and DMs (slackdump's view, cached for 20 minutes)."""
    listing = reads.channels(fresh=args.fresh)
    rows: list[dict[str, Any]] = []
    for channel in listing.raw:
        if args.type == "channels" and (channel.get("is_im") or channel.get("is_mpim")):
            continue
        if args.type == "dms" and not (channel.get("is_im") or channel.get("is_mpim")):
            continue
        if not args.include_archived and channel.get("is_archived"):
            continue
        rows.append(
            {
                "id": channel.get("id"),
                "label": listing.describe(channel),
                "name": channel.get("name") or "",
                "kind": (
                    "im"
                    if channel.get("is_im")
                    else "mpim"
                    if channel.get("is_mpim")
                    else "private"
                    if channel.get("is_private")
                    else "public"
                ),
                "user": channel.get("user") or "",
                "members": channel.get("num_members"),
                "archived": bool(channel.get("is_archived")),
                "topic": (channel.get("topic") or {}).get("value")
                if isinstance(channel.get("topic"), dict)
                else "",
            }
        )
    rows.sort(key=lambda r: (r["kind"], str(r["name"])))
    if args.json:
        _emit({"count": len(rows), "channels": rows}, True)
        return EXIT_OK
    for row in rows:
        members = f"{row['members']:>4} members" if row["members"] is not None else ""
        archived = " [archived]" if row["archived"] else ""
        print(f"{row['id']}  {row['label']:<34} {members}{archived}".rstrip())
    print(f"({len(rows)} conversations in cache; --fresh to refetch)")
    return EXIT_OK


def cmd_read(args: argparse.Namespace) -> int:
    """Read one conversation (or one thread) chronologically."""
    channel_id = reads.resolve_channel(args.channel)
    target = f"{channel_id}:{args.thread}" if args.thread else channel_id
    messages = reads.dump(target, limit=args.limit, timeout=args.timeout)
    if args.json:
        _emit(
            {
                "channel": channel_id,
                "thread": args.thread or None,
                "count": len(messages),
                "messages": [m.to_dict() for m in messages],
            },
            True,
        )
        return EXIT_OK
    if not messages:
        print(f"no messages in {target}")
        return EXIT_OK
    print(f"{target}  ({len(messages)} messages)")
    for message in messages:
        stamp = _iso_ts(message.ts)
        thread = " (thread)" if message.thread_ts and message.thread_ts != message.ts else ""
        text = message.text.replace("\n", "\n    ")
        print(f"{stamp} {message.user or '-':<12}{thread} {text}")
    return EXIT_OK


def _iso_ts(ts: str) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return ts or "?"


def cmd_users(args: argparse.Namespace) -> int:
    rows = [
        {
            "id": u.get("id"),
            "name": u.get("name"),
            "real_name": u.get("real_name") or "",
            "bot": bool(u.get("is_bot")),
            "deleted": bool(u.get("deleted")),
        }
        for u in reads.users()
    ]
    if args.json:
        _emit({"count": len(rows), "users": rows}, True)
        return EXIT_OK
    for row in rows:
        flags = ",".join(
            f for f in ("bot" if row["bot"] else "", "deleted" if row["deleted"] else "") if f
        )
        print(f"{row['id']}  {row['name']:<24} {row['real_name']}{f'  [{flags}]' if flags else ''}")
    return EXIT_OK


def cmd_search(args: argparse.Namespace) -> int:
    """Search messages through slackdump, or directly with `--via api`."""
    record = _load(args)
    hits, total = reads.search(
        record,
        args.query,
        count=args.limit,
        sort=args.sort,
        via=args.via,
        timeout=args.timeout,
    )
    if args.limit and args.via == "api":
        hits = hits[: args.limit]
    if args.json:
        _emit(
            {
                "query": args.query,
                "via": args.via,
                "total": total,
                "results": [h.to_dict() for h in hits],
            },
            True,
        )
        return EXIT_OK
    if not hits:
        print(f"no matches for {args.query!r} (via {args.via})")
        return EXIT_OK
    print(f"{total} match(es) for {args.query!r} (via {args.via})")
    for hit in hits:
        where = f"#{hit.channel_name}" if hit.channel_name else hit.channel_id
        print(f"{_iso_ts(hit.ts)} {where:<20} {hit.text[:160]}")
    return EXIT_OK


# ----------------------------------------------------------------------- passthrough


def cmd_api(args: argparse.Namespace) -> int:
    """Forward `method` to Slack verbatim, and print Slack's answer verbatim.

    This is the primary verb. Anything with a dot in the name that is not a reserved
    local verb lands here — `chat.postMessage`, `conversations.replies`,
    `files.upload`, `usergroups.list`, `admin.users.list`, `client.counts`, all of it.

    Output is always JSON, because an API response is structured by definition. The body
    goes to stdout; a failure's error code goes to *stderr* as well, so `2>/dev/null`
    still leaves a usable body while a human still sees the reason. `--raw` keeps the
    `{ok, method, data}` envelope instead of unwrapping `data`.
    """
    # Validate the payload first: a JSON typo must not be reported as a session problem.
    try:
        payload = api.parse_payload(args.payload)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    record = _load(args)
    result = api.call(
        record,
        args.method,
        payload,
        http_verb=args.http_verb,
        base_url=api_base(),
    )

    if not result.ok:
        print(ascii_safe(f"Slack error: {result.error}"), file=sys.stderr)
        for line in result.detail:
            print(ascii_safe(f"  {line}"), file=sys.stderr)
    if args.raw:
        _emit(result.to_dict(), True)
    else:
        # `ok` is the exit code's job; keep the body clean for piping through jq.
        body = {k: v for k, v in result.data.items() if k != "ok"}
        if not result.ok:
            body.setdefault("error", result.error)
        _emit(body, True)
    return EXIT_OK if result.ok else EXIT_ERROR


def cmd_help(args: argparse.Namespace) -> int:
    print(__doc__ or "")
    return EXIT_OK


# ------------------------------------------------------------------------------ parser


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--raw", action="store_true", help="print the raw API envelope")
    parser.add_argument("--verbose", action="store_true", help="show tracebacks")
    parser.add_argument("--state", help="state directory (default: ~/.local/share/mycord-slack)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mycord-slack",
        description=(
            "Slack for agents with no app registration: session capture over CDP, reads "
            "through slackdump, and a passthrough to any Slack API method."
        ),
        epilog=(
            "Any Slack API method works as a verb: `mycord-slack chat.postMessage "
            '--json \'{"channel":"C0123","text":"hi"}\'`. '
            "Run `mycord-slack session --check` first; if it fails, run `mycord-slack login`."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<method|verb>")

    login = sub.add_parser(
        "login", help="capture xoxc + d from a browser and import into slackdump"
    )
    _add_output_flags(login)
    login.add_argument(
        "--profile", help="Chrome profile path (e.g. ~/.config/google-chrome/Default)"
    )
    login.add_argument("--chrome", help="browser binary")
    login.add_argument("--team", help="team id to select when the browser has several workspaces")
    login.add_argument("--headed", action="store_true", help="run the browser with a window")
    login.add_argument("--no-copy-profile", action="store_true", help="drive the profile in place")
    login.add_argument("--timeout", type=float, default=60.0)
    login.set_defaults(func=cmd_login)

    sess = sub.add_parser("session", help="show session status without printing secrets")
    _add_output_flags(sess)
    sess.add_argument(
        "--check", action="store_true", help="also call auth.test, the only real validity check"
    )
    sess.set_defaults(func=cmd_session)

    logout = sub.add_parser("logout", help="delete the local session and forget it in slackdump")
    _add_output_flags(logout)
    logout.add_argument(
        "--keep-slackdump",
        action="store_true",
        help="leave slackdump's own copy of the credentials in place",
    )
    logout.set_defaults(func=cmd_logout)

    chans = sub.add_parser("channels", help="list channels, groups, and DMs (via slackdump)")
    _add_output_flags(chans)
    chans.add_argument("--type", choices=("all", "channels", "dms"), default="all")
    chans.add_argument("--fresh", action="store_true", help="ignore the 20-minute channel cache")
    chans.add_argument("--include-archived", action="store_true")
    chans.set_defaults(func=cmd_channels)

    read = sub.add_parser("read", help="read a conversation or thread chronologically")
    _add_output_flags(read)
    read.add_argument("channel", help="#name, name, channel id, or a Slack URL")
    read.add_argument("--thread", help="thread parent ts (reads only that thread)")
    read.add_argument("--limit", type=int, default=50, help="most recent N messages (default 50)")
    read.add_argument("--timeout", type=float, default=reads.DEFAULT_TIMEOUT)
    read.set_defaults(func=cmd_read)

    users = sub.add_parser("users", help="list workspace users (via slackdump)")
    _add_output_flags(users)
    users.set_defaults(func=cmd_users)

    search = sub.add_parser("search", help="search messages")
    _add_output_flags(search)
    search.add_argument("query")
    search.add_argument(
        "--via",
        choices=("slackdump", "api"),
        default="slackdump",
        help="slackdump paginates; api returns one page of Slack's own index",
    )
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--sort", choices=("", "score", "timestamp"), default="")
    search.add_argument("--timeout", type=float, default=reads.DEFAULT_TIMEOUT)
    search.set_defaults(func=cmd_search)

    help_cmd = sub.add_parser("help", help="show the passthrough contract")
    _add_output_flags(help_cmd)
    help_cmd.set_defaults(func=cmd_help)

    return parser


def _looks_like_method(name: str) -> bool:
    """Slack methods are dotted lower-case namespaces: `chat.postMessage`."""
    if name in RESERVED:
        return False
    head, _, tail = name.partition(".")
    return bool(head and tail) and head[0].isalpha()


def _first_positional(argv: list[str]) -> str | None:
    """The first bare word, skipping the leading global flags argparse would accept."""
    takes_value = {"--state", "--profile", "--chrome", "--team", "--timeout"}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in takes_value:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def _passthrough_parser(method: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"mycord-slack {method}",
        description=(
            f"Call the Slack API method {method}. The payload is a JSON object of that "
            "method's arguments, exactly as Slack documents them. Slack's error codes are "
            "returned verbatim."
        ),
        epilog=(
            'example: mycord-slack chat.postMessage --json \'{"channel":"C0123","text":"hi"}\''
        ),
    )
    # No `--json` output flag here: on a passthrough, `--json` is the *payload*, which is
    # the whole interface. Output is always JSON.
    parser.add_argument("--state", help="state directory (default: ~/.local/share/mycord-slack)")
    parser.add_argument("--raw", action="store_true", help="print the {ok, method, data} envelope")
    parser.add_argument("--verbose", action="store_true", help="show tracebacks")
    parser.add_argument("--http-verb", choices=("POST", "GET"), default="POST")
    parser.add_argument(
        "--json",
        dest="payload",
        default=None,
        metavar="PAYLOAD",
        help="the method's arguments as a JSON object (default: {})",
    )
    parser.add_argument(
        "payload_positional",
        nargs="?",
        default=None,
        metavar="PAYLOAD",
        help="the payload may also be given positionally",
    )
    parser.set_defaults(func=cmd_api, method=method)
    return parser


def _dispatch(argv: list[str]) -> int:
    """Route `a.b` to the passthrough; everything else to a named verb.

    The route is decided *before* argparse runs, because argparse cannot accept a
    subcommand it has never heard of — and hearing about every Slack method is exactly
    what this CLI refuses to do.
    """
    first = _first_positional(argv)
    if first is not None and _looks_like_method(first):
        # The method word is already known; drop it so it cannot be mistaken for the
        # payload positional (argparse sees every element of a passed-in argv).
        rest = list(argv)
        rest.remove(first)
        try:
            args = _passthrough_parser(first).parse_args(rest)
        except SystemExit as exc:
            return int(exc.code or EXIT_USAGE)
        if args.payload is not None and args.payload_positional is not None:
            print("error: give the payload once, as --json or positionally", file=sys.stderr)
            return EXIT_USAGE
        args.payload = args.payload or args.payload_positional or "{}"
        return int(args.func(args))

    parser = build_parser()
    try:
        known = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage
        return int(exc.code or EXIT_USAGE)
    if known.command is None:
        parser.print_help()
        return EXIT_USAGE
    return int(known.func(known))


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(args_list)
    except session_ops.SessionError as exc:
        print(f"error: {ascii_safe(str(exc))}", file=sys.stderr)
        return EXIT_AUTH
    except (reads.ReadError, ConfigError) as exc:
        print(f"error: {ascii_safe(str(exc))}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
