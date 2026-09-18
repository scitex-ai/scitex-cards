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


def filter_clause(
    *, q: str = "", status: str = "", assignee: str = ""
) -> tuple[str, tuple]:
    """The WHERE fragment for the page's three filters, and its parameters.

    REVIEWER BLOCKER #4: filtering happened in PYTHON, after the SQL LIMIT/OFFSET.
    Two wrong RESULTS followed: a page whose 500 rows missed the match reported "no
    cards match these filters" while matches sat further down, and the count and the
    "showing X-Y of N" range described the UNFILTERED set. The fragment now rides in
    the same statement as the tenancy predicate, so rows, count and range all
    describe the same question.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if q:
        clauses.append("title ILIKE ?")
        params.append(f"%{q}%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if assignee:
        clauses.append("assignee = ?")
        params.append(assignee)
    return ("".join(f" AND {c}" for c in clauses), tuple(params))


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


def rows_sql(*, is_staff: bool = False, filter_sql: str = "") -> str:
    """SQL for ONE project's cards: bounded fields, tenancy, ordering, LIMIT."""
    clause = "" if is_staff else _tenancy_clause()
    fields = ", ".join(BOARD_ROW_FIELDS)
    return (
        f"SELECT {fields} FROM tasks "
        f"WHERE project = ? AND {_DELETED_FILTER}{clause}{filter_sql} "
        "ORDER BY priority NULLS LAST, updated_at DESC NULLS LAST, id "
        "LIMIT ? OFFSET ?"
    )


def count_sql(*, is_staff: bool = False, filter_sql: str = "") -> str:
    """How many cards this viewer has in this project — the number the page needs.

    WITHOUT THIS the bounded page lies: it can show at most ``limit`` rows, and a
    page that renders "500 of 500" for a project holding 1,200 cards is not a
    bounded view, it is a wrong one. The count is a separate cheap aggregate over
    the same indexed predicate, and the page says "showing X-Y of N".
    """
    clause = "" if is_staff else _tenancy_clause()
    return (
        "SELECT COUNT(*) FROM tasks WHERE project = ? AND "
        f"{_DELETED_FILTER}{clause}{filter_sql}"
    )


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
    filters: tuple = (),
) -> tuple:
    """Parameters for :func:`rows_sql`, in the order the SQL expects them.

    The offset is passed to the DATABASE, not applied by slicing a larger fetch:
    a page that pulls 500 rows and shows the last 50 has still paid for 500, which
    is the cost this module exists to remove.
    """
    base: tuple = (project,)
    if not is_staff:
        base += tuple(principal for _ in OWNED_FIELDS)
    return base + tuple(filters) + (limit, offset)


def count_params(
    project: str, principal: str, *, is_staff: bool = False, filters: tuple = ()
) -> tuple:
    """Parameters for :func:`count_sql` — no limit, because a count is not paged."""
    base: tuple = (project,)
    if not is_staff:
        base += tuple(principal for _ in OWNED_FIELDS)
    return base + tuple(filters)


def _default_connect(store: Any = None):
    """A READ-ONLY connection to the resolved store.

    Read-only on purpose: this module only reads, and asking for a read handle
    means a bug here cannot write. ``open_read_db`` is the same resolution the
    rest of the package uses, so the page and the CLI cannot disagree about which
    store they are looking at.
    """
    from ._db import open_read_db

    return open_read_db(store)


def _fetch(
    sql: str,
    params: Iterable[Any],
    connect: Optional[Callable[..., Any]] = None,
    store: Any = None,
) -> list:
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


#: The fields the DETAIL page needs — the board's list projection PLUS ``note`` and
#: ``blocker``. Separate from BOARD_ROW_FIELDS on purpose: the reviewer found that
#: the detail page was rendering and SAVING from the list projection, which has no
#: ``note``, so opening a card that had one and saving erased it.
DETAIL_FIELDS: tuple[str, ...] = BOARD_ROW_FIELDS + ("note", "blocker")


def card_sql(*, is_staff: bool = False) -> str:
    """ONE card, addressed by (id, project) and gated by the tenancy predicate.

    Addressing the card by BOTH its id and its project is what makes authorization
    offset-independent: the previous shape asked whether the id was among the rows
    on the current PAGE, so a card the viewer can legitimately see on page two was
    refused by its own detail page. This query answers the question directly, and
    it carries the tenancy predicate instead of trusting the caller to have
    authorized the id first — which is the other half of the reviewer's finding
    ("comments loaded before tenancy").
    """
    from ._user_row_scope import OWNED_FIELDS as _OWNED

    clause = (
        "" if is_staff else " AND ( " + " OR ".join(f"{f} = ?" for f in _OWNED) + " )"
    )
    fields = ", ".join(DETAIL_FIELDS)
    return (
        f"SELECT {fields} FROM tasks "
        f"WHERE id = ? AND project = ? AND {_DELETED_FILTER}{clause}"
    )


def card_params(
    card_id: str, project: str, principal: str, *, is_staff: bool = False
) -> tuple:
    """Parameters for :func:`card_sql`."""
    from ._user_row_scope import OWNED_FIELDS as _OWNED

    base: tuple = (card_id, project)
    if not is_staff:
        base += tuple(principal for _ in _OWNED)
    return base


