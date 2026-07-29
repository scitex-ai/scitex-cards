#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards inbox`` — inbox storage-backend lifecycle.

Phase 1 of the store SQLite migration (incident card
``store-sqlite-migration-o1-writes-future-20260701``). The per-recipient
notification inbox moves off the monolithic legacy sidecar (whose 5 s
digest-poll re-parsed all ~1000 cards) onto a small SQLite DB at
``<store_dir>/runtime/todo.db``.

Verbs:
  * ``inbox migrate-to-sqlite`` — copy the YAML ``inboxes:`` records into
    SQLite (idempotent; does NOT delete the YAML section — reversible).
  * ``inbox info`` — read-side status of the SQLite inbox DB.

Enabling the SQLite backend at runtime is a SEPARATE, deliberate step: export
``SCITEX_TODO_INBOX_BACKEND=sqlite``. Until then the YAML path stays the
default and this migration is a harmless no-op-safe copy.

Attached to the root group via :func:`register`, matching the sibling
``_index`` / ``_migration_cli`` modules.
"""

from __future__ import annotations

import click


def register(main: click.Group) -> None:
    """Attach the ``inbox`` noun group to the root group."""
    main.add_command(inbox_group)


@click.group(
    "inbox",
    help=(
        "Inbox storage-backend lifecycle (Phase 1 SQLite migration).\n\n"
        "`inbox migrate-to-sqlite` copies the YAML `inboxes:` records into "
        "the SQLite DB (<store_dir>/runtime/todo.db); it is idempotent and "
        "does NOT delete the YAML section (reversible). Enable the backend "
        "with SCITEX_TODO_INBOX_BACKEND=sqlite."
    ),
)
def inbox_group() -> None:
    """The ``inbox`` noun group — verbs migrate-to-sqlite + info."""


@inbox_group.command(
    "migrate-to-sqlite",
    help=(
        "Copy the YAML `inboxes:` records into the SQLite inbox DB. "
        "Idempotent (dedups on notification id) and reversible (never "
        "deletes the YAML section).\n\n"
        "Example:\n"
        "  $ scitex-cards inbox migrate-to-sqlite --dry-run\n"
        "  $ scitex-cards inbox migrate-to-sqlite -y"
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report how many records WOULD be copied without touching the "
    "SQLite DB. Required by SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the interactive confirmation. Required when the planned "
    "action would create/mutate the SQLite DB and stdin is a TTY.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the migration stats as JSON.",
)
def inbox_migrate_cmd(
    dry_run: bool,
    assume_yes: bool,
    as_json: bool,
) -> None:
    """Copy YAML inbox records into SQLite.

    Example:
      $ scitex-cards inbox migrate-to-sqlite --dry-run
      $ scitex-cards inbox migrate-to-sqlite -y
    """
    import json as _json
    import sys as _sys

    from scitex_cards._inbox_sqlite import (
        gather_migratable_inboxes,
        inbox_db_path,
        migrate_to_sqlite,
    )
    from scitex_cards._paths import resolve_tasks_path

    store = resolve_tasks_path(None)
    db = inbox_db_path(store)

    if dry_run:
        inboxes = gather_migratable_inboxes(store)
        recipients = len(inboxes)
        records = sum(len(v) for v in inboxes.values() if isinstance(v, list))
        if as_json:
            click.echo(
                _json.dumps(
                    {
                        "dry_run": True,
                        "source": str(store),
                        "db": str(db),
                        "recipients": recipients,
                        "records": records,
                    }
                )
            )
            return
        click.echo(
            f"# dry-run: would migrate {records} record(s) across "
            f"{recipients} recipient(s)\n"
            f"#   source: {store}\n"
            f"#   db:     {db}"
        )
        return

    if not assume_yes and _sys.stdin.isatty():
        raise click.ClickException(
            "`inbox migrate-to-sqlite` creates/mutates the SQLite inbox DB. "
            "Pass -y / --yes to confirm, or --dry-run to preview."
        )

    stats = migrate_to_sqlite(store=store)
    if as_json:
        click.echo(_json.dumps({"db": str(db), **stats}))
        return
    click.echo(
        f"# migrated {stats['inserted']} inserted / {stats['skipped']} "
        f"skipped of {stats['records']} record(s) across "
        f"{stats['recipients']} recipient(s) -> {db}"
    )


@inbox_group.command(
    "info",
    help=(
        "Print status of the SQLite inbox DB (row count, unseen count, "
        "path).\n\n"
        "Example:\n"
        "  $ scitex-cards inbox info\n"
        "  $ scitex-cards inbox info --json"
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON. Required by SciTeX §2 audit on read verbs.",
)
def inbox_info_cmd(as_json: bool) -> None:
    """Read-side report on the SQLite inbox DB.

    Example:
      $ scitex-cards inbox info
      $ scitex-cards inbox info --json
    """
    import json as _json

    from scitex_cards._inbox_sqlite import info as inbox_info
    from scitex_cards._paths import resolve_tasks_path

    store = resolve_tasks_path(None)
    payload = inbox_info(store=store)
    if as_json:
        click.echo(_json.dumps(payload))
        return
    if not payload["exists"]:
        click.echo(f"# inbox DB does not exist yet: {payload['path']}")
        click.echo("# run `scitex-cards inbox migrate-to-sqlite -y` to populate.")
        return
    click.echo(
        f"# inbox DB: {payload['path']}\n"
        f"#   rows:   {payload['rows']}\n"
        f"#   unseen: {payload['unseen']}"
    )


# EOF
