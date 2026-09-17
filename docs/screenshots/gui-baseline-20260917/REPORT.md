# Cards UI baseline — desktop 1440x900 and mobile 390x844 (2026-09-17)

Card: `gui-baseline-scitex-cards-20260917`. Agent: `scitex-cards-gui`.
Revision under test: **e9c076ee** (`fix/spa-accent-literals-to-scitex-ui-tokens`),
served from the dedicated worktree `.worktrees/gui-responsive-baseline`.

## How this was produced (so the numbers can be re-run)

* Server: `python -m django scitex_cards_board --port 8123 --host 127.0.0.1 --no-browser`
  from the worktree, with `PYTHONPATH=<worktree>/src`, against the shared store
  `$SCITEX_STORE_DSN` (read-only; no schema or service write).
* Browser: Chromium 153.0.8010.12 (headless, `playwright install chromium`,
  browsers cached under `/uvwork/ms-playwright`), viewports 1440x900 and 390x844.
* Theme: switched the SUPPORTED way — `localStorage['stx-theme'] = 'light'` then
  reload, so `scitex_ui/_theme_boot.html` sets `data-theme` and the legacy
  `.dark-theme`/`.light-theme` classes on `<html>` **and** `<body>`. Forcing
  `data-theme` alone is a weaker test and was NOT relied on.
* Served-bundle identity: the page's `assets/index.css` hashes identically
  (`a4f1f30e22f84f9f…`) in the worktree file and over HTTP from the server, so the
  screenshots are of this revision and not of a stale resident board.

Caveats, stated rather than implied:

* The dev server ran with the default `DJANGO_DEBUG=true` (Django `runserver`),
  which is what serves static files without `collectstatic`. The
  `hub-screenshots-are-taken-with-debug-1-not-production-20260816` card is about
  exactly this; the artefacts here are a local baseline, not a production capture.
* The store was reachable but SLOW (see "Not baselined" below), so the board's
  data-dependent surfaces rendered their loading state in every shot.

## Evidence

| artefact | what it shows |
| --- | --- |
| `desktop-1440x900-board-dark.png` | desktop, dark theme, board (Wall) |
| `desktop-1440x900-matrix-dark.png` | desktop, Matrix layout |
| `desktop-1440x900-search-dark.png` | desktop, `project:` search applied |
| `mobile-390x844-board-dark.png` | mobile, dark theme, board |
| `mobile-390x844-timeline-dark.png` | mobile, Timeline layout |
| `mobile-390x844-header-crop-dark.png` | mobile header, dark, 2x crop |
| `desktop-1440x900-board-LIGHT.png` | desktop, LIGHT theme via stored preference |
| `mobile-390x844-board-LIGHT.png` | mobile, LIGHT theme via stored preference |
| `desktop-1440x900-board-static-dark.png` | desktop dark reference for the comparison |
| `mobile-390x844-LIGHT-header-crop.png` | mobile header, LIGHT, 2x crop |
| `theme-measurements.json` | computed background/colour per element, dark vs light |
| `contrast-ratios.json` | WCAG contrast ratios derived from those measurements |
| `capture-dark.json` | first-pass capture: overflow probe + console log per shot |

## FINDING 1 — the light theme is a hybrid: the board's chrome keeps dark surfaces while its text follows the shell theme

Reproduction: open `/` (also `/board`, `/board-v3`), select the light theme through
the shell's own preference, reload. Expected one theme's surfaces and text; actual
a page that mixes both, at BOTH viewports.

Scope of the claim, stated precisely: the shell's default is DARK
(`data-theme-default="dark"`), and in the dark theme every measured pair is >= 5:1,
so this is latent for a visitor who never selects light and immediate for one who
does. Whether the operator's Hub session resolves to light is NOT measured here —
what is measured is that light produces the exact symptom they named.

Measured (headless Chromium, computed styles, desktop 1440x900 — mobile identical):

| element | dark theme | light theme |
| --- | --- | --- |
| `body` | bg `rgb(13,17,23)` fg `rgb(230,237,243)` | bg `rgb(13,17,23)` fg `rgb(51,51,51)` |
| header bar | bg `rgb(33,38,45)` fg `rgb(230,237,243)` → 12.88:1 | bg `rgb(33,38,45)` fg `rgb(51,51,51)` → **1.20:1** |
| legend strip | bg `rgb(33,38,45)` → 12.88:1 | bg `rgb(33,38,45)` → **1.20:1** |
| right details panel | bg `rgb(33,38,45)` → 12.88:1 | bg `rgb(33,38,45)` → **1.20:1** |

Per-element text scan in the light theme found **5 elements of real text below the
4.5:1 floor**, in BOTH directions of the mix:

