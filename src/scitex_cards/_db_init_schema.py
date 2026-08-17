#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_db_init_schema.py
"""ASSERT THE SCHEMA on an open connection — extracted from :mod:`scitex_cards._db`.

``_db`` owns CONNECTIONS: resolving a target, dispatching SQLite vs PostgreSQL,
applying PRAGMAs, gating the client version. This module owns the OTHER thing
that lived there: making an already-open connection carry the current shape —
the currency gate, the DDL, the whole migration ladder, the version stamp and
the provenance rows.

They change for different reasons, and that is the whole argument. A connection
change never touches the ladder; a SCHEMA VERSION BUMP touches nothing else.
Keeping both in one file meant every schema bump edited the connection module
and pushed it past its size budget — which is exactly what happened at v10.

The convention was already established for this file's neighbours: the DDL text
went to ``_db_schema_sql``, ``verify`` to ``_db_verify``, the DM tables to
``_db_dm_schema``, the version-floor trigger to ``_schema_shape``. This is the
last passenger getting off.

``_db`` re-exports :func:`init_schema`, so every existing
``from ._db import init_schema`` resolves unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection


from ._db_dm_schema import migrate_v4_to_v5 as _migrate_v4_to_v5
from ._db_foreign_keys import _migrate_v10_to_v11
from ._db_sync_columns import _migrate_v11_to_v12
from ._db_migrations import (
    _migrate_v1_to_v2,
    _migrate_v2_to_v3,
    _migrate_v5_to_v6,
    _migrate_v6_to_v7,
    _migrate_v7_to_v8,
    _migrate_v8_to_v9,
    _migrate_v9_to_v10,
    record_migration_provenance,
)
from ._ddl import execute_ddl
from ._schema_shape import (
    SCHEMA_VERSION_FLOOR_TRIGGER_SQL,
    observed_version,
    stamp_schema_version,
)
from ._db_schema_sql import SCHEMA_SQL as _SCHEMA_SQL
from ._store_retirement import RETIREMENT_TRIGGER_SQL

__all__ = ["init_schema"]


def init_schema(conn: StoreConnection) -> None:
    """Create the schema idempotently + stamp version. Commits on success.

    Runs the ``CREATE TABLE/INDEX IF NOT EXISTS`` script, applies the additive
    column migrations, sets ``PRAGMA user_version=SCHEMA_VERSION``, and seeds the
    ``schema_meta`` rows (``schema_version`` always; ``created_at`` / ``source``
    only if absent so a re-init never clobbers the original provenance).
    """
    # BEFORE the schema script, because that script is what makes a fresh file
    # look initialised. 0 means "new file" and is a CREATE, not a migration.
    # SCHEMA_VERSION and _utc_now_iso still live in `_db`, which imports THIS
    # module — so they are taken function-locally. Reading the version from its
    # one definition (rather than re-declaring it here) is what keeps a bump a
    # ONE-LINE change instead of two places that can disagree.
    from ._db import SCHEMA_VERSION, _utc_now_iso  # noqa: PLC0415 -- import cycle
    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    if _is_postgres(conn):
        # PostgreSQL has no user_version and rejects PRAGMA outright. 0 is the
        # honest starting point here for the same reason it is on a fresh file:
        # "no stamp read" is not "version zero asserted", and the physical-shape
        # read immediately below supplies the real prior version via max().
        _prior_version = 0
    else:
        _prior_version = conn.execute("PRAGMA user_version").fetchone()[0]

    # AND THE PRAGMA ALONE IS NOT TRUSTWORTHY HERE. A PRAGMA cannot carry a
    # trigger, so the engine-level floor applied below protects `schema_meta`
    # and structurally CANNOT protect `user_version`; any writer executing a
    # bare `PRAGMA user_version=<its own>` still knocks it backwards.
    #
    # Measured on the live store 2026-07-31: `schema_migrated_at` advanced every
    # ~45s with from=5 to=7 by 0.25.0, while v6's `tasks.revision` and v7's
    # `tasks_bump_revision` were physically present the entire time. Since
    # record_migration() returns early when prior == new, that churn is proof
    # the PRAGMA genuinely kept reading 5 -- a current client re-migrating a
    # store that was never behind, in a loop, forever.
    #
    # The physical shape cannot be un-migrated by a stamp, so it is the floor.
    # `observed` is None on a fresh file (no rung present), which leaves
    # _prior_version at 0 and preserves the CREATE-not-migration branch above.
    _shape = observed_version(conn)
    if _shape.observed is not None:
        _prior_version = max(_prior_version, _shape.observed)

    # ASSERT THE SCHEMA ONCE PER STORE, NOT ONCE PER OPEN.
    #
    # Everything below this point is DDL. On SQLite that was very nearly free.
    # Against a shared PostgreSQL server it is DDL against the system
    # catalogues, and CREATE OR REPLACE FUNCTION rewrites the pg_proc row every
    # single time -- it is not a no-op when the function already matches.
    #
    # MEASURED on the live store 2026-08-01, with the entire fleet STOPPED and
    # only four host daemons connected: all 9 trigger functions were dropped and
    # recreated every ~10 seconds, continuously. And concurrency did not
    # survive it:
    #
    #      4 simultaneous open_db  ->  at least 1 deadlock
    #     12 simultaneous open_db  ->  11 of 12 failed, DeadlockDetected
    #
    # on pg_proc, which is exactly the contention two clients create by
    # replacing the same function at the same time. The comment further down
    # this function already notes that "~90 containers call init_schema on every
    # connection"; at that width this is not a slow path, it is a broken one.
    #
    # So: when the store ALREADY has the shape this client would assert, skip
    # the assertion entirely. The gate is a READ, and it is deliberately
    # conservative -- every branch that is not provably current falls through to
    # the full DDL below, so the worst case is exactly today's behaviour.
    #
    # THE GUARD TRIGGERS ARE CHECKED, NOT ASSUMED. They are not decoration: they
    # are the retirement enforcement AND the proof-of-currency mechanism, so a
    # client that skipped the DDL without confirming they exist could leave a
    # store unguarded while believing it had guarded it. Presence is verified
    # against the catalogue on every open; only the WRITE is skipped.
    from ._schema_current import schema_already_current  # noqa: PLC0415

    if schema_already_current(conn, _shape, SCHEMA_VERSION):
        conn.commit()
        return

    execute_ddl(conn, _SCHEMA_SQL)
    # Separate, not folded into _SCHEMA_SQL: per the note below, that script
    # reaches FRESH files only, and these guards must reach every store the
    # current client opens or it cannot prove it is current (_store_retirement).
    execute_ddl(conn, RETIREMENT_TRIGGER_SQL)
    # Same reason, and doubly so: the client-side floor below binds only
    # clients that HAVE it, while 2026-07-31 measured the live store swinging
    # 5 -> 7 -> 5 with v7's artifacts physically present the whole time. This
    # trigger is the copy of the rule an 0.18.0 writer cannot skip.
    execute_ddl(conn, SCHEMA_VERSION_FLOOR_TRIGGER_SQL)
    _migrate_v1_to_v2(conn)
    _migrate_v2_to_v3(conn)
    # NOTE there is no _migrate_v3_to_v4: v4's changes went into _SCHEMA_SQL
    # only, which is FRESH-database-only. `CREATE TABLE IF NOT EXISTS` is a no-op
    # on an existing table, so a v3 file upgraded straight to v5 never received
    # them — the exact trap _migrate_v1_to_v2's docstring warns about, present in
    # this very chain. Not fixed here (it needs establishing what v4 added);
    # named so the gap is visible rather than inherited as a numbering quirk.
    _migrate_v4_to_v5(conn)
    _migrate_v5_to_v6(conn)
    _migrate_v6_to_v7(conn)
    _migrate_v7_to_v8(conn)
    _migrate_v8_to_v9(conn)
    _migrate_v9_to_v10(conn)
    # v11 -> v12 RUNS HERE, BEFORE THE LOWER-NUMBERED RUNG BELOW, AND THAT IS
    # NOT A MISTAKE. This chain is ordered by COST, not by number: the comment
    # under `_migrate_v10_to_v11` says it runs "LAST, and after every column
    # rung" precisely so its advisory lock does not serialise the cheap ones.
    # v11 -> v12 is a plain additive ADD COLUMN rung, so it belongs with the
    # cheap ones. The chain already carries a numbering irregularity for a
    # different reason (there is no _migrate_v3_to_v4, noted above), so the
    # invariant to preserve is the ordering rule, not the arithmetic.
    _migrate_v11_to_v12(conn)
    # LAST, and after every column rung, because it is the only rung that takes
    # locks on tables the fleet is actively writing. It also SERIALISES ~90
    # clients on an advisory lock rather than letting them race — the same width
    # that produced 11 DeadlockDetected failures out of 12 concurrent opens
    # above, applied to ADD CONSTRAINT, which holds ShareRowExclusive on two
    # tables while it validates. Running it last keeps that serialisation off
    # the cheap rungs.
    _migrate_v10_to_v11(conn)
    # THE STAMP IS A FLOOR, NEVER A REASSIGNMENT. Both halves of that rule now
    # live in _schema_shape: this client-side one, and the engine-side trigger
    # applied above which binds the clients that predate this code.
    stamp_schema_version(conn, _prior_version, SCHEMA_VERSION)
    # ON CONFLICT DO NOTHING, not INSERT OR IGNORE: the latter is SQLite-only
    # syntax. The two are equivalent here -- both leave an existing row alone,
    # which is what preserves the ORIGINAL provenance on a re-init -- but only
    # this spelling parses on PostgreSQL. (`?` is fine on both: StoreConnection
    # translates paramstyle, so call sites stay written in one dialect.)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('created_at', ?) "
        "ON CONFLICT(key) DO NOTHING",
        (_utc_now_iso(),),
    )
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('source', 'fresh') "
        "ON CONFLICT(key) DO NOTHING",
    )
    # An upgrade of an EXISTING store names itself. See that function for why:
    # a v5 -> v6 move on the live store was un-attributable on 2026-07-30, and
    # the only evidence available was a sentinel that measures every neighbour.
    #
    # The condition is repeated here rather than left to the callee's own early
    # return, because `__version__` resolves LAZILY on purpose (#630 took cold
    # import from 425ms to ~137ms by deferring it) and ~90 containers call
    # init_schema on every connection. Paying that resolution on every open, to
    # stamp a row only a genuine upgrade writes, would spend the win #630 bought.
    if _prior_version not in (0, SCHEMA_VERSION):
        from . import __version__ as _client_version

        record_migration_provenance(
            conn,
            _prior_version,
            SCHEMA_VERSION,
            _utc_now_iso(),
            str(_client_version),
        )
    conn.commit()

__all__ = ["init_schema"]

# EOF
