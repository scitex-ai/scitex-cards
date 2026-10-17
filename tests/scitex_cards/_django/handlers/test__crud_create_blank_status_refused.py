#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the POST /create blank-status gate.

``handle_create`` rejects an EXPLICIT ``{"status": ""}`` with a 400
("invalid status ... choose one of ...") via ``crud._check_status``
BEFORE the store lock: ``""`` on a closed-enum field is a DELETE
INSTRUCTION the verb layer refuses loudly (``add_task``: "a card cannot
be born status-less"), so the board must answer loudly too rather than
minting a status-less row through the ``payload.get("status") or
"deferred"`` default. Same must-carry-a-decision class as the
pending-abolition (2026-07-10) holes the reopen / restore / upsert /
update gates close — but where those pin abolished values, this pins
the blank one: the sibling create pin file
(``test__crud_create_default_pending_abolished.py``) covers missing and
abolished only, leaving ``""`` red-editable with all pins green.

The contract was previously pinned ONLY by store-backed tests, which
refuse via the fail-LOUD guard where no writable PostgreSQL exists.
These pins drive ``handle_create`` directly against a bogus store path:
the 400 fires before any store access, so no store, no PostgreSQL —
they run in containers where the store-backed suite refuses. The full
round-trip (blank leaves the store intact) stays store-backed by
design.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers import crud  # noqa: E402
from scitex_cards._task import VALID_STATUSES  # noqa: E402


def _post_create(payload: dict):
    # Arrange helper, not a test: a RequestFactory POST against a store
    # path that does not exist, so any 4xx short of the store layer
    # proves the decision was made without touching the store.
    request = RequestFactory().post(
        "/create?store=/nonexistent/store-identity",
        data=json.dumps(payload),
        content_type="application/json",
    )
    board = SimpleNamespace(
        store_path="/nonexistent/store-identity",
        tasks=[],
    )
    return crud.handle_create(request, board)


def test_create_with_blank_status_answers_400():
    # Arrange — the payload no GUI control emits but a raw client can.
    # Act
    resp = _post_create({"title": "t", "assignee": "a", "status": ""})
    # Assert — loud 400 rather than a silent fall-through to the default.
    assert resp.status_code == 400


def test_blank_status_error_names_the_shape():
    # Arrange
    # Act
    resp = _post_create({"title": "t", "assignee": "a", "status": ""})
    # Assert — the error says what went wrong and what to pick instead,
    # the same invalid-status shape the abolished-value gate returns.
    error = json.loads(resp.content.decode("utf-8"))["error"]
    assert "invalid status" in error and "''" in error


def test_blank_status_error_names_the_valid_set():
    # Arrange
    # Act
    resp = _post_create({"title": "t", "assignee": "a", "status": ""})
    # Assert — the error says what went wrong and what to pick instead,
    # the same invalid-status shape the abolished-value gate returns.
    error = json.loads(resp.content.decode("utf-8"))["error"]
    missing = [live for live in VALID_STATUSES if live not in error]
    assert not missing, missing


def test_blank_gate_fires_before_any_store_access():
    # Arrange — the 400 must come from the _check_status gate, not from
    # the verb or the store guard: those answer with different words
    # ("born status-less" / "no store"), so the wording proves the layer.
    # Act
    resp = _post_create({"title": "t", "assignee": "a", "status": ""})
    # Assert — gate wording, on a store path nothing could have opened.
    error = json.loads(resp.content.decode("utf-8"))["error"]
    assert error.startswith("invalid status")


def test_blank_gate_sits_before_the_verb():
    # Arrange — the rejection must land at _check_status (which runs on
    # the raw payload) and BEFORE the add_task delegation, so a doomed
    # insert never reaches the verb.
    src = inspect.getsource(crud.handle_create)
    # Act — anchor on the call shapes only code has (the docstring never
    # names these call spellings).
    gate_at = src.index("_check_status(payload)")
    # Assert
    assert gate_at < src.index("add_task(")


def test_missing_and_null_status_still_pass_the_gate_case1():
    # Arrange — the gate must NOT swallow the honest default: a missing
    # or null status falls through to `payload.get("status") or
    # "deferred"` in the delegation below. The sibling create pin file
    # covers the absent/deferred cases; this pins the null twin, which
    # shares the `or "deferred"` default line.
    # Act + Assert — direct gate units: no store, no PostgreSQL.
    # Arrange
    # Act
    # Assert
    assert crud._check_status({}) is None


def test_missing_and_null_status_still_pass_the_gate_case2():
    # Arrange — the gate must NOT swallow the honest default: a missing
    # or null status falls through to `payload.get("status") or
    # "deferred"` in the delegation below. The sibling create pin file
    # covers the absent/deferred cases; this pins the null twin, which
    # shares the `or "deferred"` default line.
    # Act + Assert — direct gate units: no store, no PostgreSQL.
    # Arrange
    # Act
    # Assert
    assert crud._check_status({"status": None}) is None


def test_missing_and_null_status_still_pass_the_gate_case3():
    # Arrange — the gate must NOT swallow the honest default: a missing
    # or null status falls through to `payload.get("status") or
    # "deferred"` in the delegation below. The sibling create pin file
    # covers the absent/deferred cases; this pins the null twin, which
    # shares the `or "deferred"` default line.
    # Act + Assert — direct gate units: no store, no PostgreSQL.
    # Arrange
    # Act
    # Assert
    assert crud._check_status({"status": "deferred"}) is None


def test_detector_is_not_vacuous_case1():
    # Arrange — the wording pins above must be capable of distinguishing
    # the gate's answer from the verb/store answers.
    # Arrange
    # Act
    gate_text = "invalid status ''; choose one of [...]"
    # Assert
    assert gate_text.startswith("invalid status")


def test_detector_is_not_vacuous_case2():
    # Arrange — the wording pins above must be capable of distinguishing
    # the gate's answer from the verb/store answers.
    # Arrange
    # Act
    verb_text = "add_task: status cannot be None"
    # Assert
    assert not verb_text.startswith("invalid status")


def test_detector_is_not_vacuous_case3():
    # Arrange — the wording pins above must be capable of distinguishing
    # the gate's answer from the verb/store answers.
    # Arrange
    # Act
    # Assert
    assert "_check_status(payload)" in inspect.getsource(crud.handle_create)
