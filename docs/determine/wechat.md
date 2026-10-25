# WeChat — library determination

**Date:** 2026-10-25
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a `mycord-wechat-skill`.

> **Determination in one line:** *do not build this skill on the individual-account (个人号)
> surface.* Build it — if at all — on WeCom's official API as an **organization acting as
> itself**, and record WeChat personal-messaging voice as impossible.

---

## Decision

**Chosen path: WeCom (企业微信) server API, driven over plain HTTPS, with the
`微信客服` (WeChat Customer Service / `kf`) channel as the only sanctioned bridge to a
personal WeChat user.** The client is a thin Python wrapper over `qyapi.weixin.qq.com`
(`access_token` → `/cgi-bin/message/send`, `/cgi-bin/kf/send_msg`, `/cgi-bin/kf/sync_msg`),
needing no reverse engineering, no WeChat desktop install and no process injection; the
only app registration involved is a *tenant's own* self-built app inside *their own*
organization, which the brief's "no registered bot" rule permits because the account
speaks as the organization, not as a spoofed human.

**Runner-up, and the one that actually got most of the engineering attention during this
determination: the official Weixin-for-Linux desktop client (`com.tencent.WeChat`
4.1.13 on Flathub) driven by AT-SPI2 accessibility reads + X11 XTEST input + a virtual
PulseAudio sink/source — the modern equivalent of `wxauto`, and it provably *works*
mechanically on this machine.** It loses not on capability but on legitimacy: 《微信个人账号使用
规范》 §1.2.6 forbids exactly this shape — "通过非腾讯开发、授权的第三方软件、插件、外挂、
系统等 … 进行自动化操作，对微信软件及其组件、模块、界面、数据等进行访问、读取或控制" —
and Tencent is actively enforcing it (36 WeChat-specific GitHub DMCA/circumvention notices in
2026 alone, one of which named and removed a 2,352-repo network). A mycord skill is a durable,
published artifact; shipping one whose stated purpose is a ToS-prohibited automation of a
client UI is a category error regardless of how well the code works.

**Rejected outright:** every 个人号 library — `wechaty` + its web/puppet-xp puppets,
`itchat`/`itchat-uos`, `wxauto`/`wxautox`, `WeChatFerry`/`wcferry`, `ntchat`. Reasons are
per-candidate in the table and expanded below; in short they are Windows-only,
dead-protocol, abandoned, or TOS-violating.

---

## Comparison

