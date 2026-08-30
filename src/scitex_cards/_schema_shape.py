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
* ``PRAGMA user_version`` is not a table write. NO trigger can reach it. That
  half is NOT fixed here and cannot be; it will keep oscillating until the last
  old client is gone.

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

import enum
from dataclasses import dataclass

from ._schema_probe import (
    _is_postgres,
    _sole_value,
    has_column,
    has_table,
    has_trigger,
)

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


# Re-exported so every existing ``from ._schema_shape import ...`` keeps
# resolving. These are FORWARDS, not copies -- the identity of each object is
# asserted by a test, because a re-export that shadows rather than forwards is
# indistinguishable from one that works until two callers compare instances.
from ._schema_floor import (  # noqa: E402
    SCHEMA_VERSION_DOWNGRADE_KEYS,
    SCHEMA_VERSION_FLOOR_TRIGGER,
    SCHEMA_VERSION_FLOOR_TRIGGER_SQL,
    DowngradeReport,
    downgrade_report,
    stamp_schema_version,
)
from ._schema_ladder import LADDER_FLOOR, SHAPE_LADDER  # noqa: E402
from ._schema_ladder import _rung_present  # noqa: E402
from ._schema_probe import has_table as _has_table  # noqa: E402


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



def _read_stamps(conn) -> tuple[int | None, int | None]:
    # PostgreSQL has no PRAGMA at all, so it has no second stamp to disagree
    # with: `meta` is the only reading, and None here says "this backend does
    # not carry one" rather than "the read failed".
    if _is_postgres(conn):
        pragma = None
    else:
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
                # _sole_value, NOT row[0]: psycopg's dict_row yields a real
                # dict, which raises KeyError on a positional index. The
                # version read must not crash on the backend it is being
                # taught to read.
                meta = int(_sole_value(row))
            except (ValueError, TypeError, KeyError, StopIteration):
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
