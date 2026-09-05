#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Identity, host stamping and store resolution for the DM tables.

DESIGN: ``docs/design/dm-into-cards-db.md`` §3.3 (thread identity), §3.4
(deterministic ids for derived rows), §3.5 (message ids).

THE RULE THAT SHAPES THIS MODULE: an id is never rewritten. Rewriting one is a
delete plus an insert, which the append-only ruling forbids, so every id here
is either carried over verbatim from the sidecar or minted once and kept.

Three id shapes, for three different jobs:

* **Pair thread** — ``dm:<a>::<b>``, peers sorted. BYTE-IDENTICAL to the
  legacy ``_threads.thread_key``, which is why :func:`pair_thread_id` is the
  implementation that ``thread_key`` now delegates to rather than a second
  spelling of the same rule. Every stored record, MCP response and board URL
  carries this shape already.
* **Group thread** — ``dmg:<ulid>``, OPAQUE. Never derived from the member
  set: a derived key would CHANGE when someone joins, orphaning the history
  or forcing every message's ``thread_id`` to be rewritten.
* **Derived rows** — a content hash, so a backfill re-run maps to the SAME
  primary key and ``INSERT OR IGNORE`` inserts nothing. Determinism is the
  whole of what makes the backfill idempotent, and idempotence is what makes
  an interrupted backfill recoverable by running it again.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import socket
import time
from pathlib import Path

#: Crockford base32 — the ULID alphabet. No I/L/O/U, so a hand-copied id
#: cannot become a different valid id through the usual transcription slips.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: New message ids widen to ``m_`` + 26-char ULID (128 bits). The legacy shape
#: (``m_`` + 12 hex = 48 bits) has a birthday bound near 2^24, which is beyond
#: today's board but NOT beyond a multi-host union of independently-minted
#: ids — and a primary-key collision under ``INSERT OR IGNORE`` silently DROPS
#: a message. Existing ids are still accepted verbatim and never rewritten.
MSG_ID_PREFIX = "m_"

#: Member-event ids minted by the backfill (deterministic) share this prefix.
MEMBER_EVENT_PREFIX = "dme_"

#: Opaque group-thread prefix. Deliberately distinct from ``dm:`` so a reader
#: can tell a pair id from a group id without a table lookup.
GROUP_THREAD_PREFIX = "dmg:"

#: Truncation width for content-derived ids: 24 hex chars = 96 bits.
_DERIVED_HEX = 24

#: Overrides the host stamp. Set it where the container hostname is not the
#: identity you want recorded (an ephemeral container id, say) — the stamp is
#: what makes ``ORDER BY seq, ts, origin_host, id`` a TOTAL order across hosts.
ENV_ORIGIN_HOST = "SCITEX_CARDS_HOST"


def pair_thread_id(a: str, b: str) -> str:
    """Canonical pair-thread id: ``dm:<a>::<b>`` with the peers sorted.

    Sorting makes the id direction-agnostic, so one pair is one thread. This
    is the SAME string ``_threads.thread_key`` has always produced; the
    migration carries every existing thread id across unchanged.
    """
    lo, hi = sorted((a, b))
    return f"dm:{lo}::{hi}"


def peers_of_pair(thread_id: str) -> tuple[str, str]:
    """Inverse of :func:`pair_thread_id` — ``"dm:a::b"`` → ``("a", "b")``."""
    body = thread_id[3:] if thread_id.startswith("dm:") else thread_id
    lo, _, hi = body.partition("::")
    return lo, hi


def is_pair_thread(thread_id: str) -> bool:
    """Whether ``thread_id`` names a two-peer thread (vs. an opaque group)."""
    return thread_id.startswith("dm:")


def _b32(value: int, length: int) -> str:
    """Encode ``value`` as ``length`` Crockford-base32 chars, most-significant first."""
    out = []
    for shift in range(length - 1, -1, -1):
        out.append(_CROCKFORD[(value >> (5 * shift)) & 0x1F])
    return "".join(out)


