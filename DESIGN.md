# Sudo design

How Sudo looks, sounds and behaves. Read this before building or changing
any screen: the dashboard, an app page, a setup step. It describes what is
already in `raspi-factory/sudo-dashboard/app.css`, so new work matches what
people already know.

## Who it is for

Sudo is a small computer that lives in someone's home and runs their own
agent. The people using it are families, not developers: a mom checking who
is home, someone linking WhatsApp for the first time. The interface should
feel **calm, warm and trustworthy**: a household object, not a control
panel. Plain words, one clear action per screen, nothing that needs
explaining twice.

## Hard rules

These are not style preferences. Breaking one breaks the product.

- **Works with no internet.** The dashboard is served on the home network
  and must render offline. No CDNs, no web fonts fetched, no outside images
  or scripts. Fonts fall back to the system's.
- **Plain HTML, CSS and JS.** No build step, no framework. `index.html`,
  `app.css`, `i18n.js` and `server.py` are the whole front end.
- **Phone first.** Design at 375 px wide, then let it breathe on desktop.
  Touch targets are at least 40 px.
- **Every word is translatable.** All visible text goes through `i18n.js`
  (English, Spanish, Simplified Chinese, Arabic) with `data-i18n` or `t()`.
  No sentences built by concatenating translated fragments.
- **Right-to-left works.** Arabic flips the layout. Use logical properties
  (`margin-inline-start`, `inset-inline-end`, `text-align: start`), never
  `left`/`right` for layout. Phone numbers and addresses get `dir="ltr"`.
- **Every theme works.** There are four (`default`, `minimal`, `terminal`,
  `midnight`; the last two are dark). Use the CSS tokens. **Never hardcode a
  colour**: a hex value in a component is a bug on three themes.
- **Data is shown safely.** Anything a person or device supplied goes in with
  `textContent`, never `innerHTML`.

## Sudo and its apps

There are two kinds of screen, and they follow different colour rules.

- **Sudo itself**: the dashboard, Chat, Channels, Settings, setup. This is
  Sudo's own voice and uses Sudo's palette and colour meanings below.
- **Apps**: Contacts, Home, and whatever comes next, including apps people
  make for themselves. **Each app has its own look**, with a palette and
  colour meanings chosen for what the app is about. One day an app's colours
  may even be the person's own choice. Sudo's coral = on / purple =
  confirmed rule does **not** apply inside an app; a home app speaking in
  Sudo's settings colours makes no sense.

What every screen shares, app or not: the hard rules above (offline, i18n,
right-to-left, themes, safe data), the type families, the icon style, the
writing, and the "how to work on a screen" process.

## Colour (Sudo itself)

Tokens live at the top of `app.css` and are redefined per theme.

| Role | Token | Default | Use |
| --- | --- | --- | --- |
| Brand / confirmed | `--primary`, `--ok`, `--ok-tint`, `--ok-ink` | `#340075` purple | Main buttons, "done", "linked", "home" |
| On / alive | `--on`, `--accent`, `--coral` | `#fe6944` coral | A switch that is on, a device that is online, the send button |
| Background | `--background`, `--surface`, `--surface-low`, `--surface-container` | near white → light grey | Page, cards, sunken fields, chips |
| Text | `--ink`, `--ink-2`, `--ink-3`, `--ink-4` | darkest → faintest | Body, secondary, hints, disabled |
| Lines | `--line`, `--line-2` | light greys | Borders, dividers |
| Error | `--error`, `--danger` | `#ba1a1a` | Only for something that went wrong |
| App identities | `--sky`, `--amber`, `--violet` and their `-tint`s | | One per app (below) |

Meaning is fixed:

- **Coral means "on"**: switched on, online, live, sending.
- **Purple means "confirmed"**: saved, linked, done, home.
- **No green.** Not for success, not for online. It would be a third
  meaning for the same idea.
- Red is for errors only, never decoration.

## Colour (apps)

Each app defines its own small palette as tokens scoped to its page, with a
dark-theme version, and uses them for its header band, its tile and its
states. Pick colours and meanings that fit the subject.

```css
#view-house {                      /* Home: light blue */
  --house-blue: #3e89c9;           /* online, home */
  --house-blue-deep: #1d5a8f;      /* text on blue tints */
  --house-blue-tint: #dbeaf6;      /* "home" pills, online icons */
  --house-blue-soft: #eef5fb;      /* open editors */
  --house-band: #4f97d4;           /* the header band */
}
:root[data-theme="midnight"] #view-house { /* darker band, lighter blue */ }
```

| App | Palette | Meaning |
| --- | --- | --- |
| Contacts | coral band | (set by Contacts) |
| Home | light blue | blue = online / home, grey = away / offline |
| Next apps | your call | choose for the subject; ask if unsure |

## Type

- **Plus Jakarta Sans** for everything people read.
- **JetBrains Mono** only for machine values: an IP address, a version
  number, a model id, the `sudo` wordmark. Not for labels or counts.
