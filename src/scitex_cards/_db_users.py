#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The user registry's home in the SHARED database.

THE REGISTRY IS FLEET IDENTITY, NOT LOCAL RUNTIME STATE, and that
misclassification is the whole defect this module exists to correct.

``_users/_store_read`` and ``_users/_store_write`` resolved the registry
through :func:`scitex_cards._paths.resolve_tasks_path`, which is the
LOCAL-state resolver and says so outright — its own comment names its
clients as *"pidfiles, the delivery ledger, reminder state and the
users/groups sidecar"*. The first three genuinely are local. The fourth is
not: :func:`scitex_cards._users.resolve_user` answers "who is this agent"
for every card on a board every agent shares.

MEASURED 2026-08-17 inside a sac container, with ``$SCITEX_CARDS_DB`` set to
the fleet server::

    resolve_tasks_path(None)   -> /home/agent/.scitex/cards/tasks.yaml
    is_postgres_url(that)      -> False
    exists()                   -> False
    registered users found     -> 0

    /proc/self/mountinfo:
      .../containers/overlays/scitex-cards/upper/home/agent -> /home/agent

``/home/agent`` is the container's PRIVATE overlay, so that path is private
too. **A perfectly working write would not have fixed this**: every agent
would have built its own registry and ``resolve_user`` would still answer
``None`` for every peer. The registry presented as EMPTY rather than as
DIVERGENT only because the file had never been created here — an accident of
which failure came first, not a milder fault.

Nothing below is new machinery. The database already carried the
destination, in both directions, and only the live registry API was pointed
away from it::

    _db_sections._insert_users   users + user_names  <- doc["users"]
    _db_export (users loop)      doc["users"]        <- users

So this module is where the registry's SQL lives, and it lives here rather
than under ``_users/`` because that is a leaf package and leaf packages do
not hold SQL.

THERE IS NO FILE BRANCH, and the first version of this module was wrong
to have one.

I originally routed an EXPLICIT ``store`` to the YAML file and only an
AMBIENT one to the database, reasoning that naming a path is a caller
stating where the registry lives. That rule SPLIT THE REGISTRY IN HALF the
moment one call named a store and another did not — which is not a corner
case, it is what the notify dispatch does:

    alice = register_user(names=["alice"], store=store)   -> file
    emit(Event(...))            -> resolve_user("alice")  -> database
    enqueued ['alice'] instead of ['u_181ec73bb85f']

The registration landed in one home and the resolution looked in the other,
so identity resolution silently degraded to the raw name string. Caught by
``test_emit_result_reports_the_enqueued_owner``.

The premise was also simply false. A store path is not a file — the YAML
tier was DELETED in #512, and ``tests/.../test__notify_dispatch.py`` says so
where it builds one: *"Store is SQLite; reads/writes hit the canonical DB
and the path survives only as the store IDENTITY stamp."* So an explicit
store names WHICH DATABASE, never a different KIND of home, and the file the
registry used to write was a phantom sitting beside the real one.

Operator ruling, 2026-08-17, which settles it independently of the bug:
「データベースを使わないで状態を表しているファイルがあるならばそれは失格です」
— a file representing state outside the database is disqualified.

So ``store`` selects WHICH database and never a different KIND of home. It
still cannot be handed to :func:`open_db` raw, because a ``tasks.yaml``
store is a display LABEL that nothing downstream normalises — see
:func:`_db_target`, which inverts it to the sibling database rather than
letting SQLite create a phantom store at the label's path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Read contract for a registry row. ``repair=True`` is the TOLERANT board-read
#: contract (the backup rail uses ``False``), and tolerant is right here: a
#: registry that refuses to load because one row is damaged takes identity
#: resolution down for every agent, which is a strictly worse outcome than
#: resolving the remaining names and leaving one unresolved.
_REPAIR = True


def _db_target(store: str | Path | None) -> str | Path | None:
    """Map a store argument to a DATABASE, because a label is not one.

    The inversion and the two incidents that motivate it live in
    :func:`scitex_cards._store_target.database_for`, which this now delegates
    to. Deliberately NOT restated here: this guard existed in this module
    alone while the notification inbox had none, and a private copy is how
    that happened. One statement, one place.

    The only behaviour this adds is the ``None`` contract: ``None`` stays
    ``None`` so the ambient chain resolves it downstream, rather than being
    resolved here.
    """
    if store is None:
        return None
    from ._store_target import database_for  # noqa: PLC0415 -- cycle

    return database_for(store)


