#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards db set-min-client-version`` — deliberately raise the store's floor.

Setting the floor is a DELIBERATE ADMIN ACT, never automatic — see
:mod:`scitex_cards._min_client_version` for the full incident this answers
(operator directive 2026-07-21: three stale client venvs misbehaved against
the shared store on the same day, one of them silently serving an EMPTY
example board; nobody read the warnings, so the operator's ruling replaces
the warning with an error). Ordinary reads/writes never call
``stamp_floor`` — only this verb does — so the floor moves at release time,
deliberately, never mid-write and never as a side effect of an agent simply
using the store.

Wired directly onto :data:`scitex_cards._cli._db.db_group` — a plain import
of that already-defined group object, not an edit to ``_cli/_db.py`` itself
(that file's ``snapshot`` command is owned by a parallel change). Attaching
this verb therefore costs the rest of the CLI package a single import line
in ``_cli/__init__.py``.
"""

from __future__ import annotations

import click

from .._min_client_version import (
    parse_version_tuple,
    read_floor,
    resolve_running_version,
    stamp_floor,
)
from ._db import db_group

_DB_OPTION = click.option(
    "--db",
    "db_path",
    default=None,
    help="Explicit DB path (default: $SCITEX_CARDS_DB, else ~/.scitex/cards/cards.db).",
)


def register(main: click.Group) -> None:
    """No-op — kept for the CLI package's ``register(main)`` convention.

    The command below attaches itself to ``db_group`` via the
    ``@db_group.command`` decorator at IMPORT time; ``db_group`` is already
    registered onto ``main`` by ``_cli/_db.py``'s own ``register()``
    (invoked from ``_cli/__init__.py``), so there is nothing left to wire
    onto ``main`` directly here. Still exported so this module is imported
    the same way as every other CLI submodule.
    """


@db_group.command(
    "set-min-client-version",
    help=(
        "Set the store's minimum-client-version floor (a DELIBERATE act).\n\n"
        "Any scitex-cards client older than FLOOR is REFUSED — a raise, "
        "never a warning — the moment it opens this database, for reads "
        "and writes alike (see `_db.connect`). Refuses to set a floor "
        "higher than THIS client's own version, which would brick the "
        "very client setting it.\n\n"
        "Example:\n"
        "  scitex-cards db set-min-client-version 0.17.5"
    ),
)
@click.argument("floor")
@_DB_OPTION
def set_min_client_version_cmd(floor: str, db_path: str | None) -> None:
    """Stamp ``schema_meta.min_client_version`` after a self-brick sanity check."""
    from .._db import connect, resolve_db_path

    running = resolve_running_version()
    if parse_version_tuple(floor) > parse_version_tuple(running):
        raise click.ClickException(
            f"refusing to set the floor to {floor}: this client is only "
            f"{running}, which is BELOW {floor} — setting it would "
            f"immediately refuse this very client (and every other client "
            f"still at {running}). Upgrade this client to at least {floor} "
            f"first, then set the floor."
        )

    path = resolve_db_path(db_path)
    conn = connect(path)
    try:
        previous = read_floor(conn)
        stamp_floor(conn, floor)
        conn.commit()
    finally:
        conn.close()

    click.echo(f"# min_client_version: {previous or '(none)'} -> {floor}  ({path})")


__all__ = ["register"]

# EOF
