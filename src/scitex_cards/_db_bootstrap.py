#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite table population from an in-memory document.

The import entry points that used to live here (the sidecar importer /
``mirror_doc`` / ``_load_source``) are DELETED: SQLite is the only store, so
there is no external document to read and no second representation to project from.
What remains is the low-level table-writing machinery — the column maps, the
per-table inserters, and :func:`_rebuild_from_doc` — used by the incremental
mirror (:mod:`scitex_cards._db_mirror`) to populate a database from a document
the caller already holds in memory.

Field mapping (see :mod:`scitex_cards._db` for the schema rationale):
  * scalar Task fields → columns (``group`` → ``grp``; SQL reserved word),
  * ``deadlines`` / ``_log_meta`` → JSON TEXT columns,
  * ``comments`` → ``task_comments`` (``seq`` = position),
  * ``depends_on`` / ``blocks`` → ``task_edges`` (directional),
  * ``collaborators`` / ``subscribers`` → ``task_roles``,
  * ``users`` → ``users`` + ``user_names`` (alias fan-out),
  * ``inboxes`` map → ``notifications`` (``recipient_id`` = map key),
  * ``threads.json`` map → ``messages`` (``thread_key`` = map key).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection

import logging
from dataclasses import dataclass

from ._db import SCHEMA_VERSION
from ._db_payload import CARD_JSON_COL, card_payload_json_or_raise
from ._db_payload import json_or_none as _json_or_none
from ._db_sections import (  # re-exported: _db_mirror imports these from here
    _gen_id,  # noqa: F401
    _insert_messages,
    _insert_notifications,
    _insert_users,
)

logger = logging.getLogger(__name__)

#: (column, doc-key) pairs for the scalar ``tasks`` columns. ``group`` maps to
#: the ``grp`` column (SQL reserved word); ``deadlines`` / ``_log_meta`` /
#: ``row_order`` / ``card_json`` are handled separately (JSON / positional).
_TASK_SCALAR_COLS: tuple[tuple[str, str], ...] = (
    ("id", "id"),
    ("title", "title"),
    ("status", "status"),
    ("kind", "kind"),
    ("blocker", "blocker"),
    ("task", "task"),
    ("note", "note"),
    ("goal", "goal"),
    ("project", "project"),
    ("repo", "repo"),
    ("host", "host"),
    ("agent", "agent"),
    ("assignee", "assignee"),
    ("scope", "scope"),
    ("grp", "group"),
    ("priority", "priority"),
    ("parent", "parent"),
    ("pr_url", "pr_url"),
    ("issue_url", "issue_url"),
    ("deadline", "deadline"),
    ("scheduled", "scheduled"),
    ("created_at", "created_at"),
    ("last_activity", "last_activity"),
    ("started_at", "started_at"),
    ("finished_at", "finished_at"),
    ("created_by", "created_by"),
    ("job_id", "job_id"),
    ("command", "command"),
)

#: The FULL ordered column list every ``tasks`` INSERT writes: the scalars, the two
#: JSON side-cars, the positional ``row_order``, and the verbatim ``card_json``.
#:
#: PUBLIC ON PURPOSE. The S2 read guard probes THIS TUPLE for ``card_json`` to answer
#: "can the code running in THIS process actually populate the payload column?" — a
#: SYMBOL check against the imported object, never a version string. A version string
#: is metadata, and metadata lies: a stale wheel, an orphaned ``.dist-info`` and a SIF
#: baked months ago all report a version that outlived the code beside them. This repo
#: paid 135 SECONDS PER CARD WRITE for exactly that mistake on 2026-07-13.
TASK_INSERT_COLS: tuple[str, ...] = tuple(
    [col for col, _ in _TASK_SCALAR_COLS]
    + ["deadlines_json", "log_meta_json", "row_order", CARD_JSON_COL]
)

#: Data tables cleared before a rebuild, child-before-parent so the explicit
#: deletes never fight the FK order (cascade would also cover the children).
_CLEAR_ORDER: tuple[str, ...] = (
    "task_comments",
    "task_edges",
    "task_roles",
    "tasks",
    "user_names",
    "users",
    "inbox_recipients",
    "notifications",
    "messages",
)