def _refuse_unserialisable(users: list[dict]) -> None:
    """Raise before writing if any row carries a value JSON cannot represent.

    THE ENCODER BENEATH THIS IS SILENT, AND THAT IS ONLY SAFE WHILE THE TABLE
    IS EMPTY. ``_db_sections`` encodes every registry row with
    ``_record_json``, which is :func:`scitex_cards._db_payload.card_payload_json`
    under an alias — the encoder measured this morning to return ``None`` for a
    non-JSON value rather than failing. #893 replaced it with a raising variant
    on the CARD path only; users, notifications and messages still reach the
    silent one.

    A ``NULL`` payload is not a lost field, it is an UNREADABLE ROW: the
    reader cannot rebuild a record from its columns, so it refuses, and the
    refusal propagates to every subsequent operation by any agent. That
    hazard has never fired for the registry for exactly one reason — the
    table has zero rows. Populating it, which is this change's entire
    purpose, makes it live.

    So the check happens HERE, before the insert, rather than by widening the
    shared encoder: refusing one registration is the correct blast radius,
    and changing the encoder under notifications and messages in the same
    breath would be a second, unmeasured change riding along with this one.
    """
    from ._db_payload import _unserialisable_fields

    for row in users:
        blamed = _unserialisable_fields(row)
        if not blamed:
            continue
        uid = row.get("id")
        raise TypeError(
            f"user {uid!r} carries a value JSON cannot represent, so the "
            f"registry was NOT written: {', '.join(blamed)}.\n"
            f"Writing it would store a NULL payload, and a registry row "
            f"without its payload cannot be rebuilt — every later read of "
            f"the registry would refuse, for every agent, not just this one.\n"
            f"Fix the value at the call site (a datetime, a Path and a set "
            f"are the usual three) and register again."
        )


#: Lock-free reads are served from here for this long. See
#: :func:`load_users_rows_cached` for why the number is small and why the
#: write path must never touch it.
_CACHE_TTL_S = 1.0

#: ``{db target: (monotonic stamp, rows)}``.
_ROW_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _cache_key(store: str | Path | None) -> str:
    """The RESOLVED database, so two spellings of one board share an entry.

    Keying on the raw argument is not a smaller version of this — it is
    wrong, and it broke notify dispatch a second time. An ambient read keys
    on ``None`` while a write naming the same board keys on its path, so the
    write never retires the entry the reader is being served from:

        register_user(store=<label>)  ->  invalidates ".../cards.db"
        resolve_user()                 <-  still served from "None"

    which is a stale-read bug wearing a cache's clothing. The ambient chain
    is resolved here instead; it reads the environment and the config file
    and opens nothing, so it costs nothing next to the 4.8 ms connection the
    cache exists to avoid.
    """
    target = _db_target(store)
    if target is not None:
        return str(target)
    from ._store_target import StoreTargetNotConfigured, resolve_store_target

    try:
        return str(resolve_store_target(None))
    except StoreTargetNotConfigured:
        return "<no store configured>"


def _forget_cached_rows(store: str | Path | None) -> None:
    """Drop this store's cached rows (called after every registry write)."""
    _ROW_CACHE.pop(_cache_key(store), None)


