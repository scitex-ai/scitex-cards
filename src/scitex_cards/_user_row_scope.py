#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-user ROW scope for the shared-fleet card database.

WHY THIS MODULE EXISTS
----------------------
Under the 0.52 postgres cutover there is ONE canonical card database; the
per-user ``request.scitex_store`` attribute the hub injects is a *label*, not
a data boundary. So the defect behind the P0 tenancy leak is that the card
READ path (``_model`` → ``_read_canonical_db_or_raise``) loads the whole fleet
and returns every row to whoever is authenticated.

The USER-SCOPE boundary (:mod:`scitex_cards._django._user_scope`) already
answers *who* a request acts as. This module answers *which rows* that user
may see in the shared DB — the row predicate the read path must apply.

Semantics ("own cards", operator directive 「他人のカードを…ヤバすぎる」):
a card row is visible to a user if the user is the creator OR the actor. That
is the complement of "someone else's card" — a user must never see a card they
neither filed nor act on. The three ownership fields are read from the row and
matched against the principal:

  * ``created_by`` — the user (agent or human) who filed the card
  * ``assignee``   — the legacy single assignee
  * ``agent``      — the operator-co-designed owning agent

Staff bypass: a staff principal sees the full set (the control the hub's
regression test mirrors). This is a pure predicate — it takes already-loaded
row dicts and a principal and returns the visible rows. It has NO database,
DSN, or Django dependency, so it is unit-testable hermetically and is the exact
unit the read path wires in next.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: The row fields that mark a card as "owned by" a principal. A card is visible
#: to user U if U appears in ANY of these fields. Kept as data (not scattered
#: string literals) so the boundary is one named place and a test can pin it.
OWNED_FIELDS: tuple[str, ...] = ("created_by", "assignee", "agent")


def user_owns_row(row: Mapping[str, Any], principal: str) -> bool:
    """True if ``principal`` owns ``row`` — the per-user visibility predicate.

    A row is owned if the principal is the creator, the assignee, or the agent.
    ``None``/missing fields are simply not a match; an empty principal matches
    nothing (fail closed: a request that cannot resolve a user sees no cards).
    """
    if not principal:
        return False
    for field in OWNED_FIELDS:
        value = row.get(field)
        if value is not None and value == principal:
            return True
    return False


def scope_rows_for_user(
    rows: Sequence[Mapping[str, Any]],
    principal: str,
    *,
    is_staff: bool = False,
) -> list[Mapping[str, Any]]:
    """Return the subset of ``rows`` visible to ``principal``.

    ``is_staff=True`` returns the full set (the staff control). Otherwise only
    rows the principal owns (creator / assignee / agent) survive. Order and
    identity of the input rows are preserved — this filters, never reorders.
    """
    if is_staff:
        return list(rows)
    return [row for row in rows if user_owns_row(row, principal)]


def foreign_row_ids(
    rows: Sequence[Mapping[str, Any]],
    principal: str,
    *,
    is_staff: bool = False,
    id_field: str = "id",
) -> list[str]:
    """The ids of ``rows`` a non-staff ``principal`` must NOT see.

    This is the shape the hub's regression test needs: assert it is EMPTY for a
    user-scoped read. For staff it is trivially empty (staff see everything, so
    nothing is "foreign" to them).
    """
    if is_staff:
        return []
    visible = scope_rows_for_user(rows, principal, is_staff=is_staff)
    visible_ids = {row.get(id_field) for row in visible}
    return [
        str(row.get(id_field))
        for row in rows
        if row.get(id_field) not in visible_ids
    ]


__all__ = ["OWNED_FIELDS", "user_owns_row", "scope_rows_for_user", "foreign_row_ids"]
