#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``db verify`` — the store's self-report: schema stamp, tables, integrity.

Split out of :mod:`scitex_cards._db` when schema v5 (the DM tables) pushed that
module past its size budget. The split is along a real seam: :mod:`._db` OWNS
the schema and the connection, this module only INSPECTS what a file actually
contains. :func:`scitex_cards._db.verify` re-exports :func:`verify`, so every
existing ``from ._db import verify`` keeps resolving.

The distinction this module exists to preserve: ``PRAGMA user_version`` and
``schema_meta.schema_version`` are STAMPS — numbers some code wrote — while the
table list is the ARTIFACT. ``ok`` requires both to agree, because a stamp can
outlive the thing it describes and a v4 file carrying a v5 stamp is exactly the
shape that makes a missing table invisible.
"""

from __future__ import annotations

from pathlib import Path


def verify(explicit: str | Path | None = None) -> dict:
    """Open the DB read/verify its integrity + report table row counts.

    Returns a JSON-friendly dict::

        {"path", "exists", "ok", "user_version", "schema_version",
         "quick_check", "tables": {<name>: <row_count>, ...}, "source"}

    ``ok`` is True iff the file exists, ``user_version`` and the
    ``schema_meta.schema_version`` both equal ``SCHEMA_VERSION``, every
    expected table is present, and ``PRAGMA quick_check`` returns ``ok``.
    Never raises on a merely-absent DB (``exists=False``, ``ok=False``).
    """
    from ._db import SCHEMA_TABLES, SCHEMA_VERSION, connect, resolve_db_path

    path = resolve_db_path(explicit)
    report: dict = {
        "path": str(path),
        "exists": path.exists(),
        "ok": False,
        "user_version": None,
        "schema_version": None,
        "quick_check": None,
        "source": None,
        "tables": {},
    }
    if not path.exists():
        return report

    conn = connect(path)
    try:
        report["user_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
        report["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        tables: dict[str, int] = {}
        for name in SCHEMA_TABLES:
            if name in present:
                tables[name] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                )
        report["tables"] = tables
        meta = {
            row[0]: row[1] for row in conn.execute("SELECT key, value FROM schema_meta")
        }
        report["schema_version"] = meta.get("schema_version")
        report["source"] = meta.get("source")
        all_tables_present = all(t in present for t in SCHEMA_TABLES)
        report["ok"] = bool(
            report["user_version"] == SCHEMA_VERSION
            and report["schema_version"] == str(SCHEMA_VERSION)
            and all_tables_present
            and report["quick_check"] == "ok"
        )
    finally:
        conn.close()
    return report


__all__ = ["verify"]

# EOF
