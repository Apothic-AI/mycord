# mycord-zoom-skill — planning and progress

**Updated:** 2026-11-01 (integrated into master and re-gated there)

Scope came from `docs/determine/zoom.md` (the determination) and
`docs/implementation-decision.md` (which mediums get built now). The skill's own `README.md`
holds the measured numbers and `SKILL.md` is the authoritative usage document; this file
records the plan, what changed against the determination, and the boundary of what is proven.

---

## What shipped

| Piece | State |
| --- | --- |
| `src/mycord_zoom/{audio,desktop,speech,state,tools,errors,cli}.py` | written; `mypy --strict` clean across 9 source files |
| `tests/` (offline + live) | **101 offline passing**; 8 live passing against this host's real PipeWire server |
| `Makefile`, `SKILL.md`, `README.md` | matching `mycord-slack-skill` / `mycord-signal-skill` conventions |
| `audio up/down/status/route/recover`, `audio say`, `audio listen` | implemented |
| `join` / `status` / `leave` | implemented over `zoommtg://` |

Gates re-run **inside the integrated tree**, not the source worktree: ruff check + format
clean, `mypy --strict` clean, 101 passed / 8 deselected offline, and the live audio plane
really runs here — `-m live` gives **8 passed** against the host's PipeWire server, not 8
skips. The virtual-device machinery is therefore live-tested on this machine; what remains
unproven is Zoom's side of the boundary, not ours.

## The shape, and why it is not the one the determination sketched

The determination proposed a `module-null-sink` + `module-remap-source` pair. Implementation
found that a pair cannot serve both directions: Zoom would play into the sink the agent
writes to, putting Zoom's own voice back into Zoom's microphone. The build uses **three**
nodes — a speaker sink the agent monitors, plus a mic sink whose monitor feeds a remap-source
Zoom records from — which removes the echo path structurally rather than by guard.

That is why this skill needs no echo guard while `gvoice/` does: Google Voice returns our own
audio to us, the virtual plane never does.

## Corrections and findings not in the determination

1. **`parec` has ~2 s of startup latency before its first byte** (4 trials: 1.98 / 2.00 / 2.00
   / 1.99 s). The capture deadline originally started at process spawn, so every `--seconds`
   under ~2 s returned zero bytes — silently. Fixed by settling first and then recording the
   requested duration; there is now a live regression test for it.
2. **Teardown must re-point live streams, not just defaults.** `set-default-sink` only affects
   *new* streams, so a call that started while routed keeps playing into the plane and goes
   silent when the modules unload. `audio down` runs `move-sink-input` / `move-source-output`
   back before unloading.
3. **Restore-then-unload ordering is load-bearing.** If unloading fails, the operator must
   still be hearing their own audio; the reverse order leaves a null sink as the default,
   which mutes the machine with no obvious symptom.
4. **The local Zoom store is encrypted** (`~/.zoom/data/zoomus.enc.v2.db`), so meeting history
   cannot come from the client. Reads have to go through the `app.zoom.us` web client over
   CDP — which is why `audio` and `join` are the two halves this build ships and reads are
   not yet here.

## Verified live on the development host

Zoom 6.5.11, PipeWire 1.4.2, `pactl` 17.0. Tones travel the plane at full scale (peak 20000
for a 20000-amplitude tone, both directions); Piper speech reaches the mic source at peak
23171; `audio listen` end to end captured 131 072 bytes / 1.36 s with one 340 ms utterance
detected; teardown restored the operator's defaults byte-for-byte with 0 leftover
modules/sinks/sources; live streams were successfully re-pointed mid-playback (rc=0). Deep
links reach the client verbatim.

## Verified offline only

The three-node topology, the session record and all its crash paths, the `audio down`
idempotence and self-device refusal, the CLI verb surface and its exit-code remap
(`argparse`'s 2 → 1, so 2 always means "install something"), URL/id parsing and
password masking, and the speech worker discovery.

## Not verified — do not claim these

* **Whether Zoom lists `mycord_zoom_microphone` in Settings → Audio and records from it.**
  Everything on our side of the boundary is measured; accepting a virtual device is Zoom's
  decision and needs a **meeting with a human host**. This is the single most load-bearing
  unknown in the skill.
* **Real ASR.** No model is installed on the development host; `audio listen`'s transcription
  path is exercised against a fake worker. A real run reports the install paths and exits 2
  rather than downloading anything.
* **`leave` against a real meeting.** It closes by window-title heuristic and has only run
  against a synthetic window list; meeting-window title detection is heuristic and was only
  exercised against the home window.

## Next, when someone picks this up

1. Sit in a human-hosted meeting and confirm Zoom offers and records from the virtual
   microphone — the last unverified link.
2. Add the read surface (`app.zoom.us` over CDP): chat, participants, history.
3. Replace the window-title heuristic for `status`/`leave` with something derived from the
   client's own state once a meeting has actually been observed.