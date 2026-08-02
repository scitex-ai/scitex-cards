#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the P4 deadline + scheduled validator (no mocks; SQLite store).

Lead-approved 2026-06-12. Nullable ISO-8601 fields on Task; validator
rejects empty strings, unparseable dates, and ``deadline < scheduled``.

SQLite cutover: the store is now the canonical DB, so ACCEPT tests seed the
DB from an in-memory doc (via ``_write``) and read it back through
``load_tasks``. The REJECT tests exercise the SAME read-time gate
(``_validate_tasks`` with ``strict=False``, exactly as ``load_tasks`` calls
it) directly — the DB's TEXT columns would coerce the malformed inputs
(int -> str, '' -> stored text) and change the failure message, so the
validator is called with the exact malformed input it must reject.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import TaskValidationError
from scitex_cards._model import load_tasks
from scitex_cards._validate import _validate_tasks


def _write(tmp_path, text):
    """Seed the canonical DB from a YAML-text document; return the STORE path.

    The store is SQLite; ``load_tasks`` reads the canonical DB and IGNORES the
    path (it survives only as a label). Tests still author fixtures as readable
    YAML text; parse it, seed the DB, and return the STORE identity path (NOT
    the DB path — a write/read stamps+resolves the store identity).
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(text) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


class TestDeadlineAbsent:
    def test_no_deadline_loads_clean(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n  - {id: a, title: A, status: pending}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0].get("deadline") is None


class TestDeadlineBareDate:
    def test_accepts_yyyy_mm_dd(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n  - {id: a, title: A, status: pending, deadline: '2026-06-15'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] == "2026-06-15"


class TestDeadlineDateTime:
    def test_accepts_iso_datetime_with_offset(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending, "
            "deadline: '2026-06-15T18:00:00+09:00'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] == "2026-06-15T18:00:00+09:00"


class TestScheduledFieldRoundTrip:
    def test_scheduled_alone_loads(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n  - {id: a, title: A, status: pending, scheduled: '2026-06-10'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["scheduled"] == "2026-06-10"


class TestDeadlineEmptyStringRejected:
    def test_raises_on_empty_deadline_string(self):
        # Arrange / Act / Assert — the read-time gate rejects '' as invalid.
        # (The DB's TEXT deadline column would round-trip '' unpredictably, so
        # exercise the exact validator ``load_tasks`` runs, with the exact
        # malformed input.)
        with pytest.raises(TaskValidationError, match="invalid deadline"):
            _validate_tasks(
                [{"id": "a", "title": "A", "status": "pending", "deadline": ""}],
                source="<test>",
                strict=False,
            )


class TestDeadlineNonStringRejected:
    def test_raises_on_int(self):
        # Arrange / Act / Assert — a non-string deadline is invalid. Seeding an
        # int into the TEXT column would coerce it to "20260615" (changing the
        # message to "unparseable"); assert the validator against the real int.
        with pytest.raises(TaskValidationError, match="invalid deadline"):
            _validate_tasks(
                [
                    {
                        "id": "a",
                        "title": "A",
                        "status": "pending",
                        "deadline": 20260615,
                    }
                ],
                source="<test>",
                strict=False,
            )


class TestDeadlineUnparseableRejected:
    def test_raises_on_garbage(self):
        # Arrange / Act / Assert
        with pytest.raises(TaskValidationError, match="unparseable deadline"):
            _validate_tasks(
                [
                    {
                        "id": "a",
                        "title": "A",
                        "status": "pending",
                        "deadline": "next Tuesday",
                    }
                ],
                source="<test>",
                strict=False,
            )


class TestScheduledUnparseableRejected:
    def test_raises_on_garbage(self):
        # Arrange / Act / Assert
        with pytest.raises(TaskValidationError, match="unparseable scheduled"):
            _validate_tasks(
                [
                    {
                        "id": "a",
                        "title": "A",
                        "status": "pending",
                        "scheduled": "soon",
                    }
                ],
                source="<test>",
                strict=False,
            )


class TestDeadlineBeforeScheduledRejected:
    def test_raises_when_deadline_precedes_scheduled(self):
        # Arrange / Act / Assert
        with pytest.raises(TaskValidationError, match="before scheduled"):
            _validate_tasks(
                [
                    {
                        "id": "a",
                        "title": "A",
                        "status": "pending",
                        "deadline": "2026-06-10",
                        "scheduled": "2026-06-15",
                    }
                ],
                source="<test>",
                strict=False,
            )

    def test_equal_dates_accepted(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadline: '2026-06-15', scheduled: '2026-06-15'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] == tasks[0]["scheduled"]


class TestDeadlineAfterScheduledAccepted:
    def test_deadline_after_scheduled_is_valid(self, tmp_path):
        # Arrange
        store = _write(
            tmp_path,
            "tasks:\n"
            "  - {id: a, title: A, status: pending,"
            " deadline: '2026-06-20', scheduled: '2026-06-15'}\n",
        )
        # Act
        tasks = load_tasks(store)
        # Assert
        assert tasks[0]["deadline"] > tasks[0]["scheduled"]


# EOF
