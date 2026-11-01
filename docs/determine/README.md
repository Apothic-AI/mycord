# Library determinations

One document per communication medium, answering: **what is the optimal
no-app-registration way to drive this platform from an agent, and is voice
(ASR/TTS in real calls) possible?**

| Medium | Determination | Voice |
| --- | --- | --- |
| Signal | [signal.md](signal.md) | **possible, proven on this host** — shipped as `mycord-signal-skill/` |
| Facebook Messenger | [messenger.md](messenger.md) | **possible** — WebRTC in the page, or virtual devices |
| Microsoft Teams | [teams.md](teams.md) | **possible** — virtual devices; the browser half is verified |
| WeChat / WeCom | [wechat.md](wechat.md) | **impossible** (policy-bound) |
| Skype | [skype.md](skype.md) | retired upstream — **no skill planned** |
| X / Twitter | [xcom.md](xcom.md) | **possible in principle, unbuildable now** — one AGPL-3.0 library, unproven live |
| Zoom | [zoom.md](zoom.md) | **verified full duplex** — PipeWire virtual devices; shipped as `mycord-zoom-skill/` |

Also here:

* [`BRIEF.md`](BRIEF.md) — the shared instructions every determination agent worked to:
  the no-app bar, the ranked candidate classes, the voice criteria, and the evidence
  rules.

## Convention

A completed determination's `## Decision` section is filled in. The template text
("One paragraph: the chosen path…") must be gone — if it is still there, the file is a
scaffold and must not be cited as a conclusion.

Every determination separates **verified** from **expected** claims. Treat anything
marked expected or unverified as a lead to check, not a fact to build on.
