#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_cards._throughput``.

Real list-of-dicts inputs (no mocks; STX-NM / PA-306). Each test
follows AAA + one-assertion-per-test (STX-TQ002 / STX-TQ007) per the
scitex-dev test-quality corpus.

Covers:
  * :func:`aggregate` — per-(agent|project|host) row builder
  * :func:`classify` — RUNNABLE / BLOCKED dependency gate
  * :func:`evaluate_wip` + :func:`count_open_for_agent` — write-side
    throttle thresholds
  * :func:`build_notify_body` — per-agent push body composition
"""

from __future__ import annotations

import datetime as _dt
import os

import pytest

from scitex_cards._throughput import (
    DEFAULT_STALE_HOURS,
    DEFAULT_WIP_LIMIT,
    ENV_STALE_HOURS,
    ENV_WIP_LIMIT,
    NOTIFY_OPEN_CAP,
    GateInfo,
    GroupStats,
    _parse_iso,
    aggregate,
    build_notify_body,
    classify,
    count_open_for_agent,
    evaluate_wip,
)


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pure_backlog_tasks():
    """200 cards owned by agent `a`, not one of them actually in flight.

    Shared by the three ``TestWipIsNotBacklog`` tests that each pin one
    consequence of the same 2026-07-10 shape.
    """
    return (
        [{"agent": "a", "status": "deferred"} for _ in range(50)]
        + [{"agent": "a", "status": "blocked"} for _ in range(50)]
        + [{"agent": "a", "status": "cancelled"} for _ in range(50)]
        + [{"agent": "a", "status": "failed"} for _ in range(50)]
    )


def _sac_incident_tasks():
    """The exact shape that made sac's gate say 88 and its digest say 2."""
    return [{"agent": "a", "status": "deferred"} for _ in range(86)] + [
        {"agent": "a", "status": "in_progress"} for _ in range(2)
    ]


# --------------------------------------------------------------------------- #
# aggregate                                                                   #
# --------------------------------------------------------------------------- #


class TestAggregate:
    """One row per group, with sane created/completed/delta/ratio/velocity."""

    def test_groups_one_row_per_agent(self):
        # Arrange
        tasks = [
            {"agent": "a", "status": "done"},
            {"agent": "a", "status": "pending"},
            {"agent": "b", "status": "in_progress"},
        ]
        # Act
        rows = aggregate(tasks, by="agent")
        # Assert
        assert sorted(r.name for r in rows) == ["a", "b"]

    def test_completed_counts_only_status_done(self):
        # Arrange
        tasks = [
            {"agent": "a", "status": "done"},
            {"agent": "a", "status": "deferred"},
            {"agent": "a", "status": "in_progress"},
        ]
        # Act
        rows = aggregate(tasks, by="agent")
        # Assert
        assert rows[0].completed == 1

    def test_open_count_excludes_goal_umbrella(self):
        # Arrange
        tasks = [
            {"agent": "a", "status": "goal"},
            {"agent": "a", "status": "pending"},
        ]
        # Act
        rows = aggregate(tasks, by="agent")
        # Assert
        assert rows[0].open_count == 1

    def test_ratio_zero_when_nothing_created(self):
        # Arrange
        g = GroupStats(name="x", created=0, completed=0)
        # Act
        r = g.ratio
        # Assert
        assert r == 0.0

    def test_unassigned_renders_explicitly(self):
        # Arrange
        tasks = [{"agent": None, "status": "pending"}]
        # Act
        rows = aggregate(tasks, by="agent")
        # Assert
        assert rows[0].name == "(unassigned)"

    def test_stale_flags_in_progress_with_old_last_activity(self):
        # Arrange
        now = _utc(2026, 6, 12, 12, 0, 0)
        old = _iso(_utc(2026, 6, 1, 0, 0, 0))  # ~11 days back
        tasks = [{"agent": "a", "status": "in_progress", "last_activity": old}]
        # Act
        rows = aggregate(tasks, by="agent", now=now, stale_hours=24)
        # Assert
        assert rows[0].stale_count == 1

    def test_stale_excludes_pending(self):
        # Arrange
        now = _utc(2026, 6, 12, 12, 0, 0)
        tasks = [
            {"agent": "a", "status": "pending", "last_activity": "2026-01-01T00:00:00Z"}
        ]
        # Act
        rows = aggregate(tasks, by="agent", now=now, stale_hours=24)
        # Assert
        assert rows[0].stale_count == 0

    def test_unknown_by_axis_raises(self):
        # Arrange
        axis = "nonsense"
        # Act
        # Assert
        with pytest.raises(ValueError):
            aggregate([], by=axis)


