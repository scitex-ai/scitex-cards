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
    _insert_notifications,
    _insert_tasks,
    _insert_users,
    _rebuild_from_doc,
)
from ._db_freshness import stamp_store_provenance

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
_SECTION_KEYS = ("users", "inboxes")


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
    return {r[0]: r[1] for r in rows}


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


def _write_card(conn: sqlite3.Connection, card: dict) -> None:
    """Upsert ONE card and its derived rows."""
    tid = str(card.get("id"))
    _drop_card_rows(conn, tid)
    # _insert_tasks handles the task row + comments + edges + roles for each
    # card it is given, so a one-element list is exactly one card's worth.
    _insert_tasks(conn, [card])


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
                f"INSERT OR REPLACE INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)",
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
                f"INSERT OR REPLACE INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)",
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
        f"INSERT OR REPLACE INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)",
        [(_section_key(k), _section_hash(doc.get(k))) for k in _SECTION_KEYS],
    )


def _sync_sections(conn: sqlite3.Connection, doc: dict) -> None:
    """Rebuild ``users`` / ``notifications`` only when their section changed.

    These are whole-section tables (no per-row identity we can diff cheaply), so
    they keep the delete-and-reinsert shape — but they now pay it only when they
    have actually moved, instead of on every card write.
    """
    for key in _SECTION_KEYS:
        want = _section_hash(doc.get(key))
        row = conn.execute(
            f"SELECT hash FROM {HASH_TABLE} WHERE task_id = ?", (_section_key(key),)
        ).fetchone()
        if row and row[0] == want:
            continue
        if key == "users":
            conn.execute("DELETE FROM user_names")
            conn.execute("DELETE FROM users")
            _insert_users(conn, doc.get("users"))
        else:  # inboxes -> notifications
            conn.execute("DELETE FROM notifications")
            _insert_notifications(conn, doc.get("inboxes"))
        conn.execute(
            f"INSERT OR REPLACE INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)",
            (_section_key(key), want),
        )


__all__ = [
    "HASH_TABLE",
    "mirror_doc_incremental",
]

# EOF
