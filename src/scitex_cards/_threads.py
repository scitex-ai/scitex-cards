#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operator↔agent direct-message THREAD store (scitex-dev DM convention v1).

The board's ``/chat`` view, the ``dm_send`` / ``dm_list`` MCP verbs, and the
dm-dispatch rail all sit on this one pure store module.

Canonical DM record (scitex-dev spec v1 — a CONTRACT, field names are fixed)::

    {id, thread, from, to, body, ts, read}

Field-name bridge to the neighbouring vocabularies:

- ``from`` = a2a ``source``/``from_agent`` = card-comment ``author``
- ``body`` = a2a ``content`` = card-comment ``text``
- ``id`` = a2a ``msg_id``; ``thread`` = a2a ``conversation_id``
- ``ts`` = iso8601 ``Z`` timestamp; ``read`` = bool

Thread id: ``dm:<a>::<b>`` with the two peer names SORTED lexicographically —
ONE thread per pair, both directions. The operator's reserved peer name is
``"operator"``.

WHERE DMs LIVE NOW (schema v5 — read this before touching a write path)
----------------------------------------------------------------------
THE DATABASE IS THE STORE OF RECORD. :func:`append_message` writes
``cards.db``'s ``dm_*`` tables FIRST and raises if that fails; the
``<store_dir>/threads.json`` sidecar is then mirrored best-effort. See
``docs/design/dm-into-cards-db.md``: DMs were the one piece of fleet data the
store's protections did not cover, and appending one message rewrote the whole
document — the wipe-shaped write this package keeps paying for.

The sidecar is still the READ path and is still written, deliberately: it is
the rollback state (migration design M3). Flipping reads (M4) and retiring the
file (M5) are separate, staged changes.

On-disk (JSON): ``{"threads": {"dm:<a>::<b>": [{id, thread, from, to,
body, ts, read}, ...]}}``, guarded by its OWN flock (``.threads.json.lock``)
so chat writes never convoy with card writes; crash-safe write mechanics live
in :mod:`scitex_cards._threads_io`. There is NO legacy-YAML reader any more:
``threads_path()`` used to materialise the sidecar from a ``threads.yaml`` as
a side effect of being ASKED FOR A PATH, which would re-create the very file
this migration retires, behind its back.

dm-dispatch
-----------
``append_message`` ALSO enqueues an ``event_type="dm"`` notification into the
recipient's EXISTING pull-inbox (:func:`scitex_cards._inbox.enqueue`, keyed via
``_users.resolve_user`` exactly like ``poll_notifications``) — the >=0.7.32
unified channel server drains that inbox and pushes the message into the
agent's live session. Durable, standalone; NO a2a dependency. The
``"operator"`` recipient is enqueued too, for symmetry (the board itself
reads unread state from THIS sidecar, not the inbox).

READ CACHE vs WRITERS (the one rule this module lives or dies by)
----------------------------------------------------------------
The GUI polls a thread every ~5s. READ paths (:func:`get_thread`,
:func:`list_threads`) go through :func:`_load_threads_cached`, an
mtime-guarded cache of the parsed content.

WRITERS NEVER READ THE CACHE. :func:`append_message` and the authoritative
half of :func:`mark_read` do a read-modify-write and MUST re-read the file
fresh, under the lock, via the uncached :func:`_load_threads` — a stale read
there would silently DROP a message. Optimizing a writer onto the cache is
the one failure mode of this design; there is a test that refuses it.

