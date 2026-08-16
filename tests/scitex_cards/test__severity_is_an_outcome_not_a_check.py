#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Severity belongs to the OUTCOME, not to the check.

THE HOLE THIS CLOSES WAS MINE, AND IT WAS TWENTY MINUTES OLD. #819 gave every
check a static severity, and classified `terminal_state_honest` and
`no_falsely_blocked` as ADVISORY — correctly, for what they exist to report:
stale completion stamps and dead dependency gates, thirteen untidy rows that
block nobody.

But both checks ALSO return ``ok: False`` with

    cannot read the task store (ExportRefused: notifications row '<id>' has no
    record_json payload)

when the store will not open. That is not a data-quality finding. IT IS THE
OUTAGE, surfacing through whichever check happened to touch the store first —
and it is exactly what those two printed during the 2026-08-11/12 incident,
where an agent read the report, believed the board was merely untidy, and
stopped carding for hours.

A static ADVISORY label would have filed a live outage under "board contents
only, nothing blocked". That is the same collapse the severity axis exists to
prevent, committed by the fix for it, pointing the other way.

ESCALATION ONLY. A check may raise its own outcome to BLOCKING; it may not
lower one. A check can talk its way INTO being blocking, never out of it —
otherwise the axis becomes a way for a failing probe to excuse itself.
"""

from __future__ import annotations

import pytest

from scitex_cards._health_severity import ADVISORY, BLOCKING, DELIVERY, _run_check


def _advisory_finding():
    """What the check exists to report: untidy rows, nothing blocked."""
    return {"ok": False, "detail": "5 cards carry a stale stamp", "hint": "clear it"}


def _store_unreadable():
    """The outage, surfacing through an advisory check."""
    return {
        "ok": False,
        "severity": BLOCKING,
        "detail": "cannot read the task store (ExportRefused: ...)",
        "hint": "the STORE is unreadable",
    }


class TestAnUnreadableStoreEscalates:
    def test_an_advisory_check_reporting_an_unreadable_store_becomes_blocking(self):
        # Arrange
        check = _store_unreadable
        # Act
        _record, severity = _run_check("terminal_state_honest", check, severity=ADVISORY)
        # Assert
        assert severity == BLOCKING

    def test_a_raising_check_does_NOT_escalate(self):
        """I shipped the opposite of this first, and an hour-old test of my own
        caught it.

        The argument for escalating was "a probe that raised measured nothing".
        It does not hold: an unreadable store is caught by the check's OWN
        except and escalated deliberately, so anything that escapes to
        `_run_check`'s handler is an INTERNAL BUG IN THE PROBE. A buggy probe is
        not an availability fact, and reporting one as an outage is this axis
        collapsing toward the alarming pole — precisely what it exists to
        prevent.
        """

        # Arrange
        def explodes():
            raise RuntimeError("boom")

        # Act
        _record, severity = _run_check("terminal_state_honest", explodes, severity=ADVISORY)
        # Assert
        assert severity == ADVISORY


class TestOrdinaryFindingsStayAdvisory:
    """THE POSITIVE CONTROL, and the reason this file is not one test.

    Escalating everything would satisfy every assertion above and re-create the
    original incident exactly: thirteen untidy rows reported as an outage, which
    is what stopped an agent working in the first place.
    """

    def test_an_advisory_finding_stays_advisory(self):
        # Arrange
        check = _advisory_finding
        # Act
        _record, severity = _run_check("terminal_state_honest", check, severity=ADVISORY)
        # Assert
        assert severity == ADVISORY

    def test_a_delivery_finding_stays_delivery(self):
        # Arrange
        check = _advisory_finding
        # Act
        _record, severity = _run_check("notifyd_alive", check, severity=DELIVERY)
        # Assert
        assert severity == DELIVERY

    def test_a_passing_advisory_check_stays_advisory(self):
        # Arrange
        check = lambda: {"ok": True, "detail": "no zombies", "hint": None}  # noqa: E731
        # Act
        _record, severity = _run_check("terminal_state_honest", check, severity=ADVISORY)
        # Assert
        assert severity == ADVISORY


class TestEscalationIsOneWay:
    """A check must not be able to excuse itself down the scale."""

    def test_a_blocking_check_cannot_declare_itself_advisory(self):
        # Arrange
        def wants_out():
            return {"ok": False, "severity": ADVISORY, "detail": "store gone", "hint": "x"}

        # Act
        _record, severity = _run_check("store_canonical", wants_out, severity=BLOCKING)
        # Assert
        assert severity == BLOCKING

    def test_a_delivery_check_cannot_declare_itself_advisory(self):
        # Arrange
        def wants_out():
            return {"ok": False, "severity": ADVISORY, "detail": "no notifyd", "hint": "x"}

        # Act
        _record, severity = _run_check("notifyd_alive", wants_out, severity=DELIVERY)
        # Assert
        assert severity == DELIVERY


class TestTheEscalationIsWiredIn:
    """Defining the mechanism and USING it are different facts, and grepping for
    a symbol to check the second is what failed three times on 2026-08-12."""

    def test_both_unreadable_store_branches_declare_blocking(self):
        # Arrange
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "src/scitex_cards/_health_cards.py"
        )
        # Act
        occurrences = source.read_text(encoding="utf-8").count('"severity": BLOCKING')
        # Assert
        assert occurrences == 2


# EOF
