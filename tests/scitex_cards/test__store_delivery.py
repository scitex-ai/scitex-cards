"""Guard-kernel unit tests using explicit in-memory row ports, not PostgreSQL."""

from copy import deepcopy

import pytest

from scitex_cards import _store_delivery, _store_single_card
from scitex_cards._backend import BackendUnavailableError
from scitex_cards._store_errors import RevisionConflictError


class MemoryRows:
    """Capture read/write contracts without claiming database CAS coverage."""

    def __init__(self):
        self.card = {
            "id": "delivery-test",
            "title": "Private fixture",
            "agent": "owner",
            "status": "in_progress",
            "revision": 999,
        }
        self.revision = 7
        self.writes = []
        self.conflict = False

    def read(self, target, task_id):
        return deepcopy(self.card), self.revision

    def write(self, target, card, *, expected_revision):
        self.writes.append(expected_revision)
        if self.conflict:
            raise RevisionConflictError(card["id"], expected_revision, 8)
        self.card = deepcopy(card)
        self.revision += 1
        return {"tasks": 1}


@pytest.fixture
def rows(monkeypatch):
    """Substitute only the row ports; guard and backend resolution stay real."""
    monkeypatch.setenv("SCITEX_STORE_DSN", "postgresql://127.0.0.1:1/private_tests")
    monkeypatch.delenv("SCITEX_CARDS_HUB_URL", raising=False)
    monkeypatch.delenv(_store_delivery.FRESHNESS_ENV, raising=False)
    memory = MemoryRows()
    monkeypatch.setattr(_store_single_card, "read_card_or_raise", memory.read)
    monkeypatch.setattr(_store_single_card, "write_card_or_raise", memory.write)
    return memory


def _set_status(status):
    def mutate(card):
        card["status"] = status

    return mutate


def _namespace(required=True):
    return {
        "version": 1,
        "required": required,
        "generation": 1,
        "started_at": "2026-10-03T00:00:00Z",
        "checkpoints": {"live": {"acceptance": "passed", "by": "owner"}},
        "lease": {"fence": 1},
    }


def _refusal(call, error_type):
    try:
        call()
    except error_type as exc:
        return type(exc)
    return None


def test_legacy_completion_uses_sql_revision_not_payload(rows):
    # Arrange
    rows.card["status"] = "in_progress"
    # Act
    result = _store_delivery.mutate_card(
        None, "delivery-test", _set_status("done"), operation="complete"
    )
    # Assert
    assert (result["status"], rows.writes) == ("done", [7])


def test_enrolled_claims_cannot_complete_without_adapter(rows):
    # Arrange
    rows.card["_log_meta"] = {"delivery": _namespace()}
    before = deepcopy(rows.card)
    # Act
    error = _refusal(
        lambda: _store_delivery.mutate_card(
            None, "delivery-test", _set_status("done"), operation="resolve"
        ), _store_delivery.DeliveryGuardError,
    )
    # Assert
    assert (error, rows.card, rows.writes) == (
        _store_delivery.DeliveryGuardError, before, [],
    )


@pytest.mark.parametrize("operation", ["complete", "resolve", "update", "undo"])
def test_existing_enrolled_done_cannot_claim_noop_success(rows, operation):
    # Arrange
    rows.card.update(status="done", _log_meta={"delivery": _namespace()})
    # Act
    error = _refusal(
        lambda: _store_delivery.mutate_card(
            None, "delivery-test", lambda card: None, operation=operation
        ), _store_delivery.DeliveryGuardError,
    )
    # Assert
    assert (error, rows.writes) == (_store_delivery.DeliveryGuardError, [])


def test_legacy_noop_does_not_refresh_revision(rows):
    # Arrange
    rows.card["status"] = "done"
    # Act
    result = _store_delivery.mutate_card(
        None, "delivery-test", lambda card: None, operation="complete"
    )
    # Assert
    assert (result, rows.writes, rows.revision) == (rows.card, [], 7)


@pytest.mark.parametrize("required", [True, False])
def test_namespace_replacement_refuses_before_write(rows, required):
    # Arrange
    rows.card["_log_meta"] = {"delivery": _namespace(required)}
    before = deepcopy(rows.card)

    def mutate(card):
        card["_log_meta"]["delivery"] = {"version": 1, "required": required}

    # Act
    error = _refusal(
        lambda: _store_delivery.mutate_card(
            None, "delivery-test", mutate, operation="update"
        ), _store_delivery.DeliveryGuardError,
    )
    # Assert
    assert (error, rows.card, rows.writes) == (
        _store_delivery.DeliveryGuardError, before, [],
    )


def test_reopen_invalidates_receipts_in_same_row_write(rows):
    # Arrange
    rows.card.update(status="done", _log_meta={"delivery": _namespace()})
    # Act
    result = _store_delivery.mutate_card(
        None, "delivery-test", _set_status("in_progress"), operation="reopen"
    )
    namespace = result["_log_meta"]["delivery"]
    # Assert
    assert (namespace["generation"], namespace["checkpoints"], rows.writes) == (
        2, {}, [7]
    )


def test_revision_conflict_does_not_replay_status_action(rows):
    # Arrange
    rows.conflict = True
    calls = []

    def mutate(card):
        calls.append(card["status"])
        card["status"] = "done"

    # Act
    error = _refusal(
        lambda: _store_delivery.mutate_card(
            None, "delivery-test", mutate, operation="complete"
        ), RevisionConflictError,
    )
    # Assert
    assert (error, calls, rows.writes, rows.card["status"]) == (
        RevisionConflictError, ["in_progress"], [7], "in_progress"
    )


def test_stale_expected_revision_refuses_before_callback(rows):
    # Arrange
    calls = []
    # Act
    error = _refusal(
        lambda: _store_delivery.mutate_card(
            None, "delivery-test", lambda card: calls.append(card),
            operation="update", expected_revision=6,
        ), RevisionConflictError,
    )
    # Assert
    assert (error, calls, rows.writes) == (RevisionConflictError, [], [])


@pytest.mark.parametrize("operation", ["add", "upsert", "restore"])
def test_creation_cannot_inject_namespace(rows, operation):
    # Arrange
    candidate = deepcopy(rows.card)
    candidate["_log_meta"] = {"delivery": _namespace(False)}
    # Act
    error = _refusal(
        lambda: _store_delivery.prepare_new_card(candidate, operation=operation),
        _store_delivery.DeliveryGuardError,
    )
    # Assert
    assert (error, rows.writes) == (_store_delivery.DeliveryGuardError, [])


def test_read_port_reports_actual_revision_and_unavailable_prerequisites(rows):
    # Arrange
    rows.card["_log_meta"] = {"delivery": _namespace()}
    # Act
    result = _store_delivery.read_delivery(None, "delivery-test")
    # Assert
    assert (
        result["revision"], result["decision"]["allowed"], result["prerequisites"]
    ) == (
        7,
        False,
        ["explicit-freshness-configuration", "authenticated-delivery-observer"],
    )


def test_remote_read_refuses_local_fallback(rows, monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:1")
    # Act
    error = _refusal(
        lambda: _store_delivery.read_delivery(None, "delivery-test"),
        BackendUnavailableError,
    )
    # Assert
    assert (error, rows.writes) == (BackendUnavailableError, [])
