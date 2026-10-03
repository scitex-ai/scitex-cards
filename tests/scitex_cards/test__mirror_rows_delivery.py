"""Ordinary row-writer refusal controls with an explicit read-only connection.

The connection records the supplied canonical row and trips on a write. These
are pre-effect unit controls, not PostgreSQL or concurrent CAS acceptance tests.
"""

import json
from copy import deepcopy

import pytest

from scitex_cards._mirror_rows import _delete_card, _write_card
from scitex_cards._store_delivery import DeliveryGuardError


class RefusalConnection:
    """Expose a canonical payload/revision and fail at the first SQL mutation."""

    def __init__(self, card):
        self.card = deepcopy(card)
        self.revision = 7
        self.mutations = []
        self.answer = None

    def execute(self, sql, parameters=()):
        if sql.lstrip().upper().startswith("SELECT"):
            if "FROM tasks" in sql:
                self.answer = {
                    "id": self.card["id"],
                    "card_json": json.dumps(self.card),
                    "revision": self.revision,
                    "last_activity": self.card.get("last_activity"),
                }
            elif "FROM task_comments" in sql:
                self.answer = []
            else:
                raise AssertionError(f"unexpected read: {sql}")
            return self
        self.mutations.append((sql, parameters))
        raise AssertionError("row mutation preceded the delivery refusal")

    def fetchone(self):
        return self.answer

    def fetchall(self):
        return self.answer if isinstance(self.answer, list) else [self.answer]


def _card():
    return {
        "id": "protected-row",
        "title": "Canonical fixture",
        "status": "in_progress",
        "agent": "owner",
        "repo": "scitex-cards",
        "pr_url": "https://example.invalid/pull/1",
        "_log_meta": {
            "delivery": {
                "version": 1,
                "required": True,
                "generation": 1,
                "started_at": "2026-10-03T00:00:00Z",
                "checkpoints": {"live": {"by": "owner", "passed": True}},
                "optional": {"preserve": [1, None, False]},
            },
        },
    }


def _refusal(call):
    try:
        call()
    except DeliveryGuardError as exc:
        return type(exc)
    return None


@pytest.mark.parametrize("change", ["remove", "generation", "required", "optional"])
def test_public_row_writer_cannot_replace_any_namespace_field(change):
    # Arrange
    before = _card()
    proposed = deepcopy(before)
    metadata = proposed["_log_meta"]
    if change == "remove":
        metadata.pop("delivery")
    else:
        metadata["delivery"][change] = {"caller_claim": True}
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.card, conn.mutations) == (DeliveryGuardError, before, [])


@pytest.mark.parametrize("namespace", [None, {"version": 1, "required": False}])
def test_public_row_writer_cannot_remove_optional_or_null_namespace(namespace):
    # Arrange
    before = _card()
    before["_log_meta"]["delivery"] = namespace
    proposed = deepcopy(before)
    proposed["_log_meta"].pop("delivery")
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed, expected_revision=7))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


def test_public_row_writer_cannot_inject_delivery_into_legacy_row():
    # Arrange
    proposed = _card()
    before = deepcopy(proposed)
    before.pop("_log_meta")
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


@pytest.mark.parametrize("clock", ["completed_at", "completed_by", "closed_at", "closed_by"])
def test_public_row_writer_cannot_forge_completion_or_archive_clocks(clock):
    # Arrange
    before = _card()
    proposed = deepcopy(before)
    proposed["_log_meta"][clock] = "caller claim"
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


def test_public_row_writer_cannot_complete_using_embedded_claims():
    # Arrange
    before = _card()
    proposed = deepcopy(before)
    proposed.update(status="done", by="owner", delivery_verified=True)
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


@pytest.mark.parametrize("field", ["repo", "pr_url"])
def test_public_row_writer_requires_typed_retarget_to_invalidate_receipts(field):
    # Arrange
    before = _card()
    proposed = deepcopy(before)
    proposed[field] = "different target"
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


def test_public_row_writer_requires_typed_reopen_to_invalidate_receipts():
    # Arrange
    before = _card()
    before["status"] = "done"
    proposed = deepcopy(before)
    proposed["status"] = "in_progress"
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


@pytest.mark.parametrize("restore", [False, True])
def test_public_row_writer_cannot_bypass_marked_delete_or_restore(restore):
    # Arrange
    before = _card()
    if restore:
        before["status"] = "cancelled"
        before["_log_meta"]["deleted_at"] = "2026-10-03T00:00:00Z"
    proposed = deepcopy(before)
    if restore:
        proposed["status"] = "pending"
        proposed["_log_meta"].pop("deleted_at")
    else:
        proposed["status"] = "cancelled"
        proposed["_log_meta"]["deleted_at"] = "2026-10-03T00:00:00Z"
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _write_card(conn, proposed))
    # Assert
    assert (error, conn.mutations) == (DeliveryGuardError, [])


@pytest.mark.parametrize("namespace", [None, {"version": 1, "required": False}, {"version": 1, "required": True}])
def test_public_row_delete_cannot_remove_a_present_namespace(namespace):
    # Arrange
    before = _card()
    before["_log_meta"]["delivery"] = namespace
    conn = RefusalConnection(before)
    # Act
    error = _refusal(lambda: _delete_card(conn, before["id"]))
    # Assert
    assert (error, conn.card, conn.mutations) == (DeliveryGuardError, before, [])
