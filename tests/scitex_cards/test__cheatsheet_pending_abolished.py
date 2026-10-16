#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the fleet-cheatsheet pending-abolition sweep.

``docs/CHEATSHEET-fleet-cards.md`` is the doc a consuming agent copies
from, and it taught the abolished ``pending`` status as live AFTER
``pending`` was abolished 2026-07-10:

* the CLI read/write examples filtered/seeded ``--status pending``
  (``list-tasks --status pending``, the claim-off-the-queue recipe, the
  broadcast-epic recipe);
* the Python API examples read ``status="pending"`` and — worse —
  WROTE it via ``add_task(status="pending")``, which the source-strict
  verb gate now refuses with ``TaskValidationError`` (so a reader
  following the doc gets a loud failure, not a card);
* the §8 schema reference listed the ``status`` enum as
  ``pending|in_progress|blocked|done|deferred|failed|goal`` — seven
  entries but the WRONG seven: abolished ``pending`` in, ``cancelled``
  out — and the ``Statuses (VALID_STATUSES)`` prose listed the same
  wrong seven.

Same dead-instruction class as the README sweep
(``test__readme_enums_match_python``): a reader following the doc
writes a status the closed-enum verbs refuse, or misses rows cards
really carry. The examples now use ``deferred`` (the successor
default), both enum listings mirror ``VALID_STATUSES`` exactly, and
the prose names the abolition so the next reader knows WHY the
examples avoid ``pending``.

These pins read the tracked cheatsheet and import the single source
(``_task.VALID_STATUSES`` / ``ABOLISHED_STATUSES``): no store, no
PostgreSQL — they run in containers where the store-backed suite
refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import scitex_cards
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_CHEAT = (
    Path(scitex_cards.__file__).resolve().parent.parent.parent
    / "docs"
    / "CHEATSHEET-fleet-cards.md"
)

_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _cheat() -> str:
    assert _CHEAT.is_file(), f"cheatsheet moved? not found at {_CHEAT}"
    return _CHEAT.read_text(encoding="utf-8")


def _statuses_paragraph(text: str) -> str:
    """The ``Statuses (VALID_STATUSES)`` sentence, enum lines only.

    Fails LOUD when the paragraph is absent so a reshape moves the pin
    rather than silently vacating it. Stops at ``consistently.`` so the
    abolition note below (which names `pending` on purpose) never
    pollutes the token set.
    """
    lines = text.splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("Statuses (`VALID_STATUSES`)")
    )
    taken = []
    for line in lines[start:]:
        taken.append(line)
        if "consistently." in line:
            return "\n".join(taken)
    raise AssertionError("Statuses paragraph lost its 'consistently.' terminator")


def _schema_status_line(text: str) -> str:
    """The ``status: a|b|c`` line of the §8 YAML schema reference.

    Fails LOUD when the line is absent so a reshape moves the pin
    rather than silently vacating it.
    """
    for line in text.splitlines():
        if line.strip().startswith("status:"):
            return line.split("#")[0]
    raise AssertionError("§8 schema reference lost its 'status:' line")


def test_cheatsheet_seeds_no_python_pending_status():
    # Arrange
    text = _cheat()
    # Act — none; the pin inspects doc text loaded on demand.
    # Assert
    assert 'status="pending"' not in text


def test_cheatsheet_seeds_no_cli_pending_filter():
    # Arrange
    text = _cheat()
    # Act — none; the pin inspects doc text loaded on demand.
    # Assert
    assert "--status pending" not in text


def test_cheatsheet_schema_line_mirrors_valid_statuses():
    # Arrange
    line = _schema_status_line(_cheat())
    # Act
    tokens = {t.strip() for t in line.split("status:")[1].split("|")}
    # Assert
    assert tokens == set(VALID_STATUSES)


def test_cheatsheet_schema_mirror_covers_the_live_enum():
    # Arrange
    line = _schema_status_line(_cheat())
    # Act
    tokens = {t.strip() for t in line.split("status:")[1].split("|")}
    # Assert
    assert len(tokens) >= 7


def test_cheatsheet_statuses_prose_mirrors_valid_statuses():
    # Arrange
    paragraph = _statuses_paragraph(_cheat())
    # Act
    tokens = set(_BACKTICK_RE.findall(paragraph)) - {"VALID_STATUSES"}
    # Assert
    assert tokens == set(VALID_STATUSES)


def test_cheatsheet_names_the_abolition():
    # Arrange
    text = _cheat()
    # Act — none; the pin inspects doc text loaded on demand.
    # Assert
    assert "abolished 2026-07-10" in text


def test_cheatsheet_cli_examples_use_the_successor_default():
    # Arrange
    text = _cheat()
    # Act — none; the pin inspects doc text loaded on demand.
    # Assert
    assert "--status deferred" in text


def test_cheatsheet_python_examples_use_the_successor_default():
    # Arrange
    text = _cheat()
    # Act — none; the pin inspects doc text loaded on demand.
    # Assert
    assert 'status="deferred"' in text


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
