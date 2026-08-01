#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is the real board still INTACT? The criterion behind conftest's session guard.

Lives in its own module, not inline in ``conftest.py``, so its own tests can
import it WITHOUT re-running that module's import-time side effects (which
re-point every store env var at a fresh, unbootstrapped scratch directory —
harmless at session start, hostile in the middle of a run).

See :func:`damage` for why intactness, rather than "did the file change", is
the criterion, and for exactly what this layer does and does not catch.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

#: Every table whose row count must never DECREASE.
#:
#: NOT just ``tasks``. Watching only the card table was the first version's
#: defect, and it is the shape of a REAL wipe that leaves cards untouched:
#: ``_db_mirror._sync_sections`` issues ``DELETE FROM user_names`` +
#: ``DELETE FROM users`` (and ``DELETE FROM notifications``) whenever a
#: section hash differs, then re-inserts from the incoming document's
#: ``users``/``inboxes`` sections — and ``_db_sections`` RETURNS EARLY without
#: raising when those keys are absent, so a document carrying every task id
#: but no ``users`` key deletes the fleet's whole identity registry, inserts
#: nothing, raises nothing, and commits. ``users`` holds each agent's
#: ``turn_url``/``a2a_port``/``notify_json``; ``messages`` holds every DM.
#: Card count, ``schema_meta`` and ``integrity_check`` all stay pristine.
#:
#: Monotonicity is safe for these the same way it is safe for ``tasks``: no
#: shipped code path deletes from them except an in-transaction per-card
#: rewrite, so a legitimate peer write never lowers a count.
MONOTONE_TABLES = (
    "tasks",
    "task_comments",
    "task_edges",
    "task_roles",
    "users",
    "user_names",
    "inbox_recipients",
    "notifications",
    "messages",
)


#: The marker ``delete_task`` writes into ``_log_meta``. Mirrors
#: ``scitex_cards._task.TOMBSTONE_KEY`` — deliberately duplicated rather than
#: imported, because this module must be able to judge the live board without
#: importing the package under test.
_TOMBSTONE_KEY = "deleted_at"


def _live_task_ids(conn: sqlite3.Connection) -> frozenset[str]:
    """The ids of cards that are still VISIBLE, tombstones excluded.

    NOT ``SELECT id FROM tasks``. Since the 2026-07-21 P0, ``delete_task`` no
    longer removes a row: it marks it in place (``status`` -> ``cancelled``,
    ``_log_meta.deleted_at`` stamped) and the row is retained forever, per the
    operator ruling 一度書いたものは消えない. Every read path treats a
    tombstoned row as ABSENT. So destroying the whole board through the
    supported delete API changes neither ``count(*)`` NOR the raw id set — a
    guard watching either sees a pristine store while the board reads empty.

    Mirrors ``scitex_cards._task._is_tombstoned``: PRESENCE of ``deleted_at``,
    not ``status == 'cancelled'``, is the marker — a card can be legitimately
    cancelled without ever being deleted.
    """
    live = set()
    for task_id, log_meta_json in conn.execute("SELECT id, log_meta_json FROM tasks"):
        try:
            log_meta = json.loads(log_meta_json) if log_meta_json else None
        except (TypeError, ValueError):
            log_meta = None
        if not (isinstance(log_meta, dict) and log_meta.get(_TOMBSTONE_KEY)):
            live.add(task_id)
    return frozenset(live)


