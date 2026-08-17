#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Where the user registry reads and writes: the database, always.

The four registry mutations (``register_user``, ``add_alias``, ``set_notify``,
``touch_user``) share one shape — take the store lock, read the rows, mutate
them, write them back. This module is the seam where that shape reaches its
home, and it exists as a module rather than two lines in ``_store_write``
because that file sits one line under the 512 ceiling.

THERE IS DELIBERATELY NO BRANCH HERE, and the first version of this change
had one. I routed an EXPLICIT ``store`` to the YAML file and only an AMBIENT
one to the database, on the reasoning that naming a path is a caller saying
where the registry lives. That SPLITS THE REGISTRY the moment one call names
a store and another does not, which is exactly what the notify dispatch
does::

    alice = register_user(names=["alice"], store=store)   -> file
    emit(Event(...))            -> resolve_user("alice")  -> database
    enqueued ['alice'] instead of ['u_181ec73bb85f']

The registration landed in one home, the resolution looked in the other, and
identity resolution degraded silently to the raw name string.

The premise was false as well as dangerous: a store path is not a file. The
YAML tier was deleted in #512, and the notify test says so where it builds
one — *"Store is SQLite; reads/writes hit the canonical DB and the path
survives only as the store IDENTITY stamp."* An explicit store names WHICH
database, never a different KIND of home. So ``store`` is threaded through to
``open_db`` and nothing branches on it.

``path`` is gone from these signatures for the same reason: there is no file
to name.
"""

from __future__ import annotations

from pathlib import Path


def _read_users(store: str | Path | None) -> list[dict]:
    """Registry rows for a read-modify-write."""
    from .._db_users import load_users_rows

    return load_users_rows(store)


def _write_users(users: list[dict], store: str | Path | None) -> None:
    """Persist registry rows to the home :func:`_read_users` read from.

    Both halves take the SAME ``store`` and must keep doing so. Reading from
    one home and writing to another is a lost update in one direction and a
    silent truncation in the other — and it is not hypothetical, it is the
    bug described above, caught only because a notify test asserted on a
    resolved id rather than on a name.
    """
    from .._db_users import save_users_rows

    save_users_rows(users, store)


__all__ = [
    "_read_users",
    "_write_users",
]

# EOF
