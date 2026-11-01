# X (x.com) — research evidence dump

**Researched:** 2026-10-25 (registry/GitHub/npm queries and local probes in throwaway venvs and
`/tmp/xd2`). **Consolidated:** 2026-11-01, after two provider aborts and a machine reboot.
**Status:** evidence only. The determination this fed is [`xcom.md`](xcom.md) in this directory,
written 2026-11-01 from this evidence.

This is **not** a determination and deliberately draws no conclusion. It records what was
checked, with the command or source, and separates that from what is merely expected.

## Method and its limits

* No X account, cookie or token was ever used. No message was sent. No session was created.
  Every claim about *behaviour against live X* below is source-reading, vendor documentation,
  or a maintainer's own report — never an observation of mine.
* What was actually executed locally: PyPI JSON API and `pip index versions`; GitHub REST API
  (unauthenticated); `npm view`; `curl` of raw.githubusercontent and of `emusks.tiago.zip` docs;
  `npm pack`/tarball extraction and inspection of `emusks@2.3.20`; `uv pip install` of
  `twscrape==0.20.1` and `twifork==2.4.0` into throwaway Python 3.12 venvs; `twscrape version`
  and bare `twscrape`; `python -c "import twikit; dir(Client)"`; `npm install @roamhq/wrtc` plus
  a Node snippet constructing `RTCAudioSource`/`RTCAudioSink` and pushing one 10 ms frame.
* **Lost artifact disclosure.** `/tmp/det-xcom.log`, `/tmp/tws_http.py`, `/tmp/twikit_client.py`,
  `/tmp/twikit_v11.py`, `/tmp/twifork_client.py`, `/tmp/em-*.md` and `/tmp/xdet/` were destroyed
  by a reboot between research and consolidation. A re-probe into `/tmp/xd2` was launched but its
  log did not survive either. Everything below is reconstructed from the session record of the
  first pass; **nothing below was re-verified after the reboot**, and where a fact depended on a
  deleted file I say so rather than presenting it as freshly confirmed.

---

## Registry facts (verified)

| Claim | Check |
| --- | --- |
| `twscrape` 0.20.1, uploaded 2026-08-25, `requires_python >=3.10`, MIT, `github.com/vladkens/twscrape` | `pip index versions twscrape`; `pypi.org/pypi/twscrape/json` |
| `twikit` 2.3.3, uploaded 2025-02-07, `requires_python >=3.8`, MIT, `github.com/d60/twikit` | same |
| `twifork` 2.4.0, `requires_python >=3.10`; releases 2.3.3 (2026-06-05), 2.3.4 (06-06), 2.3.5 (07-05), 2.4.0 (2026-08-30), homepage `github.com/PawiX25/twifork` | `pypi.org/pypi/twifork/json` |
| `twint` 2.1.20, last upload **2020-04-29** | `pypi.org/pypi/twint/json` |
| `twitter-api-client` 0.10.22, uploaded 2024-04-21 | same |
| `tweepy` 4.17.0, uploaded 2026-07-02 (official-API client, so not a candidate — app registration) | same |
| `vladkens/twscrape` ★2,897, pushed 2026-10-21, not archived, 14 open issues, MIT | GitHub REST `/repos` |
| `d60/twikit` ★4,752, pushed 2026-03-10, not archived, **160 open issues**, MIT | same |
| `twintproject/twint` ★16,405, pushed 2023-02-23, **archived: true**, 589 open issues | same |
| `trevorhobenshield/twitter-api-client` ★1,900, pushed 2024-05-22, 80 open issues | same |
| `the-convocation/twitter-scraper` ★640, pushed 2026-04-01, 51 open issues, MIT | same |
| `PawiX25/twifork` ★64, pushed 2026-10-09, not archived, **0 open issues**, MIT | same |
| `fa0311/twitter-openapi` ★196 pushed 2026-10-24; `-typescript` ★223 pushed 2026-10-23; licence "NOASSERTION" = dual AGPL-3.0 / custom | same |
| `nitter/nitter` — GitHub API returns **404 Not Found** | `GET /repos/nitter/nitter` |
| npm `emusks` 2.3.20, modified 2026-10-19, **AGPL-3.0-only**, repo `code.lgbt/tiago/emusks`, 98 files, unpacked 4,648,054 B | `npm view emusks`; `dist` block |
| npm `x-client-transaction-id` 0.3.2, modified 2026-09-26, MIT, `github.com/Lqm1/x-client-transaction-id` | `npm view` |
| npm `cycletls` 2.0.5, modified 2025-10-02, **GPL-3.0** — an emusks dependency | `npm view` |
| npm `@the-convocation/twitter-scraper` 0.22.3, modified 2026-04-01 | `npm view` |
| npm `twitter-api-v2` 1.31.0, modified 2026-10-25, Apache-2.0 — official API only | `npm view` |
| `code.lgbt/tiago/emusks` resolves (HTTP 302) | `curl -o /dev/null -w '%{http_code}'` |

