"""Delivery namespace protection at the real Board Delete/Undo boundary.

These tests require the repository's managed, isolated PostgreSQL fixture.
They do not substitute an in-memory store for a platform/CAS assertion.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from conftest import seed_db_from_doc  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers.undo import handle_delete, handle_restore  # noqa: E402
from scitex_cards._store import TaskNotFoundError, get_task  # noqa: E402


@pytest.fixture
def store():
    path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    seed_db_from_doc(
        {
            "tasks": [
                {
                    "id": "delivery",
                    "title": "Delivery",
                    "status": "in_progress",
                    "_log_meta": {
                        "delivery": {"version": 1, "required": True, "generation": 2}
                    },
                },
                {
                    "id": "optional",
                    "title": "Source only",
                    "status": "in_progress",
                    "_log_meta": {"delivery": {"version": 1, "required": False}},
                },
                {
                    "id": "dependent",
                    "title": "Dependent",
                    "status": "in_progress",
                    "depends_on": ["delivery"],
                },
            ]
        },
        os.environ["SCITEX_STORE_DSN"],
    )
    return path


def _request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}", data=json.dumps(body), content_type="application/json"
    )


@pytest.mark.parametrize("task_id", ["delivery", "optional"])
def test_delete_refuses_to_remove_any_reserved_delivery_namespace(store, task_id):
    # Arrange
    # Act
    response = handle_delete(
        _request("delete", {"id": task_id}), SimpleNamespace(store_path=store)
    )

    # Assert
    assert response.status_code == 400


def test_refused_delete_preserves_dependent_edges(store):
    # Arrange
    # Act
    handle_delete(
        _request("delete", {"id": "delivery"}), SimpleNamespace(store_path=store)
    )

    # Assert
    assert get_task(store, "dependent")["depends_on"] == ["delivery"]


def test_refused_delete_preserves_the_full_canonical_card(store):
    # Arrange
    before = get_task(store, "delivery")

    # Act
    handle_delete(
        _request("delete", {"id": "delivery"}), SimpleNamespace(store_path=store)
    )

    # Assert
    assert get_task(store, "delivery") == before


@pytest.mark.parametrize("delivery", [None, {"version": 1, "required": False}])
def test_restore_cannot_inject_a_reserved_namespace_into_a_missing_row(store, delivery):
    # Arrange
    task = {
        "id": "injected",
        "title": "Injection",
        "status": "done",
        "_log_meta": {"delivery": delivery},
    }

    # Act
    response = handle_restore(
        _request("restore", {"task": task}), SimpleNamespace(store_path=store)
    )

    # Assert
    assert response.status_code == 400


def test_refused_restore_does_not_insert_the_injected_card(store):
    # Arrange
    task = {
        "id": "injected",
        "title": "Injection",
        "status": "done",
        "_log_meta": {"delivery": {"version": 1, "required": False}},
    }

    # Act
    handle_restore(_request("restore", {"task": task}), SimpleNamespace(store_path=store))

    # Assert
    with pytest.raises(TaskNotFoundError):
        get_task(store, "injected")


def test_existing_row_restore_cannot_replace_canonical_namespace(store):
    # Arrange
    task = get_task(store, "delivery")
    task["_log_meta"]["delivery"]["required"] = False

    # Act
    response = handle_restore(
        _request("restore", {"task": task}), SimpleNamespace(store_path=store)
    )

    # Assert
    assert response.status_code == 400