def content_or_none(path: Path) -> dict | None:
    """Snapshot a real store, or ``None`` when the file is ABSENT.

    Returns ``{"counts", "task_ids", "meta", "integrity"}``. A path that does
    not exist yields ``None`` (nothing to protect). A path that EXISTS but
    cannot be read yields ``{"unreadable": <reason>}`` rather than ``None`` —
    those are different facts and conflating them makes the gate fail OPEN,
    silently disarming itself for the whole session.

    Read-only (``mode=ro``, so a missing file is never created).
    """
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return {"unreadable": f"connect failed: {exc}"}
    try:
        counts = {}
        for table in MONOTONE_TABLES:
            try:
                counts[table] = conn.execute(
                    f"SELECT count(*) FROM {table}"  # noqa: S608 — fixed literals
                ).fetchone()[0]
            except sqlite3.Error:
                # Table absent in this schema version. Not damage; just not
                # observable. Recorded as None so a later snapshot showing a
                # real number is not mistaken for growth from zero.
                counts[table] = None
        return {
            "counts": counts,
            # The ID SET, not just the cardinality: delete-then-reinsert at
            # equal count is invisible to a count check, and "2,286 real cards
            # replaced by 2,286 fixture cards" is a total loss that reads as
            # unchanged.
            "live_ids": _live_task_ids(conn),
            "meta": dict(conn.execute("SELECT key, value FROM schema_meta")),
            "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        }
    except sqlite3.Error as exc:
        return {"unreadable": f"read failed: {exc}"}
    finally:
        conn.close()


def damage(before: dict | None, after: dict | None) -> str | None:
    """Describe how ``before`` -> ``after`` is DAMAGE, or ``None`` if it isn't.

    THE CRITERION IS CONTENT, NOT THE FILE'S mtime/size. This board is shared
    and LIVE: other fleet agents write to it throughout any run longer than a
    few seconds, so "the file changed" fires on essentially every session and
    says nothing about whether THIS SUITE wrote. That is not a harmless false
    positive — a gate that cries wolf every run is one people learn to scroll
    past, and this gate is the one that caught three production wipes.
    MEASURED 2026-07-22, the old criterion firing on a peer's write::

        before (mtime_ns, size) = (1784700394236217844, 34537472)
        after  (mtime_ns, size) = (1784701251174118319, 34537472)

    Identical size, mtime moved: a peer updating a card in place, reported as
    "REAL TASK STORE MUTATED".

    Each check below is chosen so a legitimate peer write cannot trip it:

    * NO WATCHED TABLE'S ROW COUNT MAY SHRINK — see :data:`MONOTONE_TABLES`
      for why the card table alone is not enough. Every wipe this suite has
      inflicted is this shape: 2136->21, 2138->1, 2138->3, 2170->18.
    * NO CARD MAY STOP BEING VISIBLE. This is the load-bearing check, and
      the counts above are only a cheap backstop to it. TWO separate holes
      close here, both of which a count comparison has by construction:

      - TOMBSTONES. ``delete_task`` no longer removes rows; it marks them
        (see :func:`_live_task_ids`). Emptying the entire board through the
        supported API leaves ``count(*)`` and the raw id set BIT-IDENTICAL.
        A count check reports a pristine store while the board reads empty.
      - PEER GROWTH MASKING PARTIAL LOSS. Counts are two absolute samples
        compared with ``<``. Peers were measured adding ~25 cards/69s, so on
        a 52-minute run they add well over a thousand; any leak destroying
        fewer cards than that nets POSITIVE and hides. The repo records a
        real 16-card partial loss (``_db_mirror.py``, 2026-07-20) — far
        inside that dead zone.

      A per-id set difference has neither weakness: peers only ADD ids, so
      their volume is irrelevant, and a card that stops being visible is
      caught however it stopped.

      ACCEPTED COST, stated rather than hidden: a peer legitimately deleting
      a card mid-run WILL fire this. That is rare (deletion is rare, and
      cancelling is not deleting), it names the ids so it is triaged in
      seconds, and the alternative is a blind spot for total board loss.
    * ``schema_meta`` must be identical — the store's identity, its
      ``min_client_version`` floor and its DO-NOT-MIRROR sentinel. Peers never
      rewrite it, and the 2026-07-21 wipe #5 damaged precisely this.
    * ``integrity_check`` must not go from ``ok`` to broken.
    * A store readable at snapshot time must not become unreadable — and a
      store that was unreadable WHEN WE SNAPSHOTTED IT is reported too, since
      that means this gate was never armed.

    WHAT THIS STILL DOES NOT CATCH, stated plainly rather than implied:

    * IN-PLACE MUTATION OF EXISTING CARDS at constant ids — a leaked
      ``update_task``/``reassign_task``/triage sweep that rewrites bodies,
      statuses or ownership without adding or removing rows. This is not an
      oversight that can be closed at this layer: peers legitimately update
      cards continuously, so any digest over row CONTENT would fire on every
      run, which is the failure mode this whole change exists to remove.
    * PURE INSERTS by a leaking test — indistinguishable here from a peer
      adding a card.

    For both, the barrier is the env pinning in ``conftest.py``, not this
    detector. This layer bounds the damage classes that actually destroyed
    data; it does not replace the prevention.
    """
    if before is None:
        # Absent when we snapshotted. Creation is not damage.
        return None
    if isinstance(before, dict) and "unreadable" in before:
        return (
            f"store was UNREADABLE when this session started "
            f"({before['unreadable']}) — this gate was never armed for it"
        )
    if after is None:
        return "store DISAPPEARED during the session (it existed at start)"
    if "unreadable" in after:
        return f"store became UNREADABLE during the session ({after['unreadable']})"

    for table in MONOTONE_TABLES:
        b, a = before["counts"].get(table), after["counts"].get(table)
        if b is None or a is None:
            continue
        if a < b:
            return f"{table} row count SHRANK: {b} -> {a}"

    vanished = before["live_ids"] - after["live_ids"]
    if vanished:
        sample = sorted(vanished)[:5]
        return (
            f"{len(vanished)} card(s) stopped being VISIBLE on the board "
            f"(deleted outright, or tombstoned via delete_task). e.g. {sample}"
        )

    if after["meta"] != before["meta"]:
        return f"schema_meta CHANGED: {before['meta']} -> {after['meta']}"

    if after["integrity"] != "ok":
        if before["integrity"] == "ok":
            return (
                f"integrity_check went 'ok' -> {after['integrity']!r} "
                f"DURING this session"
            )
        # Attribute honestly: we did not break this, but it is still broken.
        return (
            f"integrity_check was ALREADY {before['integrity']!r} before this "
            f"session started (NOT caused by this run — the board still needs "
            f"attention)"
        )
    return None


def damaged_candidates(
    before: dict[Path, dict | None],
    candidates: tuple[Path, ...],
) -> list[tuple[Path, str]]:
    """Re-read every candidate NOW and return ``(path, why)`` for the damaged.

    The whole detection chain in one callable — snapshot lookup, live re-read,
    and the :func:`damage` verdict — so it can be proven end to end against
    real databases instead of leaving the wiring untested inside a
    session-scoped fixture that only runs at teardown.
    """
    found = [
        (path, damage(before.get(path), content_or_none(path))) for path in candidates
    ]
    return [(path, why) for path, why in found if why]


# EOF
