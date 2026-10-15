#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the honesty-check status sets (pending abolition).

``_check_terminal_state_honest`` (``scitex_cards._health_cards``) finds zombie
cards with two hardcoded tuples: ``_OPEN_STATUSES`` (a ``closed_at`` card must
not sit in one of these) and ``_TERMINAL_STATUSES`` (dependencies resolved in
one of these). A status that lands in ``VALID_STATUSES`` but in NEITHER tuple
is invisible to the zombie rule — the check reports ok on exactly the rows it
exists to find — and a status in BOTH tuples is a contradiction. The abolished
``pending`` (2026-07-10) must be in neither: it is tolerated on READ for
legacy rows, but no live row may be minted in it, so the detector must not
treat it as a live open state.

``_throughput.TERMINAL_STATUSES`` is a second spelling of the terminal set
(the throughput/timeline aggregates consult it, never ``status`` prose). The
two spellings must agree, or one surface counts a card as delivered while the
other still nags it as backlog.

These pins import modules only: no store, no PostgreSQL — they run in
containers where the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from scitex_cards._health_cards import (
    _COMPLETED_STATUS,
    _OPEN_STATUSES,
    _TERMINAL_STATUSES,
)
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._throughput import TERMINAL_STATUSES


def test_open_and_terminal_partition_the_canonical_enum():
    # Arrange — the three spellings of "every status".
    # Act / Assert — the honesty tuples must cover VALID_STATUSES exactly:
    # a future status added to the enum but to neither tuple would make the
    # zombie rule lie by omission (cf. the 2026-07-14 completed_at omission
    # the module docstring records).
    assert set(_OPEN_STATUSES) | set(_TERMINAL_STATUSES) == set(VALID_STATUSES)


def test_open_and_terminal_are_disjoint():
    assert set(_OPEN_STATUSES) & set(_TERMINAL_STATUSES) == set()


def test_abolished_statuses_are_in_neither_honesty_set():
    # Arrange — every abolished value must stay out of both tuples.
    # Act / Assert.
    for status in ABOLISHED_STATUSES:
        assert status not in VALID_STATUSES
        assert status not in _OPEN_STATUSES
        assert status not in _TERMINAL_STATUSES
    assert "pending" in ABOLISHED_STATUSES


def test_health_terminal_set_agrees_with_throughput():
    # Arrange — two spellings of the terminal set (honesty detector vs the
    # throughput/timeline aggregates).
    # Act / Assert — they must name the same statuses.
    assert set(_TERMINAL_STATUSES) == set(TERMINAL_STATUSES)


def test_completed_status_is_done_and_terminal():
    assert _COMPLETED_STATUS == "done"
    assert _COMPLETED_STATUS in _TERMINAL_STATUSES
    assert _COMPLETED_STATUS not in _OPEN_STATUSES


def test_goal_is_open_not_terminal():
    # Arrange — ``goal`` is the north-star objective state: open work, never
    # a completion, so a closed-stamped goal row is a zombie by construction.
    # Act / Assert.
    assert "goal" in _OPEN_STATUSES
    assert "goal" not in _TERMINAL_STATUSES


def test_honesty_sets_are_not_vacuous():
    assert len(_OPEN_STATUSES) >= 2
    assert len(_TERMINAL_STATUSES) >= 2
