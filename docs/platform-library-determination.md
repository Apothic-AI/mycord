# WhatsApp / Slack / Signal — library determination

**Date:** 2026-10-25
**Status:** research complete; no code written yet.
**Question:** pick libraries so each platform can be driven REPL-style (like
`mycord-discord-skill` / `mycord-telegram-skill`) **without registering a bot or app**.

Every claim below was checked on this machine or against the registry that publishes the
package. Where something could not be verified, it says so.

---

## Summary

| Platform | Library | Fits "REPL, no bot" | Send | Read | Verified here |
| --- | --- | --- | --- | --- | --- |
| WhatsApp | **neonize** (Python ↔ whatsmeow) | yes — QR/phone-code device pair | yes | live events + limited history | installed 0.5.2, 103-method API |
| Slack | **slackdump** (+ `slack_sdk` for posting) | yes — `xoxc` + `d` cookie | read: yes, post: via `slack_sdk` | yes | built v3, ran, login docs confirmed |
| Signal | **signal-cli** (skill already installed) | yes — linked device | yes | yes | skill present; binary 0.14.6, account dead |

## WhatsApp: neonize

`neonize` is a Python binding to **whatsmeow**, the Go implementation of the WhatsApp
multi-device protocol by Tulir Asokan (the Mautrix author). Python calls the compiled Go
core over FFI, so there is no Node subprocess and no browser.

**Why not the alternatives:**

| Option | Why not |
| --- | --- |
| `baileys` | TypeScript only. npm `latest` tag is `7.0.0-rc14` (2026-07-29) — a release candidate, and its history is several forks. Node would also be a second runtime in a Python-only repo. |
| whatsmeow directly | Correct engine, but Go. There is no REPL story; Python is the established pattern here. |
| `whatsapp-web.js` / Selenium | Drives a real browser as a fake Linked Device; heavier and more detectable. |
| Official WhatsApp Business Cloud API | Genuinely supported, but **is** bot registration — excluded by the requirement. |

**Evidence gathered:**

* PyPI `neonize` 0.5.2, uploaded 2026-09-21, `requires_python >=3.10`, ships prebuilt
  `manylinux2014` wheels — so **no Go toolchain, no compile** on install.
* GitHub `krypton-byte/neonize`: pushed 2026-10-15, 760★, Apache-2.0, not archived.
* Upstream `tulir/whatsmeow`: pushed 2026-10-17, 7.6k★, MPL-2.0, not archived.
* Installed and imported on this host under Python 3.14.1: `NewClient` / `NewAClient`
  both construct, `NewClient("test.db")` takes a SQLite session store, 103 public methods.
* API surface confirmed: `PairPhone`, `connect`/`disconnect`, `send_message`,
  `send_image`/`send_audio`/`send_video`/`send_document`/`send_sticker`, `send_album`,
  `send_reaction`, `edit_message`, `create_group`, `get_group_info`,
  `get_joined_groups`, `mark_read`, `send_presence`, `download_any`.
* Events module exposes `MessageEv`, `UndecryptableMessageEv`, `HistorySyncEv`,
  `OfflineSyncCompletedEv`, `OfflineSyncPreviewEv`.

**Design consequence — read the caveat before choosing a REPL shape.** There is **no
"fetch conversation history" call on the client** (`[m for m in API if 'hist' in m]` is
empty). whatsmeow delivers history through `HistorySyncEv` on a fresh link, then through
live `MessageEv`s. So a WhatsApp REPL differs from the Telegram one in an important way:

> **History must be accumulated locally as messages arrive** (the library already gives
> us that for free, since every `MessageEv` can be appended to the same SQLite file the
> session lives in). A REPL cannot reasonably answer "show me the last 50 messages in
> this chat" unless it has been running and recording.

Pairing is a real UX decision, not a detail: `PairPhone` (enter a code on the phone) is
usable headlessly, while QR needs a rendered image. For a REPL, phone-code pairing is the
better default.

### How high is the ban risk, really?

Short answer: **low-to-moderate for careful low-volume use, never zero, and not something
you control.** It is not primarily a spam problem — that is the part worth understanding.

WhatsApp enforces **two separate categories**, and only one of them is about behaviour:

1. **Behavioural** — bulk/broadcast messaging, messaging people who don't have your
   number, identical messages repeated, rapid group creation, mass-adding.
