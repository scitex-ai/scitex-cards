# SQLite eradication — what may be deleted, and what must not

Operator ruling, 2026-08-17:

> スクライトはバグの元になるので全部消してください。すなわちソースコードからスクライトを
> 使っているところは全部削除してポストグラスへ移行しなければいけないですし、ローカルに
> 使っているスクライトがあればそれはアーカイブにしてあなたに作られないことを確認して
> ください。テストももちろん同じで関係しているところ全部直してください。

Remove SQLite from source entirely, migrate to PostgreSQL, archive local SQLite
files and **confirm they cannot be recreated**, and fix the tests too.

## The trap this document exists to prevent

Most SQLite vocabulary in this package is the **abolition guard** — code whose
only job is to NAME SQLite in order to REFUSE it. `reject_attempted_dsn`,
`refuse_zero_config_default`, `is_attempted_dsn`, `test__no_silent_sqlite_fallback`.
A keyword sweep would delete the very mechanism that prevents recreation. A
prior sweep on this repo already made this class of error 37 times: *a
mechanical rename leaves no mechanical trace — it produces code that reads
correctly and means less.*

So nothing here is classified by filename or by grep hit. Every row below is
classified by **what the code does with the driver**, measured with an AST walk
that separates four distinct uses:

| use | can it create a database? |
|---|---|
| `sqlite3.connect(bare_path)` | **YES** — creates the file, and `_db`/`_index` also `mkdir(parents=True)` first |
| `sqlite3.connect("file:…?mode=ro", uri=True)` | no — raises if the file is absent |
| `sqlite3.Connection` / `sqlite3.Row` in an annotation | no — every module here has `from __future__ import annotations`, so the annotation is a lazily-evaluated string that is never evaluated |
| `except sqlite3.Error` / `sqlite3.OperationalError` | no |

**The single most important measurement: there are ZERO `isinstance(..., sqlite3.…)`
checks in `src/`.** No guard in this package uses the driver to *detect* SQLite.
`_store_url.py` — which holds `is_attempted_dsn`, `reject_attempted_dsn`,
`backend_of` and every refusal in the package — does not contain the token
`sqlite3` at all. It recognises a SQLite-shaped target from a **string**.

That asymmetry is what makes this migration safe: driving `import sqlite3`
toward zero removes the ability to create a database **without weakening a
single refusal.**

## Method

- `src/scitex_cards/**/*.py` parsed with `ast`; `sqlite3.<attr>` uses bucketed by
  syntactic position (argument annotation / return annotation / `except` handler
  / call / `isinstance`).
- 38 files contain the token `sqlite3`: **30 bind the driver**, 8 mention it only
  in comments or docstrings.
- `_store_url.py` is a 39th relevant file that does not contain the token at all.

---

## Bucket A — LIVE BACKEND. Can open or create SQLite in a running deployment. MUST GO.

**6 files.**

### A1. Create-capable doors — the three places a database is manufactured

| file:line | evidence |
|---|---|
| `src/scitex_cards/_backend_connect.py:186` | `raw = sqlite3.connect(uri, uri=read_only)` — with `read_only=False` the uri is a bare `str(target)`, so this **creates**. The seam door. |
| `src/scitex_cards/_db.py:364-365` | `p.parent.mkdir(parents=True, exist_ok=True)` then `sqlite3.connect(str(p))` — creates the store **and its parent directories**. The main store door. |
| `src/scitex_cards/_index.py:66-67` | `target.parent.mkdir(parents=True, exist_ok=True)` then `sqlite3.connect(str(target))`. |

`_index.py` deserves separate attention: it is **not the cards store**. It is a
derived FTS/search index at `~/.scitex/card/.tasks.index.sqlite` (note the
singular `card/` — a second, latent defect). It is a genuinely independent
SQLite database with its own schema version and rebuild CLI
(`src/scitex_cards/_cli/_index.py:76`). Eradicating it is a separate decision
from eradicating the store, and it is the one place in this list where "migrate
to PostgreSQL" may not be the right answer — a derived cache could equally be
deleted outright.

