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

CLAIMED ONCE, NOT REWRITTEN PER WRITE
-------------------------------------
The stamp says WHICH STORE this is the database of — not WHO WROTE LAST. Those
read the same only while a store has exactly one name. This one has several: a
container and the host reach ONE inode by names neither namespace can resolve
for the other. So :func:`stamp_store_provenance` leaves an existing stamp alone
whenever it already names the same file, and the name claimed first is the name
kept. A stamp that changes with the writer cannot be the thing an ownership
guard decides on; see that function for the outage this closes, and for why it
is a MITIGATION of path-based identity rather than a fix for it.

THE FIX ITSELF IS A UUID, AND IT HAS LANDED
-------------------------------------------
:mod:`scitex_cards._store_uuid` gives the database an opaque identity that no
namespace can re-spell, and the ownership guard consults it FIRST. This module's
``store_path`` row is therefore DIAGNOSTIC now — the legacy evidence used only
when a database carries no identity AND the caller declares no expectation. It
is kept, and kept stable, because that is every store until it has been through
``scitex-cards store adopt-uuid``.

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

    THE STAMP IS CLAIMED ONCE, NOT REWRITTEN PER WRITE::

        unstamped                       -> stamp it; the first write claims it
        stamped for THIS SAME FILE      -> LEAVE IT ALONE, however this caller
                                           would have spelled that file
        stamped for a DIFFERENT file    -> not this function's problem: the
                                           ownership guard
                                           (``_dual_write._db_mirrors_this_store``)
                                           already refused upstream

    Sameness is asked of :func:`scitex_cards._dual_write._same_file` — the SAME
    predicate the ownership guard uses, deliberately, so this package carries
    exactly ONE definition of "the same store" and the stamper can never disagree
    with the guard that reads the stamp.

    WHY LEAVING AN EXISTING STAMP IS CORRECT, NOT MERELY CONVENIENT
    ---------------------------------------------------------------
    The stamp answers "WHICH STORE IS THIS THE DATABASE OF", not "who wrote
    last". A path is only meaningful inside the namespace that produced it, so
    overwriting the stamp with the current writer's spelling DESTROYS information
    for every other namespace while ADDING none — the new name is no truer than
    the old one, it is merely local. Whichever name was claimed first is at least
    STABLE, and stability is the property the ownership guard actually needs: it
    can only decide with a stamp it is able to resolve.

    THE BUG THIS CLOSES, measured live 2026-07-28/29. One inode
    (dev/ino 2096/3417791) is reached by three names::

        /home/agent/.scitex/cards/cards.db                    container's name
                                                              (host CANNOT stat it)
        /home/ywatanabe/.scitex/cards/cards.db                the host's name
        /home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db  its realpath

    The old unconditional ``ON CONFLICT DO UPDATE`` called itself "idempotent —
    a re-stamp with the same store is a no-op". That holds only for a store with
    ONE name. With two it is not a no-op, it is a FLIP. The operator's board was
    repaired by stamping a HOST-VISIBLE name so the guard's inode branch could
    run; ``/tasks`` went 500 -> 200 serving 2,684 cards. The very next
    CONTAINER-side card write re-stamped ``/home/agent/...``, the host could no
    longer ``stat`` the stamp, ``_same_file`` fell back to a realpath STRING
    compare that can never match, and the board returned to 500. The repair lost
    a race to the next write — and every container write wins that race. Inside
    the container both names ARE stat-able (bind mount), so the inode branch
    runs, this function sees "same file", and the host's stamp survives.

    THIS WAS A MITIGATION; THE FIX HAS NOW LANDED
    ---------------------------------------------
    Path-based identity CANNOT be made correct across namespaces; it can only be
    made STABLE. Two namespaces that both write will always disagree about the
    name of one file, and no rule about which name wins changes that. The real
    repair is an opaque ``store_uuid`` that no namespace can re-spell — designed
    in PR #601 (``docs/design/store-identity-is-a-uuid.md``) and IMPLEMENTED in
    :mod:`scitex_cards._store_uuid`, tracked by card
    ``scitex-cards-resolver-never-default-yaml-20260727``.

    So this row is now DIAGNOSTIC, not authoritative: it tells a human which
    spelling claimed the database first. It is still read as the LEGACY fallback
    on the guard's ``ADOPT`` branch — a database with no identity facing a caller
    with no expectation, which is every database that has not yet run
    ``scitex-cards store adopt-uuid`` — so stopping the thrash still matters
    until each store is bound. Once a store carries an identity the path is not
    consulted at all, and a container-written name being illegible to the host
    stops mattering rather than being worked around.
    """
    existing = stamped_store_path(conn)
    if existing is not None:
        # ONE definition of "same store" in this package: the ownership guard's.
        # Imported here, not at module scope, because `_dual_write` imports back
        # into this module (`stamped_store_path`) — the same deferred-import
        # shape `check_fresh` below already uses to keep the pair acyclic.
        from ._dual_write import _same_file

        if _same_file(existing, store_path):
            return

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

    UUID-FIRST, exactly as the ownership guard is. A stamped identity that
    ACCEPTs decides and the path is never consulted. This function has no
    production caller today, but leaving it path-only would build the read/write
    asymmetry the design forbids the moment somebody wired it up — and that
    asymmetry is what 2026-07-19 was made of, when the write door refused a
    foreign store correctly all day while the read door returned its rows.
    """
    from ._store_uuid import (
        ACCEPT,
        expected_store_uuid,
        identity_verdict,
        read_store_uuid,
    )

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
        # assertion about a file this mount namespace cannot even stat, and that
        # false claim is exactly what the operator read during the 2026-07-28
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
