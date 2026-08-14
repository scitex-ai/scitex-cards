#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INCREMENTAL dual-write mirror: touch only the cards that actually changed.

WHY THIS EXISTS (measured on the live 1,365-card board, 2026-07-12):

    uncontended card write : 16.31 s
      of which the mirror  :  8.69 s      <- MORE THAN HALF

S1 shipped a FULL REBUILD: ``DELETE FROM`` every doc-owned table, then re-insert
all 1,365 tasks + 3,043 comments + edges + roles. On EVERY card write. I argued
that was fine because I measured the rebuild at 1.24 s that morning and called it
noise. It is 8.69 s now — the cost grows with the board, and it more than DOUBLES
the very stall the SQLite migration exists to remove. It also doubles the
CRITICAL SECTION, which doubles the convoy for every other writer.

The full rebuild was chosen for a real reason: ``_save_doc_unlocked`` receives the
whole doc and does NOT know which card changed. This module answers that question
without needing the caller to tell it — it hashes each card and compares against
the hashes it stored last time. A typical write touches ONE card, so it does ONE
upsert instead of five thousand statements.

    reading every existing hash : one SELECT (~10 ms on 1,365 rows)
    hashing the doc            : pure Python, ~50 ms
    upserting the delta        : ~1 row

CORRECTNESS NOTES — the two ways this could quietly corrupt the mirror:

1. ``messages`` is NOT ours. It is derived from the threads.json SIDECAR, not from
   the doc. S1 nearly deleted every DM thread on every card write by rebuilding it;
   :data:`_db_bootstrap._DOC_CLEAR_ORDER` excludes it and so must we. A table must
   be owned by exactly the file that produces it.

2. A card leaves the mirror ONLY when a caller NAMES it (the explicit
   ``deleted_ids`` handed down from ``delete_task``) — NEVER by inferring deletion
   from a card's mere absence in the doc. Inference-from-absence is precisely what
   let a stale document wipe live cards on 2026-07-20; it is deleted, not guarded
   (see the reconcile loop below). The explicit path is a deliberate single-card
   verb with ``restore_task`` as its Undo, so it cannot mass-wipe from a stale read.

The hashes live in their own table (``mirror_hashes``), created on demand. If it
is missing or empty — a fresh DB, or one bootstrapped by the old full-rebuild
path — we fall back to a full rebuild ONCE and populate it. So this is safe to
deploy against an existing DB with no migration step.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from ._db_bootstrap import (
    _insert_tasks,
    _insert_users,
    _rebuild_from_doc,
)
from ._db_freshness import stamp_store_provenance

# Shape-agnostic row access. psycopg's dict_row is a real dict and raises
# KeyError on a positional index, and since #693 open_db can hand this
# module a PostgreSQL connection. _schema_probe imports nothing from this
# package, so a module-level import here cannot cycle.
from ._schema_probe import _sole_value, row_values

#: Per-card content hashes, so a write can tell what actually changed.
HASH_TABLE = "mirror_hashes"

_HASH_DDL = f"""
CREATE TABLE IF NOT EXISTS {HASH_TABLE} (
    task_id TEXT PRIMARY KEY,
    hash    TEXT NOT NULL
)
"""

