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


from ._comment_ids import stamp_comment_id
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
        found = None if row is None else row["revision"]
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
    # THE DROP BELOW DELETES COMMENTS THE DOC MAY NOT KNOW ABOUT. Fill them in
    # first, or the write silently destroys them — see the docstring.
    _merge_unseen_comment_rows(conn, tid, card)
    _drop_card_rows(conn, tid)
    # _insert_tasks handles the task row + comments + edges + roles for each
    # card it is given, so a one-element list is exactly one card's worth.
    return _insert_tasks(conn, [card], expected_revision=expected_revision)


def _comment_fields(row) -> tuple:
    """Read a ``task_comments`` row BY NAME. The row TYPE varies by backend.

    POSITIONAL INDEXING IS A BUG HERE, and it shipped in 0.49.0. ``_db.py`` sets
    ``row_factory = sqlite3.Row``, which supports ``row[0]``, so every SQLite test
    passed. The PostgreSQL path (``_db_mirror.mirror_doc_incremental``) uses
    psycopg's DICT row factory, where ``row[0]`` is a lookup of the integer KEY
    ``0`` and raises ``KeyError: 0``. Production is PostgreSQL.

    It hid because ``_merge_unseen_comment_rows`` returns early when a card has no
    comment rows: the FIRST comment on a fresh card worked and every comment on a
    card with history failed, so a smoke test that comments once was green.

    Name-based access is the one form both row types accept, so this needs no
    backend branch. A source-scanning guard keeps positional access out.
    """
    return row["author"], row["ts"], row["kind"], row["text"]


def _merge_unseen_comment_rows(conn: StoreConnection, task_id: str, card: dict) -> int:
    """Fold comment rows the DOC does not carry back into it, before the drop.

    WHY THIS EXISTS, reproduced 2026-08-23 rather than reasoned about::

        card_json=1 / task_comments=2  ->  one ordinary update_task  ->  =1

    The row that only the TABLE knew about was gone, permanently, with no error
    and a success report. ``_write_card`` drops a card's comment rows and
    re-inserts from the doc, and the doc is assembled from ``card_json`` — so
    the write's INPUT is the denormalised copy while its EFFECT is on the table.
    Anything the copy has not heard of is deleted.

    That is not a rare race. It is the STEADY STATE for any card written on
    another host: ``_copy_comments`` delivers rows here keyed on
    ``(task_id, seq)``, while ``_copy_tasks`` computes ``src_ids - dst_ids`` and
    so never re-SELECTs an existing card row — meaning this host's ``card_json``
    never learns the comment arrived. Measured on this store the same day: 259
    cards holding 808 such comments, every one of them armed to vanish on the
    next local write. And the writer's own host looks perfect throughout, which
    is why nobody had noticed.

    THE TABLE LEADS, AND THE DOC ONLY ADDS. Rows are taken in ``seq`` order
    first, then any doc comment the table does not have is appended.

      * Taking the table first PRESERVES EXISTING ``seq`` VALUES.
        ``_insert_comments`` re-derives ``seq`` from list position, and
        ``_copy_comments`` matches peers on ``(task_id, seq)`` — so reordering
        here would make every peer see unfamiliar keys and re-insert
        DUPLICATES. Stability of that key is load-bearing, not cosmetic.
      * The doc still adds, because a comment being written by THIS call is in
        the doc and not yet in the table. Dropping to "just rebuild from the
        table" would discard the very comment the caller is saving.

    SAFE IN THE DIRECTION THAT MATTERS, measured before writing this: across
    5,856 cards there were ZERO where ``card_json`` held a comment the table
    lacked, and 259 the other way. So folding the table in can only ADD. Had the
    dangerous direction been non-empty this function would be destructive in a
    new way, which is exactly why that count was taken first.

    Matching is on ``(author, ts, kind, text)`` as a MULTISET — two genuinely
    identical comments stay two, rather than one silently absorbing the other.
    ``id`` is not usable: ``task_comments`` has no column for the document's
    ``c_*`` id, and its surrogate key is re-minted on every write.

    Returns the number of rows recovered (0 when the doc was already complete),
    so a caller or test can assert on it.
    """
    rows = conn.execute(
        "SELECT author, ts, kind, text FROM task_comments "
        "WHERE task_id = ? ORDER BY seq",
        (task_id,),
    ).fetchall()
    if not rows:
        return 0

    doc = card.get("comments")
    doc = list(doc) if isinstance(doc, list) else []

    def _key(author, ts, kind, text):
        return (author, ts, kind, "" if text is None else str(text))

    # THE DOC'S OWN DICT WINS WHENEVER IT MATCHES, and that is not a detail.
    # `task_comments` has no column for the document's `c_*` comment id, so a
    # row rebuilt from the table alone comes back WITHOUT one. Stripping ids
    # from every comment on every write would be a fresh defect of exactly the
    # kind already on the board (`cards-comments-need-globally-unique-ids-
    # before-append`), so a matched table row is represented by the doc's
    # version, which carries the id and any other fields the table does not
    # model.
    by_key: dict = {}
    for comment in doc:
        if isinstance(comment, dict):
            by_key.setdefault(
                _key(
                    comment.get("author"),
                    comment.get("ts"),
                    comment.get("kind"),
                    comment.get("text"),
                ),
                [],
            ).append(comment)

    merged = []
    recovered = 0
    for row in rows:
        author, ts, kind, text = _comment_fields(row)
        key = _key(author, ts, kind, text)
        pending = by_key.get(key)
        if pending:
            merged.append(pending.pop(0))
        else:
            # MINTED, not hand-built. `task_comments` has no id column, so a
            # recovered row arrives WITHOUT one, and an id-less element is
            # exactly what `comments[]` may not contain: APPEND unions by
            # element id, and an element with no id can only be matched by
            # POSITION — which is what diverges when two hosts append at once
            # (card cards-comments-need-globally-unique-ids-before-append).
            # A source-scanning guard in test__comment_ids.py caught this
            # dict literal escaping the helper, which is the guard working.
            #
            # The id is fresh rather than the writer's original, because the
            # original never crossed — the table does not carry it. It is
            # stable from then on: once this heal lands in card_json, later
            # merges match on (author, ts, kind, text) and keep the doc's dict,
            # id included.
            merged.append(
                stamp_comment_id(
                    {"author": author, "ts": ts, "kind": kind, "text": text}
                )
            )
            recovered += 1

    # Whatever the doc still holds is new in THIS write — the comment the
    # caller is saving — and belongs after the rows already on disk.
    for leftover in by_key.values():
        merged.extend(leftover)

    card["comments"] = merged
    return recovered


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


__all__ = [
    "_delete_card",
    "_drop_card_rows",
    "_merge_unseen_comment_rows",
    "_write_card",
]

# EOF