Columns marked ✅/❌ are **verified** in this session (registry, README, or the project's own
source); ⚠️ means partially verified and the caveat is stated in "Why this one".

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
|---|---|---|---|---|---|---|---|
| **WeCom server API** (`message/send`, `kf/send_msg`, `kf/sync_msg`) | 1 — official API, own credentials | ✅ via `kf/sync_msg` for 客服; 企业内 members via callback | ⚠️ cursor-scoped only; 会话内容存档 reaches back just 5 days and is a paid, admin-enabled add-on | ✅ text/image/voice/video/file/card/miniprogram | ❌ no call transport at all | ✅ own tenant app (see note) | **Chosen** |
| **Weixin for Linux 4.1.13 + AT-SPI2/XTEST + virtual sink** | 3 — native client driven programmatically | ✅ verified: a11y tree readable host-side | ⚠️ via local DB (`message_N.db`, `session.db`, `contact.db`) key extraction | ⚠️ XTEST keystrokes / `SetTextContents` | ⚠️ **loopback possible** — see Voice assessment | ✅ no registration, but **TOS-forbidden** | **Rejected — ToS, not capability** |
| `wechaty` (core SDK) | 2 — reverse-engineered | — | — | — | ❌ | ✅ | Core is alive (v1.20.2, 150k downloads/mo) but carries no protocol; useless alone |
| `wechaty-puppet-wechat` (web/UOS) | 2 | ✅ | ❌ | ✅ | ❌ | ✅ | **Dead.** Web API returned `ret 1203` for post-2017 accounts since 2017; UOS workaround itself reported dead (`puppet-wechat#235`, Jan 2025: "基本上去就被封了") |
| `wechaty-puppet-wechat4u` | 2 | ✅ | ❌ | ✅ | ❌ | ✅ | **Dead.** Last publish 2024-01-30; own history note (v1.14.0, 2023): "大多数使用者可能出现微信被官方封禁提醒，从已知收集的封禁情况，暂未有可解决方案" |
| `wechaty-puppet-xp` | 2 | ✅ | ❌ | ✅ | ❌ | ✅ | **Windows-only, pinned to WeChat 3.9.10.27.** npm `wechaty-puppet-xp@2.1.1` ⇒ `WeChatSetup-3.9.10.27.exe`. Requires a `sidecar` DLL injection into `WeChat.exe`. Cannot run here |
| `wechaty-puppet-padlocal` / padchat / padplus | 2 — paid protocol middleman | ✅ | ✅ | ✅ | ❌ | ⚠️ needs paid token | **Dead-ish**: repo last push 2023-07-04, 206 open issues; `padchat` archived. Depended on a commercial iPad-protocol service, not an account credential |
| `itchat` 1.3.10 | 2 — web protocol | ✅ | ❌ | ✅ | ❌ | ✅ | **Dead.** Last release 2017-11-03. Repo exists only as an archive of the web API era |
| `itchat-uos` 1.4.1 / 1.5.0.dev0 | 2 — web protocol via UOS UA | ✅ | ❌ | ✅ | ❌ | ✅ | **Dead + bans accounts.** Last release 2021-06-22; author's own 2023-02-10 note: users of `1.5.0.dev0` "可能出现微信被官方封禁提醒 … 暂未有可解决方案" |
| `wxauto` (cluic) | 3 — Win32 UIAutomation | ✅ | ✅ | ✅ | ❌ | ✅ | **Windows 10/11 only**, WeChat 3.9.X. PyPI name `wxauto` returns *Not Found*; only `wxautox` (39.1.42, `win_amd64` wheels, depends on `comtypes`/`pywin32`) is installable, and the `wxauto` README now says code is for study only, "禁止用于实际生产项目" |
| `WeChatFerry` / `wcferry` 39.6.0.0 | 3 — DLL injection into `WeChat.exe` | ✅ | ✅ | ✅ | ❌ | ✅ | **Repo archived** (last push 2026-07-10). Wheel is `py3-none-any` but ships `sdk.dll`, `spy.dll`, `spy_debug.dll` — Windows-only in practice. Targets WeChat 3.9.12.x |
| `ntchat` 0.1.20 | 3 — injected protocol | ✅ | ✅ | ✅ | ❌ | ✅ | **Dead + Windows-only.** Last release 2022-12-16; wheels are `win32`/`win_amd64` only. GitHub repo no longer resolves |
| `sjzar/chatlog` (read-only history) | 2 — local DB | ✅ | ✅ | ❌ | ❌ | ✅ | **Self-destructed.** Owner received a letter from WeChat and in Oct 2025 deleted all code and history; repo is now one notice file. Read-only history through it is no longer possible |
| `pywxdump` 3.1.46 | 2/3 — local DB decrypt | ✅ | ✅ | ❌ | ❌ | ✅ | Alive on PyPI (2025-10-15) but scans WeChat 3.x Windows memory; no `xwechat_files` (4.x) support in the wheel — verified by inspection |
| `TANGandXUE/wcdb-key-tool` | 2/3 — Linux DB key extraction | ✅ | ✅ | ❌ | ❌ | ✅ | **Alive and Linux-capable** (670★, pushed 2026-08-04); GDB breakpoint + PBKDF2-HMAC-SHA512 256k rounds for 4.1+. Read-only; part of the rejected desktop path |
| `wx-11/MimicWX-Linux` | 3 — AT-SPI2 + XTEST, Linux | ✅ | ✅ | ✅ | ⚠️ n/a | ✅ | **Exists and is the shape I'd otherwise build** (Rust: `atspi.rs`, `input.rs`, `db.rs`, `api.rs`), but 8★, unlicensed, and inherits the ToS problem |
| `djasdh/wechat-bridge-linux` | 3 — AT-SPI2 + xdotool, Linux | ✅ | ✅ | ✅ | ❌ | ✅ | Same shape, 2★, MIT, Python; inherits the same ToS problem |
| `ferdium` webview (`wx.qq.com`) | 5 — CDP | ⚠️ | ❌ | ⚠️ | ❌ | ✅ | **Last resort, and it doesn't work for WeChat.** Ferdium 7.2.3 is installed here and a `wechat` recipe exists in `ferdium/ferdium-recipes`, but the login is against the dead web endpoint: `ferdium-app#2232` (open since 2024-04-12, "Wechat Recipe doesn't show chats") and r/ferdium (Jan 2025) report "WeChat refuses to let me in once I scan the QR code" |

