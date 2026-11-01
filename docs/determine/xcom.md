# X (x.com) — library determination

**Date:** 2026-11-01 (research carried out 2026-10-25; consolidated after two provider aborts
destroyed the interim artifacts)
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a `mycord-xcom-skill`.

## Decision

**Drive X's own web-client GraphQL surface with the account's cookies — `auth_token` + `ct0`
plus the bearer token that is baked into the public web bundle — through `twifork` (the
maintained fork of `twikit`, installed as `twifork==2.4.0`, imported as `twikit`), which is
the only library that covers reads, DM history and DM *sends* with cookie auth.** Runner-up
was **`twscrape` 0.20.1**, which is far better maintained (★2,897, released three times in the
last three months) and loses on one decisive point: it has **no DM support and no send path
at all**, and DMs are the only reason X belongs in a comms repo.

Voice is **possible in principle and unbuildable today**: one third-party library (`emusks`)
implements X's call stack down to raw PCM, but it is AGPL-3.0-only, GPL-3.0-contaminated
through a dependency, distributed from a non-GitHub host that advertises having been
DMCA'd, and unproven against live X. That is an operator licensing decision, not a
technical one — see *Voice assessment*.

**Read the fragility warning at the end of *Why this one* before committing to build this.**
It is a legitimate finding, not a caveat: every maintainer in this space says the interface
breaks on a cadence measured in weeks.

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **`twifork` 2.4.0** | fork of twikit; Python client | yes | yes — tweets *and* DM inbox/history | yes — tweets, DMs, groups, reactions | Spaces via optional extra (unproven) | **yes** — `auth_token` + `ct0` | **chosen** |
| `twscrape` 0.20.1 | read-only scraper, Python | yes | yes — tweets only | **no send path at all** | no | yes — cookies first-class | runner-up; read fallback |
| `twikit` 2.3.3 upstream | Python client | yes | yes | yes | no | yes | rejected — stale, PyPI release predates 2026 breakage |
| `emusks` 2.3.20 | Node full client incl. calls | yes | yes — legacy DMs **and** XChat E2EE | yes | **yes** — WebRTC, raw PCM both ways | yes — `login(auth_token)` | only voice path; **licence blocks** |
| raw web GraphQL | hand-rolled | yes | yes | yes | no | yes | what everything above wraps; do not hand-roll |
| `ferdium` / CDP | page driver | yes | DOM-dependent | yes | no mechanism identified | yes | fallback if cookies break; untested on x.com |
| `twint` 2.1.20 | archived guest scraper | no | no | no | no | n/a | rejected — archived 2023, no session |
| `the-convocation/twitter-scraper` | JS port of n0madic | partial | partial | no | no | yes | rejected — read-only port |
| `nitter` | guest frontend | n/a | n/a | no | no | n/a | rejected — `nitter/nitter` 404s from the GitHub API; guest-only by design |
| `twurl` | official-API OAuth-1 CLI | yes | yes | yes | no | **no** — registered app | excluded by the hard bar |
| `tweepy` | official-API client | yes | yes | yes | no | **no** — registered app | excluded by the hard bar |

## Why this one

### The hard bar does hold here, but only on the cookie path

X's developer API requires a registered app, which excludes `tweepy`, `twurl` and
`twitter-api-v2` outright. The surviving no-app surface is the web client's own GraphQL
endpoints, authenticated by the session cookies a signed-in browser already holds. That is
the same shape as `mycord-slack-skill` (`xoxc` + `d`), and it is what every candidate above
wraps.

Three facts fix the shape of it, each verified in library source rather than inferred:

* **The bearer is public and static.** `twscrape/account.py` hard-codes
  `Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA`.
  Nobody registers anything to obtain it; it ships in the web bundle.
* **Cookies alone suffice for sending; the "CSRF dance" is one header.** `ct0` must be echoed
  back as `X-Csrf-Token` on state-changing calls. Verified three times over — `twikit`'s
  `_base_headers`, `emusks`' `x-csrf-token` attachment, and `emusks` refusing outright when it
  is absent (`if (!auth?.csrfToken) throw new Error("must be logged in to use xchat")`).
  There is no OAuth handshake, no app, no redirect URI.
