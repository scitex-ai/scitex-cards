#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which backend is this process ACTUALLY on — and do its two rails agree?

Operator directive 2026-08-02: "fail fast, fail loud, no fallbacks", and
"sqlite モードか postgres モードか、doctor コマンドなどで検査できると良い".

TWO RAILS, AND THEY CAN DISAGREE. The card store follows ``$SCITEX_CARDS_DB``
and may be a PostgreSQL server. The notification inbox does NOT: it is a SQLite
sidecar at ``runtime_dir(store)/todo.db``, chosen by a path derived from the
store rather than by the store's own backend. So a fleet pointed at PostgreSQL
runs its cards on PostgreSQL and its notifications on SQLite, and nothing said
so anywhere.

That split is not cosmetic. Measured 2026-08-01, it is what let the operator's
DM reach ``dm_messages`` and never reach the agent: the inbox rail failed on its
own, in its own database, for its own reason (a SQL spelling the host's older
SQLite cannot parse), while every card-side check stayed green.

WHY THIS CHECK FAILS RATHER THAN INFORMS. A check that merely PRINTS the two
modes would report today's split as normal, and "normal" is precisely the wrong
word for it. So a disagreement is ``ok: False``. The doctor stays red for as
long as the two rails are on different engines, and goes green when the inbox
moves into the store — which is the actual remedy, not a configuration knob.

WHAT THIS DOES NOT DO. It does not offer a way to "turn the old SQL off",
because there is no toggle to turn: the inbox backend is not selected by
configuration in postgres mode, it is the only implementation there is. A knob
here would be a fallback wearing a switch. The honest report is that the rail is
SQLite regardless of what you set, and the fix is to move it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: What the card store is on, and what the inbox rail is on, as short tokens.
POSTGRES = "postgres"
SQLITE = "sqlite"


def _store_mode(store: str | Path | None) -> tuple[str, str]:
    """Return ``(mode, target)`` for the CARD store."""
    from ._store_target import resolve_store_target
    from ._store_url import is_postgres_url

    target = str(resolve_store_target(store))
    return (POSTGRES if is_postgres_url(target) else SQLITE), target


def _which_tier_won(store: str | Path | None, resolved: str) -> str:
    """Name the tier that supplied ``resolved`` — the store target's SOURCE.

    "I edited the config and nothing changed" is the single most confusing way
    this resolution fails, because every tier is individually working: the
    environment simply outranks the file. Naming the winner turns that into one
    line of output instead of an investigation.

    DETERMINED BY COMPARISON, NOT BY RE-IMPLEMENTING THE PRECEDENCE. A second
    copy of the ordering here would be a second thing to keep in step with
    :func:`scitex_cards._store_target.resolve_store_target`, and the two would
    eventually disagree — at which point this line would confidently name the
    wrong source. So it asks each tier what it holds and reports the
    highest-ranked one whose value MATCHES what actually resolved. When two
    tiers hold the same value the higher one is named, which is also what wins.
    """
    import os

    if store is not None:
        return "an explicit argument"

    from ._db import ENV_DB

    env_value = os.environ.get(ENV_DB)
    if env_value and str(env_value) == resolved:
        return f"the {ENV_DB} environment variable"

    try:
        from ._config import CONFIG_NAME, store_config_target

        if store_config_target() == resolved:
            return f"{CONFIG_NAME} (store.target)"
    except Exception:  # noqa: BLE001 — a doctor must not crash the caller
        pass

    if env_value:
        return f"the {ENV_DB} environment variable"
    return "the built-in default"


def _inbox_mode(store: str | Path | None) -> tuple[str, str]:
    """Return ``(mode, location)`` for the NOTIFICATION inbox rail.

    Reports what the rail IS, by asking the same function the rail itself calls,
    rather than what a setting says it should be.
    """
    from ._inbox import _use_sqlite

    if not _use_sqlite():
        from ._paths import runtime_dir

        return "yaml", str(runtime_dir(store, create=False) / "inboxes.json")

    from ._inbox_sqlite import inbox_db_path

    return SQLITE, str(inbox_db_path(store))


def check_backend_mode(store: str | Path | None = None) -> dict[str, Any]:
    """Report both rails' engines; fail when they disagree."""
    try:
        store_mode, target = _store_mode(store)
        inbox_mode, inbox_where = _inbox_mode(store)
    except Exception as exc:  # noqa: BLE001 — a doctor must not crash the caller
        return {
            "ok": False,
            "detail": f"could not determine backend mode: {type(exc).__name__}: {exc}",
            "hint": "run `scitex-cards resolve-store` to see what the store resolves to",
        }

    source = _which_tier_won(store, target)

    if store_mode == inbox_mode:
        return {
            "ok": True,
            "detail": (
                f"both rails on {store_mode}: cards at {target} "
                f"(chosen by {source}), notification inbox at {inbox_where}"
            ),
            "hint": None,
        }

    return {
        "ok": False,
        "detail": (
            f"SPLIT BACKENDS — cards are on {store_mode} ({target}, chosen by "
            f"{source}) but the "
            f"notification inbox is on {inbox_mode} ({inbox_where}). The inbox "
            "rail is a file sidecar located from the store PATH, so pointing "
            "the store at a server does not move it. Card writes and "
            "notification writes therefore land in different engines, fail "
            "independently, and a green card-side check says nothing about "
            "whether notifications are being delivered — measured 2026-08-01, "
            "when a DM committed to the store and no notification was ever "
            "created."
        ),
        "hint": (
            "There is NO setting to correct this: in postgres mode the SQLite "
            "sidecar is the only inbox implementation that exists, so any "
            "toggle would be a fallback wearing a switch. The remedy is to move "
            "the inbox table INTO the card store, tracked on "
            "cards-inbox-rail-must-live-in-postgres-drop-todo-db-20260802. "
            "Until then, treat notification delivery as unverified by this "
            "doctor and confirm it end to end."
        ),
    }


__all__ = ["check_backend_mode", "POSTGRES", "SQLITE"]

# EOF
