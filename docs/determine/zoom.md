# Zoom — library determination

**Date:** 2026-10-25
**Status:** determination complete; no code written.
**Question:** optimal no-app-registration path for a `mycord-zoom-skill`.

## Decision

**Drive the official Zoom desktop client (v6.5.11.4015, installed at `/opt/zoom`) with
`zoommtg://` deep links for join/start, and take call audio through a PipeWire
virtual-device pair (`module-null-sink` + `module-remap-source`) that the agent owns
end-to-end via `pactl`/`pw-cli` — this is the first target that gets full-featured
*voice* with no app registration and no bot identity.** Runner-up was the **web client
(`app.zoom.us/wc`) over CDP**, which is the better *messaging/history* surface (DOM
access, no GPU-abort problem, works headless) but cannot touch call audio without the
same virtual-device machinery, and loses the desktop client's system-audio-share and
native call controls. They are complementary, not exclusive: **desktop for calls, web
over CDP for reads/history** — see *Implementation shape*.

Rejected outright: `@zoom/meetingsdk` / `@zoom/videosdk` (require a registered Zoom
Marketplace app + JWT signed with its Client Secret — fails the no-app bar explicitly),
Zoom RTMS (official media plane, but credits + registered app), Zoom Rooms ZR-CSAPI
(needs a licensed Zoom Room; deprecated for Windows/macOS after April 2027), and any
PEP/`pip`/`npm` "zoom" library (all of them wrap the OAuth REST API, i.e. a registered
app, or are abandoned).

## Comparison

| Candidate | Class | Reads | History | Sends | Voice | No-app | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Zoom desktop client + `zoommtg://` + PipeWire virtual devices** | 3 (native client driven locally) | via UI/CEF; no API | local DB only (encrypted) | chat via UI only | **full duplex, verified plumbing** | **yes** | **chosen (primary for calls)** |
| **`app.zoom.us/wc` web client over CDP** | 4 (web client over CDP) | DOM text | DOM-scrolled | DOM typing | WebRTC in-page; needs virtual device or `getUserMedia` hook | **yes** (guest join allowed; invisible CAPTCHA from 2026-07-22) | **chosen (primary for reads)** |
| `@zoom/meetingsdk` 6.10.0 (MIT, active) | 1 (official SDK) | n/a | n/a | n/a | yes, in-SDK | **no** — needs Marketplace app + Client ID/Secret JWT; unpublished apps cannot join external meetings | rejected on the hard bar |
| `@zoom/rtms` 1.1.0 / PyPI `rtms` 1.1.0 (Zoom official) | 1 (official media plane) | streams | n/a | no | **media only**, server-side | **no** — app + credits | footnote: the *right* answer if the no-app rule is ever relaxed |
| Zoom Rooms ZR-CSAPI (`zCommand`/`zEvent`) | 3 (local CLI/SSH) | meeting state | n/a | commands | none | no (licensed Zoom Room) | rejected + deprecation notice (Apr 2027) |
| `zoomus.conf` / CEF `cefIpcChannel` FIFOs | 3 (undocumented local IPC) | unknown | no | unknown | **no audio** | yes | **rejected** — proprietary, unframed, breaks on update |
| `pyzoom` 1.0.8 (PyPI, MIT, 2024-03-11) | 1 (REST wrapper) | meeting metadata only | no | no | no | **no** — OAuth app | rejected |
| `zoomus` 1.2.1 (PyPI, 2023-08-30) / `zoomus` 0.1.5 (npm) | 1 (REST wrapper) | metadata | no | no | no | **no** — OAuth app | rejected |
| `zoom-api` 0.10.0 (crates.io) | 1 (generated REST client) | metadata | no | no | no | **no** — OAuth app | rejected |
| Ferdium (webview host) | 5 (fallback) | DOM | DOM | DOM | webview only | yes | not needed — see open question 3 |

## Why this one

### The hard bar eliminates every "library" candidate

Zoom's developer surface is uniformly gated on a Marketplace app. Verified from Zoom's
own docs:

