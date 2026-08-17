#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which home the user registry reads and writes — database or local file.

The four registry mutations (``register_user``, ``add_alias``, ``set_notify``,
``touch_user``) all share one shape: take the store lock, read the rows,
mutate them, write them back. This module is the single place that shape
learns the registry may not be a file at all.

Split out of ``_store_write`` rather than added to it: that module sits one
line under the 512 ceiling, and a dispatch pair is a distinct responsibility
from the YAML writer it dispatches to. Keeping it here also keeps the leaf
package free of SQL — the database half lives in
:mod:`scitex_cards._db_users`, which carries the full reasoning for why an
ambient registry belongs on the shared board.
"""

from __future__ import annotations

from pathlib import Path


def _read_users(store: str | Path | None, path: Path) -> list[dict]:
    """Registry rows for a read-modify-write, from whichever home applies.

    An AMBIENT registry (``store=None``) is fleet identity and reads from the
    shared board. An EXPLICIT ``store`` names a file and keeps the file — the
    whole test suite, deliberate imports and pre-migration deployments all
    take that branch, and none of them should be silently redirected to a
    server.
    """
    from .._db_users import load_users_rows, registry_is_database
    from ._store_read import _load_users_section

    if registry_is_database(store):
        return load_users_rows()
    return _load_users_section(path)


def _write_users(
    users: list[dict], store: str | Path | None, path: Path
) -> None:
    """Persist registry rows to whichever home :func:`_read_users` read from.

    BOTH HALVES DISPATCH ON THE SAME PREDICATE AND MUST KEEP DOING SO. A read
    from one home followed by a write to the other is not a half-applied fix,
    it is a LOST UPDATE in one direction and a silent truncation in the
    other: the file writer replaces the ``users:`` section wholesale, so rows
    read from the database would overwrite whatever the file held, while rows
    read from the file would upsert stale copies onto the shared board.

    That is why the predicate is a function both call rather than a condition
    written twice — the two spellings could drift apart, and this is a pair
    where drift destroys data rather than degrading behaviour.
    """
    from .._db_users import registry_is_database, save_users_rows
    from ._store_write import _save_users_unlocked

    if registry_is_database(store):
        save_users_rows(users)
        return
    _save_users_unlocked(users, path)


__all__ = [
    "_read_users",
    "_write_users",
]

# EOF