### A2. The live SQLite inbox backend

Still selected at runtime. `src/scitex_cards/_inbox.py` dispatches three ways at
lines 330, 441 and 502 — PostgreSQL first, then `_use_sqlite()`, then YAML —
and `_inbox.py:20` still documents "the default is SQLite".

| file | role |
|---|---|
| `src/scitex_cards/_inbox_sqlite.py` | the backend module. Its own `sqlite3` use is annotation-only (`:68`), but the module **is** the SQLite rail |
| `src/scitex_cards/_inbox_sqlite_schema.py:26` | DDL + `open_connection` |
| `src/scitex_cards/_inbox_receipt.py:59` | receipt rows on that rail |

**Removal hazard, measured:** `src/scitex_cards/_health_stranded_backlog.py:54`
does `from ._inbox_sqlite import inbox_db_path`. Deleting `_inbox_sqlite` breaks
the very health check that detects notifications stranded in a legacy SQLite
inbox — and §Phase 2 below shows 149 such rows exist right now.

The extraction is cheaper than it looks: `inbox_db_path` is not defined in
`_inbox_sqlite` at all. It lives in `_inbox_sqlite_schema.py:97` (beside
`inbox_target` at `:60`) and is merely re-exported. So the health probe can be
repointed at `_inbox_sqlite_schema` in one line, before the backend module goes
— it is a *path* helper, not a driver user. Other importers that would strand:
`_dm/receipt_state.py:173`, `_health_backend_mode.py:132`, `_cli/_inbox.py:99,176`.

---

## Bucket B — ABOLITION GUARD. Names SQLite only to refuse it. KEEP THE REFUSAL.

**2 files. Neither imports the driver.**

| file | the refusal it holds |
|---|---|
| `src/scitex_cards/_store_url.py` | `is_attempted_dsn:185`, `reject_attempted_dsn:237`, `backend_of:146`, `BACKEND_SQLITE:53`. **Does not contain the token `sqlite3`.** Recognises a malformed DSN from a string and refuses to open it as a file — the fix for three separate incidents (2026-07-31, 2026-08-02, 2026-08-12) in which a mangled DSN became a real, empty, query-answering cards database. |
| `src/scitex_cards/_store_target.py` | `refuse_zero_config_default:163`, `StoreTargetNotConfigured:144`, `StoreTargetIsNotAPath:73`, `require_db_path:272`. The zero-config SQLite default tier was **abolished 2026-08-13**; `resolve_store_target` now ends in a raise. Mentions `sqlite3` once, in prose. |

These two are the reason the eradication is tractable. They already work without
the driver, so every `import sqlite3` in the package can go without touching them.

`tests/scitex_cards/test__no_sqlite3_import_in_src.py::test_the_abolition_guard_itself_needs_no_driver`
pins this: if `_store_url` ever grows an `import sqlite3`, the refusal machinery
would itself become capable of the thing it refuses.

---

## Bucket C — LEGACY READER. Reads the retired store or migrates off it.

**9 files. Every one opens `mode=ro`, so none can create a database.**

| file:line | purpose |
|---|---|
| `_channel_rail.py:304` | read-only probe of the SQLite rail |
| `_dual_write.py:199` | identity probe of the legacy store |
| `_health_store.py:40` | health probe (`SELECT COUNT(*) FROM tasks`) |
| `_health_store_identity.py:179` | `store_uuid` probe |
| `_health_stranded_backlog.py:68,88` | counts rows stranded in a legacy inbox |
| `_inbox_migrate_postgres.py:119` | reads the legacy inbox to migrate it **into** PostgreSQL |
| `_store_canonical_read.py:271` | retirement check on the legacy store |
| `_store_uuid.py:298` | `store_uuid` reader |
| `_db_dm_schema.py` | catches `sqlite3.OperationalError` from a DM schema probe (no connect) |

These are the bucket whose fate depends on the Phase 2 measurement below.

---

## Bucket D — DEAD / vestigial vocabulary.

**22 files.**

### D1. Annotation-only — 15 files, stripped in this PR

The driver was imported solely to write `sqlite3.Connection` / `sqlite3.Row` in
a signature. Since every one of these modules has `from __future__ import
annotations`, the annotation was a string that is never evaluated — the import
bought nothing at runtime, and the annotation was **actively wrong**: on a
PostgreSQL deployment `_db.connect()` returns a `StoreConnection`, not a
`sqlite3.Connection`, so every one of these signatures was documenting a type
the caller does not receive.

`_db_bootstrap.py`, `_db_export.py`, `_db_freshness.py`, `_db_init_schema.py`,
`_db_migrations.py`, `_db_mirror.py`, `_inbox_migrate.py`, `_min_client_version.py`,
`_mirror_hashes.py`, `_mirror_rows.py`, `_dm/migrate.py`, `_dm/read.py`,
`_dm/receipt_state.py`, `_dm/write.py`, `_dm/write_rows.py`.

Retyped to `StoreConnection` under `if TYPE_CHECKING:` (zero runtime import), and
`sqlite3.Row` → `Mapping[str, Any]` in `_dm/read.py`, whose `row_to_message`
accesses rows purely by name (`row.keys()`, `row[k]`) and so is already correct
against psycopg's `dict_row`.

**One caveat, stated rather than glossed.** While the bucket-A1 doors still
exist, `_db.connect()` can still return a raw `sqlite3.Connection`, so a
function such as `enforce_min_client_version` may at runtime receive either
type. The new `StoreConnection` annotation therefore describes the **target
state**, not today's full runtime range — it becomes exactly true when A1 lands.
It was chosen over a `sqlite3.Connection | StoreConnection` union deliberately:
the union would reintroduce the import this PR removes, which is the whole point
of the change. The previous annotation was not more honest — it named *only* the
SQLite type and was already wrong on every PostgreSQL deployment, which is the
one the fleet actually runs.

### D2. Comment-only — 7 files, nothing to do

`_cli/_db.py:58`, `_db_foreign_keys.py:137`, `_ddl.py:5,186`,
`_inbox_postgres.py:28`, `_schema_probe.py:16,101`, `_store_backend.py:59`,
`_store_tx.py:60`.

**Do not "clean" these.** Every one is a comment explaining why the code avoids a
SQLite assumption — e.g. `_cli/_db.py:58` "POSITIONAL INDEXING IS NOT PORTABLE
HERE. `sqlite3.Row` supports both…". They are port machinery documentation. The
prose names SQLite precisely because the code must not assume it.

---

## Counts

| bucket | src files | disposition |
|---|---|---|
| A — live backend | 6 | must go; behavioural change, **not in this PR** |
| B — abolition guard | 2 | keep, and pin that they need no driver |
| C — legacy reader | 9 | keep pending Phase 2; see verdict below |
| D1 — annotation-only | 15 | **stripped in this PR** |
| D2 — comment-only | 7 | leave alone |

`import sqlite3` in `src/scitex_cards`: **30 files before → 15 after.**

---

## Phase 2 — is dropping the legacy reader lossless?

### Verdict: **CANNOT PROVE.** Therefore bucket C is NOT dropped.

The retired predecessor store `store_uuid = 0bb1395b-6f19-4a2d-9782-7dd4d296f2a0`
**could not be located, and appears not to exist as a file on this host.**

- 172 files across `/home/ywatanabe/` and `/home/agent/` reference that uuid
  string; magic-byte checked, **0 of the 172 are SQLite databases**. They are
  `.csv` / `.tsv` / `.json` / `.md` provenance records and transcripts.
- A magic-byte walk for extensionless SQLite files over `~/.scitex`, `~/.old`,
  `/home/agent/.scitex`, the repo and `~/.local/share` found 16 hits, all
  Chromium profile databases from a Playwright temp profile.
- The only two SQLite files carrying the **cards schema** are the phantoms
  archived on 2026-08-17 under
  `/home/ywatanabe/.old/20260817T231000Z-phantom-sqlite-dsn-paths/` — full
  15-table schema, **0 rows in every table**, no `store_uuid`.
