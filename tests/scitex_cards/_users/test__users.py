#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the standalone user registry (`scitex_cards._users`).

Real round-trips against a `tmp_path` YAML store — no mocks (Req STX-NM /
PA-306). Covers stable-id generation + persistence round-trip, fail-loud
validation, cross-registry name uniqueness, alias resolution (including an
OLD alias surviving a rename), and the hard guarantee that user writes do
NOT disturb the `tasks:` payload living in the same file.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _model, _store, _users


# --------------------------------------------------------------------------- #
# register_user: stable id, persistence, round-trip                          #
# --------------------------------------------------------------------------- #
def _full_user_kwargs():
    """Every field a registered user can carry — a FRESH dict per call, so no
    test can mutate another's fixture data."""
    return dict(
        kind="agent",
        names=["scitex-dev", "proj-scitex-dev"],
        host_at_name="ywata-note-win@scitex-dev",
        notify={"telegram": True},
    )


@pytest.fixture()
def round_tripped_user(tmp_path):
    """A fully-specified user plus the copy read FRESH off disk.

    The reload tests below each pin ONE persisted field; splitting them
    means a dropped field names itself instead of hiding behind whichever
    assertion happened to come first.
    """
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(store=store, **_full_user_kwargs())
    return created, _users.get_user(created.id, store=store)


