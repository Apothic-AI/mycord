---
name: mycord-zoom-skill
description: Drive the Zoom desktop client with no app registration, and carry real two-way call audio through a PipeWire/PulseAudio virtual device plane. Joins via the `zoommtg://` URL scheme the client registers for itself (no account, no SDK app), speaks into Zoom's microphone with Piper TTS, and captures Zoom's speaker output with an energy VAD (optionally transcribing with gvoice's parakeet/whisper workers). Use when asked to join a Zoom meeting, check whether a meeting is running, leave one, speak into a call, listen to or transcribe call audio, or manage the virtual audio devices.
---

# mycord-zoom — Zoom as the account's own human, with real call audio

Zoom publishes **no local API** for the ordinary desktop client: no socket, no CLI verbs, and
the `cefIpcChannel` FIFOs are proprietary and unframed. Its developer surface — the Meeting
SDK, RTMS — all requires a registered Marketplace app, which fails the no-app bar.

What is left is what a human actually uses, plus the one thing Zoom hides in plain sight: it
is an ordinary client of the system mixer. So this skill is two independent halves.

| Concern | Mechanism |
| --- | --- |
| Join / leave / observe | the `zoommtg://` URL scheme the client registers for itself — the same link a human clicks |
| **Call audio, both directions** | a PipeWire plane: Zoom records from our virtual microphone and plays into our virtual speaker |

Neither half needs an account, a token, or a registered app.

> **Unofficial.** The client's URL scheme is undocumented by Zoom's own admission ("these
> schemes could change at any time"), and Zoom markets RTMS *instead of* client-driving
> agents. This is a local automation tool acting as *you*, not a bot with scopes, and it is
> the same class of accepted risk as the Discord selfbot warning in the repo README.

---

## Start here

```bash
cd mycord-zoom-skill
uv sync --all-extras
uv run mycord-zoom audio --help
```

Every verb takes `--json` and returns the numbers it measured, not a summary.

## The audio plane

```
Zoom output ──▶ [speaker sink] ──monitor──▶ agent capture     (inbound: agent hears)
                (never reaches the mic)

agent TTS   ──▶ [mic sink] ──monitor──▶ [mic source] ──▶ Zoom input
                (what Zoom records from)
```

Three nodes, not two. One sink cannot serve both directions without feeding Zoom's own voice
back into Zoom's microphone as echo; separating them removes the echo path entirely, which is
why this needs no echo guard (gvoice needs one because Google Voice returns our own audio).

```bash
uv run mycord-zoom audio up                      # create the devices; records your defaults FIRST
uv run mycord-zoom audio route --to-agent        # point Zoom's audio at the plane
uv run mycord-zoom audio say "Hello, I'm an agent."
uv run mycord-zoom audio listen --seconds 5 --wav /tmp/heard.wav
uv run mycord-zoom audio down                    # ALWAYS: restore your defaults, unload the devices
```

`audio up` is idempotent and inert: it creates devices but does **not** move your audio.
Routing is an explicit second step.

## Leaving no trace

A leftover null sink silently mutes the machine, so this is the most carefully built part:

* your default sink/source are written to `~/.local/share/mycord-zoom/audio-session.json`
  **before** anything changes, atomically, and kept until teardown has provably finished;
* `audio down` restores defaults **first**, then unloads — a failed unload still leaves you
  hearing your own audio;
* live streams already playing into the plane are re-pointed back, because `set-default-*`
  only affects new streams;
* SIGTERM/SIGHUP become `SystemExit` so `finally` runs; SIGKILL and reboots are covered by
  `audio recover`, which reads the recorded session and finishes the job;
* `down` is idempotent, and `up` refuses to record a "previous default" that is its own device.

**If you think something was left behind:** `uv run mycord-zoom audio recover`, then check
`pactl get-default-sink`.

## Speech

No model is reimplemented and nothing is downloaded. `audio say` invokes Piper exactly as
`gvoice/src/tts.ts` does (`PIPER_DIR`, default `~/.local/share/piper`, voice
`en_US-amy-medium`); `audio listen` drives the same parakeet-redux/faster-whisper workers as
`gvoice/src/stt.ts` (`GVOICE_*_VENV`, default `~/.local/share/gvoice/*`). A missing model is
exit **2** with the expected path — never a multi-gigabyte surprise mid-call.

`audio listen --no-transcribe` needs no ASR at all: the capture and VAD are the useful part,
and the VAD is a port of gvoice's (hysteresis + adaptive noise floor + hangover).

## Joining

```bash
uv run mycord-zoom join "https://zoom.us/j/1234567890?pwd=secret"
uv run mycord-zoom join 1234567890
uv run mycord-zoom start "https://zoom.us/s/1234567890"   # via `join --action start`
uv run mycord-zoom status --with-audio
uv run mycord-zoom leave
```

Accepted: `/j/`, `/wc/<id>/join`, `/s/`, `/my/` URLs on Zoom hosts, `zoommtg://` and
`zoomus://` links, or a bare 9–11 digit id. Anything else is refused rather than guessed at —
launching the client into the wrong meeting is worse than an error.

Meeting passwords are treated as credentials: they go to the client, and are masked in every
printed or JSON result.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | runtime failure (bad input, audio server refused, no window) |
| 2 | something is not installed (a CLI or a speech model) |
| 3 | not configured yet (run `audio up`, or give a meeting) |

`argparse` normally exits 2 on a usage error; this CLI remaps that to 1 so 2 always means
"install something".

## What is verified, and what is not

Verified on the development host (Zoom 6.5.11, PipeWire 1.4.2): deep links reach the client
verbatim; the plane loads; a tone played into the mic sink is recoverable from the mic source
at peak 20000; a tone played into the speaker sink is recoverable from its monitor at peak
20000; Piper speech reaches the mic source at peak 23171; teardown restores the operator's
defaults exactly, including after an exception.

**Not verified:** whether Zoom itself *lists and records from* `mycord_zoom_microphone`. That
is Zoom's decision, and confirming it needs a meeting with a human host. Everything on our
side of the boundary is measured; this one link is not, and the skill does not claim it.

## Safety rails

* Never join or start a meeting without a human naming it.
* Never speak TTS into a live meeting without explicit per-meeting consent — it is audible
  to real people.
* Always `audio down` when finished; `make test-live` asserts the restore for you.
