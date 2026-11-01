"""Live tests against the real PipeWire/PulseAudio server.

These are marked `live` and are **excluded from the default `make test`**, because they
create real devices on the operator's machine. They are safe to run because every one of
them asserts the restoration in the same test that made the change:

* the operator's defaults are captured *before* anything is created, and asserted equal
  afterwards;
* no module carrying this package's marker may survive;
* a failure in one test still restores, because each test uses the same `live_plane`
  fixture, whose teardown runs `down` and re-asserts the defaults.

Run with `make test-live`. They skip cleanly when there is no audio server.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from mycord_zoom.audio import (
    SAMPLE_RATE,
    AudioPlane,
    PlaneSpec,
)
from mycord_zoom.state import StateStore

pytestmark = pytest.mark.live

MARKER = "mycord_zoom_marker"


def _pactl() -> str | None:
    for candidate in ("/usr/bin/pactl", "/usr/local/bin/pactl"):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _server_available() -> bool:
    pactl = _pactl()
    if pactl is None:
        return False
    result = subprocess.run([pactl, "info"], capture_output=True, text=True, check=False)
    return result.returncode == 0


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _server_available(), reason="no PulseAudio/PipeWire server reachable"),
]


def pactl(*args: str) -> str:
    binary = _pactl()
    assert binary is not None
    result = subprocess.run([binary, *args], capture_output=True, text=True, check=False)
    return result.stdout


def defaults() -> tuple[str, str]:
    binary = _pactl()
    assert binary is not None
    sink = subprocess.run(
        [binary, "get-default-sink"], capture_output=True, text=True, check=False
    ).stdout.strip()
    source = subprocess.run(
        [binary, "get-default-source"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return sink, source


def our_modules() -> list[str]:
    return [line for line in pactl("list", "short", "modules").splitlines() if MARKER in line]


@pytest.fixture()
def live_plane(tmp_path: Path) -> Iterator[tuple[AudioPlane, tuple[str, str]]]:
    """A real plane on the real server, with the operator's defaults as the oracle."""
    original = defaults()
    spec = PlaneSpec(
        speaker_sink="mycord_zoom_live_speaker",
        mic_sink="mycord_zoom_live_mic",
        mic_source="mycord_zoom_live_microphone",
    )
    plane = AudioPlane(StateStore(tmp_path / "state"), spec=spec)
    plane.up()
    try:
        yield plane, original
    finally:
        plane.down()
        # The invariant, checked even when the test body failed.
        assert defaults() == original, "the operator's defaults were not restored"
        assert our_modules() == [], "a module from this package survived teardown"


def tone_pcm(seconds: float, *, amplitude: int = 20000, freq: float = 440.0) -> bytes:
    frames = int(seconds * SAMPLE_RATE)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)))
        for i in range(frames)
    )

    """Run a capture/playback pair up to `attempts` times, returning each peak.

    A live audio graph has an inherent attach race: a stream can be accepted by the server
    before the node it feeds is running, and the first attempt then records silence. Retrying
    measures the capability instead of one unlucky scheduling outcome — and the assertion
    still fails if the signal never arrives.
    """
    return []


def peak(pcm: bytes) -> int:
    usable = len(pcm) - (len(pcm) % 2)
    if not usable:
        return 0
    return max(abs(sample) for (sample,) in struct.iter_unpack("<h", pcm[:usable]))


# --------------------------------------------------------------------------- the tests


def test_up_creates_real_devices_and_down_removes_them(
    live_plane: tuple[AudioPlane, tuple[str, str]],
) -> None:
    plane, original = live_plane
    status = plane.status()
    assert status.running is True
    assert defaults() == original, "`up` must not move the operator's audio on its own"

    # The real server lists all three nodes.
    sinks = pactl("list", "short", "sinks")
    sources = pactl("list", "short", "sources")
    assert plane.spec.speaker_sink in sinks
    assert plane.spec.mic_sink in sinks
    assert plane.spec.mic_source in sources


