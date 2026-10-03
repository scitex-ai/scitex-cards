"""Pure reserved-namespace and lifecycle regressions; no store or runtime."""

from copy import deepcopy

import pytest
from test__delivery_policy import NOW, Observer, capture_error, card, evaluate

from scitex_cards._delivery_policy import (
    ABSENT,
    DeliveryPolicyError,
    assert_namespace_preserved,
    completion,
    delivery_namespace,
    prepare_transition,
)


def reserved_result(current, proposed):
    before = deepcopy(current)
    error = capture_error(lambda: assert_namespace_preserved(current, proposed))
    return type(error), str(error), current == before


def test_erase_checkpoints_generic_write():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"]["checkpoints"] = {}
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_replace_entire_namespace_keep_required():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"] = {"version": 1, "required": True}
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_tamper_reviewer_generic_write():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"]["roles"]["reviewer"] = "caller-controlled"
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_erase_existing_optional_namespace():
    # Arrange
    current = {"_log_meta": {"delivery": {"version": 1, "required": False}}}
    proposed = {"status": "done"}
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_inject_new_delivery_namespace_into_legacy():
    # Arrange
    current, proposed = {"status": "in_progress"}, card()
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_tamper_existing_optional_namespace():
    # Arrange
    current = {
        "_log_meta": {
            "delivery": {"version": 1, "required": False, "audit": "original"},
        },
    }
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"]["audit"] = "tampered"
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_reopen_without_generation_increment_guard():
    # Arrange
    current = card()
    current["status"] = "done"
    current["_log_meta"].update(
        completed_at=NOW, completed_by="app-owner",
        closed_at=NOW, closed_by="app-owner",
    )
    proposed = deepcopy(current)
    proposed["status"] = "in_progress"
    before = deepcopy(current)
    # Act
    result = prepare_transition(current, proposed, "update", NOW)
    meta, data = result["_log_meta"], delivery_namespace(result)
    actual = (
        data["generation"], data["started_at"], data["checkpoints"],
        {"lease", "completion", "completed_at", "completed_by"} & data.keys(),
        {"completed_at", "completed_by", "closed_at", "closed_by"} & meta.keys(),
        meta["other_event_stamp"], current,
        proposed["_log_meta"]["delivery"]["generation"],
    )
    # Assert
    assert actual == (2, NOW, {}, set(), set(), "keep", before, 1)


def test_reopen_old_receipts_complete():
    # Arrange
    current = card()
    observer = Observer(current)
    # Act
    reopened = prepare_transition(
        current, deepcopy(current), "reopen", "2026-10-03T00:19:30Z",
    )
    result = evaluate(reopened, observer)
    # Assert
    assert (result.allowed, result.reason) == (False, "evidence-binding-mismatch")


def test_coherent_target_change_same_generation_guard():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"]["target"]["head"] = "9" * 40
    claimed = proposed["_log_meta"]["delivery"]["checkpoints"]["caller-claimed"]
    claimed["head"] = "9" * 40
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_coherent_target_change_reuses_receipt_hashes():
    # Arrange
    current = card()
    observer = Observer(current)
    proposed = deepcopy(current)
    proposed["pr_url"] = "https://github.com/fixture/application/pull/2"
    # Act
    retargeted = prepare_transition(
        current, proposed, "update", "2026-10-03T00:19:30Z",
    )
    result = evaluate(retargeted, observer)
    # Assert
    assert (delivery_namespace(retargeted)["generation"], result.allowed) == (2, False)


@pytest.mark.parametrize(
    "old,new", [(ABSENT, None), (None, ABSENT), (None, {}), ({}, None)],
)
def test_presence_is_not_truthiness(old, new):
    # Arrange
    current = {} if old is ABSENT else {"_log_meta": {"delivery": old}}
    proposed = {} if new is ABSENT else {"_log_meta": {"delivery": new}}
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_namespace_type_changes_are_not_equal_booleans():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"]["required"] = 1
    # Act
    result = reserved_result(current, proposed)
    # Assert
    assert result == (DeliveryPolicyError, "delivery-namespace-reserved", True)


def test_optional_without_generation_retains_source_only_transition():
    # Arrange
    current = {
        "status": "done",
        "_log_meta": {"delivery": {"version": 1, "required": False}},
    }
    proposed = deepcopy(current)
    proposed["status"] = "in_progress"
    # Act
    result = prepare_transition(current, proposed, "reopen", NOW)
    # Assert
    assert result == proposed


def test_optional_positive_generation_invalidates_receipts():
    # Arrange
    current = card()
    current["_log_meta"]["delivery"]["required"] = False
    # Act
    result = prepare_transition(current, deepcopy(current), "reopen", NOW)
    data = delivery_namespace(result)
    # Assert
    assert (data["generation"], data["checkpoints"]) == (2, {})


@pytest.mark.parametrize("generation", [None, 0, -1, True, "1"])
def test_invalid_generation_reopen_fails_closed(generation):
    # Arrange
    current = card()
    current["_log_meta"]["delivery"]["generation"] = generation
    # Act
    error = capture_error(
        lambda: prepare_transition(current, deepcopy(current), "reopen", NOW),
    )
    # Assert
    assert (type(error), str(error)) == (DeliveryPolicyError, "generation-invalid")


def test_caller_metadata_is_checked_before_internal_invalidation():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"]["delivery"]["generation"] = 2
    # Act
    error = capture_error(lambda: prepare_transition(current, proposed, "reopen", NOW))
    # Assert
    assert (type(error), str(error)) == (
        DeliveryPolicyError, "delivery-namespace-reserved",
    )


@pytest.mark.parametrize(
    "field,value", [
        ("repo", "fixture/another"),
        ("pr_url", "https://github.com/fixture/application/pull/2"),
    ],
)
def test_source_fields_invalidate_generation(field, value):
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed[field] = value
    # Act
    result = prepare_transition(current, proposed, "update", NOW)
    # Assert
    assert delivery_namespace(result)["generation"] == 2


def test_unrelated_edit_keeps_evidence_and_inputs_unchanged():
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["title"] = "Corrected title"
    before = deepcopy(current)
    # Act
    result = prepare_transition(current, proposed, "update", NOW)
    preserved = deepcopy(delivery_namespace(result))
    delivery_namespace(result)["checkpoints"].clear()
    # Assert
    assert (preserved, current) == (delivery_namespace(before), before)


@pytest.mark.parametrize("operation", ["update", "upsert", "undo", "restore"])
@pytest.mark.parametrize("field,value", [
    ("completed_at", NOW), ("completed_by", "caller-claimed-owner"),
])
@pytest.mark.parametrize("namespace", [None, delivery_namespace(card())])
def test_open_completion_stamp_injection_refused(operation, field, value, namespace):
    # Arrange
    current = card()
    current["_log_meta"]["delivery"] = deepcopy(namespace)
    proposed = deepcopy(current)
    proposed["_log_meta"][field] = value
    # Act
    error = capture_error(lambda: prepare_transition(current, proposed, operation, NOW))
    # Assert
    assert (type(error), str(error), field in current["_log_meta"]) == (
        DeliveryPolicyError, "delivery-completion-stamp-reserved", False,
    )


@pytest.mark.parametrize("field", ["completed_at", "completed_by"])
def test_open_enrolled_existing_stamp_cannot_be_changed(field):
    # Arrange
    current = card()
    current["_log_meta"][field] = "existing-claim"
    proposed = deepcopy(current)
    proposed["_log_meta"][field] = "different-claim"
    # Act
    error = capture_error(lambda: prepare_transition(current, proposed, "update", NOW))
    # Assert
    assert (type(error), str(error)) == (
        DeliveryPolicyError, "delivery-completion-stamp-reserved",
    )


@pytest.mark.parametrize("operation", ["complete", "resolve"])
def test_completion_operation_without_done_cannot_stamp(operation):
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["_log_meta"].update(completed_at=NOW, completed_by="app-owner")
    # Act
    error = capture_error(lambda: prepare_transition(current, proposed, operation, NOW))
    # Assert
    assert (type(error), str(error)) == (
        DeliveryPolicyError, "delivery-completion-stamp-reserved",
    )


@pytest.mark.parametrize("operation", ["complete", "resolve"])
def test_done_completion_stamps_still_require_kernel_proof(operation):
    # Arrange
    current = card()
    proposed = deepcopy(current)
    proposed["status"] = "done"
    proposed["_log_meta"].update(completed_at=NOW, completed_by="app-owner")
    # Act
    result = prepare_transition(current, proposed, operation, NOW)
    verdict = completion(result, now=NOW)
    # Assert
    assert (result["_log_meta"]["completed_at"], verdict.allowed) == (NOW, False)


def test_derived_reopen_cleanup_accepts_removal_but_refuses_changed_stamp():
    # Arrange
    current = card()
    current["status"] = "done"
    current["_log_meta"].update(completed_at=NOW, completed_by="app-owner")
    proposed = deepcopy(current)
    proposed["status"] = "in_progress"
    proposed["_log_meta"]["completed_at"] = "caller-replacement"
    # Act
    error = capture_error(lambda: prepare_transition(current, proposed, "reopen", NOW))
    for key in ("completed_at", "completed_by"):
        proposed["_log_meta"].pop(key)
    result = prepare_transition(current, proposed, "upsert", NOW)
    actual = (str(error), delivery_namespace(result)["generation"],
              "completed_at" in result["_log_meta"])
    # Assert
    assert actual == ("delivery-completion-stamp-reserved", 2, False)


def test_legacy_completion_stamp_behavior_is_preserved():
    # Arrange
    current = card()
    current["_log_meta"].pop("delivery")
    proposed = deepcopy(current)
    proposed["_log_meta"].update(completed_at=NOW, completed_by="app-owner")
    # Act
    result = prepare_transition(current, proposed, "update", NOW)
    # Assert
    assert result == proposed


def test_optional_completion_stamp_behavior_is_preserved():
    # Arrange
    current = card()
    current["_log_meta"]["delivery"]["required"] = False
    proposed = deepcopy(current)
    proposed["_log_meta"].update(completed_at=NOW, completed_by="app-owner")
    # Act
    result = prepare_transition(current, proposed, "update", NOW)
    # Assert
    assert result == proposed


@pytest.mark.parametrize("cleared_meta", [
    {}, {"deleted_at": None}, {"deleted_at": ""}, {"deleted_at": False},
])
@pytest.mark.parametrize("required", [True, False])
def test_restore_live_again_invalidates_generation(cleared_meta, required):
    # Arrange
    current = card()
    current["status"] = "cancelled"
    current["_log_meta"]["deleted_at"] = NOW
    current["_log_meta"]["delivery"]["required"] = required
    proposed = deepcopy(current)
    proposed["status"] = "in_progress"
    proposed["_log_meta"].pop("deleted_at")
    proposed["_log_meta"].update(cleared_meta)
    observer = Observer(current)
    # Act
    result = prepare_transition(current, proposed, "restore", NOW)
    data = delivery_namespace(result)
    actual = (data["generation"], data["started_at"], data["checkpoints"],
              "lease" in data, evaluate(result, observer).allowed)
    # Assert
    assert actual == (2, NOW, {}, False, not required)
