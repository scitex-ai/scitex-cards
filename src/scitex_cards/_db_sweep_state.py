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


__all__ = [
    "SCOPE_NUDGES",
    "SCOPE_REMINDERS",
    "load_sections",
    "save_sections",
]

# EOF