:func:`mark_read` gets a lock-free FAST NO: it asks the cache whether this
reader has anything to flip, and returns 0 without taking the lock when the
answer is no — safe because marking-read is idempotent and self-healing (a
stale cache costs at most one poll's delay, never data). That fast path is
fine for ``mark_read``; it would NOT be fine for ``append_message``.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from . import _threads_mirror as _mirror
from ._dm.ids import pair_thread_id, peers_of_pair
from ._paths import resolve_tasks_path

#: The sidecar's file mechanics live in :mod:`scitex_cards._threads_io`.
#: Re-exported here (rather than imported at each use site) so
#: ``_threads._threads_lock`` / ``_threads._save_threads_unlocked`` keep
#: resolving for callers and for the tests that monkeypatch them.
from ._threads_io import _save_threads_unlocked, _threads_lock  # noqa: F401

logger = logging.getLogger(__name__)

#: Sidecar filename, sibling of the resolved task store.
THREADS_FILENAME = "threads.json"

#: Top-level key of the sidecar document.
_THREADS_KEY = "threads"

#: Reserved peer name for the human operator (scitex-dev spec v1).
OPERATOR_NAME = "operator"

#: DM message-id prefix (``m_`` + 12 hex chars — the ``u_``/``n_`` id shape).
_MSG_ID_PREFIX = "m_"
_MSG_ID_TOKEN_HEX = 12


# --------------------------------------------------------------------------- #
# Paths / keys / small helpers                                                 #
# --------------------------------------------------------------------------- #
def threads_path(store: str | Path | None = None) -> Path:
    """Resolve the sidecar path: ``<store_dir>/threads.json``. PURE.

    ``store`` is the TASK store path (or ``None`` → the standard resolution
    chain); the threads sidecar always sits NEXT TO it so both files live in
    the same scope.

    THIS FUNCTION USED TO WRITE. It called ``_migrate_legacy_yaml_once``, so
    merely ASKING WHERE THE SIDECAR WOULD BE materialised it whenever a legacy
    ``threads.yaml`` was present. That is a landmine for the DM-into-the-store
    migration in two ways: a path query that creates a file re-creates the very
    sidecar being retired, behind the migration's own back — and it did so by
    reading YAML, which the operator has ruled out ("YAML は使いません"). The
    migration design (part 2 §7.3) requires the call gone BEFORE any phase
    runs, so it is gone. A path query is now a path query.
    """
    tasks = resolve_tasks_path(store) if store is None else Path(store).expanduser()
    return tasks.parent / THREADS_FILENAME


def thread_key(a: str, b: str) -> str:
    """Canonical thread id for a peer pair: ``dm:<a>::<b>``, names sorted.

    DELEGATES to :func:`scitex_cards._dm.ids.pair_thread_id`, which is the
    database's thread id. That is not indirection for its own sake: the
    migration's core promise is that no existing thread id is REWRITTEN (a
    rewrite is a delete plus an insert, which append-only forbids), and one
    shared implementation makes the two ids identical BY CONSTRUCTION rather
    than by two copies of a sorting rule staying in agreement forever.
    """
    return pair_thread_id(a, b)


def peers_of(key: str) -> tuple[str, str]:
    """Inverse of :func:`thread_key` — ``"dm:a::b"`` → ``("a", "b")``."""
    return peers_of_pair(key)


def _utc_now_iso() -> str:
    """Second-resolution ISO-8601 UTC stamp with the canonical ``Z`` suffix."""
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _generate_msg_id() -> str:
    """Fresh DM message id (``m_`` + 12 hex chars)."""
    return _MSG_ID_PREFIX + secrets.token_hex(_MSG_ID_TOKEN_HEX // 2)


# --------------------------------------------------------------------------- #
# Load / save                                                                  #
# --------------------------------------------------------------------------- #
def _load_threads(path: Path) -> dict[str, list[dict]]:
    """Read the ``threads:`` mapping off disk. NEVER raises on absence.

    Missing file / absent key / non-mapping value → ``{}``; non-list thread
    values coerce to ``[]``, non-dict entries drop — a malformed row never
    breaks a read.
    """
    if not path.exists():
        return {}
    import json

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle) or {}
    raw = data.get(_THREADS_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for key, records in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(records, list):
            out[key] = []
            continue
        out[key] = [r for r in records if isinstance(r, dict)]
    return out


"""Parsed sidecar content per path, guarded by the file's mtime+size.

``{path: (st_mtime_ns, st_size, threads)}``. READ-ONLY: the stored mapping is
handed to readers that copy on the way out and never mutate it. A writer must
NEVER be served from here — see the module docstring.
"""
_READ_CACHE: dict[str, tuple[int, int, dict[str, list[dict]]]] = {}

#: This cache's name in :mod:`._cache_stats` — where its hit rate is legible.
CACHE_NAME = "dm_threads"


def _load_threads_cached(path: Path) -> dict[str, list[dict]]:
    """:func:`_load_threads` memoized on the file's ``(mtime_ns, size)``.

    The ``services.get_board`` pattern: any write rolls the mtime forward, so
    the next read re-parses and no reader can be served stale content across a
    write. Absent file → ``{}`` and nothing cached.

    FOR READERS ONLY. Callers must treat the result as immutable and copy what
    they hand out (:func:`get_thread` and :func:`list_threads` both do).
    Writers use the uncached :func:`_load_threads` under the lock instead.
    """
    from ._cache_stats import record_hit, record_miss

    try:
        stat = path.stat()
    except OSError:
        return {}
    key = str(path)
    cached = _READ_CACHE.get(key)
    if (
        cached is not None
        and cached[0] == stat.st_mtime_ns
        and cached[1] == stat.st_size
    ):
        record_hit(CACHE_NAME)
        return cached[2]
    record_miss(CACHE_NAME)
    threads = _load_threads(path)
    _READ_CACHE[key] = (stat.st_mtime_ns, stat.st_size, threads)
    return threads


def _is_unread_for(record: dict, reader: str, wanted: set[str] | None) -> bool:
    """Whether ``record`` is one that :func:`mark_read` would flip for ``reader``.

    ONE predicate, deliberately shared by the lock-free pre-check and the
    authoritative flip. If those two ever disagreed about what "unread" means,
    the pre-check could answer "nothing to do" for a message the flip would
    have taken — a message stuck unread forever rather than one poll late.
    """
    if record.get("to") != reader or record.get("read"):
        return False
    if wanted is not None and record.get("id") not in wanted:
        return False
    return True


# --------------------------------------------------------------------------- #
# dm-dispatch (inbox enqueue)                                                  #
# --------------------------------------------------------------------------- #
#: Everything a DM write does BESIDES committing the message — inbox dispatch,
#: the sidecar copy, the receipt copy — lives in
#: :mod:`scitex_cards._threads_mirror`. Aliased onto this module's namespace so
#: the historical private names keep resolving for tests that patch them.
_dispatch_to_inbox = _mirror.dispatch_to_inbox
_mirror_to_sidecar = _mirror.mirror_to_sidecar
_mirror_receipts = _mirror.mirror_receipts


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def append_message(
    from_: str,
    to: str,
    body: str,
    *,
    store: str | Path | None = None,
    msg_id: str | None = None,
    ts: str | None = None,
) -> dict:
    """Append one DM to the DATABASE, mirror it to the sidecar, dispatch it.

    DUAL WRITE, AND THE POLARITY IS THE POINT (migration design M3). The
    database write happens FIRST and RAISES on failure — it is the store of
    record. The sidecar write is best-effort and merely logged; it is the
    ROLLBACK STATE, kept complete so that undoing this migration is redeploying
    the previous version rather than restoring anything.

    Note this INVERTS the card cutover, where YAML was the store and the
    database mirror was best-effort. Here the database is the new SSOT and the
    file is the fallback, so a database failure must be loud: silently keeping
    only the sidecar copy would re-open the exact gap this change closes.

    Mints the id (unless ``msg_id`` is given), then enqueues a ``dm``
    notification into the recipient's inbox (fail-soft). Returns a copy of the
    stored record — unchanged in shape, so no caller has to know any of this.
    """
    if not from_ or not to:
        raise ValueError("append_message requires non-empty 'from_' and 'to'")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("append_message requires a non-empty 'body'")
    key = thread_key(from_, to)
    record = {
        "id": msg_id or _generate_msg_id(),
        "thread": key,
        "from": from_,
        "to": to,
        "body": body,
        "ts": ts or _utc_now_iso(),
        "read": False,
    }
    from ._dm.write import append_pair

    append_pair(
        from_,
        to,
        body,
        store=store,
        msg_id=record["id"],
        ts=record["ts"],
        record=record,
    )
    _mirror_to_sidecar(record, key, store)
    _dispatch_to_inbox(record, store)
    return dict(record)


def get_thread(a: str, b: str, *, store: str | Path | None = None) -> list[dict]:
    """Return the pair's messages in append (chronological) order.

    Direction-agnostic; missing file or unknown pair → ``[]``. Records are
    copies — mutating them does not touch the store.
    """
    records = _load_threads_cached(threads_path(store)).get(thread_key(a, b), [])
    return [dict(r) for r in records]


#: ``list_threads`` summaries per path, keyed like ``_READ_CACHE`` — the
#: unread-count pass rescans EVERY record of every thread (~0.7 s per call on
#: the 3 MB live sidecar even with the parse cached), so it runs once per file
#: change; the copy-out in ``list_threads`` keeps callers mutation-safe.
_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, dict]]] = {}