# --------------------------------------------------------------------------- #
# classify (RUNNABLE / BLOCKED)                                              #
# --------------------------------------------------------------------------- #


class TestClassify:
    """RUNNABLE / BLOCKED decision per lead spec 02b71bd0 / 130cc5ac."""

    def test_no_deps_runnable(self):
        # Arrange
        t = {"id": "x", "status": "pending"}
        # Act
        gi = classify(t, {"x": t})
        # Assert
        assert gi.label == "RUNNABLE"

    def test_deps_all_done_runnable(self):
        # Arrange
        by_id = {
            "x": {"id": "x", "status": "pending", "depends_on": ["dep"]},
            "dep": {"id": "dep", "status": "done"},
        }
        # Act
        gi = classify(by_id["x"], by_id)
        # Assert
        assert gi.label == "RUNNABLE"

    def test_open_dep_blocks_with_dep_id(self):
        # Arrange
        by_id = {
            "x": {"id": "x", "status": "pending", "depends_on": ["dep"]},
            "dep": {"id": "dep", "status": "in_progress"},
        }
        # Act
        gi = classify(by_id["x"], by_id)
        # Assert
        assert gi.reason == "→ dep"

    def test_unknown_dep_blocks_defensive(self):
        # Arrange
        by_id = {"x": {"id": "x", "status": "pending", "depends_on": ["missing"]}}
        # Act
        gi = classify(by_id["x"], by_id)
        # Assert
        assert gi.reason == "→ unknown:missing"

    def test_status_blocked_surfaces_blocker_reason(self):
        # Arrange
        by_id = {"x": {"id": "x", "status": "blocked", "blocker": "operator-decision"}}
        # Act
        gi = classify(by_id["x"], by_id)
        # Assert
        assert gi.reason == "operator-decision"

    def test_blocked_takes_precedence_over_deps(self):
        # Arrange
        by_id = {
            "x": {
                "id": "x",
                "status": "blocked",
                "blocker": "compute",
                "depends_on": ["dep"],
            },
            "dep": {"id": "dep", "status": "in_progress"},
        }
        # Act
        gi = classify(by_id["x"], by_id)
        # Assert — `status=blocked` wins; blocker is "compute", not "→ dep"
        assert gi.reason == "compute"


# --------------------------------------------------------------------------- #
# WIP gate                                                                    #
# --------------------------------------------------------------------------- #


class TestWipGate:
    """Open-task count + warn/refuse thresholds.

    Thresholds fire on WIP (``in_progress``), not on backlog. See
    :class:`TestWipIsNotBacklog` for the incident this distinction closes.
    """

    def test_count_open_excludes_done(self):
        # Arrange
        tasks = [
            {"agent": "a", "status": "done"},
            {"agent": "a", "status": "deferred"},
        ]
        # Act
        n = count_open_for_agent(tasks, "a")
        # Assert — deferred is open ("not now" is not "never").
        assert n == 1

    def test_count_open_excludes_goal(self):
        # Arrange
        tasks = [
            {"agent": "a", "status": "goal"},
            {"agent": "a", "status": "in_progress"},
        ]
        # Act
        n = count_open_for_agent(tasks, "a")
        # Assert
        assert n == 1

    def test_evaluate_wip_none_when_no_agent(self):
        # Arrange
        agent = None
        # Act
        rep = evaluate_wip([], agent=agent)
        # Assert
        assert rep is None

    def test_warn_at_limit(self, env):
        # Arrange
        env.set(ENV_WIP_LIMIT, "3")
        tasks = [{"agent": "a", "status": "in_progress"} for _ in range(3)]
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert
        assert rep.is_warn is True

    def test_refuse_at_2x(self, env):
        # Arrange
        env.set(ENV_WIP_LIMIT, "3")
        tasks = [{"agent": "a", "status": "in_progress"} for _ in range(6)]
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert
        assert rep.is_refuse is True

    def test_below_limit_no_warn(self, env):
        # Arrange
        env.set(ENV_WIP_LIMIT, "10")
        tasks = [{"agent": "a", "status": "in_progress"} for _ in range(3)]
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert
        assert rep.is_warn is False