def test_register_user_generates_stable_prefixed_id(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    user = _users.register_user(kind="agent", names=["proj-scitex-dev"], store=store)
    # Assert
    assert user.id.startswith("u_")


def test_register_user_id_is_twelve_hex_chars(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    user = _users.register_user(kind="agent", names=["proj-scitex-dev"], store=store)
    # Assert
    token = user.id[len("u_") :]
    assert len(token) == 12 and all(c in "0123456789abcdef" for c in token)


def test_register_user_ids_are_unique(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    a = _users.register_user(kind="agent", names=["a"], store=store)
    b = _users.register_user(kind="human", names=["b"], store=store)
    # Assert
    assert a.id != b.id


def test_register_user_round_trips_on_reload(round_tripped_user):
    """A fresh read from disk finds the user at all — no in-memory carry."""
    # Arrange
    _, reloaded = round_tripped_user
    # Act
    found = reloaded is not None
    # Assert
    assert found


def test_reloaded_user_keeps_its_stable_id(round_tripped_user):
    # Arrange
    created, reloaded = round_tripped_user
    # Act
    reloaded_id = reloaded.id
    # Assert
    assert reloaded_id == created.id


def test_reloaded_user_keeps_its_kind(round_tripped_user):
    # Arrange
    _, reloaded = round_tripped_user
    # Act
    kind = reloaded.kind
    # Assert
    assert kind == "agent"


def test_reloaded_user_keeps_every_name(round_tripped_user):
    # Arrange
    _, reloaded = round_tripped_user
    # Act
    names = reloaded.names
    # Assert
    assert names == ["scitex-dev", "proj-scitex-dev"]


def test_reloaded_user_keeps_its_host_at_name(round_tripped_user):
    # Arrange
    _, reloaded = round_tripped_user
    # Act
    host_at_name = reloaded.host_at_name
    # Assert
    assert host_at_name == "ywata-note-win@scitex-dev"


def test_reloaded_user_keeps_its_notify_config(round_tripped_user):
    # Arrange
    _, reloaded = round_tripped_user
    # Act
    notify = reloaded.notify
    # Assert
    assert notify == {"telegram": True}


def test_reloaded_user_carries_a_created_at_stamp(round_tripped_user):
    # Arrange
    _, reloaded = round_tripped_user
    # Act
    created_at = reloaded.created_at
    # Assert
    assert created_at


def test_register_user_string_names_coerced_to_list(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    user = _users.register_user(kind="human", names="alice", store=store)
    # Assert
    assert user.names == ["alice"]


# --------------------------------------------------------------------------- #
# tasks in the SAME file are untouched by a user write                       #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store_with_two_tasks(tmp_path):
    """A store carrying two tasks, plus the task payload as it stood BEFORE
    any user write touched the same file."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(
        store, id="t1", title="Task one", status="pending", assignee="agent:test-suite"
    )
    _store.add_task(
        store,
        id="t2",
        title="Task two",
        status="in_progress",
        assignee="agent:test-suite",
    )
    return store, _model.load_tasks(store)


def test_user_write_does_not_disturb_tasks(store_with_two_tasks):
    # Arrange
    store, before = store_with_two_tasks
    # Act
    _users.register_user(kind="agent", names=["owner-1"], store=store)
    # Assert
    assert _model.load_tasks(store) == before


def test_user_write_leaves_the_users_section_readable(store_with_two_tasks):
    """The users section lands present + readable ALONGSIDE the tasks."""
    # Arrange
    store, _ = store_with_two_tasks
    # Act
    _users.register_user(kind="agent", names=["owner-1"], store=store)
    # Assert
    assert [u.names[0] for u in _users.load_users(store)] == ["owner-1"]


def test_task_write_after_user_write_keeps_users(tmp_path):
    """The user survives a subsequent task write (round-trip preserves it)."""
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    user = _users.register_user(kind="agent", names=["owner-1"], store=store)
    # Act
    _store.add_task(
        store, id="t1", title="Task one", status="pending", assignee="agent:test-suite"
    )
    # Assert
    assert _users.get_user(user.id, store=store) is not None


def test_task_write_after_user_write_records_the_task(tmp_path):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _users.register_user(kind="agent", names=["owner-1"], store=store)
    # Act
    _store.add_task(
        store, id="t1", title="Task one", status="pending", assignee="agent:test-suite"
    )
    # Assert
    assert [t["id"] for t in _model.load_tasks(store)] == ["t1"]


# --------------------------------------------------------------------------- #
# validate_user: fail-loud                                                   #
# --------------------------------------------------------------------------- #
def test_validate_user_rejects_bad_kind(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="robot", names=["x"], store=store)


def test_validate_user_rejects_empty_names(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=[], store=store)


def test_validate_user_rejects_empty_string_name(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=[""], store=store)


def test_validate_user_rejects_malformed_host_at_name(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    malformed = "host@"  # empty name half
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="agent", names=["x"], host_at_name=malformed, store=store
        )


def test_validate_user_accepts_bare_host_at_name(tmp_path):
    """A bare name (no '@') is a valid host-unknown id per
    canonical_agent_id."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    user = _users.register_user(
        kind="agent", names=["x"], host_at_name="just-a-name", store=store
    )
    # Assert
    assert user.host_at_name == "just-a-name"


# --------------------------------------------------------------------------- #
# name uniqueness across the registry                                        #
# --------------------------------------------------------------------------- #
def test_duplicate_name_on_register_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["dup"], store=store)
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="human", names=["dup"], store=store)


def test_duplicate_name_on_add_alias_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    a = _users.register_user(kind="agent", names=["a-name"], store=store)
    _users.register_user(kind="human", names=["b-name"], store=store)
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.add_alias(a.id, "b-name", store=store)


def test_add_alias_is_idempotent(tmp_path):
    """Re-adding a name the user already has must be a no-op, not a raise."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    a = _users.register_user(kind="agent", names=["a-name"], store=store)
    # Act
    _users.add_alias(a.id, "a-name", store=store)
    # Assert
    assert _users.get_user(a.id, store=store) is not None


def test_add_alias_no_op_does_not_duplicate_the_name(tmp_path):
    """...and it must not leave ["a-name", "a-name"] behind."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    a = _users.register_user(kind="agent", names=["a-name"], store=store)
    # Act
    _users.add_alias(a.id, "a-name", store=store)
    # Assert
    assert _users.get_user(a.id, store=store).names == ["a-name"]


# --------------------------------------------------------------------------- #
# resolve_user: alias, host_at_name, unknown, post-rename old alias          #
# --------------------------------------------------------------------------- #
def test_resolve_user_by_alias(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    # Act
    found = _users.resolve_user("scitex-dev", store=store)
    # Assert
    assert found is not None and found.id == user.id


def test_resolve_user_by_host_at_name(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["scitex-dev"],
        host_at_name="ywata-note-win@scitex-dev",
        store=store,
    )
    # Act
    found = _users.resolve_user("ywata-note-win@scitex-dev", store=store)
    # Assert
    assert found is not None and found.id == user.id


def test_resolve_user_unknown_returns_none(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    # Act
    found = _users.resolve_user("nobody", store=store)
    # Assert
    assert found is None


@pytest.fixture()
def renamed_user(tmp_path):
    """A user originally named proj-scitex-dev, then renamed: the rename adds
    the new name as an alias while KEEPING the old one, so historic card
    references still resolve. Yields (store, user)."""
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["proj-scitex-dev"], store=store)
    _users.add_alias(user.id, "scitex-dev", store=store)
    return store, user


def test_old_alias_still_resolves_after_rename(renamed_user):
    # Arrange
    store, user = renamed_user
    # Act
    old = _users.resolve_user("proj-scitex-dev", store=store)
    # Assert
    assert old is not None and old.id == user.id


def test_new_name_resolves_to_the_same_stable_id(renamed_user):
    """Both names are the SAME user — a rename must not fork the record."""
    # Arrange
    store, user = renamed_user
    # Act
    new = _users.resolve_user("scitex-dev", store=store)
    # Assert
    assert new is not None and new.id == user.id


# --------------------------------------------------------------------------- #
# delivery endpoint: turn_url / a2a_port field + user_turn_url helper         #
# --------------------------------------------------------------------------- #
def test_register_user_persists_explicit_turn_url(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["proj-x"],
        turn_url="https://explicit/v1/turn/x",
        store=store,
    )
    # Act
    reloaded = _users.get_user(created.id, store=store)
    # Assert
    assert reloaded is not None and reloaded.turn_url == "https://explicit/v1/turn/x"


def test_register_user_persists_a2a_port(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["proj-y"],
        host_at_name="ywata-note-win@proj-y",
        a2a_port=19007,
        store=store,
    )
    # Act
    reloaded = _users.get_user(created.id, store=store)
    # Assert
    assert reloaded is not None and reloaded.a2a_port == 19007


def test_user_turn_url_prefers_explicit_over_port(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["proj-z"],
        host_at_name="some-host@proj-z",
        turn_url="https://explicit/turn",
        a2a_port=19007,
        store=store,
    )
    # Act
    url = _users.user_turn_url(user)
    # Assert
    assert url == "https://explicit/turn"


def test_user_turn_url_derives_from_port_and_host_at_name(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["proj-w"],
        host_at_name="my-host@proj-w",
        a2a_port=19007,
        store=store,
    )
    # Act
    url = _users.user_turn_url(user)
    # Assert
    assert url == "http://my-host:19007/v1/turn"


def test_user_turn_url_bare_host_at_name_uses_loopback(tmp_path):
    """A bare (host-unknown) id → loopback host, mirroring the sac-registry
    a2a_port derivation convention."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent", names=["proj-bare"], a2a_port=18080, store=store
    )
    # Act
    url = _users.user_turn_url(user)
    # Assert
    assert url == "http://127.0.0.1:18080/v1/turn"


def test_user_turn_url_none_when_no_endpoint(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["proj-none"], store=store)
    # Act
    url = _users.user_turn_url(user)
    # Assert
    assert url is None


def test_validate_user_rejects_non_positive_a2a_port(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=["x"], a2a_port=0, store=store)


def test_validate_user_rejects_non_int_a2a_port(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=["x"], a2a_port="19007", store=store)


def test_validate_user_rejects_empty_string_turn_url(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="agent", names=["x"], turn_url="", store=store)


#: Backward-compat: a user with NO endpoint round-trips with both fields None,
#: and `to_dict` omits them entirely so the YAML stays compact. The three
#: tests below split that one guarantee.
@pytest.fixture()
def endpointless_user(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(kind="human", names=["alice"], store=store)
    return created, _users.get_user(created.id, store=store)


def test_endpoint_absent_user_round_trips_without_endpoint_keys(endpointless_user):
    # Arrange
    _, reloaded = endpointless_user
    # Act
    endpoint = (reloaded.turn_url, reloaded.a2a_port)
    # Assert
    assert endpoint == (None, None)


def test_endpoint_absent_user_omits_turn_url_from_the_yaml(endpointless_user):
    # Arrange
    created, _ = endpointless_user
    # Act
    keys = created.to_dict()
    # Assert
    assert "turn_url" not in keys


def test_endpoint_absent_user_omits_a2a_port_from_the_yaml(endpointless_user):
    # Arrange
    created, _ = endpointless_user
    # Act
    keys = created.to_dict()
    # Assert
    assert "a2a_port" not in keys


# --------------------------------------------------------------------------- #
# set_notify                                                                  #
# --------------------------------------------------------------------------- #
def test_set_notify_replaces_dict(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(kind="agent", names=["x"], notify={"a": 1}, store=store)
    # Act
    _users.set_notify(user.id, {"b": 2}, store=store)
    # Assert — REPLACES, does not merge {"a": 1} in.
    reloaded = _users.get_user(user.id, store=store)
    assert reloaded is not None and reloaded.notify == {"b": 2}


def test_set_notify_unknown_user_raises(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.set_notify("u_deadbeef0000", {"x": 1}, store=store)


# --------------------------------------------------------------------------- #
# list_users / get_user                                                      #
# --------------------------------------------------------------------------- #
def test_list_users_returns_all(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["a"], store=store)
    _users.register_user(kind="human", names=["b"], store=store)
    # Act
    users = _users.list_users(store=store)
    # Assert
    assert sorted(u.names[0] for u in users) == ["a", "b"]


def test_list_users_empty_when_no_section(tmp_path):
    """Tasks but no users: an absent section reads as [], never a KeyError."""
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(store, id="t1", title="Task one", assignee="agent:test-suite")
    # Act
    users = _users.list_users(store=store)
    # Assert
    assert users == []


def test_get_user_unknown_returns_none(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["a"], store=store)
    # Act
    found = _users.get_user("u_nope00000000", store=store)
    # Assert
    assert found is None


# --------------------------------------------------------------------------- #
# is_alive: alive / stale / unknown boundaries (pure classifier)              #
# --------------------------------------------------------------------------- #
import datetime as _dt  # noqa: E402

from scitex_cards._users import User as _User  # noqa: E402
from scitex_cards._users import is_alive as _is_alive  # noqa: E402


def _now():
    return _dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _seen_seconds_ago(seconds: int) -> str:
    """A `last_seen` stamp in the Z form, `seconds` before `_now()`."""
    return (_now() - _dt.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _agent_seen(last_seen=None) -> "_User":
    """A minimal agent User carrying `last_seen` (None = never seen)."""
    return _User(id="u_x", kind="agent", names=["a"], last_seen=last_seen)


def test_is_alive_unknown_when_no_last_seen():
    # Arrange
    user = _agent_seen()
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out == {"status": "unknown", "last_seen": None, "age_seconds": None}


def test_is_alive_unknown_when_user_none():
    # Arrange
    user = None
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["status"] == "unknown"


def test_is_alive_reports_no_age_when_user_none():
    # Arrange
    user = None
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["age_seconds"] is None


def test_is_alive_alive_within_ttl():
    """5 minutes ago, default ttl 600s → alive."""
    # Arrange
    user = _agent_seen(_seen_seconds_ago(300))
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["status"] == "alive"


def test_is_alive_reports_the_age_in_seconds():
    # Arrange
    user = _agent_seen(_seen_seconds_ago(300))
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["age_seconds"] == 300


def test_is_alive_echoes_the_last_seen_stamp():
    # Arrange
    seen = _seen_seconds_ago(300)
    user = _agent_seen(seen)
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["last_seen"] == seen


def test_is_alive_at_ttl_boundary_is_alive():
    """Exactly ttl old → alive (inclusive boundary)."""
    # Arrange
    user = _agent_seen(_seen_seconds_ago(600))
    # Act
    out = _is_alive(user, now=_now(), ttl_seconds=600)
    # Assert
    assert out["status"] == "alive"


def test_is_alive_stale_past_ttl():
    """601s old against a ttl of 600 → stale, by one second."""
    # Arrange
    user = _agent_seen(_seen_seconds_ago(601))
    # Act
    out = _is_alive(user, now=_now(), ttl_seconds=600)
    # Assert
    assert out["status"] == "stale"


def test_a_stale_user_still_reports_its_age():
    # Arrange
    user = _agent_seen(_seen_seconds_ago(601))
    # Act
    out = _is_alive(user, now=_now(), ttl_seconds=600)
    # Assert
    assert out["age_seconds"] == 601


def test_is_alive_unknown_on_malformed_last_seen():
    # Arrange
    user = _agent_seen("not-a-timestamp")
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["status"] == "unknown"


def test_is_alive_accepts_plain_offset_stamp():
    """The +00:00 form (not the Z form) still parses."""
    # Arrange
    user = _agent_seen((_now() - _dt.timedelta(seconds=10)).isoformat())
    # Act
    out = _is_alive(user, now=_now())
    # Assert
    assert out["status"] == "alive"


# --------------------------------------------------------------------------- #
# last_seen persistence + touch_user heartbeat                               #
# --------------------------------------------------------------------------- #
def test_a_new_user_has_never_been_seen(tmp_path):
    """Registration is not a heartbeat — `last_seen` starts empty."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    created = _users.register_user(kind="agent", names=["hb"], store=store)
    # Assert
    assert created.last_seen is None


def test_touch_user_stamps_last_seen(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["hb"], store=store)
    # Act
    touched = _users.touch_user("hb", store=store)
    # Assert
    assert touched is not None and touched.last_seen


def test_last_seen_round_trips_on_reload(tmp_path):
    """...and the stamp is on DISK, not only on the returned object."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(kind="agent", names=["hb"], store=store)
    # Act
    touched = _users.touch_user("hb", store=store)
    # Assert
    reloaded = _users.get_user(created.id, store=store)
    assert reloaded is not None and reloaded.last_seen == touched.last_seen


def test_touch_user_by_id_finds_the_record(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    user = _users.register_user(
        kind="agent",
        names=["hb2"],
        host_at_name="ywata-note-win@hb2",
        store=store,
    )
    # Act
    touched = _users.touch_user(user.id, store=store)
    # Assert
    assert touched is not None


def test_touch_user_by_host_at_name_finds_the_record(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(
        kind="agent",
        names=["hb2"],
        host_at_name="ywata-note-win@hb2",
        store=store,
    )
    # Act
    touched = _users.touch_user("ywata-note-win@hb2", store=store)
    # Assert
    assert touched is not None


def test_touch_user_unknown_returns_none(tmp_path):
    """An unregistered actor has no record to stamp → None (never raises)."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["hb3"], store=store)
    # Act
    touched = _users.touch_user("nobody", store=store)
    # Assert
    assert touched is None


@pytest.fixture()
def touched_user(tmp_path):
    """A fully-specified user, heartbeat-stamped once. The heartbeat write
    rewrites the whole users section, so each field below gets its own test:
    a dropped key must name itself."""
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent",
        names=["hb4", "old-hb4"],
        host_at_name="host@hb4",
        notify={"telegram": True},
        store=store,
    )
    _users.touch_user("hb4", store=store)
    return _users.get_user(created.id, store=store)


def test_touch_user_preserves_other_keys(touched_user):
    # Arrange
    reloaded = touched_user
    # Act
    found = reloaded is not None
    # Assert
    assert found


def test_touch_user_preserves_every_name(touched_user):
    # Arrange
    expected = ["hb4", "old-hb4"]
    # Act
    names = touched_user.names
    # Assert
    assert names == expected


def test_touch_user_preserves_the_host_at_name(touched_user):
    # Arrange
    expected = "host@hb4"
    # Act
    host_at_name = touched_user.host_at_name
    # Assert
    assert host_at_name == expected


def test_touch_user_preserves_the_notify_config(touched_user):
    # Arrange
    expected = {"telegram": True}
    # Act
    notify = touched_user.notify
    # Assert
    assert notify == expected


def test_touch_user_writes_the_last_seen_stamp(touched_user):
    # Arrange
    reloaded = touched_user
    # Act
    last_seen = reloaded.last_seen
    # Assert
    assert last_seen


def test_store_action_stamps_last_seen_on_actor(tmp_path, env):
    """A store action (comment) by a REGISTERED actor stamps its last_seen."""
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _users.register_user(kind="agent", names=["actor-1"], store=store)
    env.set("SCITEX_TODO_AGENT_ID", "actor-1")
    _store.add_task(store, id="t1", title="T", assignee="actor-1", created_by="actor-1")
    # Act — created_by heartbeat already stamped; the comment re-stamps.
    _store.comment_task(store, "t1", "hello", by="actor-1")
    # Assert
    seen = _users.resolve_user("actor-1", store=store)
    assert seen is not None and seen.last_seen


# EOF
