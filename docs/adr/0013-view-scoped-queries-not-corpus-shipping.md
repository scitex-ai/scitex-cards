# ADR-0013 — The GUI asks for what it shows, not for everything

**Status:** PROPOSED (operator-directed 2026-07-18)
**Owner:** scitex-cards
**Cards:** `adr-view-scoped-queries-not-corpus-shipping-20260718`,
`graph-payload-11mb-board-30s-20260718` (closed — the acute fix)
**Depends on:** ADR-0010 (cards.db as single source of truth). The ordering in
this document is forced by that dependency, not chosen.

## Context

The operator's board took 30+ seconds and showed `loading…`. The acute cause
was a server-side re-parse and is fixed (post-write render 4.6 s → 0.027 s,
shipped). This ADR is about the shape that made it possible.

`GET /graph` returns **the entire corpus**: 1,978 cards, 11.3 MB of JSON,
every comment and note, on every load. The browser then does the filtering,
the search, the aggregates and the layout. Composition, measured:

| field | MB | share |
|---|---|---|
| comments | 4.84 | 41% |
| note | 1.90 | 16% |
| task | 0.65 | 6% |
| mermaid (top-level) | 2.19 | 19% |
| everything the list actually draws | <0.5 | ~4% |

My first proposal was to trim fields. The operator rejected it, correctly:

> 「場面にあった情報だけを送ればいい…人が見る情報なんて一部でしかない…1%
> もない…そうしないとスケールしなくないですか」

Trimming fields still ships all 1,978 cards. It buys a constant factor and
leaves the shape intact, so the same wall returns at 5,000 cards — later, and
with the fix already "done". That is a delay, not a repair.

## Decision

**A view requests its own slice. The server filters, searches, aggregates and
paginates. Cost becomes O(viewport), not O(corpus).**

- The timeline asks for its window; the wall asks for its page; the matrix
  asks for axes and counts, not rows.
- Each view receives only the fields it renders.
- Detail (full comments, note bodies) arrives when a card is opened, not
  before.
- `mermaid` is generated only when the Graph layout asks for it — it is 19% of
  the payload serving one of four layouts.

### The consequence that makes this real work

Filtering, search, sorting and the stats aggregates live in the client
**because** the client holds the whole corpus. Moving to slices moves those
server-side. That is the actual cost of this ADR and it should not be
understated.

On YAML that was effectively impossible: every query costs a full document
parse (**measured 4.6 s**), so "filter server-side" meant 4.6 s per keystroke.
The corpus-shipping design was a rational response to a store that could not
answer questions. On SQLite it is the natural shape — the mirror already
indexes `status`, `agent`, `assignee`, `scope`, `kind`, `blocker`, `project`,
`deadline`, `parent`, and filtered reads measured **14–85×** faster than the
YAML path.

**So the order is forced, not preferred: DB-canonical first, then per-view
endpoints.** Attempting per-view queries while YAML is canonical would produce
something slower than what exists today.

## What must not be lost

A field-usage audit (2026-07-18) found that `comments` — the obvious thing to
drop — has **five list-level consumers**, none of them the detail pane:

1. the matrix's occupancy-over-time replays `kind: "rescore"` entries across
   all nodes;
2. the timeline's Simple view renders the newest comment inline per card —
   that preview is the view's stated purpose;
3. a `💬 N` count badge per card;
4. `openDetail`'s creator fallback uses the EARLIEST comment's author for
   legacy cards predating `created_by`;
5. `postCommentFromDrawer` POSTs, then re-reads its own comment **out of the
   /graph payload** — drop it and the operator posts a comment and the thread
   visibly does not change, which reads as "the Post button is broken".

`note` is read by bulk copy-to-clipboard for any selected card **without the
drawer ever opening**, and `task` labels every node in the Graph layout.

Per-view endpoints must therefore serve, per card in list context: a comment
SUMMARY (`count`, `last {author, text}`, `first {author}`, `rescore_events[]`)
rather than the array; and must keep `note` and `task` available to the views
that use them. The audit is the input to the endpoint design — it does not
need repeating.

## Consequences

- Adding cards stops degrading the board. Today every new card taxes every
  view for every user, forever.
- The client stops being the query engine, which is also why search cannot
  currently be improved: it can only filter what was already shipped.
- Detail-on-open introduces a fetch where there was none, so the drawer needs
  a loading state and `postCommentFromDrawer` must be repointed at the
  per-card fetch **in the same change** — not after.
- `/graph` remains for the Graph layout, which genuinely wants the whole
  relation set; it stops being the board's default load path.

## Non-goals

- Not a rewrite of the board UI. The views keep their current behaviour; only
  where the data comes from changes.
- Not a change to the card model.
- Does not specify pagination ergonomics (cursor vs offset) — that belongs
  with the endpoint work, once the DB is canonical.

<!-- EOF -->
