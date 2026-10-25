"""Thin bridge from Python to the `gvoice/` TypeScript library.

The library stays the single owner of the reverse-engineered details: the API key,
SAPISIDHASH, referrer rules, SIP dialog, media plane. This module only marshals JSON
across the Node boundary so a Python caller (or a shell) can use it.

Node runs the `.ts` sources directly through type stripping, so there is no build step
and no `dist/` to go stale.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GvoiceError(RuntimeError):
    """The library call failed, or Node could not run it."""


@dataclass
class LibraryPaths:
    """Where the library lives, and the session file it should read."""

    root: Path
    session: Path

    @property
    def api_module(self) -> Path:
        return self.root / "src" / "gv-api.ts"

    @property
    def install_ok(self) -> bool:
        return (self.root / "node_modules").is_dir()

    def node(self) -> str:
        node = os.environ.get("GVOICE_NODE") or shutil.which("node")
        if not node:
            raise GvoiceError("node not found on PATH; the gvoice library requires Node >= 22.18")
        return node


def _session_path(root: Path) -> Path:
    """Session file for `root`, honouring the GVOICE_SESSION override."""
    override = os.environ.get("GVOICE_SESSION")
    return Path(override).expanduser() if override else root / "gv-session.json"


def find_library(explicit: str | Path | None = None) -> LibraryPaths:
    """Locate `gvoice/` — explicit flag, env var, then the repo layout, then siblings.

    The skill is installed by symlink next to the library rather than packaged, so the
    default is "the sibling directory named gvoice", resolved from both the real path
    and the symlinked path so it works either way it was installed.
    """
    # An explicit request must not silently fall back to a sibling: if a caller names a
    # library, finding a different one is worse than failing.
    for source, value in (("--library", explicit), ("GVOICE_ROOT", os.environ.get("GVOICE_ROOT"))):
        if not value:
            continue
        root = Path(value).expanduser().resolve()
        if (root / "package.json").is_file() and (root / "src" / "gv-api.ts").is_file():
            return LibraryPaths(root=root, session=_session_path(root))
        raise GvoiceError(
            f"{source} points at {root}, which is not the gvoice library "
            "(no package.json + src/gv-api.ts there)"
        )

    candidates: list[Path] = []
    here = Path(__file__).resolve()
    for base in (here, Path(__file__)):
        # src/gvoice_skill/bridge.py -> skill dir -> repo dir
        candidates.append(base.parents[2].parent / "gvoice")

    for candidate in candidates:
        root = candidate.resolve()
        if (root / "package.json").is_file() and (root / "src" / "gv-api.ts").is_file():
            return LibraryPaths(root=root, session=_session_path(root))
    tried = "\n  ".join(str(c) for c in candidates)
    raise GvoiceError(
        "could not find the gvoice library (a directory containing package.json and "
        f"src/gv-api.ts). Tried:\n  {tried}\nPass --library or set GVOICE_ROOT."
    )


# The runner is generated rather than checked in so the payload travels as stdin and no
# shell quoting is involved. It imports the library the same way the probes do.
_RUNNER = """
import { VoiceClient, METHODS, VOICE_API_KEY } from %(api)s;
import { existsSync, readFileSync } from "node:fs";

const [, , sessionPath, method, payloadJson] = process.argv;
const payload = JSON.parse(payloadJson || "[]");

if (!existsSync(sessionPath)) {
  console.error(JSON.stringify({ __error: "missing-session", path: sessionPath }));
  process.exit(3);
}
const session = JSON.parse(readFileSync(sessionPath, "utf8"));
const client = new VoiceClient(session);
try {
  const result = await client.call(method, payload);
  process.stdout.write(JSON.stringify({ ok: true, result }));
} catch (err) {
  process.stdout.write(JSON.stringify({
    ok: false,
    method,
    status: err?.status ?? null,
    message: String(err?.message ?? err),
  }));
  process.exitCode = 2;
}
"""


def api_call(
    library: LibraryPaths,
    method: str,
    payload: list[Any] | None = None,
    *,
    timeout: float = 60.0,
) -> Any:
    """Invoke one `VoiceClientService` method and return its decoded result."""
    if not library.install_ok:
        raise GvoiceError(
            f"dependencies are not installed in {library.root}; run `pnpm install` there"
        )
    api_url = library.api_module.as_uri()
    script = _RUNNER % {"api": json.dumps(api_url)}
    proc = subprocess.run(
        [
            library.node(),
            "--input-type=module",
            "--eval",
            script,
            "runner",
            str(library.session),
            method,
            json.dumps(payload or []),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=library.root,
    )
    stdout = proc.stdout.strip()
    if not stdout:
        raise GvoiceError(
            f"the library produced no output for {method} "
            f"(exit {proc.returncode}): {proc.stderr.strip()[:400]}"
        )
    try:
        parsed = json.loads(stdout)
    except ValueError as exc:
        raise GvoiceError(f"unreadable library output for {method}: {stdout[:400]}") from exc
    if parsed.get("__error") == "missing-session":
        raise GvoiceError(
            f"no session file at {parsed['path']}; run `gvoice-skill login` to capture one "
            "from a signed-in browser profile"
        )
    if not parsed.get("ok"):
        raise GvoiceError(parsed.get("message") or f"{method} failed")
    return parsed["result"]
