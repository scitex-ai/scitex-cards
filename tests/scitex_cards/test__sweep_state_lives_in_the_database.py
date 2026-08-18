#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reminder escalation and nudge dedup live in the database, not in sidecars.

Both were YAML files under ``runtime/`` — a directory the constitution
reserves for REGENERABLE local state. Measured 2026-08-17, minutes before the
move, they were anything but stale::

    reminders.yaml   39425 B   written 53 seconds earlier
    nudges.yaml      19558 B   written 11 minutes earlier

WHY THE TESTS BELOW ARE DATABASE-BACKED, AND WHY THAT IS THE POINT

The user-registry defect on this same branch was invisible for an unknown
length of time because every test in ``tests/scitex_cards/_users/`` built a
``tmp_path / "tasks.yaml"``. Against a file the code was correct, so the suite
was green — honestly — about a backend nobody runs. The omission was not
under-tested; it was UNDETECTABLE.

So these assert against an AMBIENT store, which is the form the deployment
uses, and one of them (``test_no_sidecar_file_is_created``) exists purely to
fail if a file ever comes back.

FAIL-SOFT IS A CONTRACT, NOT AN ACCIDENT

Both loaders documented it — *"a bad sidecar must never break a sweep — the
worst case is one re-push"* — and the operator's standing rule agrees
(「カードが書けないということはなしで大丈夫です、warning で十分です」). A sweep
that raises because its bookkeeping is unavailable turns a cosmetic problem
into a delivery outage, so the unreadable-store case is pinned below.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import seed_db_from_doc

_STORE_ENV = "SCITEX_CARDS_DB"


@pytest.fixture()
def ambient_db_store(tmp_path: Path):
    """An empty board reachable AMBIENTLY — the shape the deployment has."""
    db = tmp_path / "cards.db"
    seed_db_from_doc({"tasks": []}, db)
    before = os.environ.get(_STORE_ENV)
    os.environ[_STORE_ENV] = str(db)
    try:
        yield db
    finally:
        if before is None:
            os.environ.pop(_STORE_ENV, None)
        else:
            os.environ[_STORE_ENV] = before


def test_reminder_state_round_trips_through_the_database(ambient_db_store) -> None:
    # Arrange
    from scitex_cards._reminders import load_reminder_state, save_reminder_state

    save_reminder_state({"owners": {}, "cards": {"c1": {"escalated": True}}})
    # Act
    loaded = load_reminder_state()
    # Assert
    assert loaded["cards"]["c1"] == {"escalated": True}


def test_reminder_load_always_returns_both_sections(ambient_db_store) -> None:
    """The contract the YAML loader had: index without guarding."""
    # Arrange
    from scitex_cards._reminders import load_reminder_state

    # Act
    loaded = load_reminder_state()
    # Assert
    assert set(loaded) == {"owners", "cards"}


def test_nudge_state_round_trips_through_the_database(ambient_db_store) -> None:
    # Arrange
    from scitex_cards._stale.active_nudge import (
        KIND_BLOCKED_CHECK,
        load_nudge_state,
        save_nudge_state,
    )

    record = {"fingerprint": "abc123", "delivered_at": "2026-08-17T19:00:00Z"}
    save_nudge_state({KIND_BLOCKED_CHECK: {"scitex-cards": record}})
    # Act
    loaded = load_nudge_state()
    # Assert
    assert loaded[KIND_BLOCKED_CHECK]["scitex-cards"] == record


def test_nudge_load_always_returns_every_kind(ambient_db_store) -> None:
    # Arrange
    from scitex_cards._stale.active_nudge import (
        KIND_BLOCKED_CHECK,
        KIND_PENDING_BACKLOG,
        KIND_STALE_ACTIVE,
        load_nudge_state,
    )

    # Act
    loaded = load_nudge_state()
    # Assert
    assert set(loaded) == {KIND_STALE_ACTIVE, KIND_PENDING_BACKLOG, KIND_BLOCKED_CHECK}


def test_a_dropped_entry_stops_being_returned(ambient_db_store) -> None:
    """Replace semantics, preserved from the whole-document write.

    The row is SOFT-deleted rather than removed — a tombstone can be synced to
    a peer, a missing row cannot — but the observable behaviour must match the
    file version that simply rewrote the document.
    """
    # Arrange
    from scitex_cards._reminders import load_reminder_state, save_reminder_state

    save_reminder_state({"owners": {}, "cards": {"c1": {"escalated": True}}})
    # Act
    save_reminder_state({"owners": {}, "cards": {}})
    # Assert
    assert load_reminder_state()["cards"] == {}


def test_a_resurrected_entry_comes_back(ambient_db_store) -> None:
    """Soft delete must not become a tombstone that blocks re-use of the key."""
    # Arrange
    from scitex_cards._reminders import load_reminder_state, save_reminder_state

    save_reminder_state({"owners": {}, "cards": {"c1": {"escalated": True}}})
    save_reminder_state({"owners": {}, "cards": {}})
    # Act
    save_reminder_state({"owners": {}, "cards": {"c1": {"escalated": False}}})
    # Assert
    assert load_reminder_state()["cards"]["c1"] == {"escalated": False}


def test_no_sidecar_file_is_created(ambient_db_store, tmp_path: Path) -> None:
    """If either YAML file reappears, the state left the database again."""
    # Arrange
    from scitex_cards._reminders import save_reminder_state

    # Act
    save_reminder_state({"owners": {}, "cards": {"c1": {"escalated": True}}})
    # Assert
    assert list(tmp_path.rglob("reminders.yaml")) == []


def test_an_unreadable_store_does_not_break_the_sweep(tmp_path: Path) -> None:
    """FAIL-SOFT: bookkeeping being unavailable must not raise.

    Pointed at a path that cannot be a store. The loader must warn and return
    empty sections — a sweep that dies here converts a cosmetic fault into a
    delivery outage, which is the exact trade both sidecar loaders documented.
    """
    # Arrange
    from scitex_cards._db_sweep_state import SCOPE_REMINDERS, load_sections

    before = os.environ.get(_STORE_ENV)
    os.environ[_STORE_ENV] = str(tmp_path / "nope" / "nowhere" / "cards.db")
    # Act
    try:
        loaded = load_sections(SCOPE_REMINDERS, ("owners", "cards"))
    finally:
        if before is None:
            os.environ.pop(_STORE_ENV, None)
        else:
            os.environ[_STORE_ENV] = before
    # Assert
    assert loaded == {"owners": {}, "cards": {}}


# EOF
