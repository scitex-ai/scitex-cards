#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex_cards.cardsync`` — reconcile two copies of the card store.

Three hosts each hold a full copy of the store and drift apart with nothing
reconciling them. On 2026-08-10 that reached 2,341 differing rows and was
closed by hand; measured end-to-end against both live databases the same
day, the stores drift roughly a dozen rows an hour.

WHAT THIS CAN AND CANNOT DO, because the limit is structural rather than a
gap in the implementation. The store has no oplog, so there is no history to
replay — the only available method is to compare END STATES and copy the
winner. Comparing end states cannot distinguish "this card never reached me"
from "this card was removed there": both read as absence. Therefore
:func:`~._decide.decide` NEVER treats absence as deletion, and this package
offers no delete verb at all. Deletions must propagate by some other means,
and reconciliation will re-create a card that only one side deleted. That is
a known, accepted consequence of state comparison, not a bug to file.

If the store ever grows an oplog, replaying it strictly dominates this and
this package should be deleted rather than extended.

WRITES ARE NOT ENABLED HERE YET. :class:`~._pg.PgCardStore` raises
:class:`~._pg.ReadOnlyStoreError` from ``write()``. A card spans 28 derived
columns plus three child tables (``task_comments`` / ``task_edges`` /
``task_roles``), and the correct write path is
``update_task(..., expected_revision=N)`` — the compare-and-set that shipped
in 0.35.0 — not raw SQL against that projection. Routing the write through
that verb is the follow-up; until then this measures and reports.

The interesting part is :mod:`._decide`: one pure function, three-valued,
that decides which side of a disagreement wins and records why. Everything
else is I/O.

Transplanted from ``scitex_dev.cardsync`` on the operator's ruling
(2026-08-10): reconciling two copies of the card store is the card store's
own feature, not scitex-dev's. The scitex-dev copy was reverted before this
landed, so the reconciler is not forked across two packages.
"""

from __future__ import annotations

from ._apply import CardStore, ReconcileReport, reconcile
from ._decide import Side, Verdict, decide
from ._pg import PgCardStore, ReadOnlyStoreError

__all__ = [
    "CardStore",
    "PgCardStore",
    "ReadOnlyStoreError",
    "ReconcileReport",
    "Side",
    "Verdict",
    "decide",
    "reconcile",
]

# EOF
