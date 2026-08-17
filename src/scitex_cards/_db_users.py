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

    A ``…/tasks.yaml`` store is a DISPLAY LABEL, not a file. ``_paths``
    builds it as ``resolve_db_path(None).parent / "tasks.yaml"`` and
    ``_store_add`` says what happens when it is mistaken for a location:
    *"`_resolved_store` returns a DISPLAY LABEL … good enough to name a
    store in a message, never a thing on disk. Guard the database, not the
    label."* The YAML tier itself was deleted in #512.

    Passing that label straight to :func:`open_db` is not harmless, because
    nothing downstream normalises it — ``resolve_store_target`` returns an
    explicit argument AS WRITTEN, and ``resolve_db_path`` and ``connect``
    likewise. SQLite then CREATES a database at that path. Measured while
    building this module::

        store            .../store0/tasks.yaml     r(store)  .../store0/tasks.yaml
        $SCITEX_CARDS_DB .../store0/cards.db       r(None)   .../store0/cards.db
        users@store      [['alice']]     <- a database named tasks.yaml
        users@None       []              <- the real board, empty
        resolve_user("alice") -> None

    i.e. the registration went into a PHANTOM STORE beside the real one and
    identity resolution degraded to the raw name — the same shape as the
    2026-08-02 incident where a DSN collapsed to a relative path and callers
    wrote a tree named after it.

    So the label is inverted back to its sibling database. A DSN passes
    through untouched (a server target is already a database), and ``None``
    stays ``None`` so the ambient chain resolves it.
    """
    if store is None:
        return None
    text = str(store)
    from ._store_url import is_postgres_url

    if is_postgres_url(text):
        return text
    path = Path(text).expanduser()
    if path.suffix in (".yaml", ".yml"):
        from ._db import DEFAULT_DB_FILENAME

        return path.parent / DEFAULT_DB_FILENAME
    return path


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


def load_users_rows(store: str | Path | None = None) -> list[dict]:
    """Return every registry row from the shared database.

    Targeted read: one ``SELECT`` against ``users``, NOT a document
    assembly. ``resolve_user`` runs on ordinary card paths, and routing it
    through the whole-document reader would rebuild every card on the board
    to answer a question about names.
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


__all__ = [
    "load_users_rows",
    "save_users_rows",
]

# EOF
