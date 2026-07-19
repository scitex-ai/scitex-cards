#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the package-level health doctor (:func:`scitex_cards._health.health`).

Real, hermetic round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path``
YAML store, real :mod:`scitex_cards._inbox` enqueues, and explicit ``store`` /
``agent_id`` params so nothing depends on the process environment. Sync tests
(the repo has no pytest-asyncio) — the health function is pure and synchronous.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys

import pytest

from scitex_cards import _inbox
from scitex_cards._delivery._pidfile import local_identity
from scitex_cards._health import UNSEEN_BACKLOG_THRESHOLD, health


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _healthy_store(tmp_path):
    """Write a real, minimal-but-valid task store and return its path."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    return store


def _check(report, name):
    """Return the check record with ``name`` (or raise KeyError)."""
    for c in report["checks"]:
        if c["name"] == name:
            return c
    raise KeyError(name)


def _healthy_report(tmp_path, agent_id="agent-x"):
    return health(store=_healthy_store(tmp_path), agent_id=agent_id)


# --------------------------------------------------------------------------- #
# output shape — the cross-package standard                                   #
# --------------------------------------------------------------------------- #
def test_report_has_exactly_the_standard_top_level_keys(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert set(report) == {"package", "ok", "checks", "summary"}


def test_report_names_the_package(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert report["package"] == "scitex-todo"


def test_report_ok_is_a_bool(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert isinstance(report["ok"], bool)


def test_report_summary_is_a_non_empty_string(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert isinstance(report["summary"], str) and report["summary"]


def test_report_checks_is_a_non_empty_list(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert isinstance(report["checks"], list) and report["checks"]


def test_every_check_record_has_exactly_the_four_standard_fields(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert all(set(c) == {"name", "ok", "detail", "hint"} for c in report["checks"]), (
        report["checks"]
    )


def test_every_check_name_is_a_non_empty_string(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert all(isinstance(c["name"], str) and c["name"] for c in report["checks"]), (
        report["checks"]
    )


def test_every_check_ok_is_a_bool(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert all(isinstance(c["ok"], bool) for c in report["checks"]), report["checks"]


def test_every_check_detail_is_a_string(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert all(isinstance(c["detail"], str) for c in report["checks"]), report["checks"]


def test_every_check_hint_is_a_string_or_none(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert all(
        c["hint"] is None or isinstance(c["hint"], str) for c in report["checks"]
    ), report["checks"]


def test_expected_checks_present(tmp_path):
    # Arrange
    expected = {
        "store_canonical",
        "agent_id",
        "notifyd_alive",
        "channel_drain",
        "channel_capable",
    }

    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert expected <= {c["name"] for c in report["checks"]}


def test_ok_is_true_iff_every_check_ok(tmp_path):
    # Arrange
    # Act
    report = _healthy_report(tmp_path)

    # Assert
    assert report["ok"] == all(c["ok"] for c in report["checks"])


# The contract: no silent fail — a failing check ALWAYS has a real hint. A
# deliberately unhealthy call (bad agent id, nonexistent store) provokes the
# failures, then the invariant is asserted over whatever failed.
def _failing_checks(tmp_path):
    report = health(store=tmp_path / "nope.yaml", agent_id="unknown")
    return [c for c in report["checks"] if not c["ok"]]


def test_the_unhealthy_scenario_really_fails_a_check(tmp_path):
    # Arrange
    # Act
    failing = _failing_checks(tmp_path)

    # Assert — the premise; without it the hint invariant holds vacuously.
    assert failing, "expected at least one failing check in this scenario"


def test_every_failing_check_carries_a_hint(tmp_path):
    # Arrange
    # Act
    failing = _failing_checks(tmp_path)

    # Assert
    assert all(c["hint"] for c in failing), (
        f"failing checks with no actionable hint: "
        f"{[c['name'] for c in failing if not c['hint']]}"
    )


# --------------------------------------------------------------------------- #
# store_canonical                                                             #
# --------------------------------------------------------------------------- #
def test_store_canonical_ok_for_healthy_tmp_store(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path), "store_canonical")

    # Assert
    assert c["ok"] is True


def test_a_passing_store_canonical_check_carries_no_hint(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path), "store_canonical")

    # Assert — a hint on a passing check is noise.
    assert c["hint"] is None


def test_store_canonical_fails_when_store_missing(tmp_path):
    # Arrange
    # Act
    report = health(store=tmp_path / "absent.yaml", agent_id="agent-x")

    # Assert
    assert _check(report, "store_canonical")["ok"] is False


def test_a_missing_store_failure_carries_a_hint(tmp_path):
    # Arrange
    # Act
    report = health(store=tmp_path / "absent.yaml", agent_id="agent-x")

    # Assert
    assert _check(report, "store_canonical")["hint"]


def _report_for_a_store_without_a_tasks_key(tmp_path):
    store = tmp_path / "tasks.yaml"
    store.write_text("users: {}\n", encoding="utf-8")  # valid YAML, no `tasks:`
    return health(store=store, agent_id="agent-x")


def test_store_canonical_fails_without_tasks_key(tmp_path):
    # Arrange
    # Act
    report = _report_for_a_store_without_a_tasks_key(tmp_path)

    # Assert
    assert _check(report, "store_canonical")["ok"] is False


def test_the_missing_tasks_key_hint_names_the_missing_key(tmp_path):
    # Arrange
    # Act
    report = _report_for_a_store_without_a_tasks_key(tmp_path)

    # Assert
    assert "tasks" in _check(report, "store_canonical")["hint"]


# --------------------------------------------------------------------------- #
# agent_id                                                                    #
# --------------------------------------------------------------------------- #
def test_agent_id_ok_for_real_value(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path, agent_id="real-agent"), "agent_id")

    # Assert
    assert c["ok"] is True


def test_the_agent_id_detail_echoes_the_resolved_identity(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path, agent_id="real-agent"), "agent_id")

    # Assert
    assert "real-agent" in c["detail"]


def test_agent_id_fails_on_unknown_sentinel(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path, agent_id="unknown"), "agent_id")

    # Assert
    assert c["ok"] is False


def test_the_unknown_agent_id_hint_names_the_env_var_to_set(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path, agent_id="unknown"), "agent_id")

    # Assert
    assert c["hint"] and "SCITEX_TODO_AGENT_ID" in c["hint"]


def test_agent_id_fails_on_unexpanded_placeholder(tmp_path):
    # Arrange
    # Act
    c = _check(
        _healthy_report(tmp_path, agent_id="${SCITEX_TODO_AGENT_ID}"), "agent_id"
    )

    # Assert
    assert c["ok"] is False


def test_an_unexpanded_placeholder_agent_id_carries_a_hint(tmp_path):
    # Arrange
    # Act
    c = _check(
        _healthy_report(tmp_path, agent_id="${SCITEX_TODO_AGENT_ID}"), "agent_id"
    )

    # Assert
    assert c["hint"]


# --------------------------------------------------------------------------- #
# channel_drain                                                               #
# --------------------------------------------------------------------------- #
def _enqueue_backlog(store, agent, count, *, prefix="c", day="28", actor="bob"):
    for i in range(count):
        _inbox.enqueue(
            agent,
            event_type="reassigned",
            card_id=f"{prefix}{i}",
            body=f"body {i}",
            actor=actor,
            ts=f"2026-06-{day}T{10 + i // 60:02d}:{i % 60:02d}:00Z",
            store=store,
        )


def test_channel_drain_ok_for_small_backlog(tmp_path):
    # Arrange
    store = _healthy_store(tmp_path)
    _enqueue_backlog(store, "agent-x", 3)

    # Act
    c = _check(health(store=store, agent_id="agent-x"), "channel_drain")

    # Assert
    assert c["ok"] is True


def test_a_small_backlog_drain_check_carries_no_hint(tmp_path):
    # Arrange
    store = _healthy_store(tmp_path)
    _enqueue_backlog(store, "agent-x", 3)

    # Act
    c = _check(health(store=store, agent_id="agent-x"), "channel_drain")

    # Assert
    assert c["hint"] is None


@pytest.fixture(scope="module")
def undrained_backlog_check(tmp_path_factory):
    """One over-threshold, NEVER-drained inbox (seen == 0), judged once.

    Module-scoped because building it enqueues UNSEEN_BACKLOG_THRESHOLD+1
    notifications through the real store — expensive to repeat per assertion.
    """
    store = tmp_path_factory.mktemp("undrained") / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    _enqueue_backlog(store, "agent-x", UNSEEN_BACKLOG_THRESHOLD + 1)
    return _check(health(store=store, agent_id="agent-x"), "channel_drain")


def test_channel_drain_fails_on_large_unseen_backlog_with_no_seen(
    undrained_backlog_check,
):
    # Arrange
    # Act
    c = undrained_backlog_check

    # Assert
    assert c["ok"] is False


def test_the_undrained_backlog_hint_says_the_channel_is_not_draining(
    undrained_backlog_check,
):
    # Arrange
    # Act
    c = undrained_backlog_check

    # Assert
    assert c["hint"] and "not draining" in c["hint"]


def test_the_undrained_backlog_hint_names_the_command_that_fixes_it(
    undrained_backlog_check,
):
    # Arrange
    # Act
    c = undrained_backlog_check

    # Assert
    assert "mcp start" in c["hint"]


def test_channel_drain_ok_when_some_seen_even_if_unseen_large(tmp_path):
    # Arrange — a busy-but-working inbox: drain one batch so seen > 0, then
    # pile a fresh unseen backlog on top.
    store = _healthy_store(tmp_path)
    agent = "agent-x"
    _enqueue_backlog(store, agent, UNSEEN_BACKLOG_THRESHOLD + 5)
    _inbox.poll_inbox(agent, unseen_only=True, mark_seen=True, store=store)
    _enqueue_backlog(
        store, agent, UNSEEN_BACKLOG_THRESHOLD + 5, prefix="d", day="29", actor="alice"
    )

    # Act
    c = _check(health(store=store, agent_id=agent), "channel_drain")

    # Assert — seen > 0 keeps it healthy however big the unseen pile is.
    assert c["ok"] is True


# --------------------------------------------------------------------------- #
# channel_capable                                                             #
# --------------------------------------------------------------------------- #
def test_channel_capable_ok(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path), "channel_capable")

    # Assert
    assert c["ok"] is True


def test_a_passing_channel_capable_check_carries_no_hint(tmp_path):
    # Arrange
    # Act
    c = _check(_healthy_report(tmp_path), "channel_capable")

    # Assert
    assert c["hint"] is None


# --------------------------------------------------------------------------- #
# notifyd_alive — liveness must survive a PID-namespace boundary              #
# --------------------------------------------------------------------------- #
def _stamp_pidfile(store, pid, *, identity, heartbeat):
    """Write a REAL notifyd pidfile where the health check will look for it."""
    from scitex_cards._delivery._daemon import pidfile_path
    from scitex_cards._delivery._pidfile import render

    path = pidfile_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render(pid, interval=120.0, now=heartbeat, identity=identity),
        encoding="utf-8",
    )
    return path


def _elsewhere():
    """Identity of a DIFFERENT PID namespace (the bare host, seen from a container)."""
    mine = local_identity()
    return {
        "host": mine["host"],  # apptainer shares the UTS ns — same name!
        "boot_id": mine["boot_id"],
        "pid_ns": "pid:[4026531836]-the-host",
        "container": "0",
    }


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _notifyd_check_for_a_fresh_foreign_daemon(tmp_path):
    """A daemon in another PID namespace whose heartbeat is FRESH."""
    store = _healthy_store(tmp_path)
    _stamp_pidfile(
        store,
        _dead_pid(),  # not a pid we can resolve — it is not ours to interpret
        identity=_elsewhere(),
        heartbeat=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=20),
    )
    return _check(health(store=store, agent_id="agent-x"), "notifyd_alive")


def test_notifyd_alive_for_a_fresh_daemon_in_another_namespace(tmp_path):
    """THE regression: a healthy host daemon must not be declared dead in a container.

    The pid in the shared pidfile is unresolvable here (it belongs to another PID
    namespace), but its heartbeat is fresh — health must report ALIVE.
    """
    # Arrange
    # Act
    c = _notifyd_check_for_a_fresh_foreign_daemon(tmp_path)

    # Assert
    assert c["ok"] is True, c["detail"]


def test_a_live_foreign_daemon_check_carries_no_hint(tmp_path):
    # Arrange
    # Act
    c = _notifyd_check_for_a_fresh_foreign_daemon(tmp_path)

    # Assert
    assert c["hint"] is None


def _notifyd_check_for_a_stale_foreign_daemon(tmp_path):
    """A daemon in another PID namespace whose heartbeat went STALE."""
    store = _healthy_store(tmp_path)
    _stamp_pidfile(
        store,
        os.getpid(),  # ALIVE here — proves the pid is not what decided
        identity=_elsewhere(),
        heartbeat=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=3),
    )
    return _check(health(store=store, agent_id="agent-x"), "notifyd_alive")


def test_notifyd_dead_when_a_foreign_daemon_stopped_ticking(tmp_path):
    """Fail-loud preserved across the boundary: a stale heartbeat is a real death."""
    # Arrange
    # Act
    c = _notifyd_check_for_a_stale_foreign_daemon(tmp_path)

    # Assert
    assert c["ok"] is False


def test_a_stale_foreign_daemon_is_reported_as_stale(tmp_path):
    # Arrange
    # Act
    c = _notifyd_check_for_a_stale_foreign_daemon(tmp_path)

    # Assert — the detail says WHICH death this was.
    assert "STALE" in c["detail"]


def test_a_stale_foreign_daemon_failure_carries_a_hint(tmp_path):
    # Arrange
    # Act
    c = _notifyd_check_for_a_stale_foreign_daemon(tmp_path)

    # Assert
    assert c["hint"]


def _notifyd_check_for_a_dead_local_daemon(tmp_path):
    """A corpse in OUR namespace, with a deliberately fresh heartbeat."""
    store = _healthy_store(tmp_path)
    _stamp_pidfile(
        store,
        _dead_pid(),
        identity=local_identity(),
        heartbeat=_dt.datetime.now(_dt.timezone.utc),  # fresh, but it IS a corpse
    )
    return _check(health(store=store, agent_id="agent-x"), "notifyd_alive")


def test_notifyd_dead_for_a_dead_local_daemon(tmp_path):
    """A genuinely dead daemon in OUR namespace is still caught by the pid probe."""
    # Arrange
    # Act
    c = _notifyd_check_for_a_dead_local_daemon(tmp_path)

    # Assert
    assert c["ok"] is False


def test_a_dead_local_daemon_is_reported_as_not_running(tmp_path):
    # Arrange
    # Act
    c = _notifyd_check_for_a_dead_local_daemon(tmp_path)

    # Assert — the pid probe, not the heartbeat, is what decided here.
    assert "is not running" in c["detail"]


def test_a_dead_local_daemon_failure_carries_a_hint(tmp_path):
    # Arrange
    # Act
    c = _notifyd_check_for_a_dead_local_daemon(tmp_path)

    # Assert
    assert c["hint"]


# --------------------------------------------------------------------------- #
# never raises                                                                #
# --------------------------------------------------------------------------- #
# A nonexistent store + a bad agent id must still return a well-formed report,
# never propagate an exception.
def _report_for_bad_inputs(tmp_path):
    return health(store=tmp_path / "does-not-exist.yaml", agent_id="")


def test_health_still_names_the_package_on_bad_inputs(tmp_path):
    # Arrange
    # Act
    report = _report_for_bad_inputs(tmp_path)

    # Assert
    assert report["package"] == "scitex-todo"


def test_health_still_returns_a_bool_ok_on_bad_inputs(tmp_path):
    # Arrange
    # Act
    report = _report_for_bad_inputs(tmp_path)

    # Assert
    assert isinstance(report["ok"], bool)


def test_health_still_returns_checks_on_bad_inputs(tmp_path):
    # Arrange
    # Act
    report = _report_for_bad_inputs(tmp_path)

    # Assert — a well-formed report, not an empty shell.
    assert report["checks"]


# EOF
