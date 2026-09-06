# DM into `cards.db` — migration, multi-host, compatibility, test map

**Part 2 of 2.** Part 1 —
[`dm-into-cards-db.md`](dm-into-cards-db.md) — covers the current read/write
paths, the proposed schema and the append-only rules. Section numbering
continues from it.

**Status:** DESIGN ONLY. **Nothing in this card executes any phase below.**

---

## 5. Migration strategy, and how it stays reversible

Six phases.

### M0 — schema only

Bump `SCHEMA_VERSION` 4 → 5; add the four tables + triggers to `_SCHEMA_SQL`
and to `SCHEMA_TABLES`. `CREATE TABLE IF NOT EXISTS` is a no-op on an existing
table, so — exactly as `_migrate_v1_to_v2` documents — an existing DB needs an
explicit additive `_migrate_v4_to_v5()` that creates the tables and triggers.
Nothing reads or writes them.

**Reversal: none needed; the tables are inert.**

### M1 — backfill (COPY, never move)

Read `threads.json` **under its own flock** so no writer is mid-RMW. Then:

- per thread key → `INSERT OR IGNORE INTO dm_threads(id=<key>, kind='pair', …)`;
- two deterministic `join` member events per pair thread (part 1 §3.4);
- per record → `INSERT OR IGNORE INTO dm_messages(id=<record id>, …)`, with
  `record_json` carrying the record **verbatim** (the v2/v3 exactness rule:
  typed columns are the index, the JSON is the payload);
- `read: true` → one `dm_receipts` row, `source='backfill'`.

