# Project-scoped Cards board — slice 1 evidence (2026-09-17)

Card: `cards-gui-project-board-mvp-20260917`. Spec: Hub PR #923
(`docs/product/PRIVATE_BETA_LOGIN_TO_WOW.md` §5 "Cards" + §9 ownership matrix).
Branch `feat/gui-project-board-mvp-20260917` off `origin/develop` 234fa206.

## How this was produced

* Server: `python -m django scitex_cards_board --port 8124 --host 127.0.0.1
  --no-browser` from this worktree (`PYTHONPATH=<worktree>/src`), against the
  shared store, READ-ONLY — the page lists the standalone viewer's own projects
  and their cards. No schema, no service, no write.
* Browser: headless Chromium 153.0.8010.12, viewports 1440x900 and 390x844.
* Caveat: `DJANGO_DEBUG` defaults to true on this dev server, so this is a local
  baseline, not a production capture (`hub-screenshots-are-taken-with-debug-1-not-production-20260816`).

## The four states photographed, and the two that are only unit-tested

Every shot below carries the page's own `data-stx-state` marker, printed by the
capture script and asserted by the browser check:

| shot | state | what it proves |
| --- | --- | --- |
| `*-01-no-project` | `no-project` | nothing selected: the page asks with the picker instead of guessing |
| `*-02-project-ready` | `ready` | one project's cards, grouped by canonical status (1 card / `blocked`) |
| `*-03-filtered-empty` | `filtered-empty` | a filter that matches nothing is NOT reported as an empty board |
| `*-04-denied` | `denied` | a project id the viewer cannot open: 403, no rows, no fallback to another project |

`empty` and `unavailable` have no screenshot and that is said rather than faked:
`empty` needs a host provider that lists a project the store has no rows for, and
`unavailable` needs a broken store. Both are covered by tests
(`test_empty_state_when_the_project_is_accessible_and_has_no_cards`,
`test_unavailable_state_when_the_store_cannot_be_read`).

## Measured, both viewports

* `document.scrollWidth == innerWidth` at 1440 and at 390 — no horizontal overflow.
* Zero interactive controls clipped by the right edge at either width
  (`capture-project-board.json`), which is the deliberate contrast with the fleet
  board's header at 390px (`gui-mobile-header-controls-offscreen-20260917`).
* Mobile tap targets, measured after the fix in this branch:
  `44, 44, 44, 44, 44, 58, 44` px for select/Open/search/status/assignee/Apply,
  i.e. nothing under the 44px floor. Before the fix the picker's Open button
  rendered ~30px and Apply ~28px (read off the first mobile capture).

## Tenant isolation, in the tests rather than only in prose

`tests/scitex_cards/_django/test__project_board_tenancy.py` — 30 tests, hermetic
(injected loader, no database):

* A/B: alice does not see `proj-beta` in her picker; **and** she does not see
  dana's card inside her OWN project (project tenancy composes with the row
  predicate instead of replacing it).
* Naming an inaccessible project is `denied` and does **not** fall back to the
  last-visited project (scitex-ui's precedence resolves it to None; the page must
  not quietly show a different project).
* Fail closed: a viewer the boundary cannot name sees nothing at all — not even an
  empty project list.
* Staff bypass preserved (the operator's fleet board control).
* Every state, every filter, the 403/503/200 contract, both URL spellings, and the
  SDK-picker-instead-of-a-twin case.

## Store cost inherited by this page (recorded, not worked around)

`GET /projects` answered **72.7s cold and 32.7s warm** for a viewer with 9
projects. The page's own work is trivial next to the store read it inherits —
the same class as the 24MB/91.8s `/graph` measurement recorded on
`graph-payload-11mb-board-30s-20260718`. A project board an ordinary user must
wait a minute for is a store-read problem, and it is not hidden inside this
template.
