"""The audio plane: virtual devices the agent owns on PipeWire/PulseAudio.

Why this shape at all (see ``docs/determine/zoom.md``): Zoom, like every conferencing
client, is an ordinary client of the system mixer. So instead of terminating Zoom's
WebRTC — which needs a registered SDK app — the agent presents itself as *the user's
speaker and microphone*.

Topology, and why it is **three** modules rather than the pair sketched in the
determination (that sketch was corrected during implementation, because one sink cannot
serve both directions without feeding Zoom's own output back into Zoom's microphone):

::

    Zoom output ──▶ [speaker sink]  ──monitor──▶ agent capture      (inbound: agent hears)
                    (null-sink, no remap: never reaches the mic)

    agent TTS   ──▶ [mic sink]      ──monitor──▶ [mic source] ──▶ Zoom input
                    (null-sink)       (remap-source: what Zoom records from)

**Outbound** speech is played into ``mic_sink``; the remap-source on its monitor is the
device Zoom lists as a microphone. **Inbound** audio is Zoom's output landing on
``speaker_sink``, whose monitor the agent records. Keeping them separate is what removes
the echo path: audio the agent speaks can never arrive back as "the far end", so no echo
guard is needed (gvoice needs one because Google Voice returns our own audio on the same
peer connection).

Measured on this machine before this module existed, and re-measured by
``make verify``/the live test afterwards; the numbers are in the README.

Three invariants drive the implementation:

1. **Restore, always.** The operator's default sink and source are recorded *before*
   anything changes, persisted, and restored in a ``finally`` — the restore is idempotent,
   so a retry after a crash converges instead of failing.
2. **Never adopt a module this process did not load.** Ownership is decided by module
   *order* relative to a load marker; see ``_scan`` for why ``pactl``'s echo of our own
   description is not a safe identity on its own.
3. **Symmetric routing.** ``route --to-agent`` points both directions at the plane;
   ``route --to-mixer`` points both back at the recorded pre-existing defaults.
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Protocol

from .errors import AudioError, NotInstalled, Unconfigured
from .state import AudioSession, StateStore
from .tools import CaptureRunner, Runner, Spawner, run_command, which

#: Native rate of the calls being carried, so nothing resamples mid-conversation.
SAMPLE_RATE = 48000

#: Mono: speech is mono, and stereo doubles the bytes for no information.
CHANNELS = 1

#: What the capture side speaks, and what the ASR workers expect.
SAMPLE_FORMAT = "s16le"

#: How long a freshly spawned recorder may take to deliver its first byte.
#:
#: Measured on this host, four consecutive trials on a monitor nothing was writing to:
#: 1.98 s, 2.00 s, 2.00 s, 1.99 s. This is `parec`'s own stream startup, not the sink's
#: state — priming the sink does not change it. It matters because a caller asking for
#: "2 seconds" reasonably expects two seconds *of audio*: counting the startup latency
#: against the capture deadline silently returns nothing for any capture under ~2 s, which
#: is indistinguishable from silence. `capture` therefore settles first, then records.
RECORDER_SETTLE_S = 4.0

DEFAULT_SPEAKER_SINK = "mycord_zoom_speaker"
DEFAULT_MIC_SINK = "mycord_zoom_mic"
DEFAULT_MIC_SOURCE = "mycord_zoom_microphone"

#: Module description of everything this module loads. Echoed back by `pactl list
#: modules`, which makes the modules *findable* — but see `_scan`: it is caller-supplied
#: text, so it cannot be the sole identity.
DESCRIPTION = "mycord-zoom"

#: Zero-width argument appended to every load: PulseAudio stores module arguments verbatim
#: and ignores unknown keys, so this survives as a trustworthy "loaded by this package"
#: marker in `pactl list modules`.
_LOAD_MARKER = f"mycord_zoom_marker={DESCRIPTION}"

ROLE_SPEAKER = "speaker"
ROLE_MIC_SINK = "mic_sink"
ROLE_MIC_SOURCE = "mic_source"


class PacketSink(Protocol):
    """Something PCM can be written to, i.e. the virtual microphone."""

    def write(self, data: bytes) -> int: ...


@dataclass(frozen=True)
class ModuleInfo:
    """One row of `pactl list short modules`."""

    index: int
    name: str
    argument: str


@dataclass(frozen=True)
class StreamInfo:
    """One row of `pactl list short sink-inputs` / `short source-outputs`."""

    index: int
    target: int
    client: int
    driver: str


@dataclass(frozen=True)
class AudioStatus:
    """What `mycord-zoom audio status` reports."""

    running: bool
    speaker_sink: str
    mic_sink: str
    mic_source: str
    modules: dict[str, int]
    current_default_sink: str
    current_default_source: str
    previous_default_sink: str | None
    previous_default_source: str | None
    routed: bool
    sink_inputs: tuple[StreamInfo, ...] = ()
    source_outputs: tuple[StreamInfo, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "running": self.running,
            "speaker_sink": self.speaker_sink,
            "mic_sink": self.mic_sink,
            "mic_source": self.mic_source,
            "modules": dict(self.modules),
            "current_default_sink": self.current_default_sink,
            "current_default_source": self.current_default_source,
            "previous_default_sink": self.previous_default_sink,
            "previous_default_source": self.previous_default_source,
            "routed": self.routed,
            "sink_inputs": [s.__dict__ for s in self.sink_inputs],
            "source_outputs": [s.__dict__ for s in self.source_outputs],
        }


class _TerminationGuard:
    """Turn SIGTERM/SIGHUP into `SystemExit` so ``finally`` blocks actually run.

    Without this, ``kill <pid>`` during ``up``/``down`` skips every cleanup path and leaves
    a null sink holding the operator's default audio. SIGKILL still cannot be caught; that
    case is covered by the on-disk session and ``audio recover``.
    """

    def __init__(self, signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGHUP)) -> None:
        self._signals = signals
        self._previous: dict[int, object] = {}

    def __enter__(self) -> _TerminationGuard:
        for sig in self._signals:
            self._previous[sig] = signal.signal(sig, self._raise)
        return self

    def __exit__(self, *exc_info: object) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]

    @staticmethod
    def _raise(signum: int, _frame: FrameType | None) -> None:
        raise SystemExit(128 + signum)


@dataclass
class PlaneSpec:
    """The three device names this plane owns."""

    speaker_sink: str = DEFAULT_SPEAKER_SINK
    mic_sink: str = DEFAULT_MIC_SINK
    mic_source: str = DEFAULT_MIC_SOURCE
    description: str = DESCRIPTION

    def to_dict(self) -> dict[str, str]:
        return {
            "speaker_sink": self.speaker_sink,
            "mic_sink": self.mic_sink,
            "mic_source": self.mic_source,
            "description": self.description,
        }


@dataclass
class _Scan:
    """Result of looking for our modules in the live module list."""

    found: dict[str, ModuleInfo] = field(default_factory=dict)
    marker_seen: bool = False

    @property
    def complete(self) -> bool:
        return all(role in self.found for role in (ROLE_SPEAKER, ROLE_MIC_SINK, ROLE_MIC_SOURCE))


class AudioPlane:
    """Creates, inspects, routes and tears down the virtual device plane."""

    def __init__(
        self,
        store: StateStore,
        *,
        runner: Runner = run_command,
        pactl: str | None = None,
        spec: PlaneSpec | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._pactl = pactl
        self.spec = spec or PlaneSpec()

    # -- plumbing ----------------------------------------------------------------

    @property
    def pactl(self) -> str:
        """Resolved `pactl`, looked up lazily so `--help` needs no audio server."""
        if self._pactl is None:
            self._pactl = which("pactl", purpose="the virtual audio plane")
        return self._pactl

    def _tool(self, name: str, purpose: str) -> str:
        """Resolve a companion CLI **next to `pactl`** when possible.

        `pactl`, `pacat` and `parec` must come from one installation. On a machine with both
        a distro and a Homebrew `pulseaudio-utils`, `pactl` and `pacat` can resolve from
        different builds, and the mismatch shows up as a stream that the server accepts but
        never delivers audio for — a silent capture that looks exactly like silence.
        """
        sibling = Path(self.pactl).parent / name
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
        return which(name, purpose=purpose)

    def _run(self, *args: str) -> str:
        result = self._runner([self.pactl, *args])
        if not result.ok:
            raise AudioError(f"pactl {args[0]} failed: {result.message}")
        return result.stdout

    def _try(self, *args: str) -> bool:
        """Best-effort variant for cleanup, where a failure must not mask the real error."""
        try:
            return self._runner([self.pactl, *args]).ok
        except Exception:
            return False

    def _load_module(self, module: str, *module_args: str) -> int:
        result = self._runner([self.pactl, "load-module", module, *module_args])
        if not result.ok:
            raise AudioError(f"could not load {module}: {result.message}")
        digits = re.search(r"\d+", result.stdout)
        if digits is None:
            raise AudioError(f"pactl load-module {module} printed no module index")
        return int(digits.group(0))

    def modules(self) -> tuple[ModuleInfo, ...]:
        rows: list[ModuleInfo] = []
        for line in self._run("list", "short", "modules").splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0].strip().isdigit():
                continue
            rows.append(
                ModuleInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    argument=parts[2] if len(parts) > 2 else "",
                )
            )
        return tuple(rows)

    def _scan(self) -> _Scan:
        """Find our modules, refusing to adopt ones loaded after our marker.

        The identity problem: ``device.description`` inside a module's argument is
        caller-supplied text, so a second agent could load a module with the identical
        description between our ``load-module`` returning and our ``list modules``
        running. Trusting the description alone would let two planes each "find" the same
        module and one of them unload the other's.

        So: our marker is the highest index carrying our marker argument, and only modules
        at or below it are eligible. When no marker is present the plane came from an
        earlier run (or a reboot) and name matching is all there is — ``down`` still cleans
        it up, because unloading a module we did not load is the safer failure here.
        """
        modules = self.modules()
        markers = [m.index for m in modules if _LOAD_MARKER in m.argument]
        ceiling = max(markers) if markers else None
        scan = _Scan(marker_seen=ceiling is not None)
        wanted = {
            ROLE_SPEAKER: ("module-null-sink", f"sink_name={self.spec.speaker_sink}"),
            ROLE_MIC_SINK: ("module-null-sink", f"sink_name={self.spec.mic_sink}"),
            ROLE_MIC_SOURCE: ("module-remap-source", f"source_name={self.spec.mic_source}"),
        }
        for module in modules:
            if ceiling is not None and module.index > ceiling:
                continue
            for role, (module_name, needle) in wanted.items():
                if role in scan.found:
                    continue
                if module.name == module_name and needle in module.argument:
                    scan.found[role] = module
        return scan

    # -- inspection --------------------------------------------------------------

    def get_default_sink(self) -> str:
        return self._run("get-default-sink").strip()

    def get_default_source(self) -> str:
        return self._run("get-default-source").strip()

    def _streams(self, kind: str) -> tuple[StreamInfo, ...]:
        rows: list[StreamInfo] = []
        for line in self._run("list", "short", kind).splitlines():
            parts = line.split("\t")
            if len(parts) < 4 or not parts[0].strip().isdigit():
                continue
            rows.append(
                StreamInfo(
                    index=int(parts[0]),
                    target=int(parts[1]) if _is_int(parts[1]) else -1,
                    client=int(parts[2]) if _is_int(parts[2]) else -1,
                    driver=parts[3],
                )
            )
        return tuple(rows)

    def _named_index(self, kind: str, name: str) -> int | None:
        for line in self._run("list", "short", kind).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] == name:
                return int(parts[0])
        return None

    def sink_index(self) -> int | None:
        return self._named_index("sinks", self.spec.speaker_sink)

    def mic_index(self) -> int | None:
        return self._named_index("sources", self.spec.mic_source)

    def _loaded(self) -> bool:
        """True when the three nodes exist in the server, regardless of session state."""
        return self.sink_index() is not None and self.mic_index() is not None

    def status(self) -> AudioStatus:
        session = self._store.load()
        scan = self._scan()
        loaded = self._loaded()
        current_sink = self.get_default_sink()
        current_source = self.get_default_source()
        return AudioStatus(
            running=loaded,
            speaker_sink=self.spec.speaker_sink,
            mic_sink=self.spec.mic_sink,
            mic_source=self.spec.mic_source,
            modules={role: module.index for role, module in scan.found.items()},
            current_default_sink=current_sink,
            current_default_source=current_source,
            previous_default_sink=session.previous_default_sink if session else None,
            previous_default_source=session.previous_default_source if session else None,
            routed=current_sink == self.spec.speaker_sink,
            sink_inputs=self._streams("sink-inputs") if loaded else (),
            source_outputs=self._streams("source-outputs") if loaded else (),
        )

    # -- lifecycle ---------------------------------------------------------------

    def up(self) -> AudioSession:
        """Create the plane and record how to undo it.

        Idempotent: a second call adopts the existing modules instead of stacking a second
        set, because two null sinks holding the default is worse than one.
        """
        existing = self._store.load()
        scan = self._scan()
        if existing is not None and self._loaded():
            # Already up. Re-derive module indices: they change across reboots, so the
            # recorded ones can be stale even though the plane itself is fine.
            modules = {role: m.index for role, m in scan.found.items()}
            if modules and modules != existing.modules:
                existing.modules = modules
                self._store.save(existing)
            return existing

        if existing is not None:
            # Session recorded but the modules are gone (server restart, manual unload).
            # The recorded defaults are still the ones to restore to; keep them.
            previous_sink = existing.previous_default_sink
            previous_source = existing.previous_default_source
            routed = existing.routed
        else:
            # Read the defaults BEFORE changing anything: this copy is the only one.
            previous_sink = self.get_default_sink()
            previous_source = self.get_default_source()
            routed = None

        if previous_sink == self.spec.speaker_sink or previous_source == self.spec.mic_source:
            # The operator's default already points at our name with no session recorded.
            # "previous" would then be our own device, so restoring it would be a no-op
            # that leaves them muted. Refuse rather than record a lie.
            raise Unconfigured(
                f"the default sink/source already points at {self.spec.speaker_sink!r}/"
                f"{self.spec.mic_source!r} but no session is recorded; unload it by hand "
                "(pactl unload-module <index>) or run `mycord-zoom audio recover`"
            )

        session = AudioSession(
            speaker_sink=self.spec.speaker_sink,
            mic_sink=self.spec.mic_sink,
            mic_source=self.spec.mic_source,
            modules={},
            previous_default_sink=previous_sink,
            previous_default_source=previous_source,
            routed=routed,
            description=self.spec.description,
        )
        # Persist the defaults first: a SIGKILL after this line still leaves a file that
        # `recover` can act on.
        self._store.save(session)

        loaded: list[tuple[str, int]] = []
        try:
            loaded.append(
                (
                    ROLE_SPEAKER,
                    self._load_module(
                        "module-null-sink",
                        f"sink_name={self.spec.speaker_sink}",
                        f"device.description={self.spec.description} speaker",
                        "rate=48000",
                        _LOAD_MARKER,
                    ),
                )
            )
            session.modules = dict(loaded)
            self._store.save(session)

            loaded.append(
                (
                    ROLE_MIC_SINK,
                    self._load_module(
                        "module-null-sink",
                        f"sink_name={self.spec.mic_sink}",
                        f"device.description={self.spec.description} mic carrier",
                        "rate=48000",
                        _LOAD_MARKER,
                    ),
                )
            )
            session.modules = dict(loaded)
            self._store.save(session)

            loaded.append(
                (
                    ROLE_MIC_SOURCE,
                    self._load_module(
                        "module-remap-source",
                        f"master={self.spec.mic_sink}.monitor",
                        f"source_name={self.spec.mic_source}",
                        f"device.description={self.spec.description} microphone",
                        _LOAD_MARKER,
                    ),
                )
            )
            session.modules = dict(loaded)
            self._store.save(session)
            # Start the speaker sink's graph now, so the first capture does not have to.
            self.prime()
        except BaseException:
            # Undo the half-built plane. The defaults were never changed, so nothing needs
            # restoring — but any module that made it in must go, or the operator inherits
            # dead devices.
            for _role, index in reversed(loaded):
                self._try("unload-module", str(index))
            if not self._loaded():
                self._store.clear()
            raise
        return session

    def route(self, direction: str) -> AudioSession:
        """Point audio at the plane (`to-agent`) or back at the operator (`to-mixer`)."""
        session = self._store.load()
        if session is None:
            raise Unconfigured(
                "no audio plane is recorded; run `mycord-zoom audio up` first (routing "
                "without a recorded previous default would leave no way back)"
            )
        if not self._loaded():
            raise Unconfigured(
                "the plane is recorded but missing from the server; run "
                "`mycord-zoom audio down` then `up`, or `mycord-zoom audio recover`"
            )

        if direction == "to-agent":
            self._run("set-default-sink", self.spec.speaker_sink)
            self._run("set-default-source", self.spec.mic_source)
            session.routed = "to-agent"
        elif direction == "to-mixer":
            if session.previous_default_sink:
                self._run("set-default-sink", session.previous_default_sink)
            if session.previous_default_source:
                self._run("set-default-source", session.previous_default_source)
            session.routed = "to-mixer"
        else:
            raise AudioError(f"unknown routing direction {direction!r}")
        self._store.save(session)
        return session

    def leave_streams(self) -> dict[str, object]:
        """Move any live stream that is *already* on a plane device back to the operator.

        ``set-default-*`` only affects new streams. A Zoom call that started while routed
        keeps playing into the plane after ``route --to-mixer``, which would look exactly
        like the muted machine this module exists to prevent. So teardown also re-points
        running streams by name — the operation verified in the determination
        (``pactl move-sink-input`` / ``move-source-output``, both rc=0 live).
        """
        session = self._store.load()
        target_sink = (session.previous_default_sink if session else None) or None
        target_source = (session.previous_default_source if session else None) or None
        moved: list[str] = []
        sink_index = self.sink_index() if target_sink else None
        if sink_index is not None and target_sink:
            for stream in self._streams("sink-inputs"):
                if stream.target == sink_index and self._try(
                    "move-sink-input", str(stream.index), target_sink
                ):
                    moved.append(f"sink-input {stream.index} -> {target_sink}")
        mic_index = self.mic_index() if target_source else None
        if mic_index is not None and target_source:
            for stream in self._streams("source-outputs"):
                if stream.target == mic_index and self._try(
                    "move-source-output", str(stream.index), target_source
                ):
                    moved.append(f"source-output {stream.index} -> {target_source}")
        return {"moved": moved}

    def down(self) -> dict[str, object]:
        """Restore the operator's defaults and unload the plane. Safe to call repeatedly.

        Order matters: **restore the defaults first**, so even if unloading fails the
        operator is already hearing their own audio again.
        """
        session = self._store.load()
        scan = self._scan()
        if session is None and not scan.found:
            return {
                "changed": False,
                "reason": "no plane recorded and none loaded",
                "restored": [],
                "problems": [],
                "default_sink": self._optional_default("sink"),
                "default_source": self._optional_default("source"),
                "plane_loaded": self._loaded(),
            }

        restored: list[str] = []
        problems: list[str] = []

        previous_sink = session.previous_default_sink if session else None
        previous_source = session.previous_default_source if session else None

        # Streams first: they are the ones a listener actually notices.
        streams = self.leave_streams()
        restored.extend(streams["moved"])  # type: ignore[arg-type]

        if previous_sink and self._optional_default("sink") == self.spec.speaker_sink:
            if self._try("set-default-sink", previous_sink):
                restored.append(f"default-sink -> {previous_sink}")
            else:
                problems.append(f"could not set default sink back to {previous_sink}")
        if previous_source and self._optional_default("source") == self.spec.mic_source:
            if self._try("set-default-source", previous_source):
                restored.append(f"default-source -> {previous_source}")
            else:
                problems.append(f"could not set default source back to {previous_source}")

        # Unload recorded indices first, then anything our scan found by name, so a plane
        # adopted from an earlier run is cleaned up too. Remap-source before its master.
        indices: list[int] = []
        if session is not None:
            for role in (ROLE_MIC_SOURCE, ROLE_MIC_SINK, ROLE_SPEAKER):
                index = session.modules.get(role)
                if index and index > 0 and index not in indices:
                    indices.append(index)
        for role in (ROLE_MIC_SOURCE, ROLE_MIC_SINK, ROLE_SPEAKER):
            module = scan.found.get(role)
            if module and module.index not in indices:
                indices.append(module.index)
        for index in indices:
            if self._try("unload-module", str(index)):
                restored.append(f"unloaded module {index}")
            else:
                problems.append(f"could not unload module {index}")

        # Anything still matching by name means our parsing missed it; sweep it.
        for module in self.modules():
            for module_name, needle in (
                ("module-remap-source", f"source_name={self.spec.mic_source}"),
                ("module-null-sink", f"sink_name={self.spec.mic_sink}"),
                ("module-null-sink", f"sink_name={self.spec.speaker_sink}"),
            ):
                if (
                    module.name == module_name
                    and needle in module.argument
                    and self._try("unload-module", str(module.index))
                ):
                    restored.append(f"unloaded stray module {module.index}")
        # Only forget the session once teardown has actually finished. Keeping it on a
        # partial failure is what lets `recover` retry the unload — and what keeps the
        # operator's original defaults from being lost for good.
        if not problems:
            self._store.clear()

        result: dict[str, object] = {
            "changed": bool(restored),
            "restored": restored,
            "problems": problems,
            "default_sink": self._optional_default("sink"),
            "default_source": self._optional_default("source"),
            "plane_loaded": self._loaded(),
        }
        if problems:
            raise AudioError("teardown incomplete: " + "; ".join(problems))
        return result

    def recover(self) -> dict[str, object]:
        """Finish a teardown interrupted by SIGKILL or a reboot."""
        if self._store.load() is None and not self._scan().found:
            return {"changed": False, "reason": "nothing to recover"}
        return self.down()

    def _optional_default(self, kind: str) -> str:
        try:
            return self.get_default_sink() if kind == "sink" else self.get_default_source()
        except AudioError:
            return ""

    # -- PCM ---------------------------------------------------------------------

    def mic_command(self) -> tuple[str, ...]:
        """argv that plays raw PCM **into the virtual microphone** from stdin.

        Note the *absence* of a file operand: `pacat` treats ``-`` as a literal filename and
        fails with ``open(): No such file or directory``. Omitting the argument entirely is
        what makes it read raw PCM from stdin (verified on this host: rc=0, captured peak
        12000 for a 12000-amplitude tone).
        """
        pacat = self._tool("pacat", "speaking into the virtual microphone")
        return (
            pacat,
            f"--device={self.spec.mic_sink}",
            f"--format={SAMPLE_FORMAT}",
            f"--rate={SAMPLE_RATE}",
            f"--channels={CHANNELS}",
            "--raw",
        )

    def wav_command(self, path: Path) -> tuple[str, ...]:
        """argv that plays a **WAV file** into the virtual microphone.

        Passing the WAV through unchanged is deliberate: `pacat` converts 22050 Hz mono to
        the sink's rate itself, so there is no ffmpeg on the speech path at all.
        """
        pacat = self._tool("pacat", "speaking into the virtual microphone")
        return (pacat, f"--device={self.spec.mic_sink}", "--file-format=wav", str(path))

    def speaker_command(self) -> tuple[str, ...]:
        """argv that records raw PCM **from the virtual speaker** onto stdout."""
        parec = self._tool("parec", "listening to the virtual speaker")
        return (
            parec,
            f"--device={self.spec.speaker_sink}.monitor",
            f"--format={SAMPLE_FORMAT}",
            f"--rate={SAMPLE_RATE}",
            f"--channels={CHANNELS}",
        )

    def open_mic(self, *, spawn: Spawner) -> PacketSink:
        """Start `pacat`, feeding the virtual microphone."""
        process = spawn(self.mic_command())
        if process.stdin is None:  # pragma: no cover - spawn always requests a pipe
            raise AudioError("could not open a pipe into pacat")
        return process.stdin

    def play_wav(self, clip_bytes: bytes) -> None:
        """Write a WAV payload to a temp file and play it into the microphone."""
        with tempfile.TemporaryDirectory(prefix="mycord-zoom-say-") as tmp:
            path = Path(tmp) / "say.wav"
            path.write_bytes(clip_bytes)
            result = self._runner(list(self.wav_command(path)), timeout=120.0)
        if not result.ok:
            raise AudioError(f"could not play into the virtual microphone: {result.message}")

    def prime(self, *, seconds: float = 0.3) -> None:
        """Push a little silence through the speaker sink so its monitor produces frames.

        Puts the sink into RUNNING so a client attaching later (a conferencing app, or the
        agent's own recorder) finds a live graph. Note this does **not** remove the ~2 s
        recorder startup measured in `RECORDER_SETTLE_S` — priming changes the sink's state,
        not `parec`'s startup — so `capture` handles that separately by settling first.
        """
        pacat = self._tool("pacat", "priming the virtual speaker")
        argv = [
            pacat,
            f"--device={self.spec.speaker_sink}",
            f"--format={SAMPLE_FORMAT}",
            f"--rate={SAMPLE_RATE}",
            f"--channels={CHANNELS}",
            "--raw",
        ]
        payload = b"\x00\x00" * int(SAMPLE_RATE * seconds)
        try:
            self._runner(list(argv), timeout=30.0)  # discover a missing pacat early
        except Exception:  # noqa: BLE001 - priming is best effort
            return
        subprocess.run(argv, input=payload, capture_output=True, check=False, timeout=30)
        time.sleep(0.2)

    def capture(self, *, seconds: float, capture: CaptureRunner | None = None) -> bytes:
        """Record `seconds` **of audio** from the virtual speaker's monitor.

        Five details are easy to get wrong, and all five were at some point:

        * the payload is **binary** PCM, so it must never pass through a text-mode pipe;
        * `parec` never exits by itself, so a deadline has to end the capture;
        * ``subprocess.run(timeout=…)`` **discards stdout when it times out**, so it cannot be
          used at all — the audio read up to the deadline is the result;
        * a recorder takes about ``RECORDER_SETTLE_S``-worth (~2 s, measured) to deliver its
          first byte, so the deadline must start at the first byte, not at process spawn.
          Otherwise every short capture returns nothing and looks like silence;
        * a sink whose graph has never run can yield zero bytes at all (see `prime`), so an
          empty attempt is retried once after priming.

        Wall-clock cost is therefore roughly ``startup + seconds``; `--seconds` is honest
        about the audio, not about the runtime.
        """
        if seconds <= 0:
            raise AudioError("capture duration must be positive")
        argv = list(self.speaker_command())
        if capture is not None:
            # Test seam: a runner that returns the whole payload immediately.
            code, data, stderr = capture(argv, timeout=seconds)
            if not data:
                raise AudioError(f"the recorder produced no audio (exit {code}) {stderr[:160]}")
            return data

        data, settled = self._read_for(argv, seconds)
        if not data:
            self.prime()
            data, settled = self._read_for(argv, seconds)
        if not data:
            reason = (
                f"the recorder never delivered a first byte within {RECORDER_SETTLE_S:g}s"
                if not settled
                else f"the recorder delivered no audio within {seconds:g}s of starting"
            )
            raise AudioError(
                f"{reason}, even after priming the sink. Check that the audio server is "
                f"running and that audio is reaching {self.spec.speaker_sink}."
            )
        return data

    def _read_for(self, argv: list[str], seconds: float) -> tuple[bytes, bool]:
        """Spawn a recorder, wait for its first byte, then read for `seconds`.

        Returns ``(pcm, settled)`` where ``settled`` says whether a first byte ever arrived;
        the caller needs that to tell "startup failed" from "started but produced nothing".
        """
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise NotInstalled(f"{argv[0]}: not found on PATH") from exc
        assert process.stdout is not None
        assert process.stderr is not None
        chunks: list[bytes] = []
        started: float | None = None
        give_up = time.monotonic() + RECORDER_SETTLE_S
        try:
            while True:
                now = time.monotonic()
                if started is None:
                    budget = give_up - now
                    if budget <= 0:
                        break
                else:
                    budget = started + seconds - now
                    if budget <= 0:
                        break
                ready, _, _ = select.select([process.stdout], [], [], min(budget, 0.2))
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                if started is None:
                    # The clock for `seconds` starts here: this is the first real audio.
                    started = time.monotonic()
                chunks.append(chunk)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.read()
        return b"".join(chunks), started is not None


def _is_int(value: str) -> bool:
    stripped = value.strip()
    return stripped.lstrip("-").isdigit()


@contextlib.contextmanager
def temporary_plane(plane: AudioPlane) -> Iterator[AudioPlane]:
    """`up`, yield, and guarantee `down` — on exception and on SIGTERM/SIGHUP."""
    with _TerminationGuard():
        plane.up()
        try:
            yield plane
        finally:
            plane.down()