Note on "No-app" for WeCom: it is a registered app, and I am not going to hide that. What
it is *not* is a registered app inside WeChat's ecosystem. It is a tenant's own credential
inside their own WeCom org; the identity that reaches the end user is the organization, and
WeCom's API is the sanctioned way for an organization to speak. If the project's rule is
"never register anything anywhere", WeCom fails that rule and no WeChat path survives.

---

## Why this one

### 1. The individual-account surface is not a technical problem, it is a legal one

This is the deciding evidence, and it is unambiguous. Tencent's 《微信个人账号使用规范》
(updated 2026-10-15) §1.2.6 prohibits:

> 通过非腾讯开发、授权的第三方软件、插件、外挂、系统等，注册、登录、使用微信软件及服务或
> **进行自动化操作**，对微信软件及其组件、模块、界面、数据等进行访问、读取或控制，或制作、
> 发布、传播上述工具、方法等

§1.2.4 adds that even *copying data released into terminal memory during operation* is
forbidden, which is precisely what every `xwechat_files` decryption tool does. §1.2.7
forbids circumventing security measures. This is not a grey area about "rate limits" — the
prohibited act is the automation itself, and the prohibition covers *publishing the tool*.

Enforcement is live, not theoretical. Querying `github/dmca` for WeChat notices in 2026:

| Month | WeChat-specific notices |
|---|---|
| 2026-07 | 12 |
| 2026-08 | 15 |
| 2026-09 | 7 |
| 2026-10 | 1 |

One of them (`2026-07-13-wechat-3.md`) is a law-firm notice invoking **17 U.S.C. §1201**
against `ylytdeng/wechat-decrypt`, and because the reported network exceeded 100 repos,
GitHub processed the takedown **against the entire network of 2,352 repositories**. The
notice names as infringement the *extraction of the encrypted database keys* — the exact
technique any Linux desktop path requires. A determination that recommended that path
would be recommending a design that Tencent's counsel has already had deleted, twice over.

This is also why "ban risk" is the wrong frame for the 个人号 options. The relevant risk is
not that an account gets banned — it is that the repo is a published, durable artifact
whose stated purpose is a prohibited act. `chatlog`'s author deleted a 9,179-star project
after one letter. That is the precedent to plan for, not an account suspension.

### 2. Every 个人号 library is also independently dead on this machine

Ban risk aside, none of them would run here anyway, and each for a different concrete
reason (all verified above): Windows binaries (`wxauto`, `ntchat`, `wcferry`,
`puppet-xp`), a web endpoint that refuses any account registered after 2017 with
`ret 1203` (`puppet-wechat`, `itchat`), a client version that no longer exists in a form
the tool supports (`puppet-xp` needs 3.9.10.27; current is 4.1.13), or plain abandonment
(`itchat-uos`, last release 2021). The one surface that is genuinely alive on Linux —
WeChat 4.1.13 as a native Qt app — is alive precisely *because* it is the official client,
and therefore exactly what the ToS forbids automating.

### 3. WeCom is the only surface where "reach a Chinese contact" is a supported operation

The `微信客服` (`kf`) channel is Tencent's sanctioned bridge from an organization to an
individual WeChat user, and it is a documented, ordinary HTTP API. Verified from the
official docs:

* **Send** — `POST /cgi-bin/kf/send_msg`, `touser` = the customer's `EXTERNAL_USERID`,
  `open_kfid` = the service account. Doc `94677`: "仅当微信客户在主动发送消息给客服后的
  48小时内，企业可发送消息给客户，**最多可发送5条消息**；若用户继续发送消息，企业可再次下发
  消息."
