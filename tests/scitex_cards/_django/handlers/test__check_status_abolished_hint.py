#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the board ``_check_status`` abolished-hint gap.

``crud._check_status`` is the single pre-lock status gate for the board
write verbs (create / update) and the reopen / restore handlers. It 400'd
every unknown status with the same generic "invalid status" text — so an
operator submitting the abolished ``pending`` (abolished 2026-07-10) was
told their value was invalid but never what to pick instead, while the
store write-side (``_validate.py``) and ``upsert_task`` return the
disposition hint ("a card must carry a decision. Choose: ..."). Same flaw
class as the reopen / restore / upsert holes: the board gate knew less
than the store gate.

The helper now returns the ``ABOLISHED_STATUSES`` hint for abolished
values and keeps the generic message for genuinely unknown ones. All four
verbs inherit it through the shared helper.

These pins drive ``_check_status`` directly plus one end-to-end 400 per
verb against a bogus store path: the 400 fires before any store access,
so no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import crud, reopen, undo  # noqa: E402


def _error(resp) -> str:
    # Arrange helper, not a test: decode the JsonResponse error text.
    return json.loads(resp.content.decode("utf-8"))["error"]


def test_abolished_pending_names_the_abolition_and_the_disposition_case1():
    # Arrange — the abolished value through the shared gate.
    # Act
    # Arrange
    # Act
    resp = crud._check_status({"status": "pending"})
    # Assert
    text = _error(resp)
    assert resp is not None


def test_abolished_pending_names_the_abolition_and_the_disposition_case2():
    # Arrange — the abolished value through the shared gate.
    # Act
    # Arrange
    # Act
    resp = crud._check_status({"status": "pending"})
    # Assert
    text = _error(resp)
    assert resp.status_code == 400


def test_abolished_pending_names_the_abolition_and_the_disposition_case3():
    # Arrange — the abolished value through the shared gate.
    # Act
    # Arrange
    resp = crud._check_status({"status": "pending"})
    # Act
    text = _error(resp)
    # Assert
    assert "abolished" in text


def test_abolished_pending_names_the_abolition_and_the_disposition_case4():
    # Arrange — the abolished value through the shared gate.
    # Act
    # Arrange
    resp = crud._check_status({"status": "pending"})
    # Act
    text = _error(resp)
    # Assert
    assert "must carry a decision" in text


def test_abolished_pending_names_the_abolition_and_the_disposition_case5():
    # Arrange — the abolished value through the shared gate.
    # Act
    # Arrange
    resp = crud._check_status({"status": "pending"})
    # Act
    text = _error(resp)
    # Assert
    assert "in_progress" in text and "deferred" in text


def test_unknown_status_keeps_the_generic_message_case1():
    # Arrange — a value no build knows (not abolished, just unknown).
    # Act
    # Arrange
    # Act
    resp = crud._check_status({"status": "archived"})
    # Assert
    text = _error(resp)
    assert resp is not None


def test_unknown_status_keeps_the_generic_message_case2():
    # Arrange — a value no build knows (not abolished, just unknown).
    # Act
    # Arrange
    # Act
    resp = crud._check_status({"status": "archived"})
    # Assert
    text = _error(resp)
    assert resp.status_code == 400


def test_unknown_status_keeps_the_generic_message_case3():
    # Arrange — a value no build knows (not abolished, just unknown).
    # Act
    # Arrange
    resp = crud._check_status({"status": "archived"})
    # Act
    text = _error(resp)
    # Assert
    assert "invalid status 'archived'" in text


def test_unknown_status_keeps_the_generic_message_case4():
    # Arrange — a value no build knows (not abolished, just unknown).
    # Act
    # Arrange
    resp = crud._check_status({"status": "archived"})
    # Act
    text = _error(resp)
    # Assert
    assert "abolished" not in text


def test_live_and_absent_statuses_pass_the_gate_case1():
    # Arrange / Act / Assert — the gate stays silent on good input.
    # Arrange
    # Act
    # Assert
    assert crud._check_status({"status": "deferred"}) is None


def test_live_and_absent_statuses_pass_the_gate_case2():
    # Arrange / Act / Assert — the gate stays silent on good input.
    # Arrange
    # Act
    # Assert
    assert crud._check_status({}) is None


