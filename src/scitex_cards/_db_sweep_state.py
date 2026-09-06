#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sweep bookkeeping — reminder escalation and nudge dedup — in the database.

OPERATOR DIRECTIVE 2026-08-17: 「リマインダーと[ナッジ]のほうはデータベースを
使うようにしてください。移行をお願いします」 — put reminders and nudges in the
database. It follows his standing rule (constitution §3): state lives in the
per-host PostgreSQL on 55432, design lives in files under git, and
*"never a local database file, never JSON ledgers, never files that happen to
exist."*

WHAT WAS IN THOSE FILES, measured 2026-08-17 before the move::

    reminders.yaml   39425 B   written 53 seconds before I looked
    nudges.yaml      19558 B   written 11 minutes before

    nudges     {kind: {owner: {fingerprint, delivered_at}}}   delivery dedup
    reminders  {"owners": {...}, "cards": {<id>: {escalated}}} escalation latch

Both sat under ``runtime/``, which the constitution reserves for REGENERABLE
local state. Neither is regenerable, and the failure is not "a file is
untidy": lose the nudge dedup and every owner is re-nudged; keep it per host
and each host nudges on its own schedule, so an agent is pestered once per
machine for one card. That is the same per-host divergence that produced the
926 cards whose status depended on which host you asked.

■ ONE TABLE, ROW PER ENTRY — NOT A BLOB

Both sidecars have the identical shape ``{section: {key: record}}``, so one
table serves both, keyed ``(scope, section, entry_key)``. Storing each entry
as its own ROW rather than the document as one blob is the whole point: two
hosts that nudge different owners MERGE, where two whole-document writes
would clobber. A blob in Postgres is a JSON ledger with extra steps.

■ SYNC COLUMNS FROM CREATION, AND WHY THESE TABLES SYNC AT ALL

