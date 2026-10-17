#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the blocker-clear SET fixture (pending abolition).

``tests/scitex_cards/_cli/test__write_blocker_clear.py`` seeded its
SET-blocker round-trip card with ``status="pending"`` after ``pending``
was abolished 2026-07-10. The write-side validator only warns (tolerated
read/write), so the seed still landed — but the suite is a writer, and a
writer seeding the abolished value models the wrong default for the next
copy-paste. The test's intent is a SET-blocker round-trip
(``--blocker compute`` lands on the card), which is status-agnostic, so
the fixture now carries ``deferred`` (the successor default). Same
dead-fixture class as the org (18 fixtures) and mermaid (2 fixtures)
repoints.

These pins use file text, the ``add_task`` signature default, and
``CliRunner --help`` only: no store, no PostgreSQL — they run in
containers where the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._store import add_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_FIXTURE = (
    Path(__file__).resolve().parent
    / "_cli"
    / "test__write_blocker_clear.py"
).read_text(encoding="utf-8")


def test_fixture_seeds_no_abolished_status_case1():
    # Arrange
    # Act
    # Assert
    assert 'status="pending"' not in _FIXTURE


def test_fixture_seeds_no_abolished_status_case2():
    # Arrange
    # Act
    # Assert
    assert "status='pending'" not in _FIXTURE


def test_fixture_seeds_the_successor_default():
    # Arrange
    # Act
    # Assert
    assert 'status="deferred"' in _FIXTURE


def test_add_default_is_the_successor():
    # Arrange
    # Act
    # Assert
    assert inspect.signature(add_task).parameters["status"].default == "deferred"


def test_cli_update_status_choice_rejects_pending_case1():
    # Arrange
    out = CliRunner().invoke(main, ["update", "--help"]).output
    # Act
    m = re.search(r"--status \[([^\]]+)\]", out)
    # Assert
    choices = m.group(1).split("|")
    assert m, "--status closed choice missing from update --help"


def test_cli_update_status_choice_rejects_pending_case2():
    # Arrange
    out = CliRunner().invoke(main, ["update", "--help"]).output
    m = re.search(r"--status \[([^\]]+)\]", out)
    # Act
    choices = m.group(1).split("|")
    # Assert
    assert "pending" not in choices


def test_cli_update_status_choice_rejects_pending_case3():
    # Arrange
    out = CliRunner().invoke(main, ["update", "--help"]).output
    m = re.search(r"--status \[([^\]]+)\]", out)
    # Act
    choices = m.group(1).split("|")
    # Assert
    assert "deferred" in choices


def test_cli_update_status_choice_rejects_pending_case4():
    # Arrange
    out = CliRunner().invoke(main, ["update", "--help"]).output
    m = re.search(r"--status \[([^\]]+)\]", out)
    # Act
    choices = m.group(1).split("|")
    # Assert
    assert set(choices) == set(VALID_STATUSES)


def test_pending_stays_abolished_case1():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_stays_abolished_case2():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_pending_stays_abolished_case3():
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES


def test_pins_are_nonvacuous_case1():
    # Arrange
    # Act
    # Assert
    assert "t-set" in _FIXTURE


def test_pins_are_nonvacuous_case2():
    # Arrange
    # Act
    # Assert
    assert callable(add_task)
