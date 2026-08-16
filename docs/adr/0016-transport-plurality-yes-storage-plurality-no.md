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

## AMENDMENT, 2026-07-30 — the operator revisited the ruling

This ADR said a Postgres adapter would need the operator to revisit the
2026-07-20 hard-mode ruling. **They have.** Verbatim, quoting their own earlier
instruction back and then answering it:

> 「例外を用意しないでください。甘くせずにハードに切り替えてください。曖昧にすると
> バグが残ります。他のエージェントも迷ってしまいます。唯一の方法だけソースコードに
> 含めてください。」
>
> これは私の指示が悪かったと思います。
>
> sqlite と postgres を対応させてください、可能ですか？

So the title of this ADR is now **half wrong**, and the honest thing is to
amend rather than quietly rewrite: storage plurality is permitted. What follows
is the shape it is permitted in, and why that shape does not reopen the hole the
original ruling closed.

### The requirement changed, not the reasoning

The RULE was too strong. The REASON was correct — ambiguity leaves bugs and
confuses other agents — and it survives intact below.

### scitex-db's correction: the cause was peer stores, not two engines

Credit where it belongs. This ADR (and the author) had been carrying "two
backends caused the board wipes". scitex-db read `_store_backend.py` and found
the narrower, more useful mechanism:

> reconciling two stores treated as PEERS, where absence in one is interpreted
> as deletion in the other.

That is what turned a 5-row temporary YAML into a replacement for 2,159 live
rows. The distinction is load-bearing: the old framing forbids a harmless thing
(two engines existing) while permitting the lethal one (a sync path that reads
absence as delete). The corrected framing forbids the lethal one and permits the
harmless one.

**So the invariant that survives verbatim, with no exceptions, is:**

> **No code may delete a row because it is absent from another store.**

This is also what `_assert_no_shrink` enforces from the other side. The two
guards now have one stated reason instead of two folk ones.

### The permitted shape

1. **One authoritative store per deployment**, never two at once. Default
   SQLite: single-host, zero-config, no daemon.
2. **Selected by a deploy-time config value read once at startup — NOT an env
   var.** `_store_backend.py`'s own reasoning applies: an env var leaks
   ambiently across every write in a process, which is exactly why
   `allow_shrink` is keyword-only. (scitex-db caught the author about to
   specify an env var after arguing against them.)
3. **No sync path, ever.** Nothing keeps SQLite and Postgres in agreement. The
   only crossing is a one-way migration that runs once and finishes.
4. **No implicit fallback.** A configured-but-unreachable Postgres FAILS the
   process; it must not fall back to SQLite. scitex-db's framing, which is
   better than the author's: *a fallback does not merely read the wrong
   database, it makes the wrong database WRITABLE* — and thereby manufactures
   the second-authoritative-store condition the ruling exists to prevent.
   Downtime is recoverable by restarting; a week of cards written into a store
   nobody is reading is not. Precedent: `_backend_http.py:10-13`,
   "FAIL-LOUD, NEVER FALL BACK".
5. **An unmarked destination is UNUSABLE, not "probably fine."** The migration
   writes its completion marker LAST; tool and adapter both refuse a
   destination without it. A half-copied Postgres an adapter happily connects
   to is the fallback bug with extra steps.

Under this shape "both supported" and "exactly one authoritative store" are
simultaneously true, and the operator's worry — agents not knowing which world
they are in — has one answer per process, fixed at deploy time.

### Ordering is not negotiable

Row-level writes with the `revision` optimistic lock come FIRST
(`cards-row-level-writes-with-revision-lock-20260730`). `_store_canonical_read.
py:31` still reads the ENTIRE store to change one field; measured 2026-07-30,
appending ONE comment showed as `task_comments` +2 −1 — existing rows deleted
and reinserted. Over a network that is not merely slow: it widens the window in
which a crash leaves rows deleted and not yet reinserted. The operator proposed
this ordering themselves.

### The migration must refuse to run while any writer is live

Discovered by scitex-db at the boundary between the lock and the migration, and
decided here because the lock is this package's.

Preserving `revision` across the copy is necessary — it is user-visible causal
state and belongs in the checksummed column set, not treated as backend
bookkeeping. But it is **not sufficient**. A writer that read `revision=5` from
SQLite and writes to Postgres after cutover finds `revision=5` and SUCCEEDS —
its lock is satisfied, yet it computed against a read from a different store,
and any write that landed in SQLite after the copy point is silently gone.
Preserving `revision` makes the lock FUNCTION; it does not make it MEAN
anything across a store swap, because the lock's premise ("nothing changed under
me since I read") is what the swap violates.

Therefore quiescence is not an optimisation, it is the only thing that makes the
premise hold across the boundary.

**And the tempting detector must be refused.** "Has anything been written
recently?" is a heuristic, and a bad one here: measured 15.5 row-deltas/min of
ordinary traffic, so a quiet 30 seconds proves nothing and a busy one proves
only that somebody wrote. It passes exactly when it is least safe. The gate must
be a state the WRITE PATH consults and fails closed on.

Stated limit, so nobody builds against a promise: a gate inside the package is
honoured only by processes running a version that HAS it. Measured 2026-07-30 —
one agent resident on 0.17.5, another carrying `scitex_cards` 0.13.5, and the
operator's own board process running code 57 minutes older than what was
installed on disk. So for the FIRST migration the real mechanism is the operator
quiescing the fleet; the in-package gate is what makes later ones unattended-safe.
Do not call the migration safe-to-run-unattended until both exist.

## Note on method

The audit's reading was the conclusion that suited every party at the table, and
it took an agent arguing *against its own proposal's easy win* to check it. The
citations above are all verifiable in this repository, and were verified here
before acceptance rather than taken on authority. If `_store_backend.py:32-38`
ever says something other than what is quoted, this ADR is wrong and should be
revised.