| text | fg | bg | contrast |
| --- | --- | --- | --- |
| `📊 Details` / `📊` (sidebar section head) | `rgb(224,230,240)` | `rgb(239,238,237)` | 1.08:1 |
| `SciTeX Cards` (the page title in the header band) | `rgb(51,51,51)` | `rgb(33,38,45)` | 1.20:1 |
| `›` (details collapse toggle) | `rgb(138,150,168)` | `rgb(239,238,237)` | 2.59:1 |
| `loading…` (canvas status) | `rgb(138,150,168)` | `rgb(245,244,242)` | 2.73:1 |

The page title is the worst case and it is the leaf-header text the operator
reported on: dark grey on a dark band, 1.20:1.

Mechanism, read from the tree rather than inferred:

* `static/scitex_cards/page-header.css` declares the split in its own header comment
  (line 15): the file owns shared *geometry* while "Every page keeps its own PALETTE
  (background, border, …)". Its rules then take the header FOREGROUND from shell
  tokens — `color: var(--stx-cards-header-fg, var(--text-primary))` (line 133),
  `var(--stx-cards-header-dim, var(--text-muted))` (lines 140, 148) — which is
  exactly what a theme switch changes.
* The board's own stylesheets keep the dark surfaces and have **no theme awareness
  at all**: `grep -l data-theme static/scitex_cards/board_v3/*.css` matches nothing
  (only the React SPA bundle `assets/index.css` and `ui/emoji_picker.css` carry
  `data-theme` rules), and `01-filterbar.css` still hardcodes `.filt { background:
  #232a36 }` and `.filterbar h1 { color: #fff }`.

So the same defect class the accent migration fixed for the SPA stylesheet
(`fix/spa-accent-literals-to-scitex-ui-tokens`) is still open on the page the
operator actually opens at `/`: token-consuming foregrounds wired to hardcoded
surfaces. That is a hybrid by construction, not a missing CSS variable.

## FINDING 2 — at 390x844 the header's own controls start off-screen

Measured at `innerWidth = 390` (`media (max-width:768px)` is active):

* `.filterbar.stx-cards-headerbar` computes `flex-wrap: nowrap`, `overflow-x: auto`,
  `clientWidth = 390`, **`scrollWidth = 606`**.
* `#f-search` computes `flex: 1 1 100%` (from `05-responsive.css`, whose own comment
  says "search wraps to its own row at ~100% width ... Restored 2026-06-12") but
  renders at **x = 330..590, width 260** — only ~60 px of the field is on screen and
  the placeholder is clipped mid-word ("Sear").
* The ACTIVE layout button `#f-layout-timeline` renders at **x = 374..443**: the
  control that switches Wall/Timeline/Graph/Matrix on a phone is off-screen at the
  default scroll position, reachable only by discovering a sideways swipe inside the
  header.
* The wrap cannot fire where the design put it: `#f-search` sits in
  `#f-search-wrap` inside `div.fb-center.stx-cards-filterbar__group`
  (`flex-wrap: wrap`) — but that group is itself a *shrunken 260 px flex item* in the
  nowrap bar, so the group's own `flex-wrap` has nothing to wrap.
* No page-level horizontal scroll (`docScrollWidth == 390`), so nothing is
  unreachable; the defect is that primary controls are invisible at rest.

## Not baselined, and why (measured, not skipped)

Task browsing on real cards, filtering against real results, prioritization
(Matrix/BLOCKING-YOU), history (Timeline/Recent) and card-level accessibility could
NOT be baselined in this run:

* `GET /graph` returned **23,979,978 bytes in 91.8 s** on this store (measured with
  `curl`). The board's whole first paint waits on that one response.
* A data-aware capture (`wait_for_function` on `.card`, 200 s budget) captured **zero
  cards** at desktop and logged 13 `net::ERR_NETWORK_CHANGED` console errors while
  the 24 MB response was in flight — the container's network flapped during the wait.
* While that happens the canvas renders a bare `loading…` with no progress, no
  partial content, no retry and no error affordance — i.e. for ~90 s the page is
  indistinguishable from a hung one. The structural fix is already carded
  (`cards-board-graph-payload-slim-20260710`); this run only adds a fresh
  measurement (24 MB / 91.8 s) to it.

Reported separately, NOT fixed here (no code change is in this slice):

* FINDING 1 is a GUI CSS defect on `/` but it touches `page-header.css`, shared with
  the DM page, so it belongs with the leaf/theme card
  `cards-leaf-header-theme-mobile-performance-20260916` rather than in a
  baseline-only change.
* FINDING 2 is a one-file responsive fix (`05-responsive.css` /
  `page-header.css`) and wants its own bounded card.
* A leftover of the accent migration: `rgba(155, 127, 214, α)` — the same accent,
  spelled numerically — survives in 12 places in `frontend/src/styles/board.css`
  (lines 380, 381, 396, 1558, 1566, 1581, 1594, 1601, 1674, 1702, 1767, 1803) and
  once in `search-suggest.css`. The guard added by `e9c076ee` scans for the accent
  *hex* literals, so it cannot see them; they keep the dark theme's lavender under
  the light theme, which is the same "mixed surfaces" symptom.
