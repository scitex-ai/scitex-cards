#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the crash_safe_writer fixtures (pending abolition).

``tests/scitex_cards/test__crash_safe_writer.py`` seeded the abolished
``pending`` status in 13 fixtures (good_store x2, duplicate-id validation
x6, SIGKILL huge payload, happy-path round-trip, extra-sections seed,
rich round-trip, append) after ``pending`` was abolished 2026-07-10.
Every assertion pins crash-safety — the pre-write validation gate, the
single-commit txn atomicity under SIGKILL, non-``tasks`` section
preservation, and the load -> save -> load fixed point — never the
status. ``_validate_tasks`` only WARNS on a ``pending`` row (operator
ruling 2026-07-10: bad values warn, only structural faults raise), so the
suite passed while encoding a dead status as live. Same dead-fixture
class as the model (31) + throughput (12) + deadline (11) + is_overdue
(19) + reassign_all + db-mirror + org (18) + mermaid (2) + idle-guard
(4) + write-blocker (1) + group-field (5) repoints. The fixtures now
seed ``deferred`` (the successor default: open, in VALID_STATUSES); no
src change (the write path is status-blind for crash-safety: one
validation gate + one ``write_doc_to_db`` commit, unchanged).

These pins use the file text + the pure validation gate only: no store,
no PostgreSQL — they run in containers where the store-backed suite
refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import WRITE_SOURCE, _validate_tasks

_SUBJECT = Path(__file__).with_name("test__crash_safe_writer.py")


def test_no_fixture_seeds_the_abolished_status():
    # Arrange
    source = _SUBJECT.read_text()
    # Act
    seeds = [
        line
        for line in source.splitlines()
        if "status" in line and "pending" in line and "abolished" not in line
    ]
    # Assert — every remaining "pending" is the abolition-note comment.
    assert seeds == []


def test_the_successor_default_seed_is_present():
    # Arrange
    source = _SUBJECT.read_text()
    # Act + Assert — non-vacuity: the fixtures still seed statuses, and the
    # file under test is the crash-safe writer suite, not an empty stub.
    assert '"status": "deferred"' in source
    assert "def test_sigkill_mid_write_leaves_store_unchanged" in source
    assert "def test_save_round_trips_tasks_exactly" in source


def test_pending_seed_warns_while_deferred_is_silent():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the
    crash-safety tests claim to pin. This is why the fixtures must stay
    ``deferred``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    live_warnings = [str(x.message) for x in caught]
    # Assert
    assert any("abolished" in text for text in pending_warnings)
    assert live_warnings == []


def test_write_side_tolerates_legacy_pending_without_raising():
    """The repoint changes no verdict: the write gate warns, never raises.

    A legacy ``pending`` row passes ``_validate_tasks`` on the write side
    (``WRITE_SOURCE``) with a warning — only structural faults (duplicate
    id, missing title) raise. So the duplicate-id tests raise on the ids
    either way, and the happy-path/atomicity tests never depended on the
    status. The fixtures are ``deferred`` to pin the live path, not to
    make anything pass.
    """
    # Arrange
    legacy = [{"id": "a", "title": "A", "status": "pending"}]
    structural = [
        {"id": "dup", "title": "first", "status": "deferred"},
        {"id": "dup", "title": "second", "status": "deferred"},
    ]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(legacy, source=WRITE_SOURCE)
    legacy_warnings = [str(x.message) for x in caught]
    # Assert — legacy warns (write-side label), structural still raises.
    assert any("abolished" in text for text in legacy_warnings)
    assert any("write-side" in text for text in legacy_warnings)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _validate_tasks(structural, source=WRITE_SOURCE)
    except Exception as exc:  # noqa: BLE001 — the gate's fault type is the pin
        assert "dup" in str(exc)
    else:
        raise AssertionError("duplicate ids must still raise on the write side")


def test_status_enum_mirror_holds():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
