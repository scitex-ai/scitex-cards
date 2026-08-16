#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone per-recipient pull-inbox for card-message delivery.

scitex-cards MUST deliver card-messages to its members with ZERO dependency
on any external agent runtime. The existing push rail
(:func:`scitex_cards._push.deliver`) POSTs directly to an agent's turn URL —
which CANNOT reach a *containerized* agent (the agent subscribes outbound to
a bus; a direct inbound POST is refused). The standalone-safe delivery model
is therefore **PULL**: the C4 dispatcher ENQUEUEs a notification record into
the recipient's inbox here, and the recipient's scitex-cards client POLLs the
board (via the ``poll_notifications`` MCP tool or, later, an HTTP endpoint)
for its pending notifications. The out-of-band push rail stays an OPTIONAL
parallel ACCELERATOR for host-reachable agents — never a dependency.

Storage
-------
This module is the (non-default, break-glass) file-backed inbox
implementation, selected only via ``SCITEX_CARDS_INBOX_BACKEND=yaml``
(the default is SQLite — see :mod:`scitex_cards._inbox_sqlite`).
Inboxes live in their own ``inboxes.json`` SIDECAR next to the task
store, keyed by recipient id: ``{"inboxes": {"u_3f9a1c0b7e42": [{"id":
..., "event_type": ..., "card_id": ..., "body": ..., "actor": ...,
"ts": ..., "seen": bool}, ...]}}``. A pre-existing legacy embedded
``inboxes:`` section (from the old monolithic task-store document)
migrates into ``inboxes.json`` ONCE on first access — see
:func:`_migrate_legacy_yaml_once`; no permanent YAML fallback. The
write path uses its own atomic tmp+fsync+reparse-verify dance and its
own lock (mirrors :mod:`scitex_cards._threads`'s ``threads.json``).

Hard standalone constraint
---------------------------
ZERO external-runtime / fleet imports. This module is the STANDALONE default
delivery sink and works with no external runtime present.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from ._model import _store_lock
from ._paths import resolve_tasks_path

logger = logging.getLogger(__name__)

#: Top-level store key holding the per-recipient inboxes mapping.
_INBOXES_KEY = "inboxes"

#: Env var selecting the inbox storage backend. The DEFAULT is now ``sqlite``
#: (the Phase-1 backend in :mod:`scitex_cards._inbox_sqlite`): a 5 s digest poll
#: is then an indexed ``(recipient, seen)`` lookup on
#: ``<store_dir>/runtime/todo.db`` instead of a full sidecar parse. This
#: module (the file-backed break-glass backend, its own ``inboxes.json``
#: sidecar — see the module docstring) is selected ONLY by
#: ``SCITEX_CARDS_INBOX_BACKEND=yaml`` (the value is a historical name for
#: "not sqlite"; the on-disk format itself is JSON — see the module
#: docstring); unset (or any other value) uses SQLite. There is NO silent
#: fallback: when the SQLite backend raises, the error PROPAGATES
#: (constitution: fail fast, fail loud). The SQLite path lazily
#: auto-migrates legacy embedded ``inboxes:`` records on first access, so
#: flipping the default never loses unseen notifications. See the incident
#: card ``store-sqlite-migration-o1-writes-future-20260701``.
_ENV_INBOX_BACKEND = "SCITEX_CARDS_INBOX_BACKEND"


def _use_sqlite() -> bool:
    """Back-compat shim. Prefer :func:`._inbox_backend.backend`.

    Two-valued, so it cannot express the Postgres case — which is how a
    third option gets silently folded into one of the other two. Kept only
    for callers outside this module.
    """
    from ._inbox_backend import SQLITE, backend

    return backend() == SQLITE


def _use_postgres() -> bool:
    """True when the SHARED inbox is in force (see ``_inbox_backend``)."""
    from ._inbox_backend import POSTGRES, backend

    return backend() == POSTGRES


#: Stable notification-id prefix (``n_`` + 12 hex chars, 48 bits entropy) —
#: mirrors the ``u_`` user-id shape so ids are visually distinguishable.
_NOTIFY_ID_PREFIX = "n_"

#: Number of hex chars in the random token portion of a notification id.
_NOTIFY_ID_TOKEN_HEX = 12


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path through the same chain the task/user API uses."""
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


#: Sidecar filename, sibling of the resolved task store.
_INBOXES_FILENAME = "inboxes.json"


def _inboxes_path(store: str | Path | None) -> Path:
    """Resolve the sidecar path: ``<store_dir>/inboxes.json``.

    Runs the one-time legacy-migration check before returning (see
    :func:`_migrate_legacy_yaml_once`).
    """
    tasks = _resolved_store(store)
    path = tasks.parent / _INBOXES_FILENAME
    _migrate_legacy_yaml_once(path, tasks)
    return path


# The LEGACY-YAML readers live in _inbox_migrate.py — they are migration
# concerns, not inbox concerns, and they exist only for stores that predate the
# split. Re-exported because `_inboxes_path` above calls
# `_migrate_legacy_yaml_once` and the tests import both by these names from
# here. `_inbox_migrate` already imported `_read_legacy_embedded_inboxes` back
# out of this module, which was the tell that it sat on the wrong side of the
# seam. Function-local imports on both sides keep the mutual reference from
# becoming a load-time cycle.
from ._inbox_migrate import (  # noqa: E402,F401
    _migrate_legacy_yaml_once,
    _read_legacy_embedded_inboxes,
)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with the canonical ``Z`` suffix.

    Identical second-resolution shape to ``_store._utc_now_iso`` /
    ``_users._store._utc_now_iso`` so notification timestamps match task +
    user timestamps on disk; re-implemented locally to keep this module free
    of an import cycle with the mutation layer.
    """
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _generate_notification_id() -> str:
    """Generate a fresh notification id (``n_`` + 12 hex chars)."""
    return _NOTIFY_ID_PREFIX + secrets.token_hex(_NOTIFY_ID_TOKEN_HEX // 2)


def _load_inboxes_section(path: Path) -> dict[str, list[dict]]:
    """Read the inboxes sidecar off disk (absent / malformed → {}).

    Defensive: a missing file, an absent ``inboxes:`` key, or a non-mapping
    value all yield an empty mapping; per-recipient values that are not
    lists are coerced to ``[]`` so a malformed row never breaks a poll.
    """
    if not path.exists():
        return {}
    import json

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle) or {}
    raw = data.get(_INBOXES_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for recipient_id, records in raw.items():
        if not isinstance(recipient_id, str) or not recipient_id:
            continue
        if not isinstance(records, list):
            out[recipient_id] = []
            continue
        out[recipient_id] = [r for r in records if isinstance(r, dict)]
    return out


def _save_inboxes_unlocked(inboxes: dict[str, list[dict]], path: Path) -> None:
    """Crash-safe write of the whole inboxes sidecar document.

    Mirrors ``_threads._save_threads_unlocked``: dump to a sibling ``.tmp``,
    fsync, REPARSE the tmp bytes and verify the recipient count matches,
    then ``os.replace`` (POSIX-atomic) into place. Direct callers MUST
    already hold ``_store_lock(path)`` (the one-time legacy-migration
    caller is the sole exception).
    """
    import json

    doc = {_INBOXES_KEY: inboxes}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = json.load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did "
                f"not reparse cleanly after dump "
                f"({type(verify_exc).__name__}: {verify_exc}). Canonical "
                f"file left untouched."
            ) from verify_exc
        verify_inboxes = (
            verify_doc.get(_INBOXES_KEY) if isinstance(verify_doc, dict) else None
        )
        if not isinstance(verify_inboxes, dict) or len(verify_inboxes) != len(inboxes):
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected inboxes payload. Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _is_duplicate(
    records: list[dict], *, event_type: str, card_id: Any, ts: Any, actor: Any
) -> bool:
    """Return True if ``records`` already holds the (type,card,ts,actor) key.

    The dedup key is ``(event_type, card_id, ts, actor)`` — a re-emit of the
    SAME card-event (same instant, same cause) must not double-enqueue.
    Distinct timestamps (two genuine events) are kept separately.
    """
    for r in records:
        if (
            r.get("event_type") == event_type
            and r.get("card_id") == card_id
            and r.get("ts") == ts
            and r.get("actor") == actor
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# Public inbox API                                                            #
# --------------------------------------------------------------------------- #
def enqueue(
    recipient_id: str,
    *,
    event_type: str,
    card_id: str,
    body: str,
    actor: str | None,
    ts: str | None = None,
    supersede: bool = False,
    msg_id: str | None = None,
    store: str | Path | None = None,
) -> "dict | None":
    """Append a notification record to ``recipient_id``'s inbox (STANDALONE).

    The standalone delivery sink: always works, no network. Builds a record
    ``{id, event_type, card_id, body, actor, ts, seen: False}`` and appends
    it to the recipient's inbox under the shared store lock.

    Idempotent-ish: dedups on the ``(event_type, card_id, ts, actor)`` key so
    a re-emit of the SAME card-event (same instant + cause) does not
    double-enqueue. A fresh ``ts`` (the default — :func:`_utc_now_iso`) makes
    two genuine events distinct; pass an explicit ``ts`` (the event's own
    timestamp) to make the dedup deterministic across a re-dispatch.

    Parameters
    ----------
    recipient_id : str
        The inbox key — a stable ``u_*`` user id OR a raw-name fallback (the
        same identifier :func:`scitex_cards._notify.resolve_recipients`
        returns). A falsy id is a no-op (returns ``None``).
    event_type : str
        The card-event type (``reassigned`` / ``completed`` / …).
    card_id : str
        The card the event is about.
    body : str
        The human-readable notification text (built by the dispatcher).
    actor : str | None
        Who caused the event (a raw name), or ``None``.
    ts : str | None
        The notification timestamp; defaults to now (``Z``-suffixed ISO).
    supersede : bool
        For a CUMULATIVE snapshot record (a periodic *digest*: one
        point-in-time list of an owner's open cards, re-sent every tick). When
        ``True``, BEFORE appending, every EXISTING record that is still
        UNSEEN and matches BOTH ``event_type`` AND ``card_id`` of the new
        record is REMOVED — the new snapshot strictly replaces its unseen
        predecessors, so at most ONE pending digest per recipient survives.
        A recipient whose channel is down therefore never accumulates a
        replay-storm of stale digests. SEEN records are left untouched
        (history preserved; archival is a separate concern). Only meaningful
        for cumulative/snapshot events; per-card events (created / commented /
        reassigned / completed / escalation) are each DISTINCT and must NOT
        supersede. The default (``False``) keeps the plain
        ``(type,card,ts,actor)`` dedup path unchanged.
    msg_id : str | None
        The id of the DURABLE object this notification is about — today, a
        ``dm_messages.id``. Carried so a consumer can join the notification
        back to the message EXACTLY. Without it the only available key is
        ``(event_type, card_id, ts, actor)``, which is many-to-one by
        construction because DM timestamps are second-resolution: measured on
        the live store, two distinct durable messages collapsed onto one
        notification. When present it also becomes the dedupe key, so that
        collapse cannot happen. ``None`` for events with no such object (card
        events, digests), and ``None`` on rows enqueued before this existed.
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).

    Returns
    -------
    dict | None
        The enqueued record, or ``None`` when nothing was written (a falsy
        ``recipient_id`` or a deduped re-emit).
    """
    if _use_postgres():
        from . import _inbox_postgres

        return _inbox_postgres.enqueue(
            recipient_id,
            event_type=event_type,
            card_id=card_id,
            body=body,
            actor=actor,
            ts=ts,
            supersede=supersede,
            msg_id=msg_id,
            store=store,
        )
    if _use_sqlite():
        from . import _inbox_sqlite

        return _inbox_sqlite.enqueue(
            recipient_id,
            event_type=event_type,
            card_id=card_id,
            body=body,
            actor=actor,
            ts=ts,
            supersede=supersede,
            msg_id=msg_id,
            store=store,
        )
    if not recipient_id:
        return None
    timestamp = ts if ts is not None else _utc_now_iso()
    path = _inboxes_path(store)
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.setdefault(recipient_id, [])
        if supersede:
            # Cumulative snapshot: drop every UNSEEN predecessor with the same
            # (event_type, card_id) so only the latest digest stays pending.
            # SEEN records are kept (history). Done BEFORE the dedup/append.
            records[:] = [
                r
                for r in records
                if r.get("seen")
                or not (
                    r.get("event_type") == event_type and r.get("card_id") == card_id
                )
            ]
        if msg_id:
            # Exact dedupe when the producer named the message (see the
            # ``msg_id`` parameter docs for why the tuple below cannot be).
            if any(r.get("msg_id") == msg_id for r in records):
                return None
        elif _is_duplicate(
            records,
            event_type=event_type,
            card_id=card_id,
            ts=timestamp,
            actor=actor,
        ):
            return None
        record = {
            "id": _generate_notification_id(),
            "event_type": event_type,
            "card_id": card_id,
            "body": body,
            "actor": actor,
            "ts": timestamp,
            "seen": False,
            "msg_id": msg_id,
        }
        records.append(record)
        _save_inboxes_unlocked(inboxes, path)
        return dict(record)


def poll_inbox(
    recipient_id: str,
    *,
    unseen_only: bool = True,
    mark_seen: bool = False,
    store: str | Path | None = None,
) -> list[dict]:
    """Return ``recipient_id``'s notifications (unseen by default).

    The recipient's PULL read path. Returns a list of notification records
    (each a plain dict). When ``mark_seen`` is set, the returned records are
    flipped ``seen: True`` and persisted atomically under the store lock
    (advancing the cursor) — so a SECOND ``poll_inbox(unseen_only=True)``
    returns nothing new.

    Parameters
    ----------
    recipient_id : str
        The inbox key (``u_*`` id or raw-name fallback). A falsy id or an
        empty inbox yields ``[]``.
    unseen_only : bool
        When ``True`` (default), return only records with ``seen`` falsy;
        when ``False``, return the full history.
    mark_seen : bool
        When ``True``, advance the cursor — flip every RETURNED record to
        ``seen: True`` and persist. Combined with ``unseen_only=True`` this
        is the "drain my unseen notifications" call.
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).

    Returns
    -------
    list[dict]
        The matching records (a copy each — mutating them does not touch the
        store). Order is append (oldest first).
    """
    if _use_postgres():
        from . import _inbox_postgres

        return _inbox_postgres.poll_inbox(
            recipient_id,
            unseen_only=unseen_only,
            mark_seen=mark_seen,
            store=store,
        )
    if _use_sqlite():
        from . import _inbox_sqlite

        return _inbox_sqlite.poll_inbox(
            recipient_id,
            unseen_only=unseen_only,
            mark_seen=mark_seen,
            store=store,
        )
    if not recipient_id:
        return []
    path = _inboxes_path(store)
    if not mark_seen:
        # Read-only fast path — snapshot without locking.
        records = _load_inboxes_section(path).get(recipient_id, [])
        return [dict(r) for r in records if (not unseen_only or not r.get("seen"))]
    # mark_seen → read-modify-write under the lock.
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.get(recipient_id, [])
        selected = [r for r in records if (not unseen_only or not r.get("seen"))]
        if not selected:
            return []
        for r in selected:
            r["seen"] = True
        inboxes[recipient_id] = records
        _save_inboxes_unlocked(inboxes, path)
        return [dict(r) for r in selected]


def ack(
    recipient_id: str,
    notification_ids: "list[str] | str",
    store: str | Path | None = None,
) -> list[str]:
    """Mark specific notifications seen (advance the cursor for those ids).

    Idempotent: acking an already-seen or unknown id is a no-op for that id.
    Returns the list of ids actually flipped from unseen → seen (so a caller
    can tell what changed). A falsy ``recipient_id`` or an empty id list is a
    no-op (returns ``[]``).

    Parameters
    ----------
    recipient_id : str
        The inbox key (``u_*`` id or raw-name fallback).
    notification_ids : list[str] | str
        One or more notification ids to mark seen (a bare string is accepted
        as a single-element list).
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).
    """
    if _use_postgres():
        from . import _inbox_postgres

        return _inbox_postgres.ack(recipient_id, notification_ids, store=store)
    if _use_sqlite():
        from . import _inbox_sqlite

        return _inbox_sqlite.ack(recipient_id, notification_ids, store=store)
    if not recipient_id:
        return []
    if isinstance(notification_ids, str):
        notification_ids = [notification_ids]
    wanted = {nid for nid in (notification_ids or []) if nid}
    if not wanted:
        return []
    path = _inboxes_path(store)
    flipped: list[str] = []
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.get(recipient_id, [])
        if not records:
            return []
        for r in records:
            if r.get("id") in wanted and not r.get("seen"):
                r["seen"] = True
                flipped.append(r.get("id"))
        if flipped:
            inboxes[recipient_id] = records
            _save_inboxes_unlocked(inboxes, path)
    return flipped


__all__ = ["ack", "enqueue", "poll_inbox"]

# EOF
