#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` asserts SQLite is the ONLY write target (2026-07-21 deletion).

The dual-write mirror was DELETED as a feature, not defaulted off (operator
ruling: 「データベースしか書く場所なんてありえない。デュアルライトっていうオ
プションがあること自体がおかしい」). The root cause it answers: `cards.db`
carried a stale `schema_meta` row (`yaml_path` pointing at an old
`~/.scitex/todo/tasks.yaml`), and an agent whose environment still carried
the dual-write flag had every MCP/CLI write silently routed to that dead YAML
instead of the canonical database — every call returned SUCCESS and `health`
stayed green while an entire session of card writes never reached the board.

The old `dual_write_mirror` check reported whether an env-gated mirror had
stayed in sync. That mirror is gone, so this file tests what replaced it:
`check_single_write_target`, which asks whether anything still LOOKS like a
second write target — a legacy toggle env var lingering in the process
environment (harmless today, since nothing reads it, but the exact footgun
that caused the incident), or the toggle having been reintroduced as code.
"""

from __future__ import annotations

from scitex_cards._health import health
from scitex_cards._health_write_target import (
    _LEGACY_DUAL_WRITE_ENV_VARS,
    check_single_write_target,
)


def _check(report: dict, name: str) -> dict:
    return {c["name"]: c for c in report["checks"]}[name]


def test_ok_when_no_legacy_env_var_is_set(env):
    # Arrange
    for name in _LEGACY_DUAL_WRITE_ENV_VARS:
        env.delete(name)

    # Act
    res = check_single_write_target()

    # Assert
    assert res["ok"] is True


def test_flags_a_lingering_SCITEX_TODO_DUAL_WRITE(env):
    """The pre-rename toggle name — still a footgun even though nothing reads it."""
    # Arrange
    env.set("SCITEX_TODO_DUAL_WRITE", "1")
    env.delete("SCITEX_CARDS_DUAL_WRITE")

    # Act
    res = check_single_write_target()

    # Assert
    assert res["ok"] is False
    assert "SCITEX_TODO_DUAL_WRITE" in res["detail"]
    assert "unset" in (res["hint"] or "")


def test_flags_a_lingering_SCITEX_CARDS_DUAL_WRITE(env):
    """The incident's actual env var — root cause 2026-07-21."""
    # Arrange
    env.set("SCITEX_CARDS_DUAL_WRITE", "1")
    env.delete("SCITEX_TODO_DUAL_WRITE")

    # Act
    res = check_single_write_target()

    # Assert
    assert res["ok"] is False
    assert "SCITEX_CARDS_DUAL_WRITE" in res["detail"]


def test_names_both_legacy_vars_when_both_are_set(env):
    # Arrange
    env.set("SCITEX_TODO_DUAL_WRITE", "1")
    env.set("SCITEX_CARDS_DUAL_WRITE", "1")

    # Act
    res = check_single_write_target()

    # Assert
    assert "SCITEX_TODO_DUAL_WRITE" in res["detail"]
    assert "SCITEX_CARDS_DUAL_WRITE" in res["detail"]


def test_the_deleted_toggle_symbols_are_actually_gone():
    """The regression this check exists to catch, pinned directly.

    If any of these names reappear on `_dual_write`, the toggle was
    reintroduced — `check_single_write_target` must fail on it, but that only
    matters if the symbols are ACTUALLY gone today. Verified by import, not by
    a version string, for the same reason the rest of this package insists on
    it: a version string is metadata and metadata lies.
    """
    # Arrange
    import scitex_cards._dual_write as dual_write_mod

    # Act / Assert
    for name in (
        "enabled",
        "mirror_after_save",
        "ENV_DUAL_WRITE",
        "check_mirror_healthy",
    ):
        assert not hasattr(dual_write_mod, name), (
            f"scitex_cards._dual_write.{name} must not exist — the dual-write "
            f"toggle was deleted as a feature, not defaulted off"
        )


def test_the_ownership_guard_itself_survives_the_deletion():
    """The guard is NOT part of the deleted toggle — it must still be here."""
    # Arrange
    import scitex_cards._dual_write as dual_write_mod

    # Act / Assert
    assert callable(dual_write_mod._db_mirrors_this_store)
    assert callable(dual_write_mod._same_file)


def _healthy_store(tmp_path):
    """A real, minimal-but-valid task store — hermetic, no ambient env."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    return store


def test_health_runs_single_write_target_and_not_the_deleted_check(tmp_path, env):
    """The aggregator wires in the new check under its new name."""
    # Arrange
    for name in _LEGACY_DUAL_WRITE_ENV_VARS:
        env.delete(name)

    # Act
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")

    # Assert
    names = {c["name"] for c in report["checks"]}
    assert "single_write_target" in names
    assert "dual_write_mirror" not in names


def test_health_fails_overall_when_a_legacy_var_leaks_into_the_env(tmp_path, env):
    # Arrange — every OTHER check hermetically healthy, only the legacy var is set.
    env.set("SCITEX_CARDS_DUAL_WRITE", "1")

    # Act
    report = health(store=_healthy_store(tmp_path), agent_id="agent-x")

    # Assert
    assert _check(report, "single_write_target")["ok"] is False
    assert report["ok"] is False


# EOF
