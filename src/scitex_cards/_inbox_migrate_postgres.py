#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_inbox_migrate_postgres.py
"""Move a per-host SQLite inbox into the shared Postgres one, losing nothing.

What is being rescued
---------------------
Measured 2026-08-09, before the shared inbox existed::

    laptop      4901 rows, 1981 UNSEEN, 130 recipients
    compute-04   162 rows,   87 UNSEEN,  12 recipients

Those unseen rows are notifications and DMs that were enqueued, reported
delivered, and never reached anyone — including operator messages. Losing
one of them during the migration would be the same failure the migration
exists to end, so this module is written around that single risk.

Three properties, and each one is a rule the migration must not break
--------------------------------------------------------------------
**UNSEEN STAYS UNSEEN.** The seen flag is copied verbatim. A migration that
marked everything seen would "succeed" while silently discarding 2068
undelivered messages — the loudest possible version of this bug.

**IDEMPOTENT.** Keyed on the notification id with ``ON CONFLICT DO NOTHING``,
so running it twice inserts nothing the second time and never resurrects a
row the recipient has since acknowledged. Re-running must be boring; a
migration people are afraid to repeat gets run once, half-way, by someone
who then cannot tell what landed.

**PER-HOST ROWS ARE DISTINCT, NOT DUPLICATES.** The same logical event
enqueued on two hosts has two different ids, because each host generated its
own. They are two separate deliveries to the same recipient and both are
kept. Collapsing them by (recipient, ts, body) would be a guess, and a wrong
guess deletes a message.

This does NOT delete the source
-------------------------------
The SQLite file is left exactly as it was. The migration is additive so it
can be verified — and re-run — before anyone considers removing the old
inbox. Deleting the only other copy of 2068 unseen messages on the strength
of a migration nobody has audited yet is not a trade worth making.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

from ._inbox_shape import POSTGRES_SHAPE, SQLITE_SHAPE

__all__ = ["MigrationResult", "migrate_sqlite_inbox_to_postgres"]

#: The two ends have DIFFERENT names, and taking them from the shared shape
#: is not tidiness — an earlier draft of this module read `notifications`
#: from the SQLite side and would have migrated NOTHING while reporting
#: success, because the SQLite table is `inbox` and its recipient column is
#: `recipient`. A migration that silently finds zero rows is the worst
#: possible failure here: it looks exactly like "already done".
_SRC: Final = SQLITE_SHAPE
_DST: Final = POSTGRES_SHAPE

#: Destination columns, in order. `seen` is listed deliberately and
#: explicitly: it is the field whose loss would be silent and unrecoverable.
_DST_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "recipient_id",
    "event_type",
    "card_id",
    "body",
    "actor",
    "ts",
    "seen",
    "msg_id",
)

#: Source columns, positionally matched to `_DST_COLUMNS`. Only the
#: recipient differs; the rest keep their names across the move.
_SRC_COLUMNS: Final[tuple[str, ...]] = tuple(
    _SRC.recipient if c == "recipient_id" else c for c in _DST_COLUMNS
)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """What the migration actually did — counted, not assumed.

    ``read`` and ``inserted`` are reported separately so a re-run is
    self-evidently a no-op (``read`` high, ``inserted`` zero) rather than
    looking like a failure.
    """

    source: str
    read: int
    inserted: int
    skipped_existing: int
    unseen_read: int
    unseen_inserted: int

    def describe(self) -> str:
        return (
            f"{self.source}: read {self.read} ({self.unseen_read} unseen), "
            f"inserted {self.inserted} ({self.unseen_inserted} unseen), "
            f"already present {self.skipped_existing}"
        )


def _read_sqlite_rows(db_path: "str | Path") -> list[tuple]:
    """Every row from a per-host inbox, or [] when the file is absent.

    A missing file is not an error: a host that never ran the old inbox has
    nothing to migrate, and treating that as a failure would block the
    rollout on the hosts that need it least.
    """
    path = Path(db_path)
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        names = {r[1] for r in connection.execute(f"PRAGMA table_info({_SRC.table})")}
        if not names:
            return []
        # Select only columns this SQLite actually has, so an older on-disk
        # shape migrates instead of raising. Missing ones become NULL.
        available = [c for c in _SRC_COLUMNS if c in names]
        rows = connection.execute(
            f"SELECT {', '.join(available)} FROM {_SRC.table} {_SRC.order()}"
        ).fetchall()
        out: list[tuple] = []
        for row in rows:
            record = {c: (row[c] if c in available else None) for c in _SRC_COLUMNS}
            out.append(tuple(record[c] for c in _SRC_COLUMNS))
        return out
    finally:
        connection.close()


def migrate_sqlite_inbox_to_postgres(
    sqlite_path: "str | Path",
    *,
    dsn: str,
    source_label: "str | None" = None,
) -> MigrationResult:
    """Copy one host's SQLite inbox into the shared Postgres inbox.

    Additive and idempotent. Returns counts rather than a bare success flag,
    because "it worked" is not checkable and "read 4901, inserted 4901,
    unseen 1981" is.
    """
    rows = _read_sqlite_rows(sqlite_path)
    label = source_label or str(sqlite_path)
    unseen_read = sum(1 for r in rows if not r[_DST_COLUMNS.index("seen")])

    if not rows:
        return MigrationResult(label, 0, 0, 0, 0, 0)

    import psycopg

    inserted_ids: list[str] = []
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(_DST_COLUMNS))
            columns = ", ".join(_DST_COLUMNS)
            for row in rows:
                # RETURNING id fires only on an actual insert, so the
                # skipped count is measured rather than inferred from a
                # rowcount that also counts conflicts.
                cur.execute(
                    f"INSERT INTO {_DST.table}({columns}) VALUES({placeholders}) "
                    "ON CONFLICT (id) DO NOTHING RETURNING id",
                    row,
                )
                got = cur.fetchone()
                if got:
                    inserted_ids.append(got[0])
        conn.commit()

    inserted = set(inserted_ids)
    unseen_inserted = sum(
        1
        for r in rows
        if r[_DST_COLUMNS.index("id")] in inserted
        and not r[_DST_COLUMNS.index("seen")]
    )
    return MigrationResult(
        source=label,
        read=len(rows),
        inserted=len(inserted),
        skipped_existing=len(rows) - len(inserted),
        unseen_read=unseen_read,
        unseen_inserted=unseen_inserted,
    )


# EOF