---

## Candidate 1 — `twscrape`

### Verified

* **It is a read-only scraper. There is no send path at all.** Enumerating
  `twscrape/api.py` yields exactly: `search`, `search_user`, `search_trend`, `user_by_id`,
  `user_by_login`, `user_about`, `tweet_details`, `tweet_replies`, `tweet_thread`, `retweeters`,
  `followers`, `verified_followers`, `following`, `subscriptions`, `user_tweets`,
  `user_tweets_and_replies`, `user_media`, `list_timeline`, `list_members`, `trends`, `bookmarks`,
  `community_members`, `community_moderators`, `community_tweets`, `community_info` — each with a
  `_raw` twin. No `create`, `post`, `send`, `reply`, `like`, `repost`, `delete`, `upload_media`.
* **It has no DM support whatsoever.** `grep -niE 'direct.?message|\bdm_|/dm/|inbox'` across
  `api.py`, `cli.py`, `models.py`, `queue_client.py`, `login.py` returned **zero** hits (run twice,
  second time after the reboot, same result).
* **Cookie auth is first-class and is exactly the no-app shape wanted.**
  `twscrape/account.py` defines `has_required_cookies()` as `all(cookies.get(n) for n in
  ("auth_token", "ct0"))`. The CLI verb is `twscrape add_cookie <local-label> <cookie-string>`;
  the README states cookie accounts "are activated immediately; no `login_accounts` step is
  needed", and that `auth_token` + `ct0` is the stable set.
* **The public web bearer is baked into the source**, not registered:
  `TOKEN = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"`
  in `account.py`.
* **Installed and ran.** `uv pip install twscrape` into a Python 3.12 venv → `twscrape==0.20.1`.
  `twscrape version` printed `twscrape: 0.20.1` / `SQLite runtime: 3.50.4 (3.50.4)`. Bare
  `twscrape` prints usage listing the verb set above. Note: **`twscrape --help` is not a thing** —
  it exits with `error: unrecognized arguments: --help`.
* **It ships telemetry.** `twscrape/telemetry.py` posts batched events to
  `https://app.posthog.com/batch/` with hard-coded PostHog key
  `phc_giRHYHo4460O5UPxySajdO9L4KDRsjSmNQACA7uG9px`, and derives a stable `distinct_id` from
  `machineid.hashed_id("twscrape")[:16]`. It is switchable off: `_is_disabled()` returns true when
  `TWS_TELEMETRY=0` **or** `DO_NOT_TRACK=1`.
* **It re-implements the anti-bot header itself.** `twscrape/xclid.py` fetches the x.com page,
  parses the `twitter-site-verification` meta tag, locates the `ondemand.s` bundle by regex,
  downloads it, and derives `x-client-transaction-id`; it also handles the
  `https://x.com/x/migrate` POST for migrated accounts. ~14 KB of code devoted to this one header.
* **Cloudflare forces a TLS-fingerprint backend for some endpoints.** README: "Cloudflare blocks
  `httpx` on some operations, for example `user_by_id`, so they need `curl-cffi`" —
  `pip install "twscrape[curl]"` then `TWS_HTTP_BACKEND=curl`. The default backend is httpx; both
  are wrapped behind one `Response` shim in `twscrape/http.py`, with `fake_useragent` supplying UA.
* **Dependencies** (pyproject): `aiosqlite`, `fake-useragent`, `httpx`, `loguru`, `pyotp`,
  `beautifulsoup4`, `py-machineid`; optional `curl` extra = `curl-cffi>=0.7.0`. No native code.
