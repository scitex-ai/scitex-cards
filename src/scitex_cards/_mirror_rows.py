#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-card row surgery: write one card, or remove one card, completely.

Extracted from :mod:`scitex_cards._db_mirror` (PURE MOVE -- no behaviour change),
which re-exports every name here so no importer moves.

These three functions are where the mirror touches ROWS. Two of them carry
ordering rules that are load-bearing rather than stylistic -- ``_write_card``
compares the revision BEFORE the destructive drop, and ``_delete_card`` clears
INBOUND edges that ``_drop_card_rows`` deliberately leaves alone. Both rules are
documented at the point they apply, and both were written after the failure they
prevent had already happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection


from ._db_bootstrap import _insert_tasks
from ._mirror_hashes import HASH_TABLE



def _drop_card_rows(conn: StoreConnection, task_id: str) -> None:
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
    conn: StoreConnection,
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


def _delete_card(conn: StoreConnection, task_id: str) -> None:
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


__all__ = ["_delete_card", "_drop_card_rows", "_write_card"]

# EOF
