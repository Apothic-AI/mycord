---
name: ferdium-skill
description: Drive a Ferdium install over the Chrome DevTools Protocol — launch it headless on a virtual display or headed, discover the per-service Chromium webviews, and read or interact with any service page. Use when a task needs an already-authenticated chat session (WhatsApp, and any other service configured in Ferdium) without a bot registration, OAuth app or second browser, or when a medium-specific skill needs its base driver. Handles the first-run case where a human must add an account in a visible window.
---

# mycord-ferdium — drive Ferdium over CDP

[Ferdium](https://github.com/ferdium/ferdium-app) is an Electron app that wraps every
configured chat service in its own Chromium `<webview>`. Every one of those webviews is
a CDP target, which makes Ferdium the one place an agent can reach **already-signed-in**
sessions for several platforms at once — no bot registration, no OAuth app, no proxy,
no stealth arms race.

This skill is the **base driver**. It knows nothing about any particular chat medium.
Medium-specific DOM work belongs in a skill layered on top (see
[`mycord-slack-skill`](../mycord-slack-skill/), and the planned WhatsApp skill).

| This skill owns | A medium skill owns |
| --- | --- |
| launching the app with a debug port | which selectors identify chats |
| headless (Xvfb) vs headed | how a message row is parsed |
| discovering service webviews | what "send" means for that app |
| page primitives (type, click, send, wait) | that app's quirks and traps |

---

## Start here

```bash
mycord-ferdium doctor      # installed? configured? which services and hosts?
```

`doctor` exits `2` if Ferdium is not installed and `3` if it is installed but has no
services — those are different states and both need a human, so they get their own
codes.

### First run: a human must add an account

Ferdium cannot be configured non-interactively — adding a service means clicking
through the app UI. So headless is refused until at least one service exists:

```bash
$ mycord-ferdium launch --headless
No services are configured, so a human must add one first.
Re-run with --headed and add your account in the window that opens.
# exit 3
```

Launch `--headed`, add the account in the window, and **leave it running**: the debug
port is enabled at launch, so no restart is needed afterwards. Headed and headless
differ *only* in the display — the CDP surface is identical.

### Everyday use

```bash
mycord-ferdium launch --detach        # headless on a virtual display (the default)
mycord-ferdium services               # configured services + the hosts they own
mycord-ferdium targets                # live targets, including webviews
```

## The three mechanics that are easy to get wrong

Each cost real debugging time; all are handled by the library.

**1. A debug port only exists at launch.** Ferdium never calls
`appendSwitch('remote-debugging-port')`, so `ELECTRON_ARGS` does nothing — the flag
must arrive as a raw argument. Every agent session therefore begins with a relaunch.

**2. `/json/list` does not show webviews.** Service pages appear only through the
`Target` domain. If you are writing your own client, use `Target.setDiscoverTargets` +
`Target.getTargets`; a plain HTTP target list will look empty and mislead you.

**3. Flattened sessions put `sessionId` on the envelope.** With
`Target.attachToTarget {flatten: true}`, the `sessionId` must ride on every message.
Passing it inside `params` yields `Runtime.evaluate wasn't found`, which reads like a
missing domain rather than a misplaced field.

## Page primitives

```python
from mycord_ferdium import attach, launch

with launch(headless=True) as running:          # app + display torn down here
    browser = running.browser()
    page = attach(browser, "web.whatsapp.com")  # names the cause if hibernating

    page.exists('[data-testid="cell-frame-container"]')
    page.texts("#main div[role=row]", scoped_to="#main")
    page.click_text('[data-testid="cell-frame-container"]', "Violet")
    page.send_message('#main footer [contenteditable="true"]', "Hi")
```

Two primitives are not interchangeable with the obvious approach, and both would fail
*silently* if done the natural way:

* **Typing** goes through `document.execCommand('insertText')`. Assigning `innerText`
  or `value` updates the DOM but not the framework's state, so the composer displays
  text the app will never send.
* **Pressing Enter** goes through CDP `Input.dispatchKeyEvent`. A synthetic
  `KeyboardEvent` from page JS is ignored by these apps' key handlers.

`send_message()` reads the composer back and **refuses to press Enter on a mismatch**,
so a half-typed message cannot be sent by accident.

## Headless is Xvfb

Electron's own headless switches do not keep webviews working, so `headless=True`
draws the app on a virtual display instead. It is genuinely invisible and fully
functional — but it is a virtual display, not a headless renderer, and the distinction
matters when you reason about what the page can detect. The display's lifetime belongs
to the `Running` handle, so it dies with the app rather than with the shell that
started it.

## Hibernation

Ferdium sleeps idle services, and **a hibernating service has no webview target at
all**. `attach()` raises an error naming the live webviews so this is obvious rather
than mysterious. Wake the service by opening it in the UI once, or disable hibernation
for services an agent needs continuously.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | runtime error (CDP, launch, selector) |
| 2 | Ferdium is not installed |
| 3 | installed but unconfigured — needs a human, headed |

## Safety rails

* These are **real, authenticated sessions**. Confirm with the human before anything
  that sends; a message reaches a real person.
* Never fan out messages or drive many chats in parallel — beyond being rude, it is the
  traffic shape that gets accounts restricted.
* `launch --detach` leaves a process on a known port. Track that PID rather than using
  a broad `pkill`.
* Do not delete or reset a service's partition directory: it holds that account's
  session and its fingerprint.