* **Multi-account rotation is the core design.** `accounts_pool.py` implements per-endpoint
  queues with `lock_until(queue, unlock_at)`, `get_for_queue_or_wait`, `reset_locks`,
  `mark_inactive`, `reactivate`, and reports "Total / Active / Inactive" plus per-queue locks via
  `twscrape stats`. README: "rotates accounts when an endpoint is rate-limited" and, separately,
  "X/Twitter's Terms of Service discourage using multiple accounts."
* **PyPI lags the repo.** The repo `changelog.md` has a `v0.21.0 – 2026-10-21` entry (a breaking
  change: failed requests now raise `AbortReqError`/`ApiError`/`XClIdParseError`/`UserParseError`
  instead of returning `None`) while PyPI's newest upload is 0.20.1 from 2026-08-25.

### Expected / unverified

* That `add_cookie` + these endpoints succeed against live X today. Never executed with a real
  cookie.
* That the `curl-cffi` extra is sufficient for the blocked endpoints. Read from README only.
* Actual rate limits per endpoint. Not measured; the library treats them as dynamic server state.

---

## Candidate 2 — `twikit` (upstream) and `twifork` (maintained fork)

### Verified

* **DM support exists in twikit and is not trivial.** `twikit/client/client.py` defines
  `_send_dm`, `send_dm`, `get_dm_history`, `send_dm_to_group`, `get_group_dm_history`,
  `delete_dm`, `add_reaction_to_message`, `remove_reaction_from_message`;
  `twikit/message.py` gives `Message.reply()`, `.add_reaction()`, `.remove_reaction()`,
  `.delete()`. Conversation ids are constructed as `f'{user_id}-{await self.user_id()}'`, and the
  inbox state is reachable via `DM_INBOX = /i/api/1.1/dm/inbox_initial_state.json`.
* **Endpoints** (`twikit/client/v11.py`): `DM_NEW = /i/api/1.1/dm/new2.json`,
  `DM_INBOX = /i/api/1.1/dm/inbox_initial_state.json`,
  `DM_CONVERSATION = /i/api/1.1/dm/conversation/{}.json`,
  `CONVERSATION_UPDATE_NAME = /i/api/1.1/dm/conversation/{}/update_name.json`.
* **`ct0` is the CSRF token and cookies alone are enough to send.** `_get_csrf_token()` is
  literally `return self.http.cookies.get('ct0')`; `_base_headers` sets `authorization: Bearer
  {self._token}`, `X-Twitter-Auth-Type: OAuth2Session`, `X-Twitter-Active-User: yes`,
  `Referer: https://x.com/`, and adds `X-Csrf-Token` **only if** the cookie is present. There is
  also `_remove_duplicate_ct0_cookie()`, i.e. duplicate `ct0` values are a real failure mode.
  This answers the brief's question directly: **no OAuth dance, no app; the "CSRF dance" is
  copying the `ct0` cookie into an `X-Csrf-Token` header.**
* **Upstream twikit is stale and its PyPI release is reported broken.** Last substantive code
  commit 2025-04-22; the only 2026 commit is a README edit (2026-03-10). PyPI 2.3.3 is from
  2025-02-07. 160 open issues, including "Twikit requests now always return Cloudflare 403 even
  with fresh cookies" (#396, last updated 2026-03-29), "status: 429, error" (2026-08-19), and
  "Authorization: This request looks like it might be automated" (2025-10-13).
* **`twifork` is a drop-in fork that still imports as `twikit`.** `uv pip install twifork` →
  `twifork==2.4.0`; `import twikit` → `__version__ == '2.4.0'`; `from twikit import Client`
  constructs; `dir(Client)` contains `send_dm`, `get_dm_history`, `get_dm_inbox`, `delete_dm`,
  `delete_dm_conversation`, `add_reaction_to_message`, `remove_reaction_from_message`,
  `create_group`, `get_group`, plus `is_logged_in`, `load_cookies`, `save_cookies`, `get_cookies`.
  (Verified through the installed package's public surface; the 6,075-line `client.py` was also
  fetched and grepped while it existed in `/tmp`, now gone.)
* **twikit carries its own transaction-id implementation**, `twikit/x_client_transaction/`
  (`transaction.py`, `cubic_curve.py`, `interpolate.py`, `rotation.py`, `utils.py`) — it parses
  `ondemand.s` with `bs4` + regexes `ondemand\.s` and `(\(\w{1}\[(\d{1,2})\],\s*16\))+`, and its
  `setup.py` depends on **`Js2Py-3.13`**, i.e. it executes X's minified JS through a Python
  interpreter. That is a structural fragility, not a detail.