* **Password login is gone.** X now answers `code 366, "flow name LoginFlow is currently not
  accessible"`; `/i/flow/login` redirects to a Castle-token onboarding page that wants a ~5 KB
  `$castle_token` minted by obfuscated in-page JS, with passkey/WebAuthn as first factor. The
  fork's own README, having tested it against live x.com, says: *log in with cookies, not with
  a password*. The platform has therefore closed the only route that would have needed a
  browser at setup time — which happens to remove a whole class of work.

Guest access is closed too: a logged-out request receives a ~34 KB page shell with no webpack
manifest, so `GuestClient.activate()` cannot complete and raises `InvalidSession`
(`guest/activate.json` is reachable but unusable).

### DMs decide it, and they eliminate the best-maintained option

`twscrape` — the obvious pick on every maintainability axis — enumerates exactly 25 read
verbs (`search`, `tweet_details`, `user_tweets`, `followers`, `bookmarks`, `community_tweets`,
…) and has **no `create`, `post`, `send`, `reply`, `like`, `repost`, `delete` or
`upload_media`**, and a `grep -niE 'direct.?message|\bdm_|/dm/|inbox'` across its source
returns **zero hits**. It cannot send a DM because it cannot send anything.

`twikit` and its fork have the full surface: `send_dm`, `get_dm_history`, `get_dm_inbox`,
`send_dm_to_group`, `get_group_dm_history`, `delete_dm`, message reactions, and conversation
ids built as `f'{user_id}-{await self.user_id()}'` against the v1.1 endpoints
(`/i/api/1.1/dm/new2.json`, `dm/inbox_initial_state.json`, `dm/conversation/{}.json`).
`emusks` has the widest DM surface of all (23 methods) plus XChat's E2EE successor, but is
excluded on licence.

### Why the fork and not upstream

Upstream `twikit` is effectively abandoned for this purpose: last substantive code commit
2025-04-22, PyPI 2.3.3 from 2025-02-07, **160 open issues**, including "Twikit requests now
always return Cloudflare 403 even with fresh cookies" (#396, 2026-03-29) and "Authorization:
This request looks like it might be automated" (2025-10-13). `twifork` (★64, pushed
2026-10-09, 0 open issues) exists specifically to patch the 2026 breakages, still imports as
`twikit` so the surface is identical, and documents the two things you actually need:
`client.set_cookies({'auth_token': …, 'ct0': …})` is sufficient, and the v1.1 endpoints
answer 403 with a Cloudflare page unless you install `twifork[impersonate]` and construct
`Client('en-US', impersonate='chrome124')` (curl_cffi TLS impersonation).

A 64-star single-maintainer fork as a base dependency is a real risk, and it is the reason
`twscrape` is kept as the read fallback rather than discarded.

### The structural fragility, stated plainly

This is the finding that matters more than the choice of library, and it is documented by the
maintainers themselves rather than inferred:

* `twscrape`'s last three releases (2026-08-07, 2026-08-25, 2026-10-21) each carry as their
  headline maintenance item *"Updated GraphQL operation IDs for current X API
  compatibility"*, plus "Fixed requests failing when X requires GraphQL features twscrape
  doesn't send yet" — and it introduced a dedicated `GqlFeaturesOutdatedError` for the case
  it cannot handle.
* `x-client-transaction-id` is mandatory for at least `SearchTimeline`,
  `UserTweetsAndReplies`, `Followers` and `CreateTweet`, and is derived by executing or
  re-parsing part of X's own minified bundle. `twikit` does this by running X's JavaScript
  through **Js2Py**, a Python interpreter for JS — a dependency whose failure mode is X
  shipping a different minifier.
* `the-convocation/twitter-scraper`'s README is blunt: *"Twitter's authentication
  requirements and frontend API endpoints change frequently, breaking this library. Fixes for
  these issues typically take at least a few days to go out,"* and *"any account you log into
  with this library is subject to being banned at any time."*
* A whole project, `fa0311/twitter-openapi` (★196, pushed 2026-10-24), exists solely to
  regenerate X's internal API description continuously, precisely because static bindings rot.
