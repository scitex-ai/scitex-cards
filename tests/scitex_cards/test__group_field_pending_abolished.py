#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`group`-shape fixtures must not seed the abolished `pending` status.

``status: pending`` was abolished 2026-07-10 (operator directive;
:data:`ABOLISHED_STATUSES` in :mod:`scitex_cards._task`): a card in
``pending`` carried NO decision, so every open card must now state its
disposition (``in_progress`` / ``blocked`` + blocker / ``deferred``).
The ``test__group_field.py`` suite pins `group` shapes (accept non-empty
string / absent; reject empty / non-string / integer) — all five of its
``_validate_tasks`` fixtures seeded ``status: pending`` while the
assertion under test never touched the status, so the suite passed while
teaching the abolished value as a live example. Same dead-fixture class
as the org (18 fixtures), mermaid (2), idle-guard (4) and
write-blocker-clear (1) repoints: repointed to ``deferred`` (the
successor default — :class:`Task` dataclass default and
``add_task(status="deferred")``), no src change.

Deliberately NOT repointed: the tolerated-read suites
(``test__tolerated_warning_names_its_side.py``,
``test__fleet_semantic_merge_rules.py``) and the stale-active exclusion
pins (``test_pending_never_stale_active``,
``test_only_in_progress_and_blocked``) feed legacy ``pending`` rows ON
PURPOSE — they pin that the read side tolerates (warns, never raises)
and that detectors exclude the abolished value. Repointing those would
delete the coverage, not fix a fixture.

PG-free by construction (in-memory dicts + file-text scans only): no
``postgres_dsn`` fixture, no store touch.
"""

from __future__ import annotations

from pathlib import Path

import scitex_cards
from scitex_cards._model import Task, _validate_tasks
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_GROUP_FIELD_TEST = (
    Path(scitex_cards.__file__).parent.parent.parent
    / "tests"
    / "scitex_cards"
    / "test__group_field.py"
)


def test_group_shapes_validate_on_successor_default():
    # Arrange — the repointed fixture shape: group under test + live status.
    tasks = [{"id": "t-a", "title": "x", "status": "deferred", "group": "paper-portfolio"}]
    # Act — must not raise (the status is live, the group is well-formed).
    _validate_tasks(tasks, source="<test>")
    # Assert — reaching here IS the assertion (no raise); anchor non-vacuity:
    assert tasks[0]["status"] == "deferred"


def test_legacy_pending_row_still_tolerated_never_rejected():
    # Arrange — a legacy `pending` row with a well-formed group.
    tasks = [{"id": "t-a", "title": "x", "status": "pending", "group": "paper-portfolio"}]
    # Act — the read side tolerates abolished values (warns), never raises,
    # so surviving legacy rows keep validating while new code writes `deferred`.
    _validate_tasks(tasks, source="<test>")
    # Assert — reaching here IS the assertion (no raise on legacy rows).
    assert tasks == [{"id": "t-a", "title": "x", "status": "pending", "group": "paper-portfolio"}]


def test_task_dataclass_default_is_the_successor():
    # Arrange / Act.
    t = Task(id="t-a", title="x")
    # Assert — a fresh row carries a decision out of the box, never `pending`.
    assert t.status == "deferred"


def test_enum_mirror_pending_abolished_deferred_valid():
    # Arrange / Act / Assert — the single source both sides mirror.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_no_live_pending_seed_in_group_field_tests():
    # Arrange.
    source = _GROUP_FIELD_TEST.read_text(encoding="utf-8")
    # Act / Assert — no `"status": "pending"` seed remains in the suite;
    # the sweep is anchored (non-vacuous) by the repointed `"status":
    # "deferred"` fixtures the suite now carries.
    assert '"status": "pending"' not in source
    assert source.count('"status": "deferred"') >= 5
