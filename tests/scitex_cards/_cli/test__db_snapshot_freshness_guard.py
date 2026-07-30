#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The snapshot rail refuses to commit an export that has gone stale.

On 2026-07-21 the hourly snapshot timer exported, committed "snapshot: 2168
tasks", and pushed off-site — every signal green — while the exported CONTENT
was stale: the 11:00 export showed a card as `deferred` that the DB had
already marked `done` at 10:47, and a card created at 10:47 was absent
entirely. The card COUNT never collapsed, so the pre-existing shrink-refusal
guard (``test__db_snapshot_shrink_guard.py``) never fired — shrink and
staleness are separate failure modes.

This is the complementary guard: it compares the export's own report against
a LIVE probe of the DB's typed columns, which the export never reads (the
export is built exclusively from the verbatim ``card_json`` payload — the S2
exactness contract). In a healthy DB both agree, because every write
populates them together; a disagreement means the export does not reflect
the DB's current state.
"""

from __future__ import annotations

import os

from click.testing import CliRunner

from scitex_cards._cli._db import db_group

_LAST_ACTIVITY = "2026-07-21T10:00:00Z"


def _seed_one_task(task_id: str = "t0") -> None:
    """Seed the canonical DB with a single task carrying a known last_activity.

    Goes through the normal write path (``seed_db_from_doc``), so the typed
    ``last_activity`` column and the ``card_json`` payload start IN SYNC —
    the healthy baseline every test here builds on.
    """
    from conftest import seed_db_from_doc

    doc = {
        "tasks": [
            {
                "id": task_id,
                "title": "T",
                "status": "deferred",
                "assignee": "x",
                "created_by": "x",
                "last_activity": _LAST_ACTIVITY,
            }
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])


def _desync_typed_last_activity(task_id: str, newer: str) -> None:
    """Update ONLY the typed ``last_activity`` column, bypassing ``card_json``.

    Simulates the 2026-07-21 shape directly: a write lands in the DB's live
    typed state without also refreshing the verbatim payload the export
    reads from, so the export reports OLDER content than the DB now holds.
    """
    from scitex_cards._db import connect

    conn = connect(os.environ["SCITEX_CARDS_DB"])
    try:
        conn.execute(
            "UPDATE tasks SET last_activity = ? WHERE id = ?", (newer, task_id)
        )
        conn.commit()
    finally:
        conn.close()


def test_a_fresh_export_passes_the_freshness_guard(tmp_path):
    """card_json and the typed columns agree — the guard does not fire."""
    # Arrange
    _seed_one_task()

    # Act
    result = CliRunner().invoke(
        db_group, ["snapshot", "--dir", str(tmp_path / "snapshots")]
    )

    # Assert
    assert result.exit_code == 0, result.output


def test_a_stale_export_is_refused_by_the_freshness_guard(tmp_path):
    """The 2026-07-21 shape: card_json lags the DB's live typed columns."""
    # Arrange — desync the DB's live state from what the export will read.
    _seed_one_task()
    _desync_typed_last_activity("t0", "2026-07-21T10:47:00Z")

    # Act
    result = CliRunner().invoke(
        db_group, ["snapshot", "--dir", str(tmp_path / "snapshots")]
    )

    # Assert
    assert result.exit_code != 0, "a stale export must not be snapshotted silently"
    assert "STALE EXPORT" in result.output
    assert _LAST_ACTIVITY in result.output  # the export's (stale) value
    assert "2026-07-21T10:47:00Z" in result.output  # the DB's live value


def test_a_stale_export_leaves_no_commit_behind(tmp_path):
    """A refused snapshot must not leave a commit an operator could trust."""
    # Arrange
    snaps = tmp_path / "snapshots"
    _seed_one_task()
    _desync_typed_last_activity("t0", "2026-07-21T10:47:00Z")

    # Act
    CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])

    # Assert — the refusal happens before the snapshot dir becomes a repo.
    assert not (snaps / ".git").exists()


# EOF
