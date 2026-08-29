#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_schema_ddl_objects.py
"""The objects the DDL path creates, and the gate that skips the DDL when it would make none.

WHY THIS EXISTS
---------------
``init_schema`` asserts the whole schema on every open. On SQLite that was nearly
free. Against a shared PostgreSQL server it is DDL against the system catalogues,
and the ownership-requiring statements among it -- ``CREATE INDEX``,
``CREATE [OR REPLACE] TRIGGER`` and the two migration tails that ``ALTER`` a column
or ``CREATE OR REPLACE FUNCTION`` -- demand the caller OWN the table. PostgreSQL
checks that ownership BEFORE ``IF NOT EXISTS`` short-circuits, so a role that has
full DML but is not the owner dies with ``must be owner of table tasks`` even though
every statement it would run is a no-op. (scitex-dev hit the identical wall; its fix,
#755, is this module's shape.)

THE GATE IS CONSERVATIVE IN THE SAME DIRECTION AS ``schema_already_current``
--------------------------------------------------------------------------------
Every uncertain answer is "the DDL is NOT a no-op", which runs the full DDL --
exactly today's behaviour. A wrong "no-op" would leave a store missing an object
while the client believed it was complete; that is the shape of the failure that
took this board from 2170 rows to 18, so nothing here INFERS presence. It checks
every object the DDL creates against the catalogue, and it only reports "no-op"
when the shape ladder already places the store at this client's version AND every
table, index, guard trigger, and (on PostgreSQL) every trigger function is present.

What the gate does NOT do: it does not re-derive the columns. The additive
``ALTER TABLE ... ADD COLUMN`` rungs are already individually guarded (each asks
``table_columns`` first), and the version-defining columns are exactly what the
shape ladder verifies, so a store the ladder places at SCHEMA_VERSION has its
columns. Re-checking them here would duplicate the ladder and grow a second list
that can drift from it.

The function-local imports are deliberate, matching this package's convention:
several of these modules are reachable from ``_db`` (which re-exports
``init_schema``), and a module-level import back into that graph would cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection

__all__ = ["_DDL_INDEXES", "_all_ddl_present"]

#: Every (table, index) pair the DDL path installs -- the objects the shape
#: ladder does NOT verify. A store the ladder places at SCHEMA_VERSION has the
#: version-defining tables/columns, but the ladder never looks at these indexes,
#: so a gate that trusted the ladder alone would skip a ``CREATE INDEX`` that is
#: in fact missing. Kept as a constant to diff against the DDL (and asserted
#: against a freshly-built store in the tests), not re-typed at a call site.
_DDL_INDEXES: tuple[tuple[str, str], ...] = (
    # _db_schema_sql.SCHEMA_SQL
    ("tasks", "idx_tasks_status"),
    ("tasks", "idx_tasks_agent"),
    ("tasks", "idx_tasks_assignee"),
    ("tasks", "idx_tasks_scope"),
    ("tasks", "idx_tasks_kind"),
    ("tasks", "idx_tasks_blocker"),
    ("tasks", "idx_tasks_project"),
    ("tasks", "idx_tasks_deadline"),
    ("tasks", "idx_tasks_parent"),
    ("tasks", "idx_tasks_pr_url"),
    ("task_comments", "idx_comments_task"),
    ("task_edges", "idx_edges_dst"),
    ("task_roles", "idx_roles_who"),
    ("user_names", "idx_user_names_uid"),
    ("notifications", "idx_notif_recipient_seen"),
    ("messages", "idx_messages_thread"),
    # schema v5 DM rail, from _db_dm_schema.SCHEMA_SQL_V5
    ("dm_thread_member_events", "idx_dm_member_thread"),
    ("dm_thread_member_events", "idx_dm_member_member"),
    ("dm_messages", "idx_dm_messages_thread"),
    ("dm_messages", "idx_dm_messages_sender"),
    ("dm_receipts", "idx_dm_receipts_reader"),
)


def _all_ddl_present(conn: StoreConnection, shape: Any, schema_version: int) -> bool:
    """True only when the DDL ``init_schema`` would run creates nothing.

    This is the second, narrower gate behind :func:`schema_already_current`. That
    gate reads the STAMP and the guard triggers; this one reads the OBJECTS, so
    that a store whose stamp disagrees (the measured oscillation) but whose objects
    are all physically present opens without re-running DDL that a non-owner role
    is refused. It is the #755 fix: probe, and emit DDL only where something is
    genuinely absent.

    Conservative by construction: a missing version rung, a missing table, a
    missing index, a missing guard trigger, or (on PostgreSQL) a missing trigger
    function -- or an unreadable catalogue -- all return False, which runs the
    full DDL exactly as today. Only a store the ladder places at this client's
    version with every DDL object present returns True.
    """
    # Function-local: see the module docstring. These names are all stable
    # module constants, so the per-open import cost is a dict lookup, not a re-scan.
    from ._db_migrations import _SEQ_NAME, NOTIFICATION_PAYLOAD_TRIGGER  # noqa: PLC0415
    from ._db_schema_sql import SCHEMA_TABLES  # noqa: PLC0415
    from ._schema_current import REQUIRED_GUARD_TRIGGERS  # noqa: PLC0415
    from ._schema_probe import (  # noqa: PLC0415
        _is_postgres,
        has_function,
        has_index,
        has_sequence,
        has_table,
        has_trigger,
    )

    try:
        # The shape ladder is the floor. A store it places BELOW this client's
        # version is genuinely behind, so its DDL migrates it up and must run; a
        # store it places AT (or, for an older client, ABOVE) this version has its
        # version-defining tables and columns, and the object checks below decide
        # the rest.
        if shape.observed is None or shape.observed < schema_version:
            return False
        for table in SCHEMA_TABLES:
            if not has_table(conn, table):
                return False
        for table, index in _DDL_INDEXES:
            if not has_index(conn, table, index):
                return False
        # The guard triggers exist on both backends. The payload trigger and the
        # plpgsql functions are PostgreSQL-only: SQLite's triggers are inline, so
        # there is no separate function to check and no payload trigger to find.
        for trigger in REQUIRED_GUARD_TRIGGERS:
            if not has_trigger(conn, trigger):
                return False
        if _is_postgres(conn):
            if not has_trigger(conn, NOTIFICATION_PAYLOAD_TRIGGER):
                return False
            # The v8->v9 generator is a real sequence, and it is ABSENT FROM THE
            # LADDER (which sees the seq COLUMN, not the sequence that backs it).
            # A crash between the column add and the sequence create would leave
            # the column present and the generator missing; without this check
            # the gate would read the store complete and never install it.
            if not has_sequence(conn, _SEQ_NAME):
                return False
            functions = frozenset(
                t + "_fn" for t in REQUIRED_GUARD_TRIGGERS
            ) | frozenset({NOTIFICATION_PAYLOAD_TRIGGER + "_fn"})
            for function in functions:
                if not has_function(conn, function):
                    return False
        return True
    except Exception:
        # An unreadable catalogue is not a complete schema. Run the DDL.
        return False