def test_create_surfaces_the_hint_before_the_store_case1():
    # Arrange — a create carrying the abolished status, bogus store path.
    # Arrange
    request = RequestFactory().post(
        "/create?store=/nonexistent/store-identity",
        data=json.dumps(
            {"title": "Hint card", "assignee": "scitex-cards", "status": "pending"}
        ),
        content_type="application/json",
    )
    board = SimpleNamespace(tasks=[], store_path="/nonexistent/store-identity")
    # Act
    resp = crud.handle_create(request, board)
    # Assert
    assert resp.status_code == 400


def test_create_surfaces_the_hint_before_the_store_case2():
    # Arrange — a create carrying the abolished status, bogus store path.
    # Arrange
    request = RequestFactory().post(
        "/create?store=/nonexistent/store-identity",
        data=json.dumps(
            {"title": "Hint card", "assignee": "scitex-cards", "status": "pending"}
        ),
        content_type="application/json",
    )
    board = SimpleNamespace(tasks=[], store_path="/nonexistent/store-identity")
    # Act
    resp = crud.handle_create(request, board)
    # Assert
    assert "abolished" in _error(resp)


def test_update_surfaces_the_hint_before_the_store_case1():
    # Arrange
    # Arrange
    request = RequestFactory().post(
        "/update?store=/nonexistent/store-identity",
        data=json.dumps({"id": "whatever", "status": "pending"}),
        content_type="application/json",
    )
    board = SimpleNamespace(
        tasks=[{"id": "whatever"}], store_path="/nonexistent/store-identity"
    )
    # Act
    resp = crud.handle_update(request, board)
    # Assert
    assert resp.status_code == 400


def test_update_surfaces_the_hint_before_the_store_case2():
    # Arrange
    # Arrange
    request = RequestFactory().post(
        "/update?store=/nonexistent/store-identity",
        data=json.dumps({"id": "whatever", "status": "pending"}),
        content_type="application/json",
    )
    board = SimpleNamespace(
        tasks=[{"id": "whatever"}], store_path="/nonexistent/store-identity"
    )
    # Act
    resp = crud.handle_update(request, board)
    # Assert
    assert "abolished" in _error(resp)


def test_reopen_surfaces_the_hint_before_the_store_case1():
    # Arrange
    # Arrange
    request = RequestFactory().post(
        "/reopen?store=/nonexistent/store-identity",
        data=json.dumps({"id": "whatever", "prior_status": "pending"}),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    # Act
    resp = reopen.handle_reopen(request, board)
    # Assert
    assert resp.status_code == 400


def test_reopen_surfaces_the_hint_before_the_store_case2():
    # Arrange
    # Arrange
    request = RequestFactory().post(
        "/reopen?store=/nonexistent/store-identity",
        data=json.dumps({"id": "whatever", "prior_status": "pending"}),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    # Act
    resp = reopen.handle_reopen(request, board)
    # Assert
    assert "abolished" in _error(resp)


def test_restore_surfaces_the_hint_before_the_store_case1():
    # Arrange
    # Arrange
    request = RequestFactory().post(
        "/restore?store=/nonexistent/store-identity",
        data=json.dumps(
            {"task": {"id": "was-deleted", "title": "Deleted", "status": "pending"}}
        ),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    # Act
    resp = undo.handle_restore(request, board)
    # Assert
    assert resp.status_code == 400


def test_restore_surfaces_the_hint_before_the_store_case2():
    # Arrange
    # Arrange
    request = RequestFactory().post(
        "/restore?store=/nonexistent/store-identity",
        data=json.dumps(
            {"task": {"id": "was-deleted", "title": "Deleted", "status": "pending"}}
        ),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    # Act
    resp = undo.handle_restore(request, board)
    # Assert
    assert "abolished" in _error(resp)


def test_gate_consults_the_abolished_map_before_the_generic_text_case1():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(crud._check_status)
    # Assert
    assert "ABOLISHED_STATUSES" in src


def test_gate_consults_the_abolished_map_before_the_generic_text_case2():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(crud._check_status)
    # Assert
    assert src.index("ABOLISHED_STATUSES") < src.index("choose one of")


def test_detector_is_not_vacuous():
    # Arrange — the abolished branch must be capable of firing: feed it the
    # abolished value and an unknown value and require different texts.
    # Act
    abolished_text = _error(crud._check_status({"status": "pending"}))
    unknown_text = _error(crud._check_status({"status": "archived"}))
    # Assert
    assert abolished_text != unknown_text