def ulid() -> str:
    """A 26-char ULID: 48-bit millisecond timestamp + 80 random bits.

    Lexicographically sortable by mint time, which makes a group-thread id
    self-describing without adding a column, and 80 bits of entropy is far
    past any collision concern for a fleet-sized message log.
    """
    return _b32(int(time.time() * 1000), 10) + _b32(secrets.randbits(80), 16)


def new_message_id() -> str:
    """Mint a fresh message id (``m_`` + ULID)."""
    return MSG_ID_PREFIX + ulid()


def new_group_thread_id() -> str:
    """Mint an opaque group-thread id (``dmg:`` + ULID)."""
    return GROUP_THREAD_PREFIX + ulid()


def _digest(*parts: str) -> str:
    """Stable hex digest of ``parts``, joined by a separator they cannot contain."""
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:_DERIVED_HEX]


def derived_message_id(thread_id: str, sender: str, to: str, ts: str, body: str) -> str:
    """Content-derived id for a sidecar record that carries none of its own.

    The inbox migration SKIPS id-less records because it cannot dedup them on a
    re-run. Skipping here would LOSE A MESSAGE, which this migration may not
    do, so the id is derived from the content instead: a second pass maps to
    the same primary key and inserts nothing.
    """
    return MSG_ID_PREFIX + _digest(thread_id, sender, to, ts, body)


def derived_member_event_id(thread_id: str, member: str, action: str, ts: str) -> str:
    """Deterministic id for a membership event the backfill synthesises."""
    return MEMBER_EVENT_PREFIX + _digest(thread_id, member, action, ts)


def origin_host() -> str:
    """The host stamp written onto every row this process creates.

    ``$SCITEX_CARDS_HOST`` wins; otherwise the POSIX hostname. Falls back to
    ``"unknown"`` rather than raising: a missing hostname must not be able to
    block a DM write, and an honest ``"unknown"`` still keeps the sort total.
    """
    override = os.environ.get(ENV_ORIGIN_HOST)
    if override:
        return override
    try:
        return socket.gethostname() or "unknown"
    except OSError:
        return "unknown"


