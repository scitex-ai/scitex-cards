#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the POST /update explicit-null status gate.

``handle_update`` rejects an EXPLICIT ``{"status": null}`` with a 400
("status cannot be cleared — every card carries a decision") BEFORE the
store lock: ``status`` is exempt from the GUI clear-on-empty map, and the
``update_task`` verb deletes any field passed as None — so without this
gate a raw client could leave a status-less card that drops out of every
lane, the exact state the verb's own contract forbids. Same
must-carry-a-decision class as the pending-abolition (2026-07-10) holes
the reopen / restore / upsert / create gates close.

The contract was previously pinned ONLY by store-backed tests
(``test__write_handler_error_contracts.py``), which refuse via the
fail-LOUD guard where no writable PostgreSQL exists. These pins drive
``handle_update`` directly against a bogus store path: the 400 fires
before any store access, so no store, no PostgreSQL — they run in
containers where the store-backed suite refuses. The full round-trip
(null leaves the row intact) stays store-backed by design.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import crud  # noqa: E402


def _post_update(payload: dict):
    # Arrange helper, not a test: a RequestFactory POST against a store
    # path that does not exist, so any 4xx short of the store layer
    # proves the decision was made without touching the store.
    request = RequestFactory().post(
        "/update?store=/nonexistent/store-identity",
        data=json.dumps(payload),
        content_type="application/json",
    )
    board = SimpleNamespace(
        store_path="/nonexistent/store-identity",
        tasks=[],
    )
    return crud.handle_update(request, board)


def test_update_with_explicit_null_status_answers_400():
    # Arrange — the payload no GUI control emits but a raw client can.
    # Act
    resp = _post_update({"id": "build", "status": None})
    # Assert — loud 400 rather than a silent status-key deletion.
    assert resp.status_code == 400


def test_null_status_error_names_the_decision():
    # Arrange
    # Act
    resp = _post_update({"id": "build", "status": None})
    # Assert — the error says what the card must carry, not just "invalid".
    error = json.loads(resp.content.decode("utf-8"))["error"]
    assert "status" in error and "cannot be cleared" in error


def test_absent_status_passes_the_null_gate_case1():
    # Arrange — absent status must NOT 400: the patch simply leaves the
    # row's status untouched. With an empty stub board the request falls
    # through to the cached-union 404, proving the null gate let it pass
    # without touching the store.
    # Act
    # Arrange
    # Act
    resp = _post_update({"id": "build"})
    # Assert
    assert resp.status_code == 404


def test_absent_status_passes_the_null_gate_case2():
    # Arrange — absent status must NOT 400: the patch simply leaves the
    # row's status untouched. With an empty stub board the request falls
    # through to the cached-union 404, proving the null gate let it pass
    # without touching the store.
    # Act
    # Arrange
    # Act
    resp = _post_update({"id": "build"})
    # Assert
    assert "cannot be cleared" not in json.loads(resp.content.decode("utf-8"))["error"]


def test_null_gate_sits_after_the_status_gate_before_the_verb_case1():
    # Arrange — the null rejection must land AFTER _check_status (so an
    # abolished value still gets its disposition hint) and BEFORE any
    # store access (404 fast-path, then the update_task delegation).
    # Arrange
    src = inspect.getsource(crud.handle_update)
    # Act
    null_at = src.index('{"error": "status cannot be cleared')
    # Assert
    assert src.index("_check_status(payload)") < null_at


def test_null_gate_sits_after_the_status_gate_before_the_verb_case2():
    # Arrange — the null rejection must land AFTER _check_status (so an
    # abolished value still gets its disposition hint) and BEFORE any
    # store access (404 fast-path, then the update_task delegation).
    # Arrange
    src = inspect.getsource(crud.handle_update)
    # Act
    null_at = src.index('{"error": "status cannot be cleared')
    # Assert
    assert null_at < src.index("update_task(board.store_path")


def test_update_doc_records_the_null_rejection():
    # Arrange
    doc = crud.handle_update.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert "status: null" in flat and "400" in flat and "cannot be cleared" in flat


def test_detector_is_not_vacuous_case1():
    # Arrange — the ordering check in the source pin must be capable of
    # finding the shape it looks for.
    # Arrange
    fixture = '_check_status(payload)\n{"error": "status cannot be cleared"}\nupdate_task(board.store_path'
    # Act
    null_at = fixture.index('{"error": "status cannot be cleared')
    # Assert
    assert fixture.index("_check_status(payload)") < null_at


def test_detector_is_not_vacuous_case2():
    # Arrange — the ordering check in the source pin must be capable of
    # finding the shape it looks for.
    # Arrange
    fixture = '_check_status(payload)\n{"error": "status cannot be cleared"}\nupdate_task(board.store_path'
    # Act
    null_at = fixture.index('{"error": "status cannot be cleared')
    # Assert
    assert null_at < fixture.index("update_task(board.store_path")
