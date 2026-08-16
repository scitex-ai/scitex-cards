#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards inbox`` — inbox storage-backend lifecycle.

Phase 1 of the store SQLite migration (incident card
``store-sqlite-migration-o1-writes-future-20260701``). The per-recipient
notification inbox moves off the monolithic legacy sidecar (whose 5 s
digest-poll re-parsed all ~1000 cards) onto a small SQLite DB at
``<store_dir>/runtime/cards.db``.

Verbs:
  * ``inbox migrate-to-sqlite`` — copy the YAML ``inboxes:`` records into
    SQLite (idempotent; does NOT delete the YAML section — reversible).
  * ``inbox info`` — read-side status of the SQLite inbox DB.
  * ``inbox ack`` — confirm delivery of specific notification ids. The
    STANDALONE surface onto :func:`scitex_cards._inbox_confirm.
    confirm_notifications` (NOT a second ack path — the same one verb, reachable
    without MCP). The Stop hook demands an ack, so an agent that has cards and
    nothing else must have a way to give one; otherwise the hook would be
    blocking where the actor cannot remediate.

Enabling the SQLite backend at runtime is a SEPARATE, deliberate step: export
``SCITEX_CARDS_INBOX_BACKEND=sqlite``. Until then the YAML path stays the
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
        "the SQLite DB (<store_dir>/runtime/cards.db); it is idempotent and "
        "does NOT delete the YAML section (reversible). Enable the backend "
        "with SCITEX_CARDS_INBOX_BACKEND=sqlite."
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
        inbox_target,
        migrate_to_sqlite,
    )
    from scitex_cards._paths import resolve_tasks_path

    store = resolve_tasks_path(None)
    db = inbox_target(store)

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


@inbox_group.command(
    "ack",
    help=(
        "CONFIRM delivery of specific notification ids (the only "
        "cursor-advancing verb, reachable without MCP).\n\n"
        "Idempotent: re-acking an id is a no-op, never an error. Anything you "
        "do not ack stays unseen and is redelivered.\n\n"
        "Example:\n"
        "  $ scitex-cards inbox ack --agent scitex-cards n_abc n_def"
    ),
)
@click.option(
    "--agent",
    default=None,
    help="Whose inbox to confirm in (default: $SCITEX_CARDS_AGENT_ID).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the confirmation payload as JSON.",
)
@click.argument("ids", nargs=-1)
def inbox_ack_cmd(agent: str | None, as_json: bool, ids: tuple) -> None:
    """Confirm notification ids for an agent.

    Example:
      $ scitex-cards inbox ack --agent scitex-cards n_abc n_def
    """
    import json as _json

    from scitex_cards._inbox_confirm import confirm_notifications
    from scitex_cards._store import _default_agent

    if not ids:
        raise click.ClickException(
            "no notification ids given. Pass the `id` field of each record you "
            "actually read, e.g. `scitex-cards inbox ack --agent <you> n_abc`."
        )
    result = confirm_notifications(_default_agent(agent), list(ids))
    if as_json:
        click.echo(_json.dumps(result))
        return
    click.echo(
        f"# confirmed {len(result['confirmed'])} of {len(result['requested'])} "
        f"for {result['recipient_id']}\n"
        f"#   confirmed:         {', '.join(result['confirmed']) or '-'}\n"
        f"#   already confirmed: {', '.join(result['already_confirmed']) or '-'}\n"
        f"#   unknown:           {', '.join(result['unknown']) or '-'}"
    )


# EOF
