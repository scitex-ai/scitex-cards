#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards db`` — SQLite operability verbs.

SQLite is the store. These verbs are its operability surface:

  * ``db path``     — print the resolved database path.
  * ``db verify``   — open the DB, check user_version + table counts.
  * ``db export``   — write the store out as JSON text (a backup, never a source).
  * ``db snapshot`` — export + git-commit the export off-site.

``db snapshot`` and the private guards it carries (shrink refusal, the two
freshness asserts, the previous-count parser) live in :mod:`_db_snapshot`;
they are re-exported at module level below so the test suite's existing
``from scitex_cards._cli._db import ...`` paths keep resolving unchanged.

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

# The `db snapshot` command and its private helpers (shrink refusal, the two
# freshness asserts, the previous-count parser) moved to `_db_snapshot` when
# this file hit the 512-line cap. Re-exported at module level so the test
# suite's existing `from scitex_cards._cli._db import ...` paths keep
# resolving unchanged.
from ._db_snapshot import (  # noqa: F401 -- re-exported for existing importers
    _SHRINK_REFUSAL_RATIO,
    _SNAPSHOT_SUBJECT_RE,
    _assert_export_reflects_live_db,
    _assert_export_reflects_live_dms,
    _live_dm_count,
    _live_task_fingerprint,
    _previous_snapshot_count,
    db_snapshot_cmd,
)


def register(main: click.Group) -> None:
    """Attach the ``db`` noun group to the root group."""
    from . import _db_snapshot

    main.add_command(db_group)
    _db_snapshot.register(db_group)


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
    help="Explicit DB path (default: $SCITEX_CARDS_DB, else the configured store).",
)


@db_group.command(
    "path",
    help=(
        "Print the resolved DB path.\n\n"
        "Precedence: --db arg > $SCITEX_CARDS_DB > $SCITEX_CARDS_DB "
        "(deprecated, warned) > the `store.target` key in the config file. "
        "There is NO tier below that: it used to fall back to "
        "local_state.user_path('cards','cards.db'), and since 2026-08-13 an "
        "unconfigured store REFUSES instead of naming a SQLite file nobody "
        "chose.\n\n"
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
        "  scitex-cards db verify\n"
        "  scitex-cards db verify --json"
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
    click.echo(f"# scitex-cards db verify: {status} — {report['path']}")
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
@click.option(
    "--dry-run",
    is_flag=True,
    help="Scan the DB and print the intended export paths + counts and exit 0 "
    "without writing the files.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the export report as JSON.")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — export is non-interactive; reserved for §2).",
)
def db_export_cmd(
    db_path: str | None,
    out_path: str | None,
    threads_out: str | None,
    dry_run: bool,
    as_json: bool,
    yes: bool,
) -> None:
    """Export the DB to JSON snapshot files."""
    _ = yes  # accepted for §2 compliance
    from .._db_export import export_json

    report = export_json(
        db_path=db_path,
        out=out_path,
        threads_out=threads_out,
        dry_run=dry_run,
    )
    if as_json:
        click.echo(json.dumps(report))
        return
    if dry_run:
        click.echo("# DRY RUN — nothing was written.")
    _echo_export_report(report)


# EOF
