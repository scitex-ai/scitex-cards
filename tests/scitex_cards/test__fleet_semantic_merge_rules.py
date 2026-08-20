"""The merge rules decide what survives reconciliation — so they get tests.

A wrong rule here does not raise. It silently picks a value and the losing one
is gone, across 5,637 cards at once. These tests exist to make each rule FAIL
when it is wrong, which is the only reason a passing suite means anything.

No mocks (STX-NM002): every case is plain data through the real function.

WHY THE SCRIPT IS IMPORTED BY PATH. `scripts/` is not a package and must not
become one for a one-shot migration tool. importlib gives the tests the real
module without inventing a permanent import surface for it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fleet-semantic-merge.py"


@pytest.fixture(scope="module")
def merge():
    spec = importlib.util.spec_from_file_location("fleet_semantic_merge", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.merge_field


# ── the LATCH: the rule that stops a stale replica resurrecting closed work ──


def test_terminal_status_wins_when_listed_second(merge):
    # Arrange
    values = ["in_progress", "done"]
    # Act
    result = merge("status", values)
    # Assert
    assert result == ("AUTO", "done")


def test_terminal_status_wins_when_listed_first(merge):
    # Arrange: order must not decide anything — there is no clock here.
    values = ["done", "in_progress"]
    # Act
    result = merge("status", values)
    # Assert
    assert result == ("AUTO", "done")


def test_two_terminal_states_are_never_auto_picked(merge):
    # Arrange: done and cancelled are BOTH human decisions, and they disagree.
    values = ["done", "cancelled"]
    # Act
    verdict, _ = merge("status", values)
    # Assert
    assert verdict == "CONFLICT"


def test_blocked_beats_the_unchosen_default(merge):
    # Arrange: `deferred` is what add_task writes when nobody chose.
    values = ["blocked", "deferred"]
    # Act
    result = merge("status", values)
    # Assert
    assert result == ("AUTO", "blocked")


def test_in_progress_beats_the_unchosen_default(merge):
    # Arrange
    values = ["deferred", "in_progress"]
    # Act
    result = merge("status", values)
    # Assert
    assert result == ("AUTO", "in_progress")


def test_two_engaged_statuses_still_need_a_human(merge):
    # Arrange: blocked vs in_progress are two decisions, not a tier gap.
    values = ["blocked", "in_progress"]
    # Act
    verdict, _ = merge("status", values)
    # Assert
    assert verdict == "CONFLICT"


def test_unknown_status_does_not_lose_to_the_default(merge):
    # Arrange: a value this rule has never seen must not rank below `deferred`.
    values = ["deferred", "some_future_state"]
    # Act
    result = merge("status", values)
    # Assert
    assert result == ("AUTO", "some_future_state")


# ── threads: losing a comment loses a record nobody restated ──


def test_comments_union_keeps_every_distinct_element(merge):
    # Arrange
    a = [{"id": "c1", "text": "first"}, {"id": "c2", "text": "second"}]
    b = [{"id": "c2", "text": "second"}, {"id": "c3", "text": "third"}]
    # Act
    _, merged = merge("comments", [a, b])
    # Assert
    assert [c["id"] for c in merged] == ["c1", "c2", "c3"]


def test_comments_union_does_not_duplicate_the_overlap(merge):
    # Arrange
    a = [{"id": "c1", "text": "first"}]
    b = [{"id": "c1", "text": "first"}, {"id": "c2", "text": "second"}]
    # Act
    _, merged = merge("comments", [a, b])
    # Assert
    assert len(merged) == 2


def test_id_less_comments_collapse_by_position_a_known_limit(merge):
    # Arrange: two distinct id-less comments from different stores.
    # Act
    _, merged = merge("comments", [[{"text": "x"}], [{"text": "y"}]])
    # Assert: documents the KNOWN limit rather than pretending it is solved —
    # positional keys collide across stores, so one of these is dropped.
    assert len(merged) == 1


# ── MAX: correct for ISO timestamps, and dangerous anywhere else ──


def test_last_activity_takes_the_latest(merge):
    # Arrange: ISO-8601 Z, where lexicographic order IS chronological.
    values = ["2026-08-19T10:00:00Z", "2026-08-20T09:00:00Z"]
    # Act
    result = merge("last_activity", values)
    # Assert
    assert result == ("AUTO", "2026-08-20T09:00:00Z")


def test_max_is_not_applied_to_free_text(merge):
    # Arrange: if MAX leaked here it would mean "alphabetically largest".
    values = ["zebra", "apple"]
    # Act
    verdict, _ = merge("title", values)
    # Assert
    assert verdict == "CONFLICT"


# ── absence is not disagreement ──


def test_empty_string_loses_to_real_content(merge):
    # Arrange
    values = ["real content", ""]
    # Act
    result = merge("note", values)
    # Assert
    assert result == ("AUTO", "real content")


def test_missing_value_loses_to_real_content(merge):
    # Arrange
    values = [None, "real content"]
    # Act
    result = merge("note", values)
    # Assert
    assert result == ("AUTO", "real content")


def test_two_different_texts_are_a_conflict_not_a_silent_pick(merge):
    # Arrange: the retracted-vs-corrected case, 299 of them on this fleet.
    values = ["RETRACTED: premise false", "original claim"]
    # Act
    verdict, _ = merge("note", values)
    # Assert
    assert verdict == "CONFLICT"


# ── sets: an edge nobody restated must not vanish ──


def test_union_fields_keep_every_element(merge):
    # Arrange
    values = [["a", "b"], ["b", "c"]]
    # Act
    _, merged = merge("depends_on", values)
    # Assert
    assert sorted(merged) == ["a", "b", "c"]


def test_agreement_everywhere_is_not_reported_as_a_change(merge):
    # Arrange
    values = ["same", "same", "same"]
    # Act
    verdict, _ = merge("title", values)
    # Assert
    assert verdict == "IDENTICAL"


def test_present_on_one_store_only_is_filled_in(merge):
    # Arrange
    values = ["https://x/1", None]
    # Act
    result = merge("pr_url", values)
    # Assert
    assert result == ("AUTO", "https://x/1")