* **`twikit`/`twifork` cannot log in with a password any more, per the fork's own README**
  (claims verified by its author against live x.com on 2026-07-29): X answers
  `code 366, "flow name LoginFlow is currently not accessible"`; `/i/flow/login` now redirects to
  `/i/jf/onboarding/web`, which needs a ~5 KB `$castle_token` minted by obfuscated in-page JS and
  offers passkey/WebAuthn as first factor. The fork's instruction is: **"Log in with cookies, not
  with a password."** `client.set_cookies({'auth_token': ..., 'ct0': ...})` is documented as
  sufficient.
* **Guest access is closed.** The fork README states `GuestClient.activate()` "does not even get
  that far" because a logged-out request only receives a ~34 KB page shell with no webpack
  manifest, so the transaction-id handshake cannot complete and `activate()` raises
  `InvalidSession`; `/1.1/guest/activate.json` is alive but unreachable. Corresponding upstream
  evidence: convocation issue "Bad Guest Token - Code 239" (2026-06-08), twikit issue #192.
* **TLS impersonation is required for the v1.1 endpoints.** twifork: `Client('en-US',
  impersonate='chrome124')` via `pip install "twifork[impersonate]"` (curl_cffi), otherwise
  "the v1.1 endpoints answer 403 with a Cloudflare page".
* **The fork adds X Spaces**: `client.spaces.get_space`, `.get_stream` (HLS url), `.chat(space)`,
  `.create_space(title=)`, `.end_space(id)`, with an optional `twifork[spaces]` extra for "WebRTC
  voice + live chat WebSocket", and an `examples/spaces.py`.

### Expected / unverified

* That twifork's DM send/read actually works against live X today. Never executed.
* Any claim that the fork's Cloudflare fixes are durable — it is a 64-star, single-maintainer fork
  and its whole value proposition is patching breakage that keeps recurring.
* The Spaces voice extra's shape (WebRTC voice in a Python client). Not inspected; no extra installed.

---

## Candidate 3 — `twint` and its descendants

### Verified

* `twint` is **archived** on GitHub (`archived: true`), last push 2023-02-23, last PyPI upload
  2020-04-29. It does not authenticate at all (it was a guest/scrape scraper), so `ct0`/DM/send
  were never in scope, and it cannot read anything X now requires a session for.
* No maintained successor surfaced in the registry queries: the notable live descendants are
  `the-convocation/twitter-scraper` (a port of n0madic, "a port of the now-archived
  n0madic/twitter-scraper"), and the `fa0311/twitter-openapi` generated-client family.

### Assessment (labelled as such)

`twint` descendants are read-only, session-less, and in the one archived case dead. Even if a
descendant worked, it forfeits DMs and sends by construction. Not a candidate for a comms repo.

---

## Candidate 4 — `nitter` / `twurl`

### Verified

* `nitter/nitter` is **404 from the GitHub API** — the repo does not currently resolve at that
  path. I did not establish where it went (moved, deleted, or renamed); no further check was made.
* `twurl` is a Ruby OAuth-1 CLI **for the official API**, i.e. it requires a registered app's
  consumer key/secret plus an access token. It is the definition of the excluded class. (Registry
  check not performed; class alone disqualifies it.)

### Expected / unverified

* Nitter's current state and whether any instance serves X at all. Not tested. Nitter is
  guest-only by design, so it would forfeit DMs, sends and calls regardless.

---

## Candidate 5 — raw web-client GraphQL with `auth_token` + `ct0`

This is the path every candidate above is a wrapper around. What was established about it:

### Verified

* **The bearer is public and static.** twscrape's `account.py` hard-codes the web bearer
  (`...ANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7...`); twikit sets the same style of
  `authorization: Bearer {self._token}`. Nobody registers an app to obtain it.
* **`ct0` (the CSRF cookie) must be echoed as `X-Csrf-Token` on state-changing calls.** Verified
  in twikit's `_base_headers` and, independently, in `emusks`' source, which attaches
  `"x-csrf-token": instance.auth.csrfToken` on media upload, tweet creation, XChat and call
  requests, and *refuses* the call outright when it is absent
  (`if (!auth?.csrfToken) throw new Error("must be logged in to use xchat")`).
* **`x-client-transaction-id` is mandatory and is derived from X's own JS bundle.** Independent
  write-ups agree on the shape (method + path + time + the literal keyword `obfiowerehiring` +
  an animation key parsed out of `ondemand.s`), and at least three GraphQL operations are
  reported to return a *naked 404* without it: `SearchTimeline`, `UserTweetsAndReplies`,
  `Followers`, plus `CreateTweet`. Sources: `udp.re/blog/x-client-transaction-id`,
  `sneakerdev.com/blog/twitter-header-part-2-reverse-engineering`, and several repo write-ups
  (`glizzykingdreko/twitter-generator` LEARN.md, `tjdwls101010/Agentic-X`
  docs/wiki/Transaction-ID.md). Verified in code three times over: `twscrape/xclid.py`,
  `twikit/x_client_transaction/`, and `emusks`' `src/transaction.js` (which refreshes a
  logged-in x.com home page through cycletls with `ja3`/`ja4r` fingerprints on a 10-minute TTL,
  with a 15 s socket timeout).
