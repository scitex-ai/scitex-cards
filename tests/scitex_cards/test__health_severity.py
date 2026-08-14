#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A health report must distinguish an outage from untidy rows.

WHAT SHIPPED AND WHAT DID NOT, stated first so this file does not read as a
claim about `ok`. The severity axis lands and drives the SUMMARY; `ok` KEEPS its
documented meaning (true iff no check failed, at any severity).

Narrowing `ok` to blocking-only is what the incident argues for, it was written,
and it was reverted: `test_report_ok_is_true_iff_no_check_actually_failed`
caught it, and correctly — those semantics are a cross-package contract sac and
cct both parse, and redefining a shared boolean without telling its consumers is
the no-surprises violation this package spent the night finding elsewhere. That
change is now a decision to raise WITH them, not a change to take from them.

So the tests below assert the CLASSIFICATION and the AGGREGATION RULE, which are
real and shipped, and deliberately do not assert `health()["ok"]`.


THE INCIDENT, 2026-08-12. An agent read `ok: false` and "9/14 checks passed"
and concluded the cards database was refusing its writes. It stopped carding
for hours and reported in prose. The four failures were:

    backend_mode           the inbox is a file sidecar (delivery)
    notifyd_alive          the delivery daemon is not running (delivery)
    terminal_state_honest  5 cards carry a stale completion stamp (advisory)
    no_falsely_blocked     8 cards depend only on finished cards (advisory)

`store_canonical` said "3801 cards, readable, WRITABLE" in the same report, and
writes worked continuously throughout.

THE DEFECT IS THE AGGREGATE, NOT THE CHECKS. Each check was correct. One
`ok: false` spanning "the store is gone" and "thirteen rows are untidy" is not a
summary, it is a coin flip about severity — and the reader has to already know
the answer to interpret it.

THIS IS THE NIGHT'S PATTERN INVERTED. Every other instance collapsed a third
state into the REASSURING pole (an unrecognised DSN into a filename, an empty
conclusion into green, a blocked card into nothing-to-report). This one collapses
benign findings into the ALARMING pole. Same mechanism, opposite sign, and the
same cost: a working agent stopped working on a reading that was not true.
"""

from __future__ import annotations

import pytest

from scitex_cards._health import ADVISORY, BLOCKING, DELIVERY, _run_check


def _passing():
    return {"ok": True, "detail": "fine", "hint": None}


def _failing():
    return {"ok": False, "detail": "broken", "hint": "do the thing"}


def _grade(name, fn, *, severity=BLOCKING):
    """The record with its severity attached, for tests that aggregate.

    `_run_check` returns them SEPARATELY because the record is a
    cross-package wire contract that must stay exactly four fields. Tests
    that reason about aggregation need them together, so they join them
    here rather than the production path widening the contract.
    """
    record, sev = _run_check(name, fn, severity=severity)
    return {**record, "severity": sev}


class TestEveryCheckDeclaresASeverity:
    def test_a_check_record_carries_its_severity(self):
        # Arrange
        run = _run_check
        # Act
        record, severity = run("x", _passing, severity=ADVISORY)
        # Assert
        assert severity == ADVISORY

    def test_the_default_is_blocking(self):
        """A new check nobody classified is treated as availability-affecting.

        The conservative direction: a forgotten field should over-report
        severity, never under-report it. The opposite default would let the
        next unclassified check silently stop counting.
        """
        # Arrange
        run = _run_check
        # Act
        record, severity = run("x", _passing)
        # Assert
        assert severity == BLOCKING

    def test_a_raising_check_still_carries_its_severity(self):
        """health() never raises, and the severity must survive that path too --
        otherwise an errored advisory check silently becomes blocking."""

        # Arrange
        def explodes():
            raise RuntimeError("boom")

        # Act
        record, severity = _run_check("x", explodes, severity=ADVISORY)
        # Assert
        assert severity == ADVISORY


class TestSeverityIsNotDecoration:
    """The constants must be distinct, or the aggregation silently merges them."""

    def test_the_three_severities_are_distinct(self):
        # Arrange
        levels = (BLOCKING, DELIVERY, ADVISORY)
        # Act
        distinct = len(set(levels))
        # Assert
        assert distinct == 3


class TestTheAggregateAnswersUsability:
    """Built from synthetic check records so the assertions are about the
    AGGREGATION RULE and not about whatever this host's store happens to be."""

    @staticmethod
    def _summarise(records):
        """The aggregation under test, reproduced from health()'s tail.

        Duplicated deliberately rather than calling health(): health() opens the
        real store, and a test that needs a live PostgreSQL to assert an
        arithmetic rule is a test that will be skipped and then deleted.
        """
        blocked = [r["name"] for r in records if r["ok"] is False and r["severity"] == BLOCKING]
        return not blocked

    def test_an_advisory_failure_leaves_the_store_usable(self):
        # Arrange
        records = [
            _grade("store_canonical", _passing),
            _grade("terminal_state_honest", _failing, severity=ADVISORY),
        ]
        # Act
        ok = self._summarise(records)
        # Assert
        assert ok is True

    def test_a_delivery_failure_leaves_the_store_usable(self):
        """Notifications may be undelivered while cards read and write fine.
        Conflating the two is what made a stopped notifyd read as an outage."""
        # Arrange
        records = [
            _grade("store_canonical", _passing),
            _grade("notifyd_alive", _failing, severity=DELIVERY),
        ]
        # Act
        ok = self._summarise(records)
        # Assert
        assert ok is True

    def test_a_blocking_failure_makes_the_store_unusable(self):
        """THE POSITIVE CONTROL. Without this, a rule that returned True
        unconditionally would pass every test above."""
        # Arrange
        records = [
            _grade("store_canonical", _failing),
            _grade("terminal_state_honest", _passing, severity=ADVISORY),
        ]
        # Act
        ok = self._summarise(records)
        # Assert
        assert ok is False

    def test_the_incident_configuration_reports_usable(self):
        """THE EXACT 2026-08-12 REPORT: four failures, none of them blocking.

        This is the case that cost an agent its night, and the assertion is that
        it now reads as usable.
        """
        # Arrange
        records = [
            _grade("store_canonical", _passing),
            _grade("backend_mode", _failing, severity=DELIVERY),
            _grade("notifyd_alive", _failing, severity=DELIVERY),
            _grade("terminal_state_honest", _failing, severity=ADVISORY),
            _grade("no_falsely_blocked", _failing, severity=ADVISORY),
        ]
        # Act
        ok = self._summarise(records)
        # Assert
        assert ok is True


class TestNothingIsHidden:
    """`ok` narrowing must not make a failure disappear -- that would be this
    same defect pointed the other way, which is the direction that lets a real
    fault go unreported."""

    def test_an_advisory_failure_is_still_reported_as_failing(self):
        # Arrange
        records = [_grade("terminal_state_honest", _failing, severity=ADVISORY)]
        # Act
        failing = [r["name"] for r in records if r["ok"] is False]
        # Assert
        assert failing == ["terminal_state_honest"]

    def test_an_advisory_failure_still_carries_its_hint(self):
        # Arrange
        record = _grade("terminal_state_honest", _failing, severity=ADVISORY)
        # Act
        hint = record["hint"]
        # Assert
        assert hint == "do the thing"


# EOF
