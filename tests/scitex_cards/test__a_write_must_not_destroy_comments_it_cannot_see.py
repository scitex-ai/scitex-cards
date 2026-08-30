#!/usr/bin/env python3
"""A card write must not delete comment rows the document has never heard of.

REPRODUCED ON THE LIVE STORE 2026-08-23, on a disposable card, before this
test existed::

    card_json=1 / task_comments=2  ->  one ordinary update_task  ->  =1

The row only the TABLE knew about was gone, permanently, with no error and a
success report. `_write_card` drops a card's comment rows and re-inserts from
the DOC, and the doc is assembled from `card_json` — so the write's INPUT is
the denormalised copy while its EFFECT is on the table.

THIS IS THE STEADY STATE FOR ANY CROSS-HOST CARD, not a rare race.
`_copy_comments` delivers rows to a peer keyed on `(task_id, seq)`, while
`_copy_tasks` computes `src_ids - dst_ids` and never re-SELECTs an existing
card row — so the receiving host's `card_json` never learns the comment
arrived. Measured the same day on compute-04: 259 cards holding 808 such
comments, every one armed to vanish on the next local write. scitex-hub
confirmed the other half from the writer's side: their `card_json` showed all 8
comments while the receiver's showed 0, so the only agent positioned to notice
is structurally the one who cannot.

A real database throughout, like its sibling `test__compare_and_set_preserves_
children.py`: the drop, the sequence keying and the re-insert are the things
under test, and mocking the store would mock away all three.
"""

import pytest

from scitex_cards import _db
from scitex_cards._mirror_rows import _merge_unseen_comment_rows, _write_card

VISIBLE = {"author": "me", "ts": "2026-08-23T00:00:00Z", "text": "in the doc"}
#: A comment that reached this host by sync: present in `task_comments`,
#: absent from `card_json`. Modelled by writing it and then handing `_write_card`
#: a doc that omits it — which is exactly the state the syncer leaves behind.
SYNCED = {"author": "peer", "ts": "2026-08-23T00:01:00Z", "text": "arrived by sync"}


def _card(comments):
    return {"id": "c1", "title": "T", "status": "deferred", "comments": comments}


# ROWS ARE NAME-ADDRESSABLE, NOT POSITIONAL. These helpers read `row[0]`,
# which worked only because `sqlite3.Row` accepts BOTH an index and a name.
# The store speaks to a server now and its rows are mapping-shaped, so an
# integer subscript raises `KeyError: 0`. Reading by name is what the
# package itself does throughout -- and it is the same defect that made
# every commented card read-only fleet-wide on 2026-08-23, which
# .github/workflows/postgres-backend-on-ubuntu-latest.yml names in its
# header. A COUNT needs an explicit alias to have a name at all.
def _texts(conn, task_id="c1"):
    return [
        r["text"]
        for r in conn.execute(
            "SELECT text FROM task_comments WHERE task_id = ? ORDER BY seq",
            (task_id,),
        ).fetchall()
    ]


@pytest.fixture()
def conn(tmp_path, new_store):
    connection = _db.open_db(new_store())
    yield connection
    connection.close()


@pytest.fixture()
def diverged(conn):
    """A card whose table holds a comment its document does not list."""
    _write_card(conn, _card([VISIBLE, SYNCED]))
    yield conn


def test_the_fixture_really_diverges(diverged):
    # Arrange — CALIBRATION. Every test below is vacuous if the table and the
    # doc we are about to pass already agree.
    doc = _card([VISIBLE])
    # Act
    recovered = _merge_unseen_comment_rows(diverged, "c1", doc)
    # Assert
    assert recovered == 1


def test_a_row_only_the_table_knows_survives_a_write(diverged):
    # Arrange — the doc omits SYNCED, exactly as card_json does after a sync.
    doc = _card([VISIBLE])
    # Act
    _write_card(diverged, doc)
    # Assert — this is the regression. Before the merge it was 1.
    assert len(_texts(diverged)) == 2


def test_the_recovered_row_keeps_its_text(diverged):
    # Arrange
    doc = _card([VISIBLE])
    # Act
    _write_card(diverged, doc)
    # Assert
    assert "arrived by sync" in _texts(diverged)


def test_the_rows_keep_their_original_order(diverged):
    # Arrange — `_copy_comments` matches peers on (task_id, seq) and
    # `_insert_comments` derives seq from list position, so reordering here
    # would make every peer re-insert these as new rows.
    doc = _card([VISIBLE])
    # Act
    _write_card(diverged, doc)
    # Assert
    assert _texts(diverged) == ["in the doc", "arrived by sync"]


def test_a_comment_new_in_this_write_is_still_saved(diverged):
    # Arrange — the caller is adding one; it is in the doc and not the table.
    fresh = {"author": "me", "ts": "2026-08-23T00:02:00Z", "text": "brand new"}
    doc = _card([VISIBLE, fresh])
    # Act
    _write_card(diverged, doc)
    # Assert — the recovered row AND the new one: 3, not 2 and not 1.
    assert len(_texts(diverged)) == 3


def test_a_matched_comment_keeps_its_document_id(diverged):
    # Arrange — `task_comments` has no column for the document's `c_*` id, so
    # rebuilding a matched comment from the table alone would strip it.
    doc = _card([dict(VISIBLE, id="c_keepme")])
    # Act
    _merge_unseen_comment_rows(diverged, "c1", doc)
    # Assert
    assert doc["comments"][0].get("id") == "c_keepme"


def test_an_already_agreeing_card_recovers_nothing(conn):
    # Arrange — THE OVER-REACH CONTROL. A merge wide enough to resurrect a
    # hidden row is wide enough to duplicate a visible one.
    _write_card(conn, _card([VISIBLE]))
    doc = _card([VISIBLE])
    # Act
    recovered = _merge_unseen_comment_rows(conn, "c1", doc)
    # Assert
    assert recovered == 0


def test_an_already_agreeing_card_is_not_duplicated(conn):
    # Arrange
    _write_card(conn, _card([VISIBLE]))
    # Act
    _write_card(conn, _card([VISIBLE]))
    # Assert
    assert len(_texts(conn)) == 1


def test_a_card_with_no_rows_yet_is_left_alone(conn):
    # Arrange — a brand-new card has nothing in the table to fold in.
    doc = _card([VISIBLE])
    # Act
    recovered = _merge_unseen_comment_rows(conn, "does-not-exist", doc)
    # Assert
    assert recovered == 0
