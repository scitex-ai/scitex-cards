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


def registry_is_database(store: str | Path | None) -> bool:
    """Whether THIS call's registry home is the shared database.

    An EXPLICIT ``store`` keeps the YAML behaviour, deliberately. Naming a
    path is a caller stating where the registry is, and three groups of
    callers do exactly that: the test suite (every store under
    ``tests/scitex_cards/_users/`` is a ``tmp_path / "tasks.yaml"``),
    deliberate imports/bootstraps, and any pre-database deployment. None of
    them should be silently redirected to a server.

    Only an AMBIENT call — ``store=None``, i.e. "wherever the board is" —
    resolves to the configured store, because that is the only form that was
    ever asking for the fleet's registry rather than a named file.

    No configured store target means no shared home, so the caller keeps the
    local file: that is the zero-config and pre-migration case, and it is a
    working configuration rather than an error.
    """
    if store is not None:
        return False
    from ._store_target import StoreTargetNotConfigured, resolve_store_target

    try:
        resolve_store_target(None)
    except StoreTargetNotConfigured:
        return False
    return True


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


def load_users_rows() -> list[dict]:
    """Return every registry row from the shared database.

    Targeted read: one ``SELECT`` against ``users``, NOT a document
    assembly. ``resolve_user`` runs on ordinary card paths, and routing it
    through the whole-document reader would rebuild every card on the board
    to answer a question about names.
    """
    from ._db import open_db
    from ._db_export import _record

    conn = open_db(None)
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


def save_users_rows(users: list[dict]) -> None:
    """Upsert registry rows into the shared database.

    TARGETED, AND THAT IS WHAT MAKES THIS AFFORDABLE ON THE HOT PATH.
    ``_insert_users`` is a per-row ``ON CONFLICT(id) DO UPDATE``, not a
    delete-then-insert, so writing one user disturbs no other row and no
    document is assembled. The YAML implementation this replaces had to
    rewrite the entire store to change one field, and its own docstring
    records what that cost on the liveness heartbeat: **46 s/write on 0.9.4,
    171 s/write on 0.13.x**, the root cause of the board's write timeouts.
    Routing the registry through the document reader would have reproduced
    that regression exactly; a single-row write cannot.

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

    conn = open_db(None)
    try:
        _insert_users(conn, users)
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "load_users_rows",
    "registry_is_database",
    "save_users_rows",
]

# EOF
