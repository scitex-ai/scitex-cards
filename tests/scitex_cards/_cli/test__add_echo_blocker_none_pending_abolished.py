#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the add-echo blocker-none fixtures (pending abolition).

``tests/scitex_cards/_cli/test__add_echo_and_blocker_none.py`` seeded its
three Bug-2 model-layer cards with ``status="pending"`` after ``pending``
was abolished 2026-07-10. The save-side validator only warns on an unknown
status (operator ruling 2026-07-10: a status value must never cost someone
their card), so the seeds still landed — but the suite is a writer, and a
writer seeding the abolished value models the wrong default for the next
copy-paste. The tests' intent is blocker-``none`` normalization (dropped in
place on any non-blocked row; a real blocker on a non-blocked row raises —
rules c/b in ``_validate.py``), which is status-agnostic, so the fixtures
now carry ``deferred`` (the successor default). Same dead-fixture class as
the org (18 fixtures), mermaid (2), idle-guard (4), write-blocker-clear (1),
and group-field (5) repoints.

Deliberately NOT repointed: the two CLI-rejection tests that pass
``--status pending`` on purpose — they pin the source-side closed enum
(``pending`` unreachable from the CLI), the negative control for this very
sweep.

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
    Path(__file__).resolve().parent / "test__add_echo_and_blocker_none.py"
).read_text(encoding="utf-8")


def _bug2_section() -> str:
    assert "Bug 2 at the model layer" in _FIXTURE
    return _FIXTURE.split("Bug 2 at the model layer")[1]


def _status_choices() -> list[str]:
    out = CliRunner().invoke(main, ["add", "--help"]).output
    m = re.search(r"--status \[([^\]]+)\]", out)
    assert m, "--status closed choice missing from add --help"
    return m.group(1).split("|")


def test_bug2_fixtures_seed_no_double_quoted_pending():
    # Arrange
    section = _bug2_section()
    # Act — none; the pin inspects fixture text loaded at import.
    # Assert
    assert '"status": "pending"' not in section


def test_bug2_fixtures_seed_no_single_quoted_pending():
    # Arrange
    section = _bug2_section()
    # Act — none; the pin inspects fixture text loaded at import.
    # Assert
    assert "'status': 'pending'" not in section


def test_bug2_fixtures_seed_the_successor_default():
    # Arrange
    section = _bug2_section()
    # Act — none; the pin inspects fixture text loaded at import.
    # Assert
    assert '"status": "deferred"' in section


def test_cli_rejection_pin_names_add_rejects_pending():
    # Arrange — none; the pin inspects the companion test file text.
    # Act — none; loaded at import.
    # Assert
    assert "test_add_rejects_abolished_pending_at_the_cli_boundary" in _FIXTURE


def test_cli_rejection_pin_names_pending_hint():
    # Arrange — none; the pin inspects the companion test file text.
    # Act — none; loaded at import.
    # Assert
    assert "test_add_rejecting_pending_names_the_bad_status" in _FIXTURE


def test_add_default_is_the_successor():
    # Arrange
    params = inspect.signature(add_task).parameters
    # Act
    default = params["status"].default
    # Assert
    assert default == "deferred"


def test_cli_add_status_choice_lists_closed_enum():
    # Arrange
    out = CliRunner().invoke(main, ["add", "--help"]).output
    # Act
    m = re.search(r"--status \[([^\]]+)\]", out)
    # Assert
    assert m, "--status closed choice missing from add --help"


def test_cli_add_status_choice_rejects_pending():
    # Arrange — none; help text is rendered on demand.
    # Act
    choices = _status_choices()
    # Assert
    assert "pending" not in choices


def test_cli_add_status_choice_offers_deferred():
    # Arrange — none; help text is rendered on demand.
    # Act
    choices = _status_choices()
    # Assert
    assert "deferred" in choices


def test_cli_add_status_choice_matches_valid_statuses():
    # Arrange — none; help text is rendered on demand.
    # Act
    choices = _status_choices()
    # Assert
    assert set(choices) == set(VALID_STATUSES)


def test_pending_is_abolished():
    # Arrange — none; the pin inspects the canonical enum.
    # Act — none; imported.
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_is_absent_from_valid_statuses():
    # Arrange — none; the pin inspects the canonical enum.
    # Act — none; imported.
    # Assert
    assert "pending" not in VALID_STATUSES


def test_deferred_is_a_valid_status():
    # Arrange — none; the pin inspects the canonical enum.
    # Act — none; imported.
    # Assert
    assert "deferred" in VALID_STATUSES


def test_nonvacuous_pin_covers_dropped_blocker_reload():
    # Arrange — none; the pin inspects the companion test file text.
    # Act — none; loaded at import.
    # Assert
    assert "test_save_tasks_drops_none_blocker_from_the_reloaded_row" in _FIXTURE


def test_nonvacuous_pin_covers_real_blocker_rejection():
    # Arrange — none; the pin inspects the companion test file text.
    # Act — none; loaded at import.
    # Assert
    assert "test_save_tasks_real_blocker_on_deferred_still_raises" in _FIXTURE


def test_nonvacuous_pin_covers_add_task_surface():
    # Arrange — none; the pin inspects the imported writer.
    # Act — none; imported.
    # Assert
    assert callable(add_task)