def _record_while_playing(
    plane: AudioPlane, *, record_from: str, play_into: str, seconds: float = 0.5
) -> bytes:
    """Play a tone into one node while recording another, using the plane's own argv.

    Recording goes to a temp **file** rather than a pipe: `parec` writes the WAV header and
    all audio to a file without needing a reader attached early, which removes one of the
    two attach races from the measurement.
    """
    import tempfile
    import time

    pcm = tone_pcm(seconds)
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as handle:
        path = handle.name
    try:
        with open(path, "wb") as sink:
            recorder = subprocess.Popen(
                [
                    plane._tool("parec", "test"),  # noqa: SLF001 - the plane's own resolution
                    f"--device={record_from}",
                    "--raw",
                    "--format=s16le",
                    f"--rate={SAMPLE_RATE}",
                    "--channels=1",
                ],
                stdout=sink,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.4)
            subprocess.run(
                [
                    plane._tool("pacat", "test"),  # noqa: SLF001
                    f"--device={play_into}",
                    "--format=s16le",
                    f"--rate={SAMPLE_RATE}",
                    "--channels=1",
                    "--raw",
                ],
                input=pcm,
                check=True,
                timeout=30,
            )
            time.sleep(1.0)
            recorder.terminate()
            recorder.wait(timeout=10)
        return Path(path).read_bytes()
    finally:
        Path(path).unlink(missing_ok=True)


def _best_peak(plane: AudioPlane, *, record_from: str, play_into: str, attempts: int = 3) -> int:
    """Measure the loudest of up to `attempts` runs.

    A live audio graph has an inherent attach race: a stream can be accepted before the node
    it feeds is running, and that attempt then records silence. Retrying measures the
    capability rather than one scheduling outcome — the assertion still fails if the signal
    never arrives.
    """
    peaks: list[int] = []
    for _ in range(attempts):
        peaks.append(
            peak(_record_while_playing(plane, record_from=record_from, play_into=play_into))
        )
        if peaks[-1] > 15000:
            break
    return max(peaks)


def test_audio_played_into_the_mic_sink_arrives_at_the_mic_source(
    live_plane: tuple[AudioPlane, tuple[str, str]],
) -> None:
    """The outbound path: what the agent "says" is what Zoom would record."""
    plane, _ = live_plane
    measured = _best_peak(plane, record_from=plane.spec.mic_source, play_into=plane.spec.mic_sink)
    print(f"mic remap path peak: {measured}")
    assert measured > 15000, f"the remap path lost the signal (peak {measured})"


def test_audio_landing_on_the_speaker_sink_is_capturable(
    live_plane: tuple[AudioPlane, tuple[str, str]],
) -> None:
    """The inbound path: what Zoom "hears" is what the agent captures."""
    plane, _ = live_plane
    measured = _best_peak(
        plane, record_from=f"{plane.spec.speaker_sink}.monitor", play_into=plane.spec.speaker_sink
    )
    print(f"speaker monitor path peak: {measured}")
    assert measured > 15000, f"the monitor path lost the signal (peak {measured})"


def test_capture_with_no_signal_raises_instead_of_reporting_silence(
    live_plane: tuple[AudioPlane, tuple[str, str]],
) -> None:
    """A recorder that produces nothing must be an error, not an empty capture.

    Silence and a failed recorder are indistinguishable once decoded, so the distinction has
    to be made on byte count — otherwise a broken audio path looks like a quiet room.
    """
    plane, _ = live_plane
    # 2 s, not 0.5 s: on PipeWire a sink nothing is writing to can return no frames at all in
    # a very short window (measured 0 bytes at 0.5 s, 65536 at 2.0 s for the same sink).
    pcm = plane.capture(seconds=2.0)
    assert pcm, "capture returned no bytes; it must raise in that case"
    assert peak(pcm) == 0, "nothing was playing, so the capture should be pure silence"


def test_piper_speech_reaches_the_microphone(
    live_plane: tuple[AudioPlane, tuple[str, str]],
) -> None:
    """End to end with the real TTS: Piper -> pacat -> remap-source, measured."""
    from mycord_zoom import speech

    plane, _ = live_plane
    if speech.find_piper() is None:
        pytest.skip("piper is not installed")
    tts = speech.PiperTts()
    try:
        clip = tts.synthesize("The agent is speaking through the virtual microphone.")
    except Exception as exc:  # noqa: BLE001 - a missing voice is a skip, not a failure
        pytest.skip(f"piper could not synthesise: {exc}")

    import tempfile
    import time

    best = 0
    for _ in range(3):
        with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as handle:
            path = handle.name
        with open(path, "wb") as sink:
            recorder = subprocess.Popen(
                [
                    plane._tool("parec", "test"),  # noqa: SLF001
                    f"--device={plane.spec.mic_source}",
                    "--raw",
                    "--format=s16le",
                    f"--rate={SAMPLE_RATE}",
                    "--channels=1",
                ],
                stdout=sink,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.4)
            plane.play_wav(clip.to_wav_bytes())
            time.sleep(1.0)
            recorder.terminate()
            recorder.wait(timeout=10)
        best = max(best, peak(Path(path).read_bytes()))
        Path(path).unlink(missing_ok=True)
        if best > 3000:
            break

    print(f"piper clip: {clip.duration_s:.2f}s; mic capture peak {best}")
    assert best > 3000, f"speech did not reach the mic source (peak {best})"


def test_teardown_restores_the_operator_defaults_exactly() -> None:
    """The most important test in the suite, asserted on the real server."""
    original = defaults()
    spec = PlaneSpec(
        speaker_sink="mycord_zoom_restore_speaker",
        mic_sink="mycord_zoom_restore_mic",
        mic_source="mycord_zoom_restore_microphone",
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plane = AudioPlane(StateStore(Path(tmp) / "state"), spec=spec)
        plane.up()
        plane.route("to-agent")
        assert defaults() == (spec.speaker_sink, spec.mic_source), "routing did not take"

        plane.down()

        restored = defaults()
        assert restored == original, f"expected {original}, got {restored}"
        assert our_modules() == []
        assert spec.speaker_sink not in pactl("list", "short", "sinks")
        assert spec.mic_source not in pactl("list", "short", "sources")


def test_teardown_after_an_exception_still_restores() -> None:
    """A crash inside a plane-scoped operation must not leave the operator muted.

    This exercises `temporary_plane`, which is the production guarantee: `up`, then `down` in
    a `finally`, so an exception anywhere in the body still restores the routing.
    """
    from mycord_zoom.audio import temporary_plane

    original = defaults()
    spec = PlaneSpec(
        speaker_sink="mycord_zoom_crash_speaker",
        mic_sink="mycord_zoom_crash_mic",
        mic_source="mycord_zoom_crash_microphone",
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plane = AudioPlane(StateStore(Path(tmp) / "state"), spec=spec)
        with pytest.raises(RuntimeError, match="simulated crash"), temporary_plane(plane):
            plane.route("to-agent")
            assert defaults() == (spec.speaker_sink, spec.mic_source)
            raise RuntimeError("simulated crash")

        # The `finally` in `temporary_plane` restored, even though the body raised.
        assert defaults() == original
        assert our_modules() == []


def test_short_capture_returns_audio_not_startup_latency(
    live_plane: tuple[AudioPlane, tuple[str, str]],
) -> None:
    """`--seconds 0.5` must return ~0.5 s of audio, not nothing.

    Regression test for a real bug: `parec` takes ~2 s (measured, four trials) to deliver its
    first byte, and the original capture deadline started at process spawn. Any capture
    shorter than the startup latency therefore returned zero bytes — indistinguishable from
    "nobody spoke", which is the one thing an agent must not get wrong while listening.
    """
    plane, _ = live_plane
    for seconds in (0.5, 1.0):
        data = plane.capture(seconds=seconds)
        frames = len(data) // 2
        audio_duration = frames / SAMPLE_RATE
        assert audio_duration >= seconds * 0.8, (
            f"--seconds {seconds} returned {audio_duration:.2f}s of audio; the recorder's "
            "startup latency is being charged against the capture window"
        )