def load_users_rows_cached(store: str | Path | None = None) -> list[dict]:
    """:func:`load_users_rows` behind a short TTL — for LOCK-FREE READS ONLY.

    THE CONNECTION IS THE COST, NOT THE QUERY. Measured 2026-08-17::

        connect() only              4.78 ms
        connect() + init_schema()   4.63 ms
        open_db()                   4.82 ms
        connect() + SELECT users    4.28 ms

    So a registry read costs ~4.8 ms of connection setup regardless of how
    small the table is, and ``resolve_user`` sits on ordinary card paths.
    Without this the full suite went from 3m36s to a projected ~16m — a 4.5x
    regression I introduced by replacing an mtime-guarded cache with a fresh
    connection per call. The old file path was not merely cached by accident;
    the cache was load-bearing and I dropped it.

    THE TTL IS THE PRICE OF LOSING mtime. A file could be revalidated for
    free by stat-ing it; a database cannot, and any freshness probe needs a
    connection — the very thing being avoided. So staleness is bounded by
    time instead: a peer's registration becomes visible within a second.
    That is acceptable for name -> id resolution specifically, because the
    fallback while stale is the RAW NAME, which is what the entire
    pre-registry world used and what every caller still handles.

    NEVER SERVE A READ-MODIFY-WRITE FROM HERE. ``_registry_home._read_users``
    calls the UNCACHED :func:`load_users_rows` under the store lock, and the
    old implementation carried the same warning for the same reason: a stale
    read followed by a full write back is a lost update. Rows are returned by
    reference, so a caller that mutates them would poison the cache too —
    another reason the mutating path must not come through here.
    """
    import time

    key = _cache_key(store)
    now = time.monotonic()
    hit = _ROW_CACHE.get(key)
    if hit is not None and (now - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    rows = load_users_rows(store)
    _ROW_CACHE[key] = (now, rows)
    return rows


def load_users_rows(store: str | Path | None = None) -> list[dict]:
    """Return every registry row from the shared database. UNCACHED.

    Targeted read: one ``SELECT`` against ``users``, NOT a document
    assembly. ``resolve_user`` runs on ordinary card paths, and routing it
    through the whole-document reader would rebuild every card on the board
    to answer a question about names.

    This is the form the WRITE path uses, deliberately — see
    :func:`load_users_rows_cached`.
    """
    from ._db import open_db
    from ._db_export import _record

    conn = open_db(_db_target(store))
    try:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at, id"
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(_record(row, "users", repair=_REPAIR))
    return out


def save_users_rows(users: list[dict], store: str | Path | None = None) -> None:
    """Upsert registry rows into the shared database.

    TARGETED, AND THAT IS WHAT MAKES THIS AFFORDABLE ON THE HOT PATH.
    ``_insert_users`` is a per-row ``ON CONFLICT(id) DO UPDATE``, not a
    delete-then-insert, so writing one user disturbs no other row and no
    document is assembled.

    MEASURED 2026-08-17 on the live 4982-card board, read-only: the document
    path costs ``export_doc`` 0.361 s + a full-board rehash 0.242 s + the
    hash select 0.003 s = **0.607 s per write**, plus ``_assert_no_shrink``'s
    own ``SELECT id FROM tasks``. A targeted upsert is one statement.

    Do NOT cite the 46-171 s/write figure in ``_save_users_unlocked``'s
    docstring for this — I did, and it is stale. It measured a ruamel
    round-trip of a 6.5 MB YAML file, and ``_save_doc_unlocked`` no longer
    writes YAML at all. The preference for a targeted write survives the
    correction; that particular justification for it does not, and a number
    quoted from a docstring is not a measurement of the code as it stands.

    UPSERT-ONLY, so a user absent from ``users`` is NOT deleted. This differs
    from the YAML path, which wrote the section wholesale and therefore
    dropped anything missing. It is a divergence with no current caller —
    measured 2026-08-17, nothing in ``_users/`` removes a user; every mutation
    (``register_user``, ``add_alias``, ``set_notify``, ``touch_user``) adds or
    edits. Preferring the upsert is also the safer asymmetry: a partial list
    reaching a wholesale writer silently deletes the registry, and a registry
    is precisely the thing whose accidental truncation nobody notices until
    identity resolution starts returning ``None``.
    """
    _refuse_unserialisable(users)

    from ._db import open_db
    from ._db_sections import _insert_users

    conn = open_db(_db_target(store))
    try:
        _insert_users(conn, users)
        conn.commit()
    finally:
        conn.close()
    # The cache is only ever a read accelerator, so a write must retire it
    # immediately — a registration this process cannot see is worse than one
    # a peer sees a second late.
    _forget_cached_rows(store)


__all__ = [
    "load_users_rows",
    "load_users_rows_cached",
    "save_users_rows",
]

# EOF
