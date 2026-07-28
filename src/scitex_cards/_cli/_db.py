#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards db`` — SQLite operability verbs.

SQLite is the store. These verbs are its operability surface:

  * ``db path``     — print the resolved database path.
  * ``db verify``   — open the DB, check user_version + table counts.
  * ``db export``   — write the store out as JSON text (a backup, never a source).
  * ``db snapshot`` — export + git-commit the export off-site.

The legacy-sidecar-import verbs (``db import --from-yaml``, ``db rehearse``,
and ``db snapshot --refresh``) are DELETED: there is no external sidecar to
import from any more, and an importer built on the DB read path rebuilt the
database from itself.

The group token is a NOUN per the SciTeX noun-verb CLI convention. Attached to
the root group via :func:`register`.
"""

from __future__ import annotations

import json
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
        row = conn.execute("SELECT COUNT(*), MAX(last_activity) FROM tasks").fetchone()
        return int(row[0]), row[1]
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

    from .._db import resolve_db_path
    from .._threads import THREADS_FILENAME

    path = resolve_db_path(db_path).parent / THREADS_FILENAME
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


def register(main: click.Group) -> None:
    """Attach the ``db`` noun group to the root group."""
    main.add_command(db_group)


@click.group(
    "db",
    help=(
        "SQLite store verbs. SQLite is the store.\n\n"
        "`db path` prints the resolved database location, `db verify` checks "
        "schema health, `db export` writes the store out as YAML text (a "
        "backup, never a source), and `db snapshot` commits that export "
        "off-site."
    ),
)
def db_group() -> None:
    """The ``db`` noun group."""


_DB_OPTION = click.option(
    "--db",
    "db_path",
    default=None,
    help="Explicit DB path (default: $SCITEX_CARDS_DB, else ~/.scitex/cards/cards.db).",
)


@db_group.command(
    "path",
    help=(
        "Print the resolved DB path.\n\n"
        "Precedence: --db arg > $SCITEX_CARDS_DB > $SCITEX_TODO_DB "
        "(deprecated, warned) > local_state.user_path('cards','cards.db'). "
        "Delegates the user tier to the ecosystem resolver (never a "
        "re-rolled project/user precedence).\n\n"
        "Example:\n"
        "  scitex-cards db path"
    ),
)
@_DB_OPTION
def db_path_cmd(db_path: str | None) -> None:
    """Print the resolved DB path."""
    from .._db import resolve_db_path

    click.echo(str(resolve_db_path(db_path)))


@db_group.command(
    "verify",
    help=(
        "Open the shadow DB and verify its schema health.\n\n"
        "Checks PRAGMA user_version, the schema_meta version, presence of "
        "every expected table (with row counts), and PRAGMA quick_check. "
        "Exit 0 when healthy, else 1. Pass --json for the raw report.\n\n"
        "Example:\n"
        "  scitex-todo db verify\n"
        "  scitex-todo db verify --json"
    ),
)
@_DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit the raw report as JSON.")
def db_verify_cmd(db_path: str | None, as_json: bool) -> None:
    """Verify the DB schema + integrity."""
    from .._db import verify

    report = verify(db_path)
    if as_json:
        click.echo(json.dumps(report))
        raise SystemExit(0 if report["ok"] else 1)

    status = "OK" if report["ok"] else "UNHEALTHY"
    click.echo(f"# scitex-todo db verify: {status} — {report['path']}")
    if not report["exists"]:
        click.echo("[FAIL] db does not exist yet (run `init-store`)")
        raise SystemExit(1)
    click.echo(
        f"  user_version={report['user_version']} "
        f"schema_version={report['schema_version']} "
        f"quick_check={report['quick_check']} source={report['source']}"
    )
    for name, count in report["tables"].items():
        click.echo(f"  {name}: {count}")
    raise SystemExit(0 if report["ok"] else 1)


def _echo_export_report(report: dict) -> None:
    """Print an export's counts — a silent bulk export leaves no audit trace."""
    click.echo(
        f"# exported DB -> JSON\n"
        f"  db:      {report['db']}\n"
        f"  tasks:   {report['tasks_json']}  ({report['tasks']} tasks, "
        f"{report['users']} users, {report['notifications']} notifications)\n"
        f"  threads: {report['threads_json']}  ({report['threads']} threads, "
        f"{report['messages']} messages)"
    )


