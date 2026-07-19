#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the four extension ports + the default adapters.

No mocks (STX-NM / PA-306). Real tmp paths for the file-sync adapter,
real in-process callbacks for the pubsub adapter, real dict payloads
for the ACL + liveness adapters. AAA pattern; descriptive names; one
core assertion per test where practical.
"""

from __future__ import annotations

import pytest

from scitex_cards._adapters import (
    InProcessPubSub,
    LocalFileSync,
    NullLiveness,
    OpenACL,
)
from scitex_cards._ports import (
    AgentDirectoryPort,
    AgentIdentityError,
    AgentInfo,
    EmptyAgentDirectory,
    IdentityACLPort,
    LivenessPort,
    NotificationPort,
    TaskSyncPort,
    canonical_agent_id,
    dedup_agents,
    parse_agent_id,
    resolve_agent_directory,
)

# ---------------------------------------------------------------------------
# Protocol conformance — every default adapter satisfies its Protocol via
# isinstance() (the Protocols are @runtime_checkable).
# ---------------------------------------------------------------------------


def test_local_file_sync_satisfies_task_sync_port():
    # Arrange
    sync = LocalFileSync("/tmp/_unused.yaml")
    # Act
    is_port = isinstance(sync, TaskSyncPort)
    # Assert
    assert is_port


def test_in_process_pubsub_satisfies_notification_port():
    # Arrange
    bus = InProcessPubSub()
    # Act
    is_port = isinstance(bus, NotificationPort)
    # Assert
    assert is_port


def test_null_liveness_satisfies_liveness_port():
    # Arrange
    liveness = NullLiveness()
    # Act
    is_port = isinstance(liveness, LivenessPort)
    # Assert
    assert is_port


def test_open_acl_satisfies_identity_acl_port():
    # Arrange
    acl = OpenACL()
    # Act
    is_port = isinstance(acl, IdentityACLPort)
    # Assert
    assert is_port


# ---------------------------------------------------------------------------
# LocalFileSync — load / save / reload_if_changed behaviour.
# ---------------------------------------------------------------------------


def _write_tasks_yaml(tmp_path, body: str):
    p = tmp_path / "tasks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_local_file_sync_load_returns_validated_tasks(tmp_path):
    # Arrange
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    # Act
    tasks = sync.load()
    # Assert
    assert tasks[0]["id"] == "a"


#: WHY the two `save_round_trips` tests below are split but share this
#: rationale: contract CHANGE (fix/fast-store-write) — the underlying
#: save_tasks now uses a fast safe dump. Comments are intentionally dropped;
#: the task DATA must round-trip through LocalFileSync unchanged. Those two
#: claims point in opposite directions (what MUST survive the write vs what is
#: ACCEPTED to be lost), and a save that silently wrote nothing would preserve
#: the comment while discarding the mutation — passing the claim we care least
#: about.
@pytest.fixture()
def local_file_sync_round_trip(tmp_path):
    """Load, mutate, save and reload a store that carries a YAML comment."""
    p = _write_tasks_yaml(
        tmp_path,
        "# comment intentionally NOT preserved\n"
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    tasks = sync.load()
    tasks[0]["status"] = "done"
    sync.save(tasks)
    return {"reloaded": sync.load(), "on_disk": p.read_text()}


def test_local_file_sync_save_round_trips_task_data(local_file_sync_round_trip):
    # Arrange
    scenario = local_file_sync_round_trip
    # Act
    reloaded = scenario["reloaded"]
    # Assert — the mutation round-trips through save + load.
    assert reloaded[0]["status"] == "done"


def test_local_file_sync_save_drops_yaml_comments(local_file_sync_round_trip):
    # Arrange
    scenario = local_file_sync_round_trip
    # Act
    on_disk = scenario["on_disk"]
    # Assert — the comment is gone (the accepted half of the contract change).
    assert "# comment intentionally NOT preserved" not in on_disk


def test_local_file_sync_reload_detects_external_mutation(tmp_path):
    # Arrange
    import os
    import time

    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    sync.load()  # baseline mtime captured
    # Act — external write that bumps mtime.
    time.sleep(0.01)  # ensure mtime tick
    os.utime(p, None)
    # Assert
    assert sync.reload_if_changed() is True


def test_local_file_sync_reload_returns_false_when_quiet(tmp_path):
    # Arrange
    p = _write_tasks_yaml(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: pending}\n",
    )
    sync = LocalFileSync(p)
    sync.load()
    # Act
    changed = sync.reload_if_changed()
    # Assert — no external write means reload-detection is a no-op.
    assert changed is False


# ---------------------------------------------------------------------------
# InProcessPubSub — literal + suffix-* glob + idempotent subscribe + handler-
# exception isolation.
# ---------------------------------------------------------------------------


def test_pubsub_literal_channel_match_delivers_to_subscriber():
    # Arrange
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("scitex-todo:task:x/y", seen.append)
    # Act
    bus.publish("scitex-todo:task:x/y", {"task_id": "x/y"})
    # Assert
    assert seen == [{"task_id": "x/y"}]


def test_pubsub_suffix_glob_matches_every_task_in_project():
    # Arrange
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("scitex-todo:task:demo/*", seen.append)
    # Act
    bus.publish("scitex-todo:task:demo/foo", {"task_id": "demo/foo"})
    bus.publish("scitex-todo:task:demo/bar", {"task_id": "demo/bar"})
    bus.publish("scitex-todo:task:other/baz", {"task_id": "other/baz"})
    # Assert
    assert [p["task_id"] for p in seen] == ["demo/foo", "demo/bar"]


def test_pubsub_subscribe_is_idempotent():
    # Arrange
    bus = InProcessPubSub()
    seen: list[dict] = []
    bus.subscribe("a", seen.append)
    bus.subscribe("a", seen.append)  # duplicate
    # Act
    bus.publish("a", {})
    # Assert
    assert len(seen) == 1


def test_pubsub_bad_handler_emits_user_warning():
    """Bad handler emits warnings.warn on publish (TQ007 split — was bundled
    with the other-handlers-still-fire assertion in a single test)."""
    # Arrange
    bus = InProcessPubSub()

    def boom(_):
        raise RuntimeError("intentional")

    bus.subscribe("c", boom)
    # Act
    warn_ctx = pytest.warns(UserWarning)
    # Assert — pytest.warns is the assertion: warning fires on publish.
    with warn_ctx:
        bus.publish("c", {"ok": True})


def test_pubsub_handler_exception_does_not_break_other_handlers():
    # Arrange
    bus = InProcessPubSub()

    def boom(_):
        raise RuntimeError("intentional")

    seen: list[dict] = []
    bus.subscribe("c", boom)
    bus.subscribe("c", seen.append)
    # Act — the bad handler raises (warns via warnings.warn); suppress the
    # warning here since the warning is asserted by the companion test
    # above. Other handlers must still see the event.
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        bus.publish("c", {"ok": True})
    # Assert
    assert seen == [{"ok": True}]


# ---------------------------------------------------------------------------
# NullLiveness + OpenACL — defaults are trivial but must satisfy contracts.
# ---------------------------------------------------------------------------


def test_null_liveness_returns_empty_list():
    # Arrange
    liveness = NullLiveness()
    # Act
    agents = liveness.list_agents()
    # Assert
    assert agents == []


def test_open_acl_allows_read():
    # Arrange
    acl = OpenACL()
    # Act
    allowed = acl.can_read("anyone", {"id": "x"})
    # Assert
    assert allowed is True


def test_open_acl_allows_write_any_field():
    # Arrange
    acl = OpenACL()
    # Act
    allowed = acl.can_write("anyone", {"id": "x"}, "status")
    # Assert
    assert allowed is True


# ===========================================================================
# Agent career (ADR-0009) — host@name identity helpers, AgentDirectoryPort,
# EmptyAgentDirectory default, resolver injection seam, dedup-by-join-key.
#
# No mocks / no monkeypatch: the resolver is exercised through its
# `entry_points=` injection seam with real fake entry-point objects
# (`.name` + `.load()`), exactly like tests/scitex_cards/test__hooks.py.
# ===========================================================================


class _FakeProviderEP:
    """Entry-point-shaped fake whose ``load()`` returns a zero-arg factory.

    The factory returns ``provider`` — an :class:`AgentDirectoryPort`-shaped
    object. Mirrors ``test__hooks.py::_FakeEP`` (the production injection
    seam, not a mock).
    """

    def __init__(self, name: str, provider: object):
        self.name = name
        self._provider = provider

    def load(self):
        def _factory():
            return self._provider

        return _factory


class _BoomProviderEP:
    """Entry-point-shaped fake whose factory raises — exercises the
    skip-broken-provider path of :func:`resolve_agent_directory`."""

    name = "boom"

    def load(self):
        def _factory():
            raise RuntimeError("provider exploded")

        return _factory


class _OneAgentDirectory:
    """Minimal real :class:`AgentDirectoryPort` returning one agent."""

    def __init__(self, agent: AgentInfo):
        self._agent = agent

    def list_agents(self):
        return [self._agent]

    def get_agent(self, host_at_name: str):
        return self._agent if host_at_name == self._agent.host_at_name else None


# --- canonical_agent_id / parse_agent_id ----------------------------------


def test_canonical_agent_id_joins_host_and_name():
    # Arrange
    name, host = "worker-1", "ywata-note-win"
    # Act
    joined = canonical_agent_id(name, host)
    # Assert
    assert joined == "ywata-note-win@worker-1"


def test_canonical_agent_id_round_trips_through_parse():
    # Arrange
    joined = canonical_agent_id("worker-1", "ywata-note-win")
    # Act
    parsed = parse_agent_id(joined)
    # Assert
    assert parsed == ("ywata-note-win", "worker-1")


def test_canonical_agent_id_already_qualified_returns_as_is():
    # Arrange
    already = "ywata-note-win@worker-1"
    # Act
    out = canonical_agent_id(already)
    # Assert
    assert out == "ywata-note-win@worker-1"


def test_canonical_agent_id_already_qualified_ignores_host_arg():
    # Arrange — embedded host wins; explicit host arg is ignored.
    already = "hostA@worker-1"
    # Act
    out = canonical_agent_id(already, host="hostB")
    # Assert
    assert out == "hostA@worker-1"


def test_canonical_agent_id_bare_name_when_no_host():
    # Arrange
    name = "worker-1"
    # Act
    out = canonical_agent_id(name)
    # Assert
    assert out == "worker-1"


def test_canonical_agent_id_fails_loud_on_empty_name():
    # Arrange
    bad = "   "
    # Act
    # Assert
    with pytest.raises(AgentIdentityError):
        canonical_agent_id(bad)


def test_canonical_agent_id_fails_loud_on_malformed_qualified():
    # Arrange — empty host before the '@'.
    bad = "@worker-1"
    # Act
    # Assert
    with pytest.raises(AgentIdentityError):
        canonical_agent_id(bad)


def test_parse_agent_id_bare_yields_empty_host():
    # Arrange
    bare = "worker-1"
    # Act
    parsed = parse_agent_id(bare)
    # Assert
    assert parsed == ("", "worker-1")


def test_parse_agent_id_fails_loud_on_empty():
    # Arrange
    bad = ""
    # Act
    # Assert
    with pytest.raises(AgentIdentityError):
        parse_agent_id(bad)


def test_parse_agent_id_fails_loud_on_double_at():
    # Arrange
    bad = "host@a@b"
    # Act
    # Assert
    with pytest.raises(AgentIdentityError):
        parse_agent_id(bad)


# --- AgentInfo / EmptyAgentDirectory / Protocol conformance ----------------


def test_agent_info_extra_defaults_to_empty_dict():
    # Arrange
    info = AgentInfo(host_at_name="h@x", name="x", host="h")
    # Act
    extra = info.extra
    # Assert
    assert extra == {}


def test_empty_agent_directory_satisfies_agent_directory_port():
    # Arrange
    directory = EmptyAgentDirectory()
    # Act
    is_port = isinstance(directory, AgentDirectoryPort)
    # Assert
    assert is_port


def test_empty_agent_directory_list_is_empty():
    # Arrange
    directory = EmptyAgentDirectory()
    # Act
    agents = directory.list_agents()
    # Assert
    assert agents == []


def test_empty_agent_directory_get_returns_none():
    # Arrange
    directory = EmptyAgentDirectory()
    # Act
    found = directory.get_agent("anyhost@anyname")
    # Assert
    assert found is None


# --- resolve_agent_directory — injection seam, no provider, multi, broken --


def test_resolve_agent_directory_returns_empty_when_no_provider():
    # Arrange — explicit empty entry-point list (no provider installed).
    eps: list = []
    # Act
    directory = resolve_agent_directory(entry_points=eps)
    # Assert
    assert isinstance(directory, EmptyAgentDirectory)


def test_resolve_agent_directory_picks_injected_provider():
    # Arrange
    agent = AgentInfo("h@x", "x", "h", "running")
    ep = _FakeProviderEP("sac", _OneAgentDirectory(agent))
    # Act
    directory = resolve_agent_directory(entry_points=[ep])
    # Assert
    assert directory.list_agents()[0].host_at_name == "h@x"


def test_resolve_agent_directory_multi_provider_first_by_name_wins():
    # Arrange — two providers; the lexicographically-first name ("aaa") wins.
    first = _FakeProviderEP("aaa", _OneAgentDirectory(AgentInfo("h@a", "a", "h")))
    second = _FakeProviderEP("zzz", _OneAgentDirectory(AgentInfo("h@z", "z", "h")))
    # Act
    directory = resolve_agent_directory(entry_points=[second, first])
    # Assert
    assert directory.list_agents()[0].host_at_name == "h@a"


def test_resolve_agent_directory_skips_broken_provider():
    # Arrange — a provider whose factory raises must not break the board;
    # the next provider is used instead.
    good = _FakeProviderEP("good", _OneAgentDirectory(AgentInfo("h@g", "g", "h")))
    # Act — "boom" sorts before "good" but raises; resolver skips it.
    directory = resolve_agent_directory(entry_points=[_BoomProviderEP(), good])
    # Assert
    assert directory.list_agents()[0].host_at_name == "h@g"


def test_resolve_agent_directory_empty_when_only_provider_is_broken():
    # Arrange — the sole provider raises; resolver falls back to Empty.
    # Act
    directory = resolve_agent_directory(entry_points=[_BoomProviderEP()])
    # Assert
    assert isinstance(directory, EmptyAgentDirectory)


# --- dedup_agents — first-wins by host_at_name -----------------------------


def test_dedup_agents_drops_later_duplicate_by_join_key():
    # Arrange — same host@name twice; first wins, later dropped.
    a = AgentInfo("h@x", "x", "h", "running")
    b = AgentInfo("h@x", "x", "h", "stopped")
    # Act
    deduped = dedup_agents([a, b])
    # Assert
    assert [r.status for r in deduped] == ["running"]


def test_dedup_agents_preserves_distinct_agents_in_order():
    # Arrange
    a = AgentInfo("h@a", "a", "h")
    b = AgentInfo("h@b", "b", "h")
    # Act
    deduped = dedup_agents([a, b])
    # Assert
    assert [r.host_at_name for r in deduped] == ["h@a", "h@b"]
