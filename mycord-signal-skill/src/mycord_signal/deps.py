"""Dependency detection and readiness gates for Signal.

This module exists because Signal's call path is split across two independently-versioned
components and both are load-bearing:

* ``signal-cli`` spawns the media tunnel per call and relays signalling. Before 0.14.9 it
  hard-coded ``local_device_id: 1`` into the tunnel config, which is wrong for a *linked*
  device (the only kind this skill can be) and breaks multi-device ICE.
* ``signal-call-tunnel`` must be a build with the pipe audio backend *and* the
  ``sender_device_id`` handling for ``receivedIce``. Those landed in separate commits.

A mismatch does not fail at start-up. It fails inside a call, which is the worst possible
moment, so :func:`require_call_ready` refuses loudly at the call site instead.

Everything here is a probe: nothing mutates, nothing installs, and every external command
runs under a timeout. That last part is not hygiene — ``signal-cli listDevices`` was
measured hanging for 15 hours waiting on a device sync, so an unwrapped subprocess is a
latent indefinite hang.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

#: signal-cli builds older than this are refused for the call path. 0.14.9 is the first
#: release that passes the account's real device id to the tunnel (upstream PR #2128);
#: 0.14.2 is where calling landed, so anything below 0.14.9 can ring but is wrong for a
#: linked device.
REQUIRED_SIGNAL_CLI: Final = (0, 14, 9)

#: The tunnel commit this skill is built and verified against: PR #5's pipe audio backend
#: plus the ``sender_device_id`` fix. Upstream ``visigoth`` has neither; the pipe backend
#: lives on an unmerged fork.
REQUIRED_TUNNEL_COMMIT: Final = "5a963d1"

#: Default tunnel checkout on this host.
DEFAULT_TUNNEL_SRC: Final = Path.home() / ".local" / "src" / "signal-call-tunnel"

#: signal-cli's account store. The skill never writes here.
DEFAULT_SIGNAL_DATA: Final = (
    Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "signal-cli"
)

#: signal-cli's JSON-RPC socket for single-account daemon mode.
DEFAULT_SOCKET: Final = (
    Path(os.environ.get("XDG_RUNTIME_DIR") or "/run/user/0") / "signal-cli" / "socket"
)

#: Timeout for any ``signal-cli`` subprocess. Short on purpose: every one of these is a
#: local probe, and a slow one is a hang, not a slow answer.
CLI_TIMEOUT: Final = 20.0

Status = Literal["ok", "stale", "missing", "unknown"]

_VERSION_RE: Final = re.compile(r"\b(\d+(?:\.\d+){1,3})\b")


# --------------------------------------------------------------------------- versions


def parse_version(text: str) -> tuple[int, ...] | None:
    """Pull a dotted version out of ``text``.

    ``signal-cli --version`` prints ``signal-cli 0.14.9``; the JSON-RPC ``version`` method
    returns ``{"version":"0.14.9"}``; a GraalVM native build can append a build tag. Returns
    ``None`` when nothing parses so callers must handle "unknown" explicitly rather than
    comparing against ``()`` and silently passing.
    """
    match = _VERSION_RE.search(text.strip().lstrip("vV"))
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _pad(version: Sequence[int], width: int) -> tuple[int, ...]:
    head = tuple(version[:width])
    return head + (0,) * (width - len(head))


def version_at_least(have: Sequence[int], want: Sequence[int]) -> bool:
    """Compare two dotted versions, zero-padding the shorter one."""
    width = max(len(have), len(want))
    return _pad(have, width) >= _pad(want, width)


def format_version(version: Sequence[int]) -> str:
    return ".".join(str(part) for part in version)


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Check:
    """One dependency or readiness check.

    ``status`` drives the exit code; ``detail`` carries the *measured* value, not just a
    verdict, so a human reading one line knows what is wrong and by how much.
    """

    name: str
    status: Status
    detail: str
    fix: str | None = None

    @property
    def blocking(self) -> bool:
        return self.status in ("stale", "missing")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.fix is not None:
            payload["fix"] = self.fix
        return payload


@dataclass(frozen=True)
class TunnelInfo:
    """What we could learn about the tunnel binary and its checkout."""

    binary: str | None
    binary_sha256: str | None
    checkout: str | None
    commit: str | None
    built_sha256: str | None
    at_required_commit: bool | None
    contains_required: bool | None
    subject: str | None

    @property
    def short_commit(self) -> str | None:
        return self.commit[:7] if self.commit else None


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of booting the tunnel in pipe mode."""

    ok: bool
    socket_path: str | None = None
    detail: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class Account:
    """A locally known Signal account, read from ``accounts.json``."""

    number: str | None
    uuid: str | None
    path: str | None
    environment: str | None

    def label(self) -> str:
        if self.number:
            return self.number
        if self.uuid:
            return self.uuid
        return self.path or "?"


