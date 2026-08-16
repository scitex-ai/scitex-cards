#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card that leaves ``done`` must stop counting as delivered work.

REGRESSION FOR A MEASURED DEFECT (2026-08-16, traced by scitex-dev from
scitex-hpc's `terminal_state_honest` health failure). ``clear_completion_stamp``
had exactly ONE production caller — ``reopen_task`` — while its own docstring
said "Call this from ANY transition that takes a card OUT of ``done``". Every
other exit went through ``update_task``, which never called it.

WHY THE STAMP MATTERS MORE THAN THE STATUS. ``_django/handlers/fleet/timing.py``
and ``_django/handlers/timeline.py`` aggregate throughput SOLELY on
``completed_at`` and never read ``status``. So a stamped-open card is counted as
delivered work forever WHILE ALSO nagging its owner as backlog: one card, two
contradictory facts, two readers. 5 such cards existed on 2026-07-14; 10 on
2026-08-16.

WHY ``reopen_task`` COULD NOT HAVE COVERED THIS. It forces ``status=blocked``
and ``blocker=operator-decision``. That is simply wrong for a card being
deferred or cancelled — so the honest transitions were precisely the ones that
could not use the only path that unstamped.

POSITIVE CONTROL, and it is the point of this module rather than a footnote:
:func:`test_update_task_actually_calls_the_clock` asserts the helper is WIRED
INTO the write path, by checking the call site rather than the outcome. The
outcome tests exercise the helper directly, so they would pass identically if
someone later deleted the call — which is the shape this repo keeps shipping.
"""

from __future__ import annotations

import pytest

from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done


@pytest.fixture()
def done_card() -> dict:
    """A card in the exact shape the defect produces: done, and stamped."""
    return {
        "id": "c1",
        "status": "done",
        "_log_meta": {
            "completed_at": "2026-08-16T01:00:00Z",
            "completed_by": "someone",
        },
    }


def test_deferring_a_done_card_reports_the_stamp_was_cleared(done_card):
    # Arrange — the transition the defect missed: done -> deferred.
    done_card["status"] = "deferred"
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(done_card, "done")
    # Assert
    assert cleared is True


def test_deferring_a_done_card_removes_completed_at(done_card):
    # Arrange — completed_at is what the throughput surfaces aggregate on.
    done_card["status"] = "deferred"
    # Act
    _clear_completion_stamp_on_leaving_done(done_card, "done")
    # Assert
    assert "completed_at" not in done_card.get("_log_meta", {})


def test_deferring_a_done_card_removes_completed_by(done_card):
    # Arrange
    done_card["status"] = "deferred"
    # Act
    _clear_completion_stamp_on_leaving_done(done_card, "done")
    # Assert
    assert "completed_by" not in done_card.get("_log_meta", {})


def test_cancelling_a_done_card_removes_completed_at(done_card):
    # Arrange — `cancelled` is terminal but is NOT a completion; a card can
    # stop without having shipped, and throughput must not conflate the two.
    done_card["status"] = "cancelled"
    # Act — prior status is `done`; the card is cancelled NOW.
    _clear_completion_stamp_on_leaving_done(done_card, "done")
    # Assert
    assert "completed_at" not in done_card.get("_log_meta", {})


def test_a_card_still_done_keeps_its_completed_at(done_card):
    # Arrange — a passing edit that does NOT change status is not an exit.
    expected = "2026-08-16T01:00:00Z"
    # Act
    _clear_completion_stamp_on_leaving_done(done_card, "done")
    # Assert
    assert done_card["_log_meta"]["completed_at"] == expected


def test_a_card_still_done_reports_nothing_cleared(done_card):
    # Arrange
    expected = False
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(done_card, "done")
    # Assert
    assert cleared is expected


def test_a_card_that_was_never_done_keeps_its_other_lifecycle_keys():
    # Arrange — deferred_at belongs to another clock and is not ours to remove.
    task = {"id": "c2", "status": "blocked", "_log_meta": {"deferred_at": "x"}}
    # Act
    _clear_completion_stamp_on_leaving_done(task, "deferred")
    # Assert
    assert task["_log_meta"] == {"deferred_at": "x"}


def test_update_task_actually_calls_the_clock():
    # Arrange — THE POSITIVE CONTROL. The defect was never a wrong result; it
    # was a helper nobody called. So assert the WIRING at the one place a
    # status change is applied. Delete the call and this goes red, which the
    # outcome tests above would not.
    import inspect

    from scitex_cards import _store_mutate

    # Act
    source = inspect.getsource(_store_mutate.update_task)
    # Assert
    assert "_clear_completion_stamp_on_leaving_done" in source


def test_the_clocks_are_still_importable_from_their_old_home():
    # Arrange — the extraction must not break existing importers.
    from scitex_cards import _store_mutate

    # Act
    present = [
        hasattr(_store_mutate, "_stamp_deferred_at"),
        hasattr(_store_mutate, "_stamp_blocked_at"),
    ]
    # Assert
    assert all(present)


# EOF
