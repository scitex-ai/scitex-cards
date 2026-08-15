#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The incremental mirror must be EQUIVALENT to the full rebuild, then fast.

Speed is the point, but correctness is the constraint: a mirror that is fast and
silently wrong is far worse than one that is slow and right, because S2 would
then cut the fleet's reads over to a store that is confidently incorrect.

So every test here asserts the DB CONTENT matches what a full rebuild would have
produced. The speed claim is checked separately, by counting how many cards get
written — not by timing (a wall-clock assertion would be flaky in CI).
"""

import sqlite3

import pytest

from scitex_cards._db import init_schema
from scitex_cards._db_bootstrap import _rebuild_from_doc
from scitex_cards._db_mirror import HASH_TABLE, mirror_doc_incremental


def _doc(*cards, users=None, inboxes=None):
    d = {"tasks": list(cards)}
    if users is not None:
        d["users"] = users
    if inboxes is not None:
        d["inboxes"] = inboxes
    return d


def _card(cid, **kw):
    c = {"id": cid, "title": "t-%s" % cid, "status": "deferred"}
    c.update(kw)
    return c


def _fresh_db(tmp_path):
    p = tmp_path / "todo.db"
    conn = sqlite3.connect(str(p))
    init_schema(conn)
    conn.commit()
    conn.close()
    return p


def _rows(db, table):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute("SELECT * FROM %s" % table)]
    conn.close()
    return out


def _ids(db):
    return sorted(r["id"] for r in _rows(db, "tasks"))


# --------------------------------------------------------------- correctness


def test_first_run_falls_back_to_a_full_rebuild(tmp_path):
    """A DB with no hash table must still end up correct — no migration step."""
    # Arrange
    db = _fresh_db(tmp_path)
    doc = _doc(_card("a"), _card("b"))
    # Act
    out = mirror_doc_incremental(doc, db)
    # Assert
    assert out["full"] is True


def test_first_run_writes_every_card(tmp_path):
    # Arrange
    db = _fresh_db(tmp_path)
    doc = _doc(_card("a"), _card("b"))
    # Act
    mirror_doc_incremental(doc, db)
    # Assert
    assert _ids(db) == ["a", "b"]


def test_a_changed_card_is_updated(tmp_path):
    # Arrange
    db = _fresh_db(tmp_path)
    mirror_doc_incremental(_doc(_card("a"), _card("b")), db)
    # Act
    mirror_doc_incremental(_doc(_card("a", status="done"), _card("b")), db)
    # Assert
    statuses = {r["id"]: r["status"] for r in _rows(db, "tasks")}
    assert statuses["a"] == "done"


def test_an_untouched_card_is_left_alone(tmp_path):
    # Arrange
    db = _fresh_db(tmp_path)
    mirror_doc_incremental(_doc(_card("a"), _card("b")), db)
    # Act
    mirror_doc_incremental(_doc(_card("a", status="done"), _card("b")), db)
    # Assert
    statuses = {r["id"]: r["status"] for r in _rows(db, "tasks")}
    assert statuses["b"] == "deferred"


def test_a_new_card_is_inserted(tmp_path):
    # Arrange
    db = _fresh_db(tmp_path)
    mirror_doc_incremental(_doc(_card("a")), db)
    # Act
    mirror_doc_incremental(_doc(_card("a"), _card("new")), db)
    # Assert
    assert _ids(db) == ["a", "new"]


def test_a_card_missing_from_the_document_SURVIVES(tmp_path):
    """A row absent from the incoming document is KEPT.

    INVERTED FROM ITS PREVIOUS FORM, deliberately. It asserted
    ``_ids(db) == ["a"]`` and called the alternative "the trap an upsert-only
    mirror falls into: deleted cards live forever".

    That trap is real and it is the lesser one. The greater one is what the
    delete actually did on 2026-07-20: the same 16 cards destroyed twice,
    twenty minutes apart, every card created that day and nothing older,
    because a writer holding a document read BEFORE they existed wrote it
    back and the diff called them "removed". The second loss happened with no
    test suite running.

    Absence from a document is not evidence of deletion. It is far more often
    evidence of a stale read, and reconcile cannot tell the two apart — so it
    no longer tries. Stale rows are a storage cost; this was data loss.

    The delete VERB still removes rows when a caller genuinely means it.
    """
    # Arrange
    db = _fresh_db(tmp_path)
    mirror_doc_incremental(_doc(_card("a"), _card("gone")), db)
    # Act
    mirror_doc_incremental(_doc(_card("a")), db)
    # Assert — "gone" is still there. That is the point.
    assert _ids(db) == ["a", "gone"]


def test_a_surviving_cards_hash_is_kept_too(tmp_path):
    """The hash must track the row, or the next reconcile re-writes it forever.

    Also inverted: the hash of a row absent from the document is RETAINED,
    because the row is retained. Dropping the hash while keeping the row would
    make every subsequent reconcile see it as "changed" and rewrite it.
    """
    # Arrange
    db = _fresh_db(tmp_path)
    mirror_doc_incremental(_doc(_card("a"), _card("gone")), db)
    # Act
    mirror_doc_incremental(_doc(_card("a")), db)
    # Assert
    hashes = {r["task_id"] for r in _rows(db, HASH_TABLE)}
    assert "gone" in hashes


def test_comments_are_not_duplicated_on_re_write(tmp_path):
    """THE SHARPEST EDGE: _insert_comments INSERTs (it does not REPLACE), so a
    card re-written without clearing its comments first would duplicate every one
    of them on every single write."""
    # Arrange
    db = _fresh_db(tmp_path)
    comment = {"author": "x", "ts": "2026-01-01", "text": "hello"}
    mirror_doc_incremental(_doc(_card("a", comments=[comment])), db)
    # Act — the SAME comment arrives again on a re-written card.
    mirror_doc_incremental(_doc(_card("a", status="done", comments=[comment])), db)
    # Assert
    assert len(_rows(db, "task_comments")) == 1


# ------------------------------------------------- equivalence to full rebuild


def _full_rebuild_db(tmp_path, doc):
    """A sibling DB built by the FULL rebuild — the equivalence yardstick."""
    (tmp_path / "full").mkdir(exist_ok=True)
    full = _fresh_db(tmp_path / "full")
    conn = sqlite3.connect(str(full))
    _rebuild_from_doc(conn, doc)
    conn.commit()
    conn.close()
    return full


def test_incremental_is_a_full_rebuild_PLUS_the_rows_it_refuses_to_drop(tmp_path):
    """Incremental now SUPERSETS a full rebuild, and the difference is the point.

    THIS ASSERTION WAS `_ids(inc) == _ids(full)` and its docstring called that
    "the whole safety argument in one assertion". The equivalence held because
    BOTH paths deleted rows missing from the document. Incremental no longer
    does, so they diverge by exactly the rows a stale document would have
    destroyed — here, "gone".

    That divergence is the fix, not a regression. Asserting equality again
    would re-pin the behaviour that destroyed the same 16 cards twice on
    2026-07-20.

    WHAT THIS LEAVES OPEN, deliberately and stated rather than hidden: the
    FULL REBUILD still deletes (`_rebuild_from_doc` -> `DELETE FROM {table}`,
    the path measured as the one that wiped the board). It is largely fenced —
    reached only on first run against an empty database, where it has nothing
    to delete, and separately guarded against running on a populated one — so
    this change does not make it reachable. Making the rebuild append-only too
    is its own change, carded, not smuggled in here.
    """
    # Arrange
    doc_v2 = _doc(
        _card("a", status="done", comments=[{"author": "x", "ts": "t", "text": "c"}]),
        _card("b", depends_on=["a"]),
        _card("c"),
    )
    (tmp_path / "inc").mkdir(exist_ok=True)
    inc = _fresh_db(tmp_path / "inc")
    mirror_doc_incremental(_doc(_card("a"), _card("b"), _card("gone")), inc)
    full = _full_rebuild_db(tmp_path, doc_v2)
    # Act
    mirror_doc_incremental(doc_v2, inc)
    # Assert — every row the rebuild has, incremental has too...
    assert set(_ids(full)) <= set(_ids(inc))
    # ...and the only extra is the row the stale document omitted.
    assert set(_ids(inc)) - set(_ids(full)) == {"gone"}


def test_incremental_comments_equal_a_full_rebuild(tmp_path):
    # Arrange
    doc_v2 = _doc(
        _card("a", comments=[{"author": "x", "ts": "t", "text": "c"}]),
    )
    (tmp_path / "inc").mkdir(exist_ok=True)
    inc = _fresh_db(tmp_path / "inc")
    mirror_doc_incremental(_doc(_card("a")), inc)
    full = _full_rebuild_db(tmp_path, doc_v2)
    # Act
    mirror_doc_incremental(doc_v2, inc)
    # Assert
    incremental = [(r["task_id"], r["text"]) for r in _rows(inc, "task_comments")]
    rebuilt = [(r["task_id"], r["text"]) for r in _rows(full, "task_comments")]
    assert sorted(incremental) == sorted(rebuilt)


# ------------------------------------------------------------------- the speed


def test_a_one_card_change_writes_exactly_one_card(tmp_path):
    """The performance claim, asserted as WORK DONE rather than wall-clock (a
    timing assertion would be flaky in CI). 8.69 s of the 16.31 s write was the
    full rebuild; this is what removes it."""
    # Arrange
    db = _fresh_db(tmp_path)
    cards = [_card("c%d" % i) for i in range(200)]
    mirror_doc_incremental(_doc(*cards), db)
    cards[7]["status"] = "done"
    # Act
    out = mirror_doc_incremental(_doc(*cards), db)
    # Assert
    assert out["changed"] == 1


def test_a_one_card_change_leaves_the_rest_unchanged(tmp_path):
    # Arrange
    db = _fresh_db(tmp_path)
    cards = [_card("c%d" % i) for i in range(200)]
    mirror_doc_incremental(_doc(*cards), db)
    cards[7]["status"] = "done"
    # Act
    out = mirror_doc_incremental(_doc(*cards), db)
    # Assert
    assert out["unchanged"] == 199


def test_no_change_at_all_writes_nothing(tmp_path):
    # Arrange
    db = _fresh_db(tmp_path)
    cards = [_card("c%d" % i) for i in range(50)]
    mirror_doc_incremental(_doc(*cards), db)
    # Act — the identical doc, a second time.
    out = mirror_doc_incremental(_doc(*cards), db)
    # Assert
    assert out["changed"] == 0


@pytest.mark.parametrize("field", ["status", "note", "priority"])
def test_any_field_change_is_detected(field):
    """The hash must not miss a change — a mirror that silently skips an edit is
    the failure mode that would make S2 cut over to a wrong store."""
    from scitex_cards._db_mirror import _card_hash

    # Arrange
    base = _card("a")
    edited = dict(base)
    edited[field] = 99 if field == "priority" else "changed"
    # Act
    base_hash, edited_hash = _card_hash(base), _card_hash(edited)
    # Assert
    assert base_hash != edited_hash
