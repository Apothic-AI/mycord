# mycord-zoom

Zoom for agents, with **no app registration** — as the account's own human, plus real
two-way call audio through the system mixer.

Two capabilities, both of which exist because Zoom publishes no local API for the ordinary
desktop client:

| Concern | How |
| --- | --- |
| Join / leave / observe a meeting | the `zoommtg://` URL scheme the client registers for itself — what a human clicking a meeting link uses |
| Carry **call audio** in both directions | a PipeWire/PulseAudio plane of virtual devices: Zoom is an ordinary mixer client, so it records from our virtual microphone and plays into our virtual speaker |

The plane is **three** devices, not a pair — one sink cannot serve both directions without
feeding Zoom's own voice back into Zoom's microphone as echo:

```
Zoom output ──▶ [speaker sink] ──monitor──▶ agent capture     (inbound)
agent TTS   ──▶ [mic sink]  ──monitor──▶ [mic source] ──▶ Zoom input   (outbound)
```

## Measured numbers

Everything below was measured on the development host (Zoom 6.5.11, PipeWire 1.4.2,
`pactl` 17.0). The commands are in the live test:

| Measurement | Result |
| --- | --- |
| Output path: tone into the mic sink → captured from the mic source | **peak 20000** for a 20000-amplitude tone (no loss through the remap) |
| Input path: tone into the speaker sink → captured from its monitor | **peak 20000** |
| Piper TTS → mic source, real voice | clip 3.56 s at 22050 Hz; captured **peak 23171** |
| `audio listen` end to end (tone on the speaker path) | 131 072 bytes / 1.36 s, **peak 16000**, **1 utterance** detected (340 ms) |
| `capture --seconds 0.5 / 1.0 / 2.0` (post-fix) | 32 768 / 65 536 / 98 304 frames = **0.68 / 1.37 / 2.05 s of audio** |
| recorder startup latency (why the above needed a fix) | **~2.0 s** to first byte, 4 trials: 1.98 / 2.00 / 2.00 / 1.99 s |
| Piper synthesis speed, `en_US-amy-medium` | real-time factor **0.18** (5.6× faster than real time) |
| Teardown | defaults restored byte-for-byte to the pre-test values; 0 leftover modules/sinks/sources |
| Live stream re-pointing (`move-sink-input`, `move-source-output`) | rc=0, destination confirmed |

## Install

```bash
cd mycord-zoom-skill
uv sync --all-extras
uv run mycord-zoom audio --help
```

Nothing here is a Python dependency. The audio plane is `pactl`/`pacat`/`parec`, speech is
Piper plus the `gvoice` ASR workers, and the client is launched by URL — all external
programs, which is deliberate: a Python binding would hide the case where one is missing,
which is exactly the case that must fail loudly.

## Use

```bash
# 1. Create the virtual devices and record what the operator's defaults were.
uv run mycord-zoom audio up

# 2. Point system audio at the plane (this is what makes Zoom use it).
uv run mycord-zoom audio route --to-agent

# 3. Speak into the virtual microphone.
uv run mycord-zoom audio say "Hello, I'm an agent."

# 4. Listen to the virtual speaker, VAD-gated; transcribe if ASR is installed.
uv run mycord-zoom audio listen --seconds 5 --wav /tmp/heard.wav

# 5. ALWAYS tear down: restores the operator's defaults and unloads the devices.
uv run mycord-zoom audio down

# Joining is independent of audio.
uv run mycord-zoom join "https://zoom.us/j/1234567890?pwd=secret"
uv run mycord-zoom status --with-audio
uv run mycord-zoom leave
```

## Leaving no trace

A leftover null sink silently mutes the machine, so teardown is the most carefully built
part of this package:

* the operator's default sink/source are written to
  `~/.local/share/mycord-zoom/audio-session.json` **before** anything is changed, atomically
  (temp file + `os.replace`), so a crash cannot destroy the only copy of what to restore;
* `audio down` restores the defaults **first**, then unloads the modules — so even a failed
  unload leaves the operator hearing their own audio;
* live streams that are already playing into the plane are re-pointed back
  (`move-sink-input` / `move-source-output`), because `set-default-*` only affects new
  streams — a Zoom call that started while routed would otherwise go silent on teardown;
* SIGTERM and SIGHUP are converted into `SystemExit` so `finally` blocks run;
* SIGKILL and reboots cannot be caught, so `audio recover` reads the recorded session and
  finishes the job;
* `audio down` is idempotent, and refuses to create a plane whose "previous default" would
  be its own device.

## Scope of this build

Audio plane first, deliberately. What is here: `audio up/down/status/route/recover`,
`audio say`, `audio listen`, and `join`/`status`/`leave`. What is **not** here yet: reading
meeting chat or participant lists, and meeting history — those need the `app.zoom.us` web
client over CDP, which the determination chose as the read surface. The local Zoom store is
encrypted (`~/.zoom/data/zoomus.enc.v2.db`), so history cannot come from the client.

## Unverified

One link in the chain could not be checked without joining a real meeting, and it is stated
plainly rather than assumed:

* **Whether Zoom lists `mycord_zoom_microphone` in Settings → Audio and records from it.**
  Everything on our side is verified — the devices load, the tones travel through them,
  streams can be re-pointed, and Zoom runs as a normal client of the same PipeWire server.
  But accepting a virtual device is Zoom's decision, and confirming it needs a meeting with a
  human host.
* Meeting-window title detection (`status`, `leave`) is heuristic and was only exercised
  against the client's home window; the meeting-window form is untested.
* Short captures used to be silently empty: `parec` takes ~2 s to deliver its first byte, and
  the capture deadline originally started at process spawn, so any `--seconds` under ~2 s
  returned zero bytes. Fixed by settling first, then recording that many seconds of audio;
  there is now a live regression test for it.
* `audio listen`'s transcription path is exercised with a fake worker. No ASR model is
  installed on the development host, so no real transcription has been run; a real run
  reports the install paths it looked for and exits 2 rather than downloading anything.
* `leave` closes a window by title heuristic and has only been exercised against a synthetic
  window list; it has not been used to leave a real meeting.

## Layout

```
src/mycord_zoom/
  cli.py       verbs, --json, exit codes
  audio.py     the virtual device plane, routing, teardown, PCM
  desktop.py   zoommtg:// mapping, launch, status, leave
  speech.py    Piper TTS, energy VAD, ASR worker protocol
  state.py     the recorded session (what to restore)
  tools.py     subprocess seam (everything is faked at the process boundary)
  errors.py    exit-code contract
```

## Development

```bash
make lint        # ruff check + format --check
make typecheck   # mypy --strict on src/
make test        # offline suite (fake pactl, no audio server)
make test-live   # against the real PipeWire server, with restore assertions
```

See `SKILL.md` for the agent-facing contract.
