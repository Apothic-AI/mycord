# Determination briefs — 7-way comms fan-out

Shared instructions for the per-medium determination agents. Each agent gets this plus
its own medium-specific section (see `docs/determine/<medium>.md`).

---

## Your single deliverable

`docs/determine/<medium>.md` — a **library determination** for a `mycord-<medium>-skill`.

You are **not** writing the skill. You are answering one question with evidence:

> What is the optimal way to drive this medium from an agent, REPL-style, with the most
> features — and does it support voice (ASR/TTS in real calls) today?

The output is a decision another engineer implements from without redoing your research.

## The hard bar: no bot or app registration

The whole point of mycord is reaching a platform **as the account's own human** —
identical access, no registered bot/app/OAuth client, no second identity the platform
can rate-limit or ban separately. A solution that requires registering an app is not a
candidate; it is a footnote at most.

Existing skills set the standard. Read at least two of:

* `google-voice-skill/` + `gvoice/` — the **gold standard**: full voice with ASR
  (`gvoice/src/stt.ts`), TTS (`gvoice/src/tts.ts`), VAD, echo guard, and an HTTP control
  surface. Study how voice is wired before judging any medium's voice story.
* `mycord-slack-skill/` — no-app Slack via `xoxc` + `d` cookie, `slackdump` for reads,
  `slack_sdk` passthrough for writes.
* `ferdium-skill/` — the generic CDP driver for Ferdium webviews.
* `~/.agents/skills/mycord-discord-skill/` and `mycord-telegram-skill/` — the
  established REPL-style pattern for a real user account.

## Candidate classes, ranked by preference

Evaluate all of these before concluding, and state which you rejected and **why**:

1. **An official API usable with the account's own credentials** (user token, not a
   registered app) — best when it exists.
2. **A maintained reverse-engineered library/protocol** (the WhatsApp `neonize` /
   Baileys class of thing). Judge by: stars, commit recency, open issues, whether it
   ships its own WAM/telemetry, and licence.
3. **A native client that can be driven programmatically** (local daemon, JSON-RPC,
   IPC, CLI) — this is the Signal `signal-cli` case and often the best answer.
4. **Web client over CDP**, either a dedicated browser profile or inside Ferdium.
5. **Ferdium** — **last resort.** It is a fallback for services with no better
   programmatic surface, or a way to get a *second* capability (e.g. voice) that the
   primary library lacks. If you recommend it as primary, justify why nothing better
   exists.

Prefer the option that supports the **most features** — reads, history on demand,
sends, attachments, reactions, threads, presence, and especially **calls**. Feature
breadth beats implementation convenience.

## What "voice" means here

Answer precisely, do not hand-wave:

* Does the candidate support **real-time call audio** in both directions?
* If yes: what is the audio transport (PCM socket, WebRTC, virtual device), and can an
  agent inject and capture it programmatically? Cite the code or docs that prove it.
* If no: could a **second path** supply it (e.g. library for messaging + a browser
  page for calls)? Say which, and whether they can share one session.
* If nothing can do it: the medium goes on `docs/roadmap.md` as an ASR/TTS item, and
  you say so explicitly.

Existing precedent to compare against: `gvoice/` gets call audio as PCM through
`werift`, and `signal-cli` + `signal-call-tunnel` exposes a Unix PCM socket
(`SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe`). Those are the two working shapes on this
machine; say which shape, if either, a medium could follow.

## Evidence rules

* **Verify, don't assert.** Every claim about a library gets checked: read the repo,
  check the registry (`pip index`, `npm view`, crates.io), run `--version` or `--help`
  if installed. Cite what you checked.
* **Distinguish verified from expected.** If you could not verify something, label it.
  A confident guess presented as fact is the failure mode this brief exists to prevent.
* Nothing gets installed system-wide or linked to a real account. Reading, probing and
  `--help` are fine. **Do not send any message to any real person.**
* If a medium needs an account you don't have, say what you could and could not verify.

## Required structure for `docs/determine/<medium>.md`

```markdown
# <Medium> — library determination

**Date:** <date>
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a mycord-<medium>-skill.

## Decision

One paragraph: the chosen path, in one sentence, then the runner-up and why it lost.

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
|---|---|---|---|---|---|---|---|

## Why this one

The reasoning, with the specific evidence that decided it.

## Voice assessment

Verified / expected / impossible, per the voice section above. If impossible, the exact
roadmap text you propose.

## Implementation shape

Concrete: language, package layout, entry points, the 5-10 verbs the skill exposes
(e.g. `read`, `send`, `history`, `search`), auth/session capture, and the failure modes
that will bite (rate limits, re-auth, hibernation, protocol breaks).

## Open questions

What a human must decide or provide — account, consent, phone, etc.
```

## Process

1. `graft map` first, then `graft ask` for the existing skills and conventions. Use
   grep only for exhaustive literal enumeration.
2. Research candidates (web + registry + local installs).
3. Write the determination to `docs/determine/<medium>.md` in **your** jj workspace.
4. `jj describe` the change with a real message. Do not push, do not create bookmarks.
5. Pipe long-running commands' progress to a log file so your work is observable.

---

## Medium: X (x.com)

**Key question: does the no-app bar even hold here?**

X's API requires a registered app; the only no-app surfaces are the web client's own
internal GraphQL endpoints with the account's cookies (`auth_token`, `ct0`) and the
bearer token baked into the web bundle. Determine:

* whether the web-client GraphQL path is workable and stable, and what a maintained
  library around it looks like (evaluate e.g. `twscrape`, `twikit`, and the state of
  `twint`-descendants — check stars, recency, whether they still authenticate).
* whether cookies alone suffice for **sending** (posting, DMs) or whether a CSRF/`ct0`
  dance is required, and what X's bot defences do about it.
* DM support specifically — mycord is a *comms* repo, so DMs matter more than posting.
* Voice: X audio/video calls in the web client. Judge honestly whether this is reachable
  at all (it uses WebRTC with X's own signalling); if it isn't, it's a roadmap item.

Flag plainly if X's internal API churn makes this skill structurally fragile — that is
a legitimate finding, not a failure.
