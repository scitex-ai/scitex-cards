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

import sqlite3
from pathlib import Path
from typing import Any


def _identity_on_postgres(target: str) -> dict[str, Any]:
    """The identity check against a PostgreSQL store.

    MIRRORS THE GUARD'S ORDER, uuid first -- the same requirement the SQLite
    branch states. What DROPS OUT here is the legacy path stamp: a server has no
    filesystem identity to fall back on, so on PostgreSQL the uuid is not merely
    preferred evidence, it is the ONLY evidence. An ADOPT verdict therefore says
    exactly that rather than reaching for a `store_path` row that means nothing
    across a namespace a server does not have.

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
            "hint": "run `scitex-cards db verify` for the schema report",
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
            f"store identity ADOPTABLE: {target!r} has {ident}. A PostgreSQL "
            f"store has NO path stamp to fall back on, so the uuid is the only "
            f"identity evidence there is -- unlike the SQLite branch, nothing "
            f"weaker is being consulted underneath this answer."
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

    IT MIRRORS THE GUARD'S OWN ORDER — uuid first, path only on ``ADOPT``. A
    check that disagreed with the guard would be worse than no check: it would
    either report a mismatch that is causing no refusals, or stay green through
    one that is.
    """
    from ._db import resolve_db_path
    from ._db_freshness import stamped_store_path
    from ._dual_write import _same_file
    from ._store_uuid import (
        ACCEPT,
        ENV_EXPECTED_STORE_UUID,
        REFUSE,
        expected_store_uuid,
        identity_verdict,
        read_store_uuid,
    )

    from ._store_target import resolve_store_target  # noqa: PLC0415
    from ._store_url import is_postgres_url  # noqa: PLC0415

    # POSTGRESQL BRANCHES FIRST, for the same reason its sibling in
    # _health_store does: resolve_db_path is typed `-> Path` and REFUSES a DSN
    # since #692, so on a PostgreSQL store this check RAISED before asking
    # anything. #702 fixed the canonical-store check and left THIS one -- the
    # twin -- untouched, which is the failure this module's own closing comment
    # warns about: "a split that leaves a stale twin behind is not a split."
    target = resolve_store_target(store)
    if is_postgres_url(target):
        return _identity_on_postgres(target)

    resolved = str(resolve_db_path(store))
    db_path = Path(resolve_db_path(None))
    if not db_path.exists():
        return {
            "ok": True,
            "detail": (
                f"no database at {db_path} yet — nothing to disagree with "
                f"(store_uuid=none)"
            ),
            "hint": None,
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            identity = read_store_uuid(conn)
            stamped = stamped_store_path(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "detail": f"could not read the provenance stamp from {db_path} ({exc})",
            "hint": f"check that {db_path} is readable and not corrupt",
        }

    expected = expected_store_uuid()
    # EVERY detail below carries the identity — the ok branches as well as the
    # failing ones. The registry that pairs (store_uuid, endpoint) is populated
    # from a HEALTHY board, so an identity that only a failure reveals is an
    # identity nobody can record.
    ident = f"store_uuid={identity or 'none'} expected={expected or 'none'}"
    verdict = identity_verdict(identity, expected)
    if verdict == REFUSE:
        return {
            "ok": False,
            "detail": (
                f"STORE IDENTITY MISMATCH — {db_path} carries {ident}. EVERY "
                f"WRITE AND EVERY READ IS BEING REFUSED by the ownership guard "
                f"(correctly: writing one store into another's database is how "
                f"a board gets destroyed)."
            ),
            "hint": (
                f"fix the EXPECTATION, not the database. Either unset "
                f"${ENV_EXPECTED_STORE_UUID} or set it to the identity the "
                f"store you meant actually carries, or point $SCITEX_CARDS_DB "
                f"at that store. If this database carries NO identity "
                f"(store_uuid=none) the fix is to bind it once, deliberately: "
                f"`scitex-cards store adopt-uuid`. Do NOT set "
                f"${ENV_EXPECTED_STORE_UUID} to a value this database has never "
                f"carried — that manufactures the evidence instead of checking "
                f"it. `scitex-cards resolve-store --json` prints both values."
            ),
        }
    if verdict == ACCEPT:
        return {
            "ok": True,
            "detail": (
                f"store identity accepted: {db_path} has {ident} — the path "
                f"stamp ({stamped or 'none'}) is diagnostic only and was not "
                f"consulted"
            ),
            "hint": None,
        }

    # ADOPT — no identity AND no expectation, so the legacy path stamp is the
    # best evidence available. This is where every database sits today.
    if not stamped:
        return {
            "ok": True,
            "detail": f"{db_path} carries no store stamp yet (fresh database); {ident}",
            "hint": None,
        }
    if _same_file(stamped, resolved):
        return {
            "ok": True,
            "detail": f"store and database agree: both are {resolved}; {ident}",
            "hint": None,
        }
    if not Path(stamped).exists() or not Path(resolved).exists():
        # CANNOT TELL, said honestly. The realpath string fallback used to
        # answer this case with "a DIFFERENT store" — a claim about a file this
        # mount namespace cannot even stat, and the exact falsehood the
        # operator read on 2026-07-28 about a database that was their own.
        return {
            "ok": False,
            "detail": (
                f"OWNERSHIP OF {db_path} CANNOT BE DETERMINED from a path in "
                f"this mount namespace: it is stamped for {stamped}, this "
                f"process resolves {resolved}, and at least one of those cannot "
                f"be stat'd here — so they may well be ONE file under two "
                f"names. Every write and every read is being refused. {ident}"
            ),
            "hint": (
                "do NOT re-stamp the path — that was tried on 2026-07-28 and "
                "the board came back EMPTY, which is the 2,138-card-wipe "
                "shape. Bind the store to an identity once, deliberately: "
                "`scitex-cards store adopt-uuid`, then record the printed uuid "
                "in the host registry. A uuid is the same string in both mount "
                "namespaces; a path is not. `scitex-cards db path` prints what "
                "currently resolves."
            ),
        }
    return {
        "ok": False,
        "detail": (
            f"STORE IDENTITY MISMATCH — this process resolves {resolved} but "
            f"{db_path} is stamped for {stamped}. EVERY WRITE IS BEING REFUSED "
            f"by the ownership guard (correctly: writing one store into "
            f"another's database is how a board gets destroyed). {ident}"
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


__all__ = ["_check_store_identity_agrees"]

# EOF
