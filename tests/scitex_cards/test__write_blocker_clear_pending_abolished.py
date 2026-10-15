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


def test_fixture_seeds_no_abolished_status():
    assert 'status="pending"' not in _FIXTURE
    assert "status='pending'" not in _FIXTURE


def test_fixture_seeds_the_successor_default():
    assert 'status="deferred"' in _FIXTURE


def test_add_default_is_the_successor():
    assert inspect.signature(add_task).parameters["status"].default == "deferred"


def test_cli_update_status_choice_rejects_pending():
    out = CliRunner().invoke(main, ["update", "--help"]).output
    m = re.search(r"--status \[([^\]]+)\]", out)
    assert m, "--status closed choice missing from update --help"
    choices = m.group(1).split("|")
    assert "pending" not in choices
    assert "deferred" in choices
    assert set(choices) == set(VALID_STATUSES)


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_pins_are_nonvacuous():
    assert "t-set" in _FIXTURE
    assert callable(add_task)
