"""Speech: Piper for talking, energy VAD plus a local ASR worker for listening.

No model is reimplemented and nothing is downloaded. This module shells out to the same
things ``gvoice/`` uses, discovered the same way:

===========================  =========================================
Concern                      Where it comes from
===========================  =========================================
TTS binary/voice             ``$PIPER_DIR/piper -m <onnx> -c <json> -f out.wav``
TTS default location         ``PIPER_DIR`` else ``~/.local/share/piper``
TTS voice                    ``en_US-amy-medium`` (gvoice's default)
ASR worker                   ``gvoice/tools/{parakeet,whisper}_worker.py``
ASR protocol                 ``{"wav": path}`` per line in, JSON out
ASR interpreter              ``GVOICE_*_VENV`` else ``~/.local/share/gvoice/*``
===========================  =========================================

A missing model raises `NotInstalled` (exit code 2) with the expected path in the message.
Nothing here touches the network: downloading multi-gigabyte weights as a side effect of
"say hello" is forbidden, and an agent that silently stalls on a download mid-call is worse
than one that fails at once.

The VAD is a port of ``gvoice/src/vad.ts`` — hysteresis, adaptive noise floor, hangover —
so ``listen`` works (and is tested) on a machine with no ASR installed at all.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from .errors import NotInstalled, SpeechError
from .tools import Runner, run_command

#: Every speech model here works at 16 kHz mono; the capture side runs at 48 kHz.
ASR_RATE = 16000

#: 20 ms frames at 48 kHz, matching gvoice's wire format so the VAD port is 1:1.
FRAME_SAMPLES = 960
FRAME_RATE = 48000

#: Peak target for synthesised speech: Piper renders at full scale and clips on the way
#: out, which is why gvoice normalises to 0.7 for the same reason.
TTS_PEAK = 0.7

#: How long to wait for a worker's ``{"ready": true}`` line before giving up on its model.
WORKER_READY_TIMEOUT = 180.0


class PacketSink(Protocol):
    """Something PCM can be written to, i.e. the virtual microphone."""

    def write(self, data: bytes) -> int: ...


# ---------------------------------------------------------------- 16-bit PCM helpers


def iter_i16(pcm: bytes) -> Iterator[int]:
    """Iterate little-endian signed 16-bit samples (mono, interleaved passed through)."""
    usable = len(pcm) - (len(pcm) % 2)
    for offset in range(0, usable, 2):
        yield int.from_bytes(pcm[offset : offset + 2], "little", signed=True)


def max_amplitude(pcm: bytes) -> int:
    """Peak absolute sample. Proof that a capture carried signal, not just bytes."""
    peak = 0
    for sample in iter_i16(pcm):
        magnitude = -sample if sample < 0 else sample
        if magnitude > peak:
            peak = magnitude
    return peak


def rms(pcm: bytes) -> float:
    """Root-mean-square level, normalised to 0..1."""
    count = len(pcm) // 2
    if count == 0:
        return 0.0
    total = 0
    for offset in range(0, count * 2, 2):
        sample = int.from_bytes(pcm[offset : offset + 2], "little", signed=True)
        total += sample * sample
    return math.sqrt(total / count) / 32768.0


def downmix(pcm: bytes, channels: int) -> bytes:
    """Average interleaved channels down to mono; the VAD only needs one."""
    if channels <= 1:
        return pcm
    stride = channels * 2
    out = bytearray()
    for offset in range(0, len(pcm) - stride + 1, stride):
        total = 0
        for channel in range(channels):
            start = offset + channel * 2
            total += int.from_bytes(pcm[start : start + 2], "little", signed=True)
        mixed = int(total / channels)
        out += mixed.to_bytes(2, "little", signed=True)
    return bytes(out)


def normalize(pcm: bytes, peak_target: float) -> bytes:
    """Scale so the loudest sample sits at `peak_target` of full scale."""
    peak = max_amplitude(pcm)
    if peak == 0:
        return pcm
    scale = (peak_target * 32767.0) / peak
    if 0.999 <= scale <= 1.001:
        return pcm
    out = bytearray()
    for sample in iter_i16(pcm):
        scaled = int(max(-32768, min(32767, round(sample * scale))))
        out += scaled.to_bytes(2, "little", signed=True)
    return bytes(out)


# --------------------------------------------------------------------------- WAV


@dataclass(frozen=True)
class WavClip:
    """A decoded 16-bit PCM WAV, kept as raw frames plus its format."""

    data: bytes
    sample_rate: int
    channels: int
    sample_width: int = 2

    @property
    def frames(self) -> int:
        stride = self.channels * self.sample_width
        return len(self.data) // stride if stride else 0

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def peak(self) -> int:
        return max_amplitude(self.data)

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width_bits": self.sample_width * 8,
            "frames": self.frames,
            "duration_s": round(self.duration_s, 3),
            "peak": self.peak(),
        }

    def to_wav_bytes(self) -> bytes:
        """Re-encode as a self-describing WAV, for `pacat --file-format=wav`."""
        import io

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(self.channels)
            handle.setsampwidth(self.sample_width)
            handle.setframerate(self.sample_rate)
            handle.writeframes(self.data)
        return buffer.getvalue()


def read_wav(raw: bytes) -> WavClip:
    """Parse a WAV payload held in memory."""
    import io

    with wave.open(io.BytesIO(raw), "rb") as handle:
        return WavClip(
            data=handle.readframes(handle.getnframes()),
            sample_rate=handle.getframerate(),
            channels=handle.getnchannels(),
            sample_width=handle.getsampwidth(),
        )


def write_wav(path: Path, clip: WavClip) -> Path:
    """Write a clip to `path` as a 16-bit WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(clip.to_wav_bytes())
    return path


