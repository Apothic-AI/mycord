"""A fake `pactl`, so the offline suite never touches the real audio server.

This is a **script on `PATH`**, not a mock object, for the same reason
`mycord-slack-skill/tests/fake_slack.py` is a real HTTP server: the thing under test is the
subprocess boundary — how arguments are constructed, what exit status means, and how output
is parsed — and a mocked object would test nothing but itself.

It reproduces the parts of `pactl`'s interface the plane actually depends on:

* `load-module <name> [k=v ...]` prints a new module index;
* `unload-module <index>` removes it;
* `list short modules|sinks|sources|sink-inputs|source-outputs` prints tab-separated rows;
* `get-default-sink|get-default-source`;
* `set-default-sink|set-default-source <name>`;
* `move-sink-input|move-source-output <index> <name>`;
* `--do <file>`: run one command from a file, for tests that replace it mid-flight.

It also records every invocation, so a test can assert *what was asked for* — which is how
"the default was restored" is proved without an audio server. Failure injection is
deliberate and narrow: `FAKE_PACTL_FAIL=<substring>` makes any command containing that
substring exit non-zero with a realistic stderr line.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

STATE = Path(os.environ["FAKE_PACTL_STATE"])
LOG = Path(os.environ["FAKE_PACTL_LOG"])

state = json.loads(STATE.read_text())
argv = sys.argv[1:]
with LOG.open("a") as handle:
    handle.write(json.dumps(argv) + "\n")

fail = os.environ.get("FAKE_PACTL_FAIL", "")
command = " ".join(argv)
if fail and fail in command:
    sys.stderr.write("Failure: " + fail + " (injected)\n")
    sys.exit(1)

def save():
    STATE.write_text(json.dumps(state))

def next_index():
    state["next"] = state.get("next", 100) + 1
    return state["next"]

if not argv:
    sys.exit(1)

if argv[0] == "load-module":
    module, extra = argv[1], argv[2:]
    index = next_index()
    state["modules"][str(index)] = {"name": module, "args": extra}
    if module == "module-null-sink":
        name = next((a.split("=", 1)[1] for a in extra if a.startswith("sink_name=")), None)
        if name:
            state["sinks"][name] = index
            state["sources"][name + ".monitor"] = index
    elif module == "module-remap-source":
        name = next((a.split("=", 1)[1] for a in extra if a.startswith("source_name=")), None)
        if name:
            state["sources"][name] = index
    save()
    print(index)
    sys.exit(0)

if argv[0] == "unload-module":
    index = argv[1]
    module = state["modules"].pop(index, None)
    if module is None:
        sys.stderr.write("Failure: No such entity\n")
        sys.exit(1)
    for collection in ("sinks", "sources"):
        state[collection] = {k: v for k, v in state[collection].items() if str(v) != index}
    save()
    sys.exit(0)

if argv[0] == "list":
    kind = argv[-1]
    if kind == "modules":
        for index in sorted(state["modules"], key=int):
            module = state["modules"][index]
            print("\t".join([index, module["name"], " ".join(module["args"])]))
    elif kind == "sinks":
        for name, index in state["sinks"].items():
            print("\t".join([str(index), name, "PipeWire", "s32le 2ch 48000Hz", "RUNNING"]))
    elif kind == "sources":
        for name, index in state["sources"].items():
            print("\t".join([str(index), name, "PipeWire", "s32le 2ch 48000Hz", "IDLE"]))
    elif kind in ("sink-inputs", "source-outputs"):
        for stream in state.get(kind, []):
            print("\t".join([str(stream["index"]), str(stream["target"]),
                             str(stream.get("client", 0)), "PipeWire", "s16le 1ch 48000Hz"]))
    sys.exit(0)

if argv[0] in ("get-default-sink", "get-default-source"):
    print(state["default_sink" if argv[0].endswith("sink") else "default_source"])
    sys.exit(0)

if argv[0] in ("set-default-sink", "set-default-source"):
    key = "default_sink" if argv[0].endswith("sink") else "default_source"
    state[key] = argv[1]
    save()
    sys.exit(0)

if argv[0] in ("move-sink-input", "move-source-output"):
    collection = "sink-inputs" if argv[0] == "move-sink-input" else "source-outputs"
    wanted = argv[1]
    target_name = argv[2]
    lookup = state["sinks"] if collection == "sink-inputs" else state["sources"]
    if target_name not in lookup:
        sys.stderr.write("Failure: No such entity\n")
        sys.exit(1)
    for stream in state.get(collection, []):
        if str(stream["index"]) == wanted:
            stream["target"] = lookup[target_name]
            save()
            sys.exit(0)
    sys.stderr.write("Failure: No such entity\n")
    sys.exit(1)

sys.stderr.write("Failure: unknown command " + argv[0] + "\n")
sys.exit(1)
"""

#: Starting state: one real speaker and one real microphone, exactly as an untouched
#: desktop has them. The names are chosen to be obviously *not* the plane's.
INITIAL = {
    "next": 100,
    "modules": {},
    "sinks": {"alsa_output.fake_speaker": 50},
    "sources": {"alsa_output.fake_speaker.monitor": 50, "alsa_input.fake_mic": 51},
    "default_sink": "alsa_output.fake_speaker",
    "default_source": "alsa_input.fake_mic",
    "sink-inputs": [{"index": 900, "target": 50, "client": 42}],
    "source-outputs": [],
}

OPERATOR_SINK = "alsa_output.fake_speaker"
OPERATOR_SOURCE = "alsa_input.fake_mic"


@dataclass
class FakePactl:
    """A fake audio server installed as an executable on `PATH`."""

    bin_dir: Path
    state_path: Path
    log_path: Path
    initial: dict = field(default_factory=lambda: json.loads(json.dumps(INITIAL)))

    @property
    def executable(self) -> Path:
        return self.bin_dir / "pactl"

    @property
    def called(self) -> list[list[str]]:
        """Every `pactl` invocation so far, in order."""
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]

    @property
    def state(self) -> dict:
        return json.loads(self.state_path.read_text())

    def install(self) -> FakePactl:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.executable.write_text(SCRIPT)
        self.executable.chmod(0o755)
        self.state_path.write_text(json.dumps(self.initial))
        self.log_path.write_text("")
        return self

    def write_state(self, **changes: object) -> None:
        """Rewrite parts of the server state, to simulate drift between commands."""
        state = self.state
        state.update(changes)
        self.state_path.write_text(json.dumps(state))

    def env(self, **extra: str) -> dict[str, str]:
        """Environment for a test: fake `pactl` first on `PATH`, plus its state files."""
        base = {
            "FAKE_PACTL_STATE": str(self.state_path),
            "FAKE_PACTL_LOG": str(self.log_path),
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        base.update(extra)
        return base


def main() -> int:
    """The script's own entry point, used only when run directly for debugging."""
    print(json.dumps(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
