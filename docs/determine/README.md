# Library determinations

One document per communication medium, answering: **what is the optimal
no-app-registration way to drive this platform from an agent, and is voice
(ASR/TTS in real calls) possible?**

| Medium | Determination | Voice |
| --- | --- | --- |
| Signal | [signal.md](signal.md) | **possible, proven on this host** |
| Facebook Messenger | [messenger.md](messenger.md) | **possible** — ordinary WebRTC in the page |
| Microsoft Teams | [teams.md](teams.md) | OS audio + CDP page; no API route |
| WeChat / WeCom | [wechat.md](wechat.md) | **impossible** (policy-bound) |
| Skype | [skype.md](skype.md) | retired upstream — **no skill planned** |
| X / Twitter | *pending* — [brief](briefs/xcom.md) | — |
| Zoom | *pending* — [brief](briefs/zoom.md) | — |

Also here:

* [`BRIEF.md`](BRIEF.md) — the shared instructions every determination agent worked to:
  the no-app bar, the ranked candidate classes, the voice criteria, and the evidence
  rules.
* [`briefs/`](briefs/) — per-medium briefs still in progress. A brief is *not* a
  determination; it is the question. When the answer arrives it moves up to
  `<medium>.md`.

## Convention

A completed determination's `## Decision` section is filled in. The template text
("One paragraph: the chosen path…") must be gone — if it is still there, the file is a
scaffold and must not be cited as a conclusion.

Every determination separates **verified** from **expected** claims. Treat anything
marked expected or unverified as a lead to check, not a fact to build on.
