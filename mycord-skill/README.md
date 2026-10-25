# mycord-skill

Routing skill for the [mycord](https://github.com/Apothic-AI/mycord) comms library.

It exists so an agent asked to "message someone on X" learns three things before writing
any code: whether mycord supports that medium, which component to install, and whether
voice is available. It contains no medium-specific protocol logic — each medium's own
skill is authoritative once installed.

## Install

```bash
npx skills add Apothic-AI/mycord --skill mycord-skill -g
```

## What it covers

Shipped and installable: Discord, Telegram, Slack, Google Voice (with voice ASR/TTS),
IRC, Email — plus the `ferdium-skill` and `gvoice` base layers.

Planned with a completed library determination but no code yet: Signal (voice proven
viable), WhatsApp, X, Facebook Messenger, Microsoft Teams, Zoom, WeChat/WeCom.
Skype is retired upstream and no skill is planned.

Determinations live in [`docs/determine/`](../docs/determine/).

## Why the two tables are separate

The planned mediums are listed because knowing a determination exists is useful, but
marked uninstallable because `npx skills add` fails for a component that does not exist
yet. An agent told to install a nonexistent component wastes a turn and may improvise an
unsupported route; naming the state prevents that.