def list_threads(*, store: str | Path | None = None) -> dict[str, dict]:
    """Summarize every thread: peers, last message, counts, per-peer unread.

    Returns ``{thread_key: {"peers": (a, b), "last": <record|None>,
    "count": N, "unread": {peer: n}}}`` where ``unread[p]`` counts messages
    addressed TO ``p`` that are still ``read: false`` (i.e. what ``p`` has
    not seen yet). Served from ``_SUMMARY_CACHE`` while the sidecar's
    ``(mtime_ns, size)`` is unchanged; same benign stat-then-parse race as
    :func:`_load_threads_cached` (self-heals on the next call).
    """
    path = threads_path(store)
    try:
        stat = path.stat()
    except OSError:
        return {}
    cache_key = str(path)
    cached = _SUMMARY_CACHE.get(cache_key)
    if cached is None or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
        summary: dict[str, dict] = {}
        for key, records in _load_threads_cached(path).items():
            a, b = peers_of(key)
            unread: dict[str, int] = {a: 0, b: 0}
            for r in records:
                if not r.get("read") and r.get("to") in unread:
                    unread[r["to"]] += 1
            summary[key] = {
                "peers": (a, b),
                "last": records[-1] if records else None,
                "count": len(records),
                "unread": unread,
            }
        cached = (stat.st_mtime_ns, stat.st_size, summary)
        _SUMMARY_CACHE[cache_key] = cached
    return {
        k: {
            "peers": v["peers"],
            "last": dict(v["last"]) if v["last"] else None,
            "count": v["count"],
            "unread": dict(v["unread"]),
        }
        for k, v in cached[2].items()
    }


