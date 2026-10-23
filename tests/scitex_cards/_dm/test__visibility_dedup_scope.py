"""Visibility dedup must be seq-scoped, not action-scoped (PG-free pin).

Release gate 2026-10-23 (PR #1139, all three legs):
``test_latest_event_wins_the_fold`` wrote hide→unhide→hide inside one
second and read back ``set()``. Mechanism: the derived event id digests
``(thread, reader, action, ts)`` — second-resolution ts means the second
hide recomputes the FIRST hide's id, and ``ON CONFLICT DO NOTHING``
swallows it. The fold then sees hide,unhide and answers visible.

The fix contract pinned here (PG-free, runs in this container):

1. the derivation takes the per-(thread,reader) ``seq`` and drops
   ``action`` — order comes from the counter, not from the id hash;
2. the same ``(thread, reader, ts, seq)`` still derives the same id, so
   a genuine retry of one event still deduplicates.
"""

from __future__ import annotations

from scitex_cards._dm.ids import derived_visibility_event_id


def test_same_second_retoggle_yields_distinct_ids() -> None:
    """One id per event, not per (action, second): the fold can order them."""
    # Arrange
    ts = "2026-10-23T10:00:00Z"
    # Act
    first_hide = derived_visibility_event_id("dm:a::b", "operator", ts, 1)
    unhide = derived_visibility_event_id("dm:a::b", "operator", ts, 2)
    second_hide = derived_visibility_event_id("dm:a::b", "operator", ts, 3)
    # Assert
    assert len({first_hide, unhide, second_hide}) == 3, (
        "same-second retoggles must not share derived ids — "
        "ON CONFLICT would swallow the later event"
    )


def test_same_event_inputs_still_deduplicate() -> None:
    """Determinism preserved: retrying one event maps to one row."""
    # Arrange
    # Act
    again = derived_visibility_event_id(
        "dm:a::b", "operator", "2026-10-23T10:00:00Z", 1
    )
    # Assert
    assert again == derived_visibility_event_id(
        "dm:a::b", "operator", "2026-10-23T10:00:00Z", 1
    ), "same event must derive the same id or retries double-insert"
