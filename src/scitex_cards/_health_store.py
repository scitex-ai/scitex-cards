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
``from scitex_cards._health import _verify_postgres_store`` keeps resolving to
this same object. A split that breaks its callers is a rename with extra steps.
"""

from __future__ import annotations

from ._store_url import describe_store_target

import os
from pathlib import Path
from typing import Any


def _verify_postgres_store(target: str) -> dict[str, Any]:
    """Confirm a PostgreSQL store opens, carries `tasks`, and can be WRITTEN.

    THREE QUESTIONS, each asked of a SERVER. Every one means something other
    than the filesystem question it replaced, and pretending otherwise is how a
    check starts answering a question nobody asked:

      exists    -- not a `stat()`. A server can be reachable while holding no
                   store at all, so the question is whether this database
                   carries the schema.
      readable  -- the COUNT(*) below either returns or it does not.
      writable  -- MEASURED, NEVER ASSERTED. The word "writable" was once a
                   hardcoded literal in this file that could not be false, and
                   that is how the 2026-07-28 outage stayed invisible while
                   `add` refused every card and health called the store
                   writable.

    Writability is TWO conditions:

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
            "detail": f"PostgreSQL store {describe_store_target(target)!r} did not open ({exc})",
            "hint": (
                "check the server is reachable and that $SCITEX_CARDS_DB names "
                "the right database. NOTE `scitex-cards dev db get-path` does NOT help "
                "here -- it resolves a filesystem path and refuses a DSN. Do NOT "
                "point the store elsewhere to make this green -- a fresh empty "
                "target becomes a SECOND store, which is how the board was "
                f"destroyed on 2026-07-19. {type(exc).__name__}: {exc}"
            ),
        }

    try:
        if not has_table(conn, "tasks"):
            return {
                "ok": False,
                "detail": f"PostgreSQL store {describe_store_target(target)!r} has no `tasks` table",
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
            "detail": f"PostgreSQL store {describe_store_target(target)!r} did not read ({exc})",
            "hint": (
                "do NOT re-initialise it -- a store that fails to read may still "
                "hold every card. `scitex-cards dev db verify` reports the schema. "
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
                f"PostgreSQL store {describe_store_target(target)!r} is readable ({n} cards) but the "
                f"current role has no INSERT on `tasks`"
            ),
            "hint": "GRANT INSERT (and UPDATE/DELETE) on the store tables to this role",
        }
    if in_recovery:
        return {
            "ok": False,
            "detail": (
                f"PostgreSQL store {describe_store_target(target)!r} is readable ({n} cards) but the "
                f"server is IN RECOVERY -- a standby, so every write will fail"
            ),
            "hint": "point the store at the primary, not a read replica",
        }
    return {
        "ok": True,
        "detail": f"PostgreSQL store {describe_store_target(target)!r} ({n} cards, readable, writable)",
        "hint": None,
    }


def _check_store_canonical(store: str | Path | None) -> dict[str, Any]:
    """Resolve the task store and verify it is the canonical, healthy store.

    The canonical store is the database named by ``$SCITEX_CARDS_DB``. ok when
    it is reachable, carries a ``tasks`` table, and the role can write it. An
    EXPLICIT file store (tests, ``--tasks <file>``) is taken as the intended
    target and checked as a serialized document with a top-level ``tasks`` key.
    """
    from ._paths import resolve_tasks_path
    from ._store_target import resolve_store_target
    from ._store_url import is_postgres_url

    # THE STORE BRANCHES FIRST, and it has to: this check must be able to run
    # against the live store. Health that cannot run against the live store is
    # worse than absent, because a crashing doctor reads as an infrastructure
    # problem rather than as "I never checked".
    target = resolve_store_target(store)
    if is_postgres_url(target):
        return _verify_postgres_store(target)

    # NOT THE STORE. An EXPLICIT file store (tests / `--tasks <file>`) is checked
    # as a serialized document; otherwise the target names no store at all.
    resolved = resolve_tasks_path(store)
    if store is not None and resolved.exists():
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
        "detail": f"no store: {describe_store_target(target)!r} does not name a reachable store",
        "hint": (
            "if this agent should have the FLEET board, the target is wrong — "
            "fix $SCITEX_CARDS_DB rather than creating a store, because a fresh "
            "empty one here becomes a SECOND store, which is how the board was "
            "destroyed on 2026-07-19. `scitex-cards dev db get-path` shows "
            "what resolved. "
            "Only when this agent genuinely owns a new, separate store is "
            "`scitex-cards init-store` correct. Restoring from a `scitex-cards "
            "dev db export` dump has NO CLI verb today — it is a Python-level "
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
    "_verify_postgres_store",
]

# EOF