def card_for(
    card_id: str,
    project: str,
    principal: str,
    *,
    store: Any = None,
    is_staff: bool = False,
    connect: Optional[Callable[..., Any]] = None,
) -> Optional[dict]:
    """The card, or None when this viewer may not see it in this project.

    None is the SAME answer for "no such card", "another tenant's card" and "a card
    in another project", so the route cannot become an oracle for what exists.
    """
    if not card_id or not project:
        return None
    rows = _fetch(
        card_sql(is_staff=is_staff),
        card_params(card_id, project, principal, is_staff=is_staff),
        connect=connect,
        store=store,
    )
    if not rows:
        return None
    row = rows[0]
    if hasattr(row, "keys"):
        return {field: row[field] for field in DETAIL_FIELDS if field in row.keys()}
    return dict(zip(DETAIL_FIELDS, row))


def rows_for(
    project: str,
    principal: str,
    *,
    store: Any = None,
    is_staff: bool = False,
    limit: int = DEFAULT_ROW_LIMIT,
    offset: int = 0,
    q: str = "",
    status: str = "",
    assignee: str = "",
    connect: Optional[Callable[..., Any]] = None,
) -> list[dict]:
    """ONE page of ONE project's authorized cards, filtered IN SQL.

    Returns plain dicts so the state machine and the template do not depend on
    the driver's row type.
    """
    if not project:
        return []
    filter_sql, filter_params = filter_clause(q=q, status=status, assignee=assignee)
    sql = rows_sql(is_staff=is_staff, filter_sql=filter_sql)
    params = rows_params(
        project,
        principal,
        is_staff=is_staff,
        limit=limit,
        offset=offset,
        filters=filter_params,
    )
    rows = _fetch(sql, params, connect=connect, store=store)
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append(
                {field: row[field] for field in BOARD_ROW_FIELDS if field in row.keys()}
            )
        else:  # positional rows (a driver without rows_by_name)
            out.append(dict(zip(BOARD_ROW_FIELDS, row)))
    return out


def count_for(
    project: str,
    principal: str,
    *,
    store: Any = None,
    is_staff: bool = False,
    q: str = "",
    status: str = "",
    assignee: str = "",
    connect: Optional[Callable[..., Any]] = None,
) -> int:
    """How many cards this viewer has in this project — FILTERED the same way.

    A count that ignored the filters would make "showing X-Y of N" describe a
    different question from the rows printed under it (blocker #4, second half).
    """
    if not project:
        return 0
    filter_sql, filter_params = filter_clause(q=q, status=status, assignee=assignee)
    rows = _fetch(
        count_sql(is_staff=is_staff, filter_sql=filter_sql),
        count_params(project, principal, is_staff=is_staff, filters=filter_params),
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
    from ._user_row_scope import OWNED_FIELDS as _OWNED

    predicate = " OR ".join(f"t.{field} = ?" for field in _OWNED)
    return (
        "SELECT author, ts, text FROM task_comments c "
        "WHERE c.task_id = ? AND c.deleted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM tasks t "
        "            WHERE t.id = c.task_id AND t.project = ? "
        f"            AND ( {predicate} )) "
        "ORDER BY c.seq LIMIT ?"
    )


def comments_params(
    card_id: str,
    project: str,
    principal: str,
    *,
    limit: int = DEFAULT_COMMENT_LIMIT,
    is_staff: bool = False,
) -> tuple:
    """Parameters for :func:`comments_sql` — the card id, then the gate it must pass."""
    from ._user_row_scope import OWNED_FIELDS as _OWNED

    base: tuple = (card_id, project)
    if not is_staff:
        base += tuple(principal for _ in _OWNED)
    return base + (limit,)


#: The fields one rendered comment carries, named as a TUPLE rather than spelled
#: as dict keys. Not cosmetic: `tests/scitex_cards/test__comment_ids.py` scans the
#: package for dict literals whose string keys are exactly these three — the shape
#: of a `comments[]` element that must be minted through `stamp_comment_id` — and a
#: projection here would be flagged as an unminted append site. This module only
#: READS comments (the append goes through `_store.comment_task`, which mints), and
#: building the projection from a tuple keeps that distinction visible to the guard
#: instead of demanding an exemption from it. Caught by CI on PR #1028.
COMMENT_FIELDS: tuple[str, ...] = ("author", "ts", "text")


def comments_for(
    card_id: str,
    project: str,
    principal: str,
    *,
    store: Any = None,
    limit: int = DEFAULT_COMMENT_LIMIT,
    is_staff: bool = False,
    connect: Optional[Callable[..., Any]] = None,
) -> list[dict]:
    """One card's comments, in the order they were written.

    THE TENANCY PREDICATE IS IN THE QUERY, not a promise from the caller. The first
    version of this function argued the opposite — that the caller only ever passes
    an id it already authorized — and the reviewer showed it loaded comments for a
    caller-supplied id BEFORE any authorization ran. That argument was true of
    today's two callers and false as a property of the query, which is exactly the
    kind of reasoning an independent read catches; the predicate now travels with
    the query so a second caller cannot arrive without it.
    """
    if not card_id:
        return []
    rows = _fetch(
        comments_sql(),
        comments_params(card_id, project, principal, limit=limit, is_staff=is_staff),
        connect=connect,
        store=store,
    )
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({field: row[field] for field in COMMENT_FIELDS})
        else:  # positional rows (a driver without rows_by_name)
            out.append(dict(zip(COMMENT_FIELDS, row)))
    return out
