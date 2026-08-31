#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``store_identity`` health check — can this process reach its own store?

Split out of :mod:`scitex_cards._health` (which hit the 512-line cap) exactly
as ``_health_cards`` / ``_health_write_target`` / ``_health_channel_reach``
were. THE IMPORT SURFACE DOES NOT MOVE: ``_health`` re-exports
:func:`_check_store_identity_agrees`, so every caller and test keeps its
``from scitex_cards._health import ...`` line and ``health()`` still runs it
under the check name ``store_identity``.

``_health`` answers "is the INSTALLATION wired up?". This file answers the one
question inside that which has taken the board down twice: does the store this
process RESOLVED match the identity the database on disk actually carries?
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _identity_on_postgres(target: str) -> dict[str, Any]:
    """The identity check against a PostgreSQL store.

    MIRRORS THE GUARD'S ORDER, uuid first. What DROPS OUT here is the legacy
    path stamp: a server has no filesystem identity to fall back on, so the uuid
    is not merely preferred evidence, it is the ONLY evidence. An ADOPT verdict
    therefore says exactly that rather than reaching for a `store_path` row that
    means nothing across a namespace a server does not have.

    The identity is in EVERY detail string, ok branches included, for the reason
    the sibling gives: the registry pairing (store_uuid, endpoint) is populated
    from a HEALTHY board, so an identity only a failure reveals is one nobody
    can record.
    """
    from ._db import connect  # noqa: PLC0415
    from ._store_uuid import (  # noqa: PLC0415
        ACCEPT,
        ENV_EXPECTED_STORE_UUID,
        REFUSE,
        expected_store_uuid,
        identity_verdict,
        read_store_uuid,
    )

    try:
        conn = connect(target)
    except Exception as exc:  # noqa: BLE001 -- a failed open is a reportable state
        return {
            "ok": False,
            "detail": f"could not read the identity from {target!r} ({exc})",
            "hint": (
                "check the server is reachable and $SCITEX_CARDS_DB names the "
                f"right database. {type(exc).__name__}: {exc}"
            ),
        }
    try:
        identity = read_store_uuid(conn)
    except Exception as exc:  # noqa: BLE001 -- a failed read is a reportable state
        return {
            "ok": False,
            "detail": f"could not read the identity from {target!r} ({exc})",
            "hint": "run `scitex-cards dev db verify` for the schema report",
        }
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    expected = expected_store_uuid()
    ident = f"store_uuid={identity or 'none'} expected={expected or 'none'}"
    verdict = identity_verdict(identity, expected)
    if verdict == REFUSE:
        return {
            "ok": False,
            "detail": (
                f"STORE IDENTITY MISMATCH -- {target!r} carries {ident}. EVERY "
                f"WRITE AND EVERY READ IS BEING REFUSED by the ownership guard "
                f"(correctly: writing one store into another's database is how "
                f"a board gets destroyed)."
            ),
            "hint": (
                f"fix the EXPECTATION, not the database. Either unset "
                f"${ENV_EXPECTED_STORE_UUID} or set it to the identity the "
                f"store you meant actually carries, or point $SCITEX_CARDS_DB "
                f"at that store. Do NOT set ${ENV_EXPECTED_STORE_UUID} to a "
                f"value this database has never carried -- that manufactures "
                f"the evidence instead of checking it."
            ),
        }
    if verdict == ACCEPT:
        return {
            "ok": True,
            "detail": f"store identity accepted: {target!r} has {ident}",
            "hint": None,
        }
    return {
        "ok": True,
        "detail": (
            f"store identity ADOPTABLE: {target!r} has {ident}. A server "
            f"store has NO path stamp to fall back on, so the uuid is the only "
            f"identity evidence there is -- nothing weaker is being consulted "
            f"underneath this answer."
        ),
        "hint": (
            "bind it once, deliberately, with `scitex-cards store adopt-uuid`, "
            "so a future mismatch can be detected at all"
        ),
    }


def _check_store_identity_agrees(store: str | Path | None) -> dict[str, Any]:
    """Does the RESOLVED store match the identity the database carries?

    The ownership guard in :mod:`scitex_cards._dual_write` refuses EVERY read
    and EVERY write when the answer is no — correctly, since treating one
    store's database as another's is how a board gets destroyed. But the
    symptom is a total outage with no monitor, so this check surfaces it.

    On 2026-07-19 the MCP server resolved one store while the database was
    stamped for another; every write through the surface OTHER agents use was
    refused, and it went unnoticed because the maintainer's own writes used an
    explicit path. So this check answers "can this process write at all?"
    rather than the narrower "does a parseable store exist there?" that
    ``store_canonical`` answers.

    IT NAMES THE ``store_uuid`` (contract point 8, human-facing half — design
    §11). ``_run_check`` coerces every check to ``{name, ok, detail, hint}``,
    so the identity has to be IN the detail string to reach the doctor output
    at all, and a doctor that cannot name the identity cannot diagnose an
    identity mismatch — precisely the failure it exists to report.

    IT MIRRORS THE GUARD'S OWN ORDER — the uuid, and nothing weaker. A
    check that disagreed with the guard would be worse than no check: it would
    either report a mismatch that is causing no refusals, or stay green through
    one that is.
    """
    from ._store_target import resolve_store_target  # noqa: PLC0415
    from ._store_url import is_postgres_url  # noqa: PLC0415

    target = resolve_store_target(store)
    if is_postgres_url(target):
        return _identity_on_postgres(target)
    # NOT THE STORE. There is no identity to compare, and the honest answer is
    # to say so rather than to open the target and read whatever is there --
    # which is what used to happen, and what turned an unrecognised target into
    # a real, empty, query-answering board.
    return {
        "ok": False,
        "detail": (
            f"the resolved store target {target!r} does not name a store, so it "
            f"carries no identity to check (store_uuid=none)"
        ),
        "hint": (
            "point $SCITEX_CARDS_DB at the store's DSN "
            "(postgresql://...:55432/...). Do NOT create a store at this target "
            "to make the check green -- a fresh empty one becomes a SECOND "
            "store, which is how the board was destroyed on 2026-07-19."
        ),
    }


__all__ = ["_check_store_identity_agrees"]

# EOF
