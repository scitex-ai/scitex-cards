#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards dm`` — the DM migration's operator surface.

  * ``dm backfill`` — copy ``threads.json`` into the ``dm_*`` tables.
  * ``dm verify``   — diff the sidecar against the store, by message id.
  * ``dm export``   — dump the DM tables in the shape a peer host can merge.
  * ``dm merge``    — union a peer host's export into this store.

DRY RUN IS THE DEFAULT FOR ``backfill``, and that is not timidity. A bulk copy
into the store of record is the operation that has cost this fleet three
boards; the default must be the one that prints what WOULD happen. ``--apply``
is the word an operator types once they have read the counts.

The dry run performs every insert for real and rolls the transaction back, so
the numbers it prints are measurements rather than estimates — an estimate
computed by a different code path would be describing a different operation
than the one about to run.

Both counts are always printed together, sidecar and store. The migration's
entire claim is that they agree, so reporting only one would be reporting the
half that cannot be checked.
"""

from __future__ import annotations

import json

import click


def register(main: click.Group) -> None:
    """Attach the ``dm`` noun group to the root group."""
    main.add_command(dm_group)


@click.group(
    "dm",
    help=(
        "Direct-message store verbs.\n\n"
        "DMs live in cards.db (schema v5). `dm backfill` copies the legacy "
        "threads.json sidecar into the store (dry-run by default), "
        "`dm verify` checks the two agree, and `dm export`/`dm merge` move "
        "DM rows between hosts as an append-only union."
    ),
)
def dm_group() -> None:
    """DM store verbs."""


def _default_sidecar(store: str | None) -> str:
    from .._threads import threads_path

    return str(threads_path(store))


@dm_group.command("backfill")
@click.option(
    "--sidecar", default=None, help="threads.json to read (default: beside the store)."
)
@click.option(
    "--db", "db_path", default=None, help="Database to write (default: resolved store)."
)
@click.option(
    "--store", default=None, help="Task-store container path, used to locate both."
)
@click.option(
    "--apply",
    "apply_",
    is_flag=True,
    default=False,
    help="Actually commit. Without it this is a dry run that rolls back.",
)
def backfill_cmd(sidecar, db_path, store, apply_) -> None:
    """Copy the sidecar's DMs into the store. Dry run unless ``--apply``.

    The sidecar is opened read-only under its own flock and is never written,
    moved or truncated, so this stays reversible: rolling back is redeploying
    the previous version, not restoring anything.
    """
    from .._dm.migrate import backfill_from_sidecar

    path = sidecar or _default_sidecar(store)
    report = backfill_from_sidecar(path, db=db_path, store=store, dry_run=not apply_)
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    matched = report["sidecar_messages"] == report["db_messages_after"]
    click.echo(
        f"# sidecar={report['sidecar_messages']} "
        f"db_after={report['db_messages_after']} "
        f"{'MATCH' if matched else 'DIFFER (db may legitimately hold more)'}"
    )
    if not apply_:
        click.echo("# DRY RUN - nothing was committed. Re-run with --apply.")


@dm_group.command("verify")
@click.option("--sidecar", default=None, help="threads.json to compare against.")
@click.option("--db", "db_path", default=None, help="Database to read.")
@click.option("--store", default=None, help="Task-store container path.")
def verify_cmd(sidecar, db_path, store) -> None:
    """Diff the sidecar's records against the store's, by message id.

    Exits non-zero when the store is MISSING something the sidecar has. Extra
    rows in the store are reported but are not a failure: once the write path
    flips, new DMs land in the database first, so "the database has more" is
    the healthy steady state.
    """
    from .._dm.migrate import verify_against_sidecar

    path = sidecar or _default_sidecar(store)
    report = verify_against_sidecar(path, db=db_path, store=store)
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    if not report["ok"]:
        raise SystemExit(1)


@dm_group.command("export")
@click.option("--db", "db_path", default=None, help="Database to read.")
@click.option("--store", default=None, help="Task-store container path.")
@click.option("--out", default=None, help="Write here instead of stdout.")
def export_cmd(db_path, store, out) -> None:
    """Dump every DM table in the shape ``dm merge`` consumes."""
    from pathlib import Path

    from .._dm.migrate import export_dm

    payload = export_dm(db=db_path, store=store)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if out:
        Path(out).expanduser().write_text(text, encoding="utf-8")
        counts = {k: len(v) for k, v in payload.items()}
        click.echo(f"{out}  {json.dumps(counts, sort_keys=True)}")
        return
    click.echo(text)


@dm_group.command("merge")
@click.argument("payload_path")
@click.option("--db", "db_path", default=None, help="Database to write.")
@click.option("--store", default=None, help="Task-store container path.")
def merge_cmd(payload_path, db_path, store) -> None:
    """Union a peer host's export into this store. Never overwrites, never shrinks.

    Every row carries a globally-unique primary key and every table is
    append-only, so this is an ``INSERT OR IGNORE`` union: commutative,
    associative, idempotent. A peer's export is a SNAPSHOT and may be older
    than what is here — receiving a subset must keep the local extras, and any
    post-state with fewer rows raises rather than committing.
    """
    from pathlib import Path

    from .._dm.migrate import merge_dm

    payload = json.loads(Path(payload_path).expanduser().read_text(encoding="utf-8"))
    report = merge_dm(payload, db=db_path, store=store)
    click.echo(json.dumps(report, indent=2, sort_keys=True))


__all__ = ["dm_group", "register"]

# EOF
