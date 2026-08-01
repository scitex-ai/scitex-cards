#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`rescore_history` is what lets comments[] leave the /graph payload.

The four scalars in `test__comment_digest.py` cover every list surface
except one: the Matrix view reads comment CONTENT, walking each card's
thread for `kind == "rescore"` and taking the [old, new] axis pairs to
draw quadrant transitions (`board_v3/14-matrix.js:198`). Delete
comments[] without serving those and the Matrix silently renders "now"
only — no error, no empty state, just a chart that quietly stopped
being a history.

Two properties carry the weight:

`test_order_is_preserved` — the view assigns an insertion index as its
stable tiebreak for events sharing a timestamp, so re-ordering here
changes which transition wins a tie.

`test_no_comment_prose_is_carried` — the reason this field is 0.11% of
comments[] rather than a rename of it. If author/text ride along, the
payload never shrinks and the whole exercise is theatre.
"""

from __future__ import annotations

from scitex_cards._django.handlers._comment_digest import rescore_history


def _rescore(ts="2026-07-01T00:00:00Z", urgency=(1, 4), importance=(2, 5)):
    """A rescore comment shaped as the store writes it."""
    return {
        "ts": ts,
        "author": "operator",
        "text": "urgency 1 -> 4, importance 2 -> 5",
        "kind": "rescore",
        "rescore": {"urgency": list(urgency), "importance": list(importance)},
    }


def test_rescore_comment_is_returned():
    """The event the Matrix needs survives the projection."""
    # Arrange
    task = {"comments": [_rescore()]}

    # Act
    got = rescore_history(task)

    # Assert
    assert len(got) == 1


def test_axis_pairs_survive_verbatim():
    """`rescore.urgency[0]` — the OLD half — is what draws `fromQ`."""
    # Arrange
    task = {"comments": [_rescore(urgency=(1, 4))]}

    # Act
    got = rescore_history(task)

    # Assert
    assert got[0]["rescore"]["urgency"][0] == 1


def test_timestamp_is_carried():
    """Transitions are plotted over time; the ts is not optional."""
    # Arrange
    task = {"comments": [_rescore(ts="2026-07-09T12:00:00Z")]}

    # Act
    got = rescore_history(task)

    # Assert
    assert got[0]["ts"] == "2026-07-09T12:00:00Z"


def test_no_comment_prose_is_carried():
    """Only {ts, rescore} — carrying author/text would defeat the point."""
    # Arrange
    task = {"comments": [_rescore()]}

    # Act
    got = rescore_history(task)

    # Assert
    assert set(got[0]) == {"ts", "rescore"}


def test_ordinary_comments_are_excluded():
    """A thread of prose contributes no events."""
    # Arrange
    task = {"comments": [{"ts": "t", "author": "a", "text": "hello"}]}

    # Act
    got = rescore_history(task)

    # Assert
    assert got == []


def test_other_kinds_are_excluded():
    """`kind` is a closed discriminator; only rescores are rescores."""
    # Arrange
    task = {"comments": [{"ts": "t", "kind": "reassigned", "rescore": {}}]}

    # Act
    got = rescore_history(task)

    # Assert
    assert got == []


def test_order_is_preserved():
    """Insertion order IS the same-ts tiebreak — see the module docstring."""
    # Arrange
    same_ts = "2026-07-01T00:00:00Z"
    first = _rescore(ts=same_ts, urgency=(1, 2))
    second = _rescore(ts=same_ts, urgency=(3, 4))
    task = {"comments": [first, second]}

    # Act
    got = rescore_history(task)

    # Assert
    assert [e["rescore"]["urgency"][0] for e in got] == [1, 3]


def test_absent_comments_yields_empty_list():
    """`[]` not `None`, so the client iterates without a null check."""
    # Arrange
    task = {"id": "x"}

    # Act
    got = rescore_history(task)

    # Assert
    assert got == []


def test_non_list_comments_yields_empty_list():
    """A malformed store must not blank the operator's whole board."""
    # Arrange
    task = {"comments": "not-a-list"}

    # Act
    got = rescore_history(task)

    # Assert
    assert got == []


def test_rescore_with_non_dict_payload_is_skipped():
    """A `kind: rescore` whose payload is junk is dropped, not dereferenced."""
    # Arrange
    task = {"comments": [{"ts": "t", "kind": "rescore", "rescore": "junk"}]}

    # Act
    got = rescore_history(task)

    # Assert
    assert got == []


def test_rescore_missing_payload_is_skipped():
    """The discriminator alone is not enough; the axes must be there."""
    # Arrange
    task = {"comments": [{"ts": "t", "kind": "rescore"}]}

    # Act
    got = rescore_history(task)

    # Assert
    assert got == []


def test_non_dict_entries_are_skipped():
    """Junk entries are dropped rather than counted or dereferenced."""
    # Arrange
    task = {"comments": [_rescore(), "junk", None, 42]}

    # Act
    got = rescore_history(task)

    # Assert
    assert len(got) == 1


# EOF
