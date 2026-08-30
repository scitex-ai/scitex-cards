#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The schema-version FLOOR: a recorded version must never move backwards.

Extracted from ``_schema_shape`` unchanged. That module answered two separate
questions -- "what number does the store CLAIM" and "what version do its
ARTIFACTS say" -- and this file is the first. The two are independent by
construction: a stamp is a claim by whichever client wrote last, an artifact is
a thing that either exists or does not, and the whole reason
:func:`~scitex_cards._schema_shape.observed_version` exists is that on this
store the two were measured disagreeing.

Both halves of the floor rule live here together: the ENGINE-side trigger, which
binds even clients that predate this code, and the CLIENT-side ``max()`` in
:func:`stamp_schema_version`, which binds the ones that have it.

See ``_schema_shape``'s module docstring for the 2026-07-31 measurement that
motivated all of this; it is not repeated here because one copy of a fact is the
whole point of splitting a file.
"""

from __future__ import annotations

from dataclasses import dataclass

# Aliased to the private spelling the moved code already uses. The delegating
# `_has_table` wrapper stayed with the LADDER, where its docstring belongs --
# it explains why a rung that cannot be read is reported absent. This module
# only needs the plain probe, so it takes it directly rather than importing the
# ladder for one name and coupling the two halves back together.
from ._schema_probe import _is_postgres
from ._schema_probe import has_table as _has_table

__all__ = [
    "SCHEMA_VERSION_FLOOR_TRIGGER",
    "SCHEMA_VERSION_FLOOR_TRIGGER_SQL",
    "SCHEMA_VERSION_DOWNGRADE_KEYS",
    "DowngradeReport",
    "downgrade_report",
    "stamp_schema_version",
]

#: Named so a guard can assert the ENGINE carries the rule rather than trust
#: that the DDL was ever run -- the same reason ``DM_TRIGGERS`` is named.
SCHEMA_VERSION_FLOOR_TRIGGER = "schema_meta_version_never_regresses"

# ON CONFLICT DO UPDATE fires this; INSERT OR REPLACE would not, because that
# is a DELETE followed by an INSERT and there is no OLD row left to restore
# from. No writer in this codebase uses OR REPLACE on schema_meta (checked
# across _db.py, _db_bootstrap.py and the installed 0.18.0 client), and the
# gap is covered by a test so a future one cannot open it silently.
#
# Re-entrancy: the inner UPDATE cannot loop. Without recursive triggers it does
# not re-fire at all; with them it re-fires once with NEW=high and OLD=low,
# where the WHEN clause is false. Safe either way, and both are tested rather
# than assumed from a default.
#
# IT ALSO RECORDS THE ATTEMPT, and that half is not decoration.
#
# A self-healing guard has a specific failure mode: it makes the thing it
# defends against INVISIBLE. Measured 2026-07-31 -- with the floor installed,
# `schema_meta` held at 7 through every sample while `user_version` kept
# dropping to 5 and once to 6, and after eliminating three separate
# hypotheses (the fleet, my own container, the host daemons) the writer was
# STILL unidentified. The store could say it had been migrated and by whom
# (`schema_migrated_by`), but the DESTRUCTIVE event was the one event with no
# audit trail at all.
#
# So the trigger writes what it can see: how many downgrades were refused,
# when the last one was, and what value was attempted. A trigger cannot name
# the process -- a trigger has no view of the caller -- but a count and a
# timestamp turn "something invisible is happening" into a signal that can be
# correlated against process activity, which is exactly what was missing
# while three wrong hypotheses were being formed.
SCHEMA_VERSION_DOWNGRADE_KEYS = (
    "schema_version_downgrades_refused",
    "schema_version_downgrade_last_at",
    "schema_version_downgrade_last_attempt",
)

SCHEMA_VERSION_FLOOR_TRIGGER_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {SCHEMA_VERSION_FLOOR_TRIGGER}
AFTER UPDATE OF value ON schema_meta
WHEN NEW.key = 'schema_version'
 AND CAST(NEW.value AS INTEGER) < CAST(OLD.value AS INTEGER)
BEGIN
    UPDATE schema_meta SET value = OLD.value WHERE key = 'schema_version';
    INSERT INTO schema_meta(key, value)
        VALUES('schema_version_downgrades_refused', '1')
        ON CONFLICT(key) DO UPDATE SET
            value = CAST(CAST(schema_meta.value AS INTEGER) + 1 AS TEXT);
    INSERT INTO schema_meta(key, value)
        VALUES('schema_version_downgrade_last_at',
               strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;
    INSERT INTO schema_meta(key, value)
        VALUES('schema_version_downgrade_last_attempt',
               CAST(OLD.value AS TEXT) || ' -> ' || CAST(NEW.value AS TEXT))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;
END;
"""


@dataclass(frozen=True)
class DowngradeReport:
    """What the store knows about attempts to move its version backwards.

    ``refused`` is a COUNT OF REFUSALS, not of writers: one process looping
    contributes many, and a caller must not read it as a population size.
    That distinction is stated here because reading a count as a population
    is precisely the error that produced two wrong diagnoses on 2026-07-31.
    """

    refused: int
    last_at: str = ""
    last_attempt: str = ""

    def __post_init__(self) -> None:
        if self.refused < 0:
            raise ValueError(f"refused must be >= 0, got {self.refused}")

    @property
    def ever_attempted(self) -> bool:
        """True if the store has ever refused a downgrade.

        A False here means "no downgrade has been refused SINCE THE TRIGGER
        WAS INSTALLED" -- never "this store was always consistent". A store
        that was downgraded before the guard existed reports False.
        """
        return self.refused > 0


def downgrade_report(conn) -> DowngradeReport:
    """Read the refusal counters the floor trigger maintains. Read-only."""
    if not _has_table(conn, "schema_meta"):
        return DowngradeReport(refused=0)
    rows = dict(
        conn.execute(
            "SELECT key, value FROM schema_meta WHERE key IN (?, ?, ?)",
            SCHEMA_VERSION_DOWNGRADE_KEYS,
        ).fetchall()
    )
    try:
        refused = int(rows.get("schema_version_downgrades_refused", 0) or 0)
    except (TypeError, ValueError):
        refused = 0
    return DowngradeReport(
        refused=refused,
        last_at=rows.get("schema_version_downgrade_last_at", "") or "",
        last_attempt=rows.get("schema_version_downgrade_last_attempt", "") or "",
    )


def stamp_schema_version(conn, prior_version: int, schema_version: int) -> None:
    """Record the schema version as a FLOOR, never as a reassignment.

    Extracted from ``init_schema`` so both halves of the floor rule live
    together: this one, and :data:`SCHEMA_VERSION_FLOOR_TRIGGER_SQL`.

    This used to write ``schema_version`` unconditionally, which let an OLDER
    client stamp a NEWER store as older. Measured on the live store
    2026-07-30, four read-only connections seconds apart::

        user_version=6  schema_meta=6  revision_col=True  trigger=True
        user_version=6  schema_meta=6  revision_col=True  trigger=True
        user_version=6  schema_meta=6  revision_col=True  trigger=True
        user_version=5  schema_meta=5  revision_col=True  trigger=True   <-- !

    The recorded version OSCILLATED while the physical schema (revision
    column, bump trigger) never regressed. ~90 fleet containers run 0.17.5 /
    0.18.0 / 0.23.0 / 0.24.0 SIMULTANEOUSLY, so whichever version opened the
    store last decided what it claimed to be. The stamp was a race, not a
    fact -- and it read LOWER than the store's real shape, which is the
    dangerous direction: a reader can conclude a column is absent when it is
    physically there.

    Migrations are additive (``ADD COLUMN``, ``CREATE ... IF NOT EXISTS``), so
    applied schema never goes backwards. ``max()`` therefore describes
    reality; a bare assignment describes only the last writer. A stamp encodes
    the WRITER's version, not the object's history, so never trust one writer
    to speak for a store ~90 processes share.
    """
    stamp = max(prior_version, schema_version)
    # NOT THE STORE'S STAMP. PostgreSQL has no `user_version` and rejects PRAGMA
    # outright (`syntax error at or near "PRAGMA"`, measured on the live
    # server), so the schema_meta upsert below is the WHOLE stamp -- which is
    # the direction this function's own docstring argues for anyway: the row is
    # trigger-protected and the PRAGMA structurally cannot be.
    if not _is_postgres(conn):
        conn.execute(f"PRAGMA user_version={stamp}")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = "
        # The max is taken IN SQL, not in Python: another process may have
        # raised it between our PRAGMA read and this statement, and CAST makes
        # the comparison numeric rather than lexicographic ('10' < '9' as text).
        #
        # SPELT AS A CASE RATHER THAN MAX(a, b). PostgreSQL's MAX is an
        # AGGREGATE ONLY, so a two-argument call is not a subtle behaviour
        # difference but a hard `function max(integer, integer) does not exist`.
        # GREATEST() is the PostgreSQL spelling and is not portable either --
        # but CASE is, and it is standard SQL.
        # Verified on both: max(7,5)=7, max(5,7)=7, max(10,9)=10.
        "  CAST(CASE WHEN CAST(schema_meta.value AS INTEGER) "
        "                 > CAST(excluded.value AS INTEGER) "
        "            THEN CAST(schema_meta.value AS INTEGER) "
        "            ELSE CAST(excluded.value AS INTEGER) END AS TEXT)",
        (str(stamp),),
    )

# EOF
