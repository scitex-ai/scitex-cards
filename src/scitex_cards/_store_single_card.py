#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One card, same guards: the single-card read and write behind the one-card verbs.

WHY THIS MODULE EXISTS, measured rather than argued (2026-09-02, live primary,
6,542 cards, 15,773 comments, cProfile cumulative)::

    comment_task                                              3.013 s
    ├─ _read_canonical_db_or_raise            x2              2.048 s   whole-board export, twice
    └─ dispatch_notifications -> get_task                     1.221 s   whole-board export, a third time
    DB floor, same store:  connect 15.7 ms · SELECT 1 1.9 ms · one-card query 3.1 ms

Appending one dict to one card read the entire board three times, because every
CRUD verb was built on the read-modify-write cycle (``_read_write_doc`` ->
``_save_doc_unlocked``), whose read is :func:`_read_canonical_db_or_raise` — a
full ``export_doc`` plus a ``COUNT(*)`` cross-check. That cross-check is the
right guard for a caller that PRODUCES a whole document and writes it back; a
verb that touches one card never produces one, so it paid for a guard it could
not use, linearly in board size, on every write. Mission-card ruling 2
(operator, 2026-07-18): cost O(viewport), never O(corpus).

Re-measured through this path against the same primary: 272 ms, ``export_doc``
0 times. What remains is per-connection guard cost and event dispatch, tracked
on the card that opened this module.

WHAT IS THE SAME AS THE WHOLE-DOCUMENT PATH, deliberately:

  * THE GUARDS. Both functions open the store through
    :func:`scitex_cards._store_canonical_read._guarded_connection` — the ONE
    definition of exists / ownership / retired that the canonical read uses.
    There is no lenient single-card variant; the 2026-07-19 outage was the read
    door and the write door disagreeing, and that module's docstring forbids
    recreating it.
  * THE ROW WRITER. :func:`scitex_cards._mirror_rows._write_card` is the same
    primitive the incremental mirror calls per changed card. It merges the
    comment rows the payload has not heard of BEFORE its drop (the 2026-08-23
    loss), and it compares the revision BEFORE anything destructive runs.
  * THE HASH LEDGER. A written card's hash is recorded in ``mirror_hashes``
    exactly as the mirror records it, so the whole-document diff stays truthful
    about what changed.

WHAT IS DIFFERENT:

  * The read is ``SELECT ... WHERE id = ?`` — one row, decoded with the same
    ``card_from_payload`` the exporter uses per row. No merge with the rest of
    the board, because there is no rest of the board.
  * The write is a COMPARE-AND-SET on ``tasks.revision``. A lost race RAISES
    :class:`RevisionConflictError`; it cannot shrink anything, so the shrink
    guard has nothing to say here.
  * NOT via ``mirror_doc_incremental``. Its first-run branch rebuilds the whole
    store from the caller's document, and a one-card document there is the wipe
    class this package has shipped three times.

