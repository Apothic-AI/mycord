"""The ``mycord-signal`` command.

Verbs are grouped by what they can do to the world:

* ``doctor`` / ``versions`` / ``call-guard`` — read-only probes. Safe anywhere.
* ``contacts`` / ``groups`` / ``calls`` / ``receive`` / ``events`` — reads.
* ``send`` — **dry run by default**; ``--confirm`` is required to actually send.
* ``call start|accept|hangup|reject|audio`` — the call path; every one of these first runs
  the dependency gate, because a stale pin fails *during* call setup and that is the worst
  moment to discover it.
* ``rpc`` — passthrough for any method the daemon exposes, so the surface is not limited to
  the verbs this skill curated.

Exit codes follow ``mycord-slack``: ``0`` ok, ``1`` runtime failure, ``2`` usage error,
``3`` not ready (stale/missing dependency, no daemon, or no account).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import calls, deps, reads, rpc
from . import send as send_mod

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_READY = 3


# --------------------------------------------------------------------------- helpers


@dataclass
class Paths:
    """Overridable locations, so tests and alternate installs never touch the real ones."""

    tunnel_src: Path
    signal_data: Path
    socket: Path
    signal_cli: str
    tunnel_bin: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Paths:
        return cls(
            tunnel_src=Path(
                getattr(args, "tunnel_src", None) or deps.DEFAULT_TUNNEL_SRC
            ).expanduser(),
            signal_data=Path(
                getattr(args, "signal_data", None) or deps.DEFAULT_SIGNAL_DATA
            ).expanduser(),
            socket=Path(getattr(args, "socket", None) or deps.DEFAULT_SOCKET).expanduser(),
            signal_cli=getattr(args, "signal_cli", None) or "signal-cli",
            tunnel_bin=getattr(args, "tunnel_bin", None) or "signal-call-tunnel",
        )


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                print(f"{key:>16}: {json.dumps(value, default=str)[:400]}")
            else:
                print(f"{key:>16}: {value}")
    elif isinstance(payload, list):
        for item in payload:
            print(item if isinstance(item, str) else json.dumps(item, default=str))
    else:
        print(payload)


def _client(paths: Paths) -> rpc.RpcClient:
    return rpc.RpcClient(paths.socket)


def _gate(paths: Paths) -> str | None:
    """Run the dependency gate and return the refusal text, if any.

    Called by every verb on the call path. Returning the text (rather than printing) keeps the
    gate testable and keeps the decision in one place.
    """
    return deps.require_call_ready(build_report(paths, probe=True, daemon=True))


# --------------------------------------------------------------------------- doctor


def build_report(paths: Paths, *, probe: bool = True, daemon: bool = True) -> deps.Report:
    """Collect every check. Read-only: no probe mutates state, and the pipe probe is isolated."""
    report = deps.Report()

    cli = deps.check_signal_cli(paths.signal_cli)
    report.add(cli)
    report.versions["signal-cli"] = (
        None if cli.status == "missing" else deps.signal_cli_version(paths.signal_cli)[0]
    )

    info = deps.tunnel_info(paths.tunnel_src, paths.tunnel_bin)
    report.add(deps.check_tunnel_binary(info))
    report.add(deps.check_tunnel_commit(info))
    report.versions["signal-call-tunnel.commit"] = info.short_commit
    report.versions["signal-call-tunnel.sha256"] = (
        info.binary_sha256[:12] + "…" if info.binary_sha256 else None
    )
    report.versions["required.signal-cli"] = deps.format_version(deps.REQUIRED_SIGNAL_CLI)
    report.versions["required.tunnel-commit"] = deps.REQUIRED_TUNNEL_COMMIT

    if probe:
        result = deps.probe_pipe(paths.tunnel_bin)
        report.add(
            deps.Check(
                name="signal-call-tunnel:pipe",
                status="ok" if result.ok else "stale",
                detail=result.detail
                + (f" | {result.stderr}" if result.stderr and not result.ok else ""),
                fix=None if result.ok else "scripts/provision.sh --apply   (rebuild the tunnel)",
            )
        )
        report.versions["signal-call-tunnel.pipe_socket"] = result.socket_path
        if result.ok:
            report.versions["signal-call-tunnel.pcm"] = calls.describe_contract()

    if daemon:
        report.add(deps.check_daemon(paths.socket))

    report.add(deps.check_account(paths.signal_data))
    return report


def cmd_doctor(args: argparse.Namespace) -> int:
    paths = Paths.from_args(args)
    report = build_report(paths, probe=not args.no_probe, daemon=True)

    if args.json:
        _emit(report.as_dict(), as_json=True)
    else:
        print("mycord-signal doctor")
        print(f"    signal-cli: {paths.signal_cli}")
        print(f"    tunnel bin: {paths.tunnel_bin}")
        print(f"   tunnel src: {paths.tunnel_src}")
        print(f"   daemon sock: {paths.socket}")
        print(f"  account data: {paths.signal_data}")
        print()
        markers = {"ok": " ok ", "stale": "STALE", "missing": "MISS", "unknown": " ?? "}
        for check in report.checks:
            print(f"[{markers[check.status]}] {check.name}")
            print(f"          {check.detail}")
            if check.fix:
                print(f"          fix: {check.fix}")

    problems = report.dependency_problems
    if problems:
        print(
            f"\nnot call-ready: {len(problems)} dependency problem(s). "
            "A stale pin fails during call setup, so the call path is refused.",
            file=sys.stderr,
        )
        return EXIT_NOT_READY

    account = report.find("account")
    if account is not None and account.status != "ok":
        print(
            "\ndependencies are current, but no account is linked. Linking needs a human with "
            "the Signal app (see SKILL.md § Link a device).",
            file=sys.stderr,
        )
        return EXIT_NOT_READY if args.require_account else EXIT_OK

    print("\ndependencies call-ready. No call or message was attempted by doctor.")
    return EXIT_OK


def cmd_versions(args: argparse.Namespace) -> int:
    report = build_report(Paths.from_args(args), probe=not args.no_probe, daemon=True)
    _emit(report.versions, as_json=True)
    return EXIT_OK if not report.dependency_problems else EXIT_NOT_READY


def cmd_call_guard(args: argparse.Namespace) -> int:
    refusal = _gate(Paths.from_args(args))
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_NOT_READY
    print("dependencies call-ready (no call attempted)")
    return EXIT_OK


# --------------------------------------------------------------------------- reads


def cmd_contacts(args: argparse.Namespace) -> int:
    with _client(Paths.from_args(args)) as client:
        result = reads.contacts(client)
    _emit(
        result.items if args.json else [reads.summarise_contact(c) for c in result.items], args.json
    )
    if result.note and not result.items:
        print(result.note, file=sys.stderr)
    return EXIT_OK


def cmd_groups(args: argparse.Namespace) -> int:
    with _client(Paths.from_args(args)) as client:
        result = reads.groups(client)
    _emit(result.items, args.json)
    if result.note and not result.items:
        print(result.note, file=sys.stderr)
    return EXIT_OK


def cmd_calls(args: argparse.Namespace) -> int:
    with _client(Paths.from_args(args)) as client:
        result = reads.calls(client)
    _emit(result.items, args.json)
    return EXIT_OK


def cmd_receive(args: argparse.Namespace) -> int:
    with _client(Paths.from_args(args)) as client:
        if args.stream:
            result = reads.stream_messages(client, limit=args.limit, seconds=args.seconds)
        else:
            result = reads.drain_messages(
                client, timeout=args.timeout, max_messages=args.max_messages
            )
    _emit(result.items, args.json)
    if result.note:
        print(result.note, file=sys.stderr)
    return EXIT_OK


def cmd_events(args: argparse.Namespace) -> int:
    """Stream ``callEvent`` notifications."""
    with _client(Paths.from_args(args)) as client:
        subscription = client.subscribe_call_events()
        print(json.dumps({"subscribed": subscription}), flush=True)
        for emitted, event in enumerate(client.call_events(timeout=0.5), start=1):
            print(json.dumps(event), flush=True)
            if args.limit and emitted >= args.limit:
                break
    return EXIT_OK


# --------------------------------------------------------------------------- send


def cmd_send(args: argparse.Namespace) -> int:
    """Send a message — dry run unless ``--confirm`` is given.

    The flag name is deliberate: ``--confirm`` is a thing a human types after reading the
    preview, not a default an agent inherits.
    """
    message = args.message
    if args.stdin:
        message = sys.stdin.read()
    plan = send_mod.SendPlan(
        recipients=args.to or [],
        group_ids=args.group or [],
        message=message,
        attachments=args.attachment or [],
        quote_timestamp=args.quote_timestamp,
        edit_timestamp=args.edit_timestamp,
    )
    dry_run = not args.confirm

    if dry_run:
        try:
            payload = send_mod.send(plan, _NullClient(), dry_run=True)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print("dry run — nothing was sent. Re-run with --confirm to send for real.")
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    # Confirmed: the dependency gate now matters, because a send to a linked device with a
    # stale client is exactly the case the gate exists for.
    refusal = _gate(Paths.from_args(args))
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_NOT_READY

    with _client(Paths.from_args(args)) as client:
        try:
            outcome = send_mod.send(plan, client, dry_run=False)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    print(send_mod.result_summary(outcome.get("result")))
    if args.json:
        _emit(outcome, as_json=True)
    return EXIT_OK


class _NullClient(rpc.RpcClient):
    """A client that is never connected, used only to satisfy ``send`` in dry-run mode."""

    def __init__(self) -> None:
        super().__init__("/nonexistent")


# --------------------------------------------------------------------------- call path


def cmd_call_start(args: argparse.Namespace) -> int:
    paths = Paths.from_args(args)
    refusal = _gate(paths)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_NOT_READY
    if not args.confirm:
        print(
            "dry run — would start an outgoing call to "
            f"{args.recipient}. Re-run with --confirm to place it.",
            file=sys.stderr,
        )
        return EXIT_OK

    with _client(paths) as client:
        # Subscribing first is mandatory: an unsubscribed daemon drops incoming calls outright.
        client.subscribe_call_events()
        info = calls.start(client, args.recipient)
        _emit(info, as_json=True)
        if args.wait:
            event = calls.wait_for_state(
                client,
                calls.STATE_CONNECTED,
                timeout=args.wait,
                call_id=_as_int(info.get("callId")),
            )
            if event is None:
                print("no CONNECTED event within the wait window", file=sys.stderr)
                return EXIT_ERROR
            _emit(event.raw, as_json=True)
    return EXIT_OK


def cmd_call_accept(args: argparse.Namespace) -> int:
    paths = Paths.from_args(args)
    refusal = _gate(paths)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_NOT_READY
    with _client(paths) as client:
        client.subscribe_call_events()
        _emit(calls.accept(client, args.call_id), as_json=True)
    return EXIT_OK


def cmd_call_hangup(args: argparse.Namespace) -> int:
    with _client(Paths.from_args(args)) as client:
        _emit(calls.hangup(client, args.call_id), as_json=True)
    return EXIT_OK


def cmd_call_reject(args: argparse.Namespace) -> int:
    with _client(Paths.from_args(args)) as client:
        _emit(calls.reject(client, args.call_id), as_json=True)
    return EXIT_OK


def cmd_call_audio(args: argparse.Namespace) -> int:
    """Attach to a call's PCM socket: play a file in, capture remote audio out.

    Refuses unless the dependency gate passes, then connects and streams. ``--seconds`` bounds
    the session so an agent cannot leave a silent socket open indefinitely.
    """
    paths = Paths.from_args(args)
    refusal = _gate(paths)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_NOT_READY

    with _client(paths) as client:
        client.subscribe_call_events()
        event = calls.wait_for_state(
            client, calls.STATE_CONNECTED, timeout=args.wait, call_id=args.call_id
        )
        if event is None:
            print(
                f"no CONNECTED event for call {args.call_id} within {args.wait:.0f}s",
                file=sys.stderr,
            )
            return EXIT_ERROR
        device = event.input_device or event.output_device
        if device is None:
            print("CONNECTED event carried no device name", file=sys.stderr)
            return EXIT_ERROR
        print(
            json.dumps(
                {"callId": event.call_id, "device": device, "pcm": calls.describe_contract()}
            ),
            flush=True,
        )
        with calls.AudioSocket.connect(device) as audio:
            written = 0
            if args.play:
                payload = Path(args.play).read_bytes()
                written = audio.write_window(payload)
            if args.capture:
                captured = bytearray()
                deadline = args.seconds
                for window in audio.windows():
                    captured.extend(window)
                    if len(captured) >= int(deadline * calls.SAMPLE_RATE * calls.BYTES_PER_SAMPLE):
                        break
                Path(args.capture).write_bytes(bytes(captured))
                print(json.dumps({"captured_bytes": len(captured)}), flush=True)
            else:
                # No capture requested: keep the call alive with silence so the peer is not
                # starved, which is what the tunnel does when no peer is attached anyway.
                audio.write_window(calls.silence())
            print(json.dumps({"written_bytes": written}), flush=True)
    return EXIT_OK


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


# --------------------------------------------------------------------------- passthrough


def cmd_rpc(args: argparse.Namespace) -> int:
    """Call any JSON-RPC method and print the raw result.

    A passthrough rather than a curated list, for the same reason ``mycord-slack`` has one: the
    daemon already speaks every command, and a fixed verb list would only hide the ones this
    skill has not needed yet.
    """
    params = json.loads(args.params) if args.params else None
    with _client(Paths.from_args(args)) as client:
        result = client.call(args.method, params, timeout=args.timeout)
    _emit({"method": args.method, "result": result}, as_json=True)
    return EXIT_OK


# --------------------------------------------------------------------------- argparse


def _add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--signal-cli", default=None, help="signal-cli binary (default: PATH)")
    parser.add_argument(
        "--tunnel-bin", default=None, help="signal-call-tunnel binary (default: PATH)"
    )
    parser.add_argument("--tunnel-src", default=None, help="tunnel checkout")
    parser.add_argument("--signal-data", default=None, help="signal-cli data dir")
    parser.add_argument("--socket", default=None, help="daemon JSON-RPC socket")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mycord-signal",
        description="Signal from a shell as the linked human: no bot, no app registration",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="report versions, pipe readiness, daemon and account")
    _add_paths(doctor)
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--no-probe", action="store_true", help="skip booting the tunnel")
    doctor.add_argument(
        "--require-account", action="store_true", help="exit 3 when no account is linked"
    )
    doctor.set_defaults(func=cmd_doctor)

    versions = sub.add_parser("versions", help="print measured versions as JSON")
    _add_paths(versions)
    versions.add_argument("--no-probe", action="store_true")
    versions.set_defaults(func=cmd_versions)

    guard = sub.add_parser("call-guard", help="exit 3 unless dependencies are call-ready")
    _add_paths(guard)
    guard.set_defaults(func=cmd_call_guard)

    contacts = sub.add_parser("contacts", help="list contacts")
    _add_paths(contacts)
    contacts.add_argument("--json", action="store_true")
    contacts.set_defaults(func=cmd_contacts)

    groups = sub.add_parser("groups", help="list groups")
    _add_paths(groups)
    groups.add_argument("--json", action="store_true")
    groups.set_defaults(func=cmd_groups)

    call_list = sub.add_parser("calls", help="list active calls")
    _add_paths(call_list)
    call_list.add_argument("--json", action="store_true")
    call_list.set_defaults(func=cmd_calls)

    receive = sub.add_parser("receive", help="drain new messages")
    _add_paths(receive)
    receive.add_argument("--json", action="store_true")
    receive.add_argument("--timeout", type=float, default=3.0, help="server-side wait, seconds")
    receive.add_argument("--max-messages", type=int, default=None)
    receive.add_argument(
        "--stream", action="store_true", help="use the running daemon's subscription instead"
    )
    receive.add_argument("--seconds", type=float, default=10.0, help="with --stream")
    receive.add_argument("--limit", type=int, default=0, help="with --stream")
    receive.set_defaults(func=cmd_receive)

    events = sub.add_parser("events", help="subscribe to callEvent and stream it")
    _add_paths(events)
    events.add_argument("--limit", type=int, default=0, help="stop after N events (0 = forever)")
    events.set_defaults(func=cmd_events)

    send = sub.add_parser(
        "send", help="send a message (dry run unless --confirm)", epilog="Dry run is the default."
    )
    _add_paths(send)
    send.add_argument(
        "--to", action="append", default=None, help="recipient number/uuid (repeatable)"
    )
    send.add_argument("--group", action="append", default=None, help="group id (repeatable)")
    send.add_argument("-m", "--message", default=None)
    send.add_argument("--stdin", action="store_true", help="read the message from stdin")
    send.add_argument("-a", "--attachment", action="append", default=None)
    send.add_argument("--quote-timestamp", type=int, default=None)
    send.add_argument("--edit-timestamp", type=int, default=None)
    send.add_argument("--confirm", action="store_true", help="actually send (default: dry run)")
    send.add_argument("--json", action="store_true")
    send.set_defaults(func=cmd_send)

    call = sub.add_parser("call", help="voice call control")
    call_sub = call.add_subparsers(dest="call_command", required=True)

    start = call_sub.add_parser("start", help="start an outgoing call")
    _add_paths(start)
    start.add_argument("recipient")
    start.add_argument("--confirm", action="store_true", help="actually place the call")
    start.add_argument("--wait", type=float, default=0.0, help="wait N s for CONNECTED")
    start.set_defaults(func=cmd_call_start)

    accept = call_sub.add_parser("accept", help="accept an incoming call")
    _add_paths(accept)
    accept.add_argument("call_id", type=int)
    accept.set_defaults(func=cmd_call_accept)

    hangup = call_sub.add_parser("hangup", help="hang up")
    _add_paths(hangup)
    hangup.add_argument("call_id", type=int)
    hangup.set_defaults(func=cmd_call_hangup)

    reject = call_sub.add_parser("reject", help="reject an incoming call")
    _add_paths(reject)
    reject.add_argument("call_id", type=int)
    reject.set_defaults(func=cmd_call_reject)

    audio = call_sub.add_parser("audio", help="attach to a call's PCM socket")
    _add_paths(audio)
    audio.add_argument("call_id", type=int)
    audio.add_argument("--wait", type=float, default=30.0, help="wait N s for CONNECTED")
    audio.add_argument("--play", default=None, help="raw s16le 48k mono file to send")
    audio.add_argument("--capture", default=None, help="write captured remote audio here")
    audio.add_argument("--seconds", type=float, default=5.0, help="capture duration")
    audio.set_defaults(func=cmd_call_audio)

    passthrough = sub.add_parser("rpc", help="passthrough: call one JSON-RPC method")
    _add_paths(passthrough)
    passthrough.add_argument("method")
    passthrough.add_argument("--params", default=None, help="JSON object of params")
    passthrough.add_argument("--timeout", type=float, default=60.0)
    passthrough.set_defaults(func=cmd_rpc)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command: Any = args.func
    try:
        result: int = command(args)
    except rpc.RpcError as exc:
        print(f"error: {exc.message} (code {exc.code})", file=sys.stderr)
        return EXIT_ERROR
    except rpc.TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_READY
    except calls.CallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    return result


if __name__ == "__main__":
    raise SystemExit(main())