def mark_read(
    thread: str,
    reader: str,
    *,
    ids: list[str] | None = None,
    store: str | Path | None = None,
) -> int:
    """Flip messages addressed to ``reader`` in ``thread`` to ``read: true``.

    ``ids=None`` (default) marks ALL of the reader's unread messages in the
    thread; otherwise only the listed message ids. Idempotent; returns the
    number of records actually flipped. Unknown thread → 0.

    Sits on the GUI's ~5s poll path ("nothing to flip" almost always), so it
    opens with a lock-free FAST NO off the read cache and only pays the lock
    + fresh parse when there is real work.
    """
    wanted = set(ids) if ids is not None else None
    path = threads_path(store)

    # Fast NO — tolerable here only: a stale cache costs one poll's delay or
    # one wasted lock, never a lost message (everything below re-reads fresh).
    cached = _load_threads_cached(path).get(thread)
    if not any(_is_unread_for(r, reader, wanted) for r in cached or []):
        return 0

    flipped = 0
    flipped_ids: list[str] = []
    with _threads_lock(path):
        threads = _load_threads(path)  # authoritative: never the cache
        records = threads.get(thread)
        if not records:
            return 0
        for r in records:
            if not _is_unread_for(r, reader, wanted):
                continue
            r["read"] = True
            flipped += 1
            if r.get("id"):
                flipped_ids.append(r["id"])
        if flipped:
            _save_threads_unlocked(threads, path)
    _mirror_receipts(flipped_ids, reader, store)
    return flipped


__all__ = [
    "OPERATOR_NAME",
    "THREADS_FILENAME",
    "append_message",
    "get_thread",
    "list_threads",
    "mark_read",
    "peers_of",
    "thread_key",
    "threads_path",
]

# EOF
