#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The guards that refuse a snapshot which would bank a bad backup.

EXTRACTED FROM :mod:`scitex_cards._cli._db` along the seam that module already
draws: it owns the ``dev db`` VERBS, and this owns the question "is the export
those verbs just produced safe to commit as a backup". Same split this package
made for ``_health`` -> ``_health_store`` and ``_db`` -> ``_db_verify`` /
``_db_init_schema``, and for the same reason -- the parent had reached its file
budget, and the material that came out is one responsibility rather than a
convenient slice.

THREE SEPARATE FAILURE MODES, three refusals, and they are not
interchangeable:

  stale cards  the count can MATCH while the content lags (2026-07-21: an
               11:00 export showed a card ``deferred`` that the DB had marked
               ``done`` at 10:47, and every signal was green)
  stale DMs    ``messages`` is a DERIVED mirror; it held 2042 rows whose newest
               was nine days old (2026-07-28), which reads as populated and
               complete to every reader
  collapse     half the cards vanishing between two hourly fires is damage,
               not churn

THE IMPORT SURFACE DOES NOT MOVE: ``_db`` re-exports every name below, so
``from scitex_cards._cli._db import _assert_export_reflects_live_db`` keeps
resolving to this same object. A split that breaks its callers is a rename with
extra steps.
"""

from __future__ import annotations

import re

import click


#: A snapshot holding less than this FRACTION of the previous one's cards is
#: treated as a catastrophe rather than churn, and refused. Cards are deleted
#: routinely; HALF of them vanishing between two hourly fires is not deletion,
#: it is damage. Deliberately generous — the goal is to catch a wipe, not to
#: police normal cleanup, and `--allow-shrink` covers the real bulk-delete case.
_SHRINK_REFUSAL_RATIO = 0.5

#: The rail's own commit subject, e.g. ``snapshot: 2138 tasks``. Parsed back to
#: recover the previous count, so the check needs no state of its own — the
#: history IS the record.
_SNAPSHOT_SUBJECT_RE = re.compile(r"snapshot:\s*(\d+)\s+tasks")


def _live_task_fingerprint(db_path: str | None) -> tuple[int, str | None]:
    """``(row count, newest last_activity)`` read from the DB's TYPED columns.

    Deliberately bypasses ``card_json`` — the export (``_db_export.export_json``)
    reconstructs every task EXCLUSIVELY from the verbatim ``card_json`` payload
    (the S2 exactness contract), never from the typed columns. Every healthy
    write populates both from the same call, so in a healthy DB this and the
    export's own report always agree; a live probe of the typed columns is
    therefore an INDEPENDENT ground truth to check the export against.
    """
    from .._db import open_db

    conn = open_db(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(last_activity) AS newest FROM tasks"
        ).fetchone()
        # POSITIONAL INDEXING IS NOT PORTABLE HERE. Some drivers' rows support
        # both row[0] and row["n"]; the PostgreSQL wrapper yields a DICT-LIKE row
        # where row[0] raises `KeyError: 0`. Measured 2026-08-02 — this line
        # was the SECOND thing to break the off-site snapshot on Postgres,
        # surfacing only once the resolve-path fix let execution reach it.
        #
        # A KeyError here is also easy to misread as "the tasks table is
        # missing", which is why the columns are named and read BY NAME: the
        # spelling that works on both backends, and the one whose failure says
        # what it means.
        return int(row["n"]), row["newest"]
    finally:
        conn.close()


def _assert_export_reflects_live_db(db_path: str | None, report: dict) -> None:
    """RAISE if the export just produced does not match the DB's LIVE state.

    THE 2026-07-21 FALSE-GREEN INCIDENT this exists to catch: the hourly
    snapshot timer exported, committed "snapshot: 2168 tasks", and pushed
    off-site — every signal green — while the exported CONTENT was stale.
    Proof at the time: the 11:00 export showed a card as ``deferred`` that
    the DB had already marked ``done`` at 10:47, and a card created at 10:47
    was absent entirely. The shrink-refusal guard below does not catch this
    shape: the card COUNT can match while the CONTENT lags — shrink and
    staleness are separate failure modes.

    This probes the DB's typed ``last_activity`` / row-count directly
    (:func:`_live_task_fingerprint`, bypassing ``card_json`` — the export's
    own source) and compares against what the export actually reported. Any
    disagreement means the export does not reflect the DB's current state —
    whatever the cause (a lagging mirror, a wrong resolved path, a partial
    write) — and the snapshot must not be committed or pushed as if current.
    """
    live_count, live_newest = _live_task_fingerprint(db_path)
    exported_count = int(report.get("tasks") or 0)
    exported_newest = report.get("newest_last_activity")

    if live_count != exported_count:
        raise click.ClickException(
            f"REFUSING to snapshot: STALE EXPORT. The DB's tasks table has "
            f"{live_count} rows right now, but the export just produced "
            f"{exported_count}. The export does not reflect the DB's current "
            f"state — do not trust or push this snapshot.\n"
            f"Re-run `db snapshot`. If this keeps happening, the export is "
            f"reading the wrong database (check --db / $SCITEX_CARDS_DB) or "
            f"is racing against concurrent writes."
        )
    if exported_newest != live_newest:
        raise click.ClickException(
            f"REFUSING to snapshot: STALE EXPORT. The DB's newest "
            f"last_activity (typed column, live) is {live_newest!r}, but the "
            f"export's newest last_activity (from card_json) is "
            f"{exported_newest!r} — they disagree, so the export does not "
            f"reflect the DB's current state. Do not trust or push this "
            f"snapshot.\n"
            f"This is the shape of the 2026-07-21 false-green incident (an "
            f"11:00 export showed a card still `deferred` that the DB had "
            f"marked `done` 13 minutes earlier). Investigate why card_json "
            f"and the typed columns disagree — a partial write, a stale "
            f"mirror, or the wrong resolved DB — before re-running."
        )


def _live_dm_count(db_path: str | None) -> int:
    """Messages in the LIVE ``threads.json`` sidecar beside the DB.

    The sidecar — not the DB's ``messages`` table — is the source of truth for
    DMs today: ``messages`` is a DERIVED mirror of it (``_db_mirror``: "messages
    is NOT ours"). So the sidecar is the INDEPENDENT ground truth to check a
    DM export against, exactly as the typed columns are for cards.

    A missing sidecar is zero, not an error: a board that has never carried a
    DM legitimately has no file.
    """
    import json

    from .._paths import resolve_tasks_path
    from .._threads import THREADS_FILENAME

    # THE SIDECAR IS LOCAL STATE, NOT THE STORE'S IDENTITY -- the same
    # distinction, and the same repair, as the snapshot dir in ``_db.py``.
    # This read ``resolve_db_path(db_path).parent``, which derives a filesystem
    # location from the STORE TARGET: fine while that target is a file, and an
    # outright raise once it is a DSN.
    #
    # It was missed when the snapshot dir was repaired, because the two sat
    # ~350 lines apart and only the other one had an incident attached. The
    # failure mode is identical: every ``create-snapshot`` dies on
    # ``$SCITEX_CARDS_DB names a PostgreSQL server, not a file path`` before it
    # counts a single DM -- which is how the off-site backup was down for ~31
    # hours on 2026-08-02.
    #
    # ``resolve_tasks_path`` is the local axis: the real path for a file store,
    # the user root for a DSN. File-store behaviour is unchanged.
    path = resolve_tasks_path(db_path).parent / THREADS_FILENAME
    if not path.exists():
        return 0
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise click.ClickException(
            f"REFUSING to snapshot: the live DM sidecar {path} could not be "
            f"read ({exc}). A snapshot cannot certify DMs it cannot count.\n"
            f"Inspect the file. If it is genuinely corrupt, restore it from the "
            f"most recent GOOD snapshot before taking a new one — do not let a "
            f"snapshot bank over it."
        ) from exc
    threads = doc.get("threads") or {}
    return sum(len(v or []) for v in threads.values())


def _assert_export_reflects_live_dms(db_path: str | None, report: dict) -> None:
    """RAISE if the exported DM count does not match the LIVE sidecar.

    MEASURED 2026-07-28 on the canonical store: ``messages`` held 2042 rows
    whose newest ``ts`` was ``2026-07-19T00:49:05Z``. Nine days of DMs existed
    ONLY in the sidecar, because the mirror had silently stopped refreshing.
    2042 rows is what makes it dangerous — the table looks populated, so every
    reader gets a plausible, complete-shaped, nine-day-old answer and nothing
    errors.

    :func:`_assert_export_reflects_live_db` does not cover this: it compares
    CARDS, while the same report prints a ``messages`` count that reads as
    equally verified. That is a check silently narrowing its own scope — the
    snapshot says "N messages" and means "N rows copied from a mirror nobody
    checked". Shrink, card-staleness and DM-staleness are three separate
    failure modes and each needs its own refusal.

    Had this existed it would have fired on 2026-07-19 instead of letting every
    snapshot since bank a stale copy of the chat as if it were a backup.
    """
    live = _live_dm_count(db_path)
    exported = int(report.get("messages") or 0)
    if live != exported:
        raise click.ClickException(
            f"REFUSING to snapshot: STALE DM EXPORT. The live threads.json "
            f"sidecar holds {live} message(s) right now, but the export "
            f"produced {exported} — so the snapshot would bank a DM history "
            f"that does not match reality.\n"
            f"The DB's `messages` table is a DERIVED mirror of the sidecar; "
            f"this disagreement means the mirror has drifted (it silently "
            f"stopped refreshing on 2026-07-19 once already). Do not trust or "
            f"push this snapshot. Refresh the mirror from the sidecar, then "
            f"re-run `db snapshot`."
        )


def _previous_snapshot_count(git) -> int | None:
    """Cards recorded by the most recent snapshot commit, or ``None``.

    ``None`` means "no basis to compare" — a fresh repo, an unreadable log, or
    a subject line that does not parse. Every one of those is a reason to allow
    the snapshot, not to block it: a backup rail must never refuse because its
    own bookkeeping is unfamiliar.
    """
    log = git("log", "-1", "--format=%s")
    if log.returncode != 0:
        return None
    match = _SNAPSHOT_SUBJECT_RE.search(log.stdout or "")
    return int(match.group(1)) if match else None

# EOF