- Scale:

| Use | Size | Weight |
| --- | --- | --- |
| Page title (h1) | `clamp(1.6rem, 6vw, 2.1rem)` | 750, tight tracking |
| Card / section title (h2) | `1rem` | 700 |
| Body | `.85–.9rem` | 400–650 |
| Secondary line | `.74–.78rem`, `--ink-3` | 400 |
| Microcopy, hints | `.7rem`, `--muted` | 400 |

- Sentence case everywhere. No ALL-CAPS labels in new work. No eyebrow
  label above every heading.
- Lines under ~70 characters.

## Shape, space, depth

- Radius: `--radius` (16 px) for controls and small surfaces,
  `--radius-lg` (24 px) for cards, `999px` for pills, tags and switches.
  Bigger things get rounder; don't use one radius for everything.
- Spacing comes in steps of 4: 4, 8, 12, 14, 18, 22 px. Cards pad 18 px.
- One shadow: `--shadow` (a faint purple glow). No grey drop shadows.
- Hierarchy comes from size, weight and space first, then colour, and
  borders last.

## Layout patterns

**Dashboard tab** (Chat, Channels, Apps, Settings): the shell gives the rail,
top bar and bottom nav. Content sits in one column, `max-width: 720px`,
with a `.view-heading` (h1 plus one-line `.lede`).

**App page** (Contacts at `/contacts.html`, Home at `/house.html`): its own
page, in *site mode* (`body.site-mode`). The dashboard chrome hides and the
app draws:

```
┌──────────────────────────────────────────────┐
│ sudo▌ HOME                     Dashboard ›    │  ← band in the app's colour
│ Your home                                     │
│ One line saying what this page is for.        │
└──────────────────────────────────────────────┘
   toolbar (status text · secondary action)
   content, max-width 880px, left-aligned
```

Reuse `.crm-site-header`, `.crm-band`, `.crm-brand`, `.crm-crumb`,
`.crm-site-title` and `.crm-site-body`, and set the band colour with one
modifier class. Register the route in `server.py`'s page list and in
`setTab()`'s path map, and add the tab to the `site-mode` toggle.

## Components to reuse

| Need | Use |
| --- | --- |
| The one main action in a form | `.primary-button` (full width in forms only) |
| Toolbar actions | `.btn-primary`, `.btn-secondary` |
| An on/off setting | `.access-row` with `.switch` (label left, switch right, hint in `small`) |
| Explaining something without clutter | `.info-btn` + `.info-bubble` |
| Grouped settings | `.setting-card` |
| Feedback after an action | `.form-status`, `#toast` via `toast()` |
| Small hints | `.microcopy` |
| Status / role markers | pills: `999px` radius, `.64–.7rem`, weight 700, tinted background |

## Icons

Inline SVG on a 24 × 24 grid: `fill: none; stroke: currentColor;
stroke-width: 2; stroke-linecap: round; stroke-linejoin: round`. This is the
style already used in the navigation. **No emoji as icons, and no unicode
symbols standing in for icons** (▯ ⌨ ⏻) in new work.

## Motion

- Views fade in with the existing `enter` animation (0.24 s). That is the
  page's one entrance.
- Motion answers people: a panel opening, a switch flipping, a row expanding.
- No staggered fade-ins per card, no hover lifts on every card.
- `prefers-reduced-motion` is respected (already handled globally).

## Writing

- Name things the way the person sees them: "Who's home", not "Presence";
  "Devices on your Wi-Fi", not "Network clients".
- Buttons say what happens: "Save", "Scan now", "Turn on". The result uses
  the same word ("Saved").
- Errors say what went wrong and what to do, plainly, without apologising.
- An empty state invites the next step ("Nobody yet. Tap Edit on someone's
  phone…").
- Where personal data is involved, say where it stays ("It all stays on
  this device") in one short line.
- Prefer structure to chains of `A · B · C`: a second line, or a status
  aligned to the end.

## Defaults to avoid

They make a screen look generated rather than designed:

- every block in an identical rounded card with the same shadow;
- gradient washes used as decoration;
- ALL-CAPS eyebrow labels, and `→` added to buttons;
- emoji or unicode glyphs as icons;
- green success states on Sudo's own screens (apps choose their own);
- monospace for labels and counts;
- hardcoded colours, outside fonts, CDN scripts.

## How to work on a screen

1. **Plan.** What is this screen's one job? What is the one thing on it
   worth remembering? Sketch it in a few lines of ASCII.
2. **Check the plan against this file.** Tokens, patterns, components,
   words.
3. **Build** with the existing classes first; add new ones only for what is
   new.
4. **Look at it.** Screenshot it at 375 px and on desktop, in one dark
   theme (`midnight`), and in Arabic. Fix what looks off, then look again.
   - Claude Code: use the built-in browser.
   - Claudia: `sudo-screenshot <url> /tmp/shot.png 390 844`.
5. **Ship** only once it reads as Sudo on all of those.
