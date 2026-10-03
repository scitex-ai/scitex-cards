"""Pure adapter controls; these assert no PostgreSQL or transaction coverage."""

import json
import pickle
from copy import deepcopy

import pytest

from scitex_cards import _store_delivery as guard
from scitex_cards._comment_ids import stamp_comment_id
from scitex_cards._store_errors import RevisionConflictError

TARGET = "postgresql://fixture.invalid/cards"
REVISION = 7


def card(*, required=True, status="in_progress"):
    return {
        "id": "protected", "title": "Delivery", "status": status,
        "agent": "owner", "repo": "repo", "pr_url": "pr",
        "_log_meta": {
            "delivery": {
                "version": 1, "required": required, "generation": 1,
                "started_at": "2026-10-03T00:00:00Z",
                "checkpoints": {"live": {"by": "owner", "passed": True}},
                "lease": {"fence": 1},
            },
        },
    }


def append_comment(before):
    after = deepcopy(before)
    entry = stamp_comment_id({
        "author": "claimed-owner", "ts": "2026-10-03T01:00:00Z",
        "text": "claimed completion", "kind": "done",
    })
    after.setdefault("comments", []).append(entry)
    after["last_activity"] = entry["ts"]
    return after


def refusal(call):
    try:
        call()
    except (guard.DeliveryGuardError, RevisionConflictError, TypeError) as exc:
        return type(exc)
    return None


@pytest.mark.parametrize("value", [None, {}, {"version": 1, "required": False}])
def test_generic_new_row_cannot_inject_namespace(value):
    # Arrange
    incoming = {"id": "new", "_log_meta": {"delivery": value}}
    original = deepcopy(incoming)
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        None, incoming, None, None
    ))
    # Assert
    assert (error, incoming) == (guard.DeliveryGuardError, original)


@pytest.mark.parametrize("required", [True, False])
def test_generic_harmless_edit_adopts_authoritative_revision(required):
    # Arrange
    before = card(required=required)
    after = deepcopy(before)
    after.update(title="Edited", revision=999)
    # Act
    revision = guard._preflight_generic_row_write(before, after, REVISION, None)
    # Assert
    assert (revision, after["_log_meta"]) == (REVISION, before["_log_meta"])


@pytest.mark.parametrize("attack", ["remove", "replace", "bool-generation"])
def test_generic_namespace_changes_refuse_without_mutating_input(attack):
    # Arrange
    before = card()
    after = deepcopy(before)
    namespaces = {
        "remove": {}, "replace": {"delivery": None},
        "bool-generation": {"delivery": {
            **before["_log_meta"]["delivery"], "generation": True,
        }},
    }
    after["_log_meta"] = namespaces[attack]
    original = deepcopy(after)
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, after, REVISION, REVISION
    ))
    # Assert
    assert (error, after) == (guard.DeliveryGuardError, original)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("key", guard._CLOCKS)
@pytest.mark.parametrize("change", ["inject", "erase", "type"])
def test_generic_completion_clocks_are_presence_and_type_preserved(
    required, key, change
):
    # Arrange
    before = card(required=required)
    values = {"inject": ({}, {key: 1}), "erase": ({key: 1}, {}),
              "type": ({key: 1}, {key: True})}
    old, new = values[change]
    before["_log_meta"].update(old)
    after = deepcopy(before)
    after["_log_meta"].pop(key, None)
    after["_log_meta"].update(new)
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, after, REVISION, None
    ))
    # Assert
    assert error is guard.DeliveryGuardError


@pytest.mark.parametrize("found", [None, True, -1])
def test_protected_write_requires_actual_nonnegative_integer_revision(found):
    # Arrange
    before = card()
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, deepcopy(before), found, None
    ))
    # Assert
    assert error is guard.DeliveryGuardError


def test_protected_stale_revision_refuses_before_policy_write():
    # Arrange
    before = card()
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, deepcopy(before), REVISION, REVISION - 1
    ))
    # Assert
    assert error is RevisionConflictError


