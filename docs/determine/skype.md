# Skype — library determination

**Date:** 2026-10-25
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a `mycord-skype-skill`.

---

## Headline finding: this medium should not be built

**Consumer Skype is shut down, not merely deprecated, and the shutdown has been
completing on a schedule that is already past.** Microsoft's own support page says, in
the first paragraph, "As of May 5th, 2025, Skype is retired." The same page states that
Skype chat and call history was available "until January 2026", that export must be
requested "before data deletion begins on April 1, 2026", and that for accounts that did
not migrate, "your data will be completely deleted after June 15th 2026"
(<https://support.microsoft.com/en-us/skype/skype-is-retiring-in-may-2025-what-you-need-to-know>,
fetched 2026-10-25). Today is 2026-10-25: every one of those dates is in the past.

What is still alive is a very small, paid-only remnant, and it is **not a messaging
service**. `skype.com`, `web.skype.com`, `join.skype.com`, `go.skype.com` and the Linux
download link all 301/302 to `teams.live.com`. `calling.web.skype.com` is the only
Skype-branded surface still serving an application, and it is a **PSTN dial pad**: it
requires an active legacy Skype subscription, Skype Number, or Skype Credit, and it only
does outbound calls to phone numbers. Microsoft stopped selling those products to new
customers at retirement, so the surviving user base is a closed, shrinking set of legacy
balance holders.

The honest engineering conclusion is therefore: **there is no `mycord-skype-skill` to
write, because the messaging product that such a skill would drive no longer exists.**
Skype-on-the-wire is now Microsoft Teams (consumer), and a Teams determination is being
written in parallel in this fan-out
(`tooling/mycord-teams/docs/determine-teams.md` — as of today it covers the
work/school `teams.cloud.microsoft` side and explicitly leaves `teams.live.com`
unprobed). Building a Skype
skill would in practice mean rebuilding the Teams skill against a legacy-branded surface,
with worse features and a customer base that is being deleted.

Everything below is the evidence for that, in the order the brief asked for it.

---

## Decision

**Do not build `mycord-skype-skill`.** The strongest available path, if a human insists
on "Skype" specifically, is the **legacy messaging protocol over CDP** — drive
`teams.live.com` (consumption + send) with the session Chromium already holds, reusing
the `mycord-slack-skill` capture pattern, and treat `msgapi.teams.live.com` as the API
host behind it; but that path is (a) no longer a Skype protocol, (b) unmaintained in
every existing library, and (c) only worth doing as part of the Teams skill, not beside
it. The runner-up (and the only *live* Skype-branded application) is the Dial Pad at
`calling.web.skype.com`, which is rejected as a primary because it requires a legacy paid
entitlement, exposes no chat, and its own media stack is Teams' — so it is one capability
of Teams, not a medium.

