# Implementation decision — planned comms skills

**Date:** 2026-10-25
**Decider:** mycord-agent (orchestrator)
**Scope:** which of the newly determined mediums get implemented now, and why.

---

## Decision

**Implement two now: Zoom, and Signal's dependency gathering.**

| Medium | Verdict | Why |
| --- | --- | --- |
| **Zoom** | **implement, full skill** | Voice verified full duplex on this host, the only generalizable voice shape, and the guest-join path needs **no account** — so it is verifiable end-to-end today. |
| **Signal** | **implement deps + scaffold** | Voice already proven and it is the one medium with a working call stack. But both pinned components are **behind**, and the only account on this host is dead. Gather and pin the deps now; live-verify when a phone is available. |
| X / Twitter | **deferred** | Determination not finished. Deciding before it reports would be guessing. |
| Messenger | **deferred** | Web client over CDP is sound, but it needs a signed-in Facebook account to do anything, and there is none here. Its voice finding (ordinary WebRTC in the page) is valuable and already recorded. |
| Teams | **deferred** | Needs a work/school tenant and admin consent questions the human has not answered. Building it would be untestable code. |
| WeChat / WeCom | **deferred** | Needs an organization tenant; personal-surface automation is prohibited and carries repo-level risk. |
| Skype | **not implemented** | Retired upstream 2025-05-05. No skill. |

## Why Zoom first

It is the only one where all three conditions hold at once:

1. **The capability is new.** Every existing mycord skill can read and send text. Zoom is
   the first that can carry **voice** for a medium that refuses third-party media — the
   PipeWire virtual-device shape verified in `docs/determine/zoom.md`. Nothing else in the
   repo can do this, and it generalizes to Teams and every other conferencing client.
2. **It can be verified without an account.** `zoommtg://` joins a meeting as a guest.
   So the audio plane, the deep-link mapping and the CLI can all be tested here, today,
   by the orchestrator rather than deferred on trust.
3. **The risk is bounded.** Nothing touches a real person's account, no message is sent to
   a human, and the vendor-risk (Zoom markets RTMS *instead of* client-driving agents) is
   documented rather than hidden.

## Why Signal is deps-only

The user's original framing was that Signal's "dependencies are scattered around" and need
gathering into the skill. That is the real work, and it is worth doing independently of an
account:

* `signal-cli` installed is **0.14.6**; the call path needs **≥0.14.9** (the
  linked-device `local_device_id` fix, #2128).
* `signal-call-tunnel` checkout is **2f92426**; the required build is **5a963d1**
  (PR #5's pipe backend **plus** the ICE device-id follow-up).
* There is no prebuilt binary and no crates.io crate; the checkout is 718 MB and needs
  `./build.sh` (which patches the ringrtc submodule before building).

The skill must therefore **pin and detect**, not assume. A version mismatch here fails at
call setup — the worst possible moment — so detection is the primary deliverable.

**What cannot be verified here:** the only Signal account on this host is dead
(`+14153356728` → `NotRegisteredException`), and linking a new one needs a human with the
Signal app. So the skill ships with offline tests and an explicit "unverified against a
live account" statement. It must not claim more than that.

## Sequencing

Both proceed in parallel in their existing determination workspaces, because each already
has its determination, its graft index, and its herdr tab.

## Standing constraints

Inherited from the repo, non-negotiable for both:

* No semantic string-parsing of protocol responses; no brittle selectors.
* Never fan out sends; never send to a real person without the human's say-so.
* Audio devices created during testing must be removed and the user's routing restored —
  the Zoom determination re-routed the system default sink during verification, which is
  exactly the footprint to avoid leaving behind.
* Report unverified claims as unverified. A skill that overstates what it can do is worse
  than one that says less.
