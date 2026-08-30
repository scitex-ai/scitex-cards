#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards dev db`` — store operability verbs.

The database is the store. These verbs are its operability surface:

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

import click

from ._compat import deprecated_alias
from ._dev import get_dev_group
from ._mutating import DRY_RUN_PREFIX, confirm_or_abort, mutating_options

# THE SNAPSHOT GUARDS live in :mod:`._db_snapshot_guards` -- this module owns
# the ``dev db`` VERBS, that one owns "is the export safe to bank as a backup".
# Re-exported under the same private names so every existing caller and test
# resolves unchanged.
from ._db_snapshot_guards import (
    _assert_export_reflects_live_db,
    _assert_export_reflects_live_dms,
    _live_dm_count,  # noqa: F401  (re-export: tests and callers import it here)
    _live_task_fingerprint,  # noqa: F401  (re-export)
    _previous_snapshot_count,
    _SHRINK_REFUSAL_RATIO,
    _SNAPSHOT_SUBJECT_RE,  # noqa: F401  (re-export)
)


def register(main: click.Group) -> None:
    """Attach the ``db`` noun group, with the two renamed leaves aliased.

    ``path`` -> ``get-path`` and ``snapshot`` -> ``create-snapshot``. Audit §1
    flagged both as noun leaves implying a transitive action, and both fail the
    constitution's own test: each had to be explained by restating it with a
    verb ("PRINT the resolved path", "COMMIT the export"), so that verb is the
    name. The verbs come from the canonical catalog rather than invention —
    `get` is the data-first fetch (the catalog also records that `show-<x>`
    compounds are migrating to `get`), and `create` brings a new object into
    existence ("Never `new`, `make`, `gen`").

    Both old spellings stay as Phase-W aliases: `db path` in particular is the
    documented way to answer "which store am I actually on", so it turns up in
    people's notes and in other packages' troubleshooting steps.

    THE GROUP ITSELF MOVED to `dev db` (operator, 2026-08-26): per-package
    database client commands standardize on `<package> dev db`, and the
    ecosystem-wide aggregate on `scitex-dev ecosystem dev db`. That is the
    same §13 split already applied to every other periodic/upkeep verb — a
    verb that operates on the store as an object is upkeep, not product
    surface — so `db` belongs beside `cardsync` under `dev` rather than at
    the root next to the card verbs people actually run.

    The root spelling stays as a Phase-W alias for the same reason the two
    leaf renames did, and more urgently: the ROOT spelling of get-path is the
    documented answer to "which store am I on", and it is baked into cron
    lines, troubleshooting notes and agent prompts across the fleet, none of
    which are greppable from here. Every hint inside this package was moved
    to the new spelling in the same change, because the suite enforces that a
    hint is runnable as printed — an alias resolves at the CLI but is not a
    verb the enumeration will find.
    """
    dev = get_dev_group(main)
    dev.add_command(db_group)
    deprecated_alias(
        main, "db", target=db_group, target_name="dev db", remove_in="0.54"
    )
    deprecated_alias(db_group, "path", target="get-path", remove_in="0.52")
    deprecated_alias(
        db_group, "snapshot", target="create-snapshot", remove_in="0.52"
    )


@click.group(
    "db",
    help=(
        "Card-store verbs.\n\n"
        "`dev db get-path` prints the resolved store location, `dev db verify` "
        "checks schema health, `dev db export` writes the store out as YAML "
        "text (a backup, never a source), and `dev db create-snapshot` commits "
        "that export off-site."
    ),
)
def db_group() -> None:
    """The ``db`` noun group."""


_DB_OPTION = click.option(
    "--db",
    "db_path",
    default=None,
    help="Explicit DB path (default: $SCITEX_CARDS_DB, else the configured store).",
)


@db_group.command(
    "get-path",
    help=(
        "Print the resolved DB path.\n\n"
        "Precedence: --db arg > $SCITEX_CARDS_DB > $SCITEX_CARDS_DB "
        "(deprecated, warned) > the `store.target` key in the config file. "
        "There is NO tier below that: it used to fall back to "
        "local_state.user_path('cards','cards.db'), and since 2026-08-13 an "
        "unconfigured store REFUSES instead of naming a file nobody "
        "chose.\n\n"
        "Example:\n"
        "  scitex-cards dev db get-path"
    ),
)
@_DB_OPTION
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the resolved target as JSON."
)
def db_path_cmd(db_path: str | None, as_json: bool) -> None:
    """Print the resolved store target, as text or JSON.

    `--json` ARRIVED WITH THE VERB. Renaming this leaf from `path` to the
    canonical `get-path` raised audit §2 immediately: a read verb owes
    machine-readable output. That is right for this command specifically —
    "which store am I actually on" is the question a SCRIPT asks while
    diagnosing a wrong-store read, and making it parse a bare line is how a
    DSN containing a colon becomes somebody's split() bug.

    The plain form is unchanged, so anything already piping this keeps working.
    """
    from .._db import resolve_db_path

    resolved = str(resolve_db_path(db_path))
    if as_json:
        click.echo(json.dumps({"target": resolved}))
        return
    click.echo(resolved)