**Id-less records.** The inbox migration skips them
(the retired inbox backend's `_migrate_into_conn`) because a record with no
stable id cannot
be deduped on re-run. Here we can do better: mint
`'m_' + sha256(thread_id|from|to|ts|body)[:24]` — content-derived, so a re-run
maps to the same PK. Skipping would *lose a message*, which this design may not
do.

**Unknown read time.** A backfilled receipt has no real `read_at`. It gets the
backfill timestamp plus `source='backfill'` — a **sentinel, not an absence**.
A NULL would be indistinguishable from "never read" and would make every
already-read message pop unread for everyone at cutover.

**Idempotent by construction:** every insert is `OR IGNORE` on a stable PK.
**The sidecar is not written, moved or truncated.**

**Reversal: delete nothing; the sidecar is still the file it was.**

### M2 — dual-read verification gate

A `scitex-cards dm verify` verb diffs, per thread, the sidecar's records
against the DB's, by id, and prints counts plus the first N mismatches. This is
the A/B equivalence gate ADR-0010 §2 used for the card cutover. **M3 does not
start until this is clean.**

Note the WAL snapshot lesson already recorded in `_db_export.export_doc`: read
the DB side of the comparison from **one** connection and one transaction, or a
concurrent writer manufactures a false mismatch. That exact false negative
blanked `list_tasks` fleet-wide once already.

### M3 — flip the WRITE path (dual write)

`_threads.append_message` writes the DB **and** appends to the sidecar. The DB
write is authoritative and raises on failure; the sidecar write is best-effort
and logged. Note this is the **inverse polarity** of the card cutover: there,
YAML was the store and the DB mirror was best-effort. Here the DB is the new
SSOT and the sidecar is the rollback state.

**Reversal: redeploy the previous version — the sidecar is complete.**

### M4 — flip the READ path

After M2 has been clean for an agreed window. Reads come from the DB; the
sidecar keeps being appended. `_db_export`'s `messages` source moves to
`dm_messages` in this phase.

**Reversal: same as M3.**

### M5 — stop writing the sidecar

Retire `threads.json` to `<store_dir>/.old/<timestamp>/threads.json` —
**moved, never deleted** (the `.old/` convention is already documented in
`_paths.py`). Delete `_migrate_legacy_yaml_once` (part 1 §1.2 W3) in the same
change: it is the last YAML reader on this path and it violates
「YAML は使いません」.

**Reversal after M5 already exists and is already tested.**
`_db_export.export_json(threads_out=…)` writes a `{"threads": {...}}` document
in exactly the sidecar's format, and `_threads._load_threads` reads exactly
that key. The reverse migration is `db export` → copy into place.

**One honest gap:** a *group* thread has no single `to`, so it cannot be
represented in the legacy sidecar shape. Reverse migration is therefore
**lossy for group threads only** — a feature that did not exist before the
migration and so cannot be regressed by it. Pair threads round-trip exactly.
This is a limitation to state, not to hide.

### 5.6 What happens to the old `messages` table

**Nothing. Ever.** It is not dropped, not renamed, not truncated — an
append-only store does not remove tables that hold real rows. It stops being
written after M5 and stays as a frozen pre-migration snapshot. Dropping it
would be a count decrease, which is the bug class this whole document exists to
avoid.

---

## 6. Two hosts writing concurrently

### 6.1 What breaks today

Not concurrency — **forking**. Two hosts, two `threads.json` files, no shared
state, no error. Messages diverge permanently and silently.

And if the store directory were ever put on a network share to "fix" that, the
failure gets worse rather than better:

- `fcntl.flock` is unreliable on NFS/CIFS, so W1's lock can be silently lost;
- W1 rewrites the **whole document**, so one lost update does not lose one
  message — it loses **every message in every thread** that the other writer
  added since the loser's read.

Whole-file read-modify-write is the amplifier. That is the same shape as the
2026-07-19/20 card wipes: a stale full-document write replacing live rows.

### 6.2 After: three cases, one recommendation

**1. One DB, several processes, one host — SOLVED by construction.**
WAL + `busy_timeout=300000` + a write that is a single `INSERT` instead of a
3 MB rewrite. Two concurrent appends both land; a lost update is not
expressible, because neither writer restates rows it did not author. This is a
strict improvement even with no multi-host story at all.

**2. One DB on a shared filesystem, several hosts — REJECTED, explicitly.**
A file-backed engine over NFS/CIFS is unsafe (advisory locking is unreliable
there). The store must not be put on a network share. This is written down so
nobody "fixes" multi-host that way. (Moot since the cutover to PostgreSQL,
which is reached over the network by design; kept because the reasoning is
what stopped the shared-file shortcut.)

**3. Hub-authoritative — RECOMMENDED, and already built.**
`SCITEX_CARDS_HUB_URL` + `scitex-cards serve` already route `dm_send` /
`dm_list` as `POST /v1/rpc/<verb>` to one hub (`_backend_http.py`;
`_server.py:53` already lists both verbs in `_STORE_KWARG_IS_STORE`). So
multi-host DM needs **no new distributed algorithm** — it needs the DM data to
be *inside* the hub's database instead of in a file the hub's RPC verbs happen
to touch. That is precisely this card. Concurrency then reduces to case 1.

### 6.3 Federated merge, for hosts that must work offline

ADR-0010 §5 leaves Spartan's island store open. The schema is designed so that
merge is a **pure union**, because every table is append-only and every row has
a globally-unique PK:

| Table | Merge rule |
|---|---|
| `dm_threads` | `INSERT OR IGNORE` on `id` |
| `dm_thread_member_events` | `INSERT OR IGNORE` on `id`; fold at read time |
| `dm_messages` | `INSERT OR IGNORE` on `id`; tombstone = **earlier** `deleted_at` wins (commutative, and never un-tombstones) |
| `dm_receipts` | `INSERT OR IGNORE` on `(message_id, reader)` — read is monotone |

No arbitration, no last-write-wins, no clock comparison, no vector clocks.
Merge is commutative, associative and idempotent, so re-running it is free and
merge order does not matter.

**The one thing that does not merge cleanly** is `seq`: two hosts appending
offline both mint `seq = N` for the same thread. This is *by design* not a
correctness problem, because `seq` is a **sort hint**, not an identity —
`ORDER BY seq, ts, origin_host, id` is total and deterministic, so every host
converges on the same order. The honest cost: the post-merge order may
interleave differently from what a user saw live on one host before the merge.
That is bounded to *interleaving*; it is never *loss*.

**Merge must be guarded by the no-shrink rule.** Any import whose post-state
`COUNT(*) FROM dm_messages` is lower than its pre-state raises. A merge is a
union; a union cannot shrink; a shrink means a bug, per the operator's ruling.

---

## 7. Compatibility, and the things that must move with it

### 7.1 `_threads` public API is preserved for pair threads

`append_message` / `get_thread` / `list_threads` / `mark_read` / `thread_key` /
`peers_of` keep their signatures and return shapes. For a pair thread, `to` is
derived (the other member) and `read` is derived (a receipt exists for the
reader). New verbs are additive: `create_group_thread`, `add_member`,
`remove_member`, `list_members`, `get_thread_by_id`.

### 7.2 `attachments_root` must stop going through the DM sidecar

`_django/handlers/attachments.py:49` locates the attachments directory as
`threads_path(store).parent / "attachments"`. Change it to
`resolve_tasks_path(store).parent / "attachments"` — same directory, no
behaviour change, and it removes a coupling that would otherwise break when
`threads_path` is retired. Safe to land ahead of the migration.

### 7.3 `threads_path()` must stop writing (W3)

Before any phase, `_migrate_legacy_yaml_once` must be removed from the
`threads_path()` call path. A path query that materialises a file will
re-create the sidecar behind the migration's back.

### 7.4 The two caches are deleted, not ported

`_READ_CACHE` and `_SUMMARY_CACHE` are keyed on the backing file's
`(mtime_ns, size)`. A database has no such key, and the correctness rule they
carry ("writers never read the cache") disappears with them: a DB writer
appends a row and never restates the document. The performance they bought
(R2's ~0.7 s rescan) is replaced by the indexed query in part 1 §3.1.

### 7.5 Board write-path hardening rides along

`_django/handlers/dm.py:62` documents an open arbitrary-write seam: the store a
**write** targets is still taken from the `?store=` **query** as a fallback
(card `scitex-cards-dm-store-from-query-and-forced-operator-author-20260728`).
The DM-in-DB cutover is the natural moment to delete that fallback, because the
write target stops being a caller-supplied file path at all.

---

## 8. Test-first map

`tests/scitex_cards/test__dm_into_db_design.py` encodes this design. Every
test names the section it comes from. All DB tests use an **explicit**
`tmp_path / "cards.db"` — none resolves the ambient store.

| Test | Encodes |
|---|---|
| `test_threads_path_materialises_the_sidecar_from_legacy_yaml` | §1.2 W3 — **passes today**, pins the landmine |
| `test_dm_sidecar_is_a_file_beside_the_database` | §1.1 — **passes today**, pins the starting state |
| `test_schema_declares_the_dm_threads_table` | §3 |
| `test_schema_declares_the_dm_thread_member_events_table` | §3 |
| `test_schema_declares_the_dm_messages_table` | §3 |
| `test_schema_declares_the_dm_receipts_table` | §3 |
| `test_dm_messages_has_no_recipient_column` | §3.1 — the group-DM unlock |
| `test_dm_messages_refuses_physical_delete` | §4.2 |
| `test_dm_messages_body_is_immutable` | §4.2 |
| `test_dm_messages_tombstone_marks_the_row_in_place` | §4.3 |
| `test_pair_thread_id_is_the_legacy_thread_key` | §3.3 — no id is rewritten |
| `test_group_thread_id_survives_a_membership_change` | §3.3 |
| `test_group_message_is_visible_to_every_member` | §3.1 |
| `test_read_receipt_is_scoped_to_one_reader` | §3.2 |
| `test_message_records_its_origin_host` | §3.6 / §6.3 |
| `test_thread_order_is_independent_of_insertion_order` | §3.6 |
| `test_backfill_leaves_the_sidecar_byte_identical` | §5 M1 — reversibility |
| `test_backfill_is_idempotent` | §5 M1 |
| `test_merge_from_a_peer_host_is_a_union` | §6.3 |
| `test_merge_is_idempotent` | §6.3 |
| `test_merge_never_shrinks_the_message_count` | §4.4 / §6.3 — the operator's rule |
| `test_no_source_module_deletes_from_a_dm_table` | §4.1 |

The `xfail` markers are **non-strict**: an unexpected pass reports `XPASS`
rather than failing the run, so landing the real implementation incrementally
never turns CI red on a test that started working early.

---

## 9. Open questions for review

1. **Membership: event log vs. a `left_at` column.** This design picks the
   event log because it merges by union with no arbitration, where a mutable
   `left_at` needs last-write-wins across hosts. It costs a fold at read time.
   Confirm.
2. **Federate at all, or hub-only?** §6.2 case 3 needs no new machinery and
   covers every host that has network to the hub. §6.3 is only needed for a
   genuinely offline island (Spartan). Shipping only case 3 first is smaller
   and reversible; §6.3's schema properties cost nothing to keep either way.
3. **Group-thread reverse migration is lossy (§5 M5).** Accepted, or does the
   export rail need a second, group-aware format before group DM ships?
4. **Widening message ids to ULID (part 1 §3.5)** changes the id shape agents
   see in `dm_list` output. Cosmetic, but it is a wire change.
5. **Sequencing against the open board write-seam card** (§7.5) — land them
   together, or land the seam fix first so the cutover has one less moving
   part?

<!-- EOF -->
