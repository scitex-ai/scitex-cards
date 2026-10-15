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


def test_create_doc_names_the_deferred_default():
    doc = crud.handle_create.__doc__ or ""
    assert "``deferred``" in doc
    assert "defaults\n    ``pending``" not in doc
    assert "``status`` defaults" in doc


def test_create_doc_records_the_pending_abolition():
    doc = crud.handle_create.__doc__ or ""
    assert "abolished" in doc
    assert "2026-07-10" in doc


def test_create_code_defaults_missing_status_to_deferred():
    src = inspect.getsource(crud.handle_create)
    assert 'payload.get("status") or "deferred"' in src


def test_check_status_rejects_abolished_pending():
    resp = crud._check_status({"status": "pending"})
    assert resp is not None
    assert resp.status_code == 400


def test_check_status_accepts_deferred_and_absent():
    assert crud._check_status({}) is None
    assert crud._check_status({"status": "deferred"}) is None


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES
