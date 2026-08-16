#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which backend is this process ACTUALLY on — and do its two rails agree?

Operator directive 2026-08-02: "fail fast, fail loud, no fallbacks", and
"sqlite モードか postgres モードか、doctor コマンドなどで検査できると良い".

TWO RAILS, AND THEY CAN DISAGREE. The card store follows ``$SCITEX_CARDS_DB``
and may be a PostgreSQL server. The notification inbox does NOT: it is a SQLite
sidecar at ``runtime_dir(store)/cards.db``, chosen by a path derived from the
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

    IT ASKS A THREE-VALUED QUESTION, because the rail has three backends. This
    function used to ask ``_use_sqlite()`` — two-valued — and map its ``False``
    onto "yaml". After #780 that ``False`` means POSTGRES far more often than it
    means yaml, so the doctor reported the fleet's inbox as a JSON sidecar,
    named a path that DID NOT EXIST on disk, and declared a SPLIT that had
    already been closed. Measured 2026-08-11 on this container: rail on
    postgres, doctor saying ``yaml (…/runtime/inboxes.json)``.

    That is the same class of error the check exists to catch — a report about a
    database nobody is using — pointed the other way. A doctor that cannot go
    green when the patient recovers gets ignored, which costs exactly as much as
    one that cannot go red.
    """
    from ._inbox_backend import POSTGRES as INBOX_POSTGRES
    from ._inbox_backend import SQLITE as INBOX_SQLITE
    from ._inbox_backend import backend

    active = backend()
    if active == INBOX_POSTGRES:
        from ._inbox_postgres import _safe_dsn, resolve_dsn

        try:
            return POSTGRES, _safe_dsn(resolve_dsn(store))
        except Exception as exc:  # noqa: BLE001 — a doctor must not crash
            # SELECTED BUT UNREACHABLE IS ITS OWN ANSWER, and it must not be
            # rendered as the SQLite sidecar: "the rail is a file" and "the rail
            # is a server I cannot reach" call for opposite actions.
            return POSTGRES, f"unresolved ({type(exc).__name__}: {exc})"
    if active == INBOX_SQLITE:
        # `inbox_target`, not `inbox_db_path`: the doctor must name WHERE THE
        # RAIL ACTUALLY IS. Naming `runtime/cards.db` after the rail moved would
        # send a reader to inspect an empty file and conclude the notifications
        # were lost — which is the same "report about a database nobody is
        # using" this function exists to prevent.
        from ._inbox_sqlite_schema import inbox_target

        return SQLITE, str(inbox_target(store))
    from ._paths import runtime_dir

    return "yaml", str(runtime_dir(store, create=False) / "inboxes.json")


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

    if inbox_mode == POSTGRES:
        # The inbox is on a SERVER while the cards are on a file. This is not
        # the sidecar split below and must not borrow its remedy: nothing here
        # is "located from the store PATH", and the fix is to point the STORE at
        # the same server, not to move the inbox.
        return {
            "ok": False,
            "detail": (
                f"SPLIT BACKENDS, THE OTHER WAY ROUND — the notification inbox "
                f"is on postgres ({inbox_where}) but the cards are on "
                f"{store_mode} ({target}, chosen by {source}). Notifications "
                "reference cards that the notification database has never seen, "
                "so no read can join the two and no transaction can span them."
            ),
            "hint": (
                "Point $SCITEX_CARDS_DB at the same server the inbox uses, so "
                "a notification and the card it is about live in one database."
            ),
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
            "cards-inbox-rail-must-live-in-postgres-drop-cards-db-20260802. "
            "Until then, treat notification delivery as unverified by this "
            "doctor and confirm it end to end."
        ),
    }


__all__ = ["check_backend_mode", "POSTGRES", "SQLITE"]

# EOF
