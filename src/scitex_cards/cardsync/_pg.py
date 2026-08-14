#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A PostgreSQL card store for reconciliation. READ-ONLY, on purpose.

This satisfies the read half of :class:`~._apply.CardStore` against a live
scitex-cards database, so :func:`~._apply.reconcile` can measure divergence
between two hosts. It refuses to write, and that refusal is a design
decision rather than an unfinished edge.

**Why it cannot write yet.** A card is not one row. Writing one faithfully
means reproducing scitex-cards' own projection: 28 scalar columns derived
from card keys (with ``group`` landing in ``grp``), two JSON side-cars, a
positional ``row_order``, the verbatim ``card_json`` payload, and then the
three child tables — ``task_comments``, ``task_edges``, ``task_roles``.
Re-implementing that here would fork a mapping that has to stay identical
to a package we do not own, forever, and the failure mode of drift is a row
that looks written and is invisible to ``list_tasks`` because a derived
column went stale. Measured 2026-08-10 on the live store: all 3,722 rows
have derived columns exactly agreeing with ``card_json``, so that invariant
is currently perfect and worth not breaking.

**What the write path is waiting for.** ``tasks.revision`` already exists,
is NOT NULL, and is incremented by the DB trigger ``tasks_bump_revision`` on
every update — a correct optimistic lock, fully installed. No writer in
scitex-cards reads it (measured across both the installed 0.33.0 wheel and
the repo at develop; ``revision`` appears only in migrations, schema-shape
checks and the trigger DDL). Card
``cards-revision-lock-installed-but-no-writer-asserts-it`` asks its owner to
expose it as ``update_task(..., expected_revision=N)``. When that lands,
reconciliation writes through THAT verb — one owner for the projection, and
a real compare-and-set instead of one bolted on from outside.

Until then this store measures, and a caller that tries to write gets an
exception rather than a silent no-op. A read-only store that answers False
to ``write`` is indistinguishable from a lost race, and would be counted as
one.
"""

from __future__ import annotations

from typing import Iterable, Mapping

__all__ = ["PgCardStore", "ReadOnlyStoreError", "rows_to_cards"]

#: The payload column. The typed columns beside it are an INDEX derived from
#: this text, so it -- not they -- is what reconciliation compares.
CARD_JSON_COL = "card_json"

READ_ALL_SQL = f"SELECT id, {CARD_JSON_COL} FROM tasks"


class ReadOnlyStoreError(RuntimeError):
    """Raised on any attempt to write through a read-only card store.

    Deliberately NOT a False return from ``write``. False means "somebody
    changed the row under me", which a caller counts as a skip and moves on;
    reporting a missing capability that way would turn every card into a
    plausible-looking lost race and hide the fact that nothing was applied.
    """


def rows_to_cards(rows: "Iterable[tuple[str, str | None]]") -> dict[str, str]:
    """``(id, card_json)`` pairs to the ``{id: raw_json}`` mapping.

    Pure, so the shape of what reconciliation reads can be tested without a
    database. A row whose payload is NULL is DROPPED rather than carried as
    an empty card: the reconciler treats absence as "this side has nothing
    to say", which is the honest reading, whereas an empty payload would
    decide against a real card on the other side.
    """
    return {rid: raw for rid, raw in rows if raw is not None}


class PgCardStore:
    """One scitex-cards PostgreSQL database, readable by reconciliation.

    ``dsn`` is a libpq connection string. The connection is opened per call
    rather than held: a reconciliation run reads once, and a pooled handle
    left open across a slow decision is a lock this process does not need.
    """

    def __init__(self, name: str, dsn: str, *, connect_timeout: int = 10) -> None:
        self.name = name
        self._dsn = dsn
        self._connect_timeout = connect_timeout

    def read_all(self) -> "Mapping[str, str]":
        """Every card as ``{id: raw_json}``, verbatim from ``card_json``."""
        import psycopg

        with psycopg.connect(
            self._dsn, connect_timeout=self._connect_timeout
        ) as conn:
            return rows_to_cards(conn.execute(READ_ALL_SQL))

    def write(self, card_id: str, new_raw: str, expected_raw: "str | None") -> bool:
        """Always raises. See the module docstring for what unblocks this."""
        raise ReadOnlyStoreError(
            f"{self.name}: writing cards is not implemented here on purpose. "
            "A card spans 28 derived columns plus three child tables, and the "
            "projection belongs to scitex-cards. Reconciliation will write "
            "through update_task(expected_revision=...) once that verb exists "
            "(card cards-revision-lock-installed-but-no-writer-asserts-it)."
        )

# EOF
