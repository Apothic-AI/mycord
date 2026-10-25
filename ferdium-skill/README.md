# mycord-ferdium — drive a Ferdium install over CDP

[Ferdium](https://github.com/ferdium/ferdium-app) is an Electron app that wraps every
configured chat service in its own Chromium `<webview>`. That makes it the one place
an agent can reach **already-authenticated** sessions for many platforms at once —
no bot registration, no OAuth app, no second browser, no proxy, no stealth arms race.

This package owns only what is identical across services:

| Concern | Here |
| --- | --- |
| Launching the app with a debug port | `launcher.launch()` |
| Headless (Xvfb) vs headed | `headless=True` (default) |
| Discovering service webviews | `Browser.webviews()` |
| Page primitives (type, click, send, wait) | `Page` |
| Which services are configured | `launcher.services()` |

Service-specific DOM work belongs in a dependent skill — see
[`mycord-whatsapp-skill`](../whatsapp-skill/).

## Install

```bash
uv sync --all-extras                 # dev tools only; runtime has no dependencies
flatpak install flathub org.ferdium.Ferdium
```

`mycord-ferdium` is on PATH via `uv run`, or `uv tool install ./ferdium-skill`.

## First run — a human must add an account

Ferdium cannot be configured non-interactively: adding a service means clicking
through the app's UI. So the skill refuses to run headless when nothing is configured:

```bash
$ mycord-ferdium doctor
installed    : yes
config root  : ~/.var/app/org.ferdium.Ferdium/config/Ferdium
services     : 0

No services are configured yet. Launch headed and add an account:
  mycord-ferdium launch --headed
```

```bash
$ mycord-ferdium launch --headless
No services are configured, so a human must add one first.
Re-run with --headed and add your account in the window that opens.
# exit code 3
```

Launch headed, add the account in the window, and **leave it running** — the debug
port is enabled at launch, so no restart is needed once the account is added. Headed
and headless differ *only* in the display; the CDP surface is identical.

Exit codes are a contract, so dependent skills can distinguish the states:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | runtime error (CDP, launch, selector) |
| 2 | Ferdium is not installed |
| 3 | installed but no services configured — needs a human, headed |

## Everyday use

```bash
mycord-ferdium doctor                    # install + config state, no launch
mycord-ferdium services                  # configured services and their hosts
mycord-ferdium launch --detach           # headless on a virtual display
mycord-ferdium targets                   # live targets, including webviews
```

## Three mechanics that are easy to get wrong

Each of these cost real debugging time; all are handled for you.

**1. A debug port only exists at launch.** Ferdium never calls
`appendSwitch('remote-debugging-port')`, so `ELECTRON_ARGS` does nothing. The flag
must arrive as a raw argument. Every agent session therefore begins with a relaunch.

**2. `/json/list` does not show webviews.** Service pages appear only through the
`Target` domain:

```python
browser = launcher.Browser(port)
browser.webviews()          # [{type: webview, host: web.whatsapp.com}, ...]
```

**3. Flattened sessions put `sessionId` on the envelope.** Passing it inside `params`
yields `Runtime.evaluate wasn't found`, which reads like a missing domain:

```python
session = browser.attach(target)     # sessionId goes on every message for you
session.eval("document.title")
```

## Headless is Xvfb, not native headless

Electron's headless switches do not keep webviews working. `headless=True` starts the
app on a virtual display (`Xvfb`) instead. It is genuinely invisible and fully
functional — but it is a virtual display, not a headless renderer, and worth naming
accurately.

The display's lifetime is owned by the `Running` handle, so it dies with the app
rather than with whatever shell started it:

```python
with launcher.launch() as running:
    browser = running.browser()
    ...                      # app and display both torn down here
```

## Hibernation

Ferdium sleeps idle services. A hibernating service **has no webview target**, so
`find_webview()` returns `None` and the error names the cause. Wake it by opening the
service in the UI once, or turn hibernation off in Ferdium's settings for services an
agent needs continuously.

## Requirements

- Python 3.11+
- Flatpak with `org.ferdium.Ferdium`
- `Xvfb` for headless mode (`apt install xvfb`)

No third-party Python packages: CDP is spoken over a small stdlib WebSocket client.

## Development

```bash
make setup && make lint && make typecheck && make test
make test-live      # needs `mycord-ferdium launch --detach` first
```

### Adding a service

A new medium skill is mostly reverse-engineering one service's DOM. Two things help:

* **Ferdium's own recipe is the map.** Each service ships a `webview.js` whose
  page-side script names the stores and selectors the service actually uses — e.g.
  WhatsApp's reads unread counts out of the `chat` object store in the
  `model-storage` IndexedDB. Read it before probing.
* **`reverse-engineer-anything` (REA)** if you need to go deeper into an Electron
  app — `list_electron_targets`, artifact inspection, decompilation. It is an
  optional aid, not a dependency: nothing here imports or requires it.

Two findings worth not rediscovering: message **bodies are not in IndexedDB** (the
model store carries metadata only, and `fts-storage` holds a token index), so content
has to be read from the rendered DOM; and services **hibernate**, so a sleeping
service has no webview target at all.

## Safety rails

* The app holds your real, authenticated sessions. Confirm before anything that
  sends — a message reaches a real person.
* One webview per configured service; nothing here changes account state except the
  actions you explicitly issue.
* `launch --detach` leaves a process running. It is attached to a specific port; use
  `Running.stop()` (or kill that PID) rather than a broad `pkill`.
