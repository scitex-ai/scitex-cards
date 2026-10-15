#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the POST /reopen abolished-status hole.

``handle_reopen`` restored caller-supplied ``prior_status`` with NO enum
check: ``task["status"] = new_status`` was written unchecked, and the save
path only WARNS on bad values (operator ruling 2026-07-10, cf. the
``crud._check_status`` docstring) — so the board Undo affordance could
mint a ``pending`` row the abolition forbids. Siblings were already
strict: create/update 400 via ``crud._check_status``, resolve only ever
writes ``done``. The handler now validates through the same
``_check_status`` contract BEFORE the store lock.

These pins drive ``handle_reopen`` directly with a bogus store path: the
400 fires before any store access, so no store, no PostgreSQL — they run
in containers where the store-backed suite refuses via the fail-LOUD
guard. The full round-trip lives in ``test_reopen.py`` (store fixture).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import reopen  # noqa: E402


def _post_reopen(body: dict):
    # Arrange helper, not a test: a RequestFactory POST against a store
    # path that does not exist, so any response short of the store layer
    # proves the decision was made without touching the store.
    request = RequestFactory().post(
        "/reopen?store=/nonexistent/store-identity",
        data=json.dumps(body),
        content_type="application/json",
    )
    board = SimpleNamespace(store_path="/nonexistent/store-identity")
    return reopen.handle_reopen(request, board)


def test_reopen_rejects_abolished_prior_status_with_400():
    # Arrange
    body = {"id": "was-deferred", "actor": "operator", "prior_status": "pending"}
    # Act
    resp = _post_reopen(body)
    # Assert
    assert resp.status_code == 400


def test_rejected_reopen_names_the_abolished_value():
    # Arrange
    body = {"id": "was-deferred", "actor": "operator", "prior_status": "pending"}
    # Act
    resp = _post_reopen(body)
    # Assert
    assert "pending" in json.loads(resp.content.decode("utf-8"))["error"]


def test_live_prior_status_passes_validation_to_the_store_layer():
    # Arrange — deferred must NOT 400: with a bogus store path the request
    # proceeds past validation and dies loudly at the store layer instead.
    body = {"id": "was-deferred", "actor": "operator", "prior_status": "deferred"}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_reopen(body)


def test_reopen_source_validates_before_the_store_lock():
    # Arrange
    src = inspect.getsource(reopen.handle_reopen)
    # Act
    check_at = src.index('_check_status({"status": new_status})')
    # Assert
    assert check_at < src.index("_store_lock")


def test_reopen_doc_records_the_prior_status_400():
    # Arrange
    doc = reopen.handle_reopen.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert "prior_status" in flat and "400" in flat and "pending" in flat


def test_no_pending_restore_assertion_in_reopen_tests():
    # Arrange
    test_src = Path(__file__).with_name("test_reopen.py").read_text(encoding="utf-8")
    # Act
    holders = [line for line in test_src.splitlines() if '== "pending"' in line]
    # Assert
    assert holders == []


def test_pending_prior_status_appears_only_in_rejection_tests():
    # Arrange
    test_src = Path(__file__).with_name("test_reopen.py").read_text(encoding="utf-8")
    # Act
    count = test_src.count('"prior_status": "pending"')
    # Assert
    assert count == 2


def test_reopen_test_mirror_names_the_extracted_module():
    # Arrange
    test_src = Path(__file__).with_name("test_reopen.py").read_text(encoding="utf-8")
    # Act
    header = test_src.split('"""')[1]
    # Assert
    assert "reopen.py::handle_reopen" in header


def test_detector_is_not_vacuous():
    # Arrange — the ordering check in the source pin must be capable of
    # finding the shape it looks for.
    fixture = 'err = _check_status({"status": new_status})\n_store_lock(path)'
    # Act
    check_at = fixture.index('_check_status({"status": new_status})')
    # Assert
    assert check_at < fixture.index("_store_lock")


def test_reopen_without_prior_status_passes_validation_to_the_store_layer():
    # Arrange — a body with NO prior_status must take the documented
    # "blocked" default (a live value): with a bogus store path the
    # request proceeds past validation and dies loudly at the store
    # layer instead of 400ing or minting a status-less row.
    body = {"id": "was-done", "actor": "operator"}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_reopen(body)


def test_reopen_with_empty_prior_status_passes_validation_to_the_store_layer():
    # Arrange — an EMPTY prior_status is falsy, so the same documented
    # "blocked" default applies; it must clear the gate like a live
    # value, never 400 and never a status-less write.
    body = {"id": "was-done", "actor": "operator", "prior_status": ""}
    # Act
    # Assert — loud store-layer failure, never a 400 or a silent 200.
    with pytest.raises(Exception):
        _post_reopen(body)


def test_reopen_missing_prior_status_defaults_to_blocked_before_the_gate():
    # Arrange
    src = inspect.getsource(reopen.handle_reopen)
    # Act
    default_at = src.index('payload.get("prior_status") or "blocked"')
    # Assert
    assert default_at < src.index('_check_status({"status": new_status})')


def test_reopen_doc_records_the_blocked_default():
    # Arrange
    doc = reopen.handle_reopen.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert
    assert 'prior_status' in flat and 'default ``"blocked"``' in flat