* X's defences are layered: Cloudflare TLS fingerprinting (defeated only by curl_cffi
  impersonation), the transaction-id header, Castle tokens at login, and `LoadShed:
  Unspecified` returned when a query is expensive — `emusks` documents that
  `DmAllSearchSlice` answered `LoadShed` for *every account tried* in September 2026.

**Conclusion: an X skill is a moving-target skill with a repair cadence measured in weeks.**
That is acceptable for a private tool used by one operator and unacceptable as a promise of
reliability. The `SKILL.md` must say so, and `docs/roadmap.md` should not list X work as
"done" in the sense the other skills are.

### Operational hazard worth naming before any code is written

`twscrape` ships **telemetry**: `twscrape/telemetry.py` batches events to
`https://app.posthog.com/batch/` under a hard-coded PostHog key, with a `distinct_id` derived
from `machineid.hashed_id("twscrape")`. It is disabled by `TWS_TELEMETRY=0` or
`DO_NOT_TRACK=1`. If `twscrape` is used as the read fallback, that is a non-negotiable
environment setting in the skill, not a preference — a comms skill must not phone home about
the operator's account.

## Voice assessment

**Verdict: possible in principle, implemented only by one unacceptable library, unverified
live. No roadmap item until the licensing question is answered.**

* **The transport exists and is WebRTC against X's own signalling.** XChat calls run on
  Periscope signalling plus a **Janus SFU**; `call.x.com` meetings use their own GraphQL
  operations, an RTC gateway called **Hydra**, and a binary event socket on `chat-ws.x.com`.
* **The media contract is PCM in, PCM out — the `signal-call-tunnel` shape.** In
  `emusks@2.3.20`, `xchat-calls.js` attaches `new RTCAudioSource().createTrack()` to the
  peer connection and `recordIncoming()` attaches `RTCAudioSink` to remote tracks;
  `xchat-call-media.js` shells out to ffmpeg to decode anything to `s16le` 48 kHz mono and
  pushes 10 ms frames (`numberOfFrames: 480`). That is the same window the Signal skill
  already handles (960 bytes at 48 kHz mono s16le), so an agent could feed both from one
  speech layer. It is **not** the `gvoice`/`werift` shape (no SIP, no per-call SDP work) and
  **not** the "capture audio out of a browser page" shape the roadmap currently assumes for
  WhatsApp and Slack.
* **The engine is `@roamhq/wrtc`** — a native Node addon ("Bun can't load it yet"). It cannot
  encode H.264, so the SFU rejects the video track, but "Audio is Opus everywhere and works."
  On this machine `npm i @roamhq/wrtc` succeeded and a Node snippet constructed
  `RTCAudioSource`, derived a track, attached `RTCAudioSink`, and pushed one 480-sample frame
  without error. *(Pre-reboot probe; the re-run after the reboot was not captured.)*
* **What is not verified:** that a call connects, that Opus negotiates with X's SFU, that
  `listenForCalls` detects an incoming call (it listens on the live event stream and falls
  back to 4-second inbox polling), or that any of it survives a week.
* **Spaces are not this.** Spaces are broadcast audio with HLS stream URLs, not 1:1 calls.
  `twifork` reads/creates/ends them and exposes an optional WebRTC extra. Different product;
  do not sell it as call voice.
* **The licensing blocker is real and is a human decision.** `emusks` is **AGPL-3.0-only**,
  distributed only via npm from `code.lgbt` (not GitHub), its README says *"officially dmca'd
  by twitter™"* and that it includes leaked ad bearers, and it pulls `cycletls`
  (**GPL-3.0**) and `x-client-transaction-id` (MIT).

Proposed roadmap text, to be added only if the licence question is ever settled in favour:

> **R4 — Voice for X calls: blocked on a licensing decision.** X calls are WebRTC against a
> Janus SFU (XChat) or the Hydra gateway (`call.x.com`), and the only implementation found
> exposes them as raw 48 kHz mono s16le PCM in both directions — the same contract
> `mycord-signal-skill` already consumes. That implementation is AGPL-3.0-only with a GPL-3.0
> dependency and has not been proven live. Do not build until the licence question is
> answered and one call is observed end to end.

