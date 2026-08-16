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


def _stamped_db(tmp_path, env, store):
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
    env.set(ENV_DB, str(db))
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


def test_one_store_reached_by_two_paths_is_ONE_store(tmp_path, env):
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
    if os.path.realpath(as_agent) == os.path.realpath(as_operator):
        # Fail the ARRANGE, not with a second assertion (STX-TQ007): if these
        # collapse to one realpath the test is not reproducing the bug, and a
        # green result would be meaningless rather than reassuring.
        raise AssertionError(
            "precondition: these must be two DIFFERENT realpaths, else this "
            "test is not reproducing the bug"
        )
    db = _stamped_db(tmp_path, env, as_agent)

    # Act
    allowed = _db_mirrors_this_store(db, as_operator)

    # Assert
    assert allowed, (
        "refused a write to the very store this database serves, because the "
        "caller spelled the path differently"
    )


def test_a_genuinely_different_store_is_still_refused(tmp_path, env):
    """The PAIR of the test above.

    Widening the comparison must not open the door. Without this, "always
    return True" satisfies the hard-link test and deletes the guard.
    """
    # Arrange — two separate files, not two names for one.
    mine = tmp_path / "mine.yaml"
    mine.write_text("tasks: []\n", encoding="utf-8")
    theirs = tmp_path / "theirs.yaml"
    theirs.write_text("tasks: []\n", encoding="utf-8")
    db = _stamped_db(tmp_path, env, mine)

    # Act
    allowed = _db_mirrors_this_store(db, theirs)

    # Assert
    assert not allowed


def test_a_store_that_cannot_be_stat_ed_is_CANNOT_TELL_not_a_path_compare(
    tmp_path, env
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
    db = _stamped_db(tmp_path, env, ghost)
    ghost.unlink()
    if ghost.exists():
        raise AssertionError("arrange failed: the ghost store was not removed")

    # Act — the kernel cannot be asked about either path.
    allowed = _db_mirrors_this_store(db, ghost)

    # Assert — CANNOT TELL resolves to False; the guard does not guess.
    assert not allowed


def test_an_unrelated_unstat_able_path_is_also_CANNOT_TELL(tmp_path, env):
    """The sibling of the test above, split out under STX-TQ007.

    Same truth-table row, different input: the caller names a path that never
    existed at all rather than one that was deleted. Merged with its sibling,
    this assertion ran only when that one passed — and it is the one that would
    catch a guard which special-cases "the stamped path" while still guessing
    about everything else.
    """
    # Arrange
    ghost = tmp_path / "ghost.yaml"
    ghost.write_text("tasks: []\n", encoding="utf-8")
    db = _stamped_db(tmp_path, env, ghost)
    ghost.unlink()

    # Act
    allowed = _db_mirrors_this_store(db, tmp_path / "someone-else.yaml")

    # Assert
    assert not allowed


from scitex_cards import _store_backend  # noqa: E402
from scitex_cards._db import connect  # noqa: E402
from scitex_cards._db_freshness import (  # noqa: E402
    KEY_STORE_PATH,
    check_fresh,
    stamped_store_path,
)

#: The card the legacy-shape fixture seeds. Module-level so the split tests
#: below write back the SAME document they were seeded from — a different doc
#: would make the self-migration assertions test two things at once.
_LEGACY_DOC = {
    "tasks": [
        {
            "id": "t",
            "title": "T",
            "status": "deferred",
            "assignee": "agent:test-suite",
        }
    ]
}


def _legacy_yaml_path_only_db(tmp_path, env):
    """A database in the EXACT pre-cutover shape: `yaml_path`, no `store_path`.

    EVERY existing database — including the live ``cards.db`` re-stamped to end
    the 2026-07-20 outage — carries the OLD ``yaml_path`` ``schema_meta`` key and
    NO ``store_path``. The two ownership guards MUST AGREE it is usable, or
    ``check_fresh`` refuses it while ``_db_mirrors_this_store`` adopts it — and
    the SQLite read path, with no YAML to fall back to, goes read-only again.

    This was one test with seven assertions until 2026-08-15. Split under
    STX-TQ007 because the assertions cover four DIFFERENT claims — both guards
    before the migration, and the stamp plus both guards after it — and the
    disagreement between two guards is exactly the failure that must not hide
    behind whichever one is checked first.
    """
    from conftest import seed_db_from_doc

    db = tmp_path / "cards.db"
    env.set(ENV_DB, str(db))
    seed_db_from_doc(_LEGACY_DOC, str(db))
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

    conn = connect(str(db))
    try:
        if stamped_store_path(conn) is not None:
            raise AssertionError(
                "arrange failed: store_path is set, so this is not the legacy "
                "shape and the tests below would prove nothing"
            )
    finally:
        conn.close()
    return db


def test_freshness_accepts_a_legacy_yaml_path_only_db(tmp_path, env):
    # Arrange
    db = _legacy_yaml_path_only_db(tmp_path, env)
    # Act
    conn = connect(str(db))
    try:
        ok, reason = check_fresh(conn, db)
    finally:
        conn.close()
    # Assert
    assert ok, f"check_fresh must not refuse a legacy yaml_path-only DB: {reason}"


def test_the_write_guard_adopts_a_legacy_yaml_path_only_db(tmp_path, env):
    """The OTHER guard, split out under STX-TQ007.

    The two guards must AGREE. Merged into one test, this assertion ran only
    when `check_fresh`'s did — and the failure mode that matters is precisely
    the DISAGREEMENT, where one adopts the database and the other refuses it.
    That is the shape that put the read path read-only with no YAML to fall
    back to, so it must be able to fail on its own line.
    """
    # Arrange
    db = _legacy_yaml_path_only_db(tmp_path, env)
    # Act
    adopted = _db_mirrors_this_store(db, db)
    # Assert
    assert adopted


def test_a_write_self_migrates_a_legacy_db_to_the_store_path_key(tmp_path, env):
    # Arrange
    db = _legacy_yaml_path_only_db(tmp_path, env)
    # Act — a write carries it forward; no deploy step, no migration command.
    _store_backend.write_doc_to_db(_LEGACY_DOC, db)
    # Assert
    conn = connect(str(db))
    try:
        stamped = stamped_store_path(conn)
    finally:
        conn.close()
    assert stamped is not None


def test_freshness_still_passes_after_the_self_migration(tmp_path, env):
    # Arrange
    db = _legacy_yaml_path_only_db(tmp_path, env)
    _store_backend.write_doc_to_db(_LEGACY_DOC, db)
    # Act
    conn = connect(str(db))
    try:
        ok, _ = check_fresh(conn, db)
    finally:
        conn.close()
    # Assert — migrating the key must not make the database look stale.
    assert ok


def test_the_write_guard_still_passes_after_the_self_migration(tmp_path, env):
    # Arrange
    db = _legacy_yaml_path_only_db(tmp_path, env)
    _store_backend.write_doc_to_db(_LEGACY_DOC, db)
    # Act
    adopted = _db_mirrors_this_store(db, db)
    # Assert
    assert adopted


# EOF