class TestWipIsNotBacklog:
    """Regression: the gate must bound work STARTED, never work RECORDED.

    Incident 2026-07-10 — the gate counted every card that was not
    ``done``/``goal``, so ``deferred``, ``failed`` and ``cancelled`` cards all
    consumed budget forever. After the pending→deferred migration agents hit
    "88 open tasks (>= 2x limit 20)" and could no longer file *anything* —
    including the incident card describing the jam — while their own board
    digest showed 2 open. Two predicates, one name.
    """

    def test_backlog_does_not_consume_wip(self, env):
        # Arrange — a huge parked backlog, nothing actually in flight.
        env.set(ENV_WIP_LIMIT, "3")
        tasks = _pure_backlog_tasks()
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert — 200 cards, zero of them in flight.
        assert rep.wip_count == 0

    def test_a_pure_backlog_owner_is_not_warned(self, env):
        # Arrange
        env.set(ENV_WIP_LIMIT, "3")
        tasks = _pure_backlog_tasks()
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert
        assert rep.is_warn is False

    def test_a_pure_backlog_owner_is_not_refused(self, env):
        # Arrange
        env.set(ENV_WIP_LIMIT, "3")
        tasks = _pure_backlog_tasks()
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert — this is the refusal that jammed the fleet.
        assert rep.is_refuse is False

    def test_terminal_cards_drop_out_of_open_count(self):
        # Arrange — the docstring at the top of _throughput.py always claimed
        # this; the code did not do it until 2026-07-10.
        tasks = [
            {"agent": "a", "status": "cancelled"},
            {"agent": "a", "status": "failed"},
            {"agent": "a", "status": "done"},
            {"agent": "a", "status": "goal"},
            {"agent": "a", "status": "blocked"},
        ]
        # Act
        n = count_open_for_agent(tasks, "a")
        # Assert — only the blocked card is still open.
        assert n == 1

    def test_gate_and_digest_agree_on_the_open_count(self):
        # Arrange
        tasks = _sac_incident_tasks()
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert — the backlog number, which is NOT the gate's budget.
        assert rep.open_count == 88

    def test_gate_and_digest_agree_on_the_wip_count(self):
        # Arrange
        tasks = _sac_incident_tasks()
        # Act
        rep = evaluate_wip(tasks, agent="a")
        # Assert — the in-flight number; neither is silently doing the
        # other's job, which is the whole two-predicates-one-name bug.
        assert rep.wip_count == 2

    def test_recording_an_incident_is_never_refused(self, tmp_path, env):
        # Arrange — agent is at 2x its WIP limit (the gate itself refuses
        # seeding past that, which is the sibling test's job to pin).
        import contextlib

        from scitex_cards._model import TaskValidationError
        from scitex_cards._store import add_task

        env.set(ENV_WIP_LIMIT, "1")
        store = tmp_path / "tasks.yaml"
        store.write_text("tasks: []\n")
        # add_task's post-write card-event dispatcher resolves the DEFAULT
        # store, not the one passed in — so without this the suite reads and
        # writes the operator's live ~/.scitex/todo/tasks.yaml.
        for i in range(4):
            with contextlib.suppress(TaskValidationError):
                add_task(
                    id=f"wip-{i}",
                    title=f"in flight {i}",
                    status="in_progress",
                    agent="a",
                    store=store,
                )

        # Act — filing (not starting) a card must still succeed.
        rec = add_task(
            id="incident-the-gate-is-jammed",
            title="[INCIDENT] cannot file anything",
            status="blocked",
            blocker="operator-decision",
            agent="a",
            store=store,
        )

        # Assert
        assert rec["id"] == "incident-the-gate-is-jammed"

    def test_starting_more_work_past_2x_is_still_refused(self, tmp_path, env):
        # Arrange — the gate must not become a no-op.
        from scitex_cards._model import TaskValidationError
        from scitex_cards._store import add_task

        env.set(ENV_WIP_LIMIT, "1")
        store = tmp_path / "tasks.yaml"
        store.write_text("tasks: []\n")
        for i in range(2):
            add_task(
                id=f"wip-{i}",
                title=f"in flight {i}",
                status="in_progress",
                agent="a",
                store=store,
            )

        # Act
        # Assert — a third in-flight card is past 2x and must be refused.
        with pytest.raises(TaskValidationError, match="in_progress"):
            add_task(
                id="one-too-many",
                title="starting a third",
                status="in_progress",
                agent="a",
                store=store,
            )


