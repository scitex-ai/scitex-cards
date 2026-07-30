# ADR-0016 — Many ways to reach the store, exactly one store

**Status:** accepted, 2026-07-30
**Supersedes nothing. Constrains:** every future multi-host and PostgreSQL proposal.

## The one-sentence rule

**Transport plurality is allowed. Storage plurality is not.** Two classes that
*reach* the same database are fine; two implementations that *are* a database
are not.

## Context

The store is one SQLite file (`$SCITEX_CARDS_DB`, ~46 MB, ~2,860 cards) written
by roughly 90 fleet agents. Three needs collided:

1. The board is unusable from another machine — the public deployment at
   `scitex.ai/apps/cards/` renders, authenticates, and is empty, because its
   container mounts `/app/.scitex` as an empty named volume.
2. Every write is a whole-document read-modify-write
   (`_store_canonical_read.py:31`), median **368 ms**, and concurrent writers
   lose each other's rows wholesale.
3. PostgreSQL was proposed as the multi-host answer.

(3) ran into a standing ruling. Three board wipes on 2026-07-19/21 — one where a
5-row temporary YAML replaced **2,159 live rows** — led the operator to order:

> 「例外を用意しないでください。甘くせずにハードに切り替えてください。曖昧に
> するとバグが残ります。他のエージェントも迷ってしまいます。唯一の方法だけ
> **ソースコード**に含めてください。」

Backend selection was then deleted outright in `b63ffd42`
(*"SQLite is the only store — delete the backend switch"*).

## The question that had to be settled first

Does that ruling constrain the **source** (only one storage implementation may
exist in the tree) or only the **runtime** (only one reachable per deployment)?

It decides whether a PostgreSQL adapter is admissible at all.

An external audit commissioned by the operator read it as runtime-only, and
called the source-scope reading over-fitting. That reading was **convenient for
everyone**: it dissolved the blocker and blessed the proposals on the table.
scitex-agent-container ran an independent source-and-history read and found the
opposite. Their evidence, which I verified rather than accepting:

| Claim | Verified |
|---|---|
| `_store_backend.py:3` states two clauses: *"no other backend"* **and** *"no way to select one"* | yes |
| `_store_backend.py:32-38` **explicitly considers and rejects** the runtime-only reading: *"Flipping the DEFAULT would not have been enough… it leaves the other world supported, reachable, and reviewed by nobody."* A default-off switch already satisfies "one reachable per deployment" — the file calls that insufficient | yes |
| Enforced as a literal source scan: `tests/scitex_cards/test__store_read_sqlite.py` walks `SRC.rglob("*.py")` asserting banned env names appear nowhere in `src` | yes, line 68 |
| `_health_write_target.py` flags a leftover env var *even though nothing reads it* — unreachability is explicitly not compliance | yes |
| The deletion was `b63ffd42` via PR **#535**, not #545 | yes — `_store_backend.py:17` misattributes it, and #545 is an unrelated branch |

**So the ruling is source-scoped.** The audit's correction of that point is
wrong, and my original instinct was closer to right.

## The distinction that makes progress possible

`LocalBackend` (`_backend.py:89`) and `HubBackend` (`_backend_http.py:134`)
already coexist in this source **without violating anything**, and the file says
why:

> FAIL-LOUD, NEVER FALL BACK. There is no local-store path anywhere in this
> module: a hub that cannot be reached raises with a hint, because a silent
> local fallback **would mint the separate store copy the one-database ruling
> forbids.**

The forbidden object is a second **store or copy**, in any spelling. Two ways to
*reach* one store are a transport seam. That is why the pair is legal while a
`_STORE_BACKEND` env var was not.

## Decision

1. **Multi-host is solved by the transport seam, not by a second database.**
   One process owns the file; everyone else speaks to it. `HubBackend` is that
   client and it already exists — this is finishing a seam, not adding a
   backend.
2. **A PostgreSQL adapter is not admissible under the current ruling**, because
   after any migration both storage implementations still ship in the source and
   remain selectable there. Adopting one requires the operator to *revisit*
   ADR-scope explicitly — not an inference from "one live store per deployment".
3. **Any transport must fail loud with no fallback.** A silent local fallback
   mints the second copy. This is the property that makes transport plurality
   safe, and it is not optional.
4. **Row-level writes come first**, before any transport or storage work. Three
   independent reviewers converged on this. The decisive reason is the audit's:
   the 368 ms is an artifact of the write model, so **no capacity conclusion
   about SQLite can be drawn until it is gone** — a load test run today measures
   the wrong thing.

## Consequences

**The no-shrink guard must be transferred, not deleted.** `_assert_no_shrink`
(`_store_backend.py:69`, invariant at `:78-79` — *"a written card never
disappears. Not 'not too many' — NONE"*) works by comparing an outgoing whole
document's row set against the table. Row-level writes remove the document, so
the guard silently stops meaning anything. Its safety moves to: `card_id`
uniqueness, `revision` optimistic locking (`WHERE card_id=? AND revision=?`,
affected-count must be 1 — a bare row-level `UPDATE` still loses updates on the
same field), short transactions, request-id idempotency, physical `DELETE`
forbidden, `deleted_at` tombstones.

**scitex-db is a floor, not a ceiling.** It already ships both
`_sqlite3/_SQLite3.py` and `_postgresql/_PostgreSQL.py`, and `scitex-io` already
bridges to it — `.db` registers a **loader** (`_optional_providers.py:173`)
returning `scitex_db.SQLite3(path, **kwargs)`, with **no saver**, which is
correct: you write through the handle. But scitex-db has zero files matching
`repositor`, `revision`, `optimistic` or `upsert` — its mixins are SQL mechanics
(Row, Table, Query, Index, Blob, Maintenance). It is the audit's bottom two
layers and none of the top one. Use it for adapters and connection/transaction
mechanics; keep domain verbs in each package; lift the concurrency primitives
into it once one package has them working, since those *are* the dialect
differences it should own.

**A neighbouring package shows what "row-level" alone does not buy.** sac's
state-db already writes row-level and its `db_import` merges with
`INSERT OR IGNORE` rather than reconciling to identical — so it cannot reproduce
the 2,159-row wipe. But it has no `revision` column anywhere, so two writers to
the same field still lose one silently, and `INSERT OR IGNORE` means an import
never updates: a stale row wins by being first, and the import reports success.
Row-level writes remove one failure class and leave the other; both halves are
needed.

## Note on method

The audit's reading was the conclusion that suited every party at the table, and
it took an agent arguing *against its own proposal's easy win* to check it. The
citations above are all verifiable in this repository, and were verified here
before acceptance rather than taken on authority. If `_store_backend.py:32-38`
ever says something other than what is quoted, this ADR is wrong and should be
revised.
