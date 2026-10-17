#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the POST /create default-status doc (pending abolition).

``handle_create`` defaulted missing ``status`` to ``deferred`` (the code:
``payload.get("status") or "deferred"``) while its docstring still claimed
``status`` defaults ``pending`` — abolished 2026-07-10. Same dead-prose
class as the runnable/reconcile/stale fixes: the code was already correct,
only the prose lied, inviting the next copy-paste to reintroduce a dead
default. The doc now names the deferred default with the abolition note.

These pins inspect the docstring / source and exercise ``_check_status``
directly: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("django")

from scitex_cards._django.handlers import crud
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def test_create_doc_names_the_deferred_default_case1():
    # Arrange
    # Act
    doc = crud.handle_create.__doc__ or ""
    # Assert
    assert "``deferred``" in doc


def test_create_doc_names_the_deferred_default_case2():
    # Arrange
    # Act
    doc = crud.handle_create.__doc__ or ""
    # Assert
    assert "defaults\n    ``pending``" not in doc


def test_create_doc_names_the_deferred_default_case3():
    # Arrange
    # Act
    doc = crud.handle_create.__doc__ or ""
    # Assert
    assert "``status`` defaults" in doc


def test_create_doc_records_the_pending_abolition_case1():
    # Arrange
    # Act
    doc = crud.handle_create.__doc__ or ""
    # Assert
    assert "abolished" in doc


def test_create_doc_records_the_pending_abolition_case2():
    # Arrange
    # Act
    doc = crud.handle_create.__doc__ or ""
    # Assert
    assert "2026-07-10" in doc


def test_create_code_defaults_missing_status_to_deferred():
    # Arrange
    # Act
    src = inspect.getsource(crud.handle_create)
    # Assert
    assert 'payload.get("status") or "deferred"' in src


def test_check_status_rejects_abolished_pending_case1():
    # Arrange
    # Act
    resp = crud._check_status({"status": "pending"})
    # Assert
    assert resp is not None


def test_check_status_rejects_abolished_pending_case2():
    # Arrange
    # Act
    resp = crud._check_status({"status": "pending"})
    # Assert
    assert resp.status_code == 400


def test_check_status_accepts_deferred_and_absent_case1():
    # Arrange
    # Act
    # Assert
    assert crud._check_status({}) is None


def test_check_status_accepts_deferred_and_absent_case2():
    # Arrange
    # Act
    # Assert
    assert crud._check_status({"status": "deferred"}) is None


def test_pending_stays_abolished_case1():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_stays_abolished_case2():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_pending_stays_abolished_case3():
    # Arrange
    # Act
    # Assert
    assert "deferred" in VALID_STATUSES