No ASR/TTS roadmap item is added now, because an item that cannot be started is noise.

## Implementation shape

**Language:** Python, matching every other skill in this repo. `mycord-xcom-skill/`, laid out
like `mycord-slack-skill/` (session capture, passthrough, read verbs, dry-run-then-confirm
writes).

**Auth / session capture:** `auth_token` + `ct0`, copied out of a signed-in browser profile —
the `google-voice-skill` discipline of copying the profile rather than writing to it. Password
login is a dead end (Castle token + WebAuthn). The skill must refuse to start on a partial
cookie pair, and must detect the `duplicate ct0` failure mode twikit carries its own guard for.

**Verbs (7):**

```
mycord-xcom doctor                      # cookie present? impersonation backend? pins?
mycord-xcom session import <profile>    # copy auth_token + ct0 out of a browser profile
mycord-xcom timeline [--limit N]        # home / user timeline
mycord-xcom search <query>              # SearchTimeline — the most churn-prone verb
mycord-xcom dm list                     # dm/inbox_initial_state.json
mycord-xcom dm history <conversation>   # dm/conversation/{id}.json
mycord-xcom dm send <conversation> -m … # DRY RUN unless --confirm
mycord-xcom post -m …                   # DRY RUN unless --confirm
```

**Writes are dry-run by default**, exactly as `mycord-signal-skill` does it: an agent exploring
the surface cannot reach a person, and a human reviewing a plan sees the literal request body.

**Failure modes that will actually bite:**

| Mode | Cause | Handling |
| --- | --- | --- |
| `403` HTML from Cloudflare | plain `httpx` TLS fingerprint | require `twifork[impersonate]` + `impersonate='chrome124'`; fail at startup, not mid-call |
| naked `404` on a read | stale GraphQL operation id | surface `GqlFeaturesOutdatedError`-equivalents as "pin is stale, upgrade", never retry blindly |
| `LoadShed: Unspecified` | X shedding an expensive query | report as transient; do not treat as auth failure |
| duplicate `ct0` cookie | two cookie sources merged | detect and refuse; twikit has the same guard |
| silent session death | X rotating cookies | `doctor` must re-read cookies each run, never cache a long-lived session |
| account ban | this is automation on a platform that punishes it | the skill's `SKILL.md` carries a ban warning on the Discord selfbot's terms |

**Do not** build the read path on `twscrape` *and* the write path on `twifork` in one skill by
default: two GraphQL clients means two copies of the transaction-id derivation and two pin sets
to keep current. `twscrape` is a documented fallback for reads if `twifork`'s Cloudflare
handling breaks, not a co-equal dependency.

## Open questions

A human must decide or provide:

1. **An X account.** None exists on this host — no `auth_token`, no `ct0`, no `twscrape`
   accounts database, and none of the candidate libraries is installed. Nothing in this
   determination was exercised against live X.
2. **Is an AGPL-3.0-only dependency acceptable in this repo?** This single answer determines
   whether X voice and XChat E2EE are reachable at all, or permanently out of scope. It also
   decides whether `cycletls` (GPL-3.0) is ever linked.
3. **Posting, or DMs only?** Posting is the noisiest surface on the platform and carries the
   most ban risk; DMs are what a comms skill is for. The verb set above includes both, and
   dropping `post` would materially reduce exposure.
4. **Which account bears the ban risk.** Per the Discord selfbot precedent: a throwaway, never
   a work or customer-facing one.
5. **Whether to build at all**, given the churn finding. A defensible answer is "no skill;
   use the web client by hand for the twice-a-year case that needs X".

## Evidence discipline

Everything above is registry data, published source, vendor/maintainer documentation, or a
local probe of an installed package. **No live X session, read, send, DM or call exists in this
record**, and the full evidence dump — including what was destroyed by a reboot and what was
never re-verified — is preserved beside this file as
[`xcom-research-notes.md`](xcom-research-notes.md). The template `## Decision` sentence is gone
from this file, so this is a conclusion, not a scaffold.