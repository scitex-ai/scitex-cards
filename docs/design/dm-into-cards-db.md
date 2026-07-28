# DM into `cards.db` — current state, schema, append-only rules

**Status:** DESIGN ONLY. No migration is performed by this document or by the
tests that accompany it.
**Date:** 2026-07-28
**Part 2:** [`dm-into-cards-db-migration.md`](dm-into-cards-db-migration.md) —
migration strategy, multi-host concurrency, compatibility, test map.
**Operator rulings this answers:** 「YAML は使いません」 (we do not use YAML) and
「db を使ってください；マルチホスト対応してください」 (use the db; support
multi-host).
**Hard constraint carried in:** the store is APPEND-ONLY
(「一度書いたものは消えない」). A written record never disappears; deletes are
tombstones; **a count decrease is itself a bug.**

Companion tests: `tests/scitex_cards/test__dm_into_db_design.py`. They are
`xfail` (intent) plus two passing pins (current reality). They do not migrate
anything and they do not touch the live store.

---

## 0. Scope and non-goals

**In scope.** Where DM data lives, what the table schema is, how threads and
group threads are represented, how a backfill from `threads.json` runs and how
it stays reversible, and what happens when two hosts write.

**Explicit non-goals.**

- No data is migrated. No live store is touched. The deliverable is a design
  plus failing tests, reviewed *before* anyone writes a migration.
- No UI work. The board's `/chat` pane and the MCP `dm_send` / `dm_list` verbs
  keep their current wire shapes for pair threads (part 2 §7).
- Group-DM *product* behaviour (naming, invites, permissions) is out of scope.
  This card only removes the storage-layer reason group DM is impossible.

---

## 1. Current state — the exact read/write paths

### 1.1 The sidecar

DMs live in a JSON **sidecar**, not in the canonical SQLite store:

```
<store_dir>/threads.json          # the data
<store_dir>/.threads.json.lock    # its own flock sentinel
<store_dir>/.threads.json.tmp     # write staging
```

On-disk shape: `{"threads": {"dm:<a>::<b>": [ {id, thread, from, to, body, ts,
read}, ... ]}}`. `<store_dir>` is `resolve_tasks_path(store).parent`, i.e. the
directory holding `cards.db` — so the sidecar sits *next to* the canonical
store without being *in* it.

The one module that owns it is `src/scitex_cards/_threads.py`.

### 1.2 Write paths (every place the sidecar is written)

| # | Entry point | File | What it writes |
|---|---|---|---|
| W1 | `append_message()` | `_threads.py:359` | takes `_threads_lock`, `_load_threads` (uncached, fresh), appends one record, `_save_threads_unlocked` rewrites **the whole document** |
| W2 | `mark_read()` | `_threads.py:458` | lock-free "fast NO" off the read cache, then lock + fresh parse + flip `read` flags + **whole-document** rewrite |
| W3 | `_migrate_legacy_yaml_once()` | `_threads.py:109` | **fires from `threads_path()`** — a *path query* writes a file when a legacy `threads.yaml` exists and `threads.json` does not. It is the one caller allowed to write without the lock. |
| W4 | `_save_threads_unlocked()` | `_threads.py:256` | the shared crash-safe writer: dump → sibling `.tmp` → `fsync` → **reparse-verify thread count + total message count** → `os.replace` |

W3 is a landmine for this migration and is pinned by a **passing** test in the
companion file: a function whose job is "tell me the path" materialises the
very file we are retiring, and it does so by reading YAML — which the operator
has ruled out. Any migration must neutralise W3 first or it will re-create the
sidecar behind its own back.

**The write amplification is the core defect.** W1 appends *one* message by
rewriting *every* message in *every* thread. On the live sidecar that is a
~3 MB read-modify-write per DM. The consequences compound in part 2 §6.

### 1.3 Read paths

| # | Entry point | File | Notes |
|---|---|---|---|
| R1 | `get_thread(a, b)` | `_threads.py:397` | via `_load_threads_cached`; copies records out |
| R2 | `list_threads()` | `_threads.py:414` | per-thread summary + per-peer unread; **rescans every record of every thread** (~0.7 s on the live 3 MB sidecar even with the parse cached), so it has its own `_SUMMARY_CACHE` |
| R3 | `_load_threads_cached()` | `_threads.py:213` | process-global cache keyed on the file's `(st_mtime_ns, st_size)` |

Both caches are **keyed on file mtime**. That is the correct design for a file
and has no analogue for a database — see part 2 §7.4.

### 1.4 Callers (the blast radius of any change)

| Caller | File | Uses |
|---|---|---|
| MCP verbs `dm_send` / `dm_list` | `_mcp_server.py:310-311` → `_backend.py:249-263` | `append_message`, `thread_key`, `mark_read`, `get_thread` |
| Board API `GET/POST /dm/thread/<peer>` | `_django/handlers/dm.py:178` | same four |
| Board API `GET /dm/threads` | `_django/handlers/dm.py:133` | `list_threads` |
| Hub RPC client | `_backend_http.py` | ships `dm_send` / `dm_list` as `POST /v1/rpc/<verb>` |
| Hub RPC server | `_server.py:53` | `_STORE_KWARG_IS_STORE` already contains `dm_send`, `dm_list` |
| Stop-hook reason | `_may_stop.py:153` | consumes DM records for "you have unread DMs" |
| **Chat attachments root** | `_django/handlers/attachments.py:49` | `attachments_root()` = `threads_path(store).parent / "attachments"` — a latent coupling: the attachments directory is located *via the DM sidecar's path* |

### 1.5 The `messages` table that already exists — and why it is not the answer

`cards.db` schema v3/v4 already has a `messages` table (`_db.py:250`), and
`_db_sections._insert_messages` populates it from the sidecar. It is **not** a
DM store. It is a one-directional derived mirror:

- **Ownership rule** (`_db_sections.py` docstring, `_db_mirror.py` note 1): *a
  table is owned by exactly the file that produces it.* `messages` is produced
  by `threads.json`, so `_db_bootstrap._DOC_CLEAR_ORDER` deliberately
  **excludes** it — a doc write that rebuilt `messages` would delete every DM
  thread on every card write. S1 nearly shipped exactly that.
- Nothing reads DMs from it. The only consumer is `_db_export.export_doc`
  (`_db_export.py:133`), which reconstructs `{thread_key: [records]}` and
  writes it back out as a `threads.json`-shaped file.
- Its column set hard-codes 1:1: `sender TEXT, recipient TEXT`. **One
  recipient per message.** That single column is the schema-level reason group
  DM cannot exist.

So the DB already *carries* DM bytes; it just is not *the* store of them, and
its shape cannot hold a group thread.

---

## 2. Why the sidecar blocks multi-host and group DM

**Multi-host.** Each host resolves its own `<store_dir>`, so each host has its
own `threads.json`. There is no concurrency between them — there is **forking**.
A DM sent on host A is invisible on host B forever, with no error and no
divergence signal. The hub RPC rail (`_backend_http` + `_server`) already
routes `dm_send` / `dm_list` to one host, which is the right answer — but it
routes them to a host whose DM data is in a *file*, so the hub's own database
snapshot, export, backup and integrity rails do not cover DMs.

**Group DM.** `messages.recipient` is a scalar and `thread_key(a, b)` takes
exactly two peers. Unread state is `record["read"]`, a single boolean — with
three members there is no way to express "Bob read it, Carol did not".

**Durability asymmetry.** The card store gets WAL, `busy_timeout`,
`quick_check`, store-identity stamping (`_db_freshness.KEY_STORE_PATH`), a
no-shrink guard, tombstones, export/snapshot and a min-client-version gate.
DMs get none of it. They are the least protected data in the package while
being the operator's primary channel.

---

## 3. Proposed schema — `cards.db` v5

Four new tables. **`messages` is left in place, untouched, forever**
(part 2 §5.6).

```sql
-- One row per thread. NEVER deleted.
CREATE TABLE IF NOT EXISTS dm_threads (
    id           TEXT PRIMARY KEY,   -- 'dm:<a>::<b>' (pair) | 'dmg:<ulid>' (group)
    kind         TEXT NOT NULL,      -- 'pair' | 'group'
    title        TEXT,               -- group threads only
    created_at   TEXT NOT NULL,      -- ISO-8601 Z
    created_by   TEXT,
    origin_host  TEXT NOT NULL,      -- host that minted the thread
    record_json  TEXT NOT NULL       -- verbatim payload (v3 exactness rule)
);

-- Membership as an APPEND-ONLY EVENT LOG. Current membership = fold of events.
CREATE TABLE IF NOT EXISTS dm_thread_member_events (
    id           TEXT PRIMARY KEY,   -- deterministic; see 3.4
    thread_id    TEXT NOT NULL REFERENCES dm_threads(id),
    member       TEXT NOT NULL,      -- peer name (or its resolved u_* id)
    action       TEXT NOT NULL,      -- 'join' | 'leave'
    ts           TEXT NOT NULL,
    actor        TEXT,
    origin_host  TEXT NOT NULL,
    record_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dm_member_thread
    ON dm_thread_member_events(thread_id, ts, id);
CREATE INDEX IF NOT EXISTS idx_dm_member_member
    ON dm_thread_member_events(member);

-- The messages. Immutable except their tombstone columns.
CREATE TABLE IF NOT EXISTS dm_messages (
    id           TEXT PRIMARY KEY,   -- 'm_<hex|ulid>'; see 3.5
    thread_id    TEXT NOT NULL REFERENCES dm_threads(id),
    sender       TEXT NOT NULL,
    body         TEXT NOT NULL,
    ts           TEXT NOT NULL,      -- wall clock of the origin host, DISPLAY ONLY
    seq          INTEGER NOT NULL,   -- per-thread logical counter; SORT HINT (3.6)
    origin_host  TEXT NOT NULL,
    deleted_at   TEXT,               -- TOMBSTONE. NULL = live. Row NEVER removed.
    deleted_by   TEXT,
    record_json  TEXT NOT NULL       -- verbatim payload (attachments, etc.)
);
CREATE INDEX IF NOT EXISTS idx_dm_messages_thread
    ON dm_messages(thread_id, seq, id);
CREATE INDEX IF NOT EXISTS idx_dm_messages_sender
    ON dm_messages(sender, ts);

-- Read state, per (message, reader). INSERT-ONLY.
CREATE TABLE IF NOT EXISTS dm_receipts (
    message_id   TEXT NOT NULL REFERENCES dm_messages(id),
    reader       TEXT NOT NULL,
    read_at      TEXT NOT NULL,
    origin_host  TEXT NOT NULL,
    source       TEXT NOT NULL,      -- 'live' | 'backfill' (part 2 §5 M1)
    PRIMARY KEY (message_id, reader)
);
CREATE INDEX IF NOT EXISTS idx_dm_receipts_reader ON dm_receipts(reader);
```

### 3.1 There is no `recipient` column — and that is the point

