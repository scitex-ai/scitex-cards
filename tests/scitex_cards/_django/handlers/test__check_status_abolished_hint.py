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


def test_abolished_pending_names_the_abolition_and_the_disposition():
    # Arrange — the abolished value through the shared gate.
    # Act
    resp = crud._check_status({"status": "pending"})
    # Assert — 400 carrying the disposition hint, not the generic text.
    assert resp is not None
    assert resp.status_code == 400
    text = _error(resp)
    assert "abolished" in text
    assert "must carry a decision" in text
    assert "in_progress" in text and "deferred" in text


def test_unknown_status_keeps_the_generic_message():
    # Arrange — a value no build knows (not abolished, just unknown).
    # Act
    resp = crud._check_status({"status": "archived"})
    # Assert — still a 400, still the generic enum text, no abolition words.
    assert resp is not None
    assert resp.status_code == 400
    text = _error(resp)
    assert "invalid status 'archived'" in text
    assert "abolished" not in text


def test_live_and_absent_statuses_pass_the_gate():
    # Arrange / Act / Assert — the gate stays silent on good input.
    assert crud._check_status({"status": "deferred"}) is None
    assert crud._check_status({}) is None


def test_create_surfaces_the_hint_before_the_store():
    # Arrange — a create carrying the abolished status, bogus store path.
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
    # Assert — 400 with the hint; any response short of the store layer
    # proves the decision was made without touching the store.
    assert resp.status_code == 400
    assert "abolished" in _error(resp)


def test_update_surfaces_the_hint_before_the_store():
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
    assert "abolished" in _error(resp)


def test_reopen_surfaces_the_hint_before_the_store():
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
    assert "abolished" in _error(resp)


def test_restore_surfaces_the_hint_before_the_store():
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
    assert "abolished" in _error(resp)


def test_gate_consults_the_abolished_map_before_the_generic_text():
    # Arrange
    src = inspect.getsource(crud._check_status)
    # Act / Assert — the abolished branch must precede the generic message
    # so a later edit cannot silently revert to generic-only.
    assert "ABOLISHED_STATUSES" in src
    assert src.index("ABOLISHED_STATUSES") < src.index("choose one of")


def test_detector_is_not_vacuous():
    # Arrange — the abolished branch must be capable of firing: feed it the
    # abolished value and an unknown value and require different texts.
    # Act
    abolished_text = _error(crud._check_status({"status": "pending"}))
    unknown_text = _error(crud._check_status({"status": "archived"}))
    # Assert
    assert abolished_text != unknown_text
