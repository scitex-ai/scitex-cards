#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/timeline`` Django endpoint — fleet TIME-RASTER surface.

Operator-direct ask (TG, relayed by lead a2a ``d0f7a0e3``, 2026-06-14):
the floor "Time View" handler covers:

  - 200 + expected JSON shape on GET.
  - Events filtered to the sliding window: tasks OUTSIDE the window are
    NOT included in ``events``.
  - ``lane_by=group`` groups by group; ``lane_by=agent`` (default)
    groups by agent. Tasks without a value land in ``"(ungrouped)"``.
  - Edges only included when BOTH endpoints are in the events set.
  - 405 on POST.

Django RequestFactory; no mocks (STX-NM / PA-306). AAA pattern, one
assertion per test.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util as _ilu
import json
import os
import sys
from pathlib import Path

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.timeline import (
    _build_payload,
    timeline_view,
)
from scitex_cards._django.services import _reset_cache
from scitex_cards._store import add_task, complete_task

# ``seed_db_from_doc`` lives in the SHARED tests/scitex_cards/conftest.py; the
# _django/ sibling conftest shadows the bare ``conftest`` module name, so load
# the shared one deterministically by path (same idiom as the other _django
# handler tests under the SQLite cutover).
_shared_conftest = Path(__file__).resolve().parents[2] / "conftest.py"
_spec = _ilu.spec_from_file_location("_scitex_cards_shared_conftest", _shared_conftest)
_mod = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = _mod  # register BEFORE exec (py3.12 dataclass lookup)
_spec.loader.exec_module(_mod)
seed_db_from_doc = _mod.seed_db_from_doc


# === fixtures ==============================================================


@pytest.fixture()
def store_with_timeline_tasks() -> str:
    """Seed the canonical DB with one in-window + one out-of-window task plus
    a depends_on edge; return the PINNED store-identity path the view resolves.

    The store is SQLite now: ``add_task`` writes the canonical DB and the view's
    ``get_board`` reads it back. We pass the PINNED store path (never a tmp_path
    YAML — a write stamped with a tmp path fails the next read's ownership
    check). The fresh tasks use ``add_task`` which stamps ``created_at`` to NOW
    via the standard writer — that lands inside any reasonable recent sliding
    window. The "stale" row is added with an explicit ancient ``created_at`` so
    the window filter has something to drop.
    """
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(
        store=store,
        id="t-live",
        title="Live now",
        agent="agent-a",
        group="paper",
    )
    add_task(
        store=store,
        id="t-dep",
        title="Depends on live",
        agent="agent-b",
        group="paper",
        depends_on=["t-live"],
    )
    # Stale row — created a year ago so the 24h window filter removes it.
    add_task(
        store=store,
        id="t-stale",
        title="Old row",
        agent="agent-a",
        group="paper",
        # add_task's **extras pathway accepts arbitrary keys — the writer
        # validator gates closed enums; created_at is free-form ISO.
        created_at="2020-01-01T00:00:00+00:00",
    )
    _reset_cache()
    return store