def _dedupe_last_wins(tasks: list) -> list[tuple[int, dict]]:
    """``(row_order, card)`` pairs, duplicate ids collapsed — LAST occurrence wins.

    The semantics ``INSERT OR REPLACE`` gave us for free, hoisted into Python so the
    SQL can be a plain ``INSERT`` (see :func:`_insert_tasks` — that word cost 42x).
    A duplicate card id is a DATA BUG, not routine: REPLACE absorbed it silently
    (and still appended BOTH copies' comments). Same winner, said out loud.
    """
    by_id: dict[str, tuple[int, dict]] = {}
    ordered: list[tuple[int, dict]] = []
    dupes: list[str] = []
    for order, row in enumerate(tasks):
        if not isinstance(row, dict):
            continue
        tid = row.get("id")
        if isinstance(tid, str) and tid:
            if tid in by_id:
                dupes.append(tid)
            by_id[tid] = (order, row)
        else:
            ordered.append((order, row))
    if dupes:
        logger.error(
            "!! DUPLICATE CARD ID(S) IN THE CANONICAL STORE: %s. The mirror keeps "
            "the LAST occurrence of each (the same row `INSERT OR REPLACE` would "
            "have kept), but the source document itself is inconsistent and "
            "should be repaired — two cards cannot share an id.",
            ", ".join(sorted(set(dupes))),
        )
    ordered.extend(by_id.values())
    ordered.sort(key=lambda pair: pair[0])
    return ordered


@dataclass(frozen=True)
class RevisionOutcome:
    """What a compare-and-set write did. ALWAYS this shape when opting in.

    A LOST RACE IS NOT AN ERROR. It is the ordinary outcome a reconciler counts:
    two writers touched one card and this one arrived second. Raising there would
    make routine concurrency indistinguishable from a real fault, and a caller
    reconciling thousands of rows would have to catch-and-continue around its own
    happy path. So a race returns ``applied=False`` and the caller tallies it.

    What DOES raise is misuse — a batch, or ``replace=False`` — because those are
    the caller asking for something this function cannot do, and a capability gap
    silently counted as a lost race is exactly the miscount this split prevents.

    ``found`` is three-valued on purpose: the revision now in the row, or None
    when the row is absent. "Someone wrote past me" and "the card is gone" call
    for different responses, and collapsing them into a bare False loses that.
    """

    applied: bool
    task_id: str
    expected: int
    found: int | None

    def __post_init__(self) -> None:
        if self.applied and self.found != self.expected:
            raise ValueError(
                f"malformed RevisionOutcome: applied=True requires found == "
                f"expected, got found={self.found!r} expected={self.expected!r}"
            )
        if not self.applied and self.found == self.expected:
            raise ValueError(
                "malformed RevisionOutcome: applied=False but found == expected, "
                "which describes a write that should have landed"
            )


def _insert_tasks(
    conn: StoreConnection,
    tasks: list,
    *,
    replace: bool = True,
    expected_revision: int | None = None,
) -> dict[str, int]:
    """Insert every card + its children.

    ``expected_revision`` turns the upsert into a COMPARE-AND-SET. v6 added
    ``tasks.revision`` and v7 added the ``tasks_bump_revision`` trigger, so every
    row already carries a version that advances on write — but until now NO
    writer compared it, which made every concurrent edit last-write-wins with the
    loser discarded silently. A lock nobody asserts is not a lock; it is a column
    that makes the schema look safe.

    Pass the revision you READ, and the write lands only if nobody has written
    since. On a mismatch the row is left ALONE and :class:`RevisionConflictError`
    is raised, so the caller re-reads and re-applies rather than clobbers. Reject
    over overwrite: a lost update is invisible, an exception is not.

    IT IS OPT-IN BY CONSTRUCTION, and that is load-bearing rather than politeness.
    ``_migrate_v6_to_v7`` records that scitex-db proposed REJECT semantics for
    this lock and it was RULED UNUSABLE, because "an UPDATE from a writer that
    knows nothing about ``revision`` would ABORT, so fleet writes would fail until
    every container is current" — a condition this fleet cannot establish. So when
    ``expected_revision`` is None no clause is emitted and the SQL is identical to
    before: every existing caller, and every older client, is untouched. Only a
    caller that opts in can fail.

    SINGLE CARD ONLY when opting in. A batch cannot report WHICH row lost, and a
    half-applied batch is worse than none, so a longer list is refused up front.

    ``replace`` picks the conflict clause, and it is worth 42x — MEASURED on the
    live 1,370-card store (2026-07-13)::

        INSERT OR REPLACE INTO tasks , FK ON : 4,592 us/row  -> 6.3 s for the store
        INSERT           INTO tasks , FK ON  :   110 us/row  -> 0.15 s

    ``tasks`` is a PARENT of ``task_comments`` / ``task_edges`` / ``task_roles``
    (``ON DELETE CASCADE``), so under ``PRAGMA foreign_keys=ON`` a REPLACE — a DELETE
    plus an INSERT — runs the whole cascade/FK-check machinery FOR EVERY ROW. The
    control group is next door: ``task_comments`` already uses a plain INSERT and FK
    enforcement costs it NOTHING (150 vs 149 us/row, FK on vs off). It is
    REPLACE-**on-a-parent** that is expensive, not foreign keys. (``PRAGMA
    defer_foreign_keys=ON`` does NOT help — measured SLOWER. Do not reach for it.)

    So the clause is a PRECONDITION, not a style choice, and the callers differ:

    * ``replace=False`` — caller ALREADY DELETED these rows, so a conflict is
      impossible and REPLACE is pure waste. :func:`_rebuild_from_doc` clears every
      table first; this is its 42x.
    * ``replace=True`` (DEFAULT, the SAFE one) — caller is UPSERTING over rows that
      may still be present (the incremental mirror re-writes one changed card
      without dropping its ``tasks`` row). A plain INSERT would raise ``UNIQUE
      constraint failed: tasks.id`` there, so REPLACE is load-bearing.

    Duplicate ids are collapsed by :func:`_dedupe_last_wins` (last-wins — the winner
    REPLACE would have picked), so ``replace=False`` cannot conflict with itself.
    """
    counts = {"tasks": 0, "comments": 0, "edges": 0, "roles": 0}
    if expected_revision is not None:
        if not replace:
            raise ValueError(
                "expected_revision requires replace=True: with replace=False the "
                "caller has already deleted the rows, so there is no prior "
                "revision to compare against and the check would be vacuous."
            )
        if len(tasks) != 1:
            raise ValueError(
                f"expected_revision takes exactly one card, got {len(tasks)}. "
                "A batch cannot report which row lost the race, and a "
                "half-applied batch is worse than none."
            )
    placeholders = ", ".join("?" for _ in TASK_INSERT_COLS)
    cols = ", ".join(TASK_INSERT_COLS)
    if replace:
        # ON CONFLICT DO UPDATE, not INSERT OR REPLACE. Three reasons, and the
        # first is the one that changes behaviour:
        #
        # 1. REPLACE is DELETE + INSERT, so it fires DELETE and INSERT triggers
        #    and NOT the AFTER UPDATE ones. v7's `tasks_bump_revision` is an
        #    AFTER UPDATE trigger, which means the revision lock has been INERT
        #    for every upsert taking this path. A true UPDATE fires it.
        # 2. INSERT OR REPLACE is SQLite-only syntax; ON CONFLICT parses on both
        #    engines, which is what lets this path reach PostgreSQL at all.
        # 3. It should also be FASTER, not slower. The 42x measured against
        #    REPLACE was the DELETE half dragging the whole ON DELETE CASCADE
        #    machinery through `task_comments` / `task_edges` / `task_roles` for
        #    every row. An UPDATE touches no child table.
        #
        # `id` is the conflict target because that is the invariant the
        # de-duplication above already enforces: one row per card id.
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in TASK_INSERT_COLS if c != "id"
        )
        insert_sql = (
            f"INSERT INTO tasks ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        if expected_revision is not None:
            # The compare-and-set. A WHERE on ON CONFLICT DO UPDATE makes the
            # update conditional: when it does not hold, the conflicting row is
            # left EXACTLY as it was — no write, no trigger, no revision bump.
            # `tasks.revision` is qualified deliberately; bare `revision` is
            # ambiguous against `excluded` on both engines.
            insert_sql += " WHERE tasks.revision = ?"
    else:
        insert_sql = f"INSERT INTO tasks ({cols}) VALUES ({placeholders})"
    for order, row in _dedupe_last_wins(tasks):
        values = [row.get(ykey) for _, ykey in _TASK_SCALAR_COLS]
        values.append(_json_or_none(row.get("deadlines")))
        values.append(_json_or_none(row.get("_log_meta")))
        values.append(order)
        # The VERBATIM card — the payload an S2 read reconstructs from, exactly as
        # it appeared in the source document (unknown keys, key order, types and
        # all). The typed columns above are only the INDEX. See :mod:`_db_payload`.
        #
        # _or_raise, NOT the None-returning encoder: a row stored with a NULL
        # payload is UNREADABLE, and the read refuses the whole store on it, so
        # one bad card takes the board down for every agent until somebody
        # unrelated rewrites it. Measured 2026-08-17 — `add_task(note=<datetime>)`
        # planted exactly that, and it is the tasks-variant outage of 2026-08-11.
        # The writer already knows the payload cannot be serialised at this point;
        # refusing costs this one call, storing it costs everyone else.
        values.append(card_payload_json_or_raise(row))
        if expected_revision is None:
            conn.execute(insert_sql, values)
        else:
            tid_cas = str(row.get("id"))
            # Read the CURRENT revision first. This is NOT the safety mechanism
            # — the WHERE clause is, and it is what makes the write atomic. The
            # SELECT exists so the outcome can report what it lost TO, and so the
            # row-absent case is reported rather than silently becoming an
            # INSERT: `ON CONFLICT DO UPDATE ... WHERE` only fires when a
            # conflicting row exists, so without this a compare-and-set against
            # a deleted card would quietly re-create it.
            found_row = conn.execute(
                "SELECT revision FROM tasks WHERE id = ?", (tid_cas,)
            ).fetchone()
            found = None if found_row is None else found_row[0]
            if found != expected_revision:
                counts["revision_skipped"] = 1
                counts["revision_found"] = found
                return counts
            cur = conn.execute(insert_sql, [*values, expected_revision])
            if cur.rowcount == 0:
                # The row moved between the SELECT and the UPDATE — the race the
                # WHERE exists for. Reaching here means the guard WORKED. Re-read
                # so the caller is told the truth about where the row is now,
                # rather than a stale value from before the losing attempt.
                after = conn.execute(
                    "SELECT revision FROM tasks WHERE id = ?", (tid_cas,)
                ).fetchone()
                counts["revision_skipped"] = 1
                counts["revision_found"] = None if after is None else after[0]
                return counts
        counts["tasks"] += 1
        tid = row.get("id")
        counts["comments"] += _insert_comments(conn, tid, row.get("comments"))
        counts["edges"] += _insert_edges(conn, tid, row)
        counts["roles"] += _insert_roles(conn, tid, row)
    return counts


def _insert_comments(conn, task_id, comments) -> int:
    if not isinstance(comments, list):
        return 0
    n = 0
    for seq, c in enumerate(comments):
        if not isinstance(c, dict):
            continue
        conn.execute(
            "INSERT INTO task_comments(task_id, seq, author, ts, kind, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                seq,
                c.get("author"),
                c.get("ts"),
                c.get("kind"),
                "" if c.get("text") is None else str(c.get("text")),
            ),
        )
        n += 1
    return n


def _insert_edges(conn, task_id, row) -> int:
    n = 0
    for edge_type in ("depends_on", "blocks"):
        targets = row.get(edge_type)
        if not isinstance(targets, list):
            continue
        for dst in targets:
            if not (isinstance(dst, str) and dst):
                continue
            conn.execute(
                # DO NOTHING: all three columns ARE the primary key, so a
                # conflicting row is byte-identical and there is nothing to
                # update. (REPLACE here was DELETE+INSERT of an identical row.)
                "INSERT INTO task_edges"
                "(src_task_id, dst_task_id, edge_type) VALUES (?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (task_id, dst, edge_type),
            )
            n += 1
    return n


def _insert_roles(conn, task_id, row) -> int:
    n = 0
    for role, key in (("collaborator", "collaborators"), ("subscriber", "subscribers")):
        members = row.get(key)
        if not isinstance(members, list):
            continue
        for who in members:
            if not (isinstance(who, str) and who):
                continue
            conn.execute(
                # Same shape as task_edges: the three columns are the whole
                # primary key, so a conflict has nothing left to write.
                "INSERT INTO task_roles(task_id, who, role) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (task_id, who, role),
            )
            n += 1
    return n


#: Tables the doc-write path may clear. Three are DELIBERATELY absent, all for
#: the same rule: A TABLE IS OWNED BY EXACTLY THE THING THAT PRODUCES IT.
#:
#: ``messages`` is derived from the ``threads.json`` SIDECAR, which the doc-write
#: path never touches. A doc mirror that cleared ``messages`` would silently
#: destroy every DM thread on each card write.
#:
#: ``notifications`` and ``inbox_recipients`` are produced by the DELIVERY RAIL
#: (``_inbox_postgres``) since #780, not by this document. Clearing them on a
#: first-run rebuild would delete the fleet's undelivered notifications —
#: including operator DMs that were enqueued and never read — with nothing to
#: restore them from, because the rail is now their only copy. The sibling
#: neutralisation is ``_db_mirror._SECTION_KEYS``, which no longer rebuilds
#: ``notifications`` on the INCREMENTAL path; this closes the FULL one.
#: ``users`` / ``user_names`` joined this tuple when the registry acquired a
#: live producer (``_db_users.save_users_rows``): the document is no longer
#: the thing that makes them, so a doc-only rebuild must not DELETE them.
#:
#: The upsert at ``_insert_users(conn, doc.get("users"))`` below still runs,
#: so a doc carrying a registry section still contributes its rows — this
#: removes the WIPE, not the merge. The full-restore path is unaffected: it
#: passes ``threads`` and therefore uses ``_CLEAR_ORDER``, where clearing the
#: registry is exactly right because a restore is meant to replace it.
_DOC_OWNED_ELSEWHERE = (
    "messages",
    "notifications",
    "inbox_recipients",
    "users",
    "user_names",
)
_DOC_CLEAR_ORDER = tuple(t for t in _CLEAR_ORDER if t not in _DOC_OWNED_ELSEWHERE)


def _rebuild_from_doc(
    conn: StoreConnection,
    doc: dict,
    *,
    threads: dict[str, list[dict]] | None = None,
) -> dict:
    """Rebuild the doc-derived tables from an ALREADY-PARSED doc, in ONE txn.

    The first-run rebuild primitive for :func:`scitex_cards._db_mirror`: when a
    database has no prior hashes to diff against, it is populated from the doc
    the caller holds in memory (under the store lock — no re-parse).

    ``threads`` rebuilds the ``messages`` table too. Pass it ONLY when the caller
    genuinely owns the threads sidecar — the incremental mirror path does not,
    and clearing ``messages`` there would wipe every DM on every card write.

    Caller owns the transaction boundary and the connection.

    THIS DELETEs before it inserts, which is why it is FENCED: reached only on
    first run against a database that has nothing to delete. The sidecar-import
    caller that used to reach it on a populated database has been removed — do
    not add another caller that runs this against a live board.
    """
    clear = _CLEAR_ORDER if threads is not None else _DOC_CLEAR_ORDER
    for table in clear:
        conn.execute(f"DELETE FROM {table}")

    summary: dict = {}
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    # replace=False: every row was just DELETEd above, so a conflict is impossible
    # and REPLACE would only buy SQLite's per-row FK-cascade check — which was 6.3 s
    # of this rebuild's 7.3 s. See _insert_tasks.
    summary.update(
        _insert_tasks(conn, tasks if isinstance(tasks, list) else [], replace=False)
    )
    summary.update(_insert_users(conn, doc.get("users")))
    summary["notifications"] = _insert_notifications(conn, doc.get("inboxes"))
    if threads is not None:
        summary["messages"] = _insert_messages(conn, threads)
    return summary


def _stamp_meta(conn: StoreConnection, source: str) -> None:
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('source', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (source,),
    )
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )


# EOF
