"""The `mycord-zoom` command.

Shape, and why:

* **Three verb groups, no passthrough.** Unlike Slack there is no API to pass through to:
  Zoom publishes no local interface for the ordinary client, so every verb here is a real
  operation on the client or the audio server.
* **`--json` on every verb.** Agents parse; humans read. The flag is global and each verb
  returns a dict with the numbers it actually measured, not a summary.
* **Exit codes are the contract** (`errors.py`): 0 ok, 1 runtime, 2 something not
  installed, 3 not configured. argparse exits 2 on a usage error, so that is remapped to 1
  to keep 2 meaning "install something".

Destructive verbs are named so that they cannot be run by accident: the audio plane is
``audio up`` / ``audio down``, and ``leave`` refuses to close anything it cannot identify
as a meeting window.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import desktop, speech
from .audio import (
    CHANNELS,
    SAMPLE_RATE,
    AudioPlane,
    PlaneSpec,
)
from .errors import (
    EXIT_NOT_INSTALLED,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_UNCONFIGURED,
    AudioError,
    DesktopError,
    NotInstalled,
    SpeechError,
    Unconfigured,
    ZoomError,
)
from .state import StateError, StateStore, default_state_dir
from .tools import run_command

STATE_ERROR_CODE = EXIT_RUNTIME


def emit(payload: Any, as_json: bool) -> None:
    """Print `payload` as JSON or as aligned text."""
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if isinstance(payload, str):
        print(payload)
    elif isinstance(payload, list):
        for item in payload:
            print(item)
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, dict | list):
                print(f"{key:>22}: {json.dumps(value, default=str)}")
            else:
                print(f"{key:>22}: {value}")
    else:
        print(payload)


def build_plane(args: argparse.Namespace) -> AudioPlane:
    """The plane described by the global flags, with its state directory honoured."""
    store = StateStore(Path(args.state).expanduser() if args.state else default_state_dir())
    spec = PlaneSpec(
        speaker_sink=args.speaker_sink,
        mic_sink=args.mic_sink,
        mic_source=args.mic_source,
    )
    return AudioPlane(store, runner=run_command, pactl=args.pactl, spec=spec)


# --------------------------------------------------------------------------- audio


def cmd_audio_up(args: argparse.Namespace) -> int:
    plane = build_plane(args)
    session = plane.up()
    payload = {
        "changed": True,
        "session": session.to_dict(),
        "status": plane.status().to_dict(),
        "next": "run `mycord-zoom audio route --to-agent` to point audio at the plane",
    }
    emit(payload, args.json)
    return EXIT_OK


def cmd_audio_down(args: argparse.Namespace) -> int:
    plane = build_plane(args)
    payload = plane.down()
    emit(payload, args.json)
    # `down` raises when a problem needs a human; reaching here means the teardown landed.
    return EXIT_OK


def cmd_audio_recover(args: argparse.Namespace) -> int:
    plane = build_plane(args)
    payload = plane.recover()
    emit(payload, args.json)
    return EXIT_OK


def cmd_audio_status(args: argparse.Namespace) -> int:
    plane = build_plane(args)
    emit(plane.status().to_dict(), args.json)
    return EXIT_OK


def cmd_audio_route(args: argparse.Namespace) -> int:
    direction = args.direction or args.direction_word
    if direction is None:
        # Neither spelling was given. A usage error, but reported in this CLI's own voice so
        # the exit code stays inside the documented contract.
        raise AudioError("choose a direction: --to-agent or --to-mixer")
    plane = build_plane(args)
    session = plane.route(direction)
    payload = {
        "direction": direction,
        "session": session.to_dict(),
        "status": plane.status().to_dict(),
    }
    emit(payload, args.json)
    return EXIT_OK


def cmd_audio_say(args: argparse.Namespace) -> int:
    """Synthesise text with Piper and play it into the virtual microphone."""
    plane = build_plane(args)
    if not plane.status().running:
        raise Unconfigured(
            "the audio plane is not loaded; run `mycord-zoom audio up` first "
            "(there would be no microphone to speak into)"
        )
    tts = speech.PiperTts(voice=args.voice, length_scale=args.length_scale)
    clip = tts.synthesize(args.text)
    plane.play_wav(clip.to_wav_bytes())
    payload = {
        "spoken": True,
        "voice": tts.voice,
        "clip": clip.to_dict(),
        "device": plane.spec.mic_sink,
        "note": (
            "played into the virtual microphone; whether Zoom is recording that device is "
            "checked by `mycord-zoom audio status` and is unverified until a real meeting"
        ),
    }
    emit(payload, args.json)
    return EXIT_OK


def cmd_audio_listen(args: argparse.Namespace) -> int:
    """Capture the virtual speaker, gate on the VAD, optionally transcribe."""
    plane = build_plane(args)
    if not plane.status().running:
        raise Unconfigured(
            "the audio plane is not loaded; run `mycord-zoom audio up` first "
            "(there would be no speaker to listen to)"
        )
    pcm = plane.capture(seconds=args.seconds)
    result = speech.analyse_capture(pcm, sample_rate=SAMPLE_RATE, channels=CHANNELS)
    payload: dict[str, Any] = {
        "capture": result.to_dict(),
        "duration_requested_s": args.seconds,
    }
    if args.wav:
        clip = speech.WavClip(data=pcm, sample_rate=SAMPLE_RATE, channels=CHANNELS)
        path = speech.write_wav(Path(args.wav).expanduser(), clip)
        payload["wav"] = str(path)
    if not args.no_transcribe and result.utterances:
        # Transcription is opt-in-by-default but must not fail the capture: the capture is
        # the thing that was asked for, and it already succeeded.
        try:
            payload["transcripts"] = [
                speech.transcribe(result.utterance_clip(index))
                for index in range(len(result.utterances))
            ]
        except (NotInstalled, SpeechError) as exc:
            payload["transcripts"] = None
            payload["transcription_error"] = str(exc)
    emit(payload, args.json)
    return EXIT_OK


# --------------------------------------------------------------------------- desktop


def cmd_join(args: argparse.Namespace) -> int:
    link = desktop.parse_meeting(args.meeting, action=args.action)
    plan = desktop.launch(link, runner=run_command)
    payload = {
        "joined": True,
        "meeting": link.to_dict(),
        "launched": plan.to_dict(),
        "note": (
            "the client handles the join asynchronously; use `mycord-zoom status` to see "
            "the meeting window"
        ),
    }
    emit(payload, args.json)
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    state = desktop.status(runner=run_command)
    payload = state.to_dict()
    payload["audio"] = build_plane(args).status().to_dict() if args.with_audio else None
    emit(payload, args.json)
    return EXIT_OK


def cmd_leave(args: argparse.Namespace) -> int:
    payload = desktop.leave(runner=run_command, window=args.window)
    emit(payload, args.json)
    return EXIT_OK


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mycord-zoom",
        description=(
            "Drive the Zoom desktop client and a PipeWire virtual audio plane, with no app "
            "registration. See SKILL.md for the verb contract."
        ),
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--state",
        help="state directory (default: ~/.local/share/mycord-zoom)",
    )
    parser.add_argument("--pactl", help="path to pactl (default: found on PATH)")
    parser.add_argument("--speaker-sink", default="mycord_zoom_speaker")
    parser.add_argument("--mic-sink", default="mycord_zoom_mic")
    parser.add_argument("--mic-source", default="mycord_zoom_microphone")
    parser.add_argument("--verbose", action="store_true", help="show tracebacks")

    sub = parser.add_subparsers(dest="command", required=False)

    audio = sub.add_parser("audio", help="manage the virtual audio plane")
    audio_sub = audio.add_subparsers(dest="audio_command", required=True)

    up = audio_sub.add_parser("up", help="create the virtual devices and record the defaults")
    up.set_defaults(func=cmd_audio_up)

    down = audio_sub.add_parser(
        "down", help="restore the operator's defaults and unload the virtual devices"
    )
    down.set_defaults(func=cmd_audio_down)

    recover = audio_sub.add_parser(
        "recover", help="finish a teardown that a kill or reboot interrupted"
    )
    recover.set_defaults(func=cmd_audio_recover)

    status = audio_sub.add_parser("status", help="what is loaded, and where audio is going")
    status.set_defaults(func=cmd_audio_status)

    route = audio_sub.add_parser("route", help="point system audio at the plane or back")
    # Flags are the documented form (`audio route --to-agent`); a bare positional is accepted
    # too because it is the obvious thing to type. Both spellings resolve to one `direction`.
    which_way = route.add_mutually_exclusive_group()
    which_way.add_argument(
        "--to-agent",
        dest="direction",
        action="store_const",
        const="to-agent",
        help="Zoom uses the plane (its speaker and microphone become ours)",
    )
    which_way.add_argument(
        "--to-mixer",
        dest="direction",
        action="store_const",
        const="to-mixer",
        help="back to the operator's own devices",
    )
    route.add_argument(
        "direction_word",
        nargs="?",
        choices=("to-agent", "to-mixer"),
        help="same as the flags, for convenience",
    )
    route.set_defaults(func=cmd_audio_route)

    say = audio_sub.add_parser("say", help="synthesise speech and play it into the virtual mic")
    say.add_argument("text", help="what to say")
    say.add_argument("--voice", help="piper voice name (default en_US-amy-medium)")
    say.add_argument(
        "--length-scale",
        type=float,
        default=1.0,
        help="piper speed: >1 is slower and reads as more deliberate",
    )
    say.set_defaults(func=cmd_audio_say)

    listen = audio_sub.add_parser(
        "listen", help="capture the virtual speaker, VAD-gate it, optionally transcribe"
    )
    listen.add_argument("--seconds", type=float, default=5.0, help="how long to record (default 5)")
    listen.add_argument("--wav", help="also write the capture to this WAV path")
    listen.add_argument(
        "--no-transcribe",
        action="store_true",
        help="skip ASR (the capture and VAD still run, and no model is needed)",
    )
    listen.set_defaults(func=cmd_audio_listen)

    join = sub.add_parser("join", help="join (or start) a meeting via the desktop client")
    join.add_argument("meeting", help="meeting URL, zoommtg:// link, or 9-11 digit id")
    join.add_argument(
        "--action",
        choices=("join", "start"),
        default="join",
        help="start also needs a host's start link or ZAK in the URL",
    )
    join.set_defaults(func=cmd_join)

    status_top = sub.add_parser("status", help="is the client running, and is a meeting open?")
    status_top.add_argument(
        "--with-audio", action="store_true", help="include the audio plane's status"
    )
    status_top.set_defaults(func=cmd_status)

    leave = sub.add_parser("leave", help="close the meeting window (a graceful leave)")
    leave.add_argument("--window", help="close this window id or exact title instead")
    leave.set_defaults(func=cmd_leave)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv_list = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(argv_list)
    except SystemExit as exc:
        # argparse uses 2 for usage errors; 2 here means "not installed", so a usage error
        # must not be mistaken for one.
        return EXIT_RUNTIME if exc.code == 2 else int(exc.code or EXIT_OK)
    if getattr(args, "command", None) is None:
        parser.print_help()
        return EXIT_RUNTIME
    try:
        return int(args.func(args))
    except NotInstalled as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_INSTALLED
    except Unconfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNCONFIGURED
    except (AudioError, DesktopError, SpeechError, StateError, ZoomError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            raise
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
