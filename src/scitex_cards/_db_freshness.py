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

    DIAGNOSTIC, NOT AUTHORITATIVE. Since ``store_uuid`` decides identity (see
    :mod:`scitex_cards._store_uuid`), this row exists to tell a human which
    spelling the last writer used. It is still consulted as the LEGACY fallback
    for a database with no identity facing a caller with no expectation, so it
    is still written — but it is no longer the thing ownership rests on.

    THE STAMP IS NOT REWRITTEN WHEN IT ALREADY NAMES THE SAME FILE. This used
    to be an unconditional ``ON CONFLICT DO UPDATE SET value=excluded.value``,
    and that overwrite was the FLIP MECHANISM behind the 2026-07-28 outage: one
    bind-mounted database is reachable as ``/home/agent/.scitex/cards/cards.db``
    (container only), ``/home/ywatanabe/.scitex/cards/cards.db`` (both) and
    ``/home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db`` (the realpath).
    Every write replaced the stamp with the WRITER'S OWN spelling, so the name
    flipped each time the other namespace wrote a card; whenever it landed on
    the container-only name the host could not ``stat`` it and the board was
    refused its own database with an HTTP 500. Three repairs on 2026-07-28 were
    each undone by nothing more than writing the next card from the other side.

    So: same file, same spelling, or a spelling the kernel confirms is the same
    file ⇒ leave the row ALONE. Rewriting it then is pure harm — it changes an
    unauthoritative diagnostic into a moving target for the one branch that
    still reads it. Sameness is asked of :func:`_dual_write._same_file`, the
    package's ONE definition of it; when that cannot tell (a stamped path this
    namespace cannot stat), the stamp IS refreshed to the spelling this writer
    can actually see, which is strictly more useful to the next reader than a
    name nobody here can resolve.
    """
    from ._dual_write import _same_file

    canonical = canonical_path(store_path)
    current = stamped_store_path(conn)
    if current is not None and (current == canonical or _same_file(current, canonical)):
        return
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY_STORE_PATH, canonical),
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
    from ._store_uuid import (
        ACCEPT,
        expected_store_uuid,
        identity_verdict,
        read_store_uuid,
    )

    # UUID-FIRST, exactly as the ownership guard is. A stamped identity that
    # ACCEPTs decides, and the path is not consulted at all — otherwise the two
    # checks could disagree about one database, which is the asymmetry class
    # that let a packaged fixture be read AS THE BOARD on 2026-07-19.
    if identity_verdict(read_store_uuid(conn), expected_store_uuid()) == ACCEPT:
        return True, None

    stamped = stamped_store_path(conn)
    if stamped is None:
        return True, None
    from ._dual_write import _same_file

    if _same_file(stamped, store_path):
        return True, None
    if not Path(stamped).exists() or not Path(store_path).exists():
        # CANNOT TELL — say so. Claiming "a DIFFERENT store" here would be an
        # assertion about a file this mount namespace cannot even stat, and
        # that false claim is what the operator read during the 2026-07-28
        # outage while the database in question was in fact their own.
        return False, (
            f"ownership of this database CANNOT BE DETERMINED from a path in "
            f"this mount namespace: it is stamped for {stamped!r}, this read is "
            f"of {canonical_path(store_path)!r}, and at least one of those "
            f"cannot be stat'd here — they may well be ONE file under two "
            f"names. Bind this store to an identity once: "
            f"`scitex-cards store adopt-uuid`."
        )
    return False, (
        f"this database belongs to a DIFFERENT store ({stamped!r}) than the "
        f"one being read ({canonical_path(store_path)!r}). Point "
        "$SCITEX_CARDS_DB at this store's own database."
    )


__all__ = [
    "KEY_STORE_PATH",
    "canonical_path",
    "check_fresh",
    "read_provenance",
    "stamp_store_provenance",
    "stamped_store_path",
]

# EOF
