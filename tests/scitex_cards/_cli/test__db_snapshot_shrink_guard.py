#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backup rail refuses to record a wipe as if it were a snapshot.

On 2026-07-19 the live DB was destroyed (2,138 cards -> 53) and the rail did
exactly as told: committed ``snapshot: 53 tasks`` as HEAD, one commit after
``snapshot: 2138 tasks``, silently. The rail was WORKING — that is the point. A
backup that faithfully records a catastrophe with no alarm stops being a safety
net and becomes a propagation mechanism.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from scitex_cards._cli._db import db_group


def _seed(n: int) -> None:
    """Seed the canonical DB with ``n`` done cards.

    The snapshot guard reads the canonical SQLite DB (``db snapshot`` exports it
    and counts the rows), so seeding is a full DB rebuild from an in-memory doc —
    the same doc the old YAML fixture held. ``seed_db_from_doc`` replaces every
    row, so re-seeding with a smaller ``n`` genuinely collapses the store.
    """
    from conftest import seed_db_from_doc

    doc = {
        "tasks": [
            {
                "id": f"t{i}",
                "title": f"T{i}",
                "status": "done",
                "assignee": "x",
                "created_by": "x",
            }
            for i in range(n)
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])


@pytest.fixture()
def rail(tmp_path):
    """A snapshot repo with one healthy 100-card snapshot already committed."""
    snaps = tmp_path / "snapshots"
    _seed(100)
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])
    assert result.exit_code == 0, result.output
    return snaps


def test_a_collapsed_card_count_is_refused(rail):
    """The 2026-07-19 shape: the store is wiped, the rail must NOT record it."""
    # Arrange
    snaps = rail
    _seed(3)

    # Act
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])

    # Assert
    assert result.exit_code != 0, "a wipe must not be snapshotted silently"


def test_the_collapse_refusal_reports_the_before_and_after_counts(rail):
    """Split under STX-TQ007, and it is the half that makes the refusal usable.

    A non-zero exit tells the operator the rail stopped; only the counts tell
    them WHY and how bad it is. Merged, this could never be the reported
    failure — it ran only once the exit-code claim had already passed.
    """
    # Arrange
    snaps = rail
    _seed(3)

    # Act
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])

    # Assert
    assert "collapsed from 100 to 3" in result.output


def test_allow_shrink_permits_a_genuine_bulk_delete(rail):
    """A real bulk delete is legitimate — the guard must be overridable."""
    # Arrange
    snaps = rail
    _seed(3)

    # Act
    result = CliRunner().invoke(
        db_group, ["snapshot", "--dir", str(snaps), "--allow-shrink"]
    )

    # Assert
    assert result.exit_code == 0, result.output


def test_ordinary_growth_is_never_blocked(rail):
    """The guard must not police normal churn, only catastrophe."""
    # Arrange
    snaps = rail
    _seed(140)

    # Act
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])

    # Assert
    assert result.exit_code == 0, result.output


# EOF
