#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`db rehearse` — the RFC-R4 equivalence gate as one repeatable command.

Frozen-copy semantics are the point: the rehearsal must be READ-ONLY on the
live store, judge the frozen copy (immune to mid-run writes), pass on a store
that round-trips (unknown keys, drained inboxes, threads included), and keep
its workdir as evidence exactly when it fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards._db_rehearse import rehearse
from scitex_cards._yaml import safe_dump


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    doc = {
        "tasks": [
            {"id": "a", "title": "a", "status": "done", "extra_unknown": [1]},
            {"id": "b", "title": "b", "status": "deferred"},
        ],
        "users": [{"id": "u_000000000001", "kind": "agent", "names": ["a"]}],
        "inboxes": {
            "u_000000000001": [
                {"id": "n_1", "event_type": "x", "ts": "2026", "seen": False}
            ],
            "drained": [],
        },
    }
    threads = {
        "dm:a::b": [
            {
                "id": "m_1",
                "from": "a",
                "to": "b",
                "body": "hi",
                "ts": "2026",
                "read": False,
            }
        ]
    }
    path = tmp_path / "tasks.yaml"
    path.write_text(safe_dump(doc), encoding="utf-8")
    (tmp_path / "threads.yaml").write_text(
        safe_dump({"threads": threads}), encoding="utf-8"
    )
    return path


def test_rehearse_judges_a_round_trippable_store_equal(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd"

    # Act
    report = rehearse(tasks_path=store, workdir=workdir)

    # Assert
    assert report["equal"] is True


def test_rehearse_reports_every_section_as_round_tripping(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd"

    # Act
    report = rehearse(tasks_path=store, workdir=workdir)

    # Assert
    assert report["sections"] == {
        "tasks": True,
        "users": True,
        "inboxes": True,
        "threads": True,
    }


def test_rehearse_counts_every_task_it_judged(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd"

    # Act
    report = rehearse(tasks_path=store, workdir=workdir)

    # Assert
    assert report["tasks"] == 2


def test_rehearse_counts_drained_inbox_recipients_too(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd"

    # Act
    report = rehearse(tasks_path=store, workdir=workdir)

    # Assert — the drained recipient is included.
    assert report["inbox_recipients"] == 2


def test_rehearse_never_touches_the_live_store(store, tmp_path):
    # Arrange
    before = store.read_bytes()

    # Act
    rehearse(tasks_path=store, workdir=tmp_path / "wd")

    # Assert — byte-identical afterwards.
    assert store.read_bytes() == before


def test_a_passing_rehearsal_reports_no_workdir_by_default(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd1"

    # Act
    cleaned = rehearse(tasks_path=store, workdir=workdir)

    # Assert
    assert cleaned["workdir"] is None


def test_a_passing_rehearsal_removes_its_workdir_by_default(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd1"

    # Act
    rehearse(tasks_path=store, workdir=workdir)

    # Assert
    assert not workdir.exists()


def test_a_kept_rehearsal_reports_the_workdir_it_kept(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd2"

    # Act
    kept = rehearse(tasks_path=store, workdir=workdir, keep=True)

    # Assert
    assert kept["workdir"] == str(workdir)


def test_a_kept_rehearsal_leaves_its_workdir_on_disk(store, tmp_path):
    # Arrange
    workdir = tmp_path / "wd2"

    # Act
    rehearse(tasks_path=store, workdir=workdir, keep=True)

    # Assert — the evidence survives for inspection.
    assert workdir.exists()