#: Sections of the doc that are NOT per-card. They change rarely, so they get one
#: hash each and are only rebuilt when that hash moves.
#:
#: ``inboxes`` IS DELIBERATELY ABSENT, for exactly the reason ``messages`` is
#: absent from :data:`_db_bootstrap._DOC_CLEAR_ORDER`: A TABLE IS OWNED BY
#: EXACTLY THE THING THAT PRODUCES IT, and since #780 the thing that produces
#: ``notifications`` is the delivery rail (``_inbox_postgres``), not this
#: document.
#:
#: While it was listed here, an ORDINARY CARD WRITE rebuilt the live rail:
#: :func:`_sync_sections` issues ``DELETE FROM notifications`` and re-inserts
#: from ``doc["inboxes"]`` through :func:`_db_sections._insert_notifications`,
#: which writes NINE of the table's THIRTEEN columns. ``msg_id`` (the exact DM
#: dedupe key), ``pushed_at`` and ``confirmed_at`` (the delivery receipts) were
#: therefore ERASED, and ``seq`` — the arrival order the drain and the ack both
#: order by — was re-issued from ``nextval`` in ``ts, id`` order, silently
#: RENUMBERING the queue. Nothing failed while it happened.
#:
#: The trigger was not rare either: the section hash moves whenever any
#: notification row changes, and ``seen`` is overlaid into the exported record,
#: so every poll made the next unrelated ``add_task`` rebuild the rail.
#:
#: ``_migrate_v7_to_v8`` predicted this in writing — "that DELETE must be
#: neutralised in the same change that flips the writers, or the migration turns
#: a dead mirror into a silent deletion trigger". The writers flipped in #780
#: and the DELETE was not neutralised. This is that neutralisation.
#:
#: The export still EMITS ``inboxes`` (the backup rail in ADR-0010 must contain
#: the notifications); it is only the write-back that no longer owns them.
_SECTION_KEYS = ("users",)


