#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No JobSpec this package declares may still say ``scitex-cards``.

The package was renamed to scitex-cards. The operator's instruction was that
the rename be complete rather than incremental (2026-08-15, verbatim: 「new
only というのはダメです」 / 「全部ハードにしないと」 — roughly "new-only is not
acceptable" / "it has to be done hard, all of it"), on the grounds that a
half-finished rename accrues interest: 「負債は利子が広がって大きくなりますよ」.

WHY THIS IS A TEST AND NOT A ONE-TIME EDIT. The strings here become things
the fleet runs: ``command`` is the argv a host executes unattended, ``name``
is the identity a supervisor schedules under. A stale ``scitex-cards`` in
either is not a cosmetic typo — it is either a command that invokes the old
console script, or a unit registered under a name nobody greps for anymore.
Both fail quietly, on a timer, on a machine nobody is watching.

An edit fixes today's file. Only a check fixes tomorrow's, and the next
JobSpec will be added by someone who was not part of the rename.

WHAT THIS DELIBERATELY DOES NOT ASSERT. It does not require that the strings
say ``scitex-cards`` — only that they no longer say the retired name. A future
job could legitimately invoke some other tool entirely, and this file should
not be the reason that is hard.
"""

from __future__ import annotations

import pytest

from scitex_cards._jobs_provider import provide_jobs

#: The retired package name, in the spellings that would actually reach a
#: shell or a unit file. The console script and the systemd prefix are the
#: two that execute; the underscore form is the importable module, which is
#: a DIFFERENT decision (``src/scitex_cards/`` is a deliberate, still-load-
#: bearing import shim) and is intentionally not covered here.
RETIRED_NAME = "scitex-cards"

#: The JobSpec fields whose contents are executed or scheduled, as opposed to
#: merely read by a human.
LOAD_BEARING_FIELDS = ("name", "command")


def _scheduled_jobs():
    """Every job this package asks the host to run unattended."""
    return list(provide_jobs())


class TestJobSpecsDoNotCarryTheRetiredName:
    """The rename reached the strings the fleet actually runs."""

    def test_at_least_one_job_is_provided(self):
        # Arrange
        provider = provide_jobs
        # Act
        jobs = list(provider())
        # Assert — guard the guard: an empty provider would satisfy every
        # "no offenders" assertion below while checking nothing.
        assert jobs, (
            "provide_jobs() returned nothing, so the checks in this file would "
            "pass vacuously. That is the 'gate that cannot fail' shape."
        )

    @pytest.mark.parametrize("field", LOAD_BEARING_FIELDS)
    def test_no_load_bearing_field_names_the_retired_package(self, field):
        # Arrange
        jobs = _scheduled_jobs()
        # Act
        offenders = [
            j.name for j in jobs if RETIRED_NAME in (getattr(j, field) or "")
        ]
        # Assert
        assert not offenders, (
            f"job(s) {offenders} still carry {RETIRED_NAME!r} in their "
            f"{field!r}. A stale name here is executed, not just read: as a "
            f"`command` it invokes the retired console script, and as a `name` "
            f"it schedules under an identity the fleet no longer greps for."
        )

    def test_no_description_names_the_retired_package(self):
        # Arrange
        jobs = _scheduled_jobs()
        # Act
        offenders = [
            j.name for j in jobs if RETIRED_NAME in (j.description or "")
        ]
        # Assert — descriptions are what the operator reads in `systemctl
        # status` to decide whether a unit is the one they meant.
        assert not offenders, (
            f"job(s) {offenders} describe themselves using {RETIRED_NAME!r}, "
            f"which is the text shown next to the unit in systemctl output"
        )


# EOF
