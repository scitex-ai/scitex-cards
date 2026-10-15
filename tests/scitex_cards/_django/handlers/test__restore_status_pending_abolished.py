#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the POST /restore abolished-status hole.

``handle_restore`` re-inserted the caller-supplied ``task`` mapping with NO
status check: the dict round-trips from the client, and the save path only
WARNS on bad values (operator ruling 2026-07-10, cf. the
``crud._check_status`` docstring) — so the board Delete→Undo affordance
could mint a ``pending`` row the abolition forbids from a crafted mapping.
Same flaw class as the reopen hole
(``test__reopen_prior_status_pending_abolished.py``): siblings were already
strict (create/update 400 via ``crud._check_status``, resolve only ever
writes ``done``, ``_store.reopen_task`` hardcodes ``blocked``). The handler
now validates through the same ``_check_status`` contract BEFORE the store
lock.

These pins drive ``handle_restore`` directly with a bogus store path: the
400 fires before any store access, so no store, no PostgreSQL — they run
in containers where the store-backed suite refuses via the fail-LOUD
guard. The full round-trip lives in
``test__crud_delegates_to_locked_verbs.py`` (store fixture).

STRICT scitex doctrine: no unittest.mock, no monkeypatch (PA-306) — the
live-write side is pinned via source order + the empty-status behavioral
proof below, never a stubbed store.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import undo  # noqa: E402


def _post_restore(task: dict, refs: list | None = None):
    # Arrange helper, not a test: a RequestFactory POST against a store
    # path that does not exist, so any response short of the store layer
    # proves the decision was made without touching the store.
    request = RequestFactory().post(
        "/restore?store=/nonexistent/store-identity",
        data=json.dumps({"task": task, "refs": refs or []}),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    return undo.handle_restore(request, board)


def test_restore_rejects_abolished_status_with_400():
    # Arrange — a crafted mapping carrying the abolished status.
    task = {"id": "was-deleted", "title": "Deleted once", "status": "pending"}
    # Act
    resp = _post_restore(task)
    # Assert
    assert resp.status_code == 400


def test_rejected_restore_names_the_abolished_value():
    # Arrange
    task = {"id": "was-deleted", "title": "Deleted once", "status": "pending"}
    # Act
    resp = _post_restore(task)
    # Assert
    assert "pending" in json.loads(resp.content.decode("utf-8"))["error"]


def test_live_status_passes_validation_to_the_store_layer():
    # Arrange — deferred must NOT 400: with a bogus store path the request
    # proceeds past validation and dies loudly at the store layer instead.
    task = {"id": "was-deleted", "title": "Deleted once", "status": "deferred"}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_restore(task)


def test_restore_source_validates_before_the_store_lock():
    # Arrange
    src = inspect.getsource(undo.handle_restore)
    # Act
    check_at = src.index('_check_status({"status": task.get("status")})')
    # Assert
    assert check_at < src.index("_store_lock")


def test_restore_doc_records_the_status_400():
    # Arrange
    doc = undo.handle_restore.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert "task.status" in flat and "400" in flat and "pending" in flat


def test_detector_is_not_vacuous():
    # Arrange — the ordering check in the source pin must be capable of
    # finding the shape it looks for.
    fixture = 'err = _check_status({"status": task.get("status")})\n_store_lock(path)'
    # Act
    check_at = fixture.index('_check_status({"status": task.get("status")})')
    # Assert
    assert check_at < fixture.index("_store_lock")


def test_restore_without_status_reaches_the_store_layer():
    # Arrange — a crafted mapping with NO status at all. The handler
    # defaults it to ``deferred`` (create-twin default) BEFORE the gate,
    # so the request must clear validation and die loudly at the store
    # layer on the bogus path — never a 400, never a silent 200. (This
    # locks the no-400 half; the default itself is pinned by the
    # source-order test below — ``None`` passes the gate either way, so
    # only the source pin distinguishes default-present from
    # default-absent for the absent case.)
    task = {"id": "no-status", "title": "Crafted without status"}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_restore(task)


def test_restore_with_empty_status_reaches_the_store_layer():
    # Arrange — "" is as status-less as absent, so the same default
    # applies. The gate refuses "" (only None passes as absent), so this
    # request reaching the store layer proves the default fired BEFORE
    # the gate: a regression that deletes the default but keeps the
    # gate 400s "" here instead of raising. (The whole-fix-stashed
    # negative control is covered by the 400 + source-order pins, which
    # fail without the fix.)
    task = {"id": "empty-status", "title": "Crafted with blank", "status": ""}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_restore(task)


def test_restore_status_default_precedes_validation():
    # Arrange — the default must land BEFORE the _check_status gate so a
    # defaulted row is never 400'd and a present-but-bad value still is.
    src = inspect.getsource(undo.handle_restore)
    # Act
    default_at = src.index('task["status"] = "deferred"')
    # Assert
    assert default_at < src.index('_check_status({"status": task.get("status")})')


def test_restore_doc_records_the_deferred_default():
    # Arrange
    doc = undo.handle_restore.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert "NO status" in doc and "deferred" in flat
