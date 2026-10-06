#!/usr/bin/env python
"""Persistent Whisper worker for the Google Voice client.

Loading a speech model costs seconds. A phone agent transcribes one short utterance every
few seconds, so paying that per turn is unacceptable. This holds the model in memory and
answers one JSON request per line on stdin.

Two backends:

  faster-whisper (default) -- CTranslate2, int8 on CPU. Roughly 4x the throughput of
      openai-whisper on CPU, which matters because openai-whisper's CPU-only torch took
      ~15 s for a 5 s utterance. Ships VAD filtering, which phone audio needs.

  openai-whisper -- reference implementation, used when faster-whisper is unavailable.

Protocol: write {"wav": "/path.wav", "language": "en"} per line on stdin,
read {"text": "...", "segments": [...]} per line on stdout. Requests are processed in
order. The first line of output is {"ready": true, ...}.
"""

import json
import sys
import warnings

warnings.filterwarnings("ignore")

BACKEND = "faster"
MODEL = "small.en"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"

for arg in sys.argv[1:]:
    if arg.startswith("--model="):
        MODEL = arg.split("=", 1)[1]
    elif arg.startswith("--device="):
        DEVICE = arg.split("=", 1)[1]
    elif arg.startswith("--backend="):
        BACKEND = arg.split("=", 1)[1]
    elif arg.startswith("--compute_type="):
        COMPUTE_TYPE = arg.split("=", 1)[1]


def read_wav(path: str):
    """Decode a 16-bit PCM WAV to a mono float32 NumPy array at its native rate.

    faster-whisper normally decodes through PyAV, but PyAV does not build on Python 3.14
    (`open() got an unexpected keyword argument 'metadata_errors'`). Since this project
    only ever hands the worker a WAV it wrote itself, decode with the stdlib and pass the
    array straight to `transcribe`, which accepts one.
    """
    import wave

    import numpy as np

    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got {width * 8}-bit")

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def load_model():
    """Return a callable transcribe(path, language) -> dict."""
    if BACKEND == "faster":
        from faster_whisper import WhisperModel

        model = WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE_TYPE)

        def transcribe(path: str, language: str) -> dict:
            audio, _rate = read_wav(path)
            segments, _info = model.transcribe(
                audio,
                language=language,
                # Phone audio is mostly silence between turns; VAD stops Whisper from
                # inventing text during the gaps.
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
            )
            segs = [
                {"start": s.start, "end": s.end, "text": s.text.strip()}
                for s in segments
            ]
            return {"text": " ".join(s["text"] for s in segs).strip(), "segments": segs}

        return transcribe

    import whisper  # openai-whisper

    model = whisper.load_model(MODEL, device=DEVICE)

    def transcribe(path: str, language: str) -> dict:
        result = model.transcribe(
            path,
            language=language,
            fp16=False,
            temperature=0.0,
            compression_ratio_threshold=2.4,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            condition_on_previous_text=False,
        )
        segs = [
            {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
            for s in result.get("segments", [])
        ]
        return {"text": (result.get("text") or "").strip(), "segments": segs}

    return transcribe


def main() -> None:
    try:
        transcribe = load_model()
    except ImportError as exc:
        sys.stdout.write(
            json.dumps({"error": f"backend {BACKEND} unavailable: {exc}"}) + "\n"
        )
        sys.stdout.flush()
        return

    sys.stdout.write(
        json.dumps({"ready": True, "backend": BACKEND, "model": MODEL, "device": DEVICE}) + "\n"
    )
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == "quit":
            break
        try:
            req = json.loads(line)
            resp = transcribe(req["wav"], req.get("language", "en"))
        except Exception as exc:  # noqa: BLE001 - report, never kill the worker
            resp = {"error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()