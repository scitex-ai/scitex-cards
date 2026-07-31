#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read a store's schema version from its SHAPE, and floor the stamp it claims.

THE MEASUREMENT THAT MOTIVATED THIS
------------------------------------
2026-07-31 02:45-02:51, on the live fleet store, with NO tests running and
nothing of mine writing to it::

    t=02:45:25  schema_version='5'
    t=02:45:50  schema_version='7'
    t=02:46:15  schema_version='5'

and minutes later, settled::

    tasks.revision column present : True   <- installed by v5->v6
    tasks_bump_revision trigger   : True   <- installed by v6->v7
    user_version=5   schema_meta='5'

So the store IS v7 and SAYS v5. That is the dangerous direction, not the
harmless one: a reader that gates on the stamp concludes ``tasks.revision``
is absent while it is physically sitting there, and writes accordingly.

WHY THE 0.25.0 FLOOR DID NOT STOP IT
-------------------------------------
``init_schema`` already computes ``max(_prior_version, SCHEMA_VERSION)`` and
takes ``MAX(CAST(...))`` in SQL. That fix is real, and it is level 3 --
"remember to apply it" -- so it binds only the clients that HAVE it. Read
directly out of this container's own 0.18.0 install, which is one of the
~135 still running::

    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"

Both bare assignments. No amount of care in the new client governs that
process, so the rule has to move to somewhere every client passes through:
the engine.

WHAT CAN AND CANNOT BE GUARDED -- STATED, NOT GLOSSED
------------------------------------------------------
* ``schema_meta.schema_version`` is written with ``ON CONFLICT DO UPDATE``,
  which IS an UPDATE, so a trigger reaches it. That half is fixed here, for
  every client including ones that predate this code.
* ``PRAGMA user_version`` is not a table write. NO trigger can reach it, in
  any SQLite version. That half is NOT fixed here and cannot be; it will keep
  oscillating until the last old client is gone.

Which is exactly why the second half of this module exists. If one of the two
stamps is permanently unguardable, no consumer should be gating on stamps at
all -- so :func:`observed_version` reports what the store physically IS, by
probing for each migration's artifact. That reading is unforgeable: a client
cannot claim a column it did not add.

