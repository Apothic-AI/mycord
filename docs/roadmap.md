# mycord roadmap

**Updated:** 2026-10-25

> Library determinations for eight further mediums are in
> [`docs/determine/`](determine/). They answer *what to build and whether voice is
> possible*; none has code yet. This file tracks committed work, and the two voice items
> below remain the reason it exists.

Committed future work for components that exist today. Each item is a real feature with
a design already proven somewhere in this repo — the point of listing it is that the
approach is known and only the work remains.

Nothing here is in progress. Items are recorded so they are not rediscovered later.

---

## R1 — Voice: ASR + TTS for WhatsApp calls

**Status:** not started. Deferred deliberately; this is a large piece of work.

WhatsApp voice calling through `mycord-whatsapp-skill` would need the same treatment
`gvoice/` already has working end-to-end:

| Concern | Proven precedent |
| --- | --- |
| Turn audio into text | `gvoice/src/stt.ts` — parakeet-redux (0.55 s/utterance) with faster-whisper fallback |
| Turn text into speech | `gvoice/src/tts.ts` — Piper via `PiperTts`, arbitrary backends via `CommandTtsBackend` |
| Gate on speech vs silence | `gvoice/src/vad.ts` — VAD plus a pre-roll buffer so first phonemes survive |
| Avoid self-interruption | `gvoice/src/agent.ts` — echo guard: Google Voice returns our own audio, so barge-in gates on level |
| Drive a call from outside | `gvoice/src/control.ts` — HTTP `/events` SSE stream plus `/say`, `/press`, `/shutup`, `/hangup` |

The unknown is the transport, not the speech stack: WhatsApp Web exposes calls over its
own signalling (not SIP like Google Voice), and the media would arrive inside the
Ferdium webview rather than a `werift` peer connection. Expect the work to be dominated
by sourcing call audio out of the page, not by STT/TTS.

**Do not** fork the gvoice Python/TS split. `gvoice/` is TypeScript over `werift`; this
would be Python over a browser page. Reuse the *design*, not the code.

---

## R2 — Voice: ASR + TTS for Slack calls

**Status:** not started. Deferred deliberately; same reason as R1.

Slack huddles are the equivalent surface. Same speech stack applies (R1's table), with
two Slack-specific wrinkles:

* Huddles are not 1:1 by default — the agent has to decide *whose* audio it is
  transcribing and speaking to, which is a design question Google Voice never poses.
* `mycord-slack-skill` currently reaches Slack through `slackdump` (reads) and
  `slack_sdk` (writes) using an `xoxc` + `d` cookie session. Neither surface carries
  huddle audio, so this likely needs the Ferdium/CMB route or Slack's own huddle
  client — a capability question worth answering before any implementation.

---

---

## Mediums with a determination, no code yet

Each has a completed determination stating the optimal no-app-registration path and a
voice verdict. Signal is the strongest: its voice path is **already proven on this host**.

| Medium | Chosen path | Voice |
| --- | --- | --- |
| Signal | `signal-cli` JSON-RPC daemon + `signal-call-tunnel` PCM socket | **possible, proven** |
| X / Twitter | *determination in progress* | — |
| Facebook Messenger | see [determination](determine/messenger.md) | **possible** — ordinary WebRTC in the page |
| Microsoft Teams | see [determination](determine/teams.md) | OS audio + CDP page; no API route |
| Zoom | *determination in progress* (virtual-device audio under study) | — |
| WeChat / WeCom | WeCom server API (not the personal account) | **impossible** (policy-bound) |
| Skype | **no skill planned** — consumer Skype retired 2025-05-05 | n/a |

### Voice transport shapes

Two shapes are proven on this machine, and the determinations surfaced a third that
would generalize across every conferencing medium:

| Shape | Precedent | Reaches |
| --- | --- | --- |
| WebRTC peer in the process | `gvoice/` over `werift` | Google Voice |
| Unix PCM socket | `signal-call-tunnel` (`AUDIO_MODE=pipe`) | Signal |
| OS virtual audio device (PulseAudio/PipeWire) | *proposed* | any desktop/web client that lets you pick a device — Zoom, Teams |

The third is the one worth building toward: a virtual device is client-agnostic, so one
implementation would serve every medium that cannot expose call audio in-process. It is
also the only shape available where the vendor ships a native client and refuses third
party media (Teams).

## Sequencing note

Both items share the ASR/TTS layer. When either is picked up, extract that layer once —
a local speech service with a small HTTP/stdio contract — rather than wiring Piper and
Parakeet separately into two skills. That also gives `gvoice/` a migration path off its
own embedded copies, which today duplicate the model venvs under
`~/.local/share/gvoice/`.

Constraints that still apply from the rest of this repo: no semantic string-parsing, no
brittle selectors, and confirm with a human before anything that reaches another person.