# --------------------------------------------------------------------------- Piper TTS


def piper_dir() -> Path:
    """`PIPER_DIR`, else gvoice's default. One function so tests can patch one place."""
    return Path(os.environ.get("PIPER_DIR") or Path.home() / ".local" / "share" / "piper")


def find_piper() -> tuple[str, Path] | None:
    """`(piper binary, install dir)`, or None. Mirrors `PiperTts` in gvoice."""
    directory = piper_dir()
    binary = directory / "piper"
    if binary.is_file() and os.access(binary, os.X_OK):
        return str(binary), directory
    on_path = shutil.which("piper")
    return (on_path, Path(on_path).parent) if on_path else None


class PiperTts:
    """Piper, invoked exactly as ``gvoice/src/tts.ts`` does.

    Piper is a single ONNX model on CPU and synthesises several times faster than real
    time, so it adds no perceptible latency to a live call. Its CLI ignores positional
    text, so the text goes in on stdin — the same reason gvoice uses stdin.

    Unlike gvoice, this keeps the WAV intact instead of converting to 48 kHz PCM: the
    destination is ``pacat``, which converts to the sink's rate itself. That removes
    ffmpeg from the speech path entirely (verified: a 22050 Hz mono WAV played into a
    48 kHz sink arrives at the monitor with its peak preserved).
    """

    def __init__(
        self,
        *,
        voice: str | None = None,
        runner: Runner = run_command,
        length_scale: float = 1.0,
        peak_target: float = TTS_PEAK,
    ) -> None:
        self.voice = voice or os.environ.get("MYCORD_ZOOM_PIPER_VOICE") or "en_US-amy-medium"
        self._runner = runner
        self.length_scale = length_scale
        self.peak_target = peak_target

    def require(self) -> tuple[str, Path, Path]:
        """Resolve binary, model and config, or raise `NotInstalled` naming the path."""
        found = find_piper()
        if found is None:
            raise NotInstalled(
                f"piper is not installed; expected {piper_dir() / 'piper'} or `piper` on "
                "PATH. Install Piper or set PIPER_DIR to use `audio say`."
            )
        binary, directory = found
        onnx = directory / "voices" / f"{self.voice}.onnx"
        config = directory / "voices" / f"{self.voice}.onnx.json"
        if not onnx.is_file():
            raise NotInstalled(
                f"piper voice {self.voice!r} not found at {onnx}. Download a voice from "
                "https://huggingface.co/rhasspy/piper-voices (en/en_US/amy/medium is the "
                "gvoice default) or set MYCORD_ZOOM_PIPER_VOICE."
            )
        return binary, onnx, config

    def synthesize(self, text: str) -> WavClip:
        binary, onnx, config = self.require()
        stripped = text.strip()
        if not stripped:
            raise SpeechError("refusing to synthesise empty text")
        with tempfile.TemporaryDirectory(prefix="mycord-zoom-tts-") as tmp:
            target = Path(tmp) / "out.wav"
            argv = [
                binary,
                "-m",
                str(onnx),
                "-c",
                str(config),
                "-f",
                str(target),
                "--length_scale",
                str(self.length_scale),
            ]
            # Piper dlopens its bundled espeak/libonnxruntime from its install directory,
            # which is not on the loader path when `piper` was found via PATH.
            existing = os.environ.get("LD_LIBRARY_PATH")
            lib_path = os.pathsep.join(filter(None, [str(onnx.parent.parent), existing]))
            env = {"LD_LIBRARY_PATH": lib_path}
            # Text on stdin, exactly as gvoice does it.
            result = self._run_stdin(argv, stripped, env)
            if not result.ok:
                raise SpeechError(f"piper failed: {result.message}")
            if not target.is_file():
                raise SpeechError(f"piper reported success but wrote no file to {target}")
            clip = read_wav(target.read_bytes())
        if clip.frames == 0:
            raise SpeechError("piper produced an empty clip")
        normalised = normalize(clip.data, self.peak_target)
        return WavClip(
            data=normalised,
            sample_rate=clip.sample_rate,
            channels=clip.channels,
            sample_width=clip.sample_width,
        )

    def _run_stdin(self, argv: list[str], text: str, env: dict[str, str]) -> _Result:
        """Run Piper with text on stdin (the `Runner` protocol has no stdin channel)."""
        import subprocess

        try:
            completed = subprocess.run(
                argv,
                input=text,
                capture_output=True,
                text=True,
                timeout=120.0,
                check=False,
                env={**os.environ, **env},
            )
        except FileNotFoundError as exc:
            raise NotInstalled(f"{argv[0]}: not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise SpeechError("piper timed out after 120s") from exc
        return _Result(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def message(self) -> str:
        for stream in (self.stderr, self.stdout):
            for line in stream.splitlines():
                if line.strip():
                    return line.strip()[:400]
        return f"exit {self.returncode}"


# --------------------------------------------------------------------------- VAD


@dataclass(frozen=True)
class VadOptions:
    """Thresholds, with gvoice's measured defaults."""

    min_rms: float = 0.012
    enter_factor: float = 3.0
    exit_factor: float = 1.6
    hangover_ms: float = 700.0
    min_speech_ms: float = 250.0
    max_utterance_ms: float = 30000.0
    floor_decay: float = 0.995


@dataclass(frozen=True)
class Utterance:
    """One detected burst of speech."""

    pcm: bytes
    duration_ms: float
    peak: int
    level: float

    def to_dict(self) -> dict[str, object]:
        return {
            "duration_ms": round(self.duration_ms, 1),
            "samples": len(self.pcm) // 2,
            "peak": self.peak,
            "rms": round(self.level, 5),
        }


EVENT_START = "speech-start"
EVENT_END = "speech-end"


class Vad:
    """Energy VAD with hysteresis, an adaptive floor, and hangover.

    Ported from ``gvoice/src/vad.ts``, whose docstring records the reasoning and which is
    the implementation proven on real telephone audio:

    * one threshold chatters — every breath starts a turn — so speech must clear a higher
      bar to *enter* than to *stay*;
    * speech does not stop the instant the speaker does, so quiet frames are held for a
      hangover before the utterance closes;
    * the noise floor is tracked adaptively *only while not in speech*, so a long loud
      utterance cannot raise the bar until the detector loses it.
    """

    def __init__(self, options: VadOptions | None = None) -> None:
        self.options = options or VadOptions()
        self._floor = 0.004
        self._in_speech = False
        self._quiet_frames = 0
        self._speech_frames = 0
        self._buffer: list[bytes] = []
        self._last_closed: Utterance | None = None

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def frame_ms(self) -> float:
        return (FRAME_SAMPLES / FRAME_RATE) * 1000.0

    def push(self, frame: bytes) -> str | None:
        """Feed one 20 ms frame; return ``speech-start``, ``speech-end`` or None.

        ``speech-end`` is emitted *after* the closing utterance has been recorded and is
        readable via `take_utterance`, so a caller cannot read the buffer after it has been
        cleared (the bug this method's shape exists to prevent).
        """
        level = rms(frame)
        enter_at = max(self.options.min_rms, self._floor * self.options.enter_factor)
        exit_at = max(self.options.min_rms * 0.7, self._floor * self.options.exit_factor)

        if not self._in_speech:
            self._floor = level * (1 - self.options.floor_decay) + self._floor * (
                self.options.floor_decay
            )
            if level >= enter_at:
                self._in_speech = True
                self._quiet_frames = 0
                self._speech_frames = 1
                self._buffer = [frame]
                return EVENT_START
            return None

        self._buffer.append(frame)
        if level > exit_at:
            self._speech_frames += 1
            self._quiet_frames = 0
        else:
            self._quiet_frames += 1

        quiet_ms = self._quiet_frames * self.frame_ms
        speech_ms = self._speech_frames * self.frame_ms
        if quiet_ms >= self.options.hangover_ms or speech_ms >= self.options.max_utterance_ms:
            self._close()
            return EVENT_END
        return None

    def _close(self) -> None:
        """Freeze the utterance, dropping the hangover tail, then reset the detector."""
        keep = len(self._buffer) - self._quiet_frames
        frames_kept = self._buffer[: max(0, keep)]
        self._in_speech = False
        self._quiet_frames = 0
        self._speech_frames = 0
        self._buffer = []
        if not frames_kept:
            self._last_closed = None
            return
        pcm = b"".join(frames_kept)
        duration_ms = len(frames_kept) * self.frame_ms
        if duration_ms < self.options.min_speech_ms:
            self._last_closed = None
            return
        self._last_closed = Utterance(
            pcm=pcm,
            duration_ms=duration_ms,
            peak=max_amplitude(pcm),
            level=rms(pcm),
        )

    def take_utterance(self) -> Utterance | None:
        """The utterance most recently closed, consumed once."""
        closed, self._last_closed = self._last_closed, None
        return closed

    def flush(self) -> Utterance | None:
        """Close an in-progress utterance at end of capture (no hangover available)."""
        if not self._in_speech or not self._buffer:
            return None
        self._quiet_frames = 0
        self._close()
        return self.take_utterance()


def frames(pcm: bytes, *, frame_samples: int = FRAME_SAMPLES) -> Iterator[bytes]:
    """Split mono 16-bit PCM into fixed frames, discarding a trailing partial frame."""
    stride = frame_samples * 2
    for offset in range(0, len(pcm) - stride + 1, stride):
        yield pcm[offset : offset + stride]


def split_utterances(pcm: bytes, options: VadOptions | None = None) -> list[Utterance]:
    """Run the VAD over a whole buffer (what `listen --seconds` does offline)."""
    detector = Vad(options)
    found: list[Utterance] = []
    for frame in frames(pcm):
        if detector.push(frame) == EVENT_END:
            utterance = detector.take_utterance()
            if utterance is not None:
                found.append(utterance)
    tail = detector.flush()
    if tail is not None:
        found.append(tail)
    return found


# --------------------------------------------------------------------------- ASR


def gvoice_root() -> Path:
    """Where gvoice's workers live.

    ``GVOICE_HOME`` wins; otherwise the repo checkout this package sits beside. That
    second case is why `../gvoice/tools/*_worker.py` is found without any configuration.
    """
    override = os.environ.get("GVOICE_HOME")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "gvoice"


def _venv_python(directory: Path) -> str | None:
    for name in ("python", "python3"):
        candidate = directory / "bin" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


@dataclass(frozen=True)
class AsrBackend:
    """A worker script, the interpreter that can import its model, and what to call it."""

    name: str
    python: str
    worker: Path

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "python": self.python, "worker": str(self.worker)}


def find_asr_backend() -> AsrBackend | None:
    """Locate a usable local ASR worker, the way ``gvoice/src/stt.ts`` does.

    Order is gvoice's: parakeet-redux (ternary weights, ~12x realtime on AVX2) first, then
    whisper. A worker is only usable if *both* its script and an interpreter that can
    import its model exist, so this returns None rather than a worker that will fail on
    its first request.

    **Not verified on this host**: no ASR model is installed (no gvoice venv, empty
    ``~/.cache/whisper``), so detection is covered by tests with a fake backend.
    """
    root = gvoice_root()
    for name, worker_name, env_var, default_dir, module in (
        (
            "parakeet-redux",
            "parakeet_worker.py",
            "GVOICE_PARAKEET_VENV",
            "asr-venv",
            "moondream",
        ),
        ("faster-whisper", "whisper_worker.py", "GVOICE_FW_VENV", "fw-venv", "faster_whisper"),
    ):
        worker = root / "tools" / worker_name
        if not worker.is_file():
            continue
        venv = Path(
            os.environ.get(env_var) or Path.home() / ".local" / "share" / "gvoice" / default_dir
        )
        python = _venv_python(venv)
        if python and _can_import(python, module):
            return AsrBackend(name=name, python=python, worker=worker)
    return None


def _can_import(python: str, module: str) -> bool:
    """Cheap check that `python` can import `module` (no caching: a wrong answer here
    would silently disable a working backend, and the check costs one interpreter start)."""
    return run_command([python, "-c", f"import {module}"], timeout=120.0).ok


def transcribe(clip: WavClip, *, backend: AsrBackend | None = None) -> dict[str, object]:
    """Transcribe one clip by driving the worker's line protocol.

    The workers are persistent by design (model load dominates), but a one-shot call is
    what a CLI verb needs, so this runs the worker, waits for ``{"ready": true}``, sends
    one request, reads one reply, and exits. A long-lived agent should keep the worker —
    that optimisation belongs to the future shared speech service, not here.
    """
    chosen = backend or find_asr_backend()
    if chosen is None:
        raise NotInstalled(
            "no speech-to-text backend available. Install one of: the gvoice ASR venv "
            "(GVOICE_PARAKEET_VENV, default ~/.local/share/gvoice/asr-venv with moondream), "
            "or faster-whisper (GVOICE_FW_VENV). `audio listen` still works without ASR "
            "when --no-transcribe is used."
        )
    import subprocess

    with tempfile.TemporaryDirectory(prefix="mycord-zoom-asr-") as tmp:
        wav_path = write_wav(Path(tmp) / "in.wav", clip)
        argv = [chosen.python, str(chosen.worker)]
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise NotInstalled(f"{chosen.python}: not found") from exc
        try:
            assert process.stdout is not None
            ready = _read_json_line(process.stdout, timeout=WORKER_READY_TIMEOUT)
            if ready is None or not ready.get("ready"):
                raise SpeechError(f"{chosen.name} worker did not become ready: {ready}")
            assert process.stdin is not None
            process.stdin.write(json.dumps({"wav": str(wav_path)}) + "\n")
            process.stdin.flush()
            reply = _read_json_line(process.stdout, timeout=WORKER_READY_TIMEOUT)
            if reply is None:
                raise SpeechError(f"{chosen.name} worker returned nothing")
            if "error" in reply:
                raise SpeechError(f"{chosen.name}: {reply['error']}")
        finally:
            _stop_worker(process)
    text = str(reply.get("text") or "").strip()
    return {
        "backend": chosen.name,
        "text": text,
        "segments": reply.get("segments", []),
    }


def _read_json_line(stream: IO[str], *, timeout: float) -> dict[str, object] | None:
    """Read one JSON line from a worker, or None at EOF or on timeout."""
    import select

    fd = stream.fileno()
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        return None
    raw = stream.readline()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # A worker that logs to stdout before its handshake: skip and report nothing.
        match = re.search(r"\{.*\}", raw)
        if match is None:
            return None
        parsed = json.loads(match.group(0))
    return parsed if isinstance(parsed, dict) else None


def _stop_worker(process: object) -> None:
    """Close stdin then terminate, so a worker never outlives the CLI."""
    import subprocess

    assert isinstance(process, subprocess.Popen)
    try:
        if process.stdin:
            process.stdin.close()
    except (BrokenPipeError, ValueError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


# --------------------------------------------------------------------------- capture


@dataclass(frozen=True)
class CaptureResult:
    """What `audio listen` recorded, with the numbers that prove it carried signal."""

    pcm: bytes
    sample_rate: int
    channels: int
    utterances: tuple[Utterance, ...]
    peak: int

    @property
    def duration_s(self) -> float:
        stride = 2 * self.channels
        return len(self.pcm) / stride / self.sample_rate if self.sample_rate else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bytes": len(self.pcm),
            "duration_s": round(self.duration_s, 3),
            "peak": self.peak,
            "utterances": [u.to_dict() for u in self.utterances],
        }

    def utterance_clip(self, index: int = 0) -> WavClip:
        """One utterance as a mono WAV clip, ready for `transcribe`."""
        utterance = self.utterances[index]
        return WavClip(data=utterance.pcm, sample_rate=self.sample_rate, channels=1)


def analyse_capture(pcm: bytes, *, sample_rate: int, channels: int) -> CaptureResult:
    """Run the VAD over captured PCM and keep every utterance it found."""
    mono = downmix(pcm, channels)
    return CaptureResult(
        pcm=pcm,
        sample_rate=sample_rate,
        channels=channels,
        utterances=tuple(split_utterances(mono)),
        peak=max_amplitude(pcm),
    )
