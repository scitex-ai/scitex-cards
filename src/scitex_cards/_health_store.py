#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE health checks — "is this process pointed at a store it can use?"

Split out of :mod:`scitex_cards._health` (which hit the 512-line cap) exactly
as :mod:`scitex_cards._health_cards` was. THE IMPORT SURFACE DOES NOT MOVE:
``_health`` re-exports every name below, and each is the SAME object it always
was, defined next door.

``_health_store`` = "can this process READ and WRITE the store it resolved?"
``_health_cards`` = "do the CARDS CONTRADICT THEMSELVES?"
``_health``       = the aggregator, plus identity / channel / delivery checks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _is_sqlite_db(path: Path) -> bool:
    """True when ``path`` begins with the SQLite file magic header."""
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _verify_db_store(path: Path) -> dict[str, Any]:
    """Confirm the canonical database opens and carries a ``tasks`` table."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            n = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "detail": f"canonical database {path} did not open/read ({exc})",
            "hint": (
                f"do NOT overwrite it — a database that fails to open may still "
                f"hold every card, and the recovery is to COPY IT ASIDE FIRST. "
                f"Check the snapshot repo for the newest good copy, and "
                f"`scitex-cards db verify` for the schema report. "
                f"`scitex-cards init-store` creates an EMPTY store and is "
                f"correct only when there is nothing to recover. "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    # WRITABILITY IS MEASURED, NEVER ASSERTED. This probe opens the database
    # `mode=ro`, so it learns nothing about writing — yet the detail string below
    # claims "writable". That word used to be a hardcoded literal, so it could
    # never be false: "a gate that cannot fail is not a gate ... the same as
    # deleting it, except worse: the config still lists it and everyone believes
    # it is working" (constitution §2). It is exactly how the 2026-07-28
    # create-path outage stayed invisible — `add` refused every card while health
    # cheerfully reported the same store readable AND writable. The sibling
    # file-store branch already measures this with `os.access`; this branch now
    # matches it. SQLite also writes `-wal` / `-journal` SIBLINGS, so the
    # DIRECTORY must be writable too: a writable file in a read-only directory
    # still fails every write.
    if not os.access(path, os.W_OK):
        return {
            "ok": False,
            "detail": (
                f"canonical store {path} is NOT writable "
                f"(SQLite, {n} cards, readable) — every card write will fail"
            ),
            "hint": f"fix permissions so {path} is writable (e.g. chmod u+w {path})",
        }
    if not os.access(path.parent, os.W_OK):
        return {
            "ok": False,
            "detail": (
                f"canonical store {path} is readable but its directory "
                f"{path.parent} is NOT writable (SQLite, {n} cards) — SQLite "
                f"cannot create the -wal/-journal siblings a write needs"
            ),
            "hint": (
                f"make the store's directory writable (e.g. chmod u+w {path.parent})"
            ),
        }
    return {
        "ok": True,
        "detail": f"canonical store {path} (SQLite, {n} cards, readable, writable)",
        "hint": None,
    }


def _check_store_canonical(store: str | Path | None) -> dict[str, Any]:
    """Resolve the task store and verify it is the canonical, healthy store.

    The canonical store is the SQLite database ($SCITEX_CARDS_DB). ok when it
    exists, opens, and carries a ``tasks`` table. An EXPLICIT file store (tests,
    ``--tasks <file>``) is taken as the intended target and checked as a
    serialized document with a top-level ``tasks`` key.
    """
    from ._db import resolve_db_path
    from ._paths import resolve_tasks_path

    db = Path(resolve_db_path(store))

    # The canonical store IS the database — verify it directly.
    if db.exists() and _is_sqlite_db(db):
        return _verify_db_store(db)

    # No database. An EXPLICIT file store (tests / `--tasks <file>`) is checked
    # as a serialized document; otherwise the store is genuinely absent.
    resolved = resolve_tasks_path(store)
    if store is not None and resolved.exists():
        if _is_sqlite_db(resolved):
            return _verify_db_store(resolved)
        if not os.access(resolved, os.R_OK):
            return {
                "ok": False,
                "detail": f"store {resolved} is not readable",
                "hint": f"fix permissions so {resolved} is readable (e.g. chmod u+r)",
            }
        if not os.access(resolved, os.W_OK):
            return {
                "ok": False,
                "detail": f"store {resolved} is not writable",
                "hint": f"fix permissions so {resolved} is writable (e.g. chmod u+w)",
            }
        from ._yaml import safe_load

        try:
            with resolved.open(encoding="utf-8") as handle:
                data = safe_load(handle) or {}
        except Exception as exc:  # noqa: BLE001 — a parse fail is a reportable state
            return {
                "ok": False,
                "detail": f"store {resolved} did not parse ({exc})",
                "hint": f"fix the document syntax in {resolved} ({type(exc).__name__}: {exc})",
            }
        if not isinstance(data, dict) or "tasks" not in data:
            return {
                "ok": False,
                "detail": f"store {resolved} has no top-level 'tasks' key",
                "hint": f"add a top-level `tasks:` list to {resolved}",
            }
        return {
            "ok": True,
            "detail": f"file store {resolved} (exists, readable, writable, parses)",
            "hint": None,
        }

    return {
        "ok": False,
        "detail": f"no store: the database {db} is absent",
        "hint": (
            "if this agent should have the FLEET board, the path is wrong — "
            "fix $SCITEX_CARDS_DB rather than creating a store, because a fresh "
            "empty one here becomes a SECOND store, which is how the board was "
            "destroyed on 2026-07-19. `scitex-cards db path` shows what resolved. "
            "Only when this agent genuinely owns a new, separate store is "
            "`scitex-cards init-store` correct. Restoring from a `scitex-cards "
            "db export` dump has NO CLI verb today — it is a Python-level "
            "operation (see scitex_cards._db_bootstrap) — so do not go looking "
            "for an import subcommand."
        ),
    }


def _check_store_identity_agrees(store: str | Path | None) -> dict[str, Any]:
    """Does the RESOLVED store match the identity the database is stamped with?

    The database records WHICH STORE it is the database of (its provenance
    stamp). When the store this process resolves disagrees with that stamp, the
    ownership guard in ``_dual_write`` / ``_store_backend`` refuses EVERY write —
    correctly, since writing one store's rows into another store's database is
    how a board gets destroyed. But the symptom is a total write outage with no
    monitor, so this check surfaces it.

    On 2026-07-19 the MCP server resolved one store while the database was
    stamped for another; every write through the surface OTHER agents use was
    refused, and it went unnoticed because the maintainer's own writes used an
    explicit path. So this check answers "can this process write at all?" rather
    than the narrower "does a parseable store exist there?" that
    ``store_canonical`` answers.
    """
    import sqlite3

    from ._db import resolve_db_path
    from ._db_freshness import stamped_store_path
    from ._dual_write import _same_file

    resolved = str(resolve_db_path(store))
    db_path = Path(resolve_db_path(None))
    if not db_path.exists():
        return {
            "ok": True,
            "detail": f"no database at {db_path} yet — nothing to disagree with",
            "hint": None,
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            stamped = stamped_store_path(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "detail": f"could not read the provenance stamp from {db_path} ({exc})",
            "hint": f"check that {db_path} is readable and not corrupt",
        }
    if not stamped:
        return {
            "ok": True,
            "detail": f"{db_path} carries no store stamp yet (fresh database)",
            "hint": None,
        }
    if _same_file(stamped, resolved):
        return {
            "ok": True,
            "detail": f"store and database agree: both are {resolved}",
            "hint": None,
        }
    return {
        "ok": False,
        "detail": (
            f"STORE IDENTITY MISMATCH — this process resolves {resolved} but "
            f"{db_path} is stamped for {stamped}. EVERY WRITE IS BEING REFUSED "
            f"by the ownership guard (correctly: writing one store into "
            f"another's database is how a board gets destroyed)."
        ),
        "hint": (
            f"decide which is right and make them agree, and change the POINTER "
            f"rather than the stamp unless you are certain: re-stamping tells a "
            f"database it belongs to a different store, which is the assertion "
            f"the ownership guard exists to doubt. If {db_path} is the database "
            f"this agent should use, point $SCITEX_CARDS_DB at {stamped} so the "
            f"resolved store matches the stamp. If {resolved} is genuinely the "
            f"intended store, the database for it is a DIFFERENT file — find or "
            f"create that one rather than re-labelling this database. "
            f"`scitex-cards db path` prints what currently resolves."
        ),
    }


__all__ = [
    "_check_store_canonical",
    "_check_store_identity_agrees",
    "_is_sqlite_db",
    "_verify_db_store",
]

# EOF
