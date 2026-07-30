#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assignee-liveness feature — Slice 2: surface assignee liveness at assign time.

Real round-trips against a ``tmp_path`` YAML store — no mocks (Req STX-NM /
PA-306). Covers that ``add_task`` / ``reassign_task`` / assignee-setting
``update_task`` attach an ``assignee_liveness`` block classifying the
assignee as alive / stale / unknown, and that no block is attached when there
is no assignee to classify (structurally impossible for add_task, which
requires an owner, but exercised for update_task).
"""

from __future__ import annotations

import datetime as _dt
import os

from scitex_cards import _store, _users


def _iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _register_seen(store, name, *, seconds_ago):
    """Register ``name`` and stamp its ``last_seen`` ``seconds_ago`` in the past."""
    user = _users.register_user(kind="agent", names=[name], store=store)
    seen = _iso(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago))
    # Stamp directly via the registry write path (touch_user always stamps
    # "now"; here we need a controlled age, so set it through set-like write).
    users = _users.load_users(store=store)
    for u in users:
        if u.id == user.id:
            u.last_seen = seen
    # Persist the controlled stamp using the same round-trip writer.
    from pathlib import Path

    from scitex_cards._model import _store_lock
    from scitex_cards._users import _store as _users_store

    path = Path(store)
    with _store_lock(path):
        rows = _users_store._load_users_section(path)
        for row in rows:
            if row.get("id") == user.id:
                row["last_seen"] = seen
        _users_store._save_users_unlocked(rows, path)
    return user


def _add_owned_by(store, name):
    """Insert the one card under test, owned by ``name``."""
    return _store.add_task(
        store,
        id="t1",
        title="T",
        assignee=name,
        created_by="agent:test-suite",
    )


#: The four ALIVE assertions below were one test. They share this arrangement —
#: an assignee registered as seen one minute ago, well inside the liveness TTL —
#: and each pins a different field of the block ``add_task`` attaches for it:
#: that the block exists at all, its classification, its ``last_seen`` stamp,
#: and the integer ``age_seconds`` a caller can render.
def _alive_liveness(tmp_path):
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _register_seen(store, "fresh-agent", seconds_ago=60)  # 1 min ago
    return _add_owned_by(store, "fresh-agent")


#: Likewise for the three UNKNOWN assertions: an assignee that was never
#: registered resolves to no user at all, so there is nothing to classify.
def _unregistered_liveness(tmp_path):
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    return _add_owned_by(store, "ghost-agent")


# --------------------------------------------------------------------------- #
# add_task attaches assignee_liveness                                         #
# --------------------------------------------------------------------------- #
def test_add_task_attaches_an_assignee_liveness_block(tmp_path):
    # Arrange
    expected_key = "assignee_liveness"
    # Act
    result = _alive_liveness(tmp_path)
    # Assert — assigning work surfaces who you just assigned it to.
    assert expected_key in result


def test_add_task_alive_assignee(tmp_path):
    # Arrange
    expected_status = "alive"
    # Act
    result = _alive_liveness(tmp_path)
    # Assert
    assert result["assignee_liveness"]["status"] == expected_status


def test_add_task_alive_assignee_carries_last_seen(tmp_path):
    # Arrange
    result = _alive_liveness(tmp_path)
    # Act
    live = result["assignee_liveness"]
    # Assert — the stamp the classification was derived from.
    assert live["last_seen"] is not None


def test_add_task_alive_assignee_carries_integer_age(tmp_path):
    # Arrange
    result = _alive_liveness(tmp_path)
    # Act
    live = result["assignee_liveness"]
    # Assert — a renderable age, not a raw timedelta.
    assert isinstance(live["age_seconds"], int)


def test_add_task_stale_assignee(tmp_path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _register_seen(store, "old-agent", seconds_ago=3600)  # 1 hour ago
    # Act
    result = _add_owned_by(store, "old-agent")
    # Assert
    assert result["assignee_liveness"]["status"] == "stale"


def test_add_task_unknown_assignee_unregistered(tmp_path):
    # Arrange
    expected_status = "unknown"
    # Act
    result = _unregistered_liveness(tmp_path)
    # Assert — never registered → resolves to no user → unknown.
    assert result["assignee_liveness"]["status"] == expected_status


def test_add_task_unregistered_assignee_has_no_last_seen(tmp_path):
    # Arrange
    result = _unregistered_liveness(tmp_path)
    # Act
    live = result["assignee_liveness"]
    # Assert
    assert live["last_seen"] is None


def test_add_task_unregistered_assignee_has_no_age(tmp_path):
    # Arrange
    result = _unregistered_liveness(tmp_path)
    # Act
    live = result["assignee_liveness"]
    # Assert — no stamp to measure from, so no age either.
    assert live["age_seconds"] is None


def test_add_task_unknown_assignee_never_seen(tmp_path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _users.register_user(kind="agent", names=["idle-agent"], store=store)
    # Act
    result = _add_owned_by(store, "idle-agent")
    # Assert — registered, but never touched the store → no last_seen.
    assert result["assignee_liveness"]["status"] == "unknown"


# --------------------------------------------------------------------------- #
# reassign_task / update_task attach assignee_liveness for the new owner      #
# --------------------------------------------------------------------------- #
def _reassigned_to_fresh_owner(tmp_path):
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _register_seen(store, "new-owner", seconds_ago=30)
    _add_owned_by(store, "orig")
    return _store.reassign_task(store, "t1", "new-owner", by="agent:test-suite")


def test_reassign_task_reports_the_owner_changed(tmp_path):
    # Arrange
    expected_changed = True
    # Act
    out = _reassigned_to_fresh_owner(tmp_path)
    # Assert
    assert out["changed"] is expected_changed


def test_reassign_task_reports_new_owner_liveness(tmp_path):
    # Arrange
    expected_status = "alive"
    # Act
    out = _reassigned_to_fresh_owner(tmp_path)
    # Assert — you learn whether the new owner is actually running.
    assert out["assignee_liveness"]["status"] == expected_status


def test_update_task_setting_assignee_reports_liveness(tmp_path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _register_seen(store, "u-owner", seconds_ago=30)
    _add_owned_by(store, "orig")
    # Act
    merged = _store.update_task(store, "t1", assignee="u-owner")
    # Assert
    assert merged["assignee_liveness"]["status"] == "alive"


def test_update_task_without_assignee_change_has_no_liveness(tmp_path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _add_owned_by(store, "orig")
    # Act
    merged = _store.update_task(store, "t1", note="just a note")
    # Assert — no assignee to classify, so no block is attached.
    assert "assignee_liveness" not in merged


# EOF