@pytest.fixture()
def store_ungrouped() -> str:
    """One OWNER-LESS task (no ``agent``/``assignee``/``group``) — should land
    in ``"(ungrouped)"`` regardless of ``lane_by``.

    Seeded DIRECTLY into the canonical DB on purpose: ``add_task`` now REQUIRES
    an owner (assignee/agent are mandatory — fail-loud, no silent fallback), so
    an owner-less card can only exist as a LEGACY hand-written row. The board /
    timeline must still bucket such a legacy row under ``"(ungrouped)"``
    (``card_owner`` returns ``None``). A NOW ``created_at`` keeps the row inside
    the timeline window."""
    # Stamp a NOW ``created_at`` so the row lands inside the timeline window
    # (the standard add_task auto-stamp the other fixtures rely on); we seed the
    # doc directly only to keep the row OWNER-LESS, which add_task now forbids.
    now = _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()
    doc = {
        "tasks": [
            {
                "id": "t-naked",
                "title": "No lane",
                "status": "pending",
                "created_at": now,
                "last_activity": now,
            }
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


# === GET /timeline =========================================================


def test_timeline_view_returns_200(store_with_timeline_tasks):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    response = timeline_view(req)
    # Assert
    assert response.status_code == 200


def test_timeline_view_payload_has_expected_keys(store_with_timeline_tasks):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    assert {
        "events",
        "edges",
        "window_start",
        "window_end",
        "lane_by",
        "lanes",
        "store_path",
    } <= set(payload.keys())


def test_timeline_view_event_shape_events(store_with_timeline_tasks):
    """Each event row carries the operator-brief-mandated keys."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    e = payload["events"][0]
    assert payload["events"], "expected at least one event in the window"


def test_timeline_view_event_shape_case_2(store_with_timeline_tasks):
    """Each event row carries the operator-brief-mandated keys."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    e = payload["events"][0]
    assert {
        "id",
        "title",
        "agent",
        "group",
        "lane",
        "started_at",
        "ended_at",
        "status",
        "priority",
        "kind",
    } <= set(e.keys())


def test_timeline_view_filters_out_of_window(store_with_timeline_tasks):
    """A task whose ALL three timestamps fall OUTSIDE the window must NOT
    appear in ``events`` — fail-loud filtering, not a soft skip."""
    # Arrange — default window is 24h; the seeded t-stale row was
    # created in 2020-01-01.
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    event_ids = {e["id"] for e in payload["events"]}
    # Assert
    assert "t-stale" not in event_ids


def test_timeline_view_includes_in_window(store_with_timeline_tasks):
    """The freshly-added in-window task IS in events."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    assert "t-live" in {e["id"] for e in payload["events"]}


def test_timeline_view_lane_by_agent_default(store_with_timeline_tasks):
    """Default ``lane_by=agent`` projects the agent field into the lane."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    by_id = {e["id"]: e for e in payload["events"]}
    assert by_id["t-live"]["lane"] == "agent-a"


def test_timeline_view_lane_by_group(store_with_timeline_tasks):
    """``lane_by=group`` projects the group field into the lane."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline", {"lane_by": "group"})
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    by_id = {e["id"]: e for e in payload["events"]}
    assert by_id["t-live"]["lane"] == "paper"


def test_build_payload_lane_by_task_uses_title():
    """``lane_by=task`` gives ONE lane per task, labelled by its title —
    the basis of the per-task "simple" view. Pure ``_build_payload`` unit
    (no Django / env) so it needs no env."""
    # Arrange
    now = _dt.datetime(2026, 6, 17, 12, 0, tzinfo=_dt.timezone.utc)
    tasks = [
        {
            "id": "t1",
            "title": "My Task",
            "created_at": "2026-06-17T11:30:00+00:00",
        }
    ]
    # Act
    payload = _build_payload(tasks, window_hours=24.0, lane_by="task", now=now)
    # Assert
    assert payload["events"][0]["lane"] == "My Task"


def test_build_payload_lane_by_project_uses_project():
    """``lane_by=project`` projects the task's ``project`` into the lane
    (operator by-project view). Pure unit — no env."""
    # Arrange
    now = _dt.datetime(2026, 6, 17, 12, 0, tzinfo=_dt.timezone.utc)
    tasks = [
        {
            "id": "t1",
            "title": "T1",
            "project": "proj-x",
            "created_at": "2026-06-17T11:30:00+00:00",
        }
    ]
    # Act
    payload = _build_payload(tasks, window_hours=24.0, lane_by="project", now=now)
    # Assert
    assert payload["events"][0]["lane"] == "proj-x"


def test_timeline_view_ungrouped_lane(store_ungrouped):
    """A task without an agent or group lands in the ``"(ungrouped)"``
    lane — operator-brief floor for tasks with no lane value."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    assert payload["events"][0]["lane"] == "(ungrouped)"


def test_timeline_view_invalid_lane_by_falls_back_to_agent(
    store_with_timeline_tasks,
):
    """An unknown ``lane_by`` value silently falls back to ``agent`` so
    the FE never breaks on a stale query token."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline", {"lane_by": "garbage"})
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    assert payload["lane_by"] == "agent"


def test_timeline_view_edges_only_when_both_endpoints_visible(
    store_with_timeline_tasks,
):
    """An edge is included iff BOTH endpoints land in the window-filtered
    event set. The seeded fixture has t-dep depends_on t-live (both
    in-window) so the edge fires."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    edges = payload["edges"]
    # Assert
    assert {
        "source": "t-live",
        "target": "t-dep",
        "kind": "depends_on",
    } in edges


def test_timeline_view_edge_dropped_when_endpoint_out_of_window():
    """If one endpoint of an edge is OUTSIDE the window, the edge is
    dropped from the payload — keeps the wire payload bounded and
    matches the brief's contract."""
    # Arrange — t-old is from 2020; t-new is fresh and depends_on t-old.
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(
        store=store,
        id="t-old",
        title="Older",
        agent="a",
        created_at="2020-01-01T00:00:00+00:00",
    )
    add_task(
        store=store,
        id="t-new",
        title="Newer",
        agent="a",
        depends_on=["t-old"],
    )
    _reset_cache()
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert — no edge should land in the payload (t-old is filtered out).
    assert payload["edges"] == []


def test_timeline_view_window_bounds_iso(store_with_timeline_tasks):
    """``window_start`` and ``window_end`` are ISO-8601 strings; end - start
    matches the requested window. Default = 24h."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    start = _dt.datetime.fromisoformat(payload["window_start"])
    end = _dt.datetime.fromisoformat(payload["window_end"])
    # Assert — 24h ± 1s.
    assert abs((end - start).total_seconds() - 86400.0) < 1.0


def test_timeline_view_custom_window_hours(store_with_timeline_tasks):
    """A ``?window_hours=1`` query yields a 1h window."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline", {"window_hours": "1"})
    # Act
    payload = json.loads(timeline_view(req).content)
    start = _dt.datetime.fromisoformat(payload["window_start"])
    end = _dt.datetime.fromisoformat(payload["window_end"])
    # Assert — 1h ± 1s.
    assert abs((end - start).total_seconds() - 3600.0) < 1.0


def test_timeline_view_lanes_sorted(store_with_timeline_tasks):
    """The ``lanes`` list is sorted — gives the FE a deterministic axis
    order so re-renders don't shuffle rows."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    assert payload["lanes"] == sorted(payload["lanes"])


def test_timeline_view_method_post_returns_405(store_with_timeline_tasks):
    # Arrange
    rf = RequestFactory()
    req = rf.post("/timeline")
    # Act
    response = timeline_view(req)
    # Assert
    assert response.status_code == 405


def test_timeline_view_completed_task_in_window_renders():
    """A task that COMPLETED inside the window must appear (the
    ``_log_meta.completed_at`` path is one of the three window-membership
    tests). The bar fades in the UI, but the event is still in events."""
    # Arrange — add a row created long ago, then mark it done now so
    # _log_meta.completed_at lands inside the window. The created_at
    # being stale would otherwise drop it; completed_at saves it.
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    add_task(
        store=store,
        id="t-done",
        title="Just completed",
        agent="a",
        created_at="2020-01-01T00:00:00+00:00",
    )
    complete_task(store=store, task_id="t-done")
    _reset_cache()
    rf = RequestFactory()
    req = rf.get("/timeline")
    # Act
    payload = json.loads(timeline_view(req).content)
    # Assert
    by_id = {e["id"]: e for e in payload["events"]}
    assert "t-done" in by_id and by_id["t-done"]["ended_at"] is not None


# === pure _build_payload unit ==============================================


def test_build_payload_dedupes_edges():
    """If both endpoints of a depends_on edge store the same relation
    (legacy double-write), ``_build_payload`` de-dups to one wire edge."""
    # Arrange — two tasks; "a" depends_on "b"; "b" blocks "a" (effectively
    # the same arrow in two orientations). We only check that a duplicate
    # depends_on does not appear twice.
    now = _dt.datetime(2026, 6, 14, 12, 0, tzinfo=_dt.timezone.utc)
    tasks = [
        {
            "id": "a",
            "title": "A",
            "created_at": "2026-06-14T11:30:00+00:00",
            "depends_on": ["b", "b"],
        },
        {
            "id": "b",
            "title": "B",
            "created_at": "2026-06-14T11:30:00+00:00",
        },
    ]
    # Act
    payload = _build_payload(tasks, window_hours=24.0, lane_by="agent", now=now)
    # Assert
    edges = [e for e in payload["edges"] if e["kind"] == "depends_on"]
    assert len(edges) == 1
