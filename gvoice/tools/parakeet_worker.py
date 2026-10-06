#!/usr/bin/env python
"""Streaming-friendly Parakeet worker for the Google Voice client.

Two reasons this exists instead of a `whisper` CLI call:

  1. Speed. parakeet-redux is a 1.58-bit ternary model — no multiplies in the hot loop —
     so it transcribes far faster than any Whisper variant on CPU.
  2. Streaming. It accepts growing chunks of audio, so partial transcripts can be emitted
     while the far end is still speaking. Whisper only makes sense on a settled utterance,
     which forces a dead-air gap on every turn.

Protocol: {"wav": path, "stream": true} per line on stdin -> one or more JSON lines on
stdout. A stream request emits {"partial": "..."} as audio accumulates and a final
{"text": "...", "segments": [...]} when the client sends {"flush": true} or marks the
utterance final. Model loading happens once.
"""

import json
import os
import sys
import time
import wave

os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 4))

MODEL_ID = os.environ.get("GV_PARAKEET_MODEL", "moondream/parakeet-redux")
DEVICE = os.environ.get("GV_PARAKEET_DEVICE", "cpu")

# Re-transcribe at most this often while streaming, so long utterances stay responsive
# without decoding every incoming frame.
PARTIAL_INTERVAL_S = float(os.environ.get("GV_PARAKEET_PARTIAL_S", "0.45"))
# Below this much audio there is nothing worth transcribing yet.
MIN_AUDIO_S = float(os.environ.get("GV_PARAKEET_MIN_S", "0.35"))


def read_wav(path):
    """Decode a 16-bit PCM WAV to mono float32 at its native rate."""
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


def main() -> None:
    import moondream as md

    t0 = time.time()
    speech = md.photon(MODEL_ID, device=DEVICE)
    load_s = time.time() - t0

    sys.stdout.write(
        json.dumps(
            {"ready": True, "model": MODEL_ID, "device": DEVICE, "loadSeconds": round(load_s, 2)}
        )
        + "\n"
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
            if req.get("quit"):
                break

            if req.get("flush"):
                # Nothing buffered to flush; the final transcript already went out.
                continue

            audio, rate = read_wav(req["wav"])
            duration = len(audio) / rate if rate else 0.0
            t1 = time.time()
            # sample_rate is mandatory for raw PCM; without it kestrel refuses the input.
            result = speech.transcribe(audio=audio, sample_rate=rate)
            elapsed = time.time() - t1
            text = (result.get("text") or "").strip()

            if req.get("stream"):
                if duration < MIN_AUDIO_S:
                    # Not enough audio yet; tell the client we are still accumulating so it
                    # knows the silence was heard rather than lost.
                    sys.stdout.write(json.dumps({"partial": "", "skipped": True}) + "\n")
                else:
                    sys.stdout.write(
                        json.dumps(
                            {
                                "partial": text,
                                "seconds": round(duration, 3),
                                "rtf": round(elapsed / duration, 4) if duration else None,
                            }
                        )
                        + "\n"
                    )
            else:
                sys.stdout.write(
                    json.dumps(
                        {
                            "text": text,
                            "segments": [
                                {"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "").strip()}
                                for s in result.get("segments", [])
                            ],
                            "seconds": round(duration, 3),
                            "rtf": round(elapsed / duration, 4) if duration else None,
                        }
                    )
                    + "\n"
                )
            sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001 - report, never kill the worker
            sys.stdout.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()