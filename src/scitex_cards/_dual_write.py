#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE OWNERSHIP GUARD — does this database belong to the store we resolved?

SQLite is the ONLY write target (operator ruling 2026-07-21): 「データベースし
か書く場所なんてありえない。デュアルライトっていうオプションがあること自体が
おかしい」 — there is no such thing as a second place to write; the mere
EXISTENCE of a dual-write option is the bug. What used to live in this module
alongside the guard below — an env-gated mirror-to-YAML path
(``SCITEX_TODO_DUAL_WRITE`` / ``ENV_DUAL_WRITE``, ``enabled()``,
``mirror_after_save()``, the failure counter, ``check_mirror_healthy()``) — is
DELETED, not defaulted off. A toggle that can be flipped is a second write
target that merely happens to be switched off today; deleting the code that
reads the flag is the only way to make "which store did this write actually
reach?" stop being a live question.

THE INCIDENT THIS ANSWERS (root cause, diagnosed 2026-07-21). ``cards.db``
carried a stale ``schema_meta`` row (``yaml_path`` pointing at an old
``~/.scitex/todo/tasks.yaml``). An agent whose environment still carried the
dual-write flag had every MCP/CLI write silently routed to that YAML instead
of the canonical database: every call returned SUCCESS, ``health`` stayed
green, and an entire session of card writes never reached the board. The flag
made that possible; removing it makes it unrepresentable — there is no
environment variable left to read that could send a write anywhere but the
database at ``$SCITEX_CARDS_DB``.

WHAT SURVIVES HERE, and is load-bearing, is the OWNERSHIP GUARD that keeps one
database from being written with another store's rows.

THE INVARIANT
-------------
A database is the database of exactly ONE store. Point ``$SCITEX_CARDS_DB`` at a
database built as store B's, then write store A into it, and nothing merges —
B's rows are REPLACED with A's. On 2026-07-19 this package's own concurrency
test did exactly that to the live fleet database, rebuilding it from a 21-card
fixture, because the destination came from the ambient environment while the
source came from the caller and nothing checked the two matched.

So :func:`_db_mirrors_this_store` refuses a write whose resolved store disagrees
with the database's own provenance stamp (:data:`scitex_cards._db_freshness.KEY_STORE_PATH`).
An UNSTAMPED database is adoptable — a fresh one, or a populated board being
adopted at deploy — and the first write claims it by stamping its identity.
The store's ONE write chokepoint, :func:`scitex_cards._store_backend.write_doc_to_db`,
calls this guard and RAISES rather than returning quietly on a mismatch: a
write that cannot reach the canonical DB must NEVER report success.

WHY IDENTITY IS COMPARED BY INODE, NOT BY STRING
------------------------------------------------
:func:`_same_file` compares ``st_dev``/``st_ino``, because on this host one store
directory is reached by two names that ``realpath`` resolves DIFFERENTLY
(``/home/agent/.scitex/cards`` vs ``/home/ywatanabe/.scitex/cards``, a bind
mount). A string compare called them different stores and refused every write
from whichever population did not match the stamp — measured live on 2026-07-20.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Do these two paths name the SAME FILE — by identity, not by spelling?

    ``realpath`` alone is not enough, and the difference is not academic: this
    host reaches ONE store directory by two names that resolve DIFFERENTLY.

        /home/agent/.scitex/cards      -> /home/agent/.scitex/cards
        /home/ywatanabe/.scitex/cards  -> /home/ywatanabe/.dotfiles/src/.scitex/cards

    Same ``st_dev``/``st_ino`` — the same directory, reached through a bind
    mount — but two different realpath STRINGS. A string compare therefore
    called them different stores and REFUSED every write from whichever
    population did not match the stamp, on a database that was in fact theirs.
    MEASURED on the live board 2026-07-20, immediately after a restore: cards
    written via ``/home/ywatanabe/...`` were refused against a DB stamped
    ``/home/agent/...``.

    So ask the filesystem what it knows: two paths are the same file when the
    kernel says so. The realpath compare stays as the FALLBACK for when a path
    does not exist yet — which is normal here, since in DB-canonical mode the
    YAML store is a name the DB is stamped with rather than a file on disk.
    """
    try:
        sa, sb = Path(a).stat(), Path(b).stat()
        return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)
    except OSError:
        pass
    try:
        return os.path.realpath(str(a)) == os.path.realpath(str(b))
    except OSError:
        return False


def _db_mirrors_this_store(db_path: str | Path, store_path: str | Path) -> bool:
    """Is the DB at ``db_path`` the mirror of ``store_path`` — or of some OTHER store?

    THE INVARIANT, implied everywhere and enforced nowhere: a shadow DB mirrors
    exactly ONE store. Writing store A into the DB that mirrors store B does not
    merge them, it REPLACES B's contents with A's.

    It went unenforced because the (now-deleted) mirror-to-YAML write path
    resolved the destination with a bare ``resolve_db_path()`` — no argument —
    so the DB came from the AMBIENT ENVIRONMENT while the source came from the
    CALLER. Nothing checked that the two referred to the same pairing.

    Not theoretical. On 2026-07-19 this package's own concurrency test — which
    copies ``os.environ`` into two writer subprocesses, so they inherit
    ``SCITEX_CARDS_DUAL_WRITE=1`` — wrote to a pytest ``tmp_path`` store and
    rebuilt the LIVE FLEET DATABASE from its 21-card fixture, replacing 2,136
    real cards. It was recoverable only because the YAML was still canonical and
    the DB merely a mirror; under the DB-canonical mode merged that same
    morning, running the test suite would have destroyed the board outright.

    Same shape as the env-compat incident hours earlier: a global default
    silently overriding an explicit local intent. Same fix: refuse, keep the
    good state, say so.

    WHY THE DB'S OWN STAMP AND NOT "is this the canonical store" — the naive
    guard (refuse anything that is not the canonical store) also refuses the
    package's own legitimate tests, which deliberately pair a tmp store with a
    tmp DB and are correct to mirror. The honest question is not "is this store
    special" but "do these two belong together", and the DB already answers it:
    :func:`_db_freshness.stamp_store_provenance` records which store it reflects.

    An UNSTAMPED DB is adoptable (a fresh/bootstrapping mirror, incl. every test
    fixture) — the first write claims it. A DB stamped for a DIFFERENT store is
    refused. Compared by ``realpath`` because ``~/.scitex`` is a symlink here, so
    ``/home/agent/...`` and ``/home/ywatanabe/...`` are the same file.
    """
    import sqlite3

    from ._db_freshness import stamped_store_path

    if not Path(db_path).exists():
        return True  # nothing to clobber yet
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            stamped = stamped_store_path(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — unreadable stamp ⇒ let the mirror try
        return True
    if not stamped:
        return True  # unstamped ⇒ adoptable
    if _same_file(stamped, str(store_path)):
        return True
    logger.error(
        "!! REFUSING TO MIRROR: %s is the shadow DB of %s, but this write is to "
        "%s. Mirroring would REPLACE that store's rows with this one's. If you "
        "meant to repoint the mirror, point $SCITEX_CARDS_DB at the intended "
        "store's own database; if this is a test or scratch store, point it at "
        "a scratch DB.",
        db_path,
        stamped,
        store_path,
    )
    return False


__all__ = [
    "_db_mirrors_this_store",
    "_same_file",
]

# EOF
