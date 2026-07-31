#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove a database guard ENFORCES — and make the vacuous proof unconstructible.

WHY THIS EXISTS: THE SAME TRAP CAUGHT TWO AGENTS IN ONE DAY
------------------------------------------------------------
2026-07-30, twice, independently:

  * I wrote a test that attempted ``DELETE FROM dm_messages WHERE id =
    'nonexistent-id'`` and concluded "NOT ENFORCED" — against a store where
    the guard demonstrably refuses. A ``BEFORE DELETE`` trigger fires PER ROW,
    so deleting zero rows succeeds trivially.
  * scitex-db then specified a cutover pre-check as "set ``store_status``
    retired->current, expect ABORT". On a store with no ``store_status`` row
    that ``UPDATE`` also matches zero rows, so it would have reported the
    guard dead and halted a cutover for the wrong reason.

Both are the same shape: **a statement that touches nothing cannot be
refused, and "was not refused" reads identically to "is not guarded".**

A written warning would not have helped — both of us knew the principle and
wrote the check anyway, hours apart. So the rule is mechanical here: this
module REFUSES to render a verdict when the statement affected zero rows.
The vacuous probe is not a mistake you can make; it is a state you cannot
construct.

WHY A MESSAGE FRAGMENT IS REQUIRED — scitex-db's contribution
--------------------------------------------------------------
"Did it raise?" is not enough. A read-only connection raises. A typo'd table
name raises. A locked database raises. Every one of those would read as
ENFORCED by a probe that only catches an exception. So the caller must state
a fragment of the guard's OWN refusal message, and a probe without one is
refused rather than run.

THREE-VALUED, PER THE CONSTITUTION
-----------------------------------
"Every signal is three-valued: true, false, and *unknown*. Collapsing unknown
into either pole is the most common bug we ship." So the verdict is an enum,
not a bool, and ``INCONCLUSIVE`` is a first-class answer that a caller must
handle rather than a falsy value it can ignore.
"""

from __future__ import annotations

# Shape-agnostic row access. psycopg's dict_row is a real dict and raises
# KeyError on a positional index, and since #693 open_db can hand this
# module a PostgreSQL connection. _schema_probe imports nothing from this
# package, so a module-level import here cannot cycle.
from ._schema_probe import _sole_value

import enum
from dataclasses import dataclass, field

__all__ = [
    "Enforcement",
    "EnforcementVerdict",
    "VacuousProbe",
    "probe_enforcement",
]


class Enforcement(enum.Enum):
    """The three answers. ``INCONCLUSIVE`` is an answer, not a failure."""

    ENFORCED = "enforced"
    NOT_ENFORCED = "not-enforced"
    INCONCLUSIVE = "inconclusive"


class VacuousProbe(RuntimeError):
    """The probe could not have been refused, so it proves nothing.

    Raised when the forbidden statement affected ZERO rows. This is the trap
    that caught two agents on 2026-07-30 and it is why this module exists:
    without this refusal the caller receives a confident ``NOT_ENFORCED`` that
    is really "I did not test anything".
    """


@dataclass(frozen=True)
class EnforcementVerdict:
    """The fixed shape every probe returns. Never a bare bool or a dict.

    Each signal is its own named field so a caller never has to guess which
    key exists on this call, per the constitution's answer-shape rule.
    """

    outcome: Enforcement
    rows_touched: int
    refusal_message: str = ""
    expected_fragment: str = ""
    detail: str = ""
    _validated: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        # Validator at construction, so a malformed verdict fails HERE rather
        # than three layers downstream where the shape is trusted.
        if not isinstance(self.outcome, Enforcement):
            raise TypeError(
                f"outcome must be Enforcement, got {type(self.outcome).__name__}"
            )
        if self.rows_touched < 0:
            raise ValueError(f"rows_touched must be >= 0, got {self.rows_touched}")
        if self.outcome is Enforcement.ENFORCED and not self.refusal_message:
            raise ValueError("ENFORCED requires the refusal message that proved it")
        if self.outcome is not Enforcement.INCONCLUSIVE and self.rows_touched == 0:
            raise ValueError(
                "a verdict of ENFORCED or NOT_ENFORCED with rows_touched == 0 is "
                "vacuous: nothing was attempted, so nothing was refused"
            )

    @property
    def proven(self) -> bool:
        """True only for ENFORCED. INCONCLUSIVE is NOT a pass."""
        return self.outcome is Enforcement.ENFORCED


def _rows_affected(conn, table: str, where: str, params) -> int:
    """How many rows the forbidden statement WOULD touch.

    Counted before attempting it, because after a refusal there is nothing to
    count and after a success the rows are gone.
    """
    sql = f'SELECT COUNT(*) FROM "{table}" WHERE {where}'
    return int(_sole_value(conn.execute(sql, tuple(params)).fetchone()))


def probe_enforcement(
    conn,
    *,
    table: str,
    where: str,
    statement: str,
    expect_refusal_containing: str,
    params=(),
    refusal_types=(Exception,),
) -> EnforcementVerdict:
    """Attempt a forbidden ``statement`` and report whether the guard refused it.

    ``expect_refusal_containing`` IS REQUIRED and must be non-empty: a probe
    that accepts any exception cannot tell a guard from a typo, a read-only
    connection, or a lock.

    RAISES :class:`VacuousProbe` when ``table``/``where`` select zero rows,
    because a per-row trigger cannot fire on an empty set and the resulting
    "success" would be indistinguishable from an absent guard. If you are
    probing a guard whose precondition does not exist yet — a retirement guard
    on a store that was never retired — MANUFACTURE the precondition inside
    your own transaction first, then call this, then roll back.

    Does NOT manage the transaction. The caller owns it, must roll back, and
    the docstring says so rather than this function silently committing a
    forbidden write it only meant to attempt.
    """
    if not expect_refusal_containing or not expect_refusal_containing.strip():
        raise ValueError(
            "expect_refusal_containing is required: a probe that accepts any "
            "exception cannot distinguish a guard from a typo, a read-only "
            "connection, or a locked database"
        )

    touched = _rows_affected(conn, table, where, params)
    if touched == 0:
        raise VacuousProbe(
            f'the forbidden statement matches ZERO rows in "{table}" where {where} — '
            "a per-row trigger cannot fire, so this probe would report NOT_ENFORCED "
            "on a guard that works. Manufacture the precondition inside a "
            "transaction first, then probe, then roll back."
        )

    try:
        conn.execute(statement, tuple(params))
    except refusal_types as exc:  # noqa: BLE001 — the caller declares the type
        message = str(exc)
        if expect_refusal_containing in message:
            return EnforcementVerdict(
                outcome=Enforcement.ENFORCED,
                rows_touched=touched,
                refusal_message=message,
                expected_fragment=expect_refusal_containing,
            )
        return EnforcementVerdict(
            outcome=Enforcement.INCONCLUSIVE,
            rows_touched=touched,
            refusal_message=message,
            expected_fragment=expect_refusal_containing,
            detail=(
                "something refused the statement, but the message does not carry the "
                "expected fragment — this may be a lock, a typo, or a read-only "
                "connection rather than the guard under test"
            ),
        )

    return EnforcementVerdict(
        outcome=Enforcement.NOT_ENFORCED,
        rows_touched=touched,
        expected_fragment=expect_refusal_containing,
        detail=f"the forbidden statement SUCCEEDED against {touched} row(s)",
    )


# EOF