``target`` is the RESOLVED store target (``resolve_store_target(None)``) —
the same identity the whole-document read and write key on — never a caller's
sidecar path.
"""

from __future__ import annotations

from ._db_export import missing_payload_refusal
from ._db_payload import card_from_payload
from ._mirror_hashes import _HASH_DDL, HASH_TABLE, _card_hash
from ._mirror_rows import _write_card
from ._store_canonical_read import _guarded_connection
from ._store_errors import RevisionConflictError
from ._validate import WRITE_SOURCE, _validate_tasks

#: How many times an append-only verb re-reads and re-applies after losing a
#: compare-and-set race before it gives up and raises. Three is enough for two
#: agents commenting on the same card in the same second; a verb that loses
#: three in a row has a contended card, and the caller should hear about it.
CAS_ATTEMPTS = 3


def read_card_or_raise(target: str, task_id: str) -> tuple[dict | None, int | None]:
    """ONE card by id, through the canonical read's own guards.

    Returns ``(card, revision)`` — the card exactly as ``export_doc`` would
    have rebuilt it from ``card_json``, plus the ``tasks.revision`` it was read
    at, so a caller can hand that revision back to :func:`write_card_or_raise`
    as a compare-and-set. ``(None, None)`` means no row with that id; a
    tombstoned card is RETURNED (its payload says so) and the verb decides,
    exactly as ``_find_live_task`` decided on the whole-document path.

    RAISES rather than answering softly on every failure the canonical read
    raises on: unreachable server, no ``tasks`` table, stamped for another
    store, retired store, a row with no payload. A one-card read that answered
    ``None`` to any of those would turn "the store is broken" into "the card
    does not exist" — and ``comment_task`` turns that into ``TaskNotFoundError``,
    which is a lie the caller cannot detect.
    """
    conn = _guarded_connection(target)
    try:
        row = conn.execute(
            "SELECT card_json, revision, last_activity FROM tasks WHERE id = ?",
            (str(task_id),),
        ).fetchone()
        if row is None:
            return None, None
        if row["card_json"] is None:
            raise missing_payload_refusal(str(task_id), row["last_activity"])
        return card_from_payload(row["card_json"]), row["revision"]
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def write_card_or_raise(
    target: str, card: dict, *, expected_revision: int | None
) -> dict:
    """Write ONE card back, as a compare-and-set on the revision it was read at.

    Same guards as the read (one definition, see the module docstring), same
    validator the whole-document writer runs (``_validate_tasks`` over the one
    card), same row primitive the mirror uses (:func:`_write_card`, which merges
    table-only comment rows before its drop and refuses on a stale revision
    BEFORE dropping anything), then the same hash-ledger upsert.

    ``expected_revision`` is the value :func:`read_card_or_raise` returned.
    ``None`` is accepted for a caller that has explicitly chosen last-writer-
    wins; the verbs in this package do not, and should not, because a lost
    update is invisible and an exception is not.

    RAISES :class:`RevisionConflictError` on a lost race, with the revision the
    store holds now, and NOTHING has been written when it does — the drop of the
    card's derived rows never ran. Every other failure propagates from the
    layer that saw it; nothing here is swallowed, because this is the write
    door and the one direction it must never move is quieter.

    The driver opens the transaction on the first statement and the
    ``WHERE tasks.revision = ?`` inside ``_insert_tasks`` is what makes the
    compare-and-set atomic; ``commit`` below is what ends it.

    Returns the row counts ``_write_card`` returns.
    """
    task_id = str(card.get("id") or "")
    if not task_id:
        raise ValueError("write_card_or_raise: the card has no 'id'")
    _validate_tasks([card], source=WRITE_SOURCE, strict=True)
    conn = _guarded_connection(target)
    try:
        counts = _write_card(conn, card, expected_revision=expected_revision)
        if counts.get("revision_skipped"):
            conn.rollback()
            raise RevisionConflictError(
                task_id,
                -1 if expected_revision is None else expected_revision,
                counts.get("revision_found"),
            )
        # Record the hash of what was WRITTEN — the merged card, since
        # ``_write_card`` folds table-only comment rows into ``card`` in place —
        # so the whole-document mirror's diff still knows this card's stored
        # content. The DDL is the mirror's own CREATE TABLE IF NOT EXISTS; it is
        # what the mirror runs on every write, and it is a no-op after the first.
        conn.execute(_HASH_DDL)
        conn.execute(
            f"INSERT INTO {HASH_TABLE}(task_id, hash) VALUES (?, ?)"
            f" ON CONFLICT(task_id) DO UPDATE SET hash = excluded.hash",
            (task_id, _card_hash(card)),
        )
        conn.commit()
        return counts
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — the original error is the one to raise
            pass
        raise
    finally:
        conn.close()


__all__ = ["CAS_ATTEMPTS", "read_card_or_raise", "write_card_or_raise"]

# EOF