The operator's standing rule is that any syncable table carries
``origin_node, row_uuid, revision, updated_at, deleted_at`` FROM CREATION. I
am obeying it here rather than retrofitting, because retrofitting is exactly
the open defect on the users registry (PR #897): ``users`` has none of them
and ``SYNCED_TABLES = ("tasks", "task_comments")``, so that registry is still
per-host and I had to tell the operator the fix was insufficient.

The deliberate decision, with the reason written down as the card asked:
**both scopes SHOULD sync.** It is tempting to call nudge dedup per-host
because the delivery physically happened on a host — but the thing being
deduplicated is a message to a PERSON, and they do not care which machine
sent it. If host A nudged an owner, host B re-nudging is the defect. Same for
reminder escalation, which is a fact about a CARD and cards are fleet-wide.

■ DELETION IS SOFT, BECAUSE THE OLD WRITER REPLACED THE WHOLE DOCUMENT

``save_*_state`` used to write the entire document, so an entry absent from
the payload was gone. Reproducing that with ``DELETE`` would fight the sync
columns — a hard delete cannot be reconciled, which is what ``deleted_at`` is
for. So absent entries are stamped ``deleted_at`` and skipped on read: the
observable behaviour matches the file version, and a peer can still tell
"deleted" from "never seen".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Scope values. These are the two sidecars that used to exist.
SCOPE_REMINDERS = "reminders"
SCOPE_NUDGES = "nudges"

_TABLE = "sweep_state"

#: Created on demand, like ``_mirror_hashes._HASH_DDL`` — idempotent, and it
#: keeps this table out of the schema-ladder version machinery, which stamps
#: and migrates the CARD tables. Bookkeeping does not earn a ladder rung.
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    scope        TEXT NOT NULL,
    section      TEXT NOT NULL,
    entry_key    TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    origin_node  TEXT,
    row_uuid     TEXT,
    revision     INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT,
    deleted_at   TEXT,
    PRIMARY KEY (scope, section, entry_key)
)
"""


def _now_iso() -> str:
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _origin_node() -> str:
    import os
    import socket

    return os.environ.get("SCITEX_CARDS_ORIGIN_NODE") or socket.gethostname()


def _open(store: str | Path | None):
    """Connect to the store's DATABASE, normalising a label first.

    ``_db_target`` is imported rather than re-implemented: a ``…/tasks.yaml``
    store is a DISPLAY LABEL, and handing it to ``open_db`` raw used to CREATE
    a database at the label's path. That cost a round trip on PR #897
    and the reasoning lives with the function.
    """
    from ._db import open_db
    from ._db_users import _db_target

    conn = open_db(_db_target(store))
    conn.execute(_DDL)
    return conn


def load_sections(
    scope: str, sections: tuple[str, ...], store: str | Path | None = None
) -> dict[str, dict]:
    """Return ``{section: {entry_key: record}}`` for ``scope``. FAIL-SOFT.

    Always returns every requested section so callers can index without
    guarding — the contract the YAML loaders had, kept deliberately.

    Any failure returns empty sections and warns. That is not laziness, it is
    the property both sidecar loaders documented and the operator's standing
    rule (「カードが書けないということはなしで大丈夫です、warning で十分です」):
    *a bad sidecar must never break a sweep — the worst case is one re-push.*
    A sweep that raises because bookkeeping is unavailable would convert a
    cosmetic problem into a delivery outage.
    """
    empty: dict[str, dict] = {name: {} for name in sections}
    try:
        conn = _open(store)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("sweep-state: cannot open the store for %s: %s", scope, exc)
        return empty
    try:
        rows = conn.execute(
            f"SELECT section, entry_key, payload_json FROM {_TABLE}"
            " WHERE scope = ? AND deleted_at IS NULL",
            (scope,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("sweep-state: cannot read %s: %s", scope, exc)
        return empty
    finally:
        conn.close()

    for row in rows:
        section = row["section"]
        if section not in empty:
            continue
        try:
            empty[section][row["entry_key"]] = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            logger.warning(
                "sweep-state: %s/%s/%s has an unreadable payload: %s",
                scope,
                section,
                row["entry_key"],
                exc,
            )
    return empty


def save_sections(
    scope: str, payload: dict[str, dict], store: str | Path | None = None
) -> None:
    """Persist ``{section: {entry_key: record}}`` for ``scope``. FAIL-SOFT.

    Replace semantics, matching the whole-document write this replaces: an
    entry absent from ``payload`` is soft-deleted. Present entries are
    upserted with ``revision`` bumped and ``updated_at`` stamped.

    THE UPSERT IS VERSIONED, NOT BLIND. The operator prohibits a bare
    ``ON CONFLICT DO UPDATE`` that overwrites whatever is there; this one
    carries the row's provenance forward (``revision + 1``, a fresh
    ``updated_at``, this node as ``origin_node``) so a peer reconciling two
    writes can order them instead of guessing.
    """
    now = _now_iso()
    node = _origin_node()
    try:
        conn = _open(store)
    except Exception as exc:  # noqa: BLE001 — a failed state write must not break delivery
        logger.warning("sweep-state: cannot open the store for %s: %s", scope, exc)
        return
    try:
        keep: list[tuple[str, str]] = []
        for section, entries in (payload or {}).items():
            if not isinstance(entries, dict):
                continue
            for entry_key, record in entries.items():
                blob = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
                conn.execute(
                    f"INSERT INTO {_TABLE}"
                    " (scope, section, entry_key, payload_json, origin_node,"
                    "  row_uuid, revision, updated_at, deleted_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL)"
                    " ON CONFLICT(scope, section, entry_key) DO UPDATE SET"
                    f"  payload_json = excluded.payload_json,"
                    f"  origin_node  = excluded.origin_node,"
                    f"  revision     = {_TABLE}.revision + 1,"
                    "  updated_at   = excluded.updated_at,"
                    "  deleted_at   = NULL",
                    (
                        scope,
                        section,
                        str(entry_key),
                        blob,
                        node,
                        f"{scope}:{section}:{entry_key}",
                        now,
                    ),
                )
                keep.append((section, str(entry_key)))

        # Soft-delete what the caller dropped. Done as one pass over this
        # scope rather than a DELETE, so the tombstone survives to be synced.
        live = conn.execute(
            f"SELECT section, entry_key FROM {_TABLE}"
            " WHERE scope = ? AND deleted_at IS NULL",
            (scope,),
        ).fetchall()
        keep_set = set(keep)
        for row in live:
            pair = (row["section"], row["entry_key"])
            if pair in keep_set:
                continue
            conn.execute(
                f"UPDATE {_TABLE} SET deleted_at = ?, updated_at = ?,"
                f" revision = revision + 1, origin_node = ?"
                " WHERE scope = ? AND section = ? AND entry_key = ?",
                (now, now, node, scope, pair[0], pair[1]),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("sweep-state: cannot write %s: %s", scope, exc)
    finally:
        conn.close()


#: The claim rows' OWN scope, and it may not be shared. :func:`save_sections`
#: soft-deletes every row of a scope absent from its payload, so a claim living
#: in the nudge scope would be tombstoned by the next ``save_nudge_state`` —
#: silently, and the sweep would go back to running on every host at once while
#: looking fixed. That is the single likeliest way a correct-looking
#: implementation of this disarms itself.
SCOPE_CLAIMS = "sweep_claims"

#: Second argument to ``pg_try_advisory_xact_lock``. Deliberately in the
#: two-argument space, which ``_workspace`` documents as disjoint from the
#: one-argument keys ``_store_tx`` and ``_db_foreign_keys`` use, so a new class
#: here needs no collision proof against them.
_CLAIM_LOCK_CLASS = 0x5C1D0002


def _claim_lock_key(name: str) -> int:
    """A stable signed int32 for a sweep name, for the lock's second argument.

    Same derivation as ``_workspace._provision_lock_key`` and shifted into
    signed range for the same reason: ``pg_try_advisory_xact_lock(int, int)``
    takes int4 and a bare digest overflows it above 0x7F.
    """
    import hashlib

    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) - 0x80000000


def claim_sweep(
    name: str,
    *,
    cadence_minutes: float,
    store: str | Path | None = None,
    now: str | None = None,
) -> bool:
    """Claim ``name``'s sweep for this host, or return False if someone has it.

    ONE BOARD SHOULD PRODUCE ONE DIGEST. Three notifyd daemons sweep the same
    shared store, each keeping its own cadence in a local variable reset on
    every restart, so their phases collide and every owner receives the same
    nudge two or three times within minutes — measured on 2026-09-06, with
    digests stamped compute-01, compute-03 and compute-04 arriving together.

    A CLAIM, NOT A HELD LOCK, and the difference is the whole design. The
    cadence stamp IS the state, so nothing depends on a live lock owner: a
    crashed or absent winner simply never refreshes it, the stamp ages past the
    cadence, and the next host to tick claims it — worst case one cadence of
    delay. A held lock owned by a merely WEDGED winner (process alive,
    connection alive, sweep stuck) would block every other host forever while
    logging like a healthy quiet sweep, which is the silent-outage class this
    daemon already has on its record.

    The advisory lock is transaction-scoped for a second reason: the fleet
    primary sits behind PgBouncer in TRANSACTION mode, where a session-level
    lock outlives the client's hold on the server connection and cannot be
    released by its owner. ``pg_try_advisory_xact_lock`` is correct under both
    pooled and direct connections, so this needs no deployment assumption.

    Returns True at most once per ``cadence_minutes`` across every host.
    """
    from ._store_url import BACKEND_POSTGRES, backend_of
    from ._db_users import _db_target

    target = _db_target(store)
    if backend_of(target) != BACKEND_POSTGRES:
        # NO SHARED LOCK, NO CLAIM TO ARBITRATE. Saying True here is right:
        # a caller with no shared database has exactly one sweeper by
        # construction, and refusing would turn "cannot coordinate" into "do
        # not run", which is the reassuring-pole collapse this package keeps
        # paying for.
        return True

    conn = _open(store)
    try:
        row = conn.execute(
            "SELECT pg_try_advisory_xact_lock(?, ?) AS got",
            [_CLAIM_LOCK_CLASS, _claim_lock_key(name)],
        ).fetchone()
        if not _truthy(row):
            conn.rollback()
            return False
        stamp = now or _now_iso()
        if not _cadence_elapsed(conn, name, stamp, cadence_minutes):
            conn.rollback()
            return False
        _write_claim(conn, name, stamp)
        conn.commit()  # releases the xact lock
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("sweep-claim: cannot claim %s: %s", name, exc)
        # UNCLAIMED, NOT DENIED. A store that cannot answer must not silence
        # the sweep on every host at once; the duplicate this exists to remove
        # is far cheaper than a fleet-wide silence.
        return True
    finally:
        conn.close()


def _truthy(row) -> bool:
    """Read the lock result off a row without assuming its shape."""
    if row is None:
        return False
    if isinstance(row, dict):
        return bool(row.get("got"))
    return bool(row[0])


def _cadence_elapsed(conn, name: str, stamp: str, cadence_minutes: float) -> bool:
    """Has ``cadence_minutes`` passed since this sweep last ran anywhere?"""
    import datetime as _dt

    from ._throughput import _parse_iso

    row = conn.execute(
        f"SELECT payload_json FROM {_TABLE} "
        "WHERE scope = ? AND section = ? AND entry_key = ? AND deleted_at IS NULL",
        [SCOPE_CLAIMS, name, "claim"],
    ).fetchone()
    if row is None:
        return True
    raw = row.get("payload_json") if isinstance(row, dict) else row[0]
    try:
        last = _parse_iso(json.loads(raw).get("last_run_at"))
    except Exception:  # noqa: BLE001 -- an unreadable claim must not wedge the sweep
        return True
    if last is None:
        return True
    moment = _parse_iso(stamp) or _dt.datetime.now(_dt.timezone.utc)
    return (moment - last).total_seconds() >= cadence_minutes * 60.0


def _write_claim(conn, name: str, stamp: str) -> None:
    """Upsert this sweep's claim row, in its own scope."""
    payload = json.dumps({"last_run_at": stamp})
    conn.execute(
        f"INSERT INTO {_TABLE}"
        "(scope, section, entry_key, payload_json, origin_node, updated_at, deleted_at)"
        " VALUES(?, ?, ?, ?, ?, ?, NULL)"
        " ON CONFLICT (scope, section, entry_key) DO UPDATE SET"
        " payload_json = EXCLUDED.payload_json,"
        " origin_node = EXCLUDED.origin_node,"
        " updated_at = EXCLUDED.updated_at,"
        " deleted_at = NULL",
        [SCOPE_CLAIMS, name, "claim", payload, _origin_node(), stamp],
    )


__all__ = [
    "SCOPE_CLAIMS",
    "SCOPE_NUDGES",
    "SCOPE_REMINDERS",
    "claim_sweep",
    "load_sections",
    "save_sections",
]

# EOF