---

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
|---|---|---|---|---|---|---|---|
| `teams.live.com` web client over CDP (Skype's redirect target) | 4 | yes (in-page data + `msgapi`) | yes, in-page (scroll/paging) | yes | yes — WebRTC/`sdp-ngc` calling stack, reachable only in-page | yes (browser session, no app reg) | **Only viable path; belongs to Teams, not Skype** |
| Skype Dial Pad `calling.web.skype.com` | 4 | call log only | call log only | outbound PSTN calls, no chat | yes, but Teams media stack + paid entitlement | yes | Rejected: closed legacy entitlements, no chat |
| `SkPy` (PyPI) | 2 | no — endpoints gone | no | no | no | yes | **Dead.** Archived 2025-03-13 |
| `skype4py` (PyPI 1.0.35, 2013) | 3 | via local client IPC | client-side | via local client IPC | no | yes | **Dead.** Python 2 only, needs a client that no longer runs |
| `skype-http` / `skyweb` / `skype-web-api` (npm) | 2 | no | no | no | no | yes | **Dead.** Last publishes 2018–2020 |
| Skype Desktop API (DBus/COM/AppleEvents) | 3 | historically yes | yes | yes | audio devices only | yes | **Removed by platform.** Deprecated 2013, gone with native client 2017 |
| Skype for Business Online (`UCWA`) | 1 | yes | yes | yes | separate media servers | **no** — Microsoft: "Register your application in Azure AD\… Obtain tenant admin consent" | Rejected: retired 2021-07-31, and registration-gated |
| Skype for Business Server 2019/SE, on-prem `UCWA` | 1 | yes | yes | yes | yes (separate media servers) | **yes** — no Entra app: UCWA creates a per-session *application resource* authenticated as the user | Rejected only on product grounds: 2019 left support 2025-10-14 (SE is current), and it needs an on-prem deployment + a corporate account |
| SfB **Trusted Application API** (server-side, either flavour) | 1 | yes | yes | yes | n/a | **no** — requires an application registered against the platform with an ID/secret, and Microsoft ended new registration support 2019-06-30 | Excluded by the hard bar |
| SkypeKit / SilverLake SDK | 2 | — | — | — | was the native call engine | n/a | **Gone.** `developer.skype.com` no longer resolves |
| Bot Framework / Skype Bot Connector | 1 | yes | partial | yes | no | **no — is app registration** | Excluded by the hard bar; also retired product |
| Ferdium webview of `teams.live.com` | 5 | yes | yes | yes | same as row 1 | yes | Fallback only; adds nothing over row 1. Note: **Ferdium is not installed on this machine** (no `ferdium` binary, no `~/.config/Ferdium`), so this row is a design option, not a probed capability |
| `matterbridge` / `skype4pidgin` bridges | 2 | — | — | — | — | yes | Both dropped Skype; pidgin plugin README says "Skype has now shut down" |

---

## Why this one

### 1. The product is retired, with dates verified on Microsoft's own page

Verified by fetching `support.microsoft.com/en-us/skype/skype-is-retiring-in-may-2025-what-you-need-to-know`
and extracting the sentences verbatim:

* "As of May 5th, 2025, Skype is retired."
* "All Skype Number subscriptions have ended in May 2026. Skype Number port-out requests
  are no longer available."
* "Your Skype data will be available until January 2026 for you to export or delete."
* "We recommend submitting the export request as soon as possible before data deletion
  begins on April 1, 2026."
* "If you were not active in Skype and Teams Free during this time, your data will be
  completely deleted after June 15th 2026."
* "we will no longer offer paid Skype features to new customers, including Skype Credit
  and subscriptions".

This is not a rumour or a news article; it is the vendor's own retirement notice, and
every cutoff in it precedes today's date (2026-10-25). Two independent secondary sources
in the search results corroborate the same May 2025 date and the Teams Free migration.

### 2. The domain no longer serves a Skype client at all

Probed live, with `curl -L` and a desktop Chrome UA:

| URL | Result |
|---|---|
| `https://web.skype.com/` | `301` → `https://teams.live.com/v2` → `200` |
| `https://web.skype.com/en/` | `200` at `https://teams.live.com/v2/` |
| `https://www.skype.com/` | `200` at `https://teams.live.com/free?source=skype` |
| `https://go.skype.com/skype.download` | `200` at `https://teams.live.com/free?source=skype` |
| `https://join.skype.com/` | `200` at `https://teams.live.com/free` |
| `https://secure.skype.com/portal/overview` | `200` at `https://calling.web.skype.com/account` |
| `https://secure.skype.com/en/data-export` | `200` at `https://teams.live.com/dataexport/skype` |
| `https://calling.web.skype.com/` | `200`, `<title>Skype Dial Pad</title>` |

Rendering `https://web.skype.com/` in headless Chrome produced
`<title>Microsoft Teams</title>` with a Teams marketing/meeting shell — no Skype client
markup. Rendering `https://teams.live.com/v2/` under Linux Chrome produced the
*unauthenticated* Teams consumer landing page ("Free Online Meetings & Video Calls |
Microsoft Teams"); the "Unsupported Browser" title seen on the bare `curl` fetch is a
JS-gated page that Chrome itself passes through, so **I could not verify from the outside
whether the signed-in Teams consumer chat UI ultimately works on Linux** — that is a
Teams question, and it is an Open Question below, not a Skype one.

### 3. No maintained library exists — verified at the registries and the repos

| Package | Registry fact (checked) | Repo fact (checked) |
|---|---|---|
| `SkPy` | PyPI 0.11, uploaded **2024-09-01** | `Terrance/SkPy` **archived** 2025-03-13, 270★, BSD-3, 24 open issues |
| `skype4py` | PyPI 1.0.35, uploaded **2013-05-25** | `awahlig/skype4py` last push 2016-08-23; README says "Python 2.x versions" |
| `skype-http` | npm 0.0.15, published **2018-02-14** | `ocilo/skype-http` last push 2019-03-15, 55★ |
| `skyweb` | npm 0.4.3, published **2018-03-09** | `ShyykoSerhiy/skyweb` |
| `skype-web-api` | npm 0.3.5, published **2018-07-11** | `PatriceVignola/skype-web-api` |
| `@sorunome/skype-http` | npm 1.5.2, published **2020-04-25** | fork of the above |
| `skype` (PyPI) | **404 — no such package** | — |
| crates.io `skype*` | no live protocol crate; the only hits are forensic parsers (`sootmark-messengers` reads `main.db`) and unrelated crates | — |

`skype4py` deserves its own line because it is the *only* library that ever implemented
the local-IPC class properly: its tree contains `Skype4Py/api/posix_dbus.py`, i.e. a
DBus backend for the native Linux client. That is exactly the class-3 candidate the brief
asked about, and it is dead for a structural reason, not a maintenance reason: the client
it talks to no longer exists.

### 4. The local-IPC class is gone by platform decision

The Skype Desktop API spoke DBus on Linux, COM/Window Messages on Windows and
AppleEvents on macOS, and it was **formally deprecated in December 2013**; the Linux
DBus path died with the native `Skype for Linux 4.3` client on **2017-07-01**, and the
Electron clients that replaced it never implemented the API. **Those two dates come from
secondary write-ups, not from a Microsoft page** — I could not reach a primary source:
`developer.skype.com` does not answer, and the support-article URL I guessed for the
Desktop API is a 404. The conclusion does not depend on the exact dates, because the
state of the platform is verifiable directly on this machine:

* No `skype`/`skypeforlinux` binary is installed or packaged (`command -v` empty;
  `apt-cache search --names-only skype` returns nothing).
* No `~/.Skype` or `~/.config/skype*` state directory exists.
* The Debian repository `repo.skype.com` returns **403** for both `dists/stable/Release`
  and the package pool, and its TLS certificate does not match the hostname.
* The download redirector `get.skype.com` serves a **certificate that expired
  2026-04-17**.
* There is **no `skype` snap** (Snap Store API: `resource-not-found`; snapcraft.io 404).
* Flathub still has `com.skype.Client`, but its own page carries
  `"eolMessage":"Skype was shut down in May 2025."` and `robots: noindex`.

So even the "install the legacy client and talk to its socket" escape hatch is closed:
there is nothing to install, and the client that would hold the socket cannot log in.

### 5. The one live Skype-branded surface is a Teams dial pad with an entitlement gate

`calling.web.skype.com` serves a 1.3 kB shell loading
`teams-modular-packages/skype-dial-pad/9.4.0.15/` (index, layout, entitlements, app and
calling chunks all fetched 200), and **every** host it references is
Teams infrastructure: `auth.fl.teams.microsoft.com`, `mtsvc.fl.teams.microsoft.com`,
`login.microsoftonline.com`, `teams.live.com`, `edge.skype.com`. Reading the bundle
(v9.4.0.15) shows what it is:

* It authenticates through **MSAL against `login.microsoftonline.com/consumers/`** with a
  first-party public client id (`69228f9f-…` hard-coded in the bundle) and requests the
  scope `https://auth.fl.teams.microsoft.com/teams.auth.readwrite`; it then exchanges
  that AAD token for a **skypetoken** at an `authzUrl` (`x-ms-client-type: skypedialpad`,
  `x-ms-client-version: 9.4.0.15`).
* Its only messaging API references are the call-log conversation
  (`/v1/users/ME/conversations/48:calllogs/messages` and `.../properties`) — a **call
  log**, not chat.
* It reads `entitlementsServiceUrl + /users/self/balance` and
  `/users/self/rate-sheet/countries`, i.e. it is gated on Skype Credit / calling plans.
* Microsoft's own doc for it says the entry points "are only available for users who
  currently have an active subscription, active Skype number, or active Skype Credits",
  and the flow is "enter or select a phone number to dial → Call".

That is a PSTN outbound dialer for legacy balance holders. It is not a medium, it has no
chat, and it sells nothing to new customers. It cannot be the basis of a messaging skill.

### 6. Skype for Business is the *other* answer, and it is also over

The brief asked me to say plainly if the real need is enterprise:

* **Skype for Business Online** — Microsoft Lifecycle page: start 2015-05-01,
  **retirement 2021-07-31**.
* **Skype for Business Server 2019** — Microsoft Lifecycle page: mainstream end
  2024-01-10, **extended end 2025-10-15 06:59:59 PT** — i.e. just over a year before
  today (2026-10-25).
* **Skype for Business Server Subscription Edition** — start 2025-07-01, "In Support".
  This is the only supported SfB product line, and it is on-prem server software.

**On the registration question I have to correct a first-pass assumption.** The *Trusted
Application API* does require an application registered against the SfB platform with its
own ID and secret (Microsoft's own note: the Trusted Application API was a developer
preview, and "Trusted Application registration will not be supported after 6/30/2019").
But **UCWA itself does not**. Microsoft's UCWA docs split by flavour, and I verified both
pages:

* *Online* (`/skype-sdk/ucwa/developingucwaapplicationsforsfbonline`): "Register your
  application in Azure AD … Obtain tenant admin consent" — an Entra app plus tenant-admin
  consent. Excluded, and the product is retired anyway.
* *On-premises* (`/skype-sdk/ucwa/createanapplication`): no Entra app and no admin
  consent. UCWA discovery → authenticate the user → "the application is registered with
  the UCWA 2.0 server", i.e. a per-session application resource authenticated **as the
  user**, with the `WWW-Authenticate: Bearer trusted_issuers=…, client_id=…` /
  `MsRtcOAuth` challenge-response instead of an app secret. That genuinely fits the
  "as the account's own human" bar.

So on-prem SfB is rejected **only** because of the product, not the auth model: it needs
a corporate deployment and a corporate account (neither exists here), the 2019 edition
left extended support on 2025-10-15, and its successor edition is on-prem-only. If an
organisation ever asks for SfB SE specifically, UCWA-as-the-user is the right technical
answer and `tonybaloney/pyucwa` (18★, last push 2019-10-23) is the only Python client I
found — unmaintained, so budget for writing the client. **For an enterprise need where
Teams is available, Teams is the answer and a Skype skill is not.**

### 7. The surviving protocol is Teams', and one library proves the seam

SkPy's final discussion thread is the most useful artifact in this whole investigation,
because its author and users watched the migration happen:

* Issue #294 (2025-03-13) — "messaging host is now `msgapi.teams.live.com`".
* Discussion #290 comment (2025-03-06) — "Skype Web uses a different endpoint:
  `https://msgapi.teams.live.com/v1/users/ME/conversations/` instead of
  `https://azwcus1-client-s.gateway.messenger.live.com/...`, but they seem identical in
  their functionalities."
* Issue #299 — "The retrieval of registration token is no longer working. The endpoint
  `/users/ME/endpoints` is now returning a 403", blocking all chat actions.
* Issue #297 — "SkPy and Teams … are you planning anything similar to SkPy for Teams?"
  (no such library exists).

I verified the host is live: `https://msgapi.teams.live.com/v1/users/ME/conversations`
and `/endpoints` both return **401** with
`{"errorCode":911,"message":"Authentication failed."}` for anonymous requests, for bogus
`Authorization: Bearer`, for bogus `X-Skypetoken`, and for bogus
`Authentication: skypetoken=` — i.e. the endpoint exists and is reachable, and rejects all
four uniformly, so it advertises nothing about which scheme it wants. That the credential
is a skypetoken in an **`X-Skypetoken`** header is *expected* rather than verified: it is
what the Dial Pad bundle's `setTokenHeader` does
(`case 'skype': e.headers.set('X-Skypetoken', t.token)`), and I had no valid token to
prove it against the live host. The old per-region gateways
(`client-s.gateway.messenger.live.com`, `client-s.gateway.messaging.skype.com`,
`asm.skype.com`) no longer resolve at all; `edge.skype.com/trap/` (the relay token
service) answers, `edge.skype.com/trap/tokens` answers 401, and
`edge.skype.com/trap/` returns `{"v1":"https://edge.skype.com/trap//v1"}`.

So the credential ceremony looks intact (`login.microsoftonline.com` consumer AAD →
skypetoken → `X-Skypetoken` on `msgapi.teams.live.com` — the first and last hops verified
as endpoints, the middle one only read from the bundle), but **every consumer of that
protocol is Teams**. A "Skype skill" written today is a Teams consumer client wearing a
Skype name — including the sandboxed `skypedialpad` user agent and the hard-coded
first-party client id. That is a maintenance liability with no payoff, and it belongs in
the Teams determination.

### 8. Every echo of the old ecosystem is archived

* `EionRobb/skype4pidgin` (621★; by my assessment the best-maintained
  reverse-engineered classic-protocol client) —
  **archived** 2025-06-08; its README now reads "Skype has now shut down. This code
  remains for history, but you'll want to switch over to the Teams plugin".
* `matterbridge` ships bridges for discord, harmony, irc, keybase, matrix, mattermost,
  **msteams**, mumble, nctalk, rocketchat, slack, sshchat, steam, telegram, vk,
  whatsapp(x2), xmpp, zulip — **no skype bridge**.
* `OfficeDev/skype-web-sdk-samples` — archived, last push 2022-11-28.
* GitHub repo search for `skype api pushed:>2026-01-01` returns exactly one hit,
  `coderade/pySkypeBotApi` (4★), whose real last commit is **2019**; it is a Bot
  Framework wrapper (app registration, excluded) and its push date is a metadata artifact.
* `developer.skype.com` does not answer at all (`curl` exit 6/000, no A record) — SkypeKit/
  SilverLake documentation is gone.

---

## Voice assessment

**Verified: consumer Skype call audio is not reachable as a Skype protocol, because the
Skype client and the Skype signalling are gone. The *only* call audio that still exists
is Teams consumer WebRTC inside a web page, and nothing I could find exposes it outside
the renderer.**

What I verified, and what I did not:

* **Verified — the calling stack is Teams', not Skypekit's.** The Dial Pad bundle lazily
  imports `assets/skype-calling-pluginless.bundle-BCbgqSj8.js` (3.13 MB, HTTP 200). That
  bundle contains the full browser calling stack: `RTCPeerConnection` ×19,
  `getUserMedia` ×17, `MediaStream` ×205, `createOffer` ×27, `setRemoteDescription` ×12,
  `dtmf` ×21, `audioTrack` ×6, `opus` ×7, plus the media-content types
  `application/sdp-ngc-0.5`, `application/sdp-ngc-1.0`, `application/sdp`. Its signalling
  agent is constructed with `clientType: "consumer"`, `doHostlessCalling: true`, and its
  media agent with a `relayConfigProvider` returning the page's `MDN_TRAP` blob:
  `Service.url: https://edge.skype.com/trap/`, `Token.tokenUrl:
  https://edge.skype.com/trap/tokens`, and TURN/relay fqdn `world.relay.skype.com:443/udp
  3478`. **`world.relay.skype.com` still resolves** (via
  `worldaz.relay.skype.trafficmanager.net` → Azure). So the media plane's relay fabric is
  live for Teams.
* **Verified — `getUserMedia` is used for a microphone permission probe** in the same
  bundle (`navigator.mediaDevices.getUserMedia({audio:!0}) … e.getTracks().forEach(t=>
  t.stop())`), which is the standard "test my mic" path. My reading of the rest of the
  bundle (an element-level `RTCPeerConnection` media agent, `MediaStream` plumbing, DTMF)
  is that audio is carried as browser WebRTC media, i.e. **Opus in SRTP, keyed by the
  page's own JS**, not on a socket the page exposes.
* **Not verified — end-to-end call audio.** I have no Skype/Teams account with a legacy
  paid entitlement, and the brief forbids connecting one. I did not place a call, so I
  cannot state from observation that audio flows, only that the code path that would
  carry it is present and that its relay and token services are live.
* **Not verified — whether any audio leaves the page in a form a process can capture
  without a virtual device.** Nothing in the bundle advertises a PCM or socket transport;
  the transport is WebRTC inside the renderer.

**Consequence for the repo's two working shapes.** `gvoice/` works because Google Voice
signals over **SIP on a WebSocket** and the peer connection is owned by our *process*
(`werift` does ICE/DTLS-SRTP, `media.ts` exposes `onTrack`); `signal-cli` +
`signal-call-tunnel` works because RingRTC hands a **Unix PCM socket**
(`SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe`). Neither shape is available here: there is no SIP
signalling to own, and no socket to pipe. The nearest thing that would work is the
**browser-page shape**: run `teams.live.com` (or the Dial Pad) under CDP, inject audio by
replacing `navigator.mediaDevices.getUserMedia` (or feeding
`--use-file-for-fake-audio-capture`) with a WebAudio `MediaStreamDestination`, and capture
by wrapping `RTCPeerConnection` to attach a second receiver — or by tab capture plus Opus
decode. **This paragraph is a proposal, not a verified finding**: I confirmed only that
the bundle calls `getUserMedia`, builds `RTCPeerConnection`s and receives `MediaStream`s
in-page. I did not implement or test any of that plumbing against this bundle, so treat
the injection/capture mechanism as *expected to be possible* and expensive, not as
demonstrated. It is **page surgery on Teams**, it is fragile against bundle rebuilds
(`9.4.0.15` today), and it does not belong to Skype.

**If the owner wants voice, it goes to the Teams skill, not here.** Proposed roadmap text:

> **Skype — do not build. No ASR/TTS item.** Consumer Skype was retired on 2025-05-05 and
> its data was deleted through 2026; `web.skype.com` now serves Microsoft Teams, and the
> only Skype-branded application left (`calling.web.skype.com`) is a paid-legacy PSTN dial
> pad with no chat. There is no Skype medium left to add voice to. Any realtime-agent
> work on this protocol is Teams-consumer work: page-level WebRTC capture/injection of
> `teams.live.com` (the `SkypeWebMedia` / `sdp-ngc` / `MDN_TRAP` stack), tracked under the
> Teams determination and the existing R1/R2 voice items, not as a Skype feature.

---

## Implementation shape

**None. Recommended: no code.** If a human overrides this and wants the legacy path, the
concrete shape is below so nobody has to rediscover it — but treat it as a branch of the
Teams skill.

* **Language:** Python, matching `mycord-slack-skill` (CDP capture in-process, no extra
  runtime), *not* TypeScript — there is no `werift`-style peer connection to own.
* **Package layout:** `mycord_teams_skype/` with `cdp.py` (browser bring-up + target
  selection), `session.py` (0600 snapshot with capture time, token fingerprint,
  expiry), `reads.py`, `api.py` (passthrough), `cli.py`. Copy the Slack split wholesale —
  `mycord_slack/cdp.py`'s `capture_session()` (`cdp.py:L489-L630`) and
  `mycord_slack/session.py` are the reference implementation; do not invent a new one.
  Neither the Slack suite nor the Discord/Telegram suites were run during this
  determination; they are cited as the pattern to copy, not as tested here.
* **Session capture:** the Slack pattern verbatim — copy the Chrome profile to a scratch
  dir, launch with `--remote-debugging-port`, `Network.enable`, harvest the cookie jar
  and the `skypetoken`/AAD token from the signed-in page, write `session.json` 0600
  *before* it contains secrets, refuse group/world-readable files. There is no refresh
  path: `skypetoken` comes from a bearer AAD token, so a snapshot expires and the only
  repair is another capture.
* **Verbs (the useful minimum):**
  `login` (capture), `session --check` (real validity probe against
  `msgapi.teams.live.com/v1/users/ME/conversations`, not a local expiry check),
  `chats` (list conversations), `read <chat> --limit N` (paged history),
  `send <chat> "text"`, `search <query>`, `users`, `logout`.
  Call-related verbs (`dial`, `hangup`, call log) belong to the Dial Pad/Teams surface and
  should not appear in a messaging skill.
* **Failure modes that will bite:**
  * **Protocol churn without notice.** The messaging host moved
    `client-s.gateway.messenger.live.com` → `msgapi.teams.live.com` in one quarter with no
    announcement, and `/users/ME/endpoints` started 403ing for the old client (#299). This
    is the single biggest reason not to invest.
  * **No history API for a fresh session.** `msgapi` is a conversation-message API; there
    is no "give me history" call by design — expect to page `conversations/{id}/messages`
    the way the web client does.
  * **Region/registration host.** `azwcus1-…`-style sharded hosts are gone; anything that
    hard-codes them fails to resolve.
  * **CDP + profile contention.** Chrome refuses to share a `--user-data-dir`; the Slack
    skill's answer (stage a copy) is the answer here too.
  * **Microsoft first-party client id in the calling stack.** `69228f9f-…` and the
    `skypedialpad` UA are Microsoft's; impersonating them is both brittle and a terms
    problem, and is *not* the "your own session" story this project wants.
  * **Entitlement gating.** Any call surface returns a "My Account" popup unless the
    account still holds legacy credit.

---

## Open questions

1. **Does anyone actually need Skype?** This is the only question that matters. If the
   stakeholder means "the people I used to talk to on Skype", those people are on Teams
   Free now, and the answer is `mycord-teams-skill`. If they mean an on-prem **Skype for
   Business Server** estate, the technical answer is **UCWA authenticated as the user**
   (no app registration — see §6), which needs a corporate deployment and account, on a
   product whose 2019 edition left extended support 2025-10-15 and whose only supported
   edition (Subscription Edition) is on-prem-only.
2. **Is a legacy paid entitlement available?** Only relevant if someone wants the Dial
   Pad. I did not test this: it needs a real account with Skype Credit/Number/subscription
   that was purchased before retirement, and I was told not to link any real account.
3. **Does the Teams consumer web client work signed-in on this Linux box?** I verified the
   *unauthenticated* shell renders; I could not verify the signed-in chat UI, because that
   needs an account. If Teams consumer chat turns out to be broken on Linux, then this
   whole protocol family is unreachable from this machine by any browser route, and the
   Teams determination's CDP/Ferdium plan needs the same check.
4. **Which Teams account, and does it have calling?** The Teams determination (written in
   the sibling workspace, `tooling/mycord-teams/docs/determine-teams.md`, as of today)
   explicitly covers **work/school** (`teams.cloud.microsoft`) and flags that
   `teams.live.com` is a *different* API host it did not probe. A Skype account migrates
   to the **personal** side, i.e. precisely the unprobed half. So the useful follow-up is
   not on this document: it is "complete the personal-account branch of the Teams
   determination", and note that its verified voice shape — a PipeWire virtual sink plus
   remap-source with the web client over CDP — is the browser-page shape this protocol
   would need, and it is being proven there, once, rather than twice.
5. **Cross-reference before building anything:** if the answer to (1) is "Teams", the
   deliverable is a Teams determination, and `tooling/mycord-teams/docs/determine-teams.md`
   is the document to complete — not this one.
