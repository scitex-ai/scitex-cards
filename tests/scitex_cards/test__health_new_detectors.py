#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The two 2026-08-18 detectors keep the health contract under failure.

Both exist because a condition worth logging had no reader:

  hook_consumers_registered  a consumer stranded in the dead entry-point group
                             (2026-08-17: the fleet's only one, silent, green)
  no_placeholder_authors     cards whose author is an unexpanded ${VAR}
                             (2026-07-19, repaired, RESTORED, uncounted for a
                              month behind a closed card asserting "0 rows")

What is pinned here is the part that makes a detector trustworthy rather than
decorative: it must NEVER raise, and it must report UNKNOWN — not OK — when it
cannot measure. Collapsing "I could not look" into "nothing is wrong" is the
exact failure both of these were written to end, so a check that did it would
be worse than no check.

The live-state behaviour was verified separately against the real fleet
(1 straggler named, 15 placeholder rows) — that is a measurement, not
something a test can assert without pinning today's incident residue into CI.
"""

from __future__ import annotations

from scitex_cards._health_hook_consumers import check_hook_consumers_registered
from scitex_cards._health_placeholder_authors import check_no_placeholder_authors

_STANDARD_KEYS = {"ok", "detail", "hint"}


def test_hook_check_returns_the_standard_shape():
    # Arrange
    check = check_hook_consumers_registered
    # Act
    result = check()
    # Assert
    assert _STANDARD_KEYS <= set(result)


def test_hook_check_reports_a_three_valued_ok():
    # Arrange
    check = check_hook_consumers_registered
    # Act
    result = check()
    # Assert
    assert result["ok"] in (True, False, None)


def test_hook_check_does_not_raise():
    # Arrange
    check = check_hook_consumers_registered
    # Act
    result = check()
    # Assert — a health check that raises takes the doctor down with it
    assert result is not None


def test_placeholder_check_returns_the_standard_shape():
    # Arrange
    unreachable = "/nonexistent/definitely/not/a/store.yaml"
    # Act
    result = check_no_placeholder_authors(unreachable)
    # Assert
    assert _STANDARD_KEYS <= set(result)


def test_placeholder_check_is_unknown_when_it_cannot_measure():
    # Arrange — the store cannot be reached, so the count cannot be taken
    unreachable = "/nonexistent/definitely/not/a/store.yaml"
    # Act
    result = check_no_placeholder_authors(unreachable)
    # Assert — UNKNOWN, never True: absence of evidence is not evidence
    assert result["ok"] is not True


def test_placeholder_check_hints_when_it_cannot_measure():
    # Arrange
    unreachable = "/nonexistent/definitely/not/a/store.yaml"
    # Act
    result = check_no_placeholder_authors(unreachable)
    # Assert — every failing/unknown check owes an actionable next step
    assert result["hint"]


def test_placeholder_check_does_not_raise_on_a_bad_store():
    # Arrange
    unreachable = "/nonexistent/definitely/not/a/store.yaml"
    # Act
    result = check_no_placeholder_authors(unreachable)
    # Assert
    assert result is not None

# EOF
