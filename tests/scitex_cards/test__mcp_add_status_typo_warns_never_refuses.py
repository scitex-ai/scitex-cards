#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the MCP add_task enum doc (pending abolition).

``add_task`` in ``src/scitex_cards/_mcp_write.py`` documented its
closed-enum handling as "typos raise ``TaskValidationError`` with the bad
value and the valid set". That is false for VALUES at the STORE layer: the
writer's validator only WARNS on a bad value, never refuses, per the
operator ruling 2026-07-10 (a status value must never cost someone their
card) — the write lands and the warning is returned in the JSON payload's
``warnings`` list when non-empty. Only STRUCTURAL faults raise at save
time. The TOOL is a source and delegates to the ``add_task`` verb, which
refuses a caller-supplied ``status`` outside ``VALID_STATUSES`` before the
store is touched (pinned by the refusal tests beside the verb and
``test__tolerated_warnings_reach_the_writer.py``).
A caller reading the old prose would rely on a refusal that never comes —
the same dead-prose class as the ``_store_add.add_task`` doc fix
(``test__add_status_typo_warns_never_refuses.py``), which this MCP tool
delegates to via the backend.

These pins scan the docstring and exercise ``_validate_tasks`` directly:
no store, no PostgreSQL — they run in containers where the store-backed
suite refuses via the fail-LOUD guard.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. No store is touched.
"""

from __future__ import annotations

import warnings

import pytest

from scitex_cards._mcp_write import add_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
from scitex_cards._validate import _validate_tasks


def test_mcp_add_doc_no_longer_claims_typos_raise():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    # Assert
    assert "typos raise" not in doc


def test_mcp_add_doc_names_the_warn_never_refuse_contract():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    # Assert
    assert "only warns" in doc


def test_mcp_add_doc_names_the_warnings_return_channel():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    # Assert
    assert "warnings" in doc


def test_mcp_add_doc_cites_the_ruling_date():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    # Assert
    assert "2026-07-10" in doc


def test_mcp_add_doc_limits_raises_to_structural_faults():
    # Arrange
    doc = add_task.__doc__ or ""
    # Act
    # Assert
    assert "STRUCTURAL" in doc


def test_unknown_status_passes_validation_without_raising():
    # Arrange
    rows = [{"id": "pin-mcp-add-1", "title": "x", "status": "archived"}]
    # Act
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        outcome = _validate_tasks(rows, "<pin>")
    # Assert
    assert outcome is None


def test_unknown_status_shouts_naming_the_value():
    # Arrange
    rows = [{"id": "pin-mcp-add-2", "title": "x", "status": "archived"}]
    # Act
    # Assert
    with pytest.warns(UserWarning, match="archived"):
        _validate_tasks(rows, "<pin>")


def test_abolished_pending_shouts_the_abolition_hint():
    # Arrange
    rows = [{"id": "pin-mcp-add-3", "title": "x", "status": "pending"}]
    # Act
    # Assert
    with pytest.warns(UserWarning, match="abolished"):
        _validate_tasks(rows, "<pin>")


def test_archived_is_outside_the_live_enum():
    # Arrange
    # Act
    # Assert
    assert "archived" not in VALID_STATUSES


def test_pending_is_abolished():
    # Arrange
    # Act
    # Assert
    assert "pending" in ABOLISHED_STATUSES


def test_pending_is_outside_the_live_enum():
    # Arrange
    # Act
    # Assert
    assert "pending" not in VALID_STATUSES


def test_live_enum_is_nonvacuous():
    # Arrange
    # Act
    # Assert
    assert set(VALID_STATUSES) >= {"in_progress", "deferred", "blocked", "done", "cancelled"}


# EOF
