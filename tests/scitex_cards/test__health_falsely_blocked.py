#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card blocked on a finished card is not blocked — it is unstarted.

WHY THIS FILE EXISTS (2026-07-14): the store DOES drive the unblock —
`_store_events._emit_unblock_for_dependents` fires when a card completes and emits
"your task is now unblocked" naming every dependent it freed. But AN EMIT IS A
NOTIFICATION, NOT A MECHANISM: it tells the owner, and nothing enforces that the
owner acts. Measured on the live board: 10 cards across 5 agents sat `blocked` with
every dependency already `done`. The notification had done its job and the field
still lied.

`blocked` is a CLAIM — "something outside my control is stopping me". When the gate
is gone, the claim is false, and a false `blocked` converts "I have not done this"
into "I am PREVENTED from doing this". That is what makes a backlog untouchable.
"""

import os

from conftest import seed_db_from_doc

from scitex_cards._health import _check_no_falsely_blocked


def _store(tmp_path, tasks):
    """Seed the canonical DB from an in-memory task list; return the STORE path.

    The store is SQLite now: the health checks read the canonical DB, not a
    YAML file. Seed the DB the harness pinned + bootstrapped, and return the
    pinned STORE identity path (NOT the DB path — see THE STORE-PATH RULE).
    """
    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


#: The regression itself: the only gate finished, the card still says blocked.
_FALSELY_BLOCKED_TASKS = [
    {"id": "gate", "title": "gate", "status": "done"},
    {
        "id": "waiter",
        "title": "waiting on nothing",
        "status": "blocked",
        "blocker": "dependency",
        "depends_on": ["gate"],
    },
]


def test_blocked_on_a_done_card_is_caught(tmp_path):
    # Arrange
    store = _store(tmp_path, _FALSELY_BLOCKED_TASKS)

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is False


def test_the_falsely_blocked_report_names_the_offending_card(tmp_path):
    # Arrange
    store = _store(tmp_path, _FALSELY_BLOCKED_TASKS)

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert — naming the card is what makes the finding actionable.
    assert "waiter" in result["detail"]


def test_the_falsely_blocked_report_carries_a_hint(tmp_path):
    # Arrange
    store = _store(tmp_path, _FALSELY_BLOCKED_TASKS)

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["hint"]


def test_a_still_open_gate_is_not_flagged(tmp_path):
    """No false positives: a real gate means a real block."""
    # Arrange
    store = _store(
        tmp_path,
        [
            {"id": "gate", "title": "gate", "status": "in_progress"},
            {
                "id": "waiter",
                "title": "genuinely blocked",
                "status": "blocked",
                "blocker": "dependency",
                "depends_on": ["gate"],
            },
        ],
    )

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is True


def test_one_open_gate_among_several_still_blocks(tmp_path):
    """ALL deps must be terminal. One live gate is enough to justify `blocked`."""
    # Arrange
    store = _store(
        tmp_path,
        [
            {"id": "a", "title": "a", "status": "done"},
            {"id": "b", "title": "b", "status": "deferred"},
            {
                "id": "waiter",
                "title": "one gate still open",
                "status": "blocked",
                "blocker": "dependency",
                "depends_on": ["a", "b"],
            },
        ],
    )

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is True


def test_cancelled_and_failed_gates_also_count_as_finished(tmp_path):
    """A gate that was cancelled or failed can no longer gate anything.

    Only `done` would be too narrow: a cancelled dependency is NEVER COMING, so a
    card waiting on it waits forever — the worst false block there is.
    """
    # Arrange
    store = _store(
        tmp_path,
        [
            {"id": "killed", "title": "killed", "status": "cancelled"},
            {"id": "broke", "title": "broke", "status": "failed"},
            {
                "id": "waiter",
                "title": "waiting on the never-coming",
                "status": "blocked",
                "blocker": "dependency",
                "depends_on": ["killed", "broke"],
            },
        ],
    )

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is False


def test_blocked_with_no_depends_on_is_left_alone(tmp_path):
    """A card blocked on a NAMED blocker (operator-decision) names no card.

    This check has no evidence about whether that gate is still real, so it must
    not guess. Flagging these would flood the signal with cards it cannot judge.
    """
    # Arrange
    store = _store(
        tmp_path,
        [
            {
                "id": "waiter",
                "title": "waiting on a human",
                "status": "blocked",
                "blocker": "operator-decision",
            }
        ],
    )

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is True


def test_a_dangling_dependency_is_not_conflated(tmp_path):
    """A dep id that resolves to no card is a DIFFERENT defect.

    Treating "the gate does not exist" as "the gate is finished" would silently
    convert a data-integrity bug into an all-clear.
    """
    # Arrange
    store = _store(
        tmp_path,
        [
            {
                "id": "waiter",
                "title": "points at a ghost",
                "status": "blocked",
                "blocker": "dependency",
                "depends_on": ["no-such-card"],
            }
        ],
    )

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is True


def test_a_non_blocked_card_with_finished_deps_is_fine(tmp_path):
    """Only `blocked` cards make the claim. A deferred one claims nothing."""
    # Arrange
    store = _store(
        tmp_path,
        [
            {"id": "gate", "title": "gate", "status": "done"},
            {
                "id": "waiter",
                "title": "not claiming to be blocked",
                "status": "deferred",
                "depends_on": ["gate"],
            },
        ],
    )

    # Act
    result = _check_no_falsely_blocked(store)

    # Assert
    assert result["ok"] is True


# AN INVARIANT NOBODY RUNS IS NOT AN INVARIANT. The check must appear in
# `health()`'s report, not merely exist as a function. That is exactly how the
# zombie cards survived a guard whose whole job was to catch them.
def _falsely_blocked_health_report(tmp_path):
    from scitex_cards._health import health

    store = _store(tmp_path, _FALSELY_BLOCKED_TASKS)
    return health(store=str(store))


def test_the_check_is_wired_into_the_aggregator(tmp_path):
    # Arrange
    # Act
    report = _falsely_blocked_health_report(tmp_path)

    # Assert
    names = {c["name"] for c in report["checks"]}
    assert "no_falsely_blocked" in names, "the check exists but nothing runs it"


def test_the_aggregated_check_reports_the_falsely_blocked_card(tmp_path):
    # Arrange
    report = _falsely_blocked_health_report(tmp_path)

    # Act
    check = next(c for c in report["checks"] if c["name"] == "no_falsely_blocked")

    # Assert — it is not merely wired in, it actually fires.
    assert check["ok"] is False


def test_the_aggregated_failing_check_carries_a_hint(tmp_path):
    # Arrange
    report = _falsely_blocked_health_report(tmp_path)

    # Act
    check = next(c for c in report["checks"] if c["name"] == "no_falsely_blocked")

    # Assert
    assert check["hint"], "a failing check must carry an actionable hint"