* **Cloudflare fronts at least the v1.1 surface and judges TLS fingerprints.** twscrape and
  twifork both document `403` HTML on a plain `httpx` TLS handshake and prescribe curl_cffi /
  impersonation. `__cf_bm` and `guest_id` cookies are documented in a third-party analysis
  (`xiaoxiunique/x-kit` ANALYSIS.md).
* **GraphQL operation ids churn on a scale of weeks, and maintainers say so in their changelogs.**
  twscrape `changelog.md` contains, in three consecutive releases (2026-08-07, 2026-08-25,
  2026-10-21): "Updated GraphQL operation IDs for current X API compatibility"; "Fixed X client
  transaction ID generation for builds using 16-character chunk hashes"; "Fixed requests failing
  when X requires GraphQL features twscrape doesn't send yet"; and it introduced a dedicated
  `GqlFeaturesOutdatedError`. `the-convocation/twitter-scraper`'s README states plainly:
  "Twitter's authentication requirements and frontend API endpoints change frequently, breaking
  this library. Fixes for these issues typically take at least a few days to go out," and "any
  account you log into with this library is subject to being banned at any time."
* **`fa0311/twitter-openapi` is the systematic answer to the id churn**: a generated OpenAPI spec
  of X's internal API, regenerated continuously (pushed 2026-10-24) with generators for Python,
  TypeScript and Dart, plus an HTTP relay that runs requests through a *logged-in browser*. Its
  licence is dual AGPL-3.0 / custom.

### Expected / unverified

* Whether any of these headers/ids are sufficient *today*. All churn evidence says any given
  query id has a short shelf life; nothing here was exercised live.
* Whether `ct0` rotation invalidates long-lived sessions (X is reported to rotate cookies; not
  observed by me).

---

## Candidate 6 — `emusks` (the only candidate with calls)

Discovered late; it is the only library found that implements X's **voice/video call** stack.

### Verified (from the published `emusks@2.3.20` tarball and its docs site)

* **Auth is the same no-app shape:** `await client.login("your_auth_token")` — cookie only — with
  password (requires a real browser for the Castle token), emailed-code, and guest variants.
* **`dms.*` is a complete legacy-DM surface** (`types/helpers/dms.d.ts`): `inbox`,
  `inboxTimeline`, `conversation`, `send`, `accept`, `leave`, `markRead`, `search`,
  `searchGroups`, `searchPeople`, `block`, `unblock`, `deleteConversations`, `updateLastSeen`,
  `muted`, `edit`, `permissions`, `nsfwFilter`, `updateRelationship`, `reportSpam`, `report`,
  `userUpdates`, `typing`. `src/helpers/dms.js` shows `send()` POSTing to `${DM_API}/new2.json`
  with `{conversation_id, recipient_ids: false, request_id: <uuid>, text, cards_platform:
  "Web-12", include_cards: 1, ...}` and optional `media_id` / `reply_to_dm_id` / `tweet_id`.
