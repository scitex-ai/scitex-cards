#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The PURE-read door: survive one unreadable row, never write anything back.

WHY A SECOND DOOR AT ALL

`list_tasks` and `add_task` both reach `_read_canonical_db_or_raise`, and that
function is the read half of a READ-MODIFY-WRITE: whatever it returns is
written back as the whole store. Its `COUNT(*)` cross-check exists for exactly
that reason, and its own comment says so —

    "An export that silently under-reports is the total-loss case, BECAUSE THE
     DIFFERENCE IS DELETED ON WRITE-BACK."

That premise is the whole justification, and **it does not hold for a caller
that never writes the document back**. `list_tasks` cannot delete anything. It
is refused today only because of the door it travels: one unreadable `users`
row blanks every card query fleet-wide, and `help_wait` — the card an agent
files to say it is stuck — is refused at precisely the moment it is needed.

WHY THIS IS NOT "MAKE THE READ TOLERANT"

The tolerance MUST NOT reach the write door. Measured 2026-08-17: making the
users loop tolerant and running one `comment_task` reported SUCCESS and left
the row GONE. `tests/…/test__rmw_refusal_must_not_become_tolerance.py` pins
that, and this module must leave those tests green.

HOW THIS AVOIDS DUPLICATING THE GUARDS

It does not re-implement the ownership / retired / missing-store guards, and it
does not skip them. It calls the STRICT door first and returns its answer
unchanged — so the happy path is byte-identical to today, with every guard and
the full completeness verification. Only when the strict door raises
`ExportRefused` — the case where a caller gets NOTHING today — does it retry
with `on_unrebuildable="omit"`.

That ordering is what makes it safe to reason about:

  * the retry runs only after exists/ownership/retired have already passed,
    because `ExportRefused` is raised downstream of all three;
  * it cannot mask a different fault, since any other exception propagates;
  * it cannot hide a healthy store degrading, because a store with no
    unreadable rows never reaches the retry at all.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["read_doc_tolerating_unreadable_rows"]


def read_doc_tolerating_unreadable_rows() -> dict:
    """The canonical document, with unreadable rows OMITTED and NAMED.

    Returns the strict read when the store is healthy — same object, same
    guards, same verification. Falls back to a tolerant export only when the
    strict read refuses over a row that cannot be rebuilt.

    NEVER call this from a path that writes the document back. Use
    :func:`scitex_cards._store._read_canonical_db_or_raise` there, which is
    what refuses.
    """
    from ._db import open_db
    from ._db_export import ExportRefused, export_doc
    from ._store import _read_canonical_db_or_raise

    try:
        return _read_canonical_db_or_raise()
    except ExportRefused as exc:
        logger.warning(
            "[scitex-cards] the store holds at least one UNREADABLE row, so "
            "this read-only query is being served with those rows OMITTED and "
            "named individually below. Nothing was deleted, and a WRITE will "
            "still refuse until the row is repaired — that refusal is "
            "deliberate, because a write would delete what it omitted. "
            "First refusal: %s",
            exc,
        )

    # Reached ONLY after the strict door already passed exists / ownership /
    # retired and failed at the export itself, so the store is proven to be
    # ours before we read a byte of it here.
    conn = open_db(None)
    try:
        return export_doc(conn=conn, on_unrebuildable="omit")[0]
    finally:
        conn.close()


# EOF
