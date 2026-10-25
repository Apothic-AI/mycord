# Microsoft Teams — library determination

**Date:** 2026-10-25
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a `mycord-teams-skill`.

Everything below was checked on this machine, against the npm registry, against the code
inside the published packages, or against the Microsoft Learn pages cited. Claims I could
not verify are marked **expected** or **unverified** and collected in
[§ What is verified vs expected](#what-is-verified-vs-expected). No account was linked and
no message was sent to anyone.

---

## Decision

**Chosen path: implement the Teams web client's own protocol — the internal
`chatsvc` / `chatsvcagg` / IC3 REST surface — in a Python REPL skill, authenticated by the
delegated first-party token the signed-in Teams SPA already holds (refreshable headlessly
from the `ESTSAUTHPERSISTENT` cookie), using Microsoft Graph on that same session for the
things Graph is genuinely better at (files/SharePoint/OneNote).** No Entra app is
registered at any point, and the token is the account's own delegated token, so the
permission boundary is exactly what the human sees in the web client.

The runner-up is depending directly on the npm package **`msteams`** (`nakasyou/teams-api`)
as the engine: it is the best-maintained implementation (0.2.1 published 2026-10-04,
40★, MIT, not archived, ships an MCP server) and it is the source I verified the
cookie→token→refresh chain from — but its `dist` contains **no send path at all** (verified:
zero `POST` to `/conversations/{id}/messages`, zero send/post/create functions), so it
cannot be the skill's engine; it stays a protocol reference. The second runner-up,
**`teams-cli`** (`mingnz/teams-cli`), has the closest verb set to what we want (chats,
messages, **send**, dm, search, members, recordings, transcript, watch) but is a
thin 8★ project with 11 open issues, a Playwright/persistent-profile auth model, and no
Python story — so we take its protocol and not its code.

---

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Microsoft Graph Teams APIs with a **registered Entra app** | 1 official | yes | yes | yes | **no** (see Voice) | **✗ app registration** | **rejected — fails the hard bar** |
| Graph on the **Teams first-party delegated token** (no app of our own) | 1 hybrid | chats, channels, structure, files | yes | yes, where `Chat.ReadWrite` is in the consent | no | ✓ | **keep as secondary path**; per-tenant consent contents unverified |
| Teams **internal** REST (`chatsvc`, `chatsvcagg`, `ic3`, presence, substrate search) — what the web client itself calls | 2/4 hybrid (reverse-engineered, first-party auth) | yes | yes, paged via `syncState` | yes (`RichText/Html` message POST) | no | ✓ | **chosen primary** |
| `msteams` npm 0.2.1 (`nakasyou/teams-api`) | 2 | chats, channels, notifications, `me`, **files/SharePoint, OneNote/Class Notebook** | yes (paged) | **no** | no | ✓ | best-maintained reference; **rejected as engine: read-only** |
| `teams-cli` npm 1.1.0 (`mingnz/teams-cli`) | 2 | chats, messages, search, activity, members | yes | yes (`send`, `dm`) | recordings/transcript only | ✓ | closest feature match; **rejected as dependency** (8★, 11 issues, Playwright, JS-only) — adopted as protocol reference |
| Teams **web client over CDP** (`teams.cloud.microsoft`) | 4 | yes | yes | yes | **yes, with virtual audio** | ✓ | **fallback** for API gaps, and **the only voice path** |
| New **Teams desktop** client with a debug port | 3/4 | yes | yes | yes | yes-ish | ✓ but Windows/macOS only | rejected on this host: Microsoft retired the Linux desktop client (2022); unofficial `teams-for-linux` is the only Linux shell and does not document a remote-debugging port |
| **Ferdium** webview (there is an `msteams` recipe) | 5 | via webview | yes | yes | yes-ish | ✓ | **last resort only** — adds a layer without adding capability the browser route lacks |
| `fossteams/teams-api` (Go) | 2 | yes | partial | partial | no | ✓ | **rejected: dead** — last push 2022-10-13 (167★) |
| `systemprompt-teams` crate | — | — | — | — | — | **✗** | rejected: validates an **Azure Bot Service** app, i.e. bot registration (crates.io, last publish 2026-10-22) |
| `teams` crate | — | — | — | — | — | n/a | rejected: unrelated project-management tool (crates.io 4.0.1, 2024) |
| `msteams` (PyPI) | — | — | — | — | — | ? | rejected: single release 0.1.0, unrelated/unmaintained |

---

## Why this one

### 1. The hard bar eliminates Graph-as-documented, but not Graph-the-API

Microsoft's documented route for reading Teams message content is a **registered Entra
application**. Verified on Microsoft Learn:

* `GET /chats` — Delegated (work or school): `Chat.ReadBasic`, `Chat.Read`,
  `Chat.ReadWrite`. **Delegated (personal Microsoft account): "Not supported."**
  ([chat-list](https://learn.microsoft.com/en-us/graph/api/chat-list?view=graph-rest-1.0))
* Channel messages need `ChannelMessage.Read.All` — the classic admin-consent permission
  ([permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)).
* `GET /callTranscript` — delegated `OnlineMeetingTranscript.Read.All` /
  `CallTranscripts.Read.All`; personal accounts "Not supported."
  ([calltranscript-get](https://learn.microsoft.com/en-us/graph/api/calltranscript-get?view=graph-rest-1.0))

A registered app is exactly what the brief forbids: it is a second identity the tenant can
consent to, rate-limit, or block, and it needs an admin for channel data. So **class 1 in
its documented form is out.**

But there is a real distinction the brief anticipates: the *Teams web client itself* holds
**delegated** Graph tokens for the signed-in user, minted to Microsoft's own first-party
client. Nothing is registered, and the token carries the user's own permissions.
Verified in `msteams`' code that this is what happens in practice — it requests five
audiences from the same session
(`pkg/dist/TeamsClient-DZx7lU3C.mjs`, `src/rest/constants.ts`):

```js
const SCOPES = {
  graph:    "https://graph.microsoft.com/.default openid profile offline_access",
  notes:    "https://www.onenote.com/.default …",
  chats:    "https://ic3.teams.office.com/.default …",
  channels: "https://chatsvcagg.teams.microsoft.com/.default …",
  users:    "https://api.spaces.skype.com/.default …",
};
```

So Graph is *reachable* without registering anything, and it is the right tool for files,
SharePoint and OneNote (`msteams` uses it for exactly that). Whether a first-party Teams
token also satisfies Graph's **message** endpoints is **unverified** — I have no account to
test with and would not have tested consent against a real tenant anyway. That is why Graph
is the secondary path and the internal API is primary: the internal API is what the client
demonstrably uses for message content and sends.

### 2. The auth story is better than Slack's, and it is fully documented by working code

This is the strongest single finding, and it is why Teams is a good medium rather than a
grindy one. `msteams` shows a **headless** renewal path with no browser in the loop
(verified in `pkg/dist/cli/index.mjs`, `src/auth/estsAuth.ts`):

1. `GET https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` with
   `client_id=5e3ce6c0-2b1f-4285-8d4b-75ee78787346`,
   `scope=https://api.spaces.skype.com/.default openid profile offline_access`,
   `redirect_uri=https://teams.cloud.microsoft/v2`, `prompt=none`, PKCE `S256`,
   and a `Cookie: AADSSO=NA|NoExtension; ESTSAUTHPERSISTENT=<cookie>` header.
2. The 302 `Location` carries `#code=…`; exchange it at `…/oauth2/v2.0/token`
   (`grant_type=authorization_code` + `code_verifier`) for a **`refresh_token`**.
3. From then on, per-audience access tokens come from `grant_type=refresh_token` against the
   same client id, with the refresh token **rotated on every call** — the code persists the
   new one each time and falls back to re-bootstrapping from `ESTSAUTHPERSISTENT` when it
   gets `invalid_grant` (`TeamsClient-DZx7lU3C.mjs`, `src/auth/TokenManager.ts`).

Unlike Slack — where the `xoxc` + `d` cookie pair cannot be refreshed and repair means
another browser capture — **Teams credentials renew from a cookie, in one HTTP request.**
That changes the operational shape of the skill: `login` is a rare event, and `session
--check` is a cheap token mint rather than a probe.

I confirmed the client id and redirect are the ones the live page uses, not just the ones
in the library. Unauthenticated CDP probe of `https://teams.cloud.microsoft/v2/` on this
machine (headless Chrome 156, throwaway profile, no account):

```
url: https://login.microsoftonline.com/common/oauth2/v2.0/authorize
     ?client_id=5e3ce6c0-2b1f-4285-8d4b-75ee78787346
     &scope=openId profile openid offline_access
     &redirect_uri=https%3A%2F%2Fteams.cloud.microsoft%2Fv2%2Fauthv2
     &response_mode=fragment &client_info=1 &nonce=…
     &x-client-SKU=msal.js.browser &x-client-VER=5.6.3
     &response_type=code &code_challenge=… &code_challenge_method=S256
title: "Sign in to your account"; localStorage keys: 0
```

Note the live page uses `redirect_uri=…/v2/authv2` and MSAL `5.6.3` where the package uses
`…/v2` and `3.30.0`. That is a **live drift signal**: these values are the client's, not a
contract, and the skill must read them from the page rather than hard-code them forever.

### 3. The token surface the skill needs is capture-by-audience, which ports cleanly to Python

`teams-cli` captures MSAL tokens straight out of the page's `localStorage` and keys them by
audience (verified in `dist/index.js`):

```js
function findMsalToken(aud) {
  const prefix = `https://${aud.toLowerCase()}/`;
  for (let i = 0; i < localStorage.length; i++) {
    const parts = localStorage.key(i).split("|");
    if (parts[3]?.toLowerCase() !== "accesstoken") continue;
    const scopes = (parts[6] ?? "").split(" ");
    if (!scopes.some(s => s.toLowerCase().startsWith(prefix))) continue;
    return { secret: JSON.parse(localStorage.getItem(k)).secret, expires_on: … };
  }
}
```

with `TOKEN_AUDIENCES = { ic3: "ic3.teams.office.com", search: "outlook.office.com/search",
presence: "presence.teams.microsoft.com" }` plus a per-SharePoint-host token acquired from
`/_api/` requests. This is structurally the same job `mycord-slack-skill` already does in
Python — `mycord-slack-skill/src/mycord_slack/cdp.py` copies a Chrome profile, opens it
headless, and reads `localStorage` for `localConfig_v2` (`cdp.py:L124` `ChromeProfile`,
`L489` `capture_session`, `L457` waiting on both cookie jar and localStorage). The Teams
skill reuses that machinery with a different key extractor and a different cookie name.
That is a *port*, not a rewrite.

### 4. The internal API is the only surface with both reads and writes

From the two packages, the endpoints that constitute a working client are:

| Purpose | Endpoint | Audience |
| --- | --- | --- |
| Chat/DM messages (paged) | `GET https://teams.cloud.microsoft/api/chatsvc/{region}/v1/users/ME/conversations/{id}/messages?view=msnp24Equivalent\|supportsMessageProperties&pageSize=…&startTime=…[&syncState=…]` | `ic3` |
| Send a message | `POST …/conversations/{id}/messages` body `{content:"<p>…</p>", messagetype:"RichText/Html", contenttype:"text", clientmessageid}` | `ic3` |
| Thread members | `GET …/v1/threads/{conversationId}` | `ic3` |
| Activity feed | conversation id `48:notifications` on the same route | `ic3` |
| Channel posts | `GET https://teams.cloud.microsoft/api/csa/{region}/api/v1/teams/{teamId}/channels/{channelId}?pageSize=…` (`replyChains[].messages`) | `chatsvcagg` |
| Search | `https://substrate.office.com/…` with `x-client-version: T2.1` | `outlook.office.com/search` |
| Profiles | `POST …/api/mt/{region}/beta/users/fetchShortProfile` | `api.spaces.skype.com` |
| Teams/channels structure | Graph (`/me/joinedTeams`, channel lists) | `graph` |

Two hard-coded-in-the-package details that will bite an implementation, and that a real
skill must not copy blindly:

* **Region is data, not a constant.** `teams-cli` defaults to `amer`
  (`getRegion()` → `tokens?.region ?? "amer"`) and keys it off a `DISCOVER-REGION-GTM`
  match; `msteams` hard-codes **`apac`** into its channel URLs
  (`/api/csa/apac/api/v1/teams/…`). A skill targeting a non-APAC tenant via `msteams` is
  silently wrong. Region must be learned from the page and stored in the session.
* **`clientinfo` / `behavioroverride` headers matter.** `teams-cli` sends
  `os=mac; …; clientName=skypeteams; clientVer=1415/26031223020`, plus
  `x-ms-test-user: False`, `x-ms-migration: True`, `behavioroverride: redirectAs404`.
  These are the client's own fingerprints. **Expected**, not verified: some may be optional
  and others load-bearing — this is the kind of thing to establish empirically during
  implementation, one header at a time, against a scratch conversation with a colleague's
  consent, never against a stranger.

### 5. Why not drive the DOM as primary

Because the API is a smaller, more stable, and far more capable surface than the DOM: no
selector maintenance, real history paging via `syncState`, and it survives Teams UI
redesigns (which are frequent). The DOM route stays as the **fallback** — for anything the
internal API exposes only in the UI, and as the single voice-capable route (below).

### 6. Why not the desktop client / Ferdium

Microsoft **retired the Linux desktop client** in December 2022 and pointed users at the
PWA ([Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/1791839/where-is-teams-for-linux),
[Tech Community](https://techcommunity.microsoft.com/discussions/microsoftteams/end-of-the-road-for-teams-linux-desktop-client/3630968)).
The unofficial `teams-for-linux` (IsmaelMartinez, 5,182★, GPL-3.0, pushed 2026-10-25) wraps
the web app in Electron and *does* accept Electron CLI flags — its config docs document
`performance.electronCLIFlags` ("Electron CLI flags") — but a code search of that repo finds
`remote-debugging-port` only in `.vscode/launch.json`, and its docs document only
`development.webDebug` ("Enable debug at start"). So a debug port is **plausible but not
documented**, and on Windows the new client's equivalent is WebView2
(`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` / `--remote-debugging-port`, per Microsoft's own
[WebView2 debugging doc](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code)).
Neither is a reason to add a dependency: plain Chrome is on this machine, is a supported
Teams-for-Web browser, and is already the substrate every other skill in this repo drives.

Ferdium stays a last resort. It has an `msteams` recipe upstream (verified via the
`ferdium/ferdium-recipes` recipe list), and this machine has Ferdium installed as a Flatpak
with only `whatsapp` configured — i.e. using it for Teams would require a human to add the
service first, buying nothing over Chrome.

---

## Tenant condition — state this before promising anything

The answer depends on which account this is, and the two are not variations, they are
different systems:

* **Work/school account (Entra ID, `teams.cloud.microsoft`) — the path in this document.**
  No app registration and no admin consent are needed *because we register nothing*; the
  delegated token is the user's. But **tenant Conditional Access still governs**: MFA
  re-prompts, "compliant device" requirements, session lifetime, and policies that block
  non-browser clients can each break the headless refresh. Role/permission differences are
  also real — the `msteams` README itself records a live example ("the class-specific list
  endpoint returned 403 … while the default notebook … were readable"). **Expected**: the
  internal API is not covered by any support statement, and a tenant admin can restrict it.
* **Personal account (Teams Free, `teams.live.com`) — different API host, and Graph is out.**
  Graph's Teams resources are explicitly "Not supported" for personal Microsoft accounts
  (verified on the `chat-list` and `calltranscript-get` pages), and Microsoft's own
  community answers point personal-account chat work at `https://teams.live.com/api/…`
  ([community hub](https://techcommunity.microsoft.com/discussions/teamsdeveloper/how-to-use-microsoft-teams-free-chat-api/3835352)).
  `msteams` and `teams-cli` both target `teams.cloud.microsoft`, i.e. **work/school only**.
  Supporting personal accounts would be a second implementation. **Unverified** — I did
  not probe `teams.live.com`, because doing so meaningfully requires an account.

If the account turns out to be personal, the determination changes: the internal API work
carries over in shape (same `chatsvc` family, different origin), the Graph half is dropped,
and none of the code in either reference package can be trusted for endpoints.

---

## Voice assessment

**Verdict: impossible through any no-app-registration API; possible through the OS audio
layer plus a CDP-controlled Teams web client. That is a third transport shape, not either
of the two precedents on this machine.**

### Why the API route is closed

* Graph's real-time media requires a **bot/Entra application** with `Calls.AccessMedia.All`,
  admin consent, the Teams app manifest (`supportsCalling`/`supportsVideo`), a .NET library
  (`Microsoft.Graph.Communications.Calls.Media`), and — for production — a **Windows Server
  guest OS in Azure**
  ([media bots](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/requirements-considerations-application-hosted-media-bots)).
  That is registration plus infrastructure: squarely outside the bar.
* Even with all that, a media bot only reaches media of calls **it participates in** —
  "Calls are either peer-to-peer between a person and your bot, or multiparty between your
  bot and two or more people"; "A user can initiate a peer-to-peer call with your bot or
  invite your bot into an existing multiparty call"
  ([calls-meetings-bots-overview](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/calls-meetings-bots-overview)).
  There is no "listen to someone else's call" API, and Microsoft's communications overview
  additionally forbids persisting or deriving data from media you do access.
* **Delegated** calls do not exist: on `POST /calls`, the permissions table reads
  "Delegated (work or school account) | Not supported." and the same for personal — only
  Application permissions.
* Live transcription is a *user-visible meeting feature*, not a stream: transcript/recording
  artefacts are fetched after the fact through `callTranscript` (verified above); a
  "live transcript" answer on Tech Community points at the same `callTranscript` resource,
  not a realtime feed. **Unverified** that no realtime transcript stream exists in beta; I
  found no documented one.

So for voice the skill must act as a **participant**: join the call as the human, in a
browser, with audio that the agent controls.

### What is actually possible, and what I verified

The Teams web client negotiates ordinary WebRTC in-page and reads/writes audio through the
OS. Both halves of that were tested here, without Teams:

1. **Virtual audio devices work on this host.** With PipeWire (`pactl`), I loaded
   `module-null-sink` + `module-remap-source` (the remap source is what Teams would see as a
   microphone), played a 440 Hz tone into the sink, and captured from the remapped source
   concurrently:

   ```
   frames=64237 rate=16000 peak=32768 -> AUDIO CAPTURED
   ```

   Both modules unloaded cleanly afterwards. So **TTS → sink → Teams mic** and
   **Teams speaker → sink monitor → agent** are both real today, with no browser internals
   involved. (`gvoice/` does not need this because it owns the RTP socket; a browser-based
   medium does.)
2. **The page is reachable over CDP** — the unauthenticated probe above drove
   `teams.cloud.microsoft` in headless Chrome, read `location.href`, `document.title` and
   `localStorage`, i.e. everything a skill needs for the messaging half too. Chrome's own
   launch/`--remote-debugging-port` path is proven in this repo by `ferdium-skill`.

**Expected, unverified end-to-end:** that the Teams web client, launched under Chrome on
Linux with media permission pre-granted (or `--use-fake-ui-for-media-stream`) and its
device selection pinned to `det-teams`-style virtual devices, will join a meeting as the
signed-in user and exchange audio that the agent can inject and capture. Every component is
verified; the composition is not, and it cannot be verified without sitting in a real
meeting with a real account — which this brief forbids.

Also **unverified**: whether a headless Chrome instance keeps the WebRTC audio graph alive
the way headed Chrome does. `ferdium-skill`'s docstring records the analogous lesson for
Electron ("Headless means Xvfb, not a native headless mode"); expect to need Xvfb rather
than `--headless=new` for calls.

### Which existing shape this follows

Neither. For the record, precisely:

| Medium | Transport shape | Fits Teams? |
| --- | --- | --- |
| Google Voice (`gvoice/`) | SIP over WebSocket for signalling + `werift` `RTCPeerConnection` for opus RTP; agent owns the media socket (`gvoice/src/media.ts`) | **No** — Teams signalling is proprietary and lives in the web client |
| Signal (`signal-cli` + `signal-call-tunnel`) | Local daemon, Unix PCM socket, `SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe` | **No** — no Teams equivalent exists |
| Teams (this determination) | **OS virtual audio device + CDP-driven web client**; speech stack above it is media-agnostic PCM | yes, but composition unverified |

The good news is that this shape makes the speech stack *easier* than `gvoice/`: the agent
sees plain 48 kHz PCM on a Pulse/PipeWire device, so `gvoice/src/stt.ts` +
`gvoice/src/tts.ts` are reusable in design (as `docs/roadmap.md` already says: reuse the
design, not the code), and VAD/echo-guard logic applies unchanged. The bad news is that
jitter, packet loss and clock drift are handled by Chrome, and the agent cannot tell whose
voice is whose except by VAD segmentation — Teams mixes all remote participants into one
stream.

### Proposed roadmap text

> ## R3 — Voice: ASR + TTS for Microsoft Teams calls
>
> **Status:** not started. Blocked on transport, not on speech.
>
> Teams voice cannot be reached through any API available without registering an
> application: Graph's real-time media needs a bot app with `Calls.AccessMedia.All`, admin
> consent and a Windows Server media host, delegated calls are "Not supported", and a media
> bot only ever sees calls it was invited to. So the agent has to *be* a participant, in the
> web client.
>
> The transport shape is a **third** one for this repo, not the `gvoice/` SIP+`werift` shape
> and not the `signal-cli` PCM-socket shape: a PipeWire virtual sink plus a remap-source
> (verified working on this host — tone injected into the sink was captured from the
> remapped source at peak 32768), with the Teams web client driven over CDP in Chrome and
> its microphone/speaker pinned to those devices. TTS plays into the sink; remote audio is
> captured from the sink monitor.
>
> Unknowns to settle before implementation: whether the Teams web client keeps its WebRTC
> audio graph alive under Xvfb (headless Chrome is expected not to); how to grant
> persistent mic permission to `teams.cloud.microsoft` in a copied profile; whether Teams'
> own AEC/AGC needs disabling (the `teams-for-linux` project ships
> `app/browser/tools/disableAutogain.js` for exactly this); and speaker attribution, since
> Teams mixes remote participants into one stream before it reaches the device.
>
> Reuse the ASR/TTS layer per the sequencing note below rather than duplicating it a third
> time.

---

## Implementation shape

**Language: Python 3.11+**, `src/mycord_teams/`, hatchling, `uv`, `pytest` — the repo's
established skill shape (`mycord-slack-skill/pyproject.toml` is the model: one runtime
dependency, `src/` layout, `project.scripts` entry point). **No runtime npm dependency**:
the two JS packages are read as protocol documentation, not installed as a dependency of a
Python REPL skill, and `teams-cli` would drag Playwright (a bundled Chromium download) in
for a job that system Chrome already does here.

### Layout

| Module | Job | Provenance |
| --- | --- | --- |
| `cli.py` | verb dispatch, `--json` output, exit codes | pattern: `mycord-slack-skill/src/mycord_slack/cli.py` |
| `config.py` | `~/.local/share/mycord-teams/` paths, tenant, region | new |
| `cdp.py` | copy a Chrome profile, launch headless, read cookies + `localStorage`, `Runtime.evaluate` | **port** `mycord-slack-skill/src/mycord_slack/cdp.py` (`ChromeProfile`, `capture_session`) |
| `session.py` | `session.json` at 0600: refresh token, `ESTSAUTHPERSISTENT`, tenant id, region, captured_at | new; shape follows `mycord_slack/session.py` |
| `token.py` | PKCE cookie→refresh bootstrap; per-audience `refresh_token` grants; atomic rotation; in-memory cache until `expires_in - 300 s` | **port** `msteams` `src/auth/TokenManager.ts` + `src/auth/estsAuth.ts` |
| `chat.py` | `ic3` client: conversations, messages (paging + `syncState`), send, threads, activity | port `teams-cli` `getChatClient`/`sendMessage` |
| `channels.py` | `chatsvcagg` client: team/channel lists, channel posts (`replyChains`) | port `msteams` conversations/channels routes |
| `graph.py` | files/SharePoint/OneNote + structural reads on the `graph` audience | port `msteams` files/notebooks surface |
| `voice.py` | later: virtual sink/remap lifecycle, device ids, `say`/`listen` | new; see R3 |

### Verbs (the REPL surface)

```
mycord-teams session --check        # mint a token; the only honest validity test
mycord-teams login [--headless]     # cookie→refresh, or capture from a signed-in profile
mycord-teams chats [--limit N]
mycord-teams messages <chat> [--limit N] [--since <ts>]
mycord-teams send <chat> <text> [--html]
mycord-teams search <query> [--limit N]
mycord-teams activity [--limit N]
mycord-teams members <chat>
mycord-teams channels <team>
mycord-teams channel messages <team> <channel>
mycord-teams files ls|search|download …      # Graph
mycord-teams watch [<chat>]                  # poll + syncState, newline-delimited JSON
mycord-teams raw <METHOD> <url> [--json …]   # passthrough, like Slack's "any method" escape hatch
```

`raw` is deliberate and mirrors the Slack skill's philosophy: the account's own permissions
are the boundary, and a curated verb list is a smaller tool than the account. Every verb
emits one JSON object per line with `--json`, so a REPL agent can pipe it.

### Auth / session capture

Two entry points, one stored shape:

1. `login --cookie <ESTSAUTHPERSISTENT>` — fully headless, one GET + one POST, stores the
   refresh token. **This is the default**; it is the Teams analogue of Slack's `login` but
   cheaper and repeatable.
2. `login` (no argument) — copy the signed-in Chrome profile (never write to it), open
   headless over CDP, wait for the `ic3` audience to appear in `localStorage`, and capture
   cookies + MSAL entries. Needed when (1) fails on `interaction_required`/MFA, and the only
   route when the page's own `redirect_uri`/`client_id` drift.

On every later call: mint/cache per-audience tokens, and **persist a rotated refresh token
atomically** (write temp + rename) because the grant invalidates the old one. Concurrent
mints from two REPL calls are therefore a real hazard — hold a lock file.

### Failure modes that will bite

* **Refresh-token rotation races** — two concurrent refreshes kill the chain; needs a lock
  and atomic write. (See `TokenManager`: every successful grant returns a *new*
  `refresh_token`.)
* **Conditional Access / MFA re-prompt** — surfaces as `invalid_grant` or a login redirect
  instead of a code; the only repair is a headed browser capture. Say so in the error, do
  not retry blindly.
* **Region, not `amer`** — learned from the page (`DISCOVER-REGION-GTM`) and stored; both
  reference packages get this wrong or partial (`msteams` hard-codes `apac`).
* **Audience mismatch** — the same endpoint needs the right one of `ic3` / `chatsvcagg` /
  `graph` / `outlook.office.com/search` / `spaces.skype`; a 401 usually means the wrong
  token, not a dead session.
* **Undocumented API drift** — `redirect_uri`/`client_id`/`x-client-VER` already differ
  between the live page (5.6.3, `/v2/authv2`) and the packages (3.30.0, `/v2`). Read them
  from the page; never treat the package's values as a contract.
* **HTML, not text** — message bodies are `RichText/Html`; the request must send
  `<p>…</p>` and reads must strip tags (and, better, parse them) rather than regex-stripping
  if mentions/links matter.
* **Thread/conversation id shapes** — `19:…@thread.v2` (chat/channel), `48:notifications`
  (activity feed), `19:…@unq.gbl.spaces`. Treat as opaque strings; resolve short ids from a
  cached chat index like `teams-cli` does.
* **Paging** — `syncState` is the incremental cursor; `startTime=1` walks backwards. Store
  the cursor per conversation if `watch` is to be cheap.
* **Tenant policy** — app-enforced restrictions and third-party-client blocks can make the
  internal API fail with no code path forward; treat as a documented dead end, not a bug.
* **No support contract at all** — everything above is the web client's private surface.
  Every verb is best-effort, and the skill must say so in its own SKILL.md.

---

## Open questions (human must decide / provide)

1. **Work/school or personal?** This determination covers `teams.cloud.microsoft`
   (Entra/work). A personal (`teams.live.com`) account needs a second implementation and
   loses Graph entirely. Answer changes the code, not just a config.
2. **Which account, and may an agent use its session?** Every path here acts *as* the
   human, in their tenant, under their tenant's policies. That is a consent decision, and
   the tenant admin — not the user — may be the one who gets to make it.
3. **Is an admin likely to notice or care?** No app is registered, so there is no consent
   prompt and nothing in the tenant's app list; but traffic from an unregistered client to
   `chatsvc` is visible to the tenant's security tooling. Worth stating plainly.
4. **Is voice actually wanted?** R3 is a large piece of work whose only viable transport is
   a virtual audio device plus a browser, with unattributed mixed remote audio. Confirm
   before anyone starts.
5. **May the skill ever *join a meeting* or *send* on the human's behalf unattended?**
   Different risk class from reading; should be an explicit policy, like Slack's safety
   rails, not a flag defaulting to on.
6. **Which tenant region** (`amer` / `emea` / `apac`) — needed to validate the channel
   endpoints against reality rather than against `msteams`' APAC hard-code.

---

## What is verified vs expected

**Verified on this machine / in the published artefacts:**

| Claim | How |
| --- | --- |
| `msteams` 0.2.1, MIT, published 2026-10-04, 40★, pushed 2026-10-04, 0 open issues, not archived | `npm view`, `gh api repos/nakasyou/teams-api` |
| `msteams` has no send capability | unpacked `dist/`; zero `POST` to `/messages`, zero send/create functions; `--help` command list run locally (needs `npm i` of 96 deps — it does run) |
| `msteams` auth: PKCE authorize → code → refresh token, then `grant_type=refresh_token` per scope with client id `5e3ce6c0-2b1f-4285-8d4b-75ee78787346`, rotating refresh token, `ESTSAUTHPERSISTENT` fallback | read `dist/cli/index.mjs` + `dist/TeamsClient-*.mjs` |
| `msteams` scopes: graph / onenote / ic3.teams.office.com / chatsvcagg.teams.microsoft.com / api.spaces.skype.com | `SCOPES` in `dist/TeamsClient-*.mjs` |
| `msteams` channel URLs hard-code `apac` | endpoint literals in `dist/TeamsClient-*.mjs` |
| `teams-cli` 1.1.0, MIT, published/modified 2026-05-29, 8★, 11 open issues, pushed 2026-07-01 | `npm view`, `gh api repos/mingnz/teams-cli` |
| `teams-cli` verbs: login, logout, chats, messages, send, search, activity, find, dm, members, recordings, transcript, watch | installed its 4 deps into a clean dir, ran `node index.js --help` (11 packages, 2 s) |
| `teams-cli` send payload and chat headers (`RichText/Html`, `clientmessageid`, `x-ms-test-user`, `behavioroverride`, `clientinfo`) | read `dist/index.js` |
| `teams-cli` token capture = MSAL `localStorage` scan by audience + per-host SharePoint token from `/_api/` requests | read `dist/index.js` (`findMsalToken`, `acquireSharepointToken`) |
| Live Teams web entry redirects to `login.microsoftonline.com` with client id `5e3ce6c0-…`, `redirect_uri=…/v2/authv2`, MSAL `5.6.3`, empty `localStorage` when signed out | headless Chrome 156 over CDP on this host against `https://teams.cloud.microsoft/v2/` |
| Graph `POST /calls`: Delegated (work/school **and** personal) "Not supported."; `Calls.AccessMedia.All` needed for app-hosted media | Microsoft Learn `application-post-calls` |
| Graph `GET /chats`: delegated work/school `Chat.ReadBasic`…; **personal "Not supported"** | Microsoft Learn `chat-list` |
| Graph `GET /callTranscript`: delegated `OnlineMeetingTranscript.Read.All` / `CallTranscripts.Read.All`; personal "Not supported" | Microsoft Learn `calltranscript-get` |
| App-hosted media bots need the .NET media library and a Windows Server guest OS in Azure | Microsoft Learn `requirements-considerations-application-hosted-media-bots` |
| A media bot only handles media of calls it is in ("peer-to-peer between a person and your bot, or multiparty…") | Microsoft Learn `calls-meetings-bots-overview` |
| Linux Teams desktop client was retired (Dec 2022), PWA recommended | Microsoft Q&A + Tech Community threads |
| `teams-for-linux` 5,182★, GPL-3.0, pushed 2026-10-25, documents `performance.electronCLIFlags`; no documented `remote-debugging-port` (code search hits only `.vscode/launch.json`) | `gh api`, repo config docs, code search |
| `fossteams/teams-api` last pushed 2022-10-13 (167★) — dead | `gh api` |
| Ferdium has an upstream `msteams` recipe; this host has Ferdium installed with only `whatsapp` configured | `ferdium/ferdium-recipes` contents; local Flatpak dir |
| `systemprompt-teams` crate is Azure Bot Service validation | crates.io search |
| `msgraph-sdk` PyPI current is 1.65.0 (available, unused here) | `pip index versions` |
| PipeWire can create a null sink + remap source, and audio played into the sink is captured from the remapped source | loaded `module-null-sink` + `module-remap-source`, played 440 Hz via `pacat`, captured via `ffmpeg -f pulse`: `peak=32768`, `AUDIO CAPTURED`; modules unloaded |
| Host has Chrome 156, Edge, Firefox, PipeWire, `pactl`, `pacat`, `pw-*`, no Teams client, no Teams Chrome profile | `command -v`, `flatpak list`, `/opt`, `~/.config` |

**Expected / unverified — do not present these as fact:**

* That a first-party Teams delegated token satisfies Graph's **message** endpoints
  (`/chats/{id}/messages`, channel messages). Needs a live account; would also mean testing
  consent against a real tenant, which I did not do.
* That the Teams web client, driven over CDP in Chrome on Linux, joins a meeting and
  exchanges audio through a virtual device end-to-end. Every component is verified
  separately; the composition is not. Headless Chrome keeping WebRTC audio alive is
  **doubtful** (expect Xvfb, per `ferdium-skill`'s Electron lesson).
* Whether Chrome persistent mic permission can be pre-seeded for `teams.cloud.microsoft`
  in a copied profile, versus needing `--use-fake-ui-for-media-stream` (which would also
  fake the device, so probably not what we want).
* Which of `teams-cli`'s `clientinfo` / `behavioroverride` / `x-ms-*` headers are
  load-bearing versus vestigial.
* Internal endpoints for edit, delete, reactions, presence, and typing — not present in
  either package beyond what is tabulated above. Assume they exist (the web client has them)
  and must be discovered from live traffic with consent.
* Whether any realtime (streaming) transcript API exists in Graph beta; I found only the
  post-meeting `callTranscript` resource.
* Whether tenant Conditional Access will allow headless refresh in the actual tenant. This
  is the single most likely reason the chosen path degrades to "browser capture only".
* Tenant region for the target account.

**Terms-of-service note (not a technical claim):** driving Teams through the web client's
private API means acting as Microsoft's own client with credentials obtained from the
human's signed-in session. Microsoft's terms and the tenant's acceptable-use policy govern
that; nothing here is a supported integration, and the skill's own documentation must say
so as plainly as `google-voice-skill` and `mycord-slack-skill` do.
