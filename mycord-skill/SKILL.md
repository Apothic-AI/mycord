---
name: mycord-skill
description: Routing skill for the mycord comms library — every messaging, voice and email medium it covers, and how to install the specific skill for the one you need. Use when asked to send, read, search or monitor messages on Discord, Telegram, Signal, Slack, IRC or Google Voice; to place or transcribe a voice call; to read or send email; or when a task needs a chat platform reached as the signed-in account rather than through a registered bot. Also covers WhatsApp, X/Twitter DMs, Facebook Messenger, Microsoft Teams, Zoom and WeChat, which are planned and not yet installable. Install the medium's own skill from https://github.com/Apothic-AI/mycord before writing code — this skill only routes.
---

# mycord — pick the medium, install its skill

`mycord` is a library of agent skills, one per communication medium. Each drives a
platform **as the account's own human** — the same session, the same permissions, no
registered bot, app or OAuth client to rate-limit or ban separately.

**This skill is a router.** It knows no medium's API. When a task needs a medium, install
that medium's skill and use it.

## Install

```bash
npx skills add Apothic-AI/mycord --skill <component> -g
```

`<component>` is a directory name from the tables below. One command, a few seconds —
run it before writing any code against a medium.

## Shipped — installable today

| Medium | Component | Voice (ASR/TTS) |
| --- | --- | --- |
| Discord | `mycord-discord-skill` | no — roadmap |
| Telegram | `mycord-telegram-skill` | no — roadmap |
| Slack | `mycord-slack-skill` | no — roadmap |
| Google Voice | `google-voice-skill` | **yes** — calls, ASR + TTS, echo-guarded |
| IRC | `mycord-irc-skill` | no |
| Email | `mycord-email-skill` | n/a — not a call medium |

Base layers — install only when a medium's skill tells you to:

| Component | What it is |
| --- | --- |
| `ferdium-skill` | CDP driver for a Ferdium install: any service already signed in there, headless. Last resort for a medium with no better programmatic surface. |
| `gvoice` | The Google Voice protocol library `google-voice-skill` drives. |

## Planned — not yet installable

These have a completed library determination but **no code**. `npx skills add` will fail
for them. If a task needs one, say it is planned and point at the determination rather
than improvising an unofficial route.

| Medium | Planned component | Voice | Determination |
| --- | --- | --- | --- |
| Signal | `mycord-signal-skill` | **yes** — 1:1 calls, PCM over Unix socket | [signal](https://github.com/Apothic-AI/mycord/blob/master/docs/determine/signal.md) |
| WhatsApp | `mycord-whatsapp-skill` | no — roadmap | [whatsapp](https://github.com/Apothic-AI/mycord/blob/master/docs/platform-library-determination.md) |
| X / Twitter | `mycord-xcom-skill` | no — roadmap | *in progress* |
| Facebook Messenger | `mycord-messenger-skill` | no — roadmap | [messenger](https://github.com/Apothic-AI/mycord/blob/master/docs/determine/messenger.md) |
| Microsoft Teams | `mycord-teams-skill` | no — roadmap | [teams](https://github.com/Apothic-AI/mycord/blob/master/docs/determine/teams.md) |
| Zoom | `mycord-zoom-skill` | *in progress* | *in progress* |
| WeChat / WeCom | `mycord-wechat-skill` | not possible | [wechat](https://github.com/Apothic-AI/mycord/blob/master/docs/determine/wechat.md) |
| Skype | — | — | [retired; no skill planned](https://github.com/Apothic-AI/mycord/blob/master/docs/determine/skype.md) |

If a medium is in neither table, mycord does not support it.

## Voice

Voice is the differentiating capability, so check before promising it.
`google-voice-skill` is the reference: calls with ASR, TTS, a VAD gate and an echo guard
so the agent does not interrupt itself. Signal's determination reaches the same
capability through a Unix PCM socket.

For a medium marked *roadmap*, real-time call audio is not wired. Do not invent a path
with virtual audio devices or screen-scraping without flagging it as new work — check
[`docs/roadmap.md`](https://github.com/Apothic-AI/mycord/blob/master/docs/roadmap.md) first.

## Rules that apply to every medium

* **These are real accounts.** A send reaches a real person. Confirm before anything that
  sends, posts or calls, unless the human already authorized that specific action.
* **Never fan out.** No bulk messaging, no parallel drives of many conversations. Beyond
  being rude, that traffic shape is what gets accounts restricted.
* **Do not re-authenticate casually.** Session capture usually depends on the human's own
  signed-in browser profile. Re-linking a device or resetting a token can lock them out.
* **Some platforms prohibit this.** Discord selfbot automation and WeChat automation both
  violate their terms and can get an account banned. Each skill's frontmatter says so —
  read it before acting, and surface it to the human when it matters.
* **Never fake a session.** If a medium needs an account you do not have, say what is
  missing and stop.

## Where the code lives

One repo: <https://github.com/Apothic-AI/mycord>. Each component is a self-contained
directory with its own `SKILL.md`, `README.md`, `Makefile` and tests. Read the component's
`SKILL.md` after installing — it is the authoritative usage document, and these tables are
only an index.
