#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` asserts SQLite is the ONLY write target (2026-07-21 deletion).

The dual-write mirror was DELETED as a feature, not defaulted off (operator
ruling: 「データベースしか書く場所なんてありえない。デュアルライトっていうオ
プションがあること自体がおかしい」). The root cause it answers: `cards.db`
carried a stale `schema_meta` row (`yaml_path` pointing at a `tasks.yaml`
under the pre-rename store directory), and an agent whose environment still carried
the dual-write flag had every MCP/CLI write silently routed to that dead YAML
instead of the canonical database — every call returned SUCCESS and `health`
stayed green while an entire session of card writes never reached the board.

The old `dual_write_mirror` check reported whether an env-gated mirror had
stayed in sync. That mirror is gone, so this file tests what replaced it:
`check_single_write_target`, which asks whether anything still LOOKS like a
second write target — a legacy toggle env var lingering in the process
environment (harmless today, since nothing reads it, but the exact footgun
that caused the incident), or the toggle having been reintroduced as code.

ONE ASSERTION PER TEST (STX-TQ007). These tests were merged three-to-a-function
until 2026-08-15. That grouping is exactly wrong for THIS check: a failure of
"does the detail name the offending variable" and a failure of "does the check
fail at all" are different defects with different fixes, and the merged form
reported only whichever came first. The shared setup lives in the helpers
below rather than in a bigger test.
"""

from __future__ import annotations

from scitex_cards._health import health
from scitex_cards._health_write_target import (
    _LEGACY_DUAL_WRITE_ENV_VARS,
    check_single_write_target,
)


def _check(report: dict, name: str) -> dict:
    return {c["name"]: c for c in report["checks"]}[name]


def _with_only(env, *set_vars: str) -> dict:
    """Run the check with exactly ``set_vars`` present and the rest cleared."""
    for name in _LEGACY_DUAL_WRITE_ENV_VARS:
        env.delete(name)
    for name in set_vars:
        env.set(name, "1")
    return check_single_write_target()


def _healthy_store(tmp_path):
    """A real, minimal-but-valid task store — hermetic, no ambient env."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    return store


def _health_with_only(env, tmp_path, *set_vars: str) -> dict:
    for name in _LEGACY_DUAL_WRITE_ENV_VARS:
        env.delete(name)
    for name in set_vars:
        env.set(name, "1")
    return health(store=_healthy_store(tmp_path), agent_id="agent-x")


def test_ok_when_no_legacy_env_var_is_set(env):
    # Arrange
    # Act
    res = _with_only(env)
    # Assert
    assert res["ok"] is True


# === the incident's actual env var — root cause 2026-07-21 =================
#
# There was a SECOND section here covering the pre-rename spelling of this
# toggle, and a third covering both being set at once (so that neither could
# be swallowed by the other in the detail line). The retired spelling is gone
# from the check's vocabulary, so those five tests would have pinned a name
# `_LEGACY_DUAL_WRITE_ENV_VARS` no longer contains — a "both are named" test
# over a one-element tuple. The one assertion in them that was NOT about the
# retired name is the hint's remedy wording, which is kept below against the
# surviving toggle.


def test_a_lingering_scitex_cards_dual_write_is_not_ok(env):
    # Arrange
    # Act
    res = _with_only(env, "SCITEX_CARDS_DUAL_WRITE")
    # Assert
    assert res["ok"] is False


def test_a_lingering_scitex_cards_dual_write_is_named_in_the_detail(env):
    # Arrange
    # Act
    res = _with_only(env, "SCITEX_CARDS_DUAL_WRITE")
    # Assert
    assert "SCITEX_CARDS_DUAL_WRITE" in res["detail"]


def test_a_lingering_scitex_cards_dual_write_hint_says_to_unset_it(env):
    # Arrange
    # Act
    res = _with_only(env, "SCITEX_CARDS_DUAL_WRITE")
    # Assert — constitution section 2: an error that only states what broke is
    # half-written. The hint must say what to DO.
    assert "unset" in (res["hint"] or "")


# === the deleted toggle stays deleted ======================================


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

    # Act — collect every survivor in one pass, so a failure names ALL of
    # them rather than stopping at whichever happens to be checked first.
    survivors = [
        name
        for name in (
            "enabled",
            "mirror_after_save",
            "ENV_DUAL_WRITE",
            "check_mirror_healthy",
        )
        if hasattr(dual_write_mod, name)
    ]
    # Assert
    assert not survivors, (
        f"scitex_cards._dual_write still exposes {survivors} — the dual-write "
        f"toggle was deleted as a feature, not defaulted off"
    )


def test_the_store_ownership_guard_survives_the_deletion():
    """`_db_mirrors_this_store` is NOT part of the deleted toggle."""
    # Arrange
    import scitex_cards._dual_write as dual_write_mod

    # Act
    guard = getattr(dual_write_mod, "_db_mirrors_this_store", None)
    # Assert
    assert callable(guard)


def test_the_same_file_helper_survives_the_deletion():
    """`_same_file` is NOT part of the deleted toggle."""
    # Arrange
    import scitex_cards._dual_write as dual_write_mod

    # Act
    same_file = getattr(dual_write_mod, "_same_file", None)
    # Assert
    assert callable(same_file)


# === the aggregator wires in the new check under its new name ==============


def test_health_runs_the_single_write_target_check(tmp_path, env):
    # Arrange
    # Act
    report = _health_with_only(env, tmp_path)
    # Assert
    assert "single_write_target" in {c["name"] for c in report["checks"]}


def test_health_no_longer_runs_the_deleted_dual_write_check(tmp_path, env):
    # Arrange
    # Act
    report = _health_with_only(env, tmp_path)
    # Assert — the deleted check must not linger under its old name, or the
    # report would still advertise a mirror nothing maintains.
    assert "dual_write_mirror" not in {c["name"] for c in report["checks"]}


def test_a_leaked_legacy_var_fails_the_write_target_check(tmp_path, env):
    # Arrange
    # Act
    report = _health_with_only(env, tmp_path, "SCITEX_CARDS_DUAL_WRITE")
    # Assert
    assert _check(report, "single_write_target")["ok"] is False


def test_a_leaked_legacy_var_fails_the_report_overall(tmp_path, env):
    # Arrange
    # Act
    report = _health_with_only(env, tmp_path, "SCITEX_CARDS_DUAL_WRITE")
    # Assert — split from its sibling: a check can fail while the aggregator
    # still reports green overall, and that combination is the actual bug this
    # file exists to catch.
    assert report["ok"] is False


# EOF