def resolve_dm_db(db: str | Path | None = None, *, store: str | Path | None = None):
    """Resolve which database the DM tables live in.

    Precedence: an explicit ``db`` wins; else an explicit ``store`` when it
    names a server (a DSN), verbatim; else the ambient
    :func:`scitex_cards._store_target.resolve_store_target` chain -- which is
    also where a ``store`` that is a PATH LABEL lands, because DM threads are
    fleet-wide and a per-project label names no DM store (see the store tier).

    A test that wants its DMs isolated from the live fleet database passes a
    scoped DSN (its own schema) as ``store=`` or ``db=``, exactly as the task
    reads do; a tmp PATH no longer isolates anything, because nothing writes a
    file beside it any more.
    """
    from .._db import DEFAULT_DB_FILENAME
    from .._store_url import BACKEND_POSTGRES, backend_of, reject_attempted_dsn

    if db is not None:
        # THE SAME REFUSAL THE ``store`` TIER BELOW ALREADY MAKES, and it was
        # missing here -- one line above the comment that describes the very
        # bug. ``Path(db)`` collapses a DSN's "//" into a relative directory:
        # "postgresql://scitex-primary:55432/scitex" comes back as
        # "postgresql:/scitex-primary:55432/scitex", which is the FIFTH
        # spelling of the regrowth ``is_attempted_dsn`` records four of.
        #
        # THE STORE TIER WAS FIXED AND THIS ONE WAS NOT because they are
        # reached by different callers: everything inside the package threads
        # ``store=``, so the ``db=`` tier is only taken when a caller names the
        # database OUTRIGHT -- which nothing did while a database was a file
        # beside the store, and which every ``--db``/``db=`` caller does now.
        # It surfaced the moment a test passed a real DSN as ``db=``.
        #
        # The guard is not what saved us here: ``reject_attempted_dsn`` fires on
        # the ALREADY-MANGLED string, so the failure was loud rather than a
        # phantom tree. Returning the target verbatim is what makes it work.
        reject_attempted_dsn(db)
        if backend_of(db) == BACKEND_POSTGRES:
            return db
        return Path(db).expanduser()
    if store is not None:
        # A DSN IS NOT A CONTAINER PATH, and the tier below assumes it is one:
        # it takes ``store.parent``. Run that on a server URL and ``Path``
        # collapses the "//" into a RELATIVE directory that gets created on the
        # first write. Reproduced 2026-08-19 -- a store of
        # "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards" came back as
        # "postgresql:/scitex_cards@127.0.0.1:55432/cards.db", which is both the
        # phantom tree seen on disk and a FOURTH spelling of the regrowth that
        # :func:`is_attempted_dsn` already records three of.
        reject_attempted_dsn(store)
        if backend_of(store) == BACKEND_POSTGRES:
            # The DM tables belong IN that server, not in a file beside it.
            # Handing the target back verbatim is what the ambient tier below
            # already does, and ``open_db`` re-resolves through
            # ``resolve_store_target`` so ``connect`` dispatches the URL.
            return store
        # A PATH LABEL NAMES NO DM STORE. This tier derived ``<label>.parent /
        # cards.db`` while a store was a file beside the label; since #949 there
        # is no file-backed store, so that filename names nothing and every
        # caller handed it was refused at the door -- as an UNHANDLED 500 in
        # the board's DM views. Measured 2026-09-05 by scitex-hub with a
        # one-variable differential (0.50.0 -> 0.51.1, same container, same
        # code): hub's tenancy middleware injects the per-project label
        # ``<workspace>/<project>/.scitex/todo/tasks.yaml`` and GET dm/threads
        # went 200 -> 500. The 200 was the phantom-store behaviour (an empty
        # SQLite file manufactured beside the label), not a working DM store.
        #
        # DM THREADS ARE FLEET-WIDE. A thread between two agents belongs to no
        # project (operator, 2026-08-09: DMs read the database, never a per-host
        # or per-project file), so a per-project label resolves to the AMBIENT
        # store -- the same rule ``_store_target`` applies to a tasks label --
        # and when nothing is configured that resolution RAISES
        # ``StoreTargetNotConfigured``, which the views turn into a typed
        # refusal rather than inventing an empty thread list.
        from .._store_target import resolve_store_target as _ambient

        return _ambient(None)

    # THE AMBIENT TIER RETURNS THE TARGET AS WRITTEN, path or server URL.
    # It used to call resolve_db_path, which RAISES on a DSN -- so with
    # $SCITEX_CARDS_DB pointing at PostgreSQL every DM write died here, while
    # card reads and writes worked. Measured 2026-08-01 by booting the rebuilt
    # image the way an agent does: list_tasks returned 2971 cards and the DM
    # write funnel raised StoreTargetIsNotAPath.
    #
    # Every tier can be a server now. The ``db`` tier still hands a path back
    # for the callers that name a database file outright; the ``store`` tier
    # hands a DSN back verbatim and sends a path label HERE, to the same
    # ambient resolution the task store uses.
    from .._store_target import resolve_store_target

    return resolve_store_target(None)


def utc_now_iso() -> str:
    """Second-resolution ISO-8601 UTC stamp with the canonical ``Z`` suffix."""
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


__all__ = [
    "ENV_ORIGIN_HOST",
    "GROUP_THREAD_PREFIX",
    "MEMBER_EVENT_PREFIX",
    "MSG_ID_PREFIX",
    "derived_member_event_id",
    "derived_message_id",
    "is_pair_thread",
    "new_group_thread_id",
    "new_message_id",
    "origin_host",
    "pair_thread_id",
    "peers_of_pair",
    "resolve_dm_db",
    "ulid",
    "utc_now_iso",
]

# EOF
