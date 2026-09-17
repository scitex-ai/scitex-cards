#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded, SQL-pushed reads for the PROJECT-SCOPED board.

WHY THIS MODULE EXISTS. The board page first shipped reading through the same
path the fleet board uses (`_get_board` → `load_doc` → the WHOLE store document),
then filtering in Python. Measured 2026-09-17 on the shared store: `GET /projects`
took **72.7s cold / 32.7s warm** for a viewer with 9 projects, and the store read
alone measured 15.6s for 7,819 rows before 0.2s of JSON — i.e. the page's cost was
overwhelmingly "load everything, then throw most of it away".

The Hub gate is explicit (scitex-hub, 2026-09-17): do not merge while `/projects`
is 72.7s/32.7s; target <=3s cold, <=1.5s warm; "fix in leaf store/query:
server-side user+project predicate, indexes, bounded fields/pagination; never
load/filter the global 24MB graph."

WHAT THIS DOES. Two indexed queries, no document load, no graph:

  1. the viewer's project LIST — `SELECT DISTINCT project` under the tenancy
     predicate, so the picker cannot even name a project the viewer has no card in.
  2. ONE project's rows, bounded to the fields a board card shows, with a LIMIT.

INDEXES: none were added, and that is a finding rather than a shortcut — the
schema already indexes exactly the columns this predicate and ordering use
(`idx_tasks_project`, `idx_tasks_assignee`, `idx_tasks_agent`; see
``_db_schema_sql.py``). If the planner still chooses a sequential scan, the honest
fix is a measurement (`EXPLAIN`), not more DDL on a shared store.

THE TENANCY PREDICATE IS BUILT FROM THE ONE DEFINITION. The ``WHERE`` clause is
generated from :data:`scitex_cards._user_row_scope.OWNED_FIELDS`, the same tuple
the Python predicate reads, so the SQL and the in-memory rule cannot drift into
two different answers to "whose card is this".
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional

from ._user_row_scope import OWNED_FIELDS

logger = logging.getLogger(__name__)

#: The fields a board card renders. Deliberately NOT ``*`` and deliberately not the
#: payload blob: `note` and `comments[]` are what made the full document 61.6 MB
#: (measured), and a board column shows none of them.
BOARD_ROW_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "status",
    "project",
    "assignee",
    "agent",
    "created_by",
    "priority",
)

#: Most rows one project page will render. A project with more cards than this
#: gets a bounded page and a count, never a payload that grows with the fleet.
DEFAULT_ROW_LIMIT = 500

_DELETED_FILTER = "COALESCE(is_deleted, false) = false"


def _tenancy_clause() -> str:
    """``AND ( created_by = ? OR assignee = ? OR agent = ? )`` — from OWNED_FIELDS.

    ``?`` and not ``%s``: the package's own convention (``to_paramstyle`` in
    ``_backend_connect`` translates ``?`` to whatever the driver wants), and the
    placeholders have to be the driver-independent spelling for that translation
    to have anything to do.
    """
    terms = " OR ".join(f"{field} = ?" for field in OWNED_FIELDS)
    return f" AND ( {terms} )"


def projects_sql(*, is_staff: bool = False) -> str:
    """SQL for the picker's project list, under the viewer's tenancy."""
    clause = "" if is_staff else _tenancy_clause()
    return (
        "SELECT DISTINCT project FROM tasks "
        f"WHERE project IS NOT NULL AND project <> '' AND {_DELETED_FILTER}{clause} "
        "ORDER BY project"
    )


def rows_sql(*, is_staff: bool = False) -> str:
    """SQL for ONE project's cards: bounded fields, tenancy, ordering, LIMIT."""
    clause = "" if is_staff else _tenancy_clause()
    fields = ", ".join(BOARD_ROW_FIELDS)
    return (
        f"SELECT {fields} FROM tasks "
        f"WHERE project = ? AND {_DELETED_FILTER}{clause} "
        "ORDER BY priority NULLS LAST, updated_at DESC NULLS LAST, id "
        "LIMIT ? OFFSET ?"
    )


def count_sql(*, is_staff: bool = False) -> str:
    """How many cards this viewer has in this project — the number the page needs.

    WITHOUT THIS the bounded page lies: it can show at most ``limit`` rows, and a
    page that renders "500 of 500" for a project holding 1,200 cards is not a
    bounded view, it is a wrong one. The count is a separate cheap aggregate over
    the same indexed predicate, and the page says "showing X-Y of N".
    """
    clause = "" if is_staff else _tenancy_clause()
    return f"SELECT COUNT(*) FROM tasks WHERE project = ? AND {_DELETED_FILTER}{clause}"


def projects_params(principal: str, *, is_staff: bool = False) -> tuple:
    """Parameters for :func:`projects_sql` — the principal repeated per owned field."""
    if is_staff:
        return ()
    return tuple(principal for _ in OWNED_FIELDS)


def rows_params(
    project: str,
    principal: str,
    *,
    is_staff: bool = False,
    limit: int = DEFAULT_ROW_LIMIT,
    offset: int = 0,
) -> tuple:
    """Parameters for :func:`rows_sql`, in the order the SQL expects them.

    The offset is passed to the DATABASE, not applied by slicing a larger fetch:
    a page that pulls 500 rows and shows the last 50 has still paid for 500, which
    is the cost this module exists to remove.
    """
    base: tuple = (project,)
    if not is_staff:
        base += tuple(principal for _ in OWNED_FIELDS)
    return base + (limit, offset)


