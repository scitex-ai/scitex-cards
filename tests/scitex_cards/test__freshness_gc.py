#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card forgetting: the predicate, the flip, and what must survive it.

WHAT IS PINNED. A freshness sweep is the one operation in this package that
disposes of MANY cards at once, so the tests are less about "does it cancel"
and more about the three ways a bulk flip can be wrong:

  1. IT TAKES TOO MUCH. A card whose clock is *newer* than the cutoff, or whose
     status is terminal already, must not be touched — and `blocked` is the
     interesting one, because flipping it is only honest if the flip also
     CLEARS THE BLOCKER. A cancelled card still claiming to wait on something
     is a store that disagrees with itself.
  2. IT DISAGREES WITH ITSELF. Cards are read from the `card_json` payload and
     filtered by the `status` COLUMN, so a flip that updates one and not the
     other produces a store where the same card is cancelled to a query and
     in_progress to a reader.
  3. IT CANNOT RUN TWICE. `remaining` is the readback that proves the predicate
     now selects nothing; a second sweep must be a no-op rather than a second
     wave through whatever became stale in between.

IT ALSO PINS WHAT SURVIVES: rows and history. A forgotten card stops being
work; it does not stop being a record. The tests assert the row is still there
and still readable, and that no comment was appended to it — the operator ruled
per-card comments out, and 1,267 of them would be 1,267 writes to a document the
whole fleet reads.

LOCALLY THE STORE-BACKED TESTS ERROR, by design: the seed needs
`$SCITEX_STORE_DSN`, which exists only when a throwaway cluster is resolvable
(in CI). A skipped gate and a passing one look identical, so they error instead
of pretending. The clock tests are hermetic and run anywhere.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._store_freshness_gc import (
    FORGETTABLE_STATUSES,
    cutoff_from_days,
    freshness_gc,
)

#: Fixed instant for the deterministic clock tests — 2026-09-17T12:00:00Z.
_NOW = 1_789_646_400.0

#: Two of everything that matters: old/forgettable (must go), old/terminal
#: (must stay), fresh (must stay), and one with an EMPTY `last_activity` so the
#: `created_at` fallback is exercised rather than assumed. `blocker` is present
#: on the blocked card because clearing it is half of the flip.
_DOC = {
    "tasks": [
        {
            "id": "old-goal",
            "title": "An old goal",
            "status": "goal",
            "assignee": "agent:test-suite",
            "last_activity": "2026-01-01T00:00:00Z",
        },
        {
            "id": "old-blocked",
            "title": "An old blocked card",
            "status": "blocked",
            "blocker": "dependency",
            "assignee": "agent:test-suite",
            "last_activity": "2026-02-01T00:00:00Z",
        },
        {
            "id": "old-done",
            "title": "An old card that is DONE",
            "status": "done",
            "assignee": "agent:test-suite",
            "last_activity": "2026-01-01T00:00:00Z",
        },
        {
            "id": "fresh",
            "title": "Touched today",
            "status": "in_progress",
            "assignee": "agent:test-suite",
            "last_activity": "2026-09-17T00:30:00Z",
        },
        {
            "id": "no-last-activity",
            "title": "Only ever created",
            "status": "deferred",
            "assignee": "agent:test-suite",
            "last_activity": "",
            "created_at": "2026-03-01T00:00:00Z",
        },
    ]
}

#: The cutoff these tests sweep with: after every "old" card, before "fresh".
_CUTOFF = "2026-06-01T00:00:00Z"

#: The ids that a sweep at `_CUTOFF` must forget, and the ones it must not.
_MUST_FORGET = {"old-goal", "old-blocked", "no-last-activity"}
_MUST_KEEP = {"old-done", "fresh"}


@pytest.fixture
def store():
    """Seed a throwaway store this test may WRITE to; never the fleet's.

    This is the instrument the sweep's first smoke test should have used, and
    the reason the incident of 2026-09-17 happened: a verb whose purpose is to
    dispose of cards must be exercised against a store nobody owns.
    """
    from conftest import seed_db_from_doc

    seed_db_from_doc(_DOC, os.environ["SCITEX_STORE_DSN"])
    yield os.environ["SCITEX_STORE_DSN"]


def _statuses() -> dict[str, str]:
    from scitex_cards._db_export import export_doc

    doc, _threads = export_doc()
    return {t.get("id"): t.get("status") for t in doc["tasks"]}


# ── the clock, deterministically ────────────────────────────────────────────