def _card_hash(card: dict) -> str:
    """Stable content hash of one card. ``default=str`` so a stray datetime or
    ruamel scalar cannot make an unchanged card look changed every write."""
    blob = json.dumps(card, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()  # noqa: S324


def _section_hash(value) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()  # noqa: S324


def _existing_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    conn.execute(_HASH_DDL)
    rows = conn.execute(f"SELECT task_id, hash FROM {HASH_TABLE}").fetchall()
    # row_values, NOT r[0]/r[1]: the annotation says sqlite3.Connection, but an
    # annotation is a claim, not a guarantee -- this takes the CALLER's
    # connection, and since #693 that caller can be holding a psycopg one, whose
    # dict_row raises KeyError on a positional index.

    return {v[0]: v[1] for v in (row_values(r) for r in rows)}


def _drop_card_rows(conn: sqlite3.Connection, task_id: str) -> None:
    """Remove one card's derived rows so it can be re-inserted cleanly.

    ``_insert_comments`` INSERTs (it does not REPLACE — comments carry a
    sequence), so re-inserting a card without clearing first would DUPLICATE
    every comment on it, on every write. That is the sharpest edge in this file
    and it has a test.

    NOTE the columns: ``task_edges`` keys on ``src_task_id`` / ``dst_task_id``,
    NOT ``task_id``. I assumed otherwise and the tests caught it — an assumption
    about a schema is exactly the kind of thing that silently corrupts a mirror.
    """
    conn.execute("DELETE FROM task_comments WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM task_roles WHERE task_id = ?", (task_id,))
    # Edges are written from the SOURCE card's own depends_on/blocks, so
    # re-writing that card owns exactly its outbound edges.
    conn.execute("DELETE FROM task_edges WHERE src_task_id = ?", (task_id,))


def _write_card(
    conn: sqlite3.Connection,
    card: dict,
    *,
    expected_revision: int | None = None,
) -> dict[str, int]:
    """Upsert ONE card and its derived rows. Returns the insert counts.

    ``expected_revision`` makes the whole sequence a COMPARE-AND-SET, and the
    ORDER here is the entire point — get it wrong and the guard destroys data on
    the path it refuses to take.

    `_drop_card_rows` DELETES this card's comments, roles and outbound edges
    before the upsert, because comments key on a sequence and re-inserting
    without clearing would duplicate every one of them on every write. That drop
    is load-bearing and cannot simply be removed.

    But it means a naive compare-and-set — drop first, then let `_insert_tasks`
    check the revision — would:

        1. delete the card's comments, roles and outbound edges
        2. hit the guard, skip the upsert
        3. report revision_skipped=1, i.e. "I changed nothing"

    while the WINNER's comments are already gone. A lock that silently destroys
    the data it was protecting, and then reports success at protecting it, is
    strictly worse than no lock: the caller has no reason to look.

    So the revision is read and compared BEFORE anything is dropped. The `WHERE`
    clause inside `_insert_tasks` remains the real guard against the race
    between that read and the write — this pre-check is not a substitute for it,
    it only ensures the DESTRUCTIVE half never runs for a write that was always
    going to be refused.
    """
    tid = str(card.get("id"))
    if expected_revision is not None:
        row = conn.execute(
            "SELECT revision FROM tasks WHERE id = ?", (tid,)
        ).fetchone()
        found = None if row is None else row[0]
        if found != expected_revision:
            # Refuse BEFORE the drop. Nothing has been touched.
            return {
                "tasks": 0,
                "comments": 0,
                "edges": 0,
                "roles": 0,
                "revision_skipped": 1,
                "revision_found": found,
            }
    _drop_card_rows(conn, tid)
    # _insert_tasks handles the task row + comments + edges + roles for each
    # card it is given, so a one-element list is exactly one card's worth.
    return _insert_tasks(conn, [card], expected_revision=expected_revision)


def _delete_card(conn: sqlite3.Connection, task_id: str) -> None:
    """A card that left the doc must leave the mirror COMPLETELY.

    Also drops edges pointing AT it, which ``_drop_card_rows`` deliberately does
    not (that one is for re-writing a card, which owns only its OUTBOUND edges).
    A dangling inbound edge to a card that no longer exists is exactly the kind
    of rot an equivalence check on PRESENT cards would never notice.
    """
    _drop_card_rows(conn, task_id)
    conn.execute("DELETE FROM task_edges WHERE dst_task_id = ?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.execute(f"DELETE FROM {HASH_TABLE} WHERE task_id = ?", (task_id,))


def mirror_doc_incremental(
    doc: dict,
    db_path: str | Path,
    *,
    conn: sqlite3.Connection | None = None,
    store_path: str | Path | None = None,
    deleted_ids: list[str] | None = None,
    touched_ids: list[str] | None = None,
) -> dict:
    """Mirror ``doc`` by writing ONLY what changed. Raises on failure.

    Returns a summary: ``{"changed": n, "removed": n, "unchanged": n, "full": bool}``.
    ``full`` is True when it fell back to a full rebuild (first run on a DB that
    has no hash table yet).

    ``store_path`` is the canonical YAML this doc was just written to. Pass it and
    the mirror stamps its provenance (path + mtime + size + card count) inside the
    SAME transaction as the rows — so "the data" and "which YAML the data came
    from" can never disagree. WITHOUT it the mirror is unstamped, and the S2 read
    guard REFUSES an unstamped DB rather than assume it is current: a mirror that
    cannot say which store it reflects is a photograph with no date on it.

    ``deleted_ids`` are ids a caller (``delete_task``) INTENTIONALLY removed and
    wants gone from the mirror. Reconcile never infers a delete from a card's
    absence — that inference is the wipe class this module refuses (see the loop
    below) — so an explicit single-card verb names what it removed and the mirror
    drops exactly those rows. ``None``/empty on every ordinary write.

    ``touched_ids`` IS THE MISSING HALF OF THAT SAME ARGUMENT. ``deleted_ids``
    exists because absence is not intent; ``touched_ids`` exists because
    DIFFERENCE IS NOT INTENT EITHER. Without it, ``changed`` below means "every
    card whose content differs from the database" — which silently includes
    "somebody else changed this card and I am holding an old copy". The mirror
    then faithfully writes the caller's stale version over the other agent's
    committed one, and both callers are told their write succeeded.

    Measured on the live board 2026-08-10 by figrecipe: a ``complete_task`` that
    RETURNED ``status=done`` was later found back at ``status=blocked``, reverted
    by writes to unrelated cards. Their conclusion — "there is no batching
    discipline a caller can adopt to avoid it" — is correct, because the
    competing writes come from a different process holding a different lock (the
    store lock is an ``fcntl.flock`` on a per-container FILE while the cards live
    in shared PostgreSQL).

    So a caller that knows which card it touched names it, and cards it did not
    touch are never written — the concurrent update survives regardless of who
    holds which lock. ``None`` keeps the old whole-document behaviour, so verbs
    convert one at a time rather than on a flag day.

    THIS IS NOT A LOCK AND DOES NOT PRETEND TO BE. Two callers naming the SAME
    card still race; that case wants ``pg_advisory_xact_lock``, and it is a much
    smaller problem once the blast radius is one row instead of the whole board.

    Raises deliberately, like :func:`_db_bootstrap.mirror_doc` — the POLICY for a
    failed mirror (never break the user's write, never be silent) lives in
    :mod:`_dual_write`, not in the primitive.
    """
    own_conn = conn is None
    if own_conn:
        # open_db (NOT a bare sqlite3.connect) — it applies the pragmas AND
        # init_schema, so a fresh DB has its tables. The old full-rebuild mirror
        # got this for free via _db_bootstrap; doing it by hand dropped it, and
        # the dual-write tests caught the missing tables. Fail-loud worked: the
        # mirror shouted instead of silently writing nothing.
        from ._db import open_db

        conn = open_db(db_path)
    assert conn is not None

    try:
        tasks = doc.get("tasks") if isinstance(doc, dict) else None
        cards = [c for c in (tasks or []) if isinstance(c, dict) and c.get("id")]

        def _stamp() -> None:
            # Record WHICH STORE this database is the database of, in the same
            # transaction as the rows. The identity is the store's resolved path
            # (post-cutover, the database's own $SCITEX_CARDS_DB path); the
            # ownership guard compares it before every write.
            if store_path is not None:
                stamp_store_provenance(conn, store_path)

        prior = _existing_hashes(conn)

        # FIRST RUN (or a DB bootstrapped by the old full-rebuild path): we have
        # no hashes to diff against, so do the full rebuild ONCE and record them.
        # This is what makes the change safe to deploy with no migration step.
        if not prior:
            summary = _rebuild_from_doc(conn, doc)
            conn.executemany(
                f"INSERT INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)"
                f" ON CONFLICT(task_id) DO UPDATE SET hash = excluded.hash",
                [(str(c["id"]), _card_hash(c)) for c in cards],
            )
            _remember_sections(conn, doc)
            _stamp()
            conn.commit()
            summary.update(
                {"changed": len(cards), "removed": 0, "unchanged": 0, "full": True}
            )
            return summary

        now_hashes = {str(c["id"]): _card_hash(c) for c in cards}
        by_id = {str(c["id"]): c for c in cards}

        changed = [i for i, h in now_hashes.items() if prior.get(i) != h]

        # DIFFERENCE IS NOT INTENT. `changed` above means "differs from the
        # database", which silently includes "somebody else changed this card
        # and I hold an old copy" — so a caller writing card A re-asserts its
        # stale copy of card B over another agent's committed change, and both
        # are told they succeeded.
        #
        # A caller that KNOWS what it touched narrows the write to that. Cards it
        # did not touch are never written, so a concurrent update to them
        # survives no matter who holds which lock — which matters because the
        # store lock is an fcntl.flock on a per-container FILE while the cards
        # live in shared PostgreSQL, i.e. it excludes nobody across agents.
        #
        # The intersection with `changed` is deliberate rather than replacing it:
        # naming a card whose content did NOT change must still write nothing, so
        # a caller cannot manufacture a no-op write into a clobber by over-
        # declaring. Symmetric with `deleted_ids`, which likewise names intent
        # rather than inferring it from the document.
        if touched_ids is not None:
            wanted = {str(i) for i in touched_ids}
            changed = [i for i in changed if i in wanted]

        # RECONCILE INSERTS AND UPDATES. IT NEVER *INFERS* A DELETE FROM ABSENCE.
        # (Explicit, caller-named deletes are a separate, deliberate path — see
        # the `deleted_ids` loop after the changed-writes below.)
        #
        # This loop used to end with:
        #
        #     removed = [i for i in prior if i not in now_hashes]
        #     for tid in removed:
        #         _delete_card(conn, tid)
        #
        # A document that merely LACKED a card therefore destroyed it. That is
        # not a hypothetical reading of the code — it is the mechanism that
        # removed the same 16 cards twice on 2026-07-20, twenty minutes apart:
        # every card created that day and nothing older, because a writer
        # holding a document read BEFORE they existed wrote it back, and the
        # diff called them "removed". Restoring only fed the loop; the second
        # loss happened with no test suite running at all.
        #
        # Operator ruling: 「一度データベースに入ったものって消さないほうがいい
        # んじゃないですか」 — once something has entered the database, better
        # never to delete it.
        #
        # DELETED RATHER THAN GUARDED, deliberately. A guarded delete is one
        # bug away from firing again, and this store has now been destroyed by
        # three different callers reaching the same delete. Guarding the door
        # teaches the next caller nothing; removing it ends the class. Absence
        # from a document is not evidence of deletion — it is far more often
        # evidence of a stale read.
        #
        # Deliberate consequence: a card genuinely deleted elsewhere is no
        # longer propagated here, so rows accumulate. That is the trade the
        # ruling makes, and it is the right one — unbounded growth is a
        # storage cost, and this was data loss.
        for tid in changed:
            _write_card(conn, by_id[tid])

        if changed:
            conn.executemany(
                f"INSERT INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)"
                f" ON CONFLICT(task_id) DO UPDATE SET hash = excluded.hash",
                [(tid, now_hashes[tid]) for tid in changed],
            )

        # EXPLICIT, CALLER-NAMED deletes — the ONE way a row leaves the mirror.
        # `delete_task` passes the id it intentionally removed and the mirror
        # drops exactly that row. This is categorically NOT the absence-inference
        # the ruling above forbids: the id is named by a deliberate single-card
        # verb (Undo = restore_task), not guessed from a document that merely
        # lacks it, so it cannot mass-wipe from a stale read.
        removed = [tid for tid in (deleted_ids or []) if tid in prior]
        for tid in deleted_ids or []:
            _delete_card(conn, tid)

        # Non-card sections: one hash each, rebuilt only when they actually move.
        _sync_sections(conn, doc)

        # ALWAYS stamp, even when nothing changed. The YAML was just rewritten, so
        # its mtime moved whether or not any card did; an unrefreshed stamp would
        # make an ACCURATE mirror look stale and send every reader back to the
        # 830 ms YAML parse. Freshness is about the FILE, not about the delta.
        _stamp()

        conn.commit()
        return {
            "changed": len(changed),
            # Reconcile still never INFERS a delete; this counts only the
            # explicit, caller-named removals (0 on an ordinary write).
            "removed": len(removed),
            "unchanged": len(cards) - len(changed),
            "full": False,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def _section_key(name: str) -> str:
    return "__section__:%s" % name


def _remember_sections(conn: sqlite3.Connection, doc: dict) -> None:
    conn.executemany(
        f"INSERT INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)"
        f" ON CONFLICT(task_id) DO UPDATE SET hash = excluded.hash",
        [(_section_key(k), _section_hash(doc.get(k))) for k in _SECTION_KEYS],
    )


def _sync_sections(conn: sqlite3.Connection, doc: dict) -> None:
    """Rebuild ``users`` only when its section changed.

    A whole-section table (no per-row identity we can diff cheaply), so it keeps
    the delete-and-reinsert shape — but pays it only when it has actually moved,
    instead of on every card write.

    ``notifications`` USED TO BE REBUILT HERE AND MUST NEVER BE AGAIN. See
    :data:`_SECTION_KEYS` for the measurement: the reinsert wrote 9 of 13
    columns, so an unrelated card write erased the delivery receipts and
    renumbered the queue. The rail owns that table now.
    """
    for key in _SECTION_KEYS:
        want = _section_hash(doc.get(key))
        row = conn.execute(
            f"SELECT hash FROM {HASH_TABLE} WHERE task_id = ?", (_section_key(key),)
        ).fetchone()
        if row and _sole_value(row) == want:
            continue
        conn.execute("DELETE FROM user_names")
        conn.execute("DELETE FROM users")
        _insert_users(conn, doc.get("users"))
        conn.execute(
            f"INSERT INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)"
            f" ON CONFLICT(task_id) DO UPDATE SET hash = excluded.hash",
            (_section_key(key), want),
        )


__all__ = [
    "HASH_TABLE",
    "mirror_doc_incremental",
]

# EOF
