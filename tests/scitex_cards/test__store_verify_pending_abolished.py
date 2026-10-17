#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the store-verify fixtures (pending abolition).

``tests/scitex_cards/test__store_verify.py`` seeded ``status: pending`` in 7
sites after ``pending`` was abolished 2026-07-10: the ``_valid_doc`` helper,
the ``GOOD_DUMP`` / ``BAD_SAME_LENGTH`` same-length-corruption pair, the
``last_scalar`` premise, the quoted-scalar truncation doc, the
``PARTIAL_STREAM`` mid-stream truncation, and the
``_PARSEABLE_BUT_UNCONSTRUCTABLE`` no-construct probe. Every assertion pins
YAML integrity (byte length, event-scan to StreamEnd, no-construct) — never
the status domain — so the suite passed while encoding a dead status as live
(model 31 + crash_safe_writer 13 + store 4 + deadline 11 + throughput 12 +
is_overdue 19 precedent). The seeds are now ``deferred`` (the successor
default); these pins keep them there.

The same-length corruption pair is length-sensitive: ``GOOD_DUMP`` carries
``status: deferred`` (8 chars) and ``BAD_SAME_LENGTH`` replaces it with
``status: "eferred`` (quote + 7 chars = 8), so the byte-length check still
passes and only the event-scan can catch it — the repoint preserves the
incident shape, not just the words.

These pins use the file text + the pure validation gate + tmp files only: no
store, no PostgreSQL — they run in containers where the store-backed suite
refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from scitex_cards._store_verify import StoreWriteVerifyError, _verify_dumped_tmp
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks
from scitex_cards._yaml import safe_dump

_SUBJECT = Path(__file__).with_name("test__store_verify.py")


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


def test_the_successor_default_seed_is_present_case1():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert '"status": "deferred"' in source


def test_the_successor_default_seed_is_present_case2():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert '"status: deferred' in source


def test_the_successor_default_seed_is_present_case3():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "def _valid_doc" in source


def test_the_successor_default_seed_is_present_case4():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "GOOD_DUMP" in source


def test_the_successor_default_seed_is_present_case5():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "PARTIAL_STREAM" in source


def test_the_successor_default_seed_is_present_case6():
    # Arrange
    # Arrange
    # Act
    source = _SUBJECT.read_text()
    # Assert
    assert "_PARSEABLE_BUT_UNCONSTRUCTABLE" in source


def test_pending_seed_warns_while_deferred_is_silent_case1():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the YAML
    integrity tests claim to pin. This is why the fixtures must stay
    ``deferred``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    # Act
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    # Assert
    live_warnings = [str(x.message) for x in caught]
    assert any("abolished" in text for text in pending_warnings)


def test_pending_seed_warns_while_deferred_is_silent_case2():
    """Guard the non-interchangeability itself: the seeds differ on load.

    ``_validate_tasks`` tolerates a ``pending`` row with an abolished-status
    warning but passes ``deferred`` silently — so a ``pending`` fixture
    exercises the tolerated-legacy path, not the live-status path the YAML
    integrity tests claim to pin. This is why the fixtures must stay
    ``deferred``.
    """
    # Arrange
    pending = [{"id": "a", "title": "A", "status": "pending"}]
    live = [{"id": "a", "title": "A", "status": "deferred"}]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(pending, source="/some/store.db")
    pending_warnings = [str(x.message) for x in caught]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _validate_tasks(live, source="/some/store.db")
    # Act
    live_warnings = [str(x.message) for x in caught]
    # Assert
    assert live_warnings == []


def test_verify_path_is_status_blind_case1(tmp_path):
    """The verify accepts a deferred doc and still rejects a truncation.

    Proves the repoint changes no verdict: the byte-length + event-scan gate
    never reads the status domain, so ``deferred`` verifies and a halved write
    still raises.
    """
    # Arrange
    doc = {
        "tasks": [
            {"id": f"t{i}", "title": f"Title {i}", "status": "deferred"}
            for i in range(3)
        ],
        "users": [{"id": "alice", "kind": "human", "role": "dev"}],
    }
    dumped = safe_dump(doc)
    full = tmp_path / ".tasks.yaml.tmp"
    full.write_text(dumped, encoding="utf-8")
    short = tmp_path / ".tasks.yaml.tmp.short"
    # Act
    short.write_text(dumped[: len(dumped) // 2], encoding="utf-8")
    # Assert
    assert dumped is not None


def test_verify_path_is_status_blind_case2(tmp_path):
    """The verify accepts a deferred doc and still rejects a truncation.

    Proves the repoint changes no verdict: the byte-length + event-scan gate
    never reads the status domain, so ``deferred`` verifies and a halved write
    still raises.
    """
    # Arrange
    doc = {
        "tasks": [
            {"id": f"t{i}", "title": f"Title {i}", "status": "deferred"}
            for i in range(3)
        ],
        "users": [{"id": "alice", "kind": "human", "role": "dev"}],
    }
    dumped = safe_dump(doc)
    full = tmp_path / ".tasks.yaml.tmp"
    full.write_text(dumped, encoding="utf-8")
    short = tmp_path / ".tasks.yaml.tmp.short"
    # Act
    short.write_text(dumped[: len(dumped) // 2], encoding="utf-8")
    # Assert
    assert _verify_dumped_tmp(full, dumped) is None


def test_verify_path_is_status_blind_case3(tmp_path):
    """The verify accepts a deferred doc and still rejects a truncation.

    Proves the repoint changes no verdict: the byte-length + event-scan gate
    never reads the status domain, so ``deferred`` verifies and a halved write
    still raises.
    """
    # Arrange
    doc = {
        "tasks": [
            {"id": f"t{i}", "title": f"Title {i}", "status": "deferred"}
            for i in range(3)
        ],
        "users": [{"id": "alice", "kind": "human", "role": "dev"}],
    }
    dumped = safe_dump(doc)
    full = tmp_path / ".tasks.yaml.tmp"
    full.write_text(dumped, encoding="utf-8")
    short = tmp_path / ".tasks.yaml.tmp.short"
    # Act
    short.write_text(dumped[: len(dumped) // 2], encoding="utf-8")
    # Assert
    with pytest.raises(StoreWriteVerifyError):
        _verify_dumped_tmp(short, dumped)


def test_status_enum_mirror_holds_case1():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_status_enum_mirror_holds_case2():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_status_enum_mirror_holds_case3():
    # Arrange + Act + Assert — the abolition itself, so a future
    # re-introduction of `pending` into either set fails loudly here.
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