@db_group.command(
    "export",
    help=(
        "Export the DB to JSON text (ADR-0010 backup/audit rail).\n\n"
        "Every record is reconstructed from its VERBATIM json payload "
        "(card_json / record_json) — never from typed columns — so the "
        "export is exact by construction. REFUSES loudly if any row has no "
        "payload.\n\n"
        "Example:\n"
        "  scitex-cards db export\n"
        "  scitex-cards db export --out /tmp/tasks.json --json"
    ),
)
@_DB_OPTION
@click.option(
    "--out",
    "out_path",
    default=None,
    help="tasks.json output path (default: <db_dir>/export/tasks.json).",
)
@click.option(
    "--threads-out",
    "threads_out",
    default=None,
    help="threads.json output path (default: beside --out).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the export report as JSON.")
def db_export_cmd(
    db_path: str | None,
    out_path: str | None,
    threads_out: str | None,
    as_json: bool,
) -> None:
    """Export the DB to JSON snapshot files."""
    from .._db_export import export_json

    report = export_json(db_path=db_path, out=out_path, threads_out=threads_out)
    if as_json:
        click.echo(json.dumps(report))
        return
    _echo_export_report(report)


@db_group.command(
    "snapshot",
    help=(
        "Export the DB to the snapshot dir and git-commit the export.\n\n"
        "The ADR-0010 backup rail: git tracks an EXPORT, never live data, so "
        "no git operation can ever roll back the live store. Initialises the "
        "snapshot dir as its own git repo on first run.\n\n"
        "Example:\n"
        "  scitex-cards db snapshot\n"
        "  scitex-cards db snapshot --dir ~/.scitex/cards/snapshots"
    ),
)
@_DB_OPTION
@click.option(
    "--dir",
    "snap_dir",
    default=None,
    help="Snapshot directory (default: <db_dir>/snapshots; its own git repo).",
)
@click.option(
    "--push",
    is_flag=True,
    help=(
        "Push the snapshot repo to its remote after committing. No remote "
        "configured = reported local-only (exit 0); a FAILED push exits 1 — "
        "the rail's job is the off-site copy, so a silent local-only "
        "success would be a lie."
    ),
)
@click.option(
    "--allow-shrink",
    is_flag=True,
    help=(
        "Snapshot even if the card count collapsed vs the previous snapshot. "
        "Needed for a genuine bulk delete or a deliberately fresh store; "
        "WITHOUT it a large drop is refused, because a backup that silently "
        "records a wipe buys confidence in a destroyed board."
    ),
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the snapshot report as JSON."
)
def db_snapshot_cmd(
    db_path: str | None,
    snap_dir: str | None,
    push: bool,
    allow_shrink: bool,
    as_json: bool,
) -> None:
    """Export to the snapshot dir and commit the export in its own git repo."""
    import subprocess
    from pathlib import Path

    from .._db import resolve_db_path
    from .._db_export import export_json

    root = (
        Path(snap_dir).expanduser()
        if snap_dir
        else resolve_db_path(db_path).parent / "snapshots"
    )
    root.mkdir(parents=True, exist_ok=True)

    report = export_json(
        db_path=db_path,
        out=root / "tasks.json",
        threads_out=root / "threads.json",
    )

    # A BACKUP MUST NOT RECORD A LIE, EITHER.
    #
    # The shrink guard further below catches a collapsed CARD COUNT
    # (2026-07-19). It does not catch a snapshot whose count matches but
    # whose CONTENT lags — the 2026-07-21 false-green: "snapshot: 2168
    # tasks" committed and pushed clean, while the export showed a card as
    # `deferred` that the DB had marked `done` 13 minutes earlier, and was
    # missing a card created in that same window. Shrink and staleness are
    # separate failure modes; this checks the one the other cannot see.
    _assert_export_reflects_live_db(db_path, report)
    # ...and the same question for DMs, which the guard above does NOT ask.
    # The report prints a `messages` count beside the `tasks` count, so both
    # read as equally certified; only one of them was. Measured 2026-07-28:
    # the mirror those messages come from had been frozen for nine days.
    _assert_export_reflects_live_dms(db_path, report)

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    if not (root / ".git").exists():
        _git("init", "-q")
        _git("config", "user.name", "scitex-cards")
        _git("config", "user.email", "cards@scitex.ai")
    # A BACKUP MUST NOT RECORD A CATASTROPHE WITHOUT SAYING SO.
    #
    # On 2026-07-19 the live DB was destroyed (2,138 cards -> 53) and the rail
    # did exactly what it was told: it snapshotted the wreck and committed
    # "snapshot: 53 tasks" as HEAD, one commit after "snapshot: 2138 tasks",
    # silently. The rail was WORKING — that is the point. A backup that
    # faithfully records a wipe with no alarm stops being a safety net and
    # becomes a propagation mechanism: anyone restoring from HEAD afterwards
    # gets the destroyed board, and retention eventually ages out the good one.
    #
    # Git history saved the recovery that day. History is not a plan.
    previous = _previous_snapshot_count(_git)
    now = int(report.get("tasks") or 0)
    if (
        not allow_shrink
        and previous is not None
        and previous > 0
        and now < previous * _SHRINK_REFUSAL_RATIO
    ):
        raise click.ClickException(
            f"REFUSING to snapshot: the card count collapsed from {previous} to "
            f"{now} ({now * 100 // previous}% of the previous snapshot). A backup "
            f"that records a wipe without comment is worse than no backup — it "
            f"buys confidence in a destroyed board.\n"
            f"If the store really did shrink this much (a bulk delete, a fresh "
            f"store), re-run with --allow-shrink. If it did NOT, the live store "
            f"is damaged: recover it BEFORE snapshotting, or this commit becomes "
            f"the newest 'good' state."
        )

    _git("add", "-A")
    committed = _git("commit", "-q", "-m", f"snapshot: {report['tasks']} tasks")
    # exit 1 with nothing staged = no changes since the last snapshot — a
    # legitimate outcome, reported as such rather than swallowed.
    report["committed"] = committed.returncode == 0
    report["snapshot_dir"] = str(root)

    if push:
        has_remote = bool(_git("remote").stdout.strip())
        if not has_remote:
            # Local-only mode is legitimate BEFORE a remote is wired; the
            # report says so instead of pretending an off-site copy exists.
            report["pushed"] = False
            report["push_detail"] = "no remote configured — snapshot is local-only"
        else:
            # -u origin HEAD: works on the FIRST push to a freshly-wired
            # remote (no upstream yet) and every push after.
            pushed = _git("push", "-q", "-u", "origin", "HEAD")
            report["pushed"] = pushed.returncode == 0
            report["push_detail"] = (pushed.stderr or pushed.stdout).strip()
            if not report["pushed"]:
                # A failed push means the backup did NOT go off-site. That is
                # the rail's whole job — fail LOUD so the cron tick reads red.
                _emit = (
                    json.dumps(report)
                    if as_json
                    else (
                        f"::error:: snapshot committed LOCALLY but push FAILED: "
                        f"{report['push_detail']}"
                    )
                )
                click.echo(_emit)
                raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(report))
        return
    _echo_export_report(report)
    state = "committed" if report["committed"] else "no changes since last snapshot"
    click.echo(f"  snapshot: {root} ({state})")


# EOF
