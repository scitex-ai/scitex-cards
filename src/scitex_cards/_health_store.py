#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health checks about the STORE ITSELF — is it there, usable, and ours?

Extracted from :mod:`scitex_cards._health` (which had reached its file budget)
along the seam that module's own comment already draws:

* ``_health``       = "is the INSTALLATION wired up?" (the aggregator + the
  identity / notifyd / channel / delivery checks)
* ``_health_store`` = "is the STORE the right store, and can we use it?"
* ``_health_cards`` = "do the CARDS CONTRADICT THEMSELVES?"

THE IMPORT SURFACE DOES NOT MOVE: ``_health`` re-exports every name below, so
``from scitex_cards._health import _verify_db_store`` (which the tests do)
keeps resolving to this same object. A split that breaks its callers is a
rename with extra steps.
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


def _verify_postgres_store(target: str) -> dict[str, Any]:
    """Confirm a PostgreSQL store opens, carries `tasks`, and can be WRITTEN.

    THE SAME THREE QUESTIONS AS THE SQLITE BRANCH, re-asked for a server. Each
    one means something different here, and pretending otherwise is how a check
    starts answering a question nobody asked:

      exists    -- not a `stat()` and not the SQLite magic header. A server can
                   be reachable while holding no store at all, so the question
                   is whether this database carries the schema.
      readable  -- the COUNT(*) below either returns or it does not.
      writable  -- MEASURED, NEVER ASSERTED. This is the rule this file already
                   states in `_verify_db_store`: the word "writable" was once a
                   hardcoded literal that could not be false, and that is how
                   the 2026-07-28 outage stayed invisible while `add` refused
                   every card and health called the store writable.

    Writability is TWO conditions here, exactly as the SQLite branch needs both
    the file and its directory:

      has_table_privilege(...,'INSERT')  the role may write the table
      NOT pg_is_in_recovery()            this is not a read-only standby

    Either alone is insufficient: a grant on a hot standby still cannot write,
    and a primary still refuses a role without the grant. Both are read-only
    probes, so this reports on the store without altering it.
    """
    from ._db import connect
    from ._schema_probe import _sole_value, has_table

    try:
        conn = connect(target)
    except Exception as exc:  # noqa: BLE001 -- a failed open is a reportable state
        return {
            "ok": False,
            "detail": f"PostgreSQL store {target!r} did not open ({exc})",
            "hint": (
                "check the server is reachable and the DSN is right "
                "(`scitex-cards store resolve` shows what resolved). Do NOT "
                "point the store elsewhere to make this green -- a fresh empty "
                "target becomes a SECOND store, which is how the board was "
                f"destroyed on 2026-07-19. {type(exc).__name__}: {exc}"
            ),
        }

    try:
        if not has_table(conn, "tasks"):
            return {
                "ok": False,
                "detail": f"PostgreSQL store {target!r} has no `tasks` table",
                "hint": (
                    "the server is reachable but holds no store. Verify the "
                    "DATABASE name in the DSN before creating anything -- an "
                    "empty database that gets initialised here is a second "
                    "store, not a repair."
                ),
            }
        n = int(_sole_value(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()))
        may_insert = bool(
            _sole_value(
                conn.execute(
                    "SELECT has_table_privilege(current_user, 'tasks', 'INSERT')"
                ).fetchone()
            )
        )
        in_recovery = bool(
            _sole_value(conn.execute("SELECT pg_is_in_recovery()").fetchone())
        )
    except Exception as exc:  # noqa: BLE001 -- a failed probe is a reportable state
        return {
            "ok": False,
            "detail": f"PostgreSQL store {target!r} did not read ({exc})",
            "hint": (
                "do NOT re-initialise it -- a store that fails to read may still "
                "hold every card. `scitex-cards db verify` reports the schema. "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    if not may_insert:
        return {
            "ok": False,
            "detail": (
                f"PostgreSQL store {target!r} is readable ({n} cards) but the "
                f"current role has no INSERT on `tasks`"
            ),
            "hint": "GRANT INSERT (and UPDATE/DELETE) on the store tables to this role",
        }
    if in_recovery:
        return {
            "ok": False,
            "detail": (
                f"PostgreSQL store {target!r} is readable ({n} cards) but the "
                f"server is IN RECOVERY -- a standby, so every write will fail"
            ),
            "hint": "point the store at the primary, not a read replica",
        }
    return {
        "ok": True,
        "detail": f"PostgreSQL store {target!r} ({n} cards, readable, writable)",
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
    from ._store_target import resolve_store_target
    from ._store_url import is_postgres_url

    # POSTGRESQL BRANCHES FIRST, and it has to: `resolve_db_path` is typed
    # `-> Path` and REFUSES a DSN outright since #692, so on a PostgreSQL store
    # the health check would raise from its own first statement -- crashing on
    # exactly the backend it exists to report on. Health that cannot run against
    # the live store is worse than absent, because a crashing doctor reads as an
    # infrastructure problem rather than as "I never checked".
    target = resolve_store_target(store)
    if is_postgres_url(target):
        return _verify_postgres_store(target)

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
                "hint": (
                    f"fix the document syntax in {resolved} "
                    f"({type(exc).__name__}: {exc})"
                ),
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


# `_check_store_identity_agrees` USED to live here, beside the other store
# checks. It moved to `_health_store_identity` in the change that made store
# identity a uuid, and it is NOT re-exported from here: two definitions of
# one check is two answers, and the path-only one is the answer that took
# the board down. A split that leaves a stale twin behind is not a split.


__all__ = [
    "_check_store_canonical",
    "_is_sqlite_db",
    "_verify_db_store",
]

# EOF
