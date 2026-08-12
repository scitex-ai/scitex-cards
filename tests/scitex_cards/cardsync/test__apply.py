#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Applying verdicts: compare-and-set, no delete, dry-run by default.

The store here is a REAL in-memory implementation of the CardStore
protocol, not a mock — it actually stores bytes and actually enforces the
compare-and-set, so these tests exercise the same contract Postgres does.
A mock would only assert that we called something.

``FlakyStore`` simulates the one case that matters and is hard to catch in
production: a row changing between the read and the write.
"""

from __future__ import annotations

import json

from scitex_cards.cardsync._apply import reconcile


class MemStore:
    """An honest little card store. Enforces CAS; has no delete."""

    def __init__(self, name, cards=None):
        self.name = name
        self._rows = {k: json.dumps(v, sort_keys=True) for k, v in (cards or {}).items()}
        self.writes = []

    def read_all(self):
        return dict(self._rows)

    def write(self, card_id, new_raw, expected_raw):
        if self._rows.get(card_id) != expected_raw:
            return False
        self._rows[card_id] = new_raw
        self.writes.append(card_id)
        return True

    def card(self, cid):
        return json.loads(self._rows[cid]) if cid in self._rows else None


class FlakyStore(MemStore):
    """Mutates one row just before the write — a real concurrent writer."""

    def __init__(self, name, cards, poison_id):
        super().__init__(name, cards)
        self._poison = poison_id

    def write(self, card_id, new_raw, expected_raw):
        if card_id == self._poison and self._poison in self._rows:
            self._rows[card_id] = json.dumps({"id": card_id, "note": "someone else"})
            self._poison = None
        return super().write(card_id, new_raw, expected_raw)


def _c(cid, last, **over):
    d = {"id": cid, "status": "open", "last_activity": last}
    d.update(over)
    return d


# -- dry run is the default ----------------------------------------------
def test_dry_run_is_the_default():
    # Arrange
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T12:00:00Z", status="done")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z")})
    # Act
    report = reconcile(a, b)
    # Assert
    assert report.dry_run is True


def test_dry_run_writes_nothing():
    # Arrange
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T12:00:00Z", status="done")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z")})
    # Act
    reconcile(a, b)
    # Assert
    assert b.writes == []


def test_dry_run_still_counts_what_it_would_do():
    # Arrange
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T12:00:00Z", status="done")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z")})
    # Act
    report = reconcile(a, b)
    # Assert
    assert report.applied == 1


# -- copying in both directions ------------------------------------------
def test_newer_on_a_is_written_to_b():
    # Arrange
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T12:00:00Z", status="done")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z")})
    # Act
    reconcile(a, b, apply=True)
    # Assert
    assert b.card("c1")["status"] == "done"


def test_a_card_missing_on_b_is_created_there():
    # Arrange
    a = MemStore("a", {"only": _c("only", "2026-08-10T12:00:00Z")})
    b = MemStore("b", {})
    # Act
    reconcile(a, b, apply=True)
    # Assert
    assert b.card("only") is not None


def test_a_card_missing_on_a_is_created_there():
    # Arrange
    a = MemStore("a", {})
    b = MemStore("b", {"only": _c("only", "2026-08-10T12:00:00Z")})
    # Act
    reconcile(a, b, apply=True)
    # Assert
    assert a.card("only") is not None


def test_identical_stores_write_nothing():
    # Arrange
    same = {"c1": _c("c1", "2026-08-10T10:00:00Z")}
    a, b = MemStore("a", same), MemStore("b", same)
    # Act
    report = reconcile(a, b, apply=True)
    # Assert
    assert report.applied == 0


def test_identical_stores_are_counted_as_equal():
    # Arrange
    same = {"c1": _c("c1", "2026-08-10T10:00:00Z")}
    a, b = MemStore("a", same), MemStore("b", same)
    # Act
    report = reconcile(a, b, apply=True)
    # Assert
    assert report.already_equal == 1


# -- compare-and-set: a row that moved is SKIPPED, never clobbered -------
def test_a_row_changed_under_us_is_skipped():
    # Arrange — b mutates c1 between our read and our write
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T12:00:00Z", status="done")})
    b = FlakyStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z")}, poison_id="c1")
    # Act
    report = reconcile(a, b, apply=True)
    # Assert
    assert report.skipped_changed == 1


def test_the_concurrent_writers_value_survives():
    # Arrange — the whole point: their edit must NOT be overwritten
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T12:00:00Z", status="done")})
    b = FlakyStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z")}, poison_id="c1")
    # Act
    reconcile(a, b, apply=True)
    # Assert
    assert b.card("c1")["note"] == "someone else"


# -- unresolved is reported, never guessed -------------------------------
def test_an_unresolved_card_is_not_written():
    # Arrange — equal timestamps, no completion stamp either side
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T10:00:00Z", status="blocked")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z", status="deferred")})
    # Act
    reconcile(a, b, apply=True)
    # Assert
    assert b.writes == []


def test_an_unresolved_card_is_reported_with_its_id():
    # Arrange
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T10:00:00Z", status="blocked")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z", status="deferred")})
    # Act
    report = reconcile(a, b, apply=True)
    # Assert
    assert report.unresolved[0][0] == "c1"


def test_unresolved_is_not_folded_into_skipped():
    # Arrange — different facts; a human must see the unresolved ones
    a = MemStore("a", {"c1": _c("c1", "2026-08-10T10:00:00Z", status="blocked")})
    b = MemStore("b", {"c1": _c("c1", "2026-08-10T10:00:00Z", status="deferred")})
    # Act
    report = reconcile(a, b, apply=True)
    # Assert
    assert report.skipped_changed == 0


# -- nothing is ever removed ---------------------------------------------
def test_reconciling_never_removes_a_card():
    # Arrange — a has one b lacks, b has one a lacks; both must survive
    a = MemStore("a", {"x": _c("x", "2026-08-10T10:00:00Z")})
    b = MemStore("b", {"y": _c("y", "2026-08-10T10:00:00Z")})
    # Act
    reconcile(a, b, apply=True)
    # Assert
    assert (a.card("x"), a.card("y")) != (None, None) and b.card("x") is not None

# EOF
