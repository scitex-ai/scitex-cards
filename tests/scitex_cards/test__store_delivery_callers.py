"""Synthetic public-verb behavior; no PostgreSQL or deployed evidence claims."""

from copy import deepcopy

import pytest

from scitex_cards import _help_wait, _store, _store_add, _store_lifecycle, _store_mutate, _store_upsert
from scitex_cards._store_delivery import DeliveryGuardError
from scitex_cards._store_errors import RevisionConflictError
from _delivery_store_ports import NOW, card, delivery, rows  # noqa: F401 -- scoped pytest fixture

@pytest.mark.parametrize("verb", ("update", "complete", "resolve", "upsert"))
def test_done_entrypoints_refuse_without_trusted_prerequisites(rows, verb):
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
    def act():
        calls[verb]()
    # Assert
    with pytest.raises(DeliveryGuardError):
        act()


@pytest.mark.parametrize("verb", ("add", "upsert", "restore"))
@pytest.mark.parametrize("value", (None, {"version": 1, "required": False}, delivery()))
def test_generic_creation_cannot_inject_any_delivery_namespace(rows, verb, value):
    # Arrange
    proposed = card(namespace=False)
    proposed["_log_meta"]["delivery"] = value
    calls = {
        "add": lambda: _store_add.add_task(id="unit-card", title="unit", assignee="agent:unit", _log_meta=proposed["_log_meta"]),
        "upsert": lambda: _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress", assignee="agent:unit", _log_meta=proposed["_log_meta"]),
        "restore": lambda: _store_lifecycle.restore_task(task=proposed),
    }
    # Act
    def act():
        calls[verb]()
    # Assert
    with pytest.raises(DeliveryGuardError):
        act()


@pytest.mark.parametrize("metadata", (None, {}, {"delivery": None}, {"delivery": {"version": 1, "required": False}}))
def test_generic_update_cannot_remove_or_rewrite_namespace(rows, metadata):
    # Arrange
    original = card()
    rows.seed(original)
    # Act
    def act():
        _store_mutate.update_task(task_id="unit-card", _log_meta=metadata)
    # Assert
    with pytest.raises(DeliveryGuardError):
        act()


@pytest.mark.parametrize("current_namespace", (False, True))
def test_upsert_rejects_ignored_delivery_injection_or_removal(rows, current_namespace):
    # Arrange
    original = card(namespace=current_namespace)
    rows.seed(original)
    metadata = None if current_namespace else {"delivery": delivery(False)}
    # Act
    def act():
        _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress", _log_meta=metadata)
    # Assert
    with pytest.raises(DeliveryGuardError):
        act()


@pytest.mark.parametrize("verb", ("update", "reopen", "upsert"))
def test_reopen_invalidates_previous_delivery_generation(rows, verb):
    # Arrange
    rows.seed(card(status="done"))
    calls = {
        "update": lambda: _store_mutate.update_task(task_id="unit-card", status="deferred"),
        "reopen": lambda: _store_lifecycle.reopen_task(task_id="unit-card"),
        "upsert": lambda: _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress"),
    }
    # Act
    calls[verb]()
    meta = rows.cards["unit-card"]["_log_meta"]
    data = meta["delivery"]
    # Assert
    assert (data["generation"], data["started_at"], data["checkpoints"], "lease" in data,
            "completion" in data, "completed_at" in meta, meta["other"], len(rows.writes)) == (2, NOW, {}, False, False, False, "preserve", 1)


@pytest.mark.parametrize("field", ("repo", "pr_url"))
def test_retarget_invalidates_previous_delivery_evidence(rows, field):
    # Arrange
    rows.seed(card())
    # Act
    _store_mutate.update_task(task_id="unit-card", **{field: "changed-target"})
    data = rows.cards["unit-card"]["_log_meta"]["delivery"]
    # Assert
    assert (data["generation"], data["started_at"], data["checkpoints"], "lease" in data) == (2, NOW, {}, False)


def test_legacy_completion_preserves_stamps_and_noop_behavior(rows):
    # Arrange
    rows.seed(card(status="blocked", namespace=False))
    # Act
    first = _store_lifecycle.complete_task(task_id="unit-card", by="agent:unit")
    second = _store_lifecycle.complete_task(task_id="unit-card", by="another-actor")
    # Assert
    assert (first["status"], first["_log_meta"]["completed_at"], first["_log_meta"]["completed_by"],
            first == second, len(rows.writes), len(rows.events), "blocker" in first) == (
        "done", NOW, "agent:unit", True, 1, 2, False,
    )


