"""Speech: PCM maths, the VAD port, Piper's absence path, and the ASR worker protocol.

The VAD is tested against synthesised PCM because its behaviour is the thing that decides
whether an agent hears a person or a room: hysteresis and hangover each get a test that
fails if the corresponding rule is dropped.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from mycord_zoom import speech
from mycord_zoom.errors import NotInstalled, SpeechError

pytestmark = pytest.mark.unit


def tone(
    seconds: float, *, freq: float = 440.0, amplitude: int = 12000, rate: int = 48000
) -> bytes:
    frames = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(frames)
    )


def silence(seconds: float, *, rate: int = 48000) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


# --------------------------------------------------------------------------- PCM helpers


def test_max_amplitude_finds_the_peak() -> None:
    assert speech.max_amplitude(struct.pack("<hhh", 0, -30000, 100)) == 30000


def test_max_amplitude_of_empty_is_zero() -> None:
    assert speech.max_amplitude(b"") == 0


def test_rms_of_silence_is_zero() -> None:
    assert speech.rms(silence(0.02)) == 0.0


def test_rms_of_a_full_scale_square_is_near_one() -> None:
    frame = struct.pack("<h", 32767) * 100
    assert speech.rms(frame) > 0.9


def test_downmix_averages_channels() -> None:
    stereo = struct.pack("<hh", 1000, 3000) * 2
    mono = speech.downmix(stereo, 2)
    assert struct.unpack("<hh", mono) == (2000, 2000)


def test_normalize_scales_to_the_target() -> None:
    loud = struct.pack("<hh", 32767, -32768)
    normalised = speech.normalize(loud, 0.7)
    assert 22800 <= speech.max_amplitude(normalised) <= 23000


def test_normalize_leaves_silence_alone() -> None:
    assert speech.normalize(b"\x00\x00\x00\x00", 0.7) == b"\x00\x00\x00\x00"


# --------------------------------------------------------------------------- WAV


def test_wav_round_trip() -> None:
    clip = speech.WavClip(data=tone(0.1), sample_rate=48000, channels=1)
    raw = clip.to_wav_bytes()
    again = speech.read_wav(raw)
    assert again.data == clip.data
    assert again.sample_rate == 48000
    assert again.to_dict()["duration_s"] == pytest.approx(0.1, abs=0.01)


def test_wav_duration_and_peak() -> None:
    clip = speech.WavClip(data=tone(0.5, amplitude=10000), sample_rate=48000, channels=1)
    assert clip.duration_s == pytest.approx(0.5, abs=0.01)
    assert 9500 <= clip.peak() <= 10000


def test_write_wav_creates_the_file(tmp_path: Path) -> None:
    clip = speech.WavClip(data=tone(0.05), sample_rate=16000, channels=1)
    path = speech.write_wav(tmp_path / "nested" / "out.wav", clip)
    assert path.is_file()
    assert speech.read_wav(path.read_bytes()).sample_rate == 16000


# --------------------------------------------------------------------------- VAD


def test_vad_detects_a_burst_inside_silence() -> None:
    pcm = silence(0.4) + tone(1.0) + silence(1.5)
    found = speech.split_utterances(pcm)
    assert len(found) == 1
    # The hangover tail is dropped, so the utterance is a little shorter than the tone.
    assert 800 <= found[0].duration_ms <= 1100
    assert found[0].peak > 9000


def test_vad_ignores_silence_entirely() -> None:
    assert speech.split_utterances(silence(3.0)) == []


def test_vad_rejects_a_click_shorter_than_min_speech() -> None:
    # 100 ms of tone is under min_speech_ms (250), which is what stops a door click from
    # being transcribed as a word.
    pcm = silence(0.3) + tone(0.1) + silence(1.0)
    assert speech.split_utterances(pcm) == []


def test_vad_separates_two_bursts() -> None:
    pcm = tone(0.5) + silence(1.2) + tone(0.5) + silence(1.2)
    found = speech.split_utterances(pcm)
    assert len(found) == 2


def test_vad_does_not_fragment_on_a_short_gap() -> None:
    # A 200 ms gap is inside the 700 ms hangover, so it must not split one sentence in two.
    pcm = silence(0.3) + tone(0.4) + silence(0.2) + tone(0.4) + silence(1.2)
    assert len(speech.split_utterances(pcm)) == 1


def test_vad_hysteresis_does_not_enter_on_a_quiet_frame() -> None:
    detector = speech.Vad()
    quiet = struct.pack("<h", 200) * speech.FRAME_SAMPLES  # far below min_rms
    for _ in range(10):
        assert detector.push(quiet) is None
    assert detector.in_speech is False


def test_vad_emits_end_after_hangover_and_the_utterance_is_readable() -> None:
    detector = speech.Vad()
    events: list[str] = []
    for _ in range(30):  # 600 ms of tone
        event = detector.push(tone(0.02, amplitude=12000))
        if event:
            events.append(event)
    for _ in range(80):  # 1.6 s of silence, well past the 700 ms hangover
        event = detector.push(silence(0.02))
        if event:
            events.append(event)
    assert events == [speech.EVENT_START, speech.EVENT_END]
    utterance = detector.take_utterance()
    assert utterance is not None, "the closing utterance must survive the buffer reset"
    assert utterance.peak > 9000
    assert detector.take_utterance() is None, "an utterance is consumed once"


def test_vad_flush_closes_an_open_utterance() -> None:
    detector = speech.Vad()
    for _ in range(40):
        detector.push(tone(0.02, amplitude=12000))
    tail = detector.flush()
    assert tail is not None
    assert tail.duration_ms > 250


# --------------------------------------------------------------------------- Piper


def test_say_without_piper_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIPER_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(speech.shutil, "which", lambda name: None)
    with pytest.raises(NotInstalled, match="piper is not installed"):
        speech.PiperTts().synthesize("hello")


def test_say_without_the_voice_names_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    piper = tmp_path / "piper-bin" / "piper"
    piper.parent.mkdir(parents=True)
    piper.write_text("#!/bin/sh\n")
    piper.chmod(0o755)
    monkeypatch.setenv("PIPER_DIR", str(piper.parent))
    monkeypatch.setattr(speech.shutil, "which", lambda name: None)
    with pytest.raises(NotInstalled, match="en_US-amy-medium.onnx"):
        speech.PiperTts().synthesize("hello")


def test_say_rejects_empty_text() -> None:
    with pytest.raises(SpeechError, match="empty text"):
        speech.PiperTts().synthesize("   ")


def test_say_invokes_piper_the_way_gvoice_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The invocation must match `gvoice/src/tts.ts`: -m, -c, -f, --length_scale, stdin."""
    piper_dir = tmp_path / "piper"
    voices = piper_dir / "voices"
    voices.mkdir(parents=True)
    binary = piper_dir / "piper"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (voices / "en_US-amy-medium.onnx").write_bytes(b"model")
    (voices / "en_US-amy-medium.onnx.json").write_text("{}")
    monkeypatch.setenv("PIPER_DIR", str(piper_dir))

    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        captured["argv"] = argv
        captured["stdin"] = kwargs.get("input")
        captured["env"] = kwargs.get("env")
        target = Path(argv[argv.index("-f") + 1])
        target.write_bytes(
            speech.WavClip(data=tone(0.2), sample_rate=22050, channels=1).to_wav_bytes()
        )
        return speech._Result(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        speech.PiperTts, "_run_stdin", lambda self, a, t, e: fake_run(a, input=t, env=e)
    )
    clip = speech.PiperTts().synthesize("hello there")

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[0] == str(binary)
    assert "-m" in argv and "-c" in argv and "-f" in argv
    assert "--length_scale" in argv
    assert captured["stdin"] == "hello there"
    assert "LD_LIBRARY_PATH" in str(captured["env"])
    assert clip.sample_rate == 22050
    # Normalisation is applied on the way out (gvoice normalises for the same reason).
    assert clip.peak() <= int(0.7 * 32767) + 1