# --------------------------------------------------------------------------- #
# build_notify_body                                                           #
# --------------------------------------------------------------------------- #


class TestNotifyBody:
    """Composed body per lead spec 5263c8d9 + 02b71bd0 + 130cc5ac."""

    def test_header_includes_agent_name(self):
        # Arrange
        tasks = [{"id": "t1", "title": "T1", "status": "pending", "agent": "a"}]
        # Act
        body = build_notify_body("a", tasks)
        # Assert
        assert body.startswith("a ·")

    def test_header_includes_open_count(self):
        # Arrange
        tasks = [
            {"id": "t1", "title": "T1", "status": "pending", "agent": "a"},
            {"id": "t2", "title": "T2", "status": "in_progress", "agent": "a"},
        ]
        # Act
        body = build_notify_body("a", tasks)
        # Assert
        assert "open 2" in body.splitlines()[0]

    def test_runnable_listed_before_blocked(self):
        # Arrange
        tasks = [
            {
                "id": "blk",
                "title": "Blocked one",
                "status": "blocked",
                "blocker": "compute",
                "agent": "a",
            },
            {"id": "run", "title": "Runnable one", "status": "pending", "agent": "a"},
        ]
        # Act
        body = build_notify_body("a", tasks)
        # Assert — `run` line index < `blk` line index
        lines = body.splitlines()
        idx_run = next(i for i, ln in enumerate(lines) if "run" in ln)
        idx_blk = next(i for i, ln in enumerate(lines) if "blk" in ln)
        assert idx_run < idx_blk

    def test_truncation_at_open_cap(self):
        # Arrange
        tasks = [
            {"id": f"t{i}", "title": f"Task {i}", "status": "pending", "agent": "a"}
            for i in range(NOTIFY_OPEN_CAP + 5)
        ]
        # Act
        body = build_notify_body("a", tasks)
        # Assert
        assert "more open" in body

    def test_stale_in_progress_marked(self):
        # Arrange
        now = _utc(2026, 6, 12, 12, 0, 0)
        old = _iso(_utc(2026, 6, 1, 0, 0, 0))
        tasks = [
            {
                "id": "old",
                "title": "Old task",
                "status": "in_progress",
                "last_activity": old,
                "agent": "a",
            },
        ]
        # Act
        body = build_notify_body("a", tasks, now=now, stale_hours=24)
        # Assert
        assert "⚠" in body

    def test_recent_done_section_appears(self):
        # Arrange
        now = _utc(2026, 6, 12, 12, 0, 0)
        recent = _iso(_utc(2026, 6, 11, 12, 0, 0))
        tasks = [
            {
                "id": "d1",
                "title": "Done one",
                "status": "done",
                "last_activity": recent,
                "agent": "a",
            },
        ]
        # Act
        body = build_notify_body("a", tasks, now=now)
        # Assert
        assert "done 1d ago" in body

    def test_naive_last_activity_does_not_crash(self):
        # Regression guard: a tasks.yaml row whose `last_activity` was
        # serialized WITHOUT a timezone suffix (e.g. "2026-06-08T00:42:30")
        # must not raise `TypeError: can't subtract offset-naive and
        # offset-aware datetimes` inside build_notify_body. Before the fix
        # this single naive row killed the entire --notify / --nudge-quiet
        # cron loop on the first hit, so no POSTs ever fired (proj-scitex-
        # todo P3a(c) pilot, 2026-06-13).
        # Arrange
        now = _utc(2026, 6, 13, 0, 0, 0)
        naive_ts = "2026-06-08T00:42:30"  # NO 'Z', NO offset — naive.
        tasks = [
            {
                "id": "naive",
                "title": "Naive last_activity",
                "status": "in_progress",
                "last_activity": naive_ts,
                "agent": "a",
            },
        ]
        # Act
        body = build_notify_body("a", tasks, now=now)
        # Assert — composition succeeded (header present); no crash.
        assert body.startswith("a ·")


# --------------------------------------------------------------------------- #
# _parse_iso                                                                  #
# --------------------------------------------------------------------------- #


class TestParseIso:
    """Naive-vs-aware coercion (regression for the P3a(c) cron crash)."""

    def test_naive_string_coerces_to_utc_aware(self):
        # Arrange
        naive_ts = "2026-06-08T00:42:30"
        # Act
        parsed = _parse_iso(naive_ts)
        # Assert
        assert parsed.tzinfo is _dt.timezone.utc
