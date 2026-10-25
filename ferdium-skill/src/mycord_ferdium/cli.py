"""Command line for mycord-ferdium.

The exit codes are part of the contract, because the first two states need a human
and a dependent skill must be able to tell them apart from a crash:

===== ==================================================================
0     success
1     runtime error (CDP, launch, selector)
2     Ferdium is not installed
3     Ferdium is installed but has no services configured — the caller
      must launch headed so a human can add an account
===== ==================================================================
"""

from __future__ import annotations

import argparse
import json
import sys

from . import launcher
from .cdp import Browser, CdpError
from .launcher import FerdiumError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_INSTALLED = 2
EXIT_NOT_CONFIGURED = 3


def _out(payload: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    elif isinstance(payload, list):
        for item in payload:
            print(item)
    else:
        print(payload)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report install/configuration state without launching anything."""
    is_installed = launcher.installed()
    found = launcher.services()
    report = {
        "installed": is_installed,
        "appId": launcher.APP_ID,
        "configRoot": str(launcher.CONFIG_ROOT),
        "serviceCount": len(found),
        "services": [
            {"id": s.id, "partition": s.partition, "hosts": s.indexeddb_hosts()} for s in found
        ],
        "ready": is_installed and bool(found),
    }
    if args.json:
        _out(report, True)
    else:
        print(f"installed    : {'yes' if is_installed else 'NO'}")
        print(f"config root  : {report['configRoot']}")
        print(f"services     : {report['serviceCount']}")
        for svc in found:
            hosts = ", ".join(svc.indexeddb_hosts()) or "(no known host)"
            print(f"  - {svc.id[:8]}  {hosts}")
        if not is_installed:
            print("\nFerdium is not installed:")
            print(f"  flatpak install flathub {launcher.APP_ID}")
        elif not found:
            print("\nNo services are configured yet. Launch headed and add an account:")
            print("  mycord-ferdium launch --headed")
    if not is_installed:
        return EXIT_NOT_INSTALLED
    return EXIT_OK if found else EXIT_NOT_CONFIGURED


def cmd_launch(args: argparse.Namespace) -> int:
    """Launch Ferdium for agent use, refusing headless when nothing is configured."""
    if not launcher.installed():
        print(
            f"Ferdium is not installed. Install it first:\n"
            f"  flatpak install flathub {launcher.APP_ID}",
            file=sys.stderr,
        )
        return EXIT_NOT_INSTALLED

    configured = launcher.services()
    # Headless is the DEFAULT; --headed opts into a visible window. Computing this as
    # `args.headless and ...` made headed the default, which the tests caught.
    headless = not args.headed
    if not configured:
        # A human has to add the account in the app window; headless would hide it.
        if headless:
            print(
                "No services are configured, so a human must add one first.\n"
                "Re-run with --headed and add your account in the window that opens.",
                file=sys.stderr,
            )
            return EXIT_NOT_CONFIGURED
        print(
            "Ferdium has no configured services yet. The window is opening — add your "
            "account there. The debug port stays enabled, so no restart is needed "
            "afterwards.",
            file=sys.stderr,
        )

    try:
        running = launcher.launch(port=args.port, headless=headless, wait=args.wait)
    except FerdiumError as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    info = {"port": running.port, "pid": running.proc.pid, "headless": headless}
    if args.detach:
        _out(info if args.json else f"Ferdium running on CDP port {running.port}", args.json)
        return EXIT_OK

    try:
        _out(
            info if args.json else f"Ferdium running on CDP port {running.port} (ctrl-c to stop)",
            args.json,
        )
        running.proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        running.stop()
    return EXIT_OK


def cmd_services(args: argparse.Namespace) -> int:
    found = launcher.services()
    payload = [{"id": s.id, "partition": s.partition, "hosts": s.indexeddb_hosts()} for s in found]
    if args.json:
        _out(payload, True)
    else:
        if not payload:
            print("no configured services")
            return EXIT_NOT_CONFIGURED
        for svc in payload:
            hosts = ", ".join(svc["hosts"]) or "(no known host)"
            print(f"{svc['id'][:8]}  {hosts}")
    return EXIT_OK


def cmd_targets(args: argparse.Namespace) -> int:
    """List live CDP targets, including webviews (which /json/list hides)."""
    try:
        browser = Browser(args.port)
    except CdpError as exc:
        print(
            f"cannot reach Ferdium on port {args.port}: {exc}\n"
            "Start it with `mycord-ferdium launch` (the debug port only exists at launch).",
            file=sys.stderr,
        )
        return EXIT_ERROR
    try:
        targets = browser.targets()
    finally:
        browser.close()
    payload = [
        {"type": t.type, "host": t.host, "title": t.title, "id": t.target_id} for t in targets
    ]
    if args.json:
        _out(payload, True)
    else:
        if not payload:
            print("no targets (is the app awake?)")
        for t in payload:
            print(f"[{t['type']:14}] {t['host'] or '-':28} {t['title'][:40]}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mycord-ferdium",
        description="Drive a Ferdium install over CDP (headless by default).",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    doctor = sub.add_parser("doctor", parents=[common], help="report install + configuration state")
    doctor.set_defaults(func=cmd_doctor)

    launch = sub.add_parser("launch", parents=[common], help="start Ferdium with a debug port")
    launch.add_argument("--port", type=int, default=launcher.DEFAULT_PORT)
    launch.add_argument(
        "--headed", action="store_true", help="show the app window (required for first-run)"
    )
    launch.add_argument(
        "--headless", action="store_true", help="draw on a virtual display (default)"
    )
    launch.add_argument("--detach", action="store_true", help="leave it running and return")
    launch.add_argument(
        "--wait", type=float, default=60.0, help="seconds to wait for the debug port"
    )
    launch.set_defaults(func=cmd_launch)

    services_cmd = sub.add_parser("services", parents=[common], help="list configured services")
    services_cmd.set_defaults(func=cmd_services)

    targets = sub.add_parser("targets", parents=[common], help="list live CDP targets")
    targets.add_argument("--port", type=int, default=launcher.DEFAULT_PORT)
    targets.set_defaults(func=cmd_targets)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except FerdiumError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except CdpError as exc:
        print(f"cdp error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