* **XChat (E2EE DMs, chat.x.com) is reverse-engineered end to end**: `createIdentity({pin})`,
  `loadIdentity`, `recover(pin)`, `message`, `read`, `conversations`, `react`, `edit`, `typing`,
  `canMessage`, `isOnXChat`, `publicKey`, `fingerprint`. Identity = two P-256 keypairs
  (identity + signing) whose private halves are backed up through **Juicebox** WASM under a
  4-digit PIN, matching the official app; `selfCustody: true` skips the backup. The tarball
  contains `xchat-crypto.js` (565 lines), `xchat-decode.js` (451), `xchat-juicebox.js`, and
  `juicebox-sdk_bg.wasm`, consistent with the doc.
* **Calls are real WebRTC against X's own SFU, reachable from a Node process — not from a page.**
  `xchat.call / answerCall / listenForCalls / startGroupCall / joinGroupCall / useWebRTC(engine)`;
  `xcalls.start / join / rtc / events` for `call.x.com`. Per the docs: XChat calls run on
  **Periscope signalling + a Janus SFU**; `call.x.com` meetings use their own GraphQL operations,
  an RTC gateway called **Hydra**, and a binary event socket on `chat-ws.x.com`.
* **The media contract is PCM in, PCM out — the same shape as `signal-cli`'s pipe mode.**
  Verified in `src/helpers/xchat-calls.js`: `this.audioSource = new ns.RTCAudioSource(); this.pc
  .addTrack(this.audioSource.createTrack())`, and `recordIncoming()` attaches
  `new ns.RTCAudioSink(track)` to remote tracks. Verified in `src/helpers/xchat-call-media.js`:
  `streamAudioFile()` shells out to **ffmpeg** to decode any input to `s16le` 48 kHz mono and
  pushes 10 ms frames with `source.onData({ samples: Int16Array, sampleRate: 48000,
  bitsPerSample: 16, channelCount, numberOfFrames: 480 })`. So an agent can feed arbitrary PCM
  and receive raw PCM without touching a browser.
* **The WebRTC engine is `@roamhq/wrtc`** (optional peer dependency, native addon, Node only —
  "Bun can't load it yet"). It cannot encode H.264, so video does not interop with the official
  app and the `call.x.com` SFU rejects the video track (`videoRejected`), but "Audio is Opus
  everywhere and works."
* **The PCM path itself works on this machine.** `npm i @roamhq/wrtc` succeeded, and a Node
  snippet constructed `RTCAudioSource`, derived a track, attached `RTCAudioSink`, pushed one
  480-sample 10 ms frame and stopped the sink without error. *(That probe ran pre-reboot; the
  re-run after the reboot was not captured.)*
* **Call control is evented and complete**: `connected`, `track`, `mediastatus`, `live`, `ended`,
  `error`; room ops `chat()`, `react()`, `raiseHand()`, `mute()/unmute()`, `cameraOn/Off`,
  `screenShareOn/Off`, `setCallType`, `acceptJoin`/`rejectJoin`, `pin`, `subscribe`;
  `recordIncoming({audioPath})` with a per-participant filename function.
* **Rate-limit-adjacent honesty from the vendor**: `dms.search` "Uses the GraphQL
  `DmAllSearchSlice` query… In September 2026 Twitter answered this query with `LoadShed:
  Unspecified` for every account tried, so be ready for it to fail."
* **Distribution and licence are a problem.** npm only, **AGPL-3.0-only**, hosted on
  `code.lgbt` rather than GitHub, README says "officially dmca'd by twitter™" and "includes a few
  leaked ads bearers". It pulls `cycletls` (**GPL-3.0**) and `x-client-transaction-id` (MIT).

### Expected / unverified

* That `emusks` works against live X at all. **No session, no `auth_token`, nothing executed.**
  Every behavioural claim above is its own documentation or readable source.
* That calls can be *placed or answered* by this account, and that a Janus/Periscope peer
  connection establishes from this host. Not attempted.
* Whether AGPL-3.0 is acceptable for a skill in this repo. A licensing decision, not a technical
  one; not resolved here.

---

## Candidate 7 — Ferdium / CDP

### Verified

* The repo already has `ferdium-skill` as a **base driver**: it launches Ferdium headless on Xvfb
  or headed, discovers per-service `<webview>` CDP targets, and exposes page primitives
  (`type`, `click`, `send`, `wait`). Its README states it "knows nothing about any particular chat
  medium" and that medium-specific DOM work belongs in a layered skill.
* `ferdium-skill/src/mycord_ferdium/` = `cdp.py`, `cli.py`, `launcher.py`, `page.py`;
  `doctor` exits 2 if Ferdium is absent and 3 if installed with no services; headless is refused
  until a human has added a service.