- `/home/agent/.scitex/cards/` and `/home/ywatanabe/.scitex/cards/` are the
  **same directory** (identical dev:inode), a bind mount — not a second candidate.

So the retired store's declared contract — *"refused for writes, not destroyed,
and must still answer reads"* — **is not satisfiable on this host.** There is
nothing left to answer a read. That is the headline finding, and it is a
different thing from "the data is safe": it means the guarantee is already
unmet, not that it was discharged.

Losslessness cannot be certified because there is no store to diff.

### Live PostgreSQL, for the record

`postgresql://scitex_cards@127.0.0.1:55432/scitex_cards`, read via psycopg 3.3.4,
snapshot `2026-08-17 23:32:31+00`:

`store_uuid = 1d55dd6e-3d2a-4c24-a429-a78835ab988f`,
`migrated_from_store_uuid = 0bb1395b-…`, `store_status = current`,
`schema_version = 12`. tasks **5067**, task_comments **11218**,
notifications **3836**, dm_messages 5228, messages 2042.

### A real, separate gap — 149 stranded notification envelopes

Four surviving legacy `todo.db` inbox files (under
`~/.scitex/cards/.old/…`, `~/.scitex/cards/runtime/.old/…`) hold an **identical**
365-id `inbox` set. Of those, **149 ids are absent from PostgreSQL
`notifications`.** ts range `2026-08-09T09:10:16Z` → `2026-08-11T06:52:16Z`; all
149 have `seen=1`; types dm 134, commented 7, created 4, status_changed 3,
completed 1.

Their **payloads all survive**: 134/134 dm `msg_id` present in PG `dm_messages`,
15/15 card-event `card_id` present in PG `tasks`. So these are already-delivered
notification *envelopes*, not unique content — no user-visible loss — but
dropping the legacy inbox reader is **not byte-lossless** for them.

This is precisely what `_health_stranded_backlog.py` exists to detect, which is
the concrete argument for keeping bucket C until the 149 are reconciled or
consciously written off.

### Caveats keeping this at "cannot prove"

1. The uuid search exited `rg` with code 2 (some paths unreadable), so
   exhaustiveness is high but not absolute.
2. The retired store may have lived on another host — `nas03` and `compute-04`
   both appear in the migration artifacts — which was not examined.

---

## What landed in this PR, and what did not

**Landed**

1. This classification.
2. `import sqlite3` removed from the 15 annotation-only modules (D1), with
   annotations retyped to the type callers actually receive. 30 → 15.
3. `tests/scitex_cards/test__no_sqlite3_import_in_src.py` — an AST barrier
   asserting no module under `src/scitex_cards` imports the driver, with a
   shrink-only allowlist naming the 15 remaining offenders **and why each one
   still holds the driver**, plus a positive control.

**Not landed, and why**

| item | why not |
|---|---|
| the three create-capable doors (A1) | removing them is a behavioural change to how every store opens; a large share of the 49 SQLite-touching tests build temp SQLite stores through exactly these doors, so the door and the tests must move together |
| the SQLite inbox backend (A2) | `_health_stranded_backlog.py:54` imports `inbox_db_path` from it, and 149 stranded rows currently need that check. Extract `inbox_db_path` first |
| the legacy readers (C) | Phase 2 returned **cannot prove**. Dropping a reader whose losslessness is unproven is the failure this package has scar tissue from |
| `_index.py` | a separate SQLite database (a derived FTS cache, not the store). Needs its own ruling: migrate to PostgreSQL FTS, or delete the cache outright |

## The ratchet

`KNOWN_SQLITE_IMPORTERS` in the barrier test is the migration's progress metric.
It accepts exactly one kind of edit: **deletion**. A module not on the list that
starts importing the driver fails immediately; a module on the list that stops
importing it *also* fails until its entry is removed, so the list cannot decay
from a shrinking debt into a permanent exemption.

15 entries remain. Each deletion is one module that can no longer create a cards
database.
