#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards dm`` — direct-message store verbs.

  * ``dm send``     — persist one direct message through the public API.
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

from ._compat import spec_command_kwargs
from ._mutating import DRY_RUN_PREFIX, confirm_or_abort, mutating_options


def register(main: click.Group) -> None:
    """Attach the ``dm`` noun group to the root group."""
    main.add_command(dm_group)


@click.group(
    "dm",
    help=(
        "Direct-message store verbs.\n\n"
        "DMs live in the canonical PostgreSQL store. `dm send` persists a "
        "message through the public dm_send API; persistence never claims "
        "live delivery or acknowledgement. `dm backfill` copies the legacy "
        "threads.json sidecar into the store (dry-run by default), "
        "`dm verify` checks the two agree, and `dm export`/`dm merge` move "
        "DM rows between hosts as an append-only union."
    ),
)
def dm_group() -> None:
    """DM store verbs."""


@dm_group.command(
    "send",
    **spec_command_kwargs(
        summary="Persist a direct message and open its delivery exchange.",
        description=(
            "Calls the public scitex_cards.dm_send API with no fallback store. "
            "HTTP 202 means accepted and persisted; live visibility and "
            "recipient acknowledgement remain a later status.",
        ),
        examples=(
            (
                '{prog} dm send worker "Please review card-123"',
                "Persist a DM using the environment sender identity.",
            ),
        ),
    ),
)
@click.argument("recipient")
@click.argument("message")
@click.option(
    "--sender",
    default=None,
    help=(
        "Sender identity (default: SCITEX_CARDS_AGENT_ID; unresolved identity "
        "fails loud)."
    ),
)
@click.option(
    "--client-request-id",
    default=None,
    help=(
        "Caller retry key. Reuse it after a timeout to receive the original "
        "message, notification, and exchange ids."
    ),
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit one structured JSON result."
)
@mutating_options
def send_cmd(
    recipient: str,
    message: str,
    sender: str | None,
    client_request_id: str | None,
    as_json: bool,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    """Persist MESSAGE for RECIPIENT in the canonical DM store.

    This calls the public ``scitex_cards.dm_send`` API and has no fallback
    store. Success means the returned message id is durable in PostgreSQL; it
    does not mean a live session received or acknowledged the message.

    \b
    Examples:
      $ scitex-cards dm send worker "Please review card-123"
      $ scitex-cards dm send operator "Done" --sender worker --json

    The task-scope spelling ``agent:worker`` is accepted at this boundary and
    canonicalized to the live inbox identity ``worker`` before persistence.
    """
    from .. import _dm_exchange

    if client_request_id is None:
        client_request_id = _dm_exchange.new_client_request_id()
        click.echo(f"client_request_id={client_request_id}", err=True)

    if dry_run:
        status = _dm_exchange.StatusCode(
            kind="http",
            code=200,
            message=(
                "OBSERVED: dry-run completed; no DM or exchange was written. "
                "NEXT: remove --dry-run to persist the message."
            ),
        )
        _emit_status(
            {
                "dry_run": True,
                "client_request_id": client_request_id,
                "status": status.to_dict(),
            },
            as_json,
        )
        return
    confirm_or_abort(f"Persist a DM to {recipient!r}?", assume_yes=assume_yes)
    try:
        import scitex_cards

        record = scitex_cards.dm_send(
            recipient,
            message,
            sender=sender,
            client_request_id=client_request_id,
        )
    except _dm_exchange.DmExchangeError as exc:
        _emit_failure(
            exc.exchange_id,
            exc.status,
            as_json,
            message_id=exc.message_id,
            client_request_id=client_request_id,
        )
        return
    except Exception as exc:  # noqa: BLE001 - ledger open/close failure
        status = _dm_exchange.failure_status(
            exc, None, persistence_known_absent=True
        )
        _emit_failure(
            None, status, as_json, client_request_id=client_request_id
        )
        return
    if (
        not isinstance(record, dict)
        or not record.get("id")
        or not record.get("exchange_id")
        or not record.get("notification_id")
        or record.get("client_request_id") != client_request_id
        or not isinstance(record.get("status"), dict)
    ):
        status = _dm_exchange.StatusCode(
            kind="http",
            code=502,
            message=(
                "OBSERVED: the Cards responder returned no canonical exchange "
                "id/status. NOT ESTABLISHED: whether the DM was persisted. "
                "NEXT: upgrade the Cards responder, then inspect dm_messages "
                "before sending again."
            ),
        )
        _emit_failure(
            None,
            status,
            as_json,
            message_id=record.get("id"),
            client_request_id=client_request_id,
        )
        return
    payload = {
        "exchange_id": record["exchange_id"],
        "message_id": record["id"],
        "notification_id": record.get("notification_id"),
        "client_request_id": record["client_request_id"],
        "status": record["status"],
    }
    _emit_status(payload, as_json)


def _emit_status(payload: dict, as_json: bool) -> None:
    """Emit the protocol payload without serialising derived ``ok``/``final``."""
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    status = payload["status"]
    prefix = f"{status['kind']}/{status['code']}"
    if payload.get("exchange_id"):
        prefix += f" exchange={payload['exchange_id']}"
    if payload.get("message_id"):
        prefix += f" message={payload['message_id']}"
    click.echo(prefix)
    click.echo(status["message"])


def _emit_failure(
    exchange_id,
    status,
    as_json,
    *,
    message_id=None,
    client_request_id=None,
) -> None:
    """Emit one native status failure and stop with process status 1."""
    payload = {"exchange_id": exchange_id, "status": status.to_dict()}
    if message_id is not None:
        payload["message_id"] = message_id
    if client_request_id is not None:
        payload["client_request_id"] = client_request_id
    _emit_status(payload, as_json)
    raise click.exceptions.Exit(1)


@dm_group.command(
    "get-status",
    **spec_command_kwargs(
        summary="Read the latest status for a DM delivery exchange.",
        description=(
            "HTTP 202 is non-final: it proves acceptance and persistence, "
            "not live visibility or recipient acknowledgement.",
        ),
        examples=(
            (
                "{prog} dm get-status xch_20260811T061508Z_host_a1b2c3 --json",
                "Read one exchange from the shared status ledger.",
            ),
        ),
    ),
)
@click.argument("exchange_id")
@click.option("--sender", default=None, help="Ledger reader identity.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def get_status_cmd(exchange_id: str, sender: str | None, as_json: bool) -> None:
    """Read the latest recorded status for EXCHANGE_ID.

    A 202 is accepted/persisted and non-final. A later delivery or recipient
    acknowledgement must update the same exchange separately before this
    command can report a final status.
    """
    from .._dm_exchange import StatusCode, get_exchange
    from .._messaging import resolve_sender

    try:
        payload = get_exchange(exchange_id, sender=resolve_sender(sender))
    except Exception:
        status = StatusCode(
            kind="http",
            code=503,
            message=(
                "OBSERVED: the canonical exchange ledger could not be read. "
                "NEXT: run `scitex-cards health`, then retry this query."
            ),
        )
        _emit_failure(exchange_id, status, as_json)
        return
    if payload is None:
        status = StatusCode(
            kind="http",
            code=404,
            message=(
                f"OBSERVED: exchange {exchange_id} is absent from the canonical "
                "ledger. NEXT: copy the exchange_id from the dm send result and "
                "rerun `scitex-cards dm get-status EXCHANGE_ID --json`."
            ),
        )
        _emit_failure(exchange_id, status, as_json)
        return
    _emit_status(payload, as_json)


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

    \b
    Example:
      $ scitex-cards dm backfill
      $ scitex-cards dm backfill --apply
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

    \b
    Example:
      $ scitex-cards dm verify
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
@mutating_options
def export_cmd(db_path, store, out, dry_run, assume_yes) -> None:
    """Dump every DM table in the shape ``dm merge`` consumes.

    THE FLAGS ONLY BITE ON THE `--out` PATH, which is the only one that
    changes anything. Dumping to stdout is a read: there is no preview
    distinct from the output, and nothing to confirm.

    With `--out`: `--dry-run` reports the row counts and the target without
    writing, and `--yes` skips the confirmation asked before an EXISTING file
    is overwritten — somebody may be holding that export.

    \b
    Example:
      $ scitex-cards dm export --out dms.json
      $ scitex-cards dm export --out dms.json --dry-run
    """
    from pathlib import Path

    from .._dm.migrate import export_dm

    payload = export_dm(db=db_path, store=store)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if out:
        target = Path(out).expanduser()
        counts_preview = {k: len(v) for k, v in payload.items()}
        if dry_run:
            click.echo(
                f"{DRY_RUN_PREFIX} would write {target}  "
                f"{json.dumps(counts_preview, sort_keys=True)}"
            )
            if target.exists():
                click.echo(f"{DRY_RUN_PREFIX}   (would OVERWRITE an existing file)")
            return
        if target.exists():
            confirm_or_abort(f"Overwrite {target}?", assume_yes=assume_yes)
        target.write_text(text, encoding="utf-8")
        counts = {k: len(v) for k, v in payload.items()}
        click.echo(f"{out}  {json.dumps(counts, sort_keys=True)}")
        return
    click.echo(text)


@dm_group.command("merge")
@click.argument("payload_path")
@click.option("--db", "db_path", default=None, help="Database to write.")
@click.option("--store", default=None, help="Task-store container path.")
@mutating_options
def merge_cmd(payload_path, db_path, store, dry_run, assume_yes) -> None:
    """Union a peer host's export into this store. Never overwrites, never shrinks.

    Every row carries a globally-unique primary key and every table is
    append-only, so this is an ``INSERT OR IGNORE`` union: commutative,
    associative, idempotent. A peer's export is a SNAPSHOT and may be older
    than what is here — receiving a subset must keep the local extras, and any
    post-state with fewer rows raises rather than committing.

    \b
    Example:
      $ scitex-cards dm merge peer-dms.json
      $ scitex-cards dm merge peer-dms.json --dry-run
    """
    from pathlib import Path

    from .._dm.migrate import merge_dm

    payload = json.loads(Path(payload_path).expanduser().read_text(encoding="utf-8"))
    if dry_run:
        counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
        click.echo(
            f"{DRY_RUN_PREFIX} would union {payload_path} into this store: "
            f"{json.dumps(counts, sort_keys=True)}"
        )
        click.echo(
            f"{DRY_RUN_PREFIX} INSERT OR IGNORE — existing rows are never "
            "overwritten and the row count cannot shrink"
        )
        return
    confirm_or_abort(
        f"Union {payload_path} into this store?", assume_yes=assume_yes
    )
    report = merge_dm(payload, db=db_path, store=store)
    click.echo(json.dumps(report, indent=2, sort_keys=True))


__all__ = ["dm_group", "register", "send_cmd"]

# EOF