Recipients are **derived** from thread membership. `dm_messages` says who
*sent*; `dm_thread_member_events` says who can *see*. That decoupling is the
whole group-DM unlock, and it costs nothing for pair threads (the "other
member" is a one-row lookup).

`unread(reader)` becomes:

```sql
SELECT m.* FROM dm_messages m
JOIN (current membership fold) mem
  ON mem.thread_id = m.thread_id AND mem.member = :reader
WHERE m.sender != :reader
  AND m.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM dm_receipts r
                  WHERE r.message_id = m.id AND r.reader = :reader);
```

— an indexed query, replacing R2's full rescan of every record of every thread.

### 3.2 Read receipts are a table, not a boolean

`messages.read` is today the *one* column `_db_export._OVERLAYS` treats as
mutable-after-write. Moving read state into its own INSERT-ONLY table makes
`dm_messages` strictly immutable apart from its tombstone, which is exactly
what makes a cross-host merge a pure union (part 2 §6.3). A receipt is
monotone: it is never removed, and "unread again" is not expressible. That is
deliberate.

### 3.3 Thread identity: derived for pairs, opaque for groups

- **Pair** — `id = 'dm:<a>::<b>'` with the peers sorted, i.e. **byte-identical
  to today's `thread_key()`**. Every stored id, every MCP response and every
  board URL keeps working; nothing is rewritten (rewriting an id is a
  delete-and-insert, which the append-only rule forbids).
- **Group** — `id = 'dmg:<ulid>'`, **opaque**, never derived from the member
  set. A derived group key would *change* when someone joins or leaves, which
  would orphan the history or force every message's `thread_id` to be
  rewritten. An opaque id is stable across membership change.

The asymmetry is safe because a pair's membership is immutable by construction
(two members, no join/leave). "Promote a pair thread to a group" is therefore
**not** an in-place mutation: mint a new group thread, record a
`promoted_from` link in its `record_json`, and leave the pair thread intact.

### 3.4 Deterministic ids for derived rows

Membership events created by the backfill get
`id = 'dme_' + sha256(thread_id | member | action | ts)[:24]`. Determinism is
what makes the backfill idempotent under `INSERT OR IGNORE` — a re-run maps to
the same primary key and inserts nothing. Live joins mint a ULID instead.

### 3.5 Message ids

Existing ids (`m_` + 12 hex = 48 bits of entropy) are **accepted verbatim and
never rewritten**. New ids widen to `m_<26-char ULID>` (128 bits). 48 bits has
a birthday bound near 2^24 (~16.7 M messages) — comfortably beyond today's
board, but a multi-host union of independently-minted ids is exactly the
setting where a birthday collision stops being theoretical, and a PK collision
under `INSERT OR IGNORE` silently *drops a message*.

### 3.6 Ordering

Today's order is `rowid` — insertion order into one file, meaningless across
hosts. Replacement: **`ORDER BY seq, ts, origin_host, id`**.

- `seq` is a per-thread logical counter (`1 + MAX(seq)` for that thread at
  append time). Within one database it reproduces exactly today's append order.
- `ts` is wall clock and is **display only**; it never breaks a tie alone,
  because two hosts' clocks skew.
- `(origin_host, id)` makes the sort **total and deterministic**, so every
  host that holds the same row set computes the same order.

---

## 4. Append-only, made unreachable rather than guarded

The package's own doctrine (`_store_backend.py`) is that a *guard* can be
bypassed but an *unreachable state* cannot. Applied here:

**4.1 No `DELETE FROM dm_*` exists in the source.** Testable by inspection of
the module set, and it is one of the companion tests.

**4.2 SQLite triggers make it unreachable at the engine**, not just in Python —
they fire for the `sqlite3` CLI, for a stray script, for anything:

```sql
CREATE TRIGGER IF NOT EXISTS dm_messages_no_delete
BEFORE DELETE ON dm_messages BEGIN
    SELECT RAISE(ABORT,
        'dm_messages is append-only: tombstone via deleted_at, never DELETE');
END;

CREATE TRIGGER IF NOT EXISTS dm_messages_immutable
BEFORE UPDATE ON dm_messages
WHEN OLD.thread_id   IS NOT NEW.thread_id
  OR OLD.sender      IS NOT NEW.sender
  OR OLD.body        IS NOT NEW.body
  OR OLD.ts          IS NOT NEW.ts
  OR OLD.seq         IS NOT NEW.seq
  OR OLD.origin_host IS NOT NEW.origin_host
  OR OLD.record_json IS NOT NEW.record_json
BEGIN
    SELECT RAISE(ABORT,
        'dm_messages rows are immutable except deleted_at/deleted_by');
END;
```

Equivalent `no_delete` triggers on `dm_threads`, `dm_thread_member_events` and
`dm_receipts`.

**4.3 Deletion is a tombstone.** `deleted_at` / `deleted_by` are set once. The
row, its body and its receipts all survive. Read paths hide a tombstoned
message exactly as `_task._is_tombstoned` hides a deleted card
(`status: cancelled` + `_log_meta.deleted_at`); the mechanism is the same one
the 2026-07-21 board wipe forced onto cards.

**4.4 A count decrease aborts.** `_store_backend._assert_no_shrink` already
does this for `tasks`. The DM write/merge/import path gets the same check
against `COUNT(*) FROM dm_messages`: any operation whose post-state has fewer
rows than its pre-state **raises**, it does not warn.

**4.5 Membership removal is a `leave` event, not a row deletion.** The fold
yields "not a member now"; the record of having been one survives.

**Stated honestly:** `DROP TRIGGER`, a raw file copy, or `sqlite3 .recover`
still bypass all of this. The triggers close the accident class, not the
adversary class, and rows accumulate forever by design — unbounded growth is a
storage cost, and the alternative was data loss.

---

Continue to [part 2](dm-into-cards-db-migration.md) for the migration
strategy, multi-host concurrency analysis, compatibility work and the
test-first map.

<!-- EOF -->