# --------------------------------------------------------------------------- ASR


def test_asr_absent_reports_the_install_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GVOICE_HOME", str(tmp_path / "absent"))
    monkeypatch.setattr(speech.shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert speech.find_asr_backend() is None
    with pytest.raises(NotInstalled, match="GVOICE_PARAKEET_VENV"):
        speech.transcribe(speech.WavClip(data=tone(0.2), sample_rate=16000, channels=1))


def test_asr_worker_protocol(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fake worker speaking gvoice's line protocol, end to end."""
    worker = tmp_path / "fake_worker.py"
    worker.write_text(
        "import json, sys\n"
        "print(json.dumps({'ready': True, 'model': 'fake'}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    name = request['wav'].rsplit('/', 1)[-1]\n"
        "    print(json.dumps({'text': 'hello from ' + name, 'segments': []}), flush=True)\n"
    )
    backend = speech.AsrBackend(
        name="fake", python=speech.shutil.which("python3") or "python3", worker=worker
    )
    result = speech.transcribe(
        speech.WavClip(data=tone(0.2), sample_rate=16000, channels=1), backend=backend
    )
    assert result["backend"] == "fake"
    assert result["text"].startswith("hello from")


def test_asr_worker_error_is_surfaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    worker = tmp_path / "err_worker.py"
    worker.write_text(
        "import json, sys\n"
        "print(json.dumps({'ready': True}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    print(json.dumps({'error': 'model exploded'}), flush=True)\n"
    )
    backend = speech.AsrBackend(
        name="fake", python=speech.shutil.which("python3") or "python3", worker=worker
    )
    with pytest.raises(SpeechError, match="model exploded"):
        speech.transcribe(
            speech.WavClip(data=tone(0.05), sample_rate=16000, channels=1), backend=backend
        )


def test_asr_worker_that_never_becomes_ready_is_reported(tmp_path: Path) -> None:
    worker = tmp_path / "dead_worker.py"
    worker.write_text("import sys\nsys.exit(3)\n")
    backend = speech.AsrBackend(
        name="fake", python=speech.shutil.which("python3") or "python3", worker=worker
    )
    with pytest.raises(SpeechError, match="did not become ready"):
        speech.transcribe(
            speech.WavClip(data=tone(0.05), sample_rate=16000, channels=1), backend=backend
        )


# --------------------------------------------------------------------------- capture analysis


def test_analyse_capture_reports_peak_and_utterances() -> None:
    pcm = silence(0.3) + tone(0.8, amplitude=15000) + silence(1.5)
    result = speech.analyse_capture(pcm, sample_rate=48000, channels=1)
    assert result.peak > 14000
    assert len(result.utterances) == 1
    assert result.duration_s == pytest.approx(len(pcm) / 2 / 48000, abs=0.01)
    assert result.to_dict()["utterances"][0]["peak"] > 14000


def test_analyse_capture_downmixes_stereo() -> None:
    left = tone(0.8, amplitude=12000)
    interleaved = b"".join(struct.pack("<hh", s, s) for (s,) in struct.iter_unpack("<h", left))
    result = speech.analyse_capture(interleaved, sample_rate=48000, channels=2)
    assert len(result.utterances) == 1


def test_utterance_clip_is_a_usable_wav() -> None:
    pcm = silence(0.3) + tone(0.8) + silence(1.5)
    result = speech.analyse_capture(pcm, sample_rate=48000, channels=1)
    clip = result.utterance_clip(0)
    assert clip.channels == 1
    assert clip.frames > 0
    assert speech.read_wav(clip.to_wav_bytes()).frames == clip.frames


def test_json_round_trip_of_a_capture() -> None:
    pcm = silence(0.3) + tone(0.6) + silence(1.2)
    payload = speech.analyse_capture(pcm, sample_rate=48000, channels=1).to_dict()
    assert json.loads(json.dumps(payload))["peak"] > 0
