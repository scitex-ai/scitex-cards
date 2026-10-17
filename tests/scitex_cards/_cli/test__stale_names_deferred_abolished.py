#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the CLI stale fixture names (pending abolition).

``tests/scitex_cards/_cli/test__stale.py`` seeds were repointed to
``status: deferred`` (the backlog state since ``pending`` was abolished
2026-07-10) but the ids, titles, and test names still said ``pending``
(``old-pending-card``, ``recent-pending-card``, ``undated pending card``,
``test_stale_list_includes_old_pending`` ...). The CLI was already correct
(``list-stale`` filters ``deferred``); only the names lied, inviting the
next copy-paste to reseed a dead status as live. Same dead-prose class as
the ``graph_fleet`` ``bq-pending`` -> ``bq-deferred`` repoint.

These pins use file text + imports only: no store, no PostgreSQL — they run
in containers where the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from pathlib import Path

import scitex_cards
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_SUBJECT = (
    Path(scitex_cards.__file__).parent.parent.parent
    / "tests"
    / "scitex_cards"
    / "_cli"
    / "test__stale.py"
)


def _text() -> str:
    return _SUBJECT.read_text(encoding="utf-8")


def test_fixture_names_no_pending_card_ids():
    # Arrange
    # Act
    # Assert
    assert "pending-card" not in _text()


def test_fixture_titles_no_pending_card_prose():
    # Arrange
    # Act
    # Assert
    assert "pending card" not in _text()


def test_test_names_no_pending_case1():
    # Arrange
    # Act
    names = [
        line.split("def ")[1].split("(")[0]
        for line in _text().splitlines()
        if line.startswith("def test_")
    ]
    # Assert
    assert names, "vacuity: no tests found in the subject file"


def test_test_names_no_pending_case2():
    # Arrange
    # Act
    names = [
        line.split("def ")[1].split("(")[0]
        for line in _text().splitlines()
        if line.startswith("def test_")
    ]
    # Assert
    assert not [n for n in names if "pending" in n], names


def test_successor_deferred_names_present_case1():
    # Arrange
    # Act
    text = _text()
    # Assert
    assert "old-deferred-card" in text


def test_successor_deferred_names_present_case2():
    # Arrange
    # Act
    text = _text()
    # Assert
    assert "recent-deferred-card" in text


def test_abolished_live_mirror_case1():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_abolished_live_mirror_case2():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_abolished_live_mirror_case3():
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