@dataclass
class Report:
    """Everything ``doctor`` found, plus the exit code that follows from it."""

    checks: list[Check] = field(default_factory=list)
    versions: dict[str, str | None] = field(default_factory=dict)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    def find(self, name: str) -> Check | None:
        for check in self.checks:
            if check.name == name:
                return check
        return None

    @property
    def blocking(self) -> list[Check]:
        return [check for check in self.checks if check.blocking]

    @property
    def dependency_problems(self) -> list[Check]:
        """Blocking checks that a human or script can fix without an account.

        The account row is excluded on purpose: "nobody linked a phone" is a different
        problem from "the binaries are wrong", and conflating them would make dep-only CI
        permanently red.
        """
        return [check for check in self.blocking if not check.name.startswith("account")]

    def as_dict(self) -> dict[str, Any]:
        return {
            "versions": self.versions,
            "checks": [check.as_dict() for check in self.checks],
            "dependency_ready": not self.dependency_problems,
        }


# --------------------------------------------------------------------------- helpers


def run(argv: Sequence[str], timeout: float = CLI_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a probe command with a hard timeout.

    Every caller must go through this. A raw ``subprocess.run`` without ``timeout`` here is
    a bug: ``listDevices`` is documented to hang forever and one did, for 15 hours.
    """
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def resolve_binary(name: str) -> str | None:
    """Resolve an executable name to the binary that will actually run it.

    A plain ``shutil.which`` is not enough on this host: ``signal-call-tunnel`` is reached
    through a **mise shim** — a symlink to the ``mise`` binary itself, which re-execs the real
    tool. Hashing the shim therefore hashes ``mise`` and compares it against the checkout
    build, producing a permanent, meaningless "installed binary differs" warning. Asking mise
    for its own resolution is the only way to get a comparable path.

    Falls back to the ``PATH`` result whenever mise is absent, silent, or does not know the
    name, so this stays correct on a host that does not use mise at all.
    """
    on_path = shutil.which(name)
    if on_path is None:
        return None
    resolved = Path(on_path)
    try:
        real = resolved.resolve()
    except OSError:
        return on_path
    looks_like_shim = real.name == "mise" or resolved.parent.name == "shims"
    if not looks_like_shim:
        return on_path
    try:
        completed = run(["mise", "which", name], timeout=20)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return on_path
    candidate = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    if completed.returncode == 0 and candidate and Path(candidate).exists():
        return candidate
    return on_path


def _git(repo: Path, *args: str) -> str | None:
    try:
        completed = run(["git", "-C", str(repo), *args], timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


# --------------------------------------------------------------------------- signal-cli


def signal_cli_version(signal_cli: str = "signal-cli") -> tuple[str | None, str]:
    """Return ``(version, raw)`` for an installed signal-cli.

    ``--version`` is used rather than the ``version`` subcommand because it is a pure build
    constant: it does not open the account store, does not print the dead-account WARN, and
    does not touch the network. Measured at ~0.2 s on this host.
    """
    try:
        completed = run([signal_cli, "--version"])
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    raw = ((completed.stdout or "") + (completed.stderr or "")).strip()
    for line in raw.splitlines():
        parsed = parse_version(line)
        if parsed is not None:
            return format_version(parsed), line.strip()
    return None, raw or f"exit status {completed.returncode}"


def check_signal_cli(
    signal_cli: str = "signal-cli", required: Sequence[int] = REQUIRED_SIGNAL_CLI
) -> Check:
    version, raw = signal_cli_version(signal_cli)
    want = format_version(required)
    if version is None:
        return Check(
            name="signal-cli",
            status="missing",
            detail=f"not runnable ({raw})",
            fix=_signal_cli_fix(want),
        )
    parsed = parse_version(version)
    if parsed is None or not version_at_least(parsed, required):
        return Check(
            name="signal-cli",
            status="stale",
            detail=(
                f"found {version}, need >={want}. Older builds hard-code local_device_id=1 "
                "into the tunnel config, which is wrong for a linked device"
            ),
            fix=_signal_cli_fix(want),
        )
    return Check(name="signal-cli", status="ok", detail=f"{version} (need >={want})")


def _signal_cli_fix(want: str) -> str:
    return (
        f"scripts/provision.sh --apply   (installs signal-cli >= {want}, GPG-verified), "
        "or see docs/determine/signal.md"
    )


# --------------------------------------------------------------------------- tunnel


def tunnel_info(src: Path = DEFAULT_TUNNEL_SRC, binary: str = "signal-call-tunnel") -> TunnelInfo:
    """Inspect the checkout and the installed binary, and relate them.

    Provenance and capability are reported separately. A hash match proves the installed
    binary came from this checkout; the pipe probe proves it works. When the checkout is
    absent we say ``unknown`` rather than failing — a binary installed another way can still
    be correct, we just cannot prove which commit it is, and saying so is the honest answer.
    """
    resolved = resolve_binary(binary)
    binary_path = Path(resolved) if resolved else None
    binary_sha = file_sha256(binary_path) if binary_path else None

    commit: str | None = None
    built_sha: str | None = None
    at_required: bool | None = None
    contains: bool | None = None
    subject: str | None = None

    if src.is_dir():
        commit = _git(src, "rev-parse", "HEAD")
        built = src / "signal-call-tunnel" / "target" / "release" / "signal-call-tunnel"
        built_sha = file_sha256(built) if built.exists() else None
        subject = _git(src, "log", "-1", "--format=%s")
        if commit is not None:
            at_required = commit.startswith(REQUIRED_TUNNEL_COMMIT)
            try:
                ancestor = run(
                    [
                        "git",
                        "-C",
                        str(src),
                        "merge-base",
                        "--is-ancestor",
                        REQUIRED_TUNNEL_COMMIT,
                        "HEAD",
                    ],
                    timeout=20,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                contains = None
            else:
                contains = ancestor.returncode == 0

    return TunnelInfo(
        binary=str(binary_path) if binary_path else None,
        binary_sha256=binary_sha,
        checkout=str(src) if src.is_dir() else None,
        commit=commit,
        built_sha256=built_sha,
        at_required_commit=at_required,
        contains_required=contains,
        subject=subject,
    )


def check_tunnel_binary(info: TunnelInfo) -> Check:
    if info.binary is None:
        return Check(
            name="signal-call-tunnel:binary",
            status="missing",
            detail="not found on PATH (or via SIGNAL_CALL_TUNNEL_BIN)",
            fix="scripts/provision.sh --apply",
        )
    if info.built_sha256 is None:
        return Check(
            name="signal-call-tunnel:binary",
            status="unknown",
            detail=(
                f"{info.binary} present (sha256 {str(info.binary_sha256)[:12]}…) but the "
                "checkout has no release build to compare against"
            ),
        )
    if info.binary_sha256 == info.built_sha256:
        return Check(
            name="signal-call-tunnel:binary",
            status="ok",
            detail=(
                f"{info.binary} is the build of {info.checkout} "
                f"(sha256 {str(info.binary_sha256)[:12]}…)"
            ),
        )
    return Check(
        name="signal-call-tunnel:binary",
        status="stale",
        detail=(
            f"{info.binary} sha256 {str(info.binary_sha256)[:12]}… != checkout build "
            f"{info.built_sha256[:12]}… — the installed binary is not from this checkout"
        ),
        fix="scripts/provision.sh --apply   (reinstalls the checkout build)",
    )


def check_tunnel_commit(info: TunnelInfo) -> Check:
    required = REQUIRED_TUNNEL_COMMIT
    if info.checkout is None:
        return Check(
            name="signal-call-tunnel:checkout",
            status="unknown",
            detail=f"no checkout at {DEFAULT_TUNNEL_SRC}; cannot verify which commit the binary is",
        )
    if info.commit is None:
        return Check(
            name="signal-call-tunnel:checkout",
            status="unknown",
            detail=f"{info.checkout} is not a git checkout",
        )
    if info.at_required_commit or info.contains_required:
        suffix = "" if info.at_required_commit else " (descendant; contains the required commit)"
        return Check(
            name="signal-call-tunnel:checkout",
            status="ok",
            detail=f"{info.short_commit} {info.subject or ''}".strip() + suffix,
        )
    return Check(
        name="signal-call-tunnel:checkout",
        status="stale",
        detail=(
            f"{info.short_commit} {info.subject or ''} — does not contain {required}, so it "
            "lacks the receivedIce sender_device_id fix"
        ).strip(),
        fix=f"scripts/provision.sh --apply   (checks out {required} and runs ./build.sh)",
    )


def probe_pipe(binary: str = "signal-call-tunnel", timeout: float = 25.0) -> ProbeResult:
    """Boot the tunnel in pipe mode and read its ``ready`` line.

    Needs no account, no network and no call: the tunnel binds its socket, reports both
    device names, and exits as soon as stdin closes, so one ``subprocess.run`` is enough.

    The config is not ``--help`` friendly — the binary parses stdin and *rejects* a string
    ``call_id`` — so the payload below carries real integers. (``--help`` exits with
    ``failed to parse config JSON``; that is expected, not a failure to report.)

    Proves: the binary is a pipe-capable build. Does not prove: that media flows — that
    needs a live call.
    """
    env = dict(os.environ)
    env["SIGNAL_CALL_TUNNEL_AUDIO_MODE"] = "pipe"
    base = Path(env.get("MYCORD_SIGNAL_PROBE_DIR") or "/tmp")
    socket_dir = base / f"mycord-signal-probe-{os.getpid()}"
    socket_dir.mkdir(parents=True, exist_ok=True)
    env["SIGNAL_CALL_TUNNEL_SOCKET_DIR"] = str(socket_dir)

    # A call_id in a private high range keeps a probe from ever colliding with a live call.
    call_id = 900_000_000 + (os.getpid() % 100_000)
    config = json.dumps(
        {
            "call_id": call_id,
            "is_outgoing": True,
            "local_device_id": 1,
            "audio_mode": "pipe",
        }
    )
    try:
        completed = subprocess.run(
            [binary],
            input=config + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (FileNotFoundError, PermissionError) as exc:
        return ProbeResult(ok=False, detail=f"could not execute {binary}: {exc}")
    except subprocess.TimeoutExpired:
        return ProbeResult(ok=False, detail=f"did not exit within {timeout:.0f}s")

    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "ready":
            continue
        input_name = payload.get("inputDeviceName")
        output_name = payload.get("outputDeviceName")
        if not isinstance(input_name, str) or not isinstance(output_name, str):
            return ProbeResult(ok=False, detail=f"ready without device names: {line}")
        if not input_name.startswith("unix:"):
            return ProbeResult(
                ok=False,
                detail=f"ready reports {input_name!r}; this binary is virtual-device only",
                stderr=_tail(completed.stderr),
            )
        if input_name != output_name:
            return ProbeResult(
                ok=False,
                detail=f"pipe device names differ ({input_name} vs {output_name})",
            )
        path = input_name.removeprefix("unix:")
        return ProbeResult(ok=True, socket_path=path, detail=f"pipe ready at {path}")

    return ProbeResult(
        ok=False,
        detail="no ready line on stdout",
        stderr=_tail(completed.stderr) or _tail(completed.stdout),
    )


def _tail(text: str, limit: int = 400) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= limit else "…" + cleaned[-limit:]


# --------------------------------------------------------------------------- account


def read_accounts(data_dir: Path = DEFAULT_SIGNAL_DATA) -> tuple[list[Account], str]:
    """Read ``accounts.json`` directly.

    Preferred over the CLI and the RPC surface for two verified reasons: ``listAccounts`` is
    **not implemented** over the single-account JSON-RPC daemon (it returns
    ``-32601 Method not implemented``, because ``ListAccountsCommand`` is a
    ``JsonRpcMultiLocalCommand`` and the single-account handler only dispatches single/multi
    commands), and shelling out to ``signal-cli`` costs a process and reintroduces the hang
    risk this module exists to avoid. Reading the store is local, instant, and offline.

    The file is 0600 and holds only account *pointers*, not keys; we surface number, uuid,
    path and environment and nothing else.
    """
    path = data_dir / "data" / "accounts.json"
    try:
        raw = path.read_text()
    except (OSError, NotADirectoryError) as exc:
        return [], f"could not read {path}: {exc}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"{path} is not valid JSON: {exc}"
    entries = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return [], f"{path} has no accounts list"

    accounts: list[Account] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        accounts.append(
            Account(
                number=_as_str(entry.get("number")),
                uuid=_as_str(entry.get("uuid")),
                path=_as_str(entry.get("path")),
                environment=_as_str(entry.get("environment")),
            )
        )
    return accounts, ""


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def check_account(data_dir: Path = DEFAULT_SIGNAL_DATA) -> Check:
    accounts, diagnostic = read_accounts(data_dir)
    if accounts:
        detail = f"{len(accounts)} account(s): " + ", ".join(
            account.label() for account in accounts
        )
        environments = {account.environment for account in accounts if account.environment}
        if environments - {"LIVE"}:
            detail += f" (environment: {', '.join(sorted(environments))})"
        return Check(name="account", status="ok", detail=detail)
    detail = "no linked account"
    if diagnostic:
        detail += f" ({diagnostic})"
    return Check(
        name="account",
        status="missing",
        detail=detail,
        fix=(
            "link a phone as a secondary device: `signal-cli link -n mycord`, scan the sgnl:// URI "
            "with Signal > Settings > Linked devices. Needs a human with the Signal app"
        ),
    )


# --------------------------------------------------------------------------- daemon


def daemon_version(
    socket_path: Path = DEFAULT_SOCKET, timeout: float = 10.0
) -> tuple[str | None, str]:
    """Ask a running daemon for its version over its Unix socket.

    Imported lazily to keep :mod:`deps` free of a hard dependency on the RPC module at import
    time; the RPC client is the only thing that knows the framing.
    """
    if not socket_path.exists():
        return None, f"no socket at {socket_path}"
    from . import rpc as rpc_mod

    client = rpc_mod.RpcClient(socket_path, timeout=timeout)
    try:
        client.connect()
        version = client.version()
    except (rpc_mod.TransportError, rpc_mod.RpcError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        client.close()
    parsed = parse_version(version)
    return (format_version(parsed) if parsed else version), ""


def check_daemon(socket_path: Path = DEFAULT_SOCKET) -> Check:
    version, diagnostic = daemon_version(socket_path)
    if version is None:
        return Check(
            name="daemon",
            status="missing",
            detail=f"no live JSON-RPC daemon ({diagnostic})",
            fix=(
                "signal-cli -a <number> daemon --socket   "
                "(reuse a running one: the account file is exclusively locked, so a second "
                "instance hangs waiting)"
            ),
        )
    parsed = parse_version(version)
    if parsed is not None and not version_at_least(parsed, REQUIRED_SIGNAL_CLI):
        return Check(
            name="daemon",
            status="stale",
            detail=(
                f"daemon reports {version}, need >={format_version(REQUIRED_SIGNAL_CLI)} "
                "(restart it on the upgraded binary)"
            ),
            fix="restart the daemon on the upgraded binary",
        )
    return Check(name="daemon", status="ok", detail=f"{version} on {socket_path}")


# --------------------------------------------------------------------------- gates


def require_call_ready(report: Report) -> str | None:
    """Return a refusal message when the call path must not be used, else ``None``.

    Checks the three things that fail *during* call setup rather than before it: signal-cli
    version, tunnel provenance, and pipe capability. A missing daemon is included too,
    because ``startCall`` needs one and an absent daemon would otherwise surface as a hang.
    """
    names = (
        "signal-cli",
        "signal-call-tunnel:binary",
        "signal-call-tunnel:checkout",
        "signal-call-tunnel:pipe",
        "daemon",
    )
    blocking = [
        check
        for check in report.checks
        if check.name in names and check.status in ("stale", "missing")
    ]
    if not blocking:
        return None
    lines = ["refusing the call path: dependencies are not call-ready"]
    for check in blocking:
        lines.append(f"  - {check.name} [{check.status}]: {check.detail}")
        if check.fix:
            lines.append(f"    fix: {check.fix}")
    return "\n".join(lines)
