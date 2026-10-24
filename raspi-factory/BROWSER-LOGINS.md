# Browser logins — a real browser, for sites with no API to grant

Status: **Phase 1 implemented** (2026-10-24). Phase 2 (live view for captcha/2FA) designed, not built.

## Why this exists

Composio covers apps that *offer* an API: Gmail, Calendar, Notion, Shopify. It has nothing
to offer for the sites people actually ask about — a parcel tracker, a personal Instagram, a
council portal, a utility bill. Those have no API to grant. What they have is a **session**:
sign in once in a browser, keep the cookie, reuse it.

So this is the sibling of Connectors, not a replacement. Same promise, same place in the UI,
different engine.

## Settled decisions

| Question | Decision | Why |
|---|---|---|
| Where does it live? | Inside the **Connectors** tab, as a sub-view next to API connections | Users think "connect USPS", not "which engine". One status vocabulary: `connected` / `needs login` / `needs you` / `unknown` |
| Who holds the credentials? | The **device**. Never sudo's backend, never the model | Same principle as the Composio device-flow design; nothing central to breach or subpoena |
| How is a session kept? | Per-site Chromium profile under `/var/lib/sudo-browser/profiles/<site>`, mode 0700 | It is the site's own session, not a token we invented |
| What if the site changes? | Report **unknown**, never a guess | A wrong "connected" costs the owner a failed checkout; "unknown" costs one tap |
| Captcha / 2FA? | Stop and say **needs you** | Getting around a challenge is both fragile and rude. Phase 2 adds a live view so the human can solve it themselves |
| Which sites ship? | USPS, Instagram, plus "Another site" | The two Evy actually asked about. Every extra recipe is a promise to maintain it |

## What was built, and where

| Piece | File | Notes |
|---|---|---|
| Worker | `sudo-browser/browser-worker.py` | Persistent Chromium profile per site. `classify()` is pure, so it is testable without a browser |
| Recipes | `sudo-browser/sites.json` | Hints, not dogma: one reliable marker per verdict |
| CLI | `sudo-browser/sudo-browser` | Shim in `/usr/local/bin`; the dashboard shells out to it |
| Installer | `sudo-browser/install-sudo-browser.sh` | venv + Playwright + Chromium on demand (~450 MB). Refuses 32-bit ARM |
| Dashboard API | `sudo-dashboard/server.py` | `GET /api/agent/browser-logins`; POST `install`, `add`, `probe`, `login`, `forget`, `remove` |
| Dashboard UI | `sudo-dashboard/index.html` | Sub-tabs in Connectors; per-site rows with state badge and actions |
| Styles | `sudo-dashboard/app.css` | Reuses `.home-tabs` so "a view inside a view" is one pattern, not two |
| Strings | `sudo-dashboard/i18n.js` | 28 new keys × en / es / zh / ar |
| Tests | `sudo-browser/test_browser_state.py` | 17 tests, no browser needed |

## Credential handling

- Passwords are sent to the worker on **stdin**, never argv — `ps` is world-readable.
- Stored in `/var/lib/sudo-browser/secrets.enc`, mode 0600. Encrypted with AES-GCM when
  `cryptography` is present, key derived from `/etc/sudo/device_secret` via scrypt.
- When `cryptography` is absent the file is still 0600 but **plaintext**, and the state file
  records `encrypted: false` so the UI can say so instead of implying protection it lacks.
- Never logged. The worker logs verdicts and notes, never field values.

## What it deliberately does not do

- **No live browser view.** Phase 2: render the Chromium session into the dashboard so the owner
  can log in themselves (2FA, captcha, "new device" checks). Until then those sites report
  `needs_you`, which is honest and actionable.
- **No headless evasion.** No stealth plugins, no proxy rotation, no captcha solving. The Pi's
  advantage is that it browses from the owner's own home IP, which is not something to fake.
- **No banks, no government ID portals.** Written down so nobody adds them later by accident.

## Testing on a device

1. `sudo /usr/local/bin/sudo-install-browser.sh` (or the button in Connectors → Browser logins).
2. Connectors → Browser logins → Add USPS → status should become `connected` or `needs_login`
   within a few seconds. `needs_login` on a fresh profile is correct.
2. Enter USPS credentials → `connected` means the profile now holds a session; `needs_you` means
   USPS challenged it (expected from a new IP).
3. Restart the dashboard, hit Refresh: the state must survive (it lives on disk, not in memory).
4. Remove the site: `/var/lib/sudo-browser/profiles/<site>` must be gone, and the stored secret too.

## Open questions

- 2FA capture: dashboard prompt vs asking through the bot on the phone.
- Session health checks cost a browser launch (~2-4s on a Pi 5). Worth caching the last verdict
  for a few minutes, or only probing when the agent is about to act?
- Should the same worker run on Evy's laptop (residential IP, real profile) for her own agents?
