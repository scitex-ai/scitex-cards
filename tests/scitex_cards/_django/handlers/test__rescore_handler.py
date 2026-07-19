#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The /rescore handler: a matrix drag re-scores a card via the locked verb.

ADR-0011 §8 — dragging a card in the urgency×importance matrix sets its two
axes and the rank engine recomputes the whole order. This handler is a THIN
delegation to the locked ``rescore_task`` store verb (never a handler-flock
write, which would be atomic but emit no ``rank_changed`` event — the decisive
finding on card scitex-cards-gui-matrix-view-20260717).

Pinned here the same three ways as the sibling crud/edge handlers:

1. LOST-UPDATE survival — a stale board + a concurrent ``add_task`` must both
   survive the rescore (the verb holds the store flock across its own fresh
   read-modify-write).
2. DELEGATION — the two axes + the operator actor reach ``rescore_task``
   verbatim; the handler invents no rank (computed, never asserted).
3. CONTRACT — axes validated (bad/out-of-range/non-int -> 400), unknown id ->
   404, non-POST -> 405, response shape ``{task, rank, of, store_path}``, and
   the audit trail the occupancy PR replays.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import rescore  # noqa: E402
from scitex_cards._django.services import _reset_cache, get_board  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal}\n"
    "  - {id: build, title: Build It, status: in_progress}\n"
    "  - {id: done-card, title: Done Card, status: done}\n"
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Hermetic: no per-project lane union from the real ~/proj tree.
    monkeypatch.setenv("SCITEX_TODO_LANE_GLOBS", "")
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _post(store_path, body):
    request = RequestFactory().post(
        f"/rescore?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, "rescore")


def _request(body):
    return RequestFactory().post(
        "/rescore", data=json.dumps(body), content_type="application/json"
    )


def _load(store_path):
    with open(store_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return {t["id"]: t for t in data["tasks"]}


def _stale_board(store_path):
    _reset_cache()
    board = get_board(store_path)
    _reset_cache()
    return board


def _land_concurrent_write(store_path):
    from scitex_cards._store import add_task

    add_task(
        store_path,
        id="concurrent",
        title="Concurrent Card",
        status="deferred",
        assignee="carol",
        created_by="carol",
    )


def _rescore_over_a_concurrent_write(store_path):
    """Rescore ``build`` through a board snapshot taken BEFORE a concurrent add.

    A handler-cache save would erase the concurrently-added card; the locked
    verb must keep both writes.
    """
    board = _stale_board(store_path)
    _land_concurrent_write(store_path)
    return rescore.handle_rescore(
        _request({"id": "build", "urgency": 4, "importance": 5}), board
    )


def _install_rescore_spy(monkeypatch):
    """Record every ``rescore_task`` call the handler makes; return the log."""
    calls = []

    def spy(store_arg, task_id, *, urgency, importance, by=None):
        calls.append(
            {
                "store": str(store_arg),
                "task_id": task_id,
                "urgency": urgency,
                "importance": importance,
                "by": by,
            }
        )
        return {"task": {"id": task_id}, "rank": 1, "of": 1}

    monkeypatch.setattr("scitex_cards._store.rescore_task", spy)
    return calls


def _audit_entry(store_path, task_id="build"):
    return _load(store_path)[task_id]["comments"][-1]


# ── 1. lost-update survival (the #468 property, for the new handler) ───────


def test_rescore_over_a_concurrent_write_answers_200(store):
    # Arrange
    # The handler holds a stale board; a concurrent write lands behind it.
    store_path = store
    # Act
    response = _rescore_over_a_concurrent_write(store_path)
    # Assert
    assert response.status_code == 200


def test_rescore_over_a_concurrent_write_persists_the_urgency(store):
    # Arrange
    store_path = store
    # Act
    _rescore_over_a_concurrent_write(store_path)
    # Assert
    assert _load(store_path)["build"]["urgency"] == 4


def test_rescore_over_a_concurrent_write_persists_the_importance(store):
    # Arrange
    store_path = store
    # Act
    _rescore_over_a_concurrent_write(store_path)
    # Assert
    assert _load(store_path)["build"]["importance"] == 5


def test_rescore_over_a_concurrent_write_keeps_the_other_write(store):
    # Arrange
    store_path = store
    # Act
    _rescore_over_a_concurrent_write(store_path)
    # Assert — a handler-cache save would have erased `concurrent`.
    assert "concurrent" in _load(store_path)


# ── 2. delegation — axes + actor forwarded verbatim, no client-set rank ───


def test_rescore_delegating_to_the_verb_answers_200(store, monkeypatch):
    # Arrange
    _install_rescore_spy(monkeypatch)
    # Act
    response = _post(store, {"id": "build", "urgency": 2, "importance": 4})
    # Assert
    assert response.status_code == 200


def test_rescore_forwards_axes_and_actor_to_the_verb(store, monkeypatch):
    # Arrange
    calls = _install_rescore_spy(monkeypatch)
    # Act
    _post(store, {"id": "build", "urgency": 2, "importance": 4})
    # Assert — the two axes and the operator actor reach the verb; the handler
    # never computes or forwards a rank.
    assert calls == [
        {
            "store": store,
            "task_id": "build",
            "urgency": 2,
            "importance": 4,
            "by": "operator",
        }
    ]


# ── 3a. end-to-end through the real verb ──────────────────────────────────


def test_rescore_response_carries_the_documented_key_set(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    payload = json.loads(_post(store, body).content)
    # Assert
    assert set(payload) == {"task", "rank", "of", "store_path"}


def test_rescore_response_reports_the_engine_computed_rank(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    payload = json.loads(_post(store, body).content)
    # Assert
    assert payload["rank"] == 1 and payload["of"] == 1


def test_rescore_persists_both_axes_on_the_dragged_card(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    _post(store, body)
    # Assert
    build = _load(store)["build"]
    assert build["urgency"] == 4 and build["importance"] == 5


def test_rescore_persists_the_engine_rank_on_the_card(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    _post(store, body)
    # Assert
    assert _load(store)["build"]["rank"] == 1


def test_rescore_audit_entry_is_authored_by_the_operator(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    _post(store, body)
    # Assert — the audit entry the occupancy PR (PR 3) replays.
    assert _audit_entry(store)["author"] == "operator"


def test_rescore_audit_entry_declares_the_rescore_kind(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    _post(store, body)
    # Assert
    assert _audit_entry(store)["kind"] == "rescore"


def test_rescore_audit_entry_records_the_urgency_transition(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    _post(store, body)
    # Assert — the old->new machine payload, not prose.
    assert _audit_entry(store)["rescore"]["urgency"] == [None, 4]


def test_rescore_audit_entry_records_the_importance_transition(store):
    # Arrange
    body = {"id": "build", "urgency": 4, "importance": 5}
    # Act
    _post(store, body)
    # Assert
    assert _audit_entry(store)["rescore"]["importance"] == [None, 5]


def test_rescore_terminal_card_reports_no_rank(store):
    # Arrange — a done card is not in the queue.
    body = {"id": "done-card", "urgency": 5, "importance": 5}
    # Act
    payload = json.loads(_post(store, body).content)
    # Assert
    assert payload["rank"] is None


def test_rescore_terminal_card_still_holds_both_axes(store):
    # Arrange
    body = {"id": "done-card", "urgency": 5, "importance": 5}
    # Act
    _post(store, body)
    # Assert
    done = _load(store)["done-card"]
    assert done["urgency"] == 5 and done["importance"] == 5


def test_rescore_terminal_card_stores_no_rank_key(store):
    # Arrange
    body = {"id": "done-card", "urgency": 5, "importance": 5}
    # Act
    _post(store, body)
    # Assert — finished work holds axes but no rank.
    assert "rank" not in _load(store)["done-card"]


# ── 3b. contract / error paths ────────────────────────────────────────────


def test_rescore_axis_out_of_range_is_400(store):
    # Arrange
    body = {"id": "build", "urgency": 6, "importance": 3}
    # Act
    response = _post(store, body)
    # Assert
    assert response.status_code == 400


def test_rescore_axis_out_of_range_leaves_the_store_untouched(store):
    # Arrange
    body = {"id": "build", "urgency": 6, "importance": 3}
    # Act
    _post(store, body)
    # Assert
    assert "urgency" not in _load(store)["build"]


def test_rescore_non_int_axis_is_400(store):
    # Arrange — a "4"-string never reaches the verb.
    body = {"id": "build", "urgency": "4", "importance": 3}
    # Act
    response = _post(store, body)
    # Assert — the handler shape-guard rejects it.
    assert response.status_code == 400


def test_rescore_bool_axis_is_400(store):
    # Arrange — bool IS an int in Python.
    body = {"id": "build", "urgency": True, "importance": 3}
    # Act
    response = _post(store, body)
    # Assert — the shape-guard rejects it explicitly.
    assert response.status_code == 400


def test_rescore_missing_axis_is_400(store):
    # Arrange
    body = {"id": "build", "urgency": 4}
    # Act
    response = _post(store, body)
    # Assert
    assert response.status_code == 400


def test_rescore_unknown_id_is_404(store):
    # Arrange
    body = {"id": "ghost", "urgency": 4, "importance": 5}
    # Act
    response = _post(store, body)
    # Assert
    assert response.status_code == 404


def test_rescore_requires_a_post_request(store):
    # Arrange
    request = RequestFactory().get(f"/rescore?store={store}")
    # Act
    response = views.api_dispatch(request, "rescore")
    # Assert
    assert response.status_code == 405