* **Receive** — the callback delivers only an event token; bodies are pulled with
  `/cgi-bin/kf/sync_msg` and a persistent `next_cursor` ("强烈建议对该字段…"). No polling
  firehose exists, so the design must store the cursor.
* **History** — the cursor *is* the history mechanism for 客服. There is no general
  "read the org's messages" API. The closest is 会话内容存档 (message archive,
  `msgaudit`: `getchatdata` / SDK `91774`), and it is heavily gated: the admin must
  configure a public key in 安全与管理 → 管理工具 → 数据与智能专区 → 会话内容 or **nothing is
  archived at all**; the app needs 「数据与智能专区权限」; and doc `101365` states "只可获取5天
  内的会话记录数据" — the archives retain 90 days by default (max 2 years) but the *API*
  only reaches back 5 days. It is also a separately-priced add-on. So: **no on-demand deep
  history** — history is whatever the cursor captured before it aged out, which is another
  reason the cursor store must be durable and the skill must not pretend to be an archive.
* **Message types** — text, image, voice, video, file, 图文, miniprogram, menu,
  location. Voice here means *voice **messages*** (audio files), not calls.

The 48-hour / 5-message window is the single most important design constraint in this
whole document, and it is why the skill must be a *customer-service* tool and not a
messaging tool in disguise. It also conveniently forces the honest framing: WeCom lets you
answer people who contacted you; it does not let you cold-message WeChat.

### 4. The environment supports it trivially, and that is a feature

The chosen path needs only HTTPS and a tenant — no desktop client, no display, no a11y bus,
no audio stack. That is a *better* operational shape than every rejected option: it runs
headless in a container, survives reboots without a GUI session, and has no hibernation or
re-auth cliff beyond the `access_token` lifecycle.

### 5. What I would have built if legitimacy were not the constraint (recorded, not recommended)

So the next engineer does not have to re-derive it: the modern Linux desktop automation
surface is real and I verified its primitives directly on this machine.

* **WeChat for Linux exists and is fully featured.** `flatpak search wechat` →
  `com.tencent.WeChat` **4.1.13.26** on Flathub (also deb/rpm/AppImage from
  `linux.weixin.qq.com`, x86/ARM/LoongArch). Its Flatpak manifest
  (`flathub/com.tencent.WeChat`, `com.tencent.WeChat.yaml`) declares
  `--socket=pulseaudio`, `--socket=wayland`, `--socket=fallback-x11`, `--device=all`,
  `--persist=.xwechat`, `--persist=xwechat_files`, and `--env=QT_AUTO_SCREEN_SCALE_FACTOR=1`
  — i.e. a **Qt** app with audio and a data dir at `~/.xwechat` / `xwechat_files`.
* **AT-SPI2 control works on Qt here.** I did not take this on faith. With `Xvfb :77` and
  `vlc --intf qt` (Qt5), the app registered two peers on the accessibility bus; reading
  `org.a11y.atspi.Accessible.GetChildren` from the registry enumerated every registered
  app, and `Properties.GetAll` on a window node returned
  `{Name: "Main stage", ChildCount: 2, …}` with `Collection`, `Component` and `Socket`
  interfaces — the interfaces an automation client needs. A/B check: 0 vlc peers without
  `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`, 2 with it. The lever exists and I measured it.
* **A virtual microphone and a loopback speaker are one command each.** Verified live and
  then unloaded: `pactl load-module module-pipe-source source_name=… file=/tmp/…fifo
  format=s16le rate=48000 channels=1` produced a usable capture source, and
  `module-null-sink` + `module-remap-source` produced a sink whose `.monitor` is
  capturable. So an agent *can* inject audio into the app's mic and record what the app
  plays. `pw-record`, `pacat` and `sox` are all present.

  This is what makes the voice answer below more precise than "impossible".

