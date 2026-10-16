#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ``blocked`` CLI verb (pending abolition).

``_cli/_runnable.py`` described the ``blocked`` candidate set as
``{pending, in_progress, blocked}`` after ``pending`` was abolished
2026-07-10. The code was already correct (``blocked_tasks`` admits
``RUNNABLE_STATUSES`` + ``blocked``, i.e. ``{deferred, in_progress,
blocked}``) — only the help prose lied, inviting the next copy-paste to
reintroduce a dead filter. The prose now names ``deferred``; these pins
keep code, docs, and the status enum aligned.

Follow-up (same class, one layer deeper): ``blocked_tasks``' own
docstring + inline comment still listed ``deferred`` among the EXCLUDED
statuses after the candidate set was repointed to include it — the
comment even contradicted itself (``deferred`` as both candidate and
"not blocked"). The code (``st not in RUNNABLE_STATUSES and st !=
"blocked"``) was already correct; both prose sites now name the true
excluded set (done / failed / goal / cancelled). Pins below cover the
docstring, the finished-status behavior, and the legacy-pending
exclusion.

These pins use in-memory dicts plus Click help rendering only: no
store, no PostgreSQL — they run in containers where the store-backed
suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import click

from scitex_cards._cli._runnable import blocked_cmd
from scitex_cards._runnable import blocked_tasks
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def _tasks() -> list[dict]:
    return [
        {"id": "up", "title": "Up", "status": "in_progress"},
        {
            "id": "dn",
            "title": "Down",
            "status": "deferred",
            "depends_on": ["up"],
        },
        {
            "id": "b",
            "title": "B",
            "status": "blocked",
            "blocker": "waiting on X",
        },
        {
            "id": "leg",
            "title": "Legacy",
            "status": "pending",
            "blocker": "waiting on X",
            "depends_on": ["up"],
        },
    ]


def test_blocked_candidate_set_is_deferred_in_progress_blocked():
    out = blocked_tasks(_tasks())
    by_id = {bt.id: bt for bt in out.tasks}
    assert by_id["dn"].reason == "depends-on"
    assert list(by_id["dn"].chain) == ["up"]
    assert by_id["b"].reason == "explicit-blocker"
    # The healthy upstream is runnable, so it is not a blocked task.
    assert "up" not in by_id


def test_legacy_pending_row_is_excluded_from_blocked():
    out = blocked_tasks(_tasks())
    assert "leg" not in {bt.id for bt in out.tasks}
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES


def test_blocked_help_names_deferred_not_pending():
    text = blocked_cmd.get_help(click.Context(blocked_cmd))
    assert "{deferred, in_progress, blocked}" in text
    assert "{pending, in_progress, blocked}" not in text


def test_blocked_docstring_names_true_excluded_set():
    doc = blocked_tasks.__doc__
    assert doc is not None
    # Deferred is a candidate, so it must not appear among the excluded.
    assert "deferred`` IS included" in doc
    assert "{done, deferred, failed, goal} are" not in doc
    for finished in ("done", "failed", "goal", "cancelled"):
        assert finished in doc


def test_finished_statuses_excluded_from_blocked():
    rows = [
        {"id": "d", "title": "D", "status": "done", "blocker": "waiting on X"},
        {"id": "f", "title": "F", "status": "failed", "blocker": "waiting on X"},
        {
            "id": "g",
            "title": "G",
            "status": "goal",
            "depends_on": ["missing-upstream"],
        },
        {
            "id": "c",
            "title": "C",
            "status": "cancelled",
            "blocker": "waiting on X",
        },
        # Non-vacuity anchor: a deferred card with an unresolved dep IS
        # reported, so an empty result cannot pass the exclusions above.
        {
            "id": "dn",
            "title": "Down",
            "status": "deferred",
            "depends_on": ["up"],
        },
        {"id": "up", "title": "Up", "status": "in_progress"},
    ]
    out = blocked_tasks(rows)
    ids = {bt.id for bt in out.tasks}
    assert ids == {"dn"}
    for finished in ("d", "f", "g", "c"):
        assert finished not in ids