WHY THE TRIGGER ASSIGNS INSTEAD OF REFUSING
--------------------------------------------
Precedent from this codebase, ``_migrate_v6_to_v7``: scitex-db proposed
REJECT semantics for the revision lock and it was ruled unusable, because "an
UPDATE from a writer that knows nothing about ``revision`` would ABORT, so
fleet writes would fail until every container is current" -- the condition
that is demonstrably unestablishable here. A ``RAISE(ABORT)`` floor would do
the same thing on a wider blast radius: every 0.17-0.24 client would start
failing to open the store. So the trigger SELF-HEALS -- it lets the low write
land and immediately restores the high value -- and old clients keep working
while never being able to lower the recorded version.
"""

from __future__ import annotations

from ._schema_probe import has_table, has_trigger

import enum
from dataclasses import dataclass

__all__ = [
    "SCHEMA_VERSION_FLOOR_TRIGGER_SQL",
    "SCHEMA_VERSION_FLOOR_TRIGGER",
    "SCHEMA_VERSION_DOWNGRADE_KEYS",
    "downgrade_report",
    "SHAPE_LADDER",
    "ShapeAgreement",
    "SchemaShape",
    "observed_version",
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
# Re-entrancy: the inner UPDATE cannot loop. With the SQLite default
# (recursive_triggers OFF) it does not re-fire at all; with it ON it re-fires
# once with NEW=high and OLD=low, where the WHEN clause is false. Safe under
# both settings, and both are tested rather than assumed from the default.
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
# the process -- SQLite has no view of the caller -- but a count and a
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

#: version -> the physical artifact that migration installed.
#:
#: This is a LADDER, not a lookup: version N is observed only when every rung
#: up to N is present. A store carrying v7's trigger but missing v6's column
#: is not "v7 with a gap", it is a store whose migration chain broke, and
#: reporting 7 for it would hide exactly the corruption worth finding.
#:
#: It starts at 5 deliberately. v1-v4 left no artifact this can distinguish
#: (v4's changes went into the fresh-database-only script -- see the NOTE in
#: ``init_schema``), so a store below 5 reads as UNKNOWN rather than being
#: assigned a number this module cannot actually justify.
SHAPE_LADDER: tuple[tuple[int, str, str, str], ...] = (
    (5, "table", "dm_messages", ""),
    (5, "table", "dm_threads", ""),
    (5, "table", "dm_receipts", ""),
    (5, "table", "dm_thread_member_events", ""),
    (6, "column", "tasks", "revision"),
    (7, "trigger", "tasks_bump_revision", ""),
)

#: The lowest version this module can justify from physical evidence.
LADDER_FLOOR = 5


class ShapeAgreement(enum.Enum):
    """Three-valued, because "I cannot tell" is a real answer here.

    A store below the ladder floor genuinely cannot be placed, and collapsing
    that into either AGREES or DISAGREES would be inventing a reading.
    """

    AGREES = "agrees"
    STAMP_IS_LOW = "stamp-is-low"
    STAMP_IS_HIGH = "stamp-is-high"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SchemaShape:
    """What the store IS, what it SAYS, and whether those match.

    Both stamps are carried separately and neither is merged into a single
    "version" field, because they demonstrably disagree with each other on the
    live store and a caller must be able to see which one it is looking at.
    """

    observed: int | None
    stamped_meta: int | None
    stamped_pragma: int | None
    agreement: ShapeAgreement
    broken_rung: str = ""

    def __post_init__(self) -> None:
        if self.observed is not None and self.observed < 0:
            raise ValueError(f"observed must be >= 0 or None, got {self.observed}")
        if not isinstance(self.agreement, ShapeAgreement):
            raise TypeError(
                f"agreement must be ShapeAgreement, got {type(self.agreement).__name__}"
            )
        if self.agreement is not ShapeAgreement.UNKNOWN and self.observed is None:
            raise ValueError(
                "a conclusive agreement requires an observed version; with no "
                "physical reading there is nothing to compare the stamp against"
            )

    @property
    def trustworthy_version(self) -> int | None:
        """The version a caller should ACT on: the physical one, or nothing.

        Never falls back to a stamp. The whole point of this module is that a
        stamp is a claim by whichever client wrote last, and on this store
        that claim was measured wrong in the unsafe direction.
        """
        return self.observed


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
    conn.execute(f"PRAGMA user_version={stamp}")
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = "
        # MAX in SQL, not in Python: another process may have raised it between
        # our PRAGMA read and this statement, and CAST makes the comparison
        # numeric rather than lexicographic ('10' < '9' as text).
        "  CAST(MAX(CAST(schema_meta.value AS INTEGER), "
        "           CAST(excluded.value AS INTEGER)) AS TEXT)",
        (str(stamp),),
    )


def _has_table(conn, name: str) -> bool:
    """Delegated so the ladder reads the right catalogue on either backend."""
    return has_table(conn, name)


def _has_trigger(conn, name: str) -> bool:
    """Delegated: sqlite_master does not exist on PostgreSQL, and a rung that
    cannot be seen is reported ABSENT -- which downgrades the observed version
    rather than erroring, the quiet direction."""
    return has_trigger(conn, name)


def _has_column(conn, table: str, column: str) -> bool:
    if not _has_table(conn, table):
        return False
    return any(r[1] == column for r in conn.execute(f'PRAGMA table_info("{table}")'))


def _rung_present(conn, kind: str, name: str, extra: str) -> bool:
    if kind == "table":
        return _has_table(conn, name)
    if kind == "trigger":
        return _has_trigger(conn, name)
    if kind == "column":
        return _has_column(conn, name, extra)
    raise ValueError(f"unknown ladder rung kind: {kind!r}")


def _read_stamps(conn) -> tuple[int | None, int | None]:
    try:
        pragma = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except (ValueError, TypeError, IndexError):
        pragma = None
    meta = None
    if _has_table(conn, "schema_meta"):
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if row is not None:
            try:
                meta = int(row[0])
            except (ValueError, TypeError):
                meta = None
    return meta, pragma


def observed_version(conn) -> SchemaShape:
    """Report the store's version from its physical artifacts, not its stamp.

    Walks :data:`SHAPE_LADDER` upward and stops at the first missing rung. The
    returned :class:`SchemaShape` also carries both stamps so a caller can see
    the disagreement rather than only its resolution.

    Read-only: safe against a ``mode=ro`` connection, and deliberately so --
    the cutover needs to verify a store it has quiesced.
    """
    observed: int | None = None
    broken = ""
    for version, kind, name, extra in SHAPE_LADDER:
        if _rung_present(conn, kind, name, extra):
            observed = version if observed is None else max(observed, version)
            continue
        # First gap ends the ladder. Anything above it is unreachable evidence:
        # present-but-stranded artifacts mean a broken chain, not a version.
        broken = f"{kind} {name}{('.' + extra) if extra else ''} (v{version})"
        for higher_v, higher_k, higher_n, higher_e in SHAPE_LADDER:
            if higher_v > version and _rung_present(conn, higher_k, higher_n, higher_e):
                broken += (
                    f"; but v{higher_v}'s {higher_k} {higher_n} IS present -- "
                    "the migration chain is inconsistent"
                )
                break
        break

    meta, pragma = _read_stamps(conn)

    if observed is None:
        return SchemaShape(
            observed=None,
            stamped_meta=meta,
            stamped_pragma=pragma,
            agreement=ShapeAgreement.UNKNOWN,
            broken_rung=broken or f"no rung at or above v{LADDER_FLOOR} is present",
        )

    # Compare against the LOWER of the two stamps. They disagree with each
    # other on the live store, and the low one is what a gating reader acts
    # on -- reporting agreement because the other stamp happened to be right
    # would hide the failure that actually bites.
    stamps = [s for s in (meta, pragma) if s is not None]
    if not stamps:
        agreement = ShapeAgreement.UNKNOWN
        return SchemaShape(None, meta, pragma, agreement, "no stamp to compare")
    lowest = min(stamps)
    if lowest < observed:
        agreement = ShapeAgreement.STAMP_IS_LOW
    elif max(stamps) > observed:
        agreement = ShapeAgreement.STAMP_IS_HIGH
    else:
        agreement = ShapeAgreement.AGREES

    return SchemaShape(
        observed=observed,
        stamped_meta=meta,
        stamped_pragma=pragma,
        agreement=agreement,
        broken_rung=broken,
    )


# EOF