def count_params(project: str, principal: str, *, is_staff: bool = False) -> tuple:
    """Parameters for :func:`count_sql` — no limit, because a count is not paged."""
    base: tuple = (project,)
    if not is_staff:
        base += tuple(principal for _ in OWNED_FIELDS)
    return base


def _default_connect(store: Any = None):
    """A READ-ONLY connection to the resolved store.

    Read-only on purpose: this module only reads, and asking for a read handle
    means a bug here cannot write. ``open_read_db`` is the same resolution the
    rest of the package uses, so the page and the CLI cannot disagree about which
    store they are looking at.
    """
    from ._db import open_read_db

    return open_read_db(store)


def _fetch(sql: str, params: Iterable[Any], connect: Optional[Callable[..., Any]] = None, store: Any = None) -> list:
    """Run one bounded query and return plain rows (dicts where possible)."""
    opener = connect or _default_connect
    with opener(store) as conn:
        return list(conn.fetchall(sql, tuple(params)))


def projects_for(
    principal: str,
    *,
    store: Any = None,
    is_staff: bool = False,
    connect: Optional[Callable[..., Any]] = None,
) -> list[str]:
    """The distinct projects this viewer has a card in, sorted.

    Sorted and DISTINCT because a picker whose order changes between two renders
    is a picker the reader cannot build muscle memory for.
    """
    sql = projects_sql(is_staff=is_staff)
    params = projects_params(principal, is_staff=is_staff)
    rows = _fetch(sql, params, connect=connect, store=store)
    found = []
    for row in rows:
        value = row["project"] if hasattr(row, "keys") else row[0]
        if value:
            found.append(str(value))
    return found


def rows_for(
    project: str,
    principal: str,
    *,
    store: Any = None,
    is_staff: bool = False,
    limit: int = DEFAULT_ROW_LIMIT,
    offset: int = 0,
    connect: Optional[Callable[..., Any]] = None,
) -> list[dict]:
    """ONE project's authorized cards, bounded to the fields a card shows.

    Returns plain dicts so the state machine and the template do not depend on
    the driver's row type.
    """
    if not project:
        return []
    sql = rows_sql(is_staff=is_staff)
    params = rows_params(project, principal, is_staff=is_staff, limit=limit, offset=offset)
    rows = _fetch(sql, params, connect=connect, store=store)
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({field: row[field] for field in BOARD_ROW_FIELDS if field in row.keys()})
        else:  # positional rows (a driver without rows_by_name)
            out.append(dict(zip(BOARD_ROW_FIELDS, row)))
    return out


def count_for(
    project: str,
    principal: str,
    *,
    store: Any = None,
    is_staff: bool = False,
    connect: Optional[Callable[..., Any]] = None,
) -> int:
    """How many cards this viewer has in this project, indexed and exact."""
    if not project:
        return 0
    rows = _fetch(
        count_sql(is_staff=is_staff),
        count_params(project, principal, is_staff=is_staff),
        connect=connect,
        store=store,
    )
    if not rows:
        return 0
    first = rows[0]
    value = first["count"] if hasattr(first, "keys") else first[0]
    return int(value or 0)


#: Most comments one card page will render. `task_comments` is an append-only log
#: (a long-lived card can carry hundreds of entries), so the read is bounded for
#: the same reason the card page is: a page whose size depends on how long the
#: conversation has been running is a page that eventually stops loading.
DEFAULT_COMMENT_LIMIT = 200


def comments_sql() -> str:
    """ONE card's comments, by the index that exists for exactly this read.

    ``task_comments`` is keyed ``(task_id, seq)`` (``idx_comments_task``) and the
    card's comments are carried in that CHILD table, not in the tasks row — which is
    why the board's list projection deliberately does not ask for them: the sibling
    complaint ``cards-the-board-list-payload-should-not-carry-17511-comment-bodies``
    is about exactly that cost. This query is for the DETAIL page, one card at a
    time, and it is bounded.
    """
    return (
        "SELECT author, ts, text FROM task_comments "
        "WHERE task_id = ? AND deleted_at IS NULL "
        "ORDER BY seq LIMIT ?"
    )


def comments_params(card_id: str, *, limit: int = DEFAULT_COMMENT_LIMIT) -> tuple:
    """Parameters for :func:`comments_sql`."""
    return (card_id, limit)


def comments_for(
    card_id: str,
    *,
    store: Any = None,
    limit: int = DEFAULT_COMMENT_LIMIT,
    connect: Optional[Callable[..., Any]] = None,
) -> list[dict]:
    """One card's comments, newest LAST (the order they were written in).

    No tenancy predicate here, and that is deliberate rather than an omission: the
    caller only ever asks for a card id it has ALREADY resolved through the
    tenancy-scoped board query (``update_card``/``comment_card`` both refuse an id
    that is not in ``state.rows``). A second predicate here would be a second
    definition of the boundary, which is the drift this codebase keeps paying for.
    """
    if not card_id:
        return []
    rows = _fetch(
        comments_sql(), comments_params(card_id, limit=limit), connect=connect, store=store
    )
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({"author": row["author"], "ts": row["ts"], "text": row["text"]})
        else:
            out.append({"author": row[0], "ts": row[1], "text": row[2]})
    return out
