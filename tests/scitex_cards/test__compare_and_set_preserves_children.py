#!/usr/bin/env python3
"""A refused compare-and-set must destroy NOTHING — children included.

`_write_card` deletes a card's comments, roles and outbound edges before
upserting it, because comments key on a sequence and re-inserting without
clearing would duplicate every one of them on every write. That drop is
load-bearing.

It is also destructive, and it sits in front of the revision guard. So the
ordering matters more than the guard itself: check first, or a losing writer
deletes the winner's comments and then reports `revision_skipped` — "I changed
nothing" — with no reason for anyone to look.

The existing compare-and-set tests assert the card's TITLE survives a losing
write. The title lives on the `tasks` row, which the guard genuinely protects.
The comments live in a CHILD table cleared BEFORE it. Testing the row you are
thinking about instead of the blast radius of the operation is exactly how this
class of bug ships.

A real database throughout: the drop, the sequence keying, and the v7 trigger that
moves `revision` are the things under test, and mocking the store would mock
away all three.
"""

import pytest

from scitex_cards import _db
from scitex_cards._db_mirror import _write_card


def _card(title="Original", comments=None):
    return {
        "id": "c1",
        "title": title,
        "status": "deferred",
        "comments": comments if comments is not None else [
            {"author": "someone", "ts": "2026-08-10T00:00:00Z", "text": "first"},
            {"author": "another", "ts": "2026-08-10T00:01:00Z", "text": "second"},
        ],
    }


def _revision_of(conn, task_id="c1"):
    row = conn.execute(
        "SELECT revision FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return None if row is None else row[0]


def _comment_count(conn, task_id="c1"):
    return conn.execute(
        "SELECT COUNT(*) FROM task_comments WHERE task_id = ?", (task_id,)
    ).fetchone()[0]


def _title_of(conn, task_id="c1"):
    row = conn.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return None if row is None else row[0]


@pytest.fixture()
def conn(tmp_path):
    connection = _db.open_db(tmp_path / "s.db")
    yield connection
    connection.close()


@pytest.fixture()
def contended(conn):
    """A card whose revision has already moved past the one a loser holds."""
    _write_card(conn, _card())
    stale = _revision_of(conn)
    _write_card(conn, _card("Winner"), expected_revision=stale)
    yield conn, stale


def test_a_refused_write_leaves_the_comments_intact(contended):
    # Arrange — the winner's card carries two comments.
    conn, stale = contended

    # Act — a loser holding the pre-winner revision.
    _write_card(conn, _card("Loser"), expected_revision=stale)

    # Assert — this is the assertion the earlier suite lacked.
    assert _comment_count(conn) == 2


def test_a_refused_write_leaves_the_title_intact(contended):
    # Arrange
    conn, stale = contended

    # Act
    _write_card(conn, _card("Loser"), expected_revision=stale)

    # Assert
    assert _title_of(conn) == "Winner"


def test_a_refused_write_reports_it_was_skipped(contended):
    # Arrange
    conn, stale = contended

    # Act
    counts = _write_card(conn, _card("Loser"), expected_revision=stale)

    # Assert
    assert counts["revision_skipped"] == 1


def test_a_refused_write_reports_the_revision_it_lost_to(contended):
    # Arrange
    conn, stale = contended

    # Act
    counts = _write_card(conn, _card("Loser"), expected_revision=stale)

    # Assert
    assert counts["revision_found"] == _revision_of(conn)


def test_an_accepted_write_replaces_the_comments_without_duplicating(conn):
    # Arrange — the drop exists precisely to stop comments accumulating.
    _write_card(conn, _card())
    current = _revision_of(conn)

    # Act — rewrite the same card, same two comments.
    _write_card(conn, _card("Updated"), expected_revision=current)

    # Assert — two, not four. This pins the reason the drop is there at all.
    assert _comment_count(conn) == 2


def test_a_write_that_does_not_opt_in_still_replaces_children(conn):
    # Arrange — every existing caller and every older client looks like this.
    _write_card(conn, _card())

    # Act
    _write_card(conn, _card("Legacy writer"))

    # Assert
    assert _comment_count(conn) == 2
