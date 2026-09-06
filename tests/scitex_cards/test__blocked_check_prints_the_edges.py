#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The BLOCKED-CHECK line prints the edges that decide the case.

THE RAIL PRINTED IDS AND ASKED THE READER TO REMEMBER THE REST. A card whose
real work lives in its children is, from a list of bare ids, indistinguishable
from a stalled leaf — and that is not hypothetical. scitex-ui judged
``scitex-ui-quality`` "an empty shell" and cancelled it on 08-05 (retracted 20
minutes later), again on 08-23 (retracted 12 minutes later), and wrote a wrong
conclusion about it on 09-04. Each time they read status, blocker,
last_activity and priority, and never ``parent`` or ``depends_on``. Their own
words: "the information was always there; it was not in my query."

So the fix is not a better nudge, it is the FIELD THAT DECIDES THE CASE being
present at the point of decision. A card's liveness is a property of its
edges; its age is a property of the card.

WHY THE CHILD COUNT MUST BE CARRIED, NOT COMPUTED AT RENDER TIME: the line
composer receives one owner's bucket, never the full task list, so it cannot
know how many children a card has. That is the crux of the change — the count
is gathered in the detector, where the whole list is in hand.

A PRINTED "-" IS NOT NOISE, IT IS THE MEASUREMENT. "Looked and found none" and
"never asked" are different states, and only the first is worth trusting; a
column that appears solely on cards WITH edges teaches the reader to skim the
rest, which is the habit that produced the three cancellations above.
"""

import datetime as _dt

from scitex_cards._stale.active import StaleCard, detect_blocked_external
from scitex_cards._stale.active_lines import blocked_external_nudge_line

NOW = _dt.datetime(2026, 9, 6, 5, 43, 0, tzinfo=_dt.timezone.utc)

#: Well past the blocked-check threshold on any clock.
BLOCKED_AT = "2026-08-20T00:00:00Z"

OWNER = "someone"


def _blocked(**extra) -> dict:
    base = {
        "id": "umbrella",
        "title": "umbrella",
        "status": "blocked",
        "blocker": "dependency",
        "agent": OWNER,
        "blocked_at": BLOCKED_AT,
    }
    base.update(extra)
    return base


def test_the_detector_carries_the_parent_onto_the_row():
    # Arrange
    tasks = [_blocked(parent="scitex-quality")]
    # Act
    card = detect_blocked_external(tasks, now=NOW)[OWNER][0]
    # Assert
    assert card.parent == "scitex-quality"


def test_the_detector_carries_the_depends_on_edges_onto_the_row():
    # Arrange
    tasks = [_blocked(depends_on=["a", "b"])]
    # Act
    card = detect_blocked_external(tasks, now=NOW)[OWNER][0]
    # Assert
    assert card.depends_on == ("a", "b")


def test_the_detector_counts_children_from_the_whole_task_list():
    """THE CRUX. The composer sees one owner's bucket; only the detector can
    count children, because only it holds every task."""
    # Arrange — two children owned by SOMEONE ELSE, so the count cannot come
    # from the owner's own bucket.
    tasks = [
        _blocked(),
        {"id": "kid1", "title": "kid1", "status": "deferred", "agent": "other", "parent": "umbrella"},
        {"id": "kid2", "title": "kid2", "status": "done", "agent": "other", "parent": "umbrella"},
    ]
    # Act
    card = detect_blocked_external(tasks, now=NOW)[OWNER][0]
    # Assert
    assert card.children == 2


def test_a_card_with_no_edges_carries_the_empty_defaults():
    # Arrange
    tasks = [_blocked()]
    # Act
    card = detect_blocked_external(tasks, now=NOW)[OWNER][0]
    # Assert
    assert (card.parent, card.depends_on, card.children) == (None, (), 0)


def test_the_line_prints_a_dash_for_a_card_with_no_edges():
    """LOOKED AND FOUND NONE, said out loud."""
    # Arrange
    cards = [StaleCard(id="leaf", title="leaf", status="blocked", age_hours=99.0)]
    # Act
    line = blocked_external_nudge_line(OWNER, cards)
    # Assert
    assert "leaf [parent=- deps=0 children=0]" in line


def test_the_line_prints_the_edges_when_the_card_has_them():
    # Arrange
    cards = [
        StaleCard(
            id="umbrella",
            title="umbrella",
            status="blocked",
            age_hours=99.0,
            parent="scitex-quality",
            depends_on=("a", "b"),
            children=3,
        )
    ]
    # Act
    line = blocked_external_nudge_line(OWNER, cards)
    # Assert
    assert "umbrella [parent=scitex-quality deps=2 children=3]" in line


def test_the_stale_active_line_is_left_byte_identical():
    """THE CAP LIVES IN ONE PLACE AND THE OTHER RAILS MUST NOT MOVE.

    The render seam defaults to today's behaviour precisely so this stays
    true; without the assertion, widening one line silently widens three.
    """
    # Arrange
    from scitex_cards._stale.active_lines import stale_active_nudge_line

    cards = [StaleCard(id="c1", title="c1", status="in_progress", age_hours=99.0)]
    # Act
    line = stale_active_nudge_line(OWNER, cards)
    # Assert
    assert line.endswith("reconcile or update: c1")


def test_the_blocked_line_still_caps_its_id_list():
    """The edge columns must not defeat the cap that bounds the message."""
    # Arrange
    from scitex_cards._stale.active_lines import NUDGE_ID_CAP

    cards = [
        StaleCard(id=f"c{n}", title=f"c{n}", status="blocked", age_hours=99.0)
        for n in range(NUDGE_ID_CAP + 5)
    ]
    # Act
    line = blocked_external_nudge_line(OWNER, cards)
    # Assert
    assert line.endswith("+5 more")


# EOF