@pytest.mark.parametrize("verb", ("add", "upsert", "restore"))
def test_legacy_creation_still_works(rows, verb):
    # Arrange
    payload = card(namespace=False)
    calls = {
        "add": lambda: _store_add.add_task(id="unit-card", title="unit", assignee="agent:unit"),
        "upsert": lambda: _store_upsert.upsert_task(id="unit-card", title="unit", status="in_progress", assignee="agent:unit", created_by="agent:unit"),
        "restore": lambda: _store_lifecycle.restore_task(task=payload),
    }
    # Act
    calls[verb]()
    # Assert
    assert (rows.cards["unit-card"]["id"], len(rows.writes)) == ("unit-card", 1)


def test_optional_namespace_allows_source_only_completion_and_is_preserved(rows):
    # Arrange
    original = card(status="blocked")
    original["_log_meta"]["delivery"] = delivery(False)
    rows.seed(original)
    # Act
    result = _store_lifecycle.complete_task(task_id="unit-card")
    # Assert
    assert (result["status"], result["_log_meta"]["delivery"], len(rows.writes)) == (
        "done", original["_log_meta"]["delivery"], 1,
    )


def test_legacy_upsert_reopen_clears_completion_stamps(rows):
    # Arrange
    rows.seed(card(status="done", namespace=False))
    # Act
    result = _store_upsert.upsert_task(id="unit-card", title="recurrence", status="in_progress")
    # Assert
    assert (result["status"], result["_upsert_action"], result["_log_meta"], len(rows.writes)) == (
        "in_progress", "reopened", {"other": "preserve"}, 1,
    )


def test_generic_exit_from_done_does_not_mutate_caller_metadata(rows):
    # Arrange
    original = card(status="done")
    rows.seed(original)
    payload = deepcopy(original["_log_meta"])
    before = deepcopy(payload)
    # Act
    _store_mutate.update_task(task_id="unit-card", status="deferred", _log_meta=payload)
    # Assert
    assert (payload, rows.cards["unit-card"]["_log_meta"]["delivery"]["generation"]) == (before, 2)


@pytest.mark.parametrize("verb", ("update", "complete", "resolve", "reopen", "upsert"))
def test_tombstone_cannot_be_resurrected_by_other_verbs(rows, verb):
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
    def act():
        calls[verb]()
    # Assert
    with pytest.raises(error, match="DELETED" if verb == "upsert" else None):
        act()


def test_explicit_revision_conflict_does_not_run_side_effects(rows):
    # Arrange
    original = card(namespace=False)
    rows.seed(original, revision=4)
    # Act
    def act():
        _store_mutate.update_task(task_id="unit-card", title="stale", expected_revision=3)
    # Assert
    with pytest.raises(RevisionConflictError):
        act()


def test_concurrent_authoritative_write_is_not_retried(rows):
    # Arrange
    rows.seed(card(namespace=False), revision=4)
    attempts = []

    def race():
        attempts.append(True)
        rows.cards["unit-card"]["title"] = "concurrent winner"
        rows.revisions["unit-card"] += 1

    rows.before_write = race
    # Act
    def act():
        _store_mutate.update_task(task_id="unit-card", title="stale loser")
    # Assert
    with pytest.raises(RevisionConflictError):
        act()


@pytest.mark.parametrize("namespace", (None, {"version": 1, "required": False}, delivery()))
def test_any_present_namespace_refuses_delete_before_refs_are_scrubbed(rows, namespace):
    # Arrange
    original = card(namespace=False)
    original["_log_meta"]["delivery"] = namespace
    dependent = card(namespace=False)
    dependent.update(id="dependent", depends_on=["unit-card"])
    rows.seed(original)
    rows.seed(dependent)
    before = deepcopy(rows.cards)
    # Act
    def act():
        _store_lifecycle.delete_task(task_id="unit-card")
    # Assert
    with pytest.raises(DeliveryGuardError, match="controlled-delivery-removal-unavailable"):
        act()


def test_tombstone_restore_cannot_inject_delivery_namespace(rows):
    # Arrange
    original = card(status="cancelled", namespace=False)
    original["_log_meta"]["deleted_at"] = NOW
    rows.seed(original)
    payload = card()
    # Act
    def act():
        _store_lifecycle.restore_task(task=payload)
    # Assert
    with pytest.raises(DeliveryGuardError):
        act()


def test_legacy_restore_does_not_mutate_caller_payload(rows):
    # Arrange
    original = card(status="cancelled", namespace=False)
    original["_log_meta"]["deleted_at"] = NOW
    rows.seed(original)
    payload = card(namespace=False)
    before = deepcopy(payload)
    # Act
    result = _store_lifecycle.restore_task(task=payload, refs=["dependent"])
    # Assert
    assert (payload, result["task"]["last_activity"], result["refs"], len(rows.writes)) == (before, NOW, ["dependent"], 1)


def test_legacy_delete_preserves_lossless_undo_payload_and_ref_scrub(rows):
    # Arrange
    original = card(status="blocked", namespace=False)
    original["comments"] = [{"author": "agent:unit", "text": "Before deletion", "ts": NOW}]
    rows.seed(original)
    dependent = card(namespace=False)
    dependent.update(id="dependent", depends_on=["unit-card"])
    rows.seed(dependent)
    # Act
    deleted = _store_lifecycle.delete_task(task_id="unit-card")
    _store_lifecycle.restore_task(task=deleted["removed"], refs=deleted["refs"])
    restored = rows.cards["unit-card"]
    # Assert
    assert (deleted["removed"], deleted["refs"], "depends_on" in rows.cards["dependent"],
            restored["status"], restored["blocker"], "deleted_at" in restored["_log_meta"]) == (
        original, ["dependent"], False, "blocked", "operator-decision", False,
    )


def test_existing_structural_validation_still_refuses_bad_legacy_blocker(rows):
    # Arrange
    original = card(namespace=False)
    rows.seed(original)
    # Act
    def act():
        _store_mutate.update_task(task_id="unit-card", blocker="banana")
    # Assert
    with pytest.raises(_store.TaskValidationError):
        act()


def test_existing_tolerated_status_warning_reaches_caller(rows):
    # Arrange
    rows.seed(card(namespace=False))
    # Act
    def act():
        result = _store_mutate.update_task(task_id="unit-card", status="banana")
    # Assert
    with pytest.warns(UserWarning, match="TOLERATED"):
        act()


def test_help_clear_cannot_bypass_enrolled_completion(rows):
    # Arrange
    original = card(status="blocked")
    original["id"] = _help_wait.help_card_id("agent:unit")
    rows.seed(original)
    # Act
    def act():
        _help_wait.help_clear(agent="agent:unit")
    # Assert
    with pytest.raises(DeliveryGuardError):
        act()


def test_help_wait_reopen_invalidates_delivery_generation_without_events(rows):
    # Arrange
    original = card(status="done")
    original["id"] = _help_wait.help_card_id("agent:unit")
    rows.seed(original)
    # Act
    result = _help_wait.help_wait(agent="agent:unit", question="another decision", host="unit-host")
    meta = result["_log_meta"]
    data = meta["delivery"]
    # Assert
    assert (result["status"], result["blocker"], data["generation"], data["checkpoints"],
            "lease" in data, "completed_at" in meta, len(rows.writes), rows.events) == (
        "blocked", "operator-decision", 2, {}, False, False, 1, [],
    )


def test_help_clear_missing_card_retains_noop_contract(rows):
    # Arrange
    agent = "agent:unit"
    # Act
    result = _help_wait.help_clear(agent=agent)
    # Assert
    assert (result, rows.writes, rows.events) == ({"task_id": _help_wait.help_card_id(agent), "cleared": False}, [], [])


def test_help_wait_creation_preserves_legacy_contract_and_no_events(rows):
    # Arrange
    agent = "agent:unit"
    # Act
    result = _help_wait.help_wait(agent=agent, question="question", host="unit-host")
    # Assert
    assert (result["id"], result["status"], result["blocker"], result["assignee"],
            result["note"], result["host"], result["created_at"], rows.events) == (
        _help_wait.help_card_id(agent), "blocked", "operator-decision", agent,
        "question", "unit-host", NOW, [],
    )


def test_help_wait_creation_writes_only_its_new_card(rows):
    # Arrange
    unrelated = card()
    rows.seed(unrelated)
    # Act
    result = _help_wait.help_wait(agent="agent:unit", question="question", host="unit-host")
    # Assert
    assert [task["id"] for task, _revision in rows.writes] == [result["id"]]


def test_help_clear_legacy_completion_preserves_no_event_contract(rows):
    # Arrange
    original = card(status="blocked", namespace=False)
    original["id"] = _help_wait.help_card_id("agent:unit")
    rows.seed(original)
    # Act
    result = _help_wait.help_clear(agent="agent:unit")
    # Assert
    assert (result["cleared"], result["task"]["status"], "blocker" in result["task"],
            len(rows.writes), rows.events) == (True, "done", False, 1, [])

