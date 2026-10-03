"""Synthetic public-verb behavior; no PostgreSQL or deployed evidence claims."""

import warnings
from copy import deepcopy

import pytest

from scitex_cards import _help_wait, _store, _store_add, _store_lifecycle, _store_mutate, _store_upsert
from scitex_cards._store_delivery import DeliveryGuardError
from scitex_cards._store_errors import RevisionConflictError
from _delivery_store_ports import NOW, card, delivery, rows  # noqa: F401 -- scoped pytest fixture

@pytest.mark.parametrize("verb", ("update", "complete", "resolve", "upsert"))
def test_done_entrypoints_refuse_without_trusted_prerequisites_leaves_storage_unchanged(rows, verb):
    # Arrange
    original = card(status="blocked")
    rows.seed(original)
    calls = {
        "update": lambda: _store_mutate.update_task(task_id="unit-card", status="done", blocker=None),
        "complete": lambda: _store_lifecycle.complete_task(task_id="unit-card"),
        "resolve": lambda: _store_lifecycle.resolve_task(task_id="unit-card"),
        "upsert": lambda: _store_upsert.upsert_task(id="unit-card", title="refresh", status="done"),
    }
    # Act
    try:
        calls[verb]()
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


@pytest.mark.parametrize("verb", ("add", "upsert", "restore"))
@pytest.mark.parametrize("value", (None, {"version": 1, "required": False}, delivery()))
def test_generic_creation_cannot_inject_any_delivery_namespace_leaves_storage_unchanged(rows, verb, value):
    # Arrange
    proposed = card(namespace=False)
    proposed["_log_meta"]["delivery"] = value
    calls = {
        "add": lambda: _store_add.add_task(id="unit-card", title="unit", assignee="agent:unit", _log_meta=proposed["_log_meta"]),
        "upsert": lambda: _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress", assignee="agent:unit", _log_meta=proposed["_log_meta"]),
        "restore": lambda: _store_lifecycle.restore_task(task=proposed),
    }
    # Act
    try:
        calls[verb]()
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards, rows.writes, rows.events) == ({}, [], [])


@pytest.mark.parametrize("metadata", (None, {}, {"delivery": None}, {"delivery": {"version": 1, "required": False}}))
def test_generic_update_cannot_remove_or_rewrite_namespace_leaves_storage_unchanged(rows, metadata):
    # Arrange
    original = card()
    rows.seed(original)
    # Act
    try:
        _store_mutate.update_task(task_id="unit-card", _log_meta=metadata)
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


@pytest.mark.parametrize("current_namespace", (False, True))
def test_upsert_rejects_ignored_delivery_injection_or_removal_leaves_storage_unchanged(rows, current_namespace):
    # Arrange
    original = card(namespace=current_namespace)
    rows.seed(original)
    metadata = None if current_namespace else {"delivery": delivery(False)}
    # Act
    try:
        _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress", _log_meta=metadata)
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


@pytest.mark.parametrize("verb", ("update", "complete", "resolve", "reopen", "upsert"))
def test_tombstone_cannot_be_resurrected_by_other_verbs_leaves_storage_unchanged(rows, verb):
    # Arrange
    original = card(status="cancelled", namespace=False)
    original["_log_meta"]["deleted_at"] = NOW
    rows.seed(original)
    calls = {
        "update": lambda: _store_mutate.update_task(task_id="unit-card", status="in_progress"),
        "complete": lambda: _store_lifecycle.complete_task(task_id="unit-card"),
        "resolve": lambda: _store_lifecycle.resolve_task(task_id="unit-card"),
        "reopen": lambda: _store_lifecycle.reopen_task(task_id="unit-card"),
        "upsert": lambda: _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress"),
    }
    # Act
    error = ValueError if verb == "upsert" else _store.TaskNotFoundError
    try:
        calls[verb]()
    except error:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


def test_explicit_revision_conflict_does_not_run_side_effects_leaves_storage_unchanged(rows):
    # Arrange
    original = card(namespace=False)
    rows.seed(original, revision=4)
    # Act
    try:
        _store_mutate.update_task(task_id="unit-card", title="stale", expected_revision=3)
    except RevisionConflictError:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


def test_concurrent_authoritative_write_is_not_retried_leaves_storage_unchanged(rows):
    # Arrange
    rows.seed(card(namespace=False), revision=4)
    attempts = []

    def race():
        attempts.append(True)
        rows.cards["unit-card"]["title"] = "concurrent winner"
        rows.revisions["unit-card"] += 1

    rows.before_write = race
    # Act
    try:
        _store_mutate.update_task(task_id="unit-card", title="stale loser")
    except RevisionConflictError:
        pass
    # Assert
    assert (rows.cards["unit-card"]["title"], attempts, rows.writes, rows.events) == ("concurrent winner", [True], [], [])


@pytest.mark.parametrize("namespace", (None, {"version": 1, "required": False}, delivery()))
def test_any_present_namespace_refuses_delete_before_refs_are_scrubbed_leaves_storage_unchanged(rows, namespace):
    # Arrange
    original = card(namespace=False)
    original["_log_meta"]["delivery"] = namespace
    dependent = card(namespace=False)
    dependent.update(id="dependent", depends_on=["unit-card"])
    rows.seed(original)
    rows.seed(dependent)
    before = deepcopy(rows.cards)
    # Act
    try:
        _store_lifecycle.delete_task(task_id="unit-card")
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards, rows.writes, rows.events) == (before, [], [])


def test_tombstone_restore_cannot_inject_delivery_namespace_leaves_storage_unchanged(rows):
    # Arrange
    original = card(status="cancelled", namespace=False)
    original["_log_meta"]["deleted_at"] = NOW
    rows.seed(original)
    payload = card()
    # Act
    try:
        _store_lifecycle.restore_task(task=payload)
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


def test_existing_structural_validation_still_refuses_bad_legacy_blocker_leaves_storage_unchanged(rows):
    # Arrange
    original = card(namespace=False)
    rows.seed(original)
    # Act
    try:
        _store_mutate.update_task(task_id="unit-card", blocker="banana")
    except _store.TaskValidationError:
        pass
    # Assert
    assert (rows.cards["unit-card"], rows.writes, rows.events) == (original, [], [])


def test_tolerated_status_warning_is_returned_in_result(rows):
    # Arrange
    rows.seed(card(namespace=False))
    # Act
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        result = _store_mutate.update_task(task_id="unit-card", status="banana")
    # Assert
    assert (result["status"], len(result["warnings"]), len(rows.writes)) == ("banana", 1, 1)


def test_help_clear_cannot_bypass_enrolled_completion_leaves_storage_unchanged(rows):
    # Arrange
    original = card(status="blocked")
    original["id"] = _help_wait.help_card_id("agent:unit")
    rows.seed(original)
    # Act
    try:
        _help_wait.help_clear(agent="agent:unit")
    except DeliveryGuardError:
        pass
    # Assert
    assert (rows.cards[original["id"]], rows.writes, rows.events) == (original, [], [])