def test_the_cutoff_is_exactly_n_days_before_a_pinned_now():
    """The org-wide default is a NUMBER; this is where it becomes an instant."""
    # Arrange
    expected = "2026-08-18T12:00:00+00:00"
    # Act
    got = cutoff_from_days(30, now=_NOW)
    # Assert
    assert got == expected


def test_zero_days_means_the_pinned_now():
    """A boundary the sweep's own predicate turns on."""
    # Arrange
    expected = "2026-09-17T12:00:00+00:00"
    # Act
    got = cutoff_from_days(0, now=_NOW)
    # Assert
    assert got == expected


def test_a_negative_number_of_days_is_refused():
    """A negative horizon would sweep the FUTURE, which is never intended."""
    # Arrange
    negative = -1
    # Act
    cut = cutoff_from_days
    # Assert
    with pytest.raises(ValueError):
        cut(negative, now=_NOW)


def test_every_forgettable_status_is_the_documented_set():
    """The set is an interface (scitex-dev passes a cutoff against it)."""
    # Arrange
    expected = ("goal", "in_progress", "blocked", "deferred")
    # Act
    observed = tuple(FORGETTABLE_STATUSES)
    # Assert
    assert observed == expected


# ── the sweep, against a real store ─────────────────────────────────────────


def test_a_sweep_forgets_exactly_the_stale_forgettable_cards(store):
    """Counts first: matched == cancelled == the cards that SHOULD go."""
    # Arrange
    # (the store fixture seeded _DOC)
    # Act
    result = freshness_gc(cutoff=_CUTOFF)
    # Assert
    assert (result.matched, result.cancelled, result.remaining) == (3, 3, 0)


def test_the_sweep_leaves_terminal_and_fresh_cards_alone(store):
    """The half that matters more: what a bulk flip must NOT reach."""
    # Arrange
    before = {"old-done": "done", "fresh": "in_progress"}
    # Act
    freshness_gc(cutoff=_CUTOFF)
    after = _statuses()
    # Assert
    assert {k: after.get(k) for k in before} == before


def test_a_forgotten_card_is_cancelled_and_its_blocker_is_cleared(store):
    """`blocked` is forgettable only because the flip clears the blocker."""
    # Arrange
    from scitex_cards._db_export import export_doc

    # Act
    freshness_gc(cutoff=_CUTOFF)
    cards = {t.get("id"): t for t in export_doc()[0]["tasks"]}
    blocked = cards["old-blocked"]
    # Assert
    assert (blocked.get("status"), blocked.get("blocker")) == ("cancelled", None)


def test_the_payload_and_the_status_column_agree_after_a_flip(store):
    """A store that filters on the column and reads the payload cannot disagree."""
    # Arrange
    import os as _os

    import psycopg

    # Act
    freshness_gc(cutoff=_CUTOFF)
    conn = psycopg.connect(_os.environ["SCITEX_STORE_DSN"])
    rows = conn.execute(
        "SELECT status, card_json::jsonb->>'status' FROM tasks WHERE id = ANY(%s)",
        (list(_MUST_FORGET),),
    ).fetchall()
    # Assert
    assert all(row[0] == row[1] == "cancelled" for row in rows) and len(rows) == 3


def test_a_dry_run_reports_the_same_count_and_changes_nothing(store):
    """The rehearsal must be the real run's count, or it is a guess."""
    # Arrange
    before = _statuses()
    # Act
    result = freshness_gc(cutoff=_CUTOFF, dry_run=True)
    # Assert
    assert (result.matched, result.cancelled, _statuses() == before) == (3, 0, True)


def test_a_second_sweep_is_a_no_op(store):
    """`remaining` is what makes the first sweep's claim checkable."""
    # Arrange
    freshness_gc(cutoff=_CUTOFF)
    # Act
    second = freshness_gc(cutoff=_CUTOFF)
    # Assert
    assert (second.matched, second.cancelled, second.remaining) == (0, 0, 0)


def test_a_forgotten_card_is_still_a_row_with_its_history(store):
    """Forgetting removes work, not records — and adds no per-card comment."""
    # Arrange
    freshness_gc(cutoff=_CUTOFF)
    # Act
    from scitex_cards._db_export import export_doc

    cards = {t.get("id"): t for t in export_doc()[0]["tasks"]}
    forgotten = cards["old-goal"]
    # Assert
    assert forgotten["id"] == "old-goal" and not forgotten.get("comments")


# EOF
