# google-voice-skill — planning and progress

**Last Updated:** 2026-10-25
**Status:** COMPLETE — shipped, installed, verified against the live account.

Scope: add `google-voice-skill/` to this repo, an agent skill over the existing
`gvoice/` TypeScript library.

---

## Why the component exists

`gvoice/` reverse-engineers Google Voice end to end: proto3-JSON over
`clients6.google.com`, SIP over WebSocket, a `werift` media plane, opus, VAD, and a
local agent loop. That library is the achievement, and it is not an agent skill — it is
a library plus a set of probe scripts. Two things stood between "the library works" and
"an agent can use it unattended":

1. **The session.** Every call needs the Google cookie jar including HttpOnly cookies.
   The library's answer was a hand-written DevTools snippet plus a listening HTTP
   server — fine while a human is already in DevTools, unusable from a shell.
2. **Reading.** The API returns positional proto3-JSON arrays. Nothing about the field
   layout is guessable, and two of the obvious guesses are wrong (below).

## Design decisions

**The library keeps ownership of the protocol.** The skill does not reimplement
SAPISIDHASH, SIP, or the media plane. `bridge.py` marshals JSON across a Node boundary
to `gvoice/src/gv-api.ts`; `call`/`sip`/`sms send` exec the library's own probes. One
implementation, not two.

**The library stays where it is.** Confirmed with the user: `gvoice/` really belongs in
its own repo eventually, but for now it stays a sibling directory. Discovery resolves a
sibling by default and accepts `--library` / `GVOICE_ROOT`, so the move is a one-line
change later.

**Capture over CDP, not the cookie database.** Reading Chrome's SQLite `Cookies` file
means decrypting with the OS keyring; the Extensions API needs a signed CRX;
Playwright's `context.cookies()` needs a Playwright-launched browser. CDP is the
narrowest working path, and it needs no dependency — `cdp.py` implements the four
WebSocket frames involved over the standard library.

**Copy the profile, do not write to it.** Chrome refuses CDP on a profile another
instance is using, and mutating a live profile is a good way to corrupt it, so `login`
copies to a scratch dir first. `--no-copy-profile` overrides when no Chrome is running.

**No invented direction.** See below; the skill reports message *kind* and refuses to
guess authorship.

## What the live account disproved

Both of these shipped wrong in the first draft and were caught by running the CLI rather
than reading the code. They are recorded because the wrong answers looked plausible.

**`f5` is not direction.** The first decoder read a direction flag from `f5`. An
all-inbound automated thread (four verification codes from one short code) produced
`f5 = 1, 1, 0, 1`, so the field varies within messages that were all received.

**Kind/type is not direction either.** Second attempt: `f4`=10/`f12`=5 versus 11/6 looked
like a clean split. Across 69 messages in 12 threads it is exactly two clusters — SMS and
MMS — and both appear on both sides of a real two-way conversation. `f17` is set on every
MMS row and null on every SMS row, so it tracks type, not sender. `f2` equalled the
account's own number in all 69 rows and `f13` was `1` in all of them.

So there is no direction field in this response, and the module says so rather than
labelling every message inbound. A test pins the measurement so nobody "fixes" it back.

**`thread/search`: `group[0]` is the message id, not the thread id.** Using it as a
thread id yields `s.<sha1>` values that look right and are rejected by `thread/get`. The
thread id is `group[6]`.

## Verified behaviour

| Behaviour | Evidence |
| --- | --- |
| Cookie capture from a signed-in profile | 39 cookies across 11 hosts, `SAPISID` present |
| `account/get` | returns the account's own E.164 number |
| `inbox/warmsync` → `thread/list` | token is an integer; `[pageSize]` alone returns no threads |
| `thread/get` | messages decode with timestamp, kind, peer, text |
| `thread/search` | filters (nonsense query → 0 results), thread id from `group[6]` |
| SIP REGISTER | `200 OK` from a plain Node client |

58 tests pass, 9 of them against the live account (`-m live`). `ruff` and `mypy
--strict` are clean.

## Remaining work (library, not skill)

Sending SMS still needs a server-issued, single-use message id (`f9`) or the encrypted
envelope (`f11`). Reversing what issues that id belongs in `gvoice/`; the skill blocks
`sms send` without a captured exchange and explains why.

## Follow-ups

- Install the skill by symlink (`~/.agents/skills/google-voice-skill`) when a machine
  needs it discoverable by name.
- If `gvoice/` moves to its own repo, only `bridge.find_library()` and the `README`
  cross-references change.
