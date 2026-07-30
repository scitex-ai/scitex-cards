#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view tests for ``GET /fleet/timing``.

No mocks (STX-NM/PA-306). Drives the view via Django's RequestFactory
against a real on-disk task store pinned through ``SCITEX_TODO_TASKS_YAML_SHARED``,
following the same pattern as ``test__timeline_view.py``.

Contract pinned here:

  1. ``GET /fleet/timing`` returns 200 with the load-bearing JSON keys
     (``window_days``, ``per_agent``, ``per_project``, ``per_group``,
     ``n_tasks_in_window``, ``n_tasks_missing_timestamps``).
  2. The default ``window_days`` is 30 (matches the operator brief).
  3. ``?window_days=7`` is parsed and surfaced in the envelope.
  4. ``POST`` returns 405.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers.fleet import (  # noqa: E402
    fleet_timing_view,
)
from scitex_cards._store import add_task  # noqa: E402

# === fixtures ==============================================================


def _now_minus(minutes: float) -> str:
    """ISO-8601 ``minutes`` before NOW (UTC)."""
    ts = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(minutes=minutes)
    return ts.isoformat()


@pytest.fixture()
def store_with_done_task() -> str:
    """Seed the canonical DB with one done task carrying a full ``_log_meta``
    set so the timing compute has something to aggregate. The store is the
    pinned SQLite DB; ``add_task`` writes there and the view's
    ``resolve_tasks_path(None)`` resolves the SAME pinned identity, so the
    seeded card round-trips into the payload. Passing the pinned STORE path
    (never a ``tmp_path`` yaml) keeps the DB provenance stamp matching what the
    read path resolves — see THE STORE-PATH RULE."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    # add_task's **extras pathway accepts arbitrary keys; the writer
    # validator gates closed enums but lets free-form fields through —
    # we use it to inject ``_log_meta`` directly so the test doesn't
    # need a separate started-stamping API.
    add_task(
        store=store,
        id="t-done",
        title="Completed task",
        agent="agent-alpha",
        project="proj-x",
        group="g1",
        status="done",
        _log_meta={
            "started_at": _now_minus(50),
            "completed_at": _now_minus(20),
            "completed_by": "agent-alpha",
        },
    )
    return store


# === GET /fleet/timing =====================================================


def test_timing_view_returns_200(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing")
    # Act
    response = fleet_timing_view(req)
    # Assert
    assert response.status_code == 200


def test_timing_view_payload_has_expected_keys(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing")
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert — the chart (Phase 5) keys off these.
    assert {
        "window_days",
        "window_start",
        "window_end",
        "per_agent",
        "per_project",
        "per_group",
        "n_tasks_in_window",
        "n_tasks_missing_timestamps",
    } <= set(payload.keys())


def test_timing_view_default_window_days_is_30(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing")
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert
    assert payload["window_days"] == 30


def test_timing_view_parses_window_days_query(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing", {"window_days": "7"})
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert
    assert payload["window_days"] == 7


def test_timing_view_aggregates_real_done_task_n_tasks_in_window(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing")
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert — the seeded task should be in window and bucketed.
    assert payload["n_tasks_in_window"] == 1


def test_timing_view_aggregates_real_done_task_per_agent_contains(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing")
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert — the seeded task should be in window and bucketed.
    assert "agent-alpha" in payload["per_agent"]


def test_timing_view_aggregates_real_done_task_n_tasks_done(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/fleet/timing")
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert — the seeded task should be in window and bucketed.
    assert payload["per_agent"]["agent-alpha"]["n_tasks_done"] == 1


def test_timing_view_rejects_post_with_405_status_code(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.post("/fleet/timing")
    # Act
    response = fleet_timing_view(req)
    # Assert
    data = json.loads(response.content)
    assert response.status_code == 405


def test_timing_view_rejects_post_with_405_data_contains(store_with_done_task):
    # Arrange
    rf = RequestFactory()
    req = rf.post("/fleet/timing")
    # Act
    response = fleet_timing_view(req)
    # Assert
    data = json.loads(response.content)
    assert "error" in data


def test_timing_view_invalid_window_days_falls_back_to_default(
    store_with_done_task,
):
    # Arrange — bogus value should not crash, the floor is "always show
    # something" so the operator never gets a blank chart over a typo.
    rf = RequestFactory()
    req = rf.get("/fleet/timing", {"window_days": "not-a-number"})
    # Act
    payload = json.loads(fleet_timing_view(req).content)
    # Assert
    assert payload["window_days"] == 30


# EOF