### Expected / unverified

* Whether x.com behaves acceptably inside a Ferdium webview (virtualised feed, focus/visibility
  handling, notification permissions). **Not tested — Ferdium was never launched for X.**
* Whether call audio would flow through a Ferdium webview's WebRTC and be capturable via CDP.
  Not tested; no mechanism identified.

---

## Cross-cutting answers to the brief's questions

### Does cookies alone suffice for sending, or is there a CSRF dance?

**Verified in three independent codebases:** cookies suffice, and the dance is one header.
`auth_token` authenticates the session; `ct0` must be sent back as `X-Csrf-Token` on writes
(twikit `_base_headers`, emusks `x-csrf-token`, emusks' hard refusal without it), and
`x-client-transaction-id` must be computed per request from X's own bundle for the gated
operations. X's bot defences are layered on top of that: Cloudflare TLS fingerprinting (defeated
by curl_cffi), the transaction-id header (defeated by re-parsing `ondemand.s`), Castle tokens at
login (requires a real browser), and `LoadShed` when a query is expensive.

### Is DM support reachable without an app?

**Verified as reachable in library code, unverified live.** twikit/twifork (`send_dm`,
`get_dm_history`, group DMs, reactions, delete) and emusks (`dms.*`, 23 methods) both speak the
v1.1 `dm/*` endpoints with cookie auth. `twscrape` cannot do DMs at all. XChat (the E2EE successor)
is also implemented, by emusks only, and requires creating an identity (a key-pair publish + PIN
backup) — a one-time setup step with real consequences, not a transparent detail.

### Voice: are X audio/video calls reachable, and how?

**Verified that a path exists in code; not verified that it works.** The transport is WebRTC
against X's Periscope-signalling + Janus SFU (XChat calls) or the Hydra RTC gateway
(`call.x.com`). `emusks` drives both from Node with `@roamhq/wrtc`, exposing **raw PCM in and out**
(`RTCAudioSource.onData` / `RTCAudioSink`), 48 kHz s16le mono, with ffmpeg for file I/O. That is
the `signal-cli` + `signal-call-tunnel` pipe shape, **not** the `gvoice` `werift` shape exactly
(no SIP, no per-call SDP work for the caller), and **not** the "capture audio out of a browser
page" shape the roadmap currently assumes for WhatsApp/Slack.

What was *not* established: that a call connects, that Opus negotiates, that incoming call
detection works via `listenForCalls` (which the docs say listens on the live event stream and
falls back to 4 s inbox polling), or that any of this survives X's churn. Anything written about
X voice must be labelled "implemented by a third-party AGPL library, unproven here".

### Spaces

twifork reads/creates/ends Spaces and can fetch HLS stream URLs, with WebRTC voice behind an
optional extra. emusks has a broader `spaces.*` surface (`get`, `search`, `stream`, `broadcast`,
`subscribe`). Neither was exercised. Spaces are broadcast audio, not 1:1 calls — a different
product from what "voice for a comms skill" usually means.

### Is X's internal API churn a structural problem?

**Yes, and it is documented by the maintainers themselves, not inferred by me.** Evidence in one
place: three consecutive twscrape releases whose only maintenance item is "Updated GraphQL
operation IDs for current X API compatibility"; twikit's need to *execute X's minified JS* via
Js2Py to compute one header; twikit going from ★4.7k and active to no code commits since April
2025 while a 64-star fork exists specifically to patch the 2026 breakages; convocation's
"typically take at least a few days to go out"; emusks' `LoadShed` note; and the whole
`fa0311/twitter-openapi` project existing to regenerate the API description continuously. Any
X skill built on this is a moving-target skill with a repair cadence measured in weeks.

---

## Not verified anywhere in this research

* No live X session, send, DM, call, or read against x.com exists in this record.
* No `auth_token`/`ct0` was available, requested, or used.
* Rate-limit numbers per endpoint.
* Whether `emusks` (or twifork) currently works at all against production X.
* Whether X Chat's E2EE metadata handling (the docs note encryption "does not cover associated
  metadata") matters for agent use.
* Ferdium behaviour with x.com.
* Licensing fit of AGPL-3.0 (`emusks`) or GPL-3.0 (`cycletls`) for this repo. **A human decision.**