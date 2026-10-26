# Resume after reboot — comms skills

**Paused:** 2026-10-25T20:05Z, at operator request, for a machine reboot.

Everything was stopped cleanly. Nothing is mid-flight. This file is the handoff so work
resumes without rediscovery.

## State at pause

**Pushed to `master` (`b1351367`) — safe, nothing to redo:**

| Work | Commit |
| --- | --- |
| `ferdium-skill` + `mycord-slack-skill` + roadmap | `043d4a7b` |
| 6 of 8 determinations (signal, messenger, teams, skype, wechat, zoom) + `mycord-skill` router | `f807b163` |
| Zoom determination (virtual-device audio verified) | `e46f8316` |
| Implementation decision (what gets built and why) | `b1351367` |

**Not committed, preserved on disk in jj workspaces:**

| Workspace | Contains |
| --- | --- |
| `../mycord-signal` | `docs/determine-signal.md`, `docs/implementation-decision.md` |
| `../mycord-xcom` | `docs/determine-xcom.md` — **still an unfilled template**, see below |
| `../mycord-zoom` | `docs/determine-zoom.md` |
| `../mycord-messenger` | `docs/determine-messenger.md` (already in master too) |
| `../mycord-teams` | `docs/determine-teams.md` (already in master too) |
| `../mycord-skype` | `docs/determine-skype.md` + roadmap edit (already in master) |
| `../mycord-wechat` | `docs/determine-wechat.md` + roadmap edit (already in master) |

The determination copies are already committed in `docs/determine/`, so those workspaces
are redundant and can be forgotten after a check. **Signal and Zoom are the two to keep**
until their implementations land.

## The three agents, and what each needs next

All three were interrupted with `esc` and confirmed `done`. Pane ids/tabs survive a
herdr restart only if the session does; re-check with `herdr agent list`.

| Agent | Was doing | Next action |
| --- | --- | --- |
| `det-zoom` (`w0:pF`) | Implementing `mycord-zoom-skill`, **8/17 todos** | Re-brief: build `audio.py` → `desktop.py` → say/listen → CLI → tests |
| `det-signal` (`w0:pB`) | Implementing Signal deps, **6/14 todos** | Re-brief: `provision.sh`, upgrade `signal-cli` ≥0.14.9, rebuild tunnel at `5a963d1` |
| `det-xcom` (`w0:pC`) | Determination, hit a **provider abort at 1h09m** | Resume from its own recovered notes: `/tmp/det-xcom.log`, `/tmp/tws_http.py`, `/tmp/twikit_client.py`, `/tmp/twikit_v11.py`, `/tmp/twifork_client.py`. Then write the decision into `docs/determine-xcom.md`. |

**No source files were written** by the Zoom or Signal implementation agents before the
pause — they were still scaffolding, so nothing is half-built and nothing is lost.

## X determination — specific warning

`../mycord-xcom/docs/determine-xcom.md` is **153 lines of untouched brief**. The template
sentence *"One paragraph: the chosen path…"* is still in `## Decision`. Do not treat this
file as a conclusion; per `docs/determine/README.md` the presence of that sentence means
the deliverable does not exist.

The agent's research was real and worth recovering: `twscrape` 0.20.1 installed and
probed, plus twikit and twifork probes. `/tmp` probes may be cleared by the reboot —
re-probe if missing.

## Audio — verified clean before reboot

The Zoom agent re-routed system audio during its research. Checked after interrupting:

* default sink: `bluez_output.C8_7B_23_51_26_FB.1`
* default source: `bluez_input.C8:7B:23:51:26:FB`
* no `null-sink` / `remap-source` / `detzoom` modules or sinks left loaded

Anyone resuming Zoom work must keep the same discipline: **restore the operator's default
sink and source on teardown, including on exception.** A leftover null sink silently
mutes their machine.

## Standing constraints (unchanged)

* No app/bot registration for any medium.
* Never send to a real person without explicit approval.
* Report unverified claims as unverified.
* `signal-cli` and the tunnel are both **behind** their required pins — the skill must
  detect and refuse, not fail at call setup.
* Zoom: do not join a real meeting without a human host; that link stays unverified.

## Verified environment facts (still true after reboot)

* Disk is **not** a constraint: 476 G free. The old disk-pressure rules are stale.
* `signal-cli` 0.14.6 installed; tunnel checkout `2f92426`, 718 MB, no prebuilt binary.
* `pactl`, `pw-cli`, `pw-cat`, `parec`, `wmctrl`, `xdotool` all present.
* `zoommtg://` registered to `Zoom.desktop`; Zoom at `/opt/zoom`.
* `mycord-slack-skill` (94 tests) and `ferdium-skill` (36 tests) pass offline.
