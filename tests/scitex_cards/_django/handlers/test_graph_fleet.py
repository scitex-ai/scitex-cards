#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``fleet`` key on the GET /graph payload — the per-agent
liveness summary the operator scans from the board header to answer
"who is alive + working on what + blocked on me" without leaving the
board (ADR-0008 design, ticket ``proj-scitex-todo-fleet-liveness``,
operator TG 9576: 返事が来ない＝私にとって死んだのと同じ).

First-slice scope (this PR — what's covered here):
  * payload includes a ``fleet`` list, ordered deterministically (sorted
    by agent name)
  * status precedence: blocking-operator > working > active > idle
  * current_task derivation: in_progress > most-recent activity > top-
    priority pending
  * counts (task_count / runnable_count / blocked_count /
    blocking_operator_count) reflect the input
  * tasks without ``agent`` (and without ``assignee`` fallback) are
    excluded
  * ``assignee`` is honoured as a fallback for older rows

DEFERRED (NOT covered):
  * sidecar daemon liveness (no daemon today; status derived from YAML)
  * cross-host roll-up (single-host slice only)
  * JS interaction tests (no headless browser in the suite)

Real ``RequestFactory`` GET against a tmp ``tasks.yaml`` — no mocks
(STX-NM / PA-306). Mirrors the test pattern in ``test_resolve.py``.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402


def _ago_iso(seconds: float) -> str:
    """ISO-8601 UTC stamp ``seconds`` ago, ``Z``-suffixed.

    Used in the curated fixture so timestamps stay RELATIVE to the
    test's "now", which lets the working-status decay rule (operator
    TG12739) make the right call without freezing clocks: a fresh
    in_progress row stays "working", an old one decays to "stale".
    """
    ts = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _store_text() -> str:
    """Curated 7-task fixture exercising every status-precedence branch +
    the current_task derivation chain + the assignee fallback + the
    working-status decay rule.

    Built at call-time so the relative timestamps land inside the
    decay windows regardless of wall-clock drift. The clew in_progress
    task is fresh (60s ago, well inside the default 10-min working
    window) so it reads as "working"; the hub-active task is older
    (15 min) so it reads "active" only inside the default 60-min
    active window; the orochi-stale task is in_progress but old
    (1h+30m) so it decays to "stale".
    """
    return (
        "tasks:\n"
        "  - id: clew-pac-line\n"
        "    title: 'clew PAC line'\n"
        "    status: in_progress\n"
        "    agent: proj-paper-scitex-clew\n"
        "    project: paper-scitex-clew\n"
        "    priority: 1\n"
        f"    last_activity: '{_ago_iso(60)}'\n"
        "  - id: clew-figure-fixes\n"
        "    title: 'clew figure fixes'\n"
        "    status: pending\n"
        "    agent: proj-paper-scitex-clew\n"
        "    priority: 30\n"
        "  - id: nv-stuck-on-op\n"
        "    title: 'NV stuck on operator decision'\n"
        "    status: blocked\n"
        "    blocker: operator-decision\n"
        "    agent: proj-neurovista\n"
        f"    last_activity: '{_ago_iso(60 * 60 * 6)}'\n"
        "  - id: nv-other\n"
        "    title: 'NV other'\n"
        "    status: pending\n"
        "    agent: proj-neurovista\n"
        "    priority: 50\n"
        "  - id: hub-recent\n"
        "    title: 'hub recent activity'\n"
        "    status: pending\n"
        "    agent: proj-scitex-hub\n"
        f"    last_activity: '{_ago_iso(60 * 15)}'\n"
        "    priority: 20\n"
        "  - id: orochi-stale-in-progress\n"
        "    title: 'orochi forgot to flip back'\n"
        "    status: in_progress\n"
        "    agent: proj-scitex-orochi\n"
        "    project: scitex-orochi\n"
        "    priority: 10\n"
        f"    last_activity: '{_ago_iso(60 * 60 * 1 + 60 * 30)}'\n"
        "  - id: orphan-task\n"
        "    title: 'task with no agent or assignee'\n"
        "    status: pending\n"
        "  - id: legacy-assignee-task\n"
        "    title: 'older row using assignee instead of agent'\n"
        "    status: pending\n"
        "    assignee: legacy-agent\n"
        "    priority: 60\n"
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(_store_text(), encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _graph(store_path: str) -> dict:
    """GET /graph against the tmp store and return the parsed payload."""
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    if response.status_code != 200:
        raise AssertionError(f"GET /graph failed: {response.content!r}")
    return json.loads(response.content)


def _fleet_by_name(payload: dict) -> dict:
    """Index the fleet list by agent name."""
    return {a["name"]: a for a in payload["fleet"]}


# === Payload shape ==========================================================


def test_graph_payload_contains_fleet_key(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    assert "fleet" in payload


def test_fleet_is_a_list(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    assert isinstance(payload["fleet"], list)


def test_fleet_is_sorted_by_agent_name(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    # deterministic ordering so the FE dot-strip doesn't reshuffle
    # on every poll. Sorted by the `name` field ascending.
    names = [a["name"] for a in payload["fleet"]]
    assert names == sorted(names)


# === Exclusion of agent-less tasks ==========================================


def test_orphan_task_with_no_agent_is_excluded(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    # the only task carrying neither `agent` nor `assignee`
    # should not surface a fleet row (the dot-strip stays focused on
    # known agents).
    names = {a["name"] for a in payload["fleet"]}
    assert "orphan-task" not in names


def test_assignee_is_used_as_agent_fallback(store):
    # Arrange
    # Act
    payload = _graph(store)
    # Assert
    # older rows using `assignee` (the pre-rename field) still
    # surface as a fleet row keyed by that name.
    names = {a["name"] for a in payload["fleet"]}
    assert "legacy-agent" in names


# === Status precedence ======================================================


def test_blocking_operator_takes_precedence_over_working(store):
    # Arrange
    # proj-neurovista has 1 blocker=operator-decision + 1 pending.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["status"] == "blocking-operator"


def test_in_progress_yields_working_status(store):
    # Arrange
    # proj-paper-scitex-clew has an in_progress task + a pending.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-paper-scitex-clew"]["status"] == "working"


def test_recent_activity_yields_active_status(store):
    # Arrange
    # proj-scitex-hub has only pending tasks but one has
    # last_activity set (no blocker, no in_progress).
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-scitex-hub"]["status"] == "active"


def test_no_activity_no_progress_yields_idle_status(store):
    # Arrange
    # legacy-agent owns one pending task with no last_activity.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["legacy-agent"]["status"] == "idle"


def test_stale_in_progress_decays_to_stale_status(store):
    # Arrange
    # proj-scitex-orochi has an in_progress task whose
    # last_activity is OLDER than the working window (the operator-TG12739
    # decay rule). Manual `working` is no longer treated as live activity.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-scitex-orochi"]["status"] == "stale"


# === current_task derivation ================================================


def test_current_task_prefers_in_progress(store):
    # Arrange
    # proj-paper-scitex-clew has clew-pac-line (in_progress) and
    # clew-figure-fixes (pending). The dot-strip tooltip should name the
    # in_progress one.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-paper-scitex-clew"]["current_task_id"] == "clew-pac-line"


def test_current_task_falls_back_to_most_recent_activity(store):
    # Arrange
    # proj-scitex-hub: no in_progress; one task with
    # last_activity. The dot-strip should surface that one.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-scitex-hub"]["current_task_id"] == "hub-recent"


# === Counts =================================================================


def test_task_count_matches_input(store):
    # Arrange
    # proj-neurovista has 2 tasks in the fixture.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["task_count"] == 2


def test_blocked_count_only_counts_status_blocked(store):
    # Arrange
    # proj-neurovista has 1 blocked + 1 pending.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["blocked_count"] == 1


def test_blocking_operator_count_only_counts_operator_decision(store):
    # Arrange
    # proj-neurovista has 1 task with blocker=operator-decision.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["blocking_operator_count"] == 1


def test_runnable_count_excludes_blocked_done_deferred_failed_goal(store):
    # Arrange
    # proj-neurovista: 1 blocked + 1 pending. Runnable = the
    # pending one. blocked / done / deferred / failed / goal are the
    # non-runnable set (mirrors the task-harvest skill).
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert fleet["proj-neurovista"]["runnable_count"] == 1


def test_overdue_count_present_on_every_fleet_row(store):
    # The fleet payload exposes per-agent `overdue_count` so the FE can
    # surface a tally without re-walking the store (todo-p6-overdue-ui).
    # The curated fixture has no deadlines, so every count is 0 — the
    # key still must be there.
    # Arrange
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert all("overdue_count" in row for row in fleet.values())


def test_overdue_count_zero_when_no_deadlines(store):
    # Arrange
    # fixture has no deadline fields anywhere.
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert all(row["overdue_count"] == 0 for row in fleet.values())


# === Overdue counts (dedicated fixture with deadline rows) ==================


_OVERDUE_FIXTURE = (
    "tasks:\n"
    "  - id: t-overdue-yesterday\n"
    "    title: 'Late task'\n"
    "    status: pending\n"
    "    agent: proj-late\n"
    "    deadline: '2000-01-01'\n"
    "  - id: t-future\n"
    "    title: 'Future task'\n"
    "    status: pending\n"
    "    agent: proj-late\n"
    "    deadline: '2099-01-01'\n"
    "  - id: t-done-past\n"
    "    title: 'Past done task'\n"
    "    status: done\n"
    "    agent: proj-late\n"
    "    deadline: '2000-01-01'\n"
)


@pytest.fixture
def overdue_store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(_OVERDUE_FIXTURE, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def test_overdue_count_counts_pending_past_deadline(overdue_store):
    # Arrange
    # proj-late has 1 overdue pending + 1 future + 1 done-past.
    # The done one must NOT count (terminal state); the future one must
    # NOT count (deadline ahead).
    # Act
    fleet = _fleet_by_name(_graph(overdue_store))
    # Assert
    assert fleet["proj-late"]["overdue_count"] == 1


# === Waiting-on-operator queue (operator P1 ============================ #
# todo-operator-blocking-queue-view): the per-agent count + id list of
# cards stuck on the operator. SSOT predicate is
# `_match(..., blocking_me=True)` == status==blocked AND
# blocker==operator-decision. These tests cover the three discriminating
# cases: a matching card IS counted/listed, a blocked card with a DIFFERENT
# blocker is NOT, and a non-blocked card is NOT. (An in_progress card with
# blocker=operator-decision is IMPOSSIBLE — the store validator forbids a
# blocker on a non-blocked status — which is itself why the count can never
# include non-blocked cards.)

_BLOCKING_FIXTURE = (
    "tasks:\n"
    # MATCH: blocked + operator-decision → in the queue.
    "  - id: bq-stuck-on-op\n"
    "    title: 'stuck on operator decision'\n"
    "    status: blocked\n"
    "    blocker: operator-decision\n"
    "    agent: proj-blockq\n"
    # NOT a match: blocked but a DIFFERENT blocker (dependency).
    "  - id: bq-blocked-on-dep\n"
    "    title: 'blocked on a dependency, not the operator'\n"
    "    status: blocked\n"
    "    blocker: dependency\n"
    "    agent: proj-blockq\n"
    # NOT a match: a non-blocked (pending) card — the canonical predicate
    # requires status==blocked.
    "  - id: bq-pending\n"
    "    title: 'pending, not waiting on the operator'\n"
    "    status: pending\n"
    "    agent: proj-blockq\n"
)


@pytest.fixture
def blocking_store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(_BLOCKING_FIXTURE, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def test_blocking_operator_count_uses_canonical_predicate(blocking_store):
    # Arrange
    # proj-blockq has exactly ONE card matching the BLOCKING-YOU
    # predicate (blocked + operator-decision). The blocked-on-dependency
    # and the pending cards must NOT count.
    # Act
    fleet = _fleet_by_name(_graph(blocking_store))
    # Assert
    assert fleet["proj-blockq"]["blocking_operator_count"] == 1


def test_blocking_operator_ids_lists_only_the_matching_card(blocking_store):
    # Arrange
    # same fixture; the id list must contain only the
    # blocked+operator-decision card.
    # Act
    fleet = _fleet_by_name(_graph(blocking_store))
    # Assert
    assert fleet["proj-blockq"]["blocking_operator_ids"] == ["bq-stuck-on-op"]


def test_blocked_on_other_blocker_not_in_operator_queue(blocking_store):
    # Arrange
    # a blocked card whose blocker is NOT operator-decision.
    # Act
    fleet = _fleet_by_name(_graph(blocking_store))
    # Assert
    assert "bq-blocked-on-dep" not in fleet["proj-blockq"]["blocking_operator_ids"]


def test_non_blocked_card_not_in_queue(blocking_store):
    # Arrange
    # a non-blocked (pending) card; the canonical predicate
    # requires status==blocked, so it must NOT surface.
    # Act
    fleet = _fleet_by_name(_graph(blocking_store))
    # Assert
    assert "bq-pending" not in fleet["proj-blockq"]["blocking_operator_ids"]


def test_blocking_operator_ids_present_on_every_fleet_row(store):
    # The field must always be present (possibly empty) so the FE never
    # null-checks, mirroring overdue_count's contract.
    # Arrange
    # Act
    fleet = _fleet_by_name(_graph(store))
    # Assert
    assert all("blocking_operator_ids" in row for row in fleet.values())
