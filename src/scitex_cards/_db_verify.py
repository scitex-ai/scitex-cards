#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``db verify`` — the store's self-report: schema stamp, tables, shape.

Split out of :mod:`scitex_cards._db` when schema v5 (the DM tables) pushed that
module past its size budget. The split is along a real seam: :mod:`._db` OWNS
the schema and the connection, this module only INSPECTS what a store actually
contains. :func:`scitex_cards._db.verify` re-exports :func:`verify`, so every
existing ``from ._db import verify`` keeps resolving.

The distinction this module exists to preserve: ``schema_meta.schema_version``
is a STAMP — a number some code wrote — while the tables and the columns are the
ARTIFACT. ``ok`` requires both to agree, because a stamp can outlive the thing
it describes, and a v4 store carrying a v5 stamp is exactly the shape that makes
a missing table invisible.

THIS VERB WAS DEAD AGAINST THE STORE AND NOTHING SAID SO. Every line of it
assumed a local file: it resolved through ``resolve_db_path``, which RAISES on a
DSN; it reported ``exists`` from ``Path.exists()``; it read ``PRAGMA
user_version`` and ``PRAGMA quick_check``, which are syntax errors on the
shipping engine; and it read ``row[0]``/``row[1]``, which raises ``KeyError: 0``
on this driver's dict-shaped rows. Four independent ways to fail, so
``scitex-cards dev db verify`` could not survive its first statement. Found by
converting the tests below onto a real server, not by reading.

WHAT CHANGED IN THE REPORT, and why each is a restatement rather than a loss:

``path`` -> ``target``   The value is a DSN. The old name invited ``.parent`` /
                         ``.exists()`` on it, which is the assumption that took
                         the write path down (see ``_store_backend``).
``user_version`` ->      There is ONE stamp now; ``_read_stamps`` returns
``observed_version``     ``stamped_pragma=None`` on this engine BY DESIGN. So
                         the second field stops being a duplicate of the first
                         and becomes what this module's own docstring says it
                         wants: the ARTIFACT. ``observed_version`` walks the
                         physical shape ladder — the reading a stamp cannot lie
                         about — and ``ok`` now requires the artifact and the
                         stamp to agree.
``quick_check`` removed  It ran the previous engine's page-level scan of a local
                         file. This engine checksums its own pages and exposes
                         no client-callable equivalent, so there is nothing to
                         restate; the term is dropped from ``ok`` rather than
                         replaced by something invented. The ``observed_version``
                         term above is what keeps ``ok`` from getting weaker.
``error`` added          The verb must not raise — it is a health report, and a
                         health check that dies on an unconfigured store tells
                         the operator less than one that says so. An
                         unresolvable target, a refused one, and an unreachable
                         server are all reported here.
"""

from __future__ import annotations

from ._schema_probe import table_names

from pathlib import Path


def _first_line(exc: BaseException) -> str:
    return str(exc).splitlines()[0][:300]


def verify(explicit: str | Path | None = None) -> dict:
    """Open the store, check its stamp against its shape, and count its rows.

    Returns a JSON-friendly dict::

        {"target", "exists", "ok", "observed_version", "schema_version",
         "source", "error", "tables": {<name>: <row_count>, ...}}

    ``exists`` means THE STORE IS THERE — the target resolves, the server
    answers, and ``schema_meta`` is present. A reachable server carrying no
    tables is not a store; it is somewhere a store could be installed.

    ``ok`` is True iff the store exists, the stamp and the physical shape BOTH
    read ``SCHEMA_VERSION``, and every expected table is present.

    NEVER RAISES. Every refusal on the way in is reported in ``error`` with
    ``exists=False``.
    """
    from ._db import SCHEMA_TABLES, SCHEMA_VERSION, connect
    from ._schema_shape import observed_version
    from ._store_target import resolve_store_target

    report: dict = {
        "target": None,
        "exists": False,
        "ok": False,
        "observed_version": None,
        "schema_version": None,
        "source": None,
        "error": None,
        "tables": {},
    }
    try:
        target = resolve_store_target(explicit)
    except Exception as exc:  # noqa: BLE001 - report it, do not guess at it
        report["error"] = f"{type(exc).__name__}: {_first_line(exc)}"
        return report
    report["target"] = target

    try:
        conn = connect(target)
    except Exception as exc:  # noqa: BLE001 - a refused or unreachable target
        report["error"] = f"{type(exc).__name__}: {_first_line(exc)}"
        return report

    try:
        present = table_names(conn)
        if "schema_meta" not in present:
            report["error"] = (
                "the target names a database with no cards schema on it "
                "(schema_meta is absent); run `scitex-cards init-store`"
            )
            return report
        report["exists"] = True
        report["observed_version"] = observed_version(conn).observed
        meta = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM schema_meta").fetchall()
        }
        report["schema_version"] = meta.get("schema_version")
        report["source"] = meta.get("source")
        report["tables"] = {
            name: int(
                conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
            )
            for name in SCHEMA_TABLES
            if name in present
        }
        report["ok"] = bool(
            report["schema_version"] == str(SCHEMA_VERSION)
            and report["observed_version"] == SCHEMA_VERSION
            and all(t in present for t in SCHEMA_TABLES)
        )
    except Exception as exc:  # noqa: BLE001 - a half-built store is a report
        report["error"] = f"{type(exc).__name__}: {_first_line(exc)}"
    finally:
        conn.close()
    return report


__all__ = ["verify"]

# EOF
