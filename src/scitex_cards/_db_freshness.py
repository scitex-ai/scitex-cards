#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE IDENTITY — which store is this database THE database of?

SQLite is the store. There is no second document to be a mirror OF, so the old
"is the mirror current with the YAML" freshness question is gone: nothing on
disk moves independently of the database any more. What SURVIVES that cutover is
the narrower, load-bearing question of IDENTITY.

THE FAILURE THIS PREVENTS
-------------------------
A database file is opaque about which logical store it belongs to. Point
``$SCITEX_CARDS_DB`` at a database that was built as store B's, then write store
A into it, and nothing merges — B's rows are REPLACED with A's. That is not
hypothetical: on 2026-07-19 this package's own concurrency test rebuilt the live
fleet database from a 21-card fixture because the destination came from the
ambient environment while the source came from the caller, and nothing checked
that the two referred to the same store.

THE STAMP
---------
So every path that writes the database records WHICH STORE it is the database
of — the store's own resolved path (which, post-cutover, is the database path
itself) — into ``schema_meta`` under :data:`KEY_STORE_PATH`. A read or a write
then compares the store it resolved against that stamp. Disagree and the write
is refused: a foreign-stamped database is never clobbered.

An UNSTAMPED database is adoptable — a fresh one, or an existing one that
predates this key. The first write claims it by stamping :data:`KEY_STORE_PATH`.
This is what lets an already-populated board be adopted once, at deploy, and
then be pinned to its own identity from that write on.

WHY IDENTITY, NOT CONTENT
-------------------------
The comparison is CONTENT-INDEPENDENT on purpose: it never parses the store, it
only asks "does this database belong to the store I resolved?". That question
has a stable answer even as the board changes under it, which is exactly what a
guard on the write path needs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: ``schema_meta`` key holding the resolved path of the store this database IS.
#: (Was ``yaml_path`` before the SQLite cutover, when the database mirrored a
#: YAML file; renamed with the cutover — the identity is now the database's own
#: ``$SCITEX_CARDS_DB`` path, not a YAML file that no longer exists.)
KEY_STORE_PATH = "store_path"

_KEYS = (KEY_STORE_PATH,)


def canonical_path(store_path: str | Path) -> str:
    """The ONE spelling of a store path that both the stamp and the check must use.

    ``resolve()``, not just ``expanduser()``. The stamp and the check can be made by
    different processes with different working directories — a relative path stamped
    by one and resolved absolutely by another would otherwise read as "a DIFFERENT
    store" and refuse a database that is in fact its own. Comparing paths means
    comparing them CANONICALLY.
    """
    return str(Path(store_path).expanduser().resolve())


def stamp_store_provenance(conn: sqlite3.Connection, store_path: str | Path) -> None:
    """Record WHICH store this database is the database of. Call inside the write txn.

    Writes the store's canonical path into :data:`KEY_STORE_PATH`. Idempotent —
    a re-stamp with the same store is a no-op update. This is the whole of the
    provenance now: there is no separate document whose ``mtime``/size/card-count
    could drift, so there is nothing else to record.
    """
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY_STORE_PATH, canonical_path(store_path)),
    )


def read_provenance(conn: sqlite3.Connection) -> dict[str, str]:
    """The stamped provenance rows (missing keys simply absent)."""
    placeholders = ", ".join("?" for _ in _KEYS)
    rows = conn.execute(
        f"SELECT key, value FROM schema_meta WHERE key IN ({placeholders})",
        _KEYS,
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def stamped_store_path(conn: sqlite3.Connection) -> str | None:
    """The store path this database is stamped for, or ``None`` if unstamped."""
    return read_provenance(conn).get(KEY_STORE_PATH)


def check_fresh(
    conn: sqlite3.Connection, store_path: str | Path
) -> tuple[bool, str | None]:
    """Is this database USABLE as the database of ``store_path``? ``(ok, reason)``.

    Identity only — one ``schema_meta`` lookup, no store parse.

    An UNSTAMPED database — no :data:`KEY_STORE_PATH` row — is USABLE, not
    refused. This is load-bearing and MUST match
    :func:`scitex_cards._dual_write._db_mirrors_this_store`'s adoptable branch:
    EVERY database created before this key (including the live ``cards.db``
    re-stamped under the pre-cutover ``yaml_path`` key) carries no
    ``store_path``. If this guard refused them while the write guard adopts them,
    a legacy database would brick the SQLite read path on deploy — read-only
    board, the exact outage this rename must not re-introduce. Under DB-canonical
    there is no separate document to be stale against, so "unstamped" means
    "not yet claimed", not "wrong"; the first write claims it by stamping
    :data:`KEY_STORE_PATH`. No pre-cutover key is READ here — the code never
    touches the legacy identity key; forward migration happens on write.

    A database stamped for a GENUINELY DIFFERENT store is still refused. The
    comparison is by :func:`_same_file` (inode), so the ``/home/agent`` vs
    ``/home/ywatanabe`` bind-mount alias reads as one store — consistent with
    the write guard.
    """
    stamped = stamped_store_path(conn)
    if stamped is None:
        return True, None
    from ._dual_write import _same_file

    if not _same_file(stamped, store_path):
        return False, (
            f"this database belongs to a DIFFERENT store ({stamped!r}) than the "
            f"one being read ({canonical_path(store_path)!r}). Point "
            "$SCITEX_CARDS_DB at this store's own database."
        )
    return True, None


__all__ = [
    "KEY_STORE_PATH",
    "canonical_path",
    "check_fresh",
    "read_provenance",
    "stamp_store_provenance",
    "stamped_store_path",
]

# EOF
