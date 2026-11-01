# mycord roadmap

**Updated:** 2026-11-01

> Library determinations for eight further mediums are in
> [`docs/determine/`](determine/). They answer *what to build and whether voice is
> possible*. Two have since shipped as skills — Signal and Zoom — and the rest remain
> determinations only. This file tracks committed work, and the voice items below remain
> the reason it exists.

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

## R3 — WeChat personal-account automation: **not planned — prohibited by platform terms**

**Status:** closed. This is not deferred work and it should not be picked up.

《微信个人账号使用规范》 (§1.2.4, §1.2.6, §1.2.7) prohibits automating the client, and
prohibits accessing or reading data the client releases into process memory, through
unauthorised third-party software. That is the whole mechanism of every individual-account
library: `wechaty` puppets, `itchat`/`itchat-uos`, `wxauto`, `WeChatFerry`, `ntchat`, and the
Linux AT-SPI/DB-decryption projects alike. Enforcement is active, and it targets the *tool*
rather than just the account — 36 WeChat notices in `github/dmca` during 2026, including a
17 U.S.C. §1201 notice processed against a network of 2,352 repositories, and `chatlog`'s
author deleting a 9,179-star project after one letter from WeChat.

This is also a dead-end technically, so nothing is being given up: the web protocol refuses
any account registered after 2017 (`ret 1203`), the surviving puppets are pinned to WeChat
builds that no longer exist, and the Windows-only tools cannot run here.

The sanctioned route, if the need is real, is WeCom 微信客服 — see
[the WeChat determination](determine/wechat.md).

---

## R3a — Voice: ASR + TTS for WeChat calls: **impossible**

**Status:** closed. Do not re-open without a change in Tencent's terms.

There is no published signalling or media transport for WeChat calls — not SIP, not WebRTC,
no media socket. The academic work on WeChat VoIP recovers *flow-level* artifacts only, and
the only public demonstrations of getting bytes into a call patch the client's
`AudioRecord`/memory or drive its audio devices through an unauthorised automation layer.

Note what this is *not*: the speech half is not the problem, and neither is the audio capture.
On Linux a virtual `module-pipe-source` is a working microphone and a `module-null-sink`
monitor is a working recorder (both verified on this machine), and the Zoom determination
([zoom.md](determine/zoom.md)) proved the full virtual-device shape end-to-end — so a WeChat
call's audio is mechanically reachable at the OS mixer without parsing any vendor protocol.
The blocker is purely that getting *into* the call means taking over a signed-in client's UI,
and injecting and capturing its audio is the prohibited act in R3. See the
"Virtual-device audio (post-Zoom finding)" subsection of [wechat.md](determine/wechat.md).

WeCom does support file-based **voice messages** (audio payloads in `kf/send_msg` and
`media/upload`), which a skill can transcribe with the same Parakeet/Piper stack. That is
not call audio and should not be described as such.

---

---

## Mediums with a determination, no code yet

Each has a completed determination stating the optimal no-app-registration path and a
voice verdict. Signal is the strongest: its voice path is **already proven on this host**.

| Medium | Chosen path | Voice |
| --- | --- | --- |
| Signal | `signal-cli` JSON-RPC daemon + `signal-call-tunnel` PCM socket | **possible, proven** — skill shipped (`mycord-signal-skill/`) |
| X / Twitter | `twifork` (twikit fork) over the web GraphQL with `auth_token` + `ct0` | **possible in principle, unbuildable now** — only implementation is AGPL-3.0 |
| Facebook Messenger | see [determination](determine/messenger.md) | **possible** — ordinary WebRTC in the page |
| Microsoft Teams | see [determination](determine/teams.md) | OS audio + CDP page; no API route |
| Zoom | desktop client via `zoommtg://` for calls + web client over CDP for reads | **verified full duplex** — PipeWire virtual devices; skill shipped (`mycord-zoom-skill/`) |
| WeChat / WeCom | WeCom server API (not the personal account) | **impossible** (policy-bound) |
| Skype | **no skill planned** — consumer Skype retired 2025-05-05 | n/a |

### Voice transport shapes

Two shapes are proven on this machine, and the determinations surfaced a third that
has since been built. All three are now implemented:

| Shape | Precedent | Reaches |
| --- | --- | --- |
| WebRTC peer in the process | `google-voice-skill/` over `gvoice/` | anything whose signalling you can speak |
| Unix PCM socket | `mycord-signal-skill/` + `signal-call-tunnel` | Signal 1:1 calls; the tunnel owns the peer connection |
| OS virtual audio device (PulseAudio/PipeWire) | **`mycord-zoom-skill/`** — verified full duplex, null sink + remap-source | any desktop or web client that lets you pick a device |

The third generalizes: a virtual device is client-agnostic, so one implementation serves
every medium that cannot expose call audio in-process, and it is the only shape available
where the vendor ships a native client and refuses third-party media (Teams, Messenger).
`mycord-zoom-skill/` implements it; adapting it to another client is new work, not
configuration.

## Not on the roadmap: Skype

**Do not build `mycord-skype-skill`, and no ASR/TTS item for it.** Consumer Skype was
retired 2025-05-05; its data was deleted through 2026, `web.skype.com` now serves
Microsoft Teams, no maintained library or client exists, and the only Skype-branded
application left (`calling.web.skype.com`) is a paid-legacy PSTN dial pad with no chat.
Any realtime-agent work on this protocol is Teams-consumer work — page-level WebRTC
capture/injection of `teams.live.com` — and belongs under R1/R2 and the Teams
determination. Evidence: [`skype.md`](determine/skype.md).

## Sequencing note

Both items share the ASR/TTS layer. When either is picked up, extract that layer once —
a local speech service with a small HTTP/stdio contract — rather than wiring Piper and
Parakeet separately into two skills. That also gives `gvoice/` a migration path off its
own embedded copies, which today duplicate the model venvs under
`~/.local/share/gvoice/`.

R3/R3a need nothing from that layer: R3 is closed on policy, and R3a's blocker is transport,
not speech. X is not on this list either — its blocker is a licensing decision, not design.

Constraints that still apply from the rest of this repo: no semantic string-parsing, no
brittle selectors, and confirm with a human before anything that reaches another person.
