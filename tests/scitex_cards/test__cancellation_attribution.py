"""A cancellation must say who did it — and must not lie about when.

WHY THESE EXIST. On 2026-08-19 an unattributed operation cancelled 844 cards,
including one titled "three live vulns" and 213 at priority 1, leaving no rule,
no comment and no actor. The store keeps no mutation audit, so those are
unattributable by construction. `_stamp_cancellation_attribution` is the narrow
fix; these tests are what make it fail when it stops working.

The transition-only invariant is the load-bearing one. Every clock in
`_store_clocks` is wrong if it fires on a passing mutation instead of a
transition — stamp on any touch and the field starts reporting the last EDIT
rather than the cancellation, which is exactly the confusion it was added to
end.

No mocks (STX-NM002): the helper is pure, so these are plain dicts in and out.
"""

from __future__ import annotations

import pytest

from scitex_cards._store_clocks import (
    FIELD_CANCELLED_AT,
    FIELD_CANCELLED_BY,
    UNRESOLVED_ACTOR,
    _stamp_cancellation_attribution,
)


@pytest.fixture
def cancelled_card() -> dict:
    """A card that has just been moved into `cancelled`."""
    return {"id": "c1", "status": "cancelled"}


# ── entering cancelled: record who and when ──────────────────────────────────


def test_entering_cancelled_records_the_actor(cancelled_card):
    # Arrange
    prior = "in_progress"
    # Act
    _stamp_cancellation_attribution(cancelled_card, prior, "scitex-dev")
    # Assert
    assert cancelled_card[FIELD_CANCELLED_BY] == "scitex-dev"


def test_entering_cancelled_records_a_timestamp(cancelled_card):
    # Arrange
    prior = "in_progress"
    # Act
    _stamp_cancellation_attribution(cancelled_card, prior, "scitex-dev")
    # Assert
    assert cancelled_card[FIELD_CANCELLED_AT].endswith("Z")


# ── the unresolvable actor is WRITTEN DOWN, never omitted ────────────────────


def test_unresolvable_actor_is_recorded_not_omitted(cancelled_card):
    # Arrange: a supervisor process with no agent id in its environment — the
    # case that produced "creator unresolved" 347 times on 2026-08-20.
    # Act
    _stamp_cancellation_attribution(cancelled_card, "deferred", None)
    # Assert
    assert cancelled_card[FIELD_CANCELLED_BY] == UNRESOLVED_ACTOR


def test_blank_actor_is_recorded_not_omitted(cancelled_card):
    # Arrange: an env var set to empty is the same fact as one not set.
    # Act
    _stamp_cancellation_attribution(cancelled_card, "deferred", "   ")
    # Assert
    assert cancelled_card[FIELD_CANCELLED_BY] == UNRESOLVED_ACTOR


def test_unresolvable_actor_still_gets_a_timestamp(cancelled_card):
    # Arrange: an unsigned cancellation is still a dated one.
    # Act
    _stamp_cancellation_attribution(cancelled_card, "deferred", None)
    # Assert
    assert FIELD_CANCELLED_AT in cancelled_card


# ── the transition-only invariant: this module's shared failure mode ─────────


def test_a_passing_edit_does_not_restamp_the_time(cancelled_card):
    # Arrange: already cancelled, someone edits the card (a comment, a retitle).
    cancelled_card[FIELD_CANCELLED_AT] = "2026-08-19T23:13:00Z"
    cancelled_card[FIELD_CANCELLED_BY] = "someone-else"
    # Act
    _stamp_cancellation_attribution(cancelled_card, "cancelled", "a-later-editor")
    # Assert: the ORIGINAL cancellation time survives.
    assert cancelled_card[FIELD_CANCELLED_AT] == "2026-08-19T23:13:00Z"


def test_a_passing_edit_does_not_reattribute_the_actor(cancelled_card):
    # Arrange: the editor is not the canceller, and must not become them.
    cancelled_card[FIELD_CANCELLED_AT] = "2026-08-19T23:13:00Z"
    cancelled_card[FIELD_CANCELLED_BY] = "someone-else"
    # Act
    _stamp_cancellation_attribution(cancelled_card, "cancelled", "a-later-editor")
    # Assert
    assert cancelled_card[FIELD_CANCELLED_BY] == "someone-else"


def test_a_non_cancelling_transition_stamps_nothing():
    # Arrange: deferred -> in_progress has nothing to do with cancellation.
    task = {"id": "c1", "status": "in_progress"}
    # Act
    _stamp_cancellation_attribution(task, "deferred", "scitex-cards")
    # Assert
    assert FIELD_CANCELLED_BY not in task


# ── leaving cancelled: both stamps go, together ──────────────────────────────


def test_leaving_cancelled_clears_the_actor():
    # Arrange: a card being reopened out of cancelled.
    task = {
        "id": "c1",
        "status": "in_progress",
        FIELD_CANCELLED_AT: "2026-08-19T23:13:00Z",
        FIELD_CANCELLED_BY: "someone",
    }
    # Act
    _stamp_cancellation_attribution(task, "cancelled", "scitex-dev")
    # Assert
    assert FIELD_CANCELLED_BY not in task


def test_leaving_cancelled_clears_the_timestamp():
    # Arrange: a stale cancellation date on a live card is worse than none —
    # it reads as freshly stamped while describing a cancellation that was
    # undone.
    task = {
        "id": "c1",
        "status": "in_progress",
        FIELD_CANCELLED_AT: "2026-08-19T23:13:00Z",
        FIELD_CANCELLED_BY: "someone",
    }
    # Act
    _stamp_cancellation_attribution(task, "cancelled", "scitex-dev")
    # Assert
    assert FIELD_CANCELLED_AT not in task


def test_recancelling_after_a_reopen_records_the_new_actor():
    # Arrange: the whole reason the stamps clear on exit — a second
    # cancellation must not inherit the first one's signature.
    task = {"id": "c1", "status": "cancelled"}
    # Act
    _stamp_cancellation_attribution(task, "in_progress", "second-canceller")
    # Assert
    assert task[FIELD_CANCELLED_BY] == "second-canceller"


def test_leaving_cancelled_does_not_crash_on_an_unstamped_card():
    # Arrange: every card cancelled before this shipped carries no stamps.
    task = {"id": "c1", "status": "deferred"}
    # Act
    _stamp_cancellation_attribution(task, "cancelled", "scitex-cards")
    # Assert
    assert task["status"] == "deferred"


# ── IS IT ACTUALLY WIRED IN? ────────────────────────────────────────────────
#
# Everything above exercises the helper DIRECTLY, so every one of those tests
# would pass identically if the call site in `update_task` were deleted, or
# passed its arguments in the wrong order, or threw. That is the shape this
# repo keeps shipping — `_clear_completion_stamp_on_leaving_done` had a correct
# implementation and ONE caller for weeks while its docstring claimed
# otherwise, and 10 cards were miscounted as delivered work because of it.
#
# So: one source check (the house pattern, catches deletion) and one REAL write
# through the store (catches a call that is present but broken, which a source
# grep cannot).


def test_update_task_actually_calls_the_cancellation_stamp():
    # Arrange — the positive control: assert the WIRING, not the outcome.
    import inspect

    from scitex_cards import _store_mutate

    # Act
    source = inspect.getsource(_store_mutate.update_task)
    # Assert
    assert "_stamp_cancellation_attribution" in source


def test_a_real_cancel_through_update_task_records_an_actor():
    # Arrange — the end-to-end check a source grep cannot give: drive the real
    # write path against the ambient test store.
    from scitex_cards._store import add_task, get_task, update_task

    add_task(id="cancel-e2e-1", title="a card to cancel", status="deferred",
             assignee="scitex-cards", created_by="scitex-cards")
    # Act
    update_task(None, "cancel-e2e-1", status="cancelled")
    # Assert — WHATEVER the environment, the field is populated. A resolvable
    # agent gives its id, an unresolvable one gives the sentinel; the invariant
    # that fixes the 844 is that neither leaves it ABSENT.
    assert get_task(None, "cancel-e2e-1").get(FIELD_CANCELLED_BY)


def test_a_real_cancel_through_update_task_records_a_timestamp():
    # Arrange
    from scitex_cards._store import add_task, get_task, update_task

    add_task(id="cancel-e2e-2", title="a card to cancel", status="deferred",
             assignee="scitex-cards", created_by="scitex-cards")
    # Act
    update_task(None, "cancel-e2e-2", status="cancelled")
    # Assert
    assert get_task(None, "cancel-e2e-2").get(FIELD_CANCELLED_AT)


def test_a_real_reopen_through_update_task_clears_the_attribution():
    # Arrange — a stale signature on a live card is the failure this prevents.
    from scitex_cards._store import add_task, get_task, update_task

    add_task(id="cancel-e2e-3", title="a card to cancel", status="deferred",
             assignee="scitex-cards", created_by="scitex-cards")
    update_task(None, "cancel-e2e-3", status="cancelled")
    # Act
    update_task(None, "cancel-e2e-3", status="in_progress")
    # Assert
    assert FIELD_CANCELLED_BY not in get_task(None, "cancel-e2e-3")


def test_a_passing_edit_on_a_real_cancelled_card_keeps_the_original_actor():
    # Arrange — the transition-only invariant, end to end rather than in a dict.
    from scitex_cards._store import add_task, get_task, update_task

    add_task(id="cancel-e2e-4", title="a card to cancel", status="deferred",
             assignee="scitex-cards", created_by="scitex-cards")
    update_task(None, "cancel-e2e-4", status="cancelled")
    first = get_task(None, "cancel-e2e-4")[FIELD_CANCELLED_AT]
    # Act — retitle it; the cancellation did not happen again.
    update_task(None, "cancel-e2e-4", title="renamed after cancellation")
    # Assert
    assert get_task(None, "cancel-e2e-4")[FIELD_CANCELLED_AT] == first