* **Meeting SDK** — auth requires an app-scoped **JWT signed HS256 with the app's Client
  ID/Client Secret**; joining meetings *outside* the developer's own account additionally
  requires the app to be **reviewed by Zoom** and to authenticate with a **ZAK or OBF
  token** ([Meeting SDK authorization](https://developers.zoom.us/docs/meeting-sdk/auth/),
  [Get credentials](https://developers.zoom.us/docs/meeting-sdk/get-credentials/)).
  The legacy SDK Key/Secret was deprecated in favour of app Client ID/Secret, enforced
  from 2026-06-27. A forum thread confirms the practical consequence: an unpublished
  Meeting SDK app **cannot join public meetings**
  ([devforum](https://devforum.zoom.us/t/unpublished-zoom-sdk-app-to-join-public-meetings/95899)).
* **RTMS** — real, current, official (`@zoom/rtms` 1.1.0, 2026-04-16; `rtms` 1.1.0 on
  PyPI) and it is exactly the "give an agent the meeting's audio" product. It still
  requires app credentials **and paid credits** ("you'll need credits on your account",
  [RTMS docs](https://developers.zoom.us/docs/rtms/)), and media arrives server-side,
  not as a spoke participant.
* **REST wrappers** on the registries are all OAuth/S2S-app clients over
  `api.zoom.us/v2`: `pyzoom` (MIT, last upload 2024-03-11, deps `requests`/`pydantic`),
  `zoomus` (PyPI 1.2.1, 2023-08-30; npm 0.1.5, MIT, deps `qs`/`lodash.isplainobject`),
  `zoom-api` on crates.io (0.10.0, 2025-04-08, "fully generated & opinionated API
  client"). None carries call media. pyzoom/zoomus are also stale.
  Not found on PyPI: `zoom-sdk`, `zoomsdk`, `zoommeeting`, `zoom-api`. On npm:
  `zoom-sdk` was **unpublished** 2021-12-10, `zoomsdk`/`zoom-meeting-sdk`/`zoom-web`
  do not exist. On crates.io: `zoom` is a particle-physics crate, `zoomsdk`/`zoom-sdk`
  /`zoom-client` do not exist. `zoom-launcher` (npm 1.1.0) is a macOS Calendar.app
  helper last touched 2022; `zoommtg` (npm 0.1.0) is an unrelated Create-React-App
  skeleton.
* **Meeting SDK is also off-limits by policy, not just auth**: its own README says it
  "*is reserved for human use cases and does not support bots or AI notetakers*". So
  even with an app, an agent using it would be violating the vendor's stated intent.

So there is **no library**. The question becomes how to drive the client the account
already owns.

### Deep links are the control surface — verified, not assumed

`Zoom.desktop` registers `x-scheme-handler/zoommtg`, `zoomus`, and more
(`/usr/share/applications/Zoom.desktop`), and `xdg-mime query default
x-scheme-handler/zoommtg` returns `Zoom.desktop`. Verified by watching `/proc` during a
launch: the URL is passed **verbatim as an argument** to the real binary, with no
translation layer:

```
/opt/zoom/ZoomLauncher  zoommtg://zoom.us/join?action=join&confno=000000000&uname=probe
/opt/zoom/zoom          zoommtg://zoom.us/join?action=join&confno=000000000&uname=probe
```

and the client logs it back: `cmd line: zoommtg://zoom.us/join?action=join&confno=123456789&pwd=AbCdEf&uname=detprobe`
(`~/.zoom/logs/zoom_stdout_stderr.log`). The binary contains the `zoommtg` and `zoomus`
scheme strings and parses `action_name`/`confno` (string table in `/opt/zoom/zoom`).
Joining therefore needs **no account, no token, no app** — only a meeting URL, which is
the same thing a human clicks. Caveat, labelled: Zoom has never documented these
parameters (Zoom staff on the dev forum say so explicitly: schemes "*could change at any
time*"), so the join URL must be treated as a versioned, verified-at-runtime contract,
not a stable API.

### The desktop client has no usable programmatic API

This was checked rather than assumed, because it changes the design:

* **No listening socket.** With the client running, `ss -tlnp` shows no Zoom-owned TCP
  listener; there is no ZR-CSAPI-equivalent for the plain client (that product is
  licensed separately and is being deprecated for Windows/macOS after April 2027).
* **`cefIpcChannel` is not an agent surface.** `~/.zoom/data/cefIpcChannel/` contains
  pairs of **named pipes**, `webview:{uuid}_req` / `_res` (`prw-------`, i.e. FIFOs).
  They are per-webview-pane channels between the native shell and its embedded CEF
  panes; the framing is proprietary and unframed from outside (no length prefix, no
  schema, no handshake observable in the binary's string table). Writing into a live
  pane's `_req` FIFO is a protocol-guessing exercise that breaks on any client update.
  **Rejected as brittle** per the repo's rules. Not attempted: it would inject into a
  signed-in client for no gain over the UI.
* **`webview.debug.enable=true` does not open DevTools.** Setting it in
  `~/.config/zoomus.conf` and restarting produced no `DevToolsActivePort` file, no
  `remote-debugging-port` argument in any `ZoomWebviewHost` command line, and no
  listener. (Tested 2026-10-25; config restored to `false` afterwards.) Note the CEF
  panes report `Chrome/137.0.7151.69` in their UA, so the flag may exist in principle,
  but I could not make it expose CDP. **Labelled: could not verify / likely dead end.**
* **`~/.zoom/data/` is encrypted.** `zoomus.enc.v2.db`, `zoomus.zmdb.kvs.enc.db`,
  `zoomus.tmp.enc.db` — no readable local message store to read `history` from. So
  history must come from the web client, not the local DB.

No CLI verbs either: the binary exposes no `--join`/`--test-audio`-style long flags
(beyond `--path/--play/--select/--share/--transcoding`), so `zoommtg://` is the only
scriptable entry point. Verified with `zoom --help` (no help output; it just launches).

### The web client is the right read surface

`app.zoom.us` is reachable and no-app-friendly: `https://app.zoom.us/wc/join` → 200,
`https://zoom.us/wc/0000000000/join` → 200 (524 KB app shell). Guest join is supported
("You do not need a Zoom account to join meetings as a participant"), the *"Join from
your browser"* setting is **enabled by default** for accounts, and E2EE (if the host
enables it) disables the browser path. One forward-looking constraint from Zoom's own
doc: *"Starting July 22, 2026, the Require solving a CAPTCHA for guest users setting will
start to be enabled for all accounts in a phased rollout"* — an **invisible** CAPTCHA,
capable of demanding sign-in when a session is flagged. That is a real risk for a
headless guest-join skill and is why the desktop client stays primary for calls.

Because the web path is a normal Chromium/Page session, it fits this repo's existing
`cdp.py` + `Page` pattern (as `mycord-slack-skill` and `ferdium-skill` already do), and
`getUserMedia` is interceptable in-page.

## Voice assessment

**Verdict: verified possible, full duplex, and it does not require the SDK, an app, or a
bot — the third voice shape the brief hoped for. Three independent mechanisms are
verified; the residual unknown is device-selection plumbing, labelled below.**

### The transport: PipeWire/Pulse virtual devices (this machine, verified)

Local `pactl` is `PulseAudio (on PipeWire 1.4.2)`, and Zoom's main binary carries
`libpipewire-0.3.so`, `libpulse.so.0`, and `libasound.so.2` in its string table
(`/opt/zoom/zoom`; the Qt plugin `/opt/zoom/Qt/plugins/audio/libqtmedia_pulse.so` has 39
undefined `pa_*` symbols and `NEEDED libpulse.so.0`). Zoom identifies itself to the
audio server as **`ZOOM VoiceEngine`**, observed live via `pw-cli ls Client`. So Zoom is
an ordinary client of the system mixer — **no virtual sound card kernel module, no
loopback hardware, no root**.

Everything below was executed on this machine and the results are from that run
(full log: `/tmp/det-zoom.log`):

| Step | Command | Result |
| --- | --- | --- |
| Null sink as a "speaker" | `pactl load-module module-null-sink sink_name=detzoom_vspk` | rc=0, `detzoom_vspk` RUNNING |
| Its monitor as a "microphone" | `pactl load-module module-remap-source master=detzoom_vspk.monitor source_name=detzoom_vmic` | rc=0, `detzoom_vmic` present |
| File-backed mic (alternative) | `pactl load-module module-pipe-source source_name=detzoom_pipe file=/tmp/x` | rc=0, SUSPENDED until written |
| Loopback / echo-cancel | `module-loopback`, `module-echo-cancel` | rc=0 (both load) |
| **Inject audio into "speaker" and capture what Zoom would hear** | write 1 s 440 Hz tone to `detzoom_vspk`, `parec` from `detzoom_vmic` | **peak=32768 (full scale), 163 840 frames** — signal survives the remap path intact |
| **Re-point a live playback stream** | `pactl move-sink-input <id> detzoom_vspk` | **rc=0, destination confirmed** as `detzoom_vspk` |
| **Re-point a live capture stream** | `pactl move-source-output <id> detzoom_vmic` | **rc=0, destination confirmed** as `detzoom_vmic` |
| System-default route | `pactl set-default-sink detzoom_vspk` | rc=0; all existing clients re-bound to it (9 sink-inputs) |

That is the shape: the agent **writes PCM to a sink** (which Zoom believes is a speaker
it need not consume) and **reads PCM from a source** (which Zoom believes is a
microphone). Mechanically this is identical to *gvoice*'s job — decode inbound speech,
run VAD, synthesise a reply — with the `werift` RTP plane replaced by a PipeWire node,
and to *signal-cli*'s `SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe` PCM socket. **It is the third
shape, and it generalises to every conferencing client that uses the system mixer
(Teams, Skype, Meet-in-browser, Slack huddles) because it depends on the client having
no exotic audio path, which is the norm.** Recommend extracting it as a shared
`mycord-audio` plane for roadmap R1/R2 as well.

### The residual unknown, labelled honestly

**Whether Zoom *offers* `detzoom_vmic` in its microphone dropdown, and whether it obeys
a system-default change for a *meeting* (as opposed to a UI sound), is NOT verified.**
I could not reach Zoom's Settings → Audio panel or join an actual meeting without a real
meeting ID and account, which the brief forbids. What I did verify: (a) the client starts
and runs normally with the virtual devices present (`DetZoomSpeaker` /
`DetZoomMicrophone` loaded before launch, client launched 3/3 successfully); (b) it
registers as `ZOOM VoiceEngine` with the same PipeWire daemon that owns those devices;
(c) `zoomus.conf` exposes only `system.audio.type=default` as an audio key on a clean
profile (no device-selection keys were written), while the binary's string table contains
`defaultMicID`/`defaultMicName`/`defaultSpeakerID`/`defaultSpeakerName`/`speakerDevices`,
i.e. device selection is stored somewhere but not in the files written on an unconfigured
profile. Since Zoom is a stock PulseAudio/PipeWire client that ships a "Same as System"
audio mode, appearing in the device list is **expected with high confidence but
unverified**; the first implementation task is to confirm it with a real meeting (see
open question 1).

If Zoom turned out to bind early and hard (bypassing the mixer), the fallbacks are, in
order: `pw-loopback` (present, `/usr/bin/pw-loopback`, libpipewire 1.4.2) to wire nodes
explicitly; `module-echo-cancel` as a named microphone Zoom must list; or per-process
audio isolation via a second PipeWire daemon. All three are local, no-root mechanisms.

### The other two mechanisms, for completeness

1. **Web client + CDP (verified present, not wired):** Chrome on this machine advertises
   `--use-fake-device-for-media-stream`, `--use-fake-ui-for-media-stream`, and
   `--use-file-for-fake-audio-capture` (strings present in `/opt/google/chrome/chrome`),
   and Camoufox/Firefox `156.0.1-beta.36` advertises `media.navigator.streams.fake`
   (string present in its `libxul.so`). Either turns the browser into a deterministic
   audio peer with no virtual devices at all — the cleanest path for the *web* client,
   but it is a browser-only trick and gives no inbound capture beyond what the page's
   own APIs expose.
2. **RTMS (official, rejected):** the sanctioned "agent gets meeting audio" product, but
   app + credits. Worth revisiting only if the no-app rule is ever relaxed for Zoom.

### If, and only if, virtual devices prove unusable

Roadmap text, for `docs/roadmap.md`:

> **Zoom voice — ASR/TTS over virtual devices**
> `mycord-zoom-skill` can join and read/write text, but call audio needs a PipeWire
> virtual sink + remap-source pair (`module-null-sink` / `module-remap-source`) plus
> stream rerouting (`pactl move-sink-input` / `move-source-output`), with the gvoice
> speech stack (parakeet-redux / faster-whisper, Piper) attached to the PCM sides.
> Blocker: Zoom must list and honour the virtual device; unverified without a live
> meeting.

## Implementation shape

**Language: Python, package `mycord_zoom` (a `src/` layout mirroring
`mycord-slack-skill/`), with a `cdp.py` lifted from the existing skills.** Rationale:
audio I/O is process work (`pactl` + `pw-cat`/`parec` subprocesses, or `pipewire` bindings
later), the web half is CDP, and both are already Python in this repo. TypeScript/`werift`
is wrong here — there is no RTP to terminate, only local nodes.

Layout:

```
mycord-zoom-skill/
  SKILL.md                    # agent-facing contract
  pyproject.toml              # deps: websocket-client (CDP), stdlib otherwise
  src/mycord_zoom/
    cli.py                    # argparse subcommands, --json everywhere
    cdp.py                    # shared CDP client (copy of slack/ferdium version)
    page.py                   # cef-owned Page for app.zoom.us
    desktop.py                # zoommtg:// deep links + launch/geometry helpers
    audio.py                  # PipeWire plane: devices, routes, PCM in/out
    state.py                  # ~/.local/share/mycord-zoom (session, device ids)
  tests/                      # fake pactl + fake Page, as in slack/ferdium skills
```

Verbs (each `--json`; reads fail loudly rather than silently empty):

| Verb | Does | Backed by |
| --- | --- | --- |
| `join --url <meeting-url>` | parse the URL to `zoommtg://…` and launch the client | `desktop.py` (verified mapping) |
| `start --url <start-url>` | same for `action=start` (needs host/ZAK in URL) | `desktop.py` |
| `leave` | close the meeting window (window-close via `wmctrl`/`xdotool`) | `desktop.py` |
| `status` | client running? in meeting? which meeting ID? | process + window title + log tail |
| `session` | web session health (signed in? guest?) | `page.py` |
| `read --chat` / `read --participants` | scrape the meeting/web client DOM | `page.py` |
| `history --limit N` | meeting list / chat history from the web client | `page.py` (local DB is encrypted — see below) |
| `send --to <who\|everyone> --text` | type into the chat input | `page.py` |
| `search --query` | web-client search | `page.py` |
| `audio devices` | create/inspect/unload the virtual pair | `audio.py` |
| `audio route --to-agent` / `--to-mixer` | reroute Zoom's live streams (or set defaults) | `audio.py` |
| `audio say --text` / `audio listen` | TTS into the virtual mic / ASR off the virtual speaker | `audio.py` + gvoice speech stack |

Auth/session capture: **none for joins** (deep links need only the URL). For signed-in
reads, capture cookies from a dedicated browser profile over CDP exactly like
`mycord-slack-skill login` does — reusing that pattern rather than inventing one. Never
touch the operator's real Chrome profile.

Failure modes that will bite, in likelihood order:

1. **`zoommtg://` parameter drift.** Undocumented by Zoom's own admission. Parse the
   `https://…/j/<id>?pwd=…` URL yourself and construct the deep link; validate by
   checking the client's log line (`cmd line:`) and window title, and fail loudly when
   the shape changes rather than silently opening a browser.
2. **Audio device selection (the real blocker).** If Zoom does not list or accept the
   virtual device, route with `move-sink-input`/`move-source-output` at stream level —
   verified to work — and only then fall back to defaults.
3. **GPU/abort on this machine.** The client aborted (signal 6, `Failed to create OpenGL
   context`) repeatedly while I was polling windows too aggressively; with a plain
   launch it started 3/3. Treat launch as asynchronous: poll for the window, never
   kill-and-immediately-relaunch, and prefer `LIBGL_ALWAYS_SOFTWARE=1` on headless boxes.
4. **Guest CAPTCHA on the web path** (phased rollout from 2026-07-22, invisible, may
   demand sign-in). Keep a signed-in profile available; do not promise headless guest
   joins forever.
5. **E2EE meetings have no browser path at all** ("Web App users will be prompted to join
   from the Zoom Workplace desktop or mobile app instead") — desktop only.
6. **Re-auth and hibernation:** the client is a GUI app; closing the lid / session
   suspend drops the call. `status` must distinguish "signed out" from "not running".
7. **Rate/etiquette:** joining as a silent participant is visible to humans. Never
   auto-join a meeting without a human naming it, and never speak TTS into a live
   meeting without explicit per-meeting consent (repo rule: confirm before anything that
   reaches another person).

## Open questions

1. **A real meeting ID + host permission, twice over:** once on a throwaway meeting to
   confirm (a) Zoom lists `DetZoomMicrophone` in Settings → Audio and (b) a virtual-device
   join carries audio in both directions. This is the only unverified link in the voice
   chain, and it needs a human's meeting or a host's consent — nothing in this brief
   permitted joining a real one.
2. **Which identity the skill presents.** Joining as a guest with an arbitrary display
   name is no-app and needs nothing from the user; joining *as the account* requires an
   authenticated profile (the web client, or the desktop client signed in once by hand —
   the skill must never type the user's password). The brief's no-app bar is satisfied
   either way; someone should decide which is the default before the skill is written.
3. **Whether the web client is actually needed** for this skill's first version, given
   the desktop client covers calls and the encrypted local DB blocks history. If reads
   are in scope from day one, the `page.py` half is real work; if not, ship `join`/`leave`
   /`status`/`audio` first and add reads later. Recommendation: ship audio + join first —
   that is the capability no other medium in this repo has.
4. **Vendor risk to accept consciously.** Zoom's schemes are undocumented and it markets
   RTMS *instead of* bots/notetakers, so a client-driving agent is against the spirit of
   their developer programme even though it uses only the user's own client. Recorded as
   accepted risk, same class as the Discord selfbot warning in the top-level README.
5. **Shared speech plane.** Per `docs/roadmap.md`'s sequencing note, Zoom's voice work
   should consume the same extracted local ASR/TTS service as WhatsApp/Slack voice — not
   a third embedded copy of Piper/Parakeet.
