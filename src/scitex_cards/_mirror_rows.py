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
from uuid import uuid4

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection


from ._card_row_errors import CardAlreadyExistsError
from ._comment_ids import stamp_comment_id
from ._db_bootstrap import _insert_task_children, _insert_tasks
from ._delivery_policy import ABSENT, delivery_namespace
from ._mirror_hashes import HASH_TABLE
from ._store_errors import RevisionConflictError


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
    insert_if_absent: bool = False,
) -> dict[str, int]:
    """Write one parent and replace only its owned derived rows.

    Read the canonical object and apply the existing delivery guard on this
    transaction. Explicit revisions use UPDATE-only CAS. Explicit absence uses
    INSERT DO NOTHING. Legacy None locks an existing parent, or fences insertion
    when no parent was read, so enrollment cannot race an unconditional UPSERT.

    Only the parent winner drops comments, roles and outbound edges. Recover
    table-only comments before that drop using the existing merge rule. A row
    savepoint rolls back late skips and child-write failures even when a mirror
    caller catches a refusal and continues its transaction. Protected revision
    losses remain typed exceptions; legacy revision losses return skip counts.
    """
    savepoint = "cards_row_" + uuid4().hex
    conn.execute("SAVEPOINT " + savepoint)
    try:
        counts = _write_card_in_savepoint(
            conn,
            card,
            expected_revision=expected_revision,
            insert_if_absent=insert_if_absent,
        )
        if counts.get("revision_skipped") or counts.get("absence_skipped"):
            conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
        return counts
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
        raise
    finally:
        conn.execute("RELEASE SAVEPOINT " + savepoint)


def _write_card_in_savepoint(conn, card, *, expected_revision, insert_if_absent):
    from ._store_delivery import _guard_row_write
    from ._store_single_card import _read_card_with_connection

    tid = str(card.get("id"))
    before, found = _read_card_with_connection(
        conn,
        tid,
        for_update=expected_revision is None,
    )
    if insert_if_absent and before is not None:
        return {
            "tasks": 0,
            "comments": 0,
            "edges": 0,
            "roles": 0,
            "absence_skipped": 1,
            "revision_found": found,
        }
    expected_revision = _guard_row_write(conn, before, card, found, expected_revision)
    if expected_revision is not None:
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
    # Recover table-only comments before claiming the parent. This read and
    # in-memory merge do not mutate the derived rows of a losing write.
    _merge_unseen_comment_rows(conn, tid, card)
    # Claim exactly one parent first; only the winner replaces its children.
    counts = _insert_tasks(
        conn,
        [card],
        expected_revision=expected_revision,
        insert_if_absent=insert_if_absent
        or (before is None and expected_revision is None),
        include_children=False,
    )
    if counts.get("absence_skipped") and not insert_if_absent:
        raise CardAlreadyExistsError(tid, counts.get("revision_found"))
    protected = delivery_namespace(before or {}) is not ABSENT
    if counts.get("revision_skipped") and protected:
        # Keep the existing protected-row refusal visible to its caller;
        # legacy mirror callers instead receive a harmless skip outcome.
        raise RevisionConflictError(
            tid, expected_revision, counts.get("revision_found")
        )
    if not counts.get("revision_skipped") and not counts.get("absence_skipped"):
        _drop_card_rows(conn, tid)
        counts.update(_insert_task_children(conn, card))
    return counts


def _comment_fields(row) -> tuple:
    """Read a ``task_comments`` row BY NAME. The row TYPE varies by backend.

    POSITIONAL INDEXING IS A BUG HERE, and it shipped in 0.49.0. The row factory
    it was written against supported ``row[0]``, so every test on that backend
    passed. The store path (``_db_mirror.mirror_doc_incremental``) uses
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


def _delete_card(
    conn: StoreConnection, task_id: str, *, expected_revision: int | None = None
) -> bool:
    """A card that left the doc must leave the mirror COMPLETELY.

    Also drops edges pointing AT it, which ``_drop_card_rows`` deliberately does
    not (that one is for re-writing a card, which owns only its OUTBOUND edges).
    A dangling inbound edge to a card that no longer exists is exactly the kind
    of rot an equivalence check on PRESENT cards would never notice.

    Any present delivery namespace refuses before effects. Delete the parent
    only at the observed (or explicitly supplied) revision before child/hash
    cleanup, so enrollment after the policy read cannot be physically removed.
    """
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValueError("expected_revision must be a non-negative integer")
    savepoint = "cards_delete_" + uuid4().hex
    conn.execute("SAVEPOINT " + savepoint)
    try:
        return _delete_card_in_savepoint(conn, task_id, expected_revision)
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
        raise
    finally:
        conn.execute("RELEASE SAVEPOINT " + savepoint)


def _delete_card_in_savepoint(conn, task_id, expected_revision):
    from ._store_delivery import _guard_removal
    from ._store_single_card import _read_card_with_connection

    before, revision = _read_card_with_connection(conn, task_id)
    _guard_removal(before)
    if before is None:
        return False
    expected = revision if expected_revision is None else expected_revision
    removed = conn.execute(
        "DELETE FROM tasks WHERE id = ? AND revision = ? RETURNING id",
        (task_id, expected),
    ).fetchone()
    if removed is None:
        latest = conn.execute(
            "SELECT revision FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        raise RevisionConflictError(
            task_id, expected, None if latest is None else latest["revision"]
        )
    _drop_card_rows(conn, task_id)
    conn.execute("DELETE FROM task_edges WHERE dst_task_id = ?", (task_id,))
    from ._mirror_hashes import _HASH_DDL

    conn.execute(_HASH_DDL)
    conn.execute(f"DELETE FROM {HASH_TABLE} WHERE task_id = ?", (task_id,))
    return True


__all__ = [
    "_delete_card",
    "_drop_card_rows",
    "_merge_unseen_comment_rows",
    "_write_card",
]

# EOF