2. **Unauthorised client** — "using modified/unofficial apps or linking through
   unofficial apps". This applies regardless of what you send, and it is the category an
   unofficial library lives in by definition.

Evidence that category 2 is real and not theoretical:

* whatsmeow issue **#810** (open across 2025-05 → 2026-10): operators report the
  *"Your account may be at risk — recent activity indicates that your account may be
  using unauthorized tools"* warning on accounts **"that are idle but connected to
  whatsmeow … never send a message from it"**. An idle connection is enough to be flagged.
* whatsmeow issue **#989**: 24-hour suspensions where the reporter says *"I could not
  find a reason"*, with the client log showing
  `enforcement_type: "BULK_MESSAGING"` in the server notice.
* Older precedent: yowsup bans were issued explicitly for *"using an unauthorized
  application and/or unsupported device"*, a stated reason independent of spam.
* Scale: WhatsApp banned ~5.48 million accounts in India in February 2026 alone. This is
  industrialised detection, not manual review of reported abuse.

Evidence in the other direction, which is why this is not "high" risk:

* mautrix-whatsapp runs whatsmeow as the engine for large self-hosted bridges, and most
  hobby deployments run for a long time without enforcement.
* The overwhelming majority of ban reports in those threads involve *some* automation at
  volume, or recently registered / "unwarmed" numbers, or messaging non-contacts.

So the honest framing — behaviour dominates the probability, but a persistent unofficial
connection is itself a flagged condition, so **behaviour alone cannot make the risk zero**.
Practical tiers:

| Usage | Risk |
| --- | --- |
| Read-only, occasional replies, people message you first, established number | low |
| Regular daily use, normal volume | moderate |
| Any bulk/broadcast, messaging strangers, new number, rapid group ops | high |

**Mitigation, and the one real decision:** use a number you can afford to lose rather than
a primary one. That single choice changes the downside from "lose my identity" to
"re-link later". Everything else (rate limiting, human pacing) reduces probability but does
not remove category 2.

No voice/video call support. Requires a phone number already on WhatsApp as the primary
device.

## Slack: slackdump for reading, slack_sdk for posting

Slack is the awkward one, and the awkwardness is a **policy** fact rather than a
technical one.

`chat.postMessage` requires `chat:write` — available on bot (`xoxb-`) or user (`xoxp-`)
tokens. Both come from registering an app, which the requirement excludes. The token the
Slack web client itself uses is `xoxc-`, and it only works alongside the httpOnly `d`
cookie (`xoxd-`).

> Slack's own position, from their support reply: *"using xoxc tokens for the API is not
> supported or recommended"*, and the first-party client token *"is not available for 3rd
> party apps"*. It is not blocked in a way that breaks tools, but a workspace **may**
> restrict it, and an org policy can turn on app approval at any time.

So there are two capabilities, not one:

| Capability | Tool | How |
| --- | --- | --- |
| Read/search/export | **slackdump** v3 | `slackdump workspace import` with `SLACK_TOKEN=xoxc-…` + `SLACK_COOKIE=xoxd-…` |
| Post as yourself | **slack_sdk** `WebClient` | `WebClient(token=xoxc-…, headers={"Cookie": f"d={xoxd}"})` |

**Evidence gathered:**

* Built `github.com/rusq/slackdump/v3/cmd/slackdump` from source on this host with Go
  1.27.1 (`GOBIN=… go install …@latest`), 48 MB binary, runs.
* `slackdump help login` documents exactly the no-app path: *"Login with Client Token
  (`xoxc-`) and a cookie value"*, plus `workspace import` from a `.env` with
  `SLACK_TOKEN` / `SLACK_COOKIE`.
* **Verified slackdump has no send capability:** every one of its 16 subcommands was
  checked for `send`/`post message`/`reply` — none matched. It is an export/inspection
  tool, so posting has to come from elsewhere.
* `slack_sdk` 3.45.0 `WebClient.__init__` accepts `token` **and** `headers`, which is the
  mechanism for carrying `Cookie: d=…` alongside `xoxc-`. This is what makes posting
  possible without an app.
* Installed to `~/.local/bin/slackdump` (on PATH).

**Honest caveat:** `slackdump version` prints `unknown` for a `@latest`-built binary with
no VCS stamp. If reproducible versioning matters, pin a release tag instead of `@latest`.

