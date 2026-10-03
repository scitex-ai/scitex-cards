"""Real Board callbacks over explicit synthetic row and notification ports.

The revision check here is an in-memory unit control. It does not establish
PostgreSQL CAS, HTTP authentication, deployed evidence or adapter acceptance.
The matching managed-fixture tests remain a separate unmet prerequisite.
"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402
from test__store_delivery_callers import card, delivery, rows  # noqa: E402,F401

from scitex_cards._django.handlers import reopen, resolve, undo  # noqa: E402
from scitex_cards._store_errors import RevisionConflictError  # noqa: E402


class RecordingBus:
    """A local notification unit port; publishes never leave this test."""

    def __init__(self):
        self.published = []

    def publish(self, channel, payload):
        self.published.append((channel, deepcopy(payload)))


@pytest.fixture
def board_ports(rows, monkeypatch):
    bus = RecordingBus()
    monkeypatch.setattr(resolve.handle_resolve, "_BUS", bus, raising=False)
    monkeypatch.setattr(reopen.handle_reopen, "_BUS", bus, raising=False)
    return rows, SimpleNamespace(store_path=None), bus


def request(endpoint, body):
    return RequestFactory().post(
        f"/{endpoint}", data=json.dumps(body), content_type="application/json"
    )


def resolve_error(board):
    """Capture the actual conflict for a single outcome assertion."""
    try:
        resolve.handle_resolve(request("resolve", {"id": "unit-card"}), board)
    except RevisionConflictError as exc:
        return exc
    return None


@pytest.mark.parametrize("actor", ["operator", "agent:reviewer"])
def test_board_actor_and_claimed_proof_cannot_resolve_enrolled_card(board_ports, actor):
    # Arrange
    rows, board, bus = board_ports
    original = card()
    rows.seed(original, revision=37)
    payload = {
        "id": "unit-card", "actor": actor, "force": True,
        "verified": True, "receipt_sha256": "a" * 64,
        "delivery_proof": {"answer": "a" * 64},
    }

    # Act
    response = resolve.handle_resolve(request("resolve", payload), board)

    # Assert
    assert (response.status_code, rows.cards["unit-card"], rows.writes, bus.published) == (
        400, original, [], [],
    )


def test_board_done_shaped_enrollee_cannot_return_unproven_noop_success(board_ports):
    # Arrange
    rows, board, bus = board_ports
    original = card(status="done")
    rows.seed(original)

    # Act
    response = resolve.handle_resolve(request("resolve", {"id": "unit-card"}), board)

    # Assert
    assert (response.status_code, rows.cards["unit-card"], rows.writes, bus.published) == (
        400, original, [], [],
    )


def test_board_undo_prior_status_done_cannot_bypass_completion(board_ports):
    # Arrange
    rows, board, bus = board_ports
    original = card()
    rows.seed(original)

    # Act
    response = reopen.handle_reopen(
        request("reopen", {"id": "unit-card", "prior_status": "done", "actor": "operator"}),
        board,
    )

    # Assert
    assert (response.status_code, rows.cards["unit-card"], rows.writes, bus.published) == (
        400, original, [], [],
    )


def test_board_legacy_resolve_preserves_response_audit_and_true_noop(board_ports):
    # Arrange
    rows, board, bus = board_ports
    rows.seed(card(status="blocked", namespace=False), revision=37)

    # Act
    first = resolve.handle_resolve(
        request("resolve", {"id": "unit-card", "actor": "operator"}), board
    )
    persisted = deepcopy(rows.cards["unit-card"])
    second = resolve.handle_resolve(
        request("resolve", {"id": "unit-card", "actor": "different"}), board
    )
    payload = json.loads(first.content)
    noop = json.loads(second.content)

    # Assert
    assert (
        first.status_code, second.status_code, payload["prior_status"],
        payload["prior_blocker"], payload["comment"]["author"], noop["noop"],
        rows.cards["unit-card"], len(rows.writes), len(bus.published),
    ) == (200, 200, "blocked", "operator-decision", "operator", True, persisted, 1, 1)


def test_board_resolve_passes_the_same_observed_revision_to_write_port(board_ports):
    # Arrange
    rows, board, _bus = board_ports
    rows.seed(card(namespace=False), revision=37)

    # Act
    resolve.handle_resolve(request("resolve", {"id": "unit-card"}), board)

    # Assert
    assert (len(rows.writes), rows.writes[0][1], rows.revisions["unit-card"]) == (1, 37, 38)


def test_board_revision_conflict_does_not_retry_write_or_publish(board_ports):
    # Arrange
    rows, board, bus = board_ports
    original = card(namespace=False)
    rows.seed(original, revision=37)
    attempts = []

    def concurrent_write():
        attempts.append(True)
        rows.revisions["unit-card"] = 38

    rows.before_write = concurrent_write

    # Act
    error = resolve_error(board)

    # Assert
    assert (type(error), rows.cards["unit-card"], rows.writes, bus.published, attempts) == (
        RevisionConflictError, original, [], [], [True],
    )


def test_board_reopen_advances_generation_and_discards_receipts_in_one_row_write(board_ports):
    # Arrange
    rows, board, bus = board_ports
    rows.seed(card(status="done"), revision=37)

    # Act
    response = reopen.handle_reopen(
        request("reopen", {"id": "unit-card", "prior_status": "in_progress"}), board
    )
    data = rows.cards["unit-card"]["_log_meta"]["delivery"]

    # Assert
    assert (
        response.status_code, data["generation"], data["checkpoints"],
        "lease" in data, rows.writes[0][1], len(rows.writes), len(bus.published),
    ) == (200, 2, {}, False, 37, 1, 1)


@pytest.mark.parametrize("marker", [None, delivery(False), "malformed"])
def test_board_delete_marker_refusal_precedes_target_and_ref_writes(board_ports, marker):
    # Arrange
    rows, board, bus = board_ports
    original = card()
    original["_log_meta"]["delivery"] = marker
    rows.seed(original)

    # Act
    response = undo.handle_delete(request("delete", {"id": "unit-card"}), board)

    # Assert
    assert (response.status_code, rows.cards["unit-card"], rows.writes, bus.published) == (
        400, original, [], [],
    )


def test_board_restore_missing_row_cannot_inject_optional_namespace(board_ports):
    # Arrange
    rows, board, bus = board_ports
    proposed = card()
    proposed["_log_meta"]["delivery"] = delivery(False)

    # Act
    response = undo.handle_restore(request("restore", {"task": proposed}), board)

    # Assert
    assert (response.status_code, rows.cards, rows.writes, bus.published) == (400, {}, [], [])


@pytest.mark.parametrize(
    "initial", [("in_progress", {}), ("cancelled", {"deleted_at": "2026-10-03T09:45:00Z"})]
)
def test_board_restore_existing_row_cannot_replace_its_namespace(board_ports, initial):
    # Arrange
    rows, board, bus = board_ports
    original = card(status=initial[0])
    original["_log_meta"].update(initial[1])
    rows.seed(original)
    proposed = card()
    proposed["_log_meta"]["delivery"] = delivery(False)

    # Act
    response = undo.handle_restore(request("restore", {"task": proposed}), board)

    # Assert
    assert (response.status_code, rows.cards["unit-card"], rows.writes, bus.published) == (
        400, original, [], [],
    )


def test_board_restore_legacy_tombstone_uses_observed_row_revision(board_ports):
    # Arrange
    rows, board, bus = board_ports
    original = card(status="cancelled", namespace=False)
    original["_log_meta"]["deleted_at"] = "2026-10-03T09:45:00Z"
    rows.seed(original, revision=37)
    proposed = card(namespace=False)

    # Act
    response = undo.handle_restore(request("restore", {"task": proposed}), board)

    # Assert
    assert (
        response.status_code, rows.cards["unit-card"], rows.writes[0][1],
        len(rows.writes), bus.published,
    ) == (200, proposed, 37, 1, [])


@pytest.mark.parametrize("restored_meta", [{}, {"deleted_at": None}])
def test_board_restore_tombstone_invalidates_the_old_delivery_generation(
    board_ports, restored_meta
):
    # Arrange
    rows, board, bus = board_ports
    original = card(status="cancelled")
    original["_log_meta"]["deleted_at"] = "2026-10-03T09:45:00Z"
    rows.seed(original, revision=37)
    proposed = card()
    proposed["_log_meta"].update(restored_meta)

    # Act
    response = undo.handle_restore(request("restore", {"task": proposed}), board)
    data = rows.cards["unit-card"]["_log_meta"]["delivery"]

    # Assert
    assert (
        response.status_code, data["generation"], data["checkpoints"],
        "lease" in data, "completion" in data, rows.writes[0][1],
        len(rows.writes), bus.published,
    ) == (200, 2, {}, False, False, 37, 1, [])