def test_legacy_stale_revision_remains_for_existing_lower_cas_contract():
    # Arrange
    before = {"id": "legacy", "status": "in_progress"}
    after = {**before, "title": "Edited", "_log_meta": {"closed_at": True}}
    # Act
    revision = guard._preflight_generic_row_write(before, after, REVISION, 2)
    # Assert
    assert (revision, after["_log_meta"]) == (2, {"closed_at": True})


@pytest.mark.parametrize("field", ["repo", "pr_url", "status"])
def test_generic_retarget_or_reopen_requires_kernel_handoff(field):
    # Arrange
    before = card(status="done")
    after = deepcopy(before)
    after[field] = "in_progress" if field == "status" else "new-target"
    original = deepcopy(after)
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, after, REVISION, REVISION
    ))
    # Assert
    assert (error, after) == (guard.DeliveryGuardError, original)


def test_serialized_claims_cannot_authorize_done():
    # Arrange
    before = card()
    after = deepcopy(before)
    after.update(status="done", by="owner", authenticated=True,
                 operation="complete", hash="claimed", prepared=True)
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, after, REVISION, None
    ))
    # Assert
    assert error is guard.DeliveryGuardError


def test_controlled_reopen_resets_generation_and_clocks_in_one_handoff():
    # Arrange
    before = card(status="done")
    before["_log_meta"].update({key: "old" for key in guard._CLOCKS})
    proposed = deepcopy(before)
    proposed["status"] = "in_progress"
    conn = object()
    # Act
    with guard._prepared_write(
        TARGET, before, proposed, REVISION, operation="reopen"
    ) as after:
        guard._bind_write(TARGET, conn, after, REVISION)
        revision = guard._guard_row_write(conn, before, after, REVISION, REVISION)
        namespace = after["_log_meta"]["delivery"]
        clocks = [key for key in guard._CLOCKS if key in after["_log_meta"]]
    # Assert
    assert (revision, namespace["generation"], namespace["checkpoints"], clocks,
            guard._WRITE_CONTEXT.get()) == (REVISION, 2, {}, [], None)


def test_done_transition_cannot_mint_private_authority_from_claims():
    # Arrange
    before = card()
    proposed = deepcopy(before)
    proposed["status"] = "done"

    def mint():
        with guard._prepared_write(
            TARGET, before, proposed, REVISION, operation="complete"
        ):
            return None

    # Act
    error = refusal(mint)
    # Assert
    assert (error, guard._WRITE_CONTEXT.get()) == (guard.DeliveryGuardError, None)


def test_private_capsule_cannot_be_serialized_as_a_token():
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        error = refusal(lambda: json.dumps(guard._WRITE_CONTEXT.get()))
    # Assert
    assert (error, guard._WRITE_CONTEXT.get()) == (TypeError, None)


@pytest.mark.parametrize("change", ["target", "after", "revision"])
def test_capsule_writer_binding_cannot_change_handoff(change):
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    targets = {"target": "other", "after": TARGET, "revision": TARGET}
    revisions = {"target": REVISION, "after": REVISION, "revision": 999}
    payloads = {"target": after, "after": {**after, "status": "done-again"},
                "revision": after}
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        error = refusal(lambda: guard._bind_write(
            targets[change], object(), payloads[change], revisions[change]
        ))
    # Assert
    assert error is guard.DeliveryGuardError


@pytest.mark.parametrize("change", ["connection", "before", "after", "revision"])
def test_bound_capsule_requires_same_connection_and_exact_snapshots(change):
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    conn = object()
    connections = {"connection": object(), "before": conn, "after": conn,
                   "revision": conn}
    befores = {"connection": before, "before": {**before, "title": "changed"},
               "after": before, "revision": before}
    afters = {"connection": after, "before": after,
              "after": {**after, "title": "changed"}, "revision": after}
    revisions = {"connection": REVISION, "before": REVISION,
                 "after": REVISION, "revision": True}
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        guard._bind_write(TARGET, conn, after, REVISION)
        error = refusal(lambda: guard._guard_row_write(
            connections[change], befores[change], afters[change],
            REVISION, revisions[change]
        ))
    # Assert
    assert error is guard.DeliveryGuardError


def test_fresh_revision_conflict_retains_comment_retry_exception():
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    fresh = {**before, "title": "concurrent winner"}
    conn = object()
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        guard._bind_write(TARGET, conn, after, REVISION)
        error = refusal(lambda: guard._guard_row_write(
            conn, fresh, after, REVISION + 1, REVISION
        ))
    # Assert
    assert error is RevisionConflictError


def test_private_handoff_is_consumed_once():
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    conn = object()
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        guard._bind_write(TARGET, conn, after, REVISION)
        first = guard._guard_row_write(conn, before, after, REVISION, REVISION)
        second = refusal(lambda: guard._guard_row_write(
            conn, before, after, REVISION, REVISION
        ))
    # Assert
    assert (first, second) == (REVISION, guard.DeliveryGuardError)


def test_document_preflight_never_uses_private_comment_authority():
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        error = refusal(lambda: guard._preflight_generic_row_write(
            before, after, REVISION, REVISION
        ))
    # Assert
    assert error is guard.DeliveryGuardError


def test_exact_done_comment_append_does_not_authenticate_completion_claims():
    # Arrange
    before = card(status="done")
    before["_log_meta"].update({key: "retained" for key in guard._CLOCKS})
    after = append_comment(before)
    conn = object()
    # Act
    with guard._comment_write(TARGET, before, after, REVISION) as prepared:
        guard._bind_write(TARGET, conn, prepared, REVISION)
        revision = guard._guard_row_write(conn, before, prepared, REVISION, REVISION)
    # Assert
    assert (revision, prepared["_log_meta"], prepared["status"]) == (
        REVISION, before["_log_meta"], "done"
    )


@pytest.mark.parametrize("field", ["status", "agent", "repo", "_log_meta"])
def test_comment_append_cannot_change_other_card_fields(field):
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    after[field] = {} if field == "_log_meta" else "changed"
    # Act
    error = refusal(lambda: guard._assert_comment_append(before, after))
    # Assert
    assert error is guard.DeliveryGuardError


@pytest.mark.parametrize("attack", ["two", "missing-id", "activity", "old-edit"])
def test_comment_append_must_be_one_stamped_entry_with_unchanged_prefix(attack):
    # Arrange
    before = card()
    before["comments"] = [{"id": "old", "text": "retained"}]
    after = append_comment(before)
    attacks = {
        "two": after["comments"] + [deepcopy(after["comments"][-1])],
        "missing-id": before["comments"] + [{"text": "unstamped"}],
        "activity": after["comments"],
        "old-edit": [{"id": "old", "text": "replaced"}] + after["comments"][1:],
    }
    after["comments"] = attacks[attack]
    activities = {"two": after["last_activity"], "missing-id": after["last_activity"],
                  "activity": True, "old-edit": after["last_activity"]}
    after["last_activity"] = activities[attack]
    # Act
    error = refusal(lambda: guard._assert_comment_append(before, after))
    # Assert
    assert error is guard.DeliveryGuardError


@pytest.mark.parametrize("namespace", [None, {}, {"version": 1, "required": False}])
def test_removal_and_rebuild_refuse_any_present_canonical_namespace(namespace):
    # Arrange
    before = {"id": "protected", "_log_meta": {"delivery": namespace}}
    # Act
    errors = (refusal(lambda: guard._guard_removal(before)),
              refusal(lambda: guard._guard_rebuild([before], [deepcopy(before)])))
    # Assert
    assert errors == (guard.DeliveryGuardError, guard.DeliveryGuardError)


def test_rebuild_cannot_inject_namespace_into_legacy_board():
    # Arrange
    before = {"id": "legacy"}
    incoming = card(required=False)
    # Act
    error = refusal(lambda: guard._guard_rebuild([before], [incoming]))
    # Assert
    assert error is guard.DeliveryGuardError


def test_legacy_rebuild_and_removal_remain_available():
    # Arrange
    before = {"id": "legacy", "status": "done", "_log_meta": {"closed_at": None}}
    # Act
    result = (guard._guard_removal(before), guard._guard_removal(None),
              guard._guard_rebuild([before], [{**before, "title": "retained"}]))
    # Assert
    assert result == (None, None, None)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("key", ["closed_at", "closed_by"])
def test_complete_operations_cannot_authorize_closed_clock_changes(required, key):
    # Arrange
    before = card(required=required)
    proposed = deepcopy(before)
    proposed["status"] = "done"
    proposed["_log_meta"][key] = "forged"
    # Act
    error = refusal(lambda: guard._prepare_existing(
        before, proposed, REVISION, "complete"
    ))
    # Assert
    assert error is guard.DeliveryGuardError


@pytest.mark.parametrize("key", guard._CLOCKS)
def test_typed_optional_update_preserves_all_completion_clocks(key):
    # Arrange
    before = card(required=False)
    proposed = deepcopy(before)
    proposed["_log_meta"][key] = "injected"
    # Act
    error = refusal(lambda: guard._prepare_existing(
        before, proposed, REVISION, "update"
    ))
    # Assert
    assert error is guard.DeliveryGuardError


def test_typed_optional_completion_retains_source_only_completion_stamp_behavior():
    # Arrange
    before = card(required=False)
    proposed = deepcopy(before)
    proposed["status"] = "done"
    proposed["_log_meta"].update(completed_at="server-clock", completed_by="owner")
    # Act
    after = guard._prepare_existing(before, proposed, REVISION, "complete")
    # Assert
    assert after == proposed


@pytest.mark.parametrize("namespace", [None, {}, {"version": 1, "required": False}])
@pytest.mark.parametrize("operation", ["update", "delete", "restore"])
def test_marked_tombstone_creation_refuses_in_every_typed_operation(namespace, operation):
    # Arrange
    before = {"id": "protected", "_log_meta": {"delivery": namespace}}
    proposed = deepcopy(before)
    proposed["_log_meta"]["deleted_at"] = "2026-10-03T01:00:00Z"
    # Act
    error = refusal(lambda: guard._prepare_existing(
        before, proposed, REVISION, operation
    ))
    # Assert
    assert error is guard.DeliveryGuardError


@pytest.mark.parametrize("deleted_after", [None, "", False])
def test_generic_tombstone_revival_requires_typed_restore(deleted_after):
    # Arrange
    before = card()
    before["_log_meta"]["deleted_at"] = "2026-10-03T00:30:00Z"
    proposed = deepcopy(before)
    proposed["_log_meta"]["deleted_at"] = deleted_after
    # Act
    error = refusal(lambda: guard._preflight_generic_row_write(
        before, proposed, REVISION, REVISION
    ))
    # Assert
    assert error is guard.DeliveryGuardError


def test_typed_restore_invalidates_optional_positive_generation():
    # Arrange
    before = card(required=False, status="cancelled")
    before["_log_meta"].update(deleted_at="2026-10-03T00:30:00Z", closed_at="old")
    proposed = deepcopy(before)
    proposed["_log_meta"]["deleted_at"] = None
    proposed["status"] = "in_progress"
    # Act
    after = guard._prepare_existing(before, proposed, REVISION, "restore")
    # Assert
    assert (after["_log_meta"]["delivery"]["generation"],
            after["_log_meta"].get("closed_at")) == (2, None)


def test_private_capsule_explicitly_refuses_pickling():
    # Arrange
    before = card(status="done")
    after = append_comment(before)
    # Act
    with guard._comment_write(TARGET, before, after, REVISION):
        error = refusal(lambda: pickle.dumps(guard._WRITE_CONTEXT.get()))
    # Assert
    assert error is TypeError


def test_capsule_binding_and_consume_refuse_untyped_context_objects():
    # Arrange
    before = card()
    conn = object()
    token = guard._WRITE_CONTEXT.set({"prepared": True})
    # Act
    try:
        errors = (refusal(lambda: guard._bind_write(TARGET, conn, before, REVISION)),
                  refusal(lambda: guard._guard_row_write(
                      conn, before, before, REVISION, REVISION
                  )))
    finally:
        guard._WRITE_CONTEXT.reset(token)
    # Assert
    assert errors == (guard.DeliveryGuardError, guard.DeliveryGuardError)