@db_group.command(
    "verify",
    help=(
        "Open the shadow DB and verify its schema health.\n\n"
        "Checks PRAGMA user_version, the schema_meta version, presence of "
        "every expected table (with row counts), and PRAGMA quick_check. "
        "Exit 0 when healthy, else 1. Pass --json for the raw report.\n\n"
        "Example:\n"
        "  scitex-cards dev db verify\n"
        "  scitex-cards dev db verify --json"
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
    click.echo(f"# scitex-cards dev db verify: {status} — {report['path']}")
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
        "  scitex-cards dev db export\n"
        "  scitex-cards dev db export --dry-run\n"
        "  scitex-cards dev db export --out /tmp/tasks.json --json"
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
@mutating_options
def db_export_cmd(
    db_path: str | None,
    out_path: str | None,
    threads_out: str | None,
    as_json: bool,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    """Export the DB to JSON snapshot files.

    This verb ALWAYS writes files — unlike `dm export` there is no stdout
    path — so both flags apply unconditionally. `--dry-run` names the targets
    it would write without touching them; `--yes` skips the confirmation asked
    when a target already exists, because an export somebody is holding should
    not be replaced silently.
    """
    from pathlib import Path

    from .._db_export import export_json, export_targets

    targets = export_targets(db_path=db_path, out=out_path, threads_out=threads_out)
    if dry_run:
        for label, target in targets.items():
            exists = " (would OVERWRITE)" if Path(target).exists() else ""
            click.echo(f"{DRY_RUN_PREFIX} would write {label}: {target}{exists}")
        click.echo(f"{DRY_RUN_PREFIX} nothing written")
        return
    existing = [t for t in targets.values() if Path(t).exists()]
    if existing:
        confirm_or_abort(
            f"Overwrite {len(existing)} existing export file(s)?",
            assume_yes=assume_yes,
        )
    report = export_json(db_path=db_path, out=out_path, threads_out=threads_out)
    if as_json:
        click.echo(json.dumps(report))
        return
    _echo_export_report(report)


@db_group.command(
    "create-snapshot",
    help=(
        "Export the DB to the snapshot dir and git-commit the export.\n\n"
        "The ADR-0010 backup rail: git tracks an EXPORT, never live data, so "
        "no git operation can ever roll back the live store. Initialises the "
        "snapshot dir as its own git repo on first run.\n\n"
        "Example:\n"
        "  scitex-cards dev db create-snapshot\n"
        "  scitex-cards dev db create-snapshot --dir ~/.scitex/cards/snapshots"
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
@mutating_options
def db_snapshot_cmd(
    db_path: str | None,
    snap_dir: str | None,
    push: bool,
    allow_shrink: bool,
    as_json: bool,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    """Export to the snapshot dir and commit the export in its own git repo.

    THE GUARDS CAME WITH THE VERB. This leaf was `db snapshot` until audit §1
    called it a noun; renaming it to the canonical `create` immediately raised
    §2 — a mutating verb owes `--dry-run` and `--yes`. That is the naming
    system working rather than nagging: `create-snapshot` PROMISES those flags
    to anyone who has learned them on any other scitex verb, and the promise
    has to be kept. The rename was half the change.
    """
    import subprocess
    from pathlib import Path

    from .._db_export import export_json
    from .._paths import resolve_tasks_path

    # THE SNAPSHOT DIR IS A LOCAL STATE DIR, NOT THE STORE'S IDENTITY.
    #
    # This used to be `resolve_db_path(db_path).parent / "snapshots"`, which
    # derives a filesystem location from the STORE TARGET. That is fine while
    # the target is a file and raises outright once it is a DSN — measured
    # 2026-08-02, and it took the off-site backup down for ~31 hours: every
    # hourly run died on `$SCITEX_CARDS_DB names a PostgreSQL server, not a
    # file path`, with the traceback going to a log file nobody reads.
    #
    # The guard was right; the caller was wrong. Store identity (which may be a
    # DSN) and local state dir (always a real directory) are INDEPENDENT AXES,
    # and a backup needs the second one. `resolve_tasks_path` is the local
    # axis: it returns the real path for a file store and the user root for a
    # DSN, so the snapshot lands beside the store when there is one and in
    # ~/.scitex/cards otherwise. File-store behaviour is unchanged.
    root = (
        Path(snap_dir).expanduser()
        if snap_dir
        else resolve_tasks_path(db_path).parent / "snapshots"
    )
    # BOTH GUARDS SIT IN FRONT OF THE FIRST WRITE, which is the mkdir below —
    # not in front of the git commit. A dry run that had already created the
    # directory and written two export files would have changed the filesystem
    # while reporting that it changed nothing, and that is the failure the flag
    # exists to prevent.
    if dry_run:
        click.echo(f"{DRY_RUN_PREFIX} snapshot dir: {root}")
        click.echo(f"{DRY_RUN_PREFIX} would export : tasks.json, threads.json")
        click.echo(
            f"{DRY_RUN_PREFIX} would git-commit the export"
            + (" and push it" if push else " (no push)")
        )
        return
    confirm_or_abort(f"Snapshot the store into {root}?", assume_yes=assume_yes)

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
