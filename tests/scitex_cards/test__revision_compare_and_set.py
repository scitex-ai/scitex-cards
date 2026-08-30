#!/usr/bin/env python3
"""The revision lock, asserted at the ROW-LEVEL write path.

v6 installed ``tasks.revision`` and v7 installed the ``tasks_bump_revision``
trigger, and then NO writer ever compared the column. These tests pin the
compare-and-set that closes that hole, and the two properties that make it safe
to ship into a fleet running mixed versions:

  * a caller that does NOT opt in behaves exactly as before, because
    ``_migrate_v6_to_v7`` already ruled REJECT-by-default unusable ("fleet writes
    would fail until every container is current");
  * a caller that DOES opt in and loses leaves the row untouched, because a
    half-applied write is worse than a refused one.

A LOST RACE IS REPORTED, NOT RAISED. It is the ordinary outcome a reconciler
counts; an exception would make routine concurrency look like a fault.

A real database throughout — ``open_db`` builds the actual schema including the
trigger under test. Mocking the store here would mock away the trigger, which is
the only thing that moves ``revision``.
"""

from functools import partial

import pytest

from scitex_cards import _db
from scitex_cards._db_bootstrap import _insert_tasks


def _card(title="Original"):
    return {"id": "c1", "title": title, "status": "deferred"}


def _revision_of(conn, task_id="c1"):
    row = conn.execute(
        "SELECT revision FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return None if row is None else row[0]


def _title_of(conn, task_id="c1"):
    row = conn.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return None if row is None else row[0]


@pytest.fixture()
def conn(tmp_path):
    connection = _db.open_db(tmp_path / "s.db")
    yield connection
    connection.close()


@pytest.fixture()
def seeded(conn):
    _insert_tasks(conn, [_card()])
    yield conn, _revision_of(conn)


@pytest.fixture()
def contended(seeded):
    """A card that a concurrent writer has already moved past ``stale``."""
    conn, stale = seeded
    _insert_tasks(conn, [_card("Winner")], expected_revision=stale)
    yield conn, stale


def test_a_write_holding_the_current_revision_lands(seeded):
    # Arrange
    conn, revision = seeded

    # Act
    _insert_tasks(conn, [_card("Updated")], expected_revision=revision)

    # Assert
    assert _title_of(conn) == "Updated"


def test_a_write_holding_a_stale_revision_is_reported_skipped(contended):
    # Arrange
    conn, stale = contended

    # Act
    counts = _insert_tasks(conn, [_card("Loser")], expected_revision=stale)

    # Assert
    assert counts["revision_skipped"] == 1


def test_a_skipped_write_does_not_count_as_a_write(contended):
    # Arrange
    conn, stale = contended

    # Act
    counts = _insert_tasks(conn, [_card("Loser")], expected_revision=stale)

    # Assert
    assert counts["tasks"] == 0


def test_a_losing_write_leaves_the_row_untouched(contended):
    # Arrange
    conn, stale = contended

    # Act
    _insert_tasks(conn, [_card("Loser")], expected_revision=stale)

    # Assert
    assert _title_of(conn) == "Winner"


def test_a_skipped_write_reports_the_revision_it_lost_to(contended):
    # Arrange
    conn, stale = contended

    # Act
    counts = _insert_tasks(conn, [_card("Loser")], expected_revision=stale)

    # Assert
    assert counts["revision_found"] == _revision_of(conn)


def test_a_writer_that_does_not_opt_in_still_lands(contended):
    # Arrange — the row has moved, exactly as under fleet traffic.
    conn, _stale = contended

    # Act — every existing caller and every older client looks like this.
    _insert_tasks(conn, [_card("Legacy writer")])

    # Assert — REJECT-by-default was ruled unusable; this must keep working.
    assert _title_of(conn) == "Legacy writer"


def test_compare_and_set_against_an_absent_row_is_skipped(conn):
    # Arrange — no card seeded. `ON CONFLICT ... WHERE` never fires without a
    # conflicting row, so a naive implementation would silently INSERT here.
    absent = 1

    # Act
    counts = _insert_tasks(conn, [_card()], expected_revision=absent)

    # Assert
    assert counts["revision_skipped"] == 1


def test_compare_and_set_against_an_absent_row_creates_nothing(conn):
    # Arrange
    absent = 1

    # Act
    _insert_tasks(conn, [_card()], expected_revision=absent)

    # Assert
    assert _revision_of(conn) is None


def test_compare_and_set_refuses_a_batch(conn):
    # Arrange — a batch cannot report WHICH row lost, so it is misuse, and
    # misuse RAISES where a lost race merely reports. Conflating the two would
    # let a missing capability be tallied as ordinary contention.
    two = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]

    # Act
    attempt = partial(_insert_tasks, conn, two, expected_revision=1)

    # Assert
    with pytest.raises(ValueError):
        attempt()


def test_compare_and_set_refuses_replace_false(conn):
    # Arrange — with replace=False the caller already deleted the rows, so
    # there is no prior revision and the check would be vacuous.
    card = [_card()]

    # Act
    attempt = partial(_insert_tasks, conn, card, replace=False, expected_revision=1)

    # Assert
    with pytest.raises(ValueError):
        attempt()
