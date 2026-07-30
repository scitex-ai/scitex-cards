#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Store IDENTITY: when are two paths the same store?

Split from ``test__dual_write.py``, which covers the mirror's POLICY (when it
writes, when it declines, what it stamps). This file covers the question that
policy rests on and that nothing tested: given two path strings, do they name
the same store?

It got its own file because it got its own bug. The guard compared realpath
STRINGS, and on this host one store directory is reachable by two names that
resolve differently:

    /home/agent/.scitex/cards      -> /home/agent/.scitex/cards
    /home/ywatanabe/.scitex/cards  -> /home/ywatanabe/.dotfiles/src/.scitex/cards

Same inode, two realpaths. The guard therefore refused every write from
whichever population did not match the stamp, against a database that was
theirs. MEASURED on the live board 2026-07-20, minutes after a restore.
"""

from __future__ import annotations

import os

from scitex_cards._db import ENV_DB
from scitex_cards._dual_write import _db_mirrors_this_store


def _stamped_db(tmp_path, monkeypatch, store):
    """A database stamped as the mirror of ``store``.

    Seeds a fresh DB from the store's doc and stamps its provenance for
    ``store`` — the explicit form of the deleted
    ``import_from_yaml(tasks_path=store, as_store=store)``. SQLite is the only
    store and the importer is gone, so both halves are done by hand: seed via
    ``seed_db_from_doc`` (the surviving rebuild primitive), then stamp
    ``KEY_YAML_PATH`` with ``store`` — which is exactly what
    ``_db_mirrors_this_store`` reads, so the identity assertions are unchanged.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._db import connect
    from scitex_cards._db_freshness import stamp_store_provenance
    from scitex_cards._yaml import safe_load

    db = tmp_path / "cards.db"
    monkeypatch.setenv(ENV_DB, str(db))
    doc = safe_load(store.read_text(encoding="utf-8")) or {}
    seed_db_from_doc(doc, str(db))
    conn = connect(str(db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        stamp_store_provenance(conn, store)
        conn.commit()
    finally:
        conn.close()
    return db


def test_one_store_reached_by_two_paths_is_ONE_store(tmp_path, monkeypatch):
    """Same file, two names, different realpaths -> still the same store.

    Hard links reproduce the production shape exactly: one inode, two real
    paths, neither a symlink, so ``realpath`` cannot collapse them. A symlink
    would NOT reproduce it — realpath resolves symlinks and the old string
    compare passed for those, which is why this went unnoticed.
    """
    # Arrange — two genuine paths to ONE file.
    as_agent = tmp_path / "as-agent.yaml"
    as_agent.write_text("tasks: []\n", encoding="utf-8")
    as_operator = tmp_path / "as-operator.yaml"
    os.link(as_agent, as_operator)
    assert os.path.realpath(as_agent) != os.path.realpath(as_operator), (
        "precondition: these must be two DIFFERENT realpaths, else this test "
        "is not reproducing the bug"
    )
    db = _stamped_db(tmp_path, monkeypatch, as_agent)

    # Act
    allowed = _db_mirrors_this_store(db, as_operator)

    # Assert
    assert allowed, (
        "refused a write to the very store this database serves, because the "
        "caller spelled the path differently"
    )


def test_a_genuinely_different_store_is_still_refused(tmp_path, monkeypatch):
    """The PAIR of the test above.

    Widening the comparison must not open the door. Without this, "always
    return True" satisfies the hard-link test and deletes the guard.
    """
    # Arrange — two separate files, not two names for one.
    mine = tmp_path / "mine.yaml"
    mine.write_text("tasks: []\n", encoding="utf-8")
    theirs = tmp_path / "theirs.yaml"
    theirs.write_text("tasks: []\n", encoding="utf-8")
    db = _stamped_db(tmp_path, monkeypatch, mine)

    # Act
    allowed = _db_mirrors_this_store(db, theirs)

    # Assert
    assert not allowed


def test_a_store_that_cannot_be_stat_ed_is_CANNOT_TELL_not_a_path_compare(
    tmp_path, monkeypatch
):
    """CHANGED DELIBERATELY. This test used to pin the realpath STRING fallback.

    It asserted that when a stamped path cannot be ``stat``-ed, the guard falls
    back to comparing realpath strings — so a stamp and a caller that SPELL the
    path identically are "the same store" even though neither exists. Under
    ``docs/design/store-identity-is-a-uuid.md`` §8 that fallback is REMOVED and
    this row of the truth table now reads ``either path not stat-able -> CANNOT
    TELL -> False``.

    WHY THE OLD ASSERTION HAD TO GO. scitex-dev's framing, endorsed in the
    design: *a fallback that triggers only in the case it cannot judge is worse
    than no fallback.* It fires precisely when a path is unstat-able — i.e.
    exactly when you are across a mount-namespace boundary and least entitled
    to an opinion. On the host, ``/home/agent/.scitex/cards/cards.db`` cannot be
    stat'd, so the strings never matched, and the board was refused its own
    database with an HTTP 500 all day on 2026-07-28.

    WHY REMOVING IT COSTS NOTHING REAL. This test's original premise — "in
    DB-canonical mode the YAML store is frequently a NAME the database is
    stamped with rather than a file on disk" — no longer holds at any
    production call site. Both doors call ``_db_mirrors_this_store(db_path,
    db_path)``, and a ``db_path`` that does not exist returns ``True`` at the
    first line. The only unstat-able side reachable in production is the STAMPED
    path, which is the cross-namespace case the fallback answered WRONGLY.

    The escape from CANNOT TELL is not a looser comparison — it is binding the
    store to an identity once (``scitex-cards store adopt-uuid``), after which
    the path is not consulted at all.
    """
    # Arrange — stamp for a path, then delete it.
    ghost = tmp_path / "ghost.yaml"
    ghost.write_text("tasks: []\n", encoding="utf-8")
    db = _stamped_db(tmp_path, monkeypatch, ghost)
    ghost.unlink()
    assert not ghost.exists()

    # Act / Assert — the kernel cannot be asked, so the guard does not guess.
    assert not _db_mirrors_this_store(db, ghost)
    assert not _db_mirrors_this_store(db, tmp_path / "someone-else.yaml")


def test_a_legacy_yaml_path_only_db_passes_both_guards_and_self_migrates(
    tmp_path, monkeypatch
):
    """A database stamped ONLY under the pre-cutover key must not brick on deploy.

    EVERY existing database — including the live ``cards.db`` re-stamped to end
    the 2026-07-20 outage — carries the OLD ``yaml_path`` ``schema_meta`` key and
    NO ``store_path``. The two ownership guards MUST AGREE it is usable, or
    ``check_fresh`` refuses it while ``_db_mirrors_this_store`` adopts it — and
    the SQLite read path, with no YAML to fall back to, goes read-only again.
    The first write then self-migrates it to ``store_path`` (no deploy step, no
    pre-cutover key read in the code).
    """
    from conftest import seed_db_from_doc

    from scitex_cards import _store_backend
    from scitex_cards._db import connect
    from scitex_cards._db_freshness import (
        KEY_STORE_PATH,
        check_fresh,
        stamped_store_path,
    )

    # Arrange — seed a DB, then force the EXACT legacy shape: ONLY `yaml_path`,
    # never `store_path` (delete it if the seeder wrote one).
    db = tmp_path / "cards.db"
    monkeypatch.setenv(ENV_DB, str(db))
    doc = {
        "tasks": [
            {
                "id": "t",
                "title": "T",
                "status": "deferred",
                "assignee": "agent:test-suite",
            }
        ]
    }
    seed_db_from_doc(doc, str(db))
    conn = connect(str(db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (KEY_STORE_PATH,))
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('yaml_path', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(db),),
        )
        conn.commit()
    finally:
        conn.close()

    # Assert — precondition + BOTH guards agree the legacy DB is usable.
    conn = connect(str(db))
    try:
        assert stamped_store_path(conn) is None, "precondition: no store_path yet"
        ok, reason = check_fresh(conn, db)
        assert ok, f"check_fresh must not refuse a legacy yaml_path-only DB: {reason}"
    finally:
        conn.close()
    assert _db_mirrors_this_store(db, db), (
        "the write guard must adopt a legacy yaml_path-only DB"
    )

    # Act — a write self-migrates it forward to the new key.
    _store_backend.write_doc_to_db(doc, db)

    # Assert — it now carries store_path (claimed), and both guards still pass.
    conn = connect(str(db))
    try:
        assert stamped_store_path(conn) is not None, "the write must stamp store_path"
        ok, _ = check_fresh(conn, db)
        assert ok
    finally:
        conn.close()
    assert _db_mirrors_this_store(db, db)


# EOF