**Design consequence:** the REPL is a thin Python layer that shells out to `slackdump` for
reads and uses `slack_sdk` for writes. Do **not** reimplement entity caching or channel
resolution — slackdump already carries an archive, a search index, and a users/channels
cache that are better than anything written from scratch.

### Access level: same as the human, as required

Confirmed: an `xoxc-` token paired with the `d` cookie **"operates exactly like your active
browser session and carries no OAuth scope limits"**, so it works with *all* API methods —
public and internal — that the owning account is permitted to use. That is precisely the
brief: same/full access as the human who owns the account, no bot scopes, no admin
approval, no app review.

Consequences for the design:

* **Make the skill a passthrough, not a verb list.** The right shape is
  `mycord-slack <api.method> --json '{...}'` straight to `slack_sdk.WebClient`, the same
  philosophy as `mycord-email` handing arguments to himalaya. Curating verbs would
  artificially re-impose the scope limits this approach deliberately avoids.
* **Admin/Enterprise endpoints are capability-limited by role, not by token type.**
  `admin.users.*` etc. are Enterprise Grid only and require an Org Admin/Owner; a standard
  member's `xoxc` gets `admin_unauthorized` regardless. The skill should surface
  Slack's error verbatim rather than guessing.
* **The session expires.** There is no endpoint to refresh `xoxc`; when the browser
  session dies the token stops working and must be re-extracted. A `login` step that
  captures both values (and reports expiry plainly) is therefore core, not optional.

**Verified vs not:** the mechanism is verified locally — `slack_sdk` 3.45.0 `WebClient`
accepts both `token` and `headers`, which is how the `d` cookie rides along. Live posting
against a real workspace is **not** tested, because this machine holds no Slack
credentials. First live run should confirm one read and one post before the skill claims
they work.

## Signal: already covered

There is **already** a skill at `~/.agents/skills/signal-cli/SKILL.md` (287 lines) and the
binary is installed. It is more complete than a new skill would be:

* `signal-cli` native 0.14.6 → `/opt/signal-cli` (latest upstream is v0.14.9, so slightly
  behind but well inside the ~3-month window Signal tolerates).
* linking as a secondary device, the `qrencode`-closes-the-pipe trap, messaging,
  attachments, reactions, typing, `daemon --socket` JSON-RPC + SSE events.
* `signal-call-tunnel` for **1:1 voice calls**, including the opt-in
  `SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe` PCM contract (48 kHz mono s16le, 960-byte windows
  on a Unix socket) for programmatic call audio.
* troubleshooting table and rebuild instructions for the tunnel fork.

**Conclusion: write no new Signal skill.** "REPL-like" for Signal is already served by
`daemon --socket` (persistent session, one connection, many RPC calls) — arguably a better
model than eval-style REPL, because it keeps `receive` running so crypto/sync stays healthy,
which the skill explicitly warns about.

**One thing that needs the human:** the stored account is dead —
`listAccounts` reports `Ignoring +14153356728: User is not registered
(NotRegisteredException)` and `accounts.json` is a 179-byte stub. That is a *re-link*, not
a code problem, and only the person holding the phone can scan the QR.

## Recommendation

1. `mycord-whatsapp-skill/` — Python over **neonize**. REPL shape mirrors
   `mycord-telegram-skill` (session daemon + `eval`), with one deliberate difference:
   a message store appended on `MessageEv`, because history cannot be fetched on demand.
2. `mycord-slack-skill/` — Python thin wrapper: **slackdump** for read/search/export,
   **slack_sdk** for posting as the user. Needs a `login` step that imports
   `xoxc` + `xoxd` into slackdump's encrypted store.
3. **No new Signal skill** — the existing `signal-cli` skill is sufficient; re-link the
   dead account when convenient.

### Resolved / still open

Resolved:

* **Ban risk** — moderate for careful use, never zero, dominated by behaviour but with an
  unavoidable "unofficial client" component. Mitigation is choosing a disposable number,
  not tuning message rates.
* **Slack access level** — full, same as the owning human: `xoxc` + `d` carries no scope
  limits. Build a passthrough, not a curated verb list.

Still open:

* **Is a dedicated number available** for the WhatsApp link? If not, the skill should
  still work but lead with the re-link-later warning.
* **State directory convention** for both new skills (`~/.local/share/<platform>/`), and
  whether the WhatsApp message store should be its own SQLite file or reuse the neonize
  session DB.