* **DB history on Linux is possible but requires defeating encryption.** Paths
  `~/.local/share/com.tencent.wechat/xwechat_files` / `~/.xwechat`, DBs
  `message/message_N.db`, `session.db`, `contact.db`, SQLCipher-4
  (`PBKDF2-HMAC-SHA512`, 256 000 rounds); 4.0.x keys are memory-scannable, 4.1+ need a GDB
  breakpoint at the key-derivation site plus a forced re-login (`wcdb-key-tool`, verified).
  Per §1.2.4 and §1.2.7 above, that is the prohibited act.

I rejected this surface on legitimacy, and I want that on the record as a *policy* choice,
not a claim that it does not work.

---

## Voice assessment

**WeChat personal-messaging voice calls: impossible to do *legitimately*. Rated
"impossible (policy-bound)", not "unverified".**

Three independent reasons, in increasing order of hardness:

1. **No transport.** WeChat calls do not use SIP, WebRTC or any published signalling. The
   academic work on WeChat VoIP (ICACT 2020 "Forensic Artifacts of Network Traffic on
   WeChat Calls"; ICCET 2019 "UVP: Uncovering WeChat VoIP Peers over Time") only
   *correlates flows* and identifies peers — it does not describe a usable signalling or
   media protocol. Neither of this repo's two working shapes (`gvoice`'s `werift` SIP/WSS
   peer connection, `signal-call-tunnel`'s Unix PCM socket) has an analogue: there is no
   peer to negotiate with and no socket to open.
2. **The only working technique is injection.** The public examples of getting bytes into a
   WeChat call are Android hooks like `carrys17/HookWxVoiceCall`, which patch
   `AudioRecord.read` inside the WeChat process. That is process tampering, i.e. the
   `外挂` case in §1.2.6/§1.2.7 — strictly worse than the desktop automation path already
   rejected.
3. **Even the "legal-looking" desktop shape is out of scope.** On Linux the only way to
   reach call audio would be the virtual-sink/pipe-source trick I verified in §5 above —
   which mechanically works but is, by construction, "using an unauthorized third-party
   system to access and control WeChat's modules and data". Same verdict.

**Could a second path supply it?** Only for a *different* question. An agent could join a
call in some other product as an ordinary participant and relay — e.g. a meeting platform
with its own API. That is not "voice for mycord-wechat", it is "use a different product for
meetings", and I did not verify any specific meeting product here; it is out of scope for
this determination. I would not build it under this skill's name.

**WeCom voice:** 客服 and app messages support `voice` as a **file-based voice message** —
`kf/send_msg` documents `voice` among its sendable types ("文本、图片、语音、视频、文件、图文、
小程序、菜单消息、地理位置、获客链接") and takes a `media_id`, uploaded via
`/cgi-bin/media/upload` (verified in the docs; note temporary media is retained ~3 days).
That is genuinely useful for a notification/listening workflow: an agent can send a spoken
reply as a voice attachment, and receive a voice message and transcribe it locally with the
same Parakeet/Piper stack `gvoice/` uses. There is **no call audio** — I found no API surface
exposing a two-way media stream, and WeCom's client-side calling is not part of the server
API reference. So the honest split is: **file-based voice messages = yes; real-time call
audio = no.**

**Roadmap text proposed (for `docs/roadmap.md`, as instructed by the brief):**

> ## R3 — WeChat personal-account automation: **not planned — prohibited by platform terms**
>
> Not deferred work; a closed question. Tencent's 《微信个人账号使用规范》 §1.2.6 prohibits
> automating the client or accessing its UI/data through unauthorised third-party tools, and
> §1.2.4 prohibits using data the client releases into process memory. Enforcement is active:
> 36 WeChat notices in `github/dmca` during 2026, including a §1201 notice processed against a
> 2,352-repository network. `chatlog`'s author deleted a 9,179-star project after one letter.
> No mycord component will be built on this surface. If personal WeChat access is genuinely
> needed, the sanctioned route is WeCom 微信客服 (`docs/determine-wechat.md`).

> ## R3a — Voice for WeChat personal calls: **impossible**
>
> No published signalling or media transport exists; the only demonstrated injection
> techniques require patching the client's `AudioRecord`/memory (Android hooks) or driving the
> desktop client's audio devices through an unauthorised automation layer. Both are the
> prohibited act in R3. Closing this permanently; do not re-open without a change in Tencent's
> terms.

---

## Implementation shape

**Language:** Python 3.11+, `uv`, matching `mycord-slack-skill` / `mycord-email-skill`
(`hatchling`, `src/mycord_wecom/`, `[project.scripts] mycord-wecom = "mycord_wecom.cli:main"`,
`ruff` + `mypy --strict` + `pytest`). Runtime dependency: **`httpx`** only — WeCom is plain
JSON over HTTPS, so `wechatpy` (1.8.18, last release 2021-11-11, enterprise module present)
is *not* worth taking on as a dependency; its `wechatpy/enterprise/` package exists but the
project has been dormant for five years and the API surface needed here is four endpoints.

**Skill name:** `mycord-wecom-skill`, deliberately *not* `mycord-wechat-skill`. The name is
the safety rail: an agent that reads the name must not conclude it can DM Aunt Wang.

**Layout**

```
mycord-wecom-skill/
  SKILL.md                 # frontmatter + verbs + safety rails
  README.md
  pyproject.toml
  src/mycord_wecom/
    __init__.py
    cli.py                 # argparse, --json, exit codes (mirror mycord-ferdium/cli.py)
    config.py              # corp_id, app secret / kf secret, trusted egress IP note
    auth.py                # access_token cache (7200 s, refresh at 80%), 40014/42001 handling
    api.py                 # thin POST/GET wrapper, errcode→exception, never logs secrets
    kf.py                  # 客服: sync_msg cursor store, send_msg
    app.py                 # 企业内: message/send, appchat, user/department reads
    media.py               # media/upload → media_id; media/get → file (voice/amr)
    redact.py              # mask corp id / open_kfid / external_userid in output
  tests/                   # fake transport; no live tenant in CI
```

**The 8 verbs**

| Verb | Endpoint | Notes |
|---|---|---|
| `auth.check` | `/cgi-bin/gettoken` (doc `91039`) | reports token age, not the token |
| `kf.sync` | `/cgi-bin/kf/sync_msg` | advances and **persists** `next_cursor`; the only receive path |
| `kf.send` | `/cgi-bin/kf/send_msg` | text/markdown; refuses outside the 48 h/5-message window with a clear error |
| `kf.accounts` | `/cgi-bin/kf/account/list` | which 客服 accounts exist (verified path, doc `94691`) |
| `app.send` | `/cgi-bin/message/send` (doc `90236`) | to 企业内 members; respects `agentid` visibility |
| `app.read` | `/cgi-bin/user/list` (doc `90201`) + `/cgi-bin/department/list` (`90208`) | directory, not messages |
| `media.upload` / `media.get` | `/cgi-bin/media/upload|get` | voice messages as files (`amr`/`speex` → transcode to wav for STT); temporary media is retained ~3 days |
| `stats` | `/cgi-bin/kf/customer/*` service state + `/cgi-bin/kf/sync_msg` counts | I did **not** find a dedicated 客服 statistics endpoint; session counts are derived from synced messages, which is also why the cursor store must be durable |

**Auth/session capture:** no browser, no QR. `corp_id` + app secret from env
(`MYCORD_WECOM_CORP_ID`, `MYCORD_WECOM_SECRET`) or `~/.local/share/mycord-wecom/session.json`
(mode 0600, directory 0700), same convention as `mycord-slack`. A human administrator must
create the self-built app and copy the secret; there is no way to obtain it programmatically.

**Failure modes that will bite — all verified from the docs, none hypothetical**

1. **Trusted-IP interception is mandatory.** WeCom help `21711`: "starting from 20:00 on
   June 20, 2022, newly enabled Contacts Sync Assistants and newly created custom apps must
   have trusted IP ranges configured in the Admin Console. API access will be restricted
   exclusively to these configured trusted IPs", and wildcards/IP ranges are **not** allowed.
   ⇒ a roaming agent, a laptop on a dynamic IP, or a Fly machine with no reserved egress IP
   simply cannot call the API. This is the #1 deployment blocker and must be in `doctor`.
2. **`access_token` expiry** (7200 s) and the `40014`/`42001` rotation errors; cache and
   refresh once, never retry-loop (WeCom rate-limits token issuance).
3. **The 客服 window.** 5 messages per 48 h after the customer's last message. Exceeding it
   fails silently-ish (`errcode` 95018-class); the skill must surface the window state
   *before* composing, mirroring how `mycord-slack` surfaces `auth.test`.
4. **`企业群发` needs a human.** Doc `92135`: "调用该接口并不会直接发送消息给客户/客户群，
   需要成员确认后才会执行发送". Any bulk-send idea must model that confirmation step; it is
   not a fire-and-forget API.
5. **`sync_msg` cursor loss = duplicate deliveries.** Store it atomically with the last-seen
   `msgid`, dedupe on read.
6. **Verification is a separate, paid, annual thing.** WeCom help `19729`/`15422`: registration
   itself takes company name + industry + employee size and an admin mobile + SMS code, but
   "Company verification fee of 300 CNY or more is charged by Tencent" and "WeCom
   verification is valid for 1 year". Unverified orgs get fewer members and no verified badge
   — relevant if the use case is outward-facing.
7. **No `wxid` for individuals.** `EXTERNAL_USERID` is per-organization and stable only for
   that org; it cannot be cross-referenced with a personal WeChat ID, and 客服 requires the
   user to have contacted you first.
8. **No deep history, by design.** 会话内容存档 is admin-enabled, paid, permission-gated and
   reaches back only 5 days (`101365`). The skill is a live relay, not an archive; do not
   promise search over the past.

**Guardrails I would require in `SKILL.md` before this ships**

* Refuse to operate on a personal WeChat account; the skill's only credential is a WeCom
  tenant.
* Hard `--dry-run` default for every send; require explicit `--send` (as the repo's roadmap
  already says: "confirm with a human before anything that reaches another person").
* No bulk/broadcast verb. No contact-list harvesting.
* Never shell out to, or bundle, any DB-decryption or injection helper.
* Log every outbound message with recipient class (客服 vs member) and window state.

**Is this skill worth building at all?** Yes, *if and only if* the actual need is "reach
Chinese business contacts, or answer inbound WeChat users as an organization". If the need
is "read and reply to my personal WeChat", the answer is no, and no amount of engineering
changes it — the fastest legitimate path there is the phone in your hand.

---

## Open questions

Decisions a human must make or facts they must supply. None can be resolved by more
research on this machine.

1. **What is the actual need?** Personal WeChat (→ no skill; closed) vs. organization →
   WeChat customer (→ build the WeCom skill) vs. organization ↔ organization (→ WeCom
   internal API, no WeChat bridge at all). This single answer decides everything downstream.
2. **A WeCom tenant.** Registering one requires a company name, industry, employee count and
   an admin mobile number receiving an SMS (`15422`). I did not create one and must not.
   Does the project have one, or is this hypothetical until a customer provides theirs?
3. **A stable egress IP.** Someone must decide where the skill runs and add that address to
   the app's trusted-IP list; ranges and wildcards are rejected (`21711`).
4. **Which app type.** 自建应用 (secret, internal members) vs. 微信客服 secret (external WeChat
   users). They have *different* secrets and do not share permission — using the wrong one
   yields 60020-class errors.
5. **Whether to pursue verification** (300 CNY+/year) and whether the counterparties need to
   see a verified badge.
6. **Whether to keep the desktop experiment as a documented dead end.** I recommend keeping
   this file (which records exactly what works and why it is not allowed) so the next person
   does not spend a session rediscovering AT-SPI on a Qt client. Say if you want it trimmed.

---

## What was verified, and how

Everything below was measured in this session, not recalled.

| Claim | How |
|---|---|
| WeChat for Linux 4.1.13.26 exists on Flathub with pulseaudio + Qt | `flatpak search wechat`; manifest at `flathub/com.tencent.WeChat` |
| Flatpak can't see the host a11y bus | `flatpak run --command=sh org.ferdium.Ferdium -c 'ls $XDG_RUNTIME_DIR'` → `pulse` present, no `at-spi` |
| AT-SPI control works on Qt here | `Xvfb :77` + `vlc --intf qt`; 0 peers without `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`, 2 with; `GetChildren`/`Properties.GetAll` returned real nodes; `vlc` killed after |
| Virtual mic + loopback speaker are available | `pactl load-module module-pipe-source …`, `module-null-sink` + `module-remap-source`; both verified then **unloaded** |
| ToS forbids automation of the client | 《微信个人账号使用规范》 §1.2.4/§1.2.6/§1.2.7, fetched from `weixin.qq.com/agreement/personal_account` |
| Tencent enforces via DMCA/§1201, at scale | `github/dmca` 2026 notices counted (36); read `2026-07-13-wechat-3.md` (2,352 repos) |
| `chatlog` deleted itself | its README; repo now contains only the notice |
| wechaty core alive, puppets dead/Windows-only | npm `version`/`time.modified`, download API, repo issue threads, puppet-xp README version table |
| `itchat-uos` last release + ban note | PyPI JSON; README update 2023-02-10 |
| `wcferry` ships Windows DLLs; repo archived | wheel listing (`sdk.dll`, `spy.dll`, `spy_debug.dll`); GitHub `archived: true` |
| `ntchat`/`wxautox` are Windows-only | PyPI wheel filenames (`win_amd64`), classifier `Operating System :: Microsoft :: Windows`, `comtypes`/`pywin32` deps |
| WeCom 客服 48 h / 5-message window | official doc `94677`, quoted |
| WeCom 群发 requires member confirmation | official doc `92135`, quoted |
| WeCom trusted IP is mandatory, no wildcards | WeCom help `21711`, quoted |
| WeCom registration needs a company; verification is 300 CNY/yr | WeCom help `15422`/`19729`, quoted |
| `wechatpy` has an enterprise module | downloaded the wheel, listed `wechatpy/enterprise/*` |
| Linux DB key extraction is real and already a DMCA target | `TANGandXUE/wcdb-key-tool` README; `ylytdeng/wechat-decrypt` blocked (`dmca` reason in GitHub API) |
| WeCom has no deep history; archive API reaches 5 days, is paid + admin-enabled | docs `101365` ("只可获取5天内的会话记录数据"), `91774`, `100252`; all endpoint paths in the verb table (`91039`, `90236`, `90201`, `90208`, `94677`, `94670`, `94691`, `90253`/`90254`) checked against the doc pages |

**Not verified, explicitly labelled:**

* Whether WeChat 4.1.13's AT-SPI tree exposes *send* affordances the way `wxauto`'s Win32
  tree does. `MimicWX-Linux` (`src/wechat.rs`) expects `entry`, `frame`, `list`, `label`,
  `push button`, `split pane`, `text` roles and localized label `"Chats"`/`"Messages"` — that
  is a third-party report plus B1's finding that the underlying interfaces exist, not a
  measurement of WeChat itself. **WeChat was not installed and not run** (no system installs,
  per the brief). Treat "desktop automation of WeChat 4.1.13 on Linux works end-to-end" as
  *probable, unverified*.
* Whether the `wcdb-key-tool` GDB path works against 4.1.13.26 specifically (its README says
  4.1+, verified on real hardware by its author, not by me).
* Whether a *specific* WeCom tenant can enable `kf` — that depends on their console and
  verification state, which no amount of reading settles.
* Real-world account-ban rates for each 个人号 tool. No tool publishes data, and the numbers
  in issue threads are self-reported. It does not change the decision, which rests on ToS
  text and DMCA enforcement rather than on ban probability.

---

## Bottom line

There is no legitimate way to drive a personal WeChat account from an agent on this machine.
The libraries that once did it are dead by protocol, abandoned by their authors, Windows-only,
or removed under legal pressure, and the one live surface — the official Linux client — is
explicitly forbidden to automate. The only path that survives is WeCom's official API, which
is a different product with a different audience and a 48-hour window into WeChat. If that is
the need, build `mycord-wecom-skill` on `httpx` as specified above. If it is not, the correct
determination is that this skill should not exist, and WeChat voice should be closed rather
than deferred.
