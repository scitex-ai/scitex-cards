#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schema migrations — *how an older database file becomes current*.

Extracted from :mod:`scitex_cards._db`, which re-exports every name here so no
importer moves. The precedent already existed: ``migrate_v4_to_v5`` lives in
:mod:`scitex_cards._db_dm_schema`, so migrations were already not all in ``_db``;
this makes that consistent rather than one-off.

Every migration in here is ADDITIVE and IDEMPOTENT — a column or a trigger, never
a table rewrite — because they run on every ``init_schema``, i.e. on every
``open_db``, from ~90 agent containers concurrently.

THE CHAIN HAS A HOLE, and it is documented at the call site in ``init_schema``
rather than here so a reader following the sequence meets it: there is no
``_migrate_v3_to_v4``. Whatever v4 added went into ``_SCHEMA_SQL`` only, which is
fresh-database-only (``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing
table), so a v3 file upgraded straight to v5 never received it — while its stamp
reads 5 regardless. That is the exact trap :func:`_migrate_v1_to_v2`'s own
docstring warns about, present in the very chain that docstring lives in.
Consequence for anything reading the stamp: check the COLUMNS
(:func:`table_columns`), never ``user_version``.
"""

from __future__ import annotations

from ._ddl import execute_ddl

import sqlite3

__all__ = [
    "table_columns",
    "_migrate_v1_to_v2",
    "_migrate_v2_to_v3",
    "_migrate_v5_to_v6",
    "_migrate_v6_to_v7",
    "REVISION_TRIGGER_SQL",
    "record_migration_provenance",
]


def record_migration_provenance(
    conn: sqlite3.Connection,
    prior_version: int,
    new_version: int,
    now_iso: str,
    client_version: str,
) -> bool:
    """Stamp WHO upgraded an EXISTING store's schema, and WHEN. Returns whether.

    WHY THIS EXISTS, from a diagnosis that went wrong for want of exactly this.
    On 2026-07-30 the live store was found to have moved v5 -> v6. Nothing in the
    store recorded which client did it or when, so the only available evidence was
    a test-suite sentinel that compares the store before and after a run -- and
    that store is written continuously by ~90 fleet agents, so it attributes every
    neighbour's write to whoever happens to be running. I read it as my own work
    and reported that I had migrated the production store. I had not. The
    correction was only possible by INFERRING the culprit's version from what the
    migration left behind (revision column present, v7 trigger absent, therefore a
    SCHEMA_VERSION=6 client). That inference should not have been necessary.

    So: an upgrade now names itself. ``schema_migrated_from`` /
    ``schema_migrated_to`` / ``schema_migrated_at`` / ``schema_migrated_by``.

    ONLY FOR AN UPGRADE OF AN EXISTING STORE. A fresh database reports
    ``PRAGMA user_version == 0`` and is being CREATED, not migrated; stamping that
    would put a migration record on every new store and make the field useless for
    the question it exists to answer. Re-running against an already-current store
    (``prior == new``) is likewise not a migration -- ~90 containers open this
    store constantly and init_schema is idempotent by design, so recording those
    would rewrite the stamp on every connection and destroy the timestamp's
    meaning.

    THIS IS A RECORD, NOT A GATE. It does not stop a client from silently
    upgrading a shared store, which is the actual defect
    (cards-pytest-collection-migrates-the-live-store-20260730): whichever client
    connects first still decides the schema. Refusing to migrate without explicit
    consent would break every fleet client that relies on auto-migration today,
    so that change needs a fleet-wide decision and does not belong in this commit.
    What this buys is that the next such event is attributable in one query
    instead of by reasoning backwards from residue.
    """
    if prior_version == 0 or prior_version == new_version:
        return False

    rows = [
        ("schema_migrated_from", str(prior_version)),
        ("schema_migrated_to", str(new_version)),
        ("schema_migrated_at", now_iso),
        ("schema_migrated_by", client_version),
    ]
    conn.executemany(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rows,
    )
    return True


#: The DB-side enforcement of the v6 counter. A module constant so the migration
#: and any fresh-schema path install the SAME text, and so scitex-db's Postgres
#: translation has exactly one thing to read.
REVISION_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS tasks_bump_revision AFTER UPDATE ON tasks
  FOR EACH ROW WHEN NEW.revision = OLD.revision
BEGIN
  UPDATE tasks SET revision = OLD.revision + 1 WHERE id = NEW.id;
END;
"""


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Install ``tasks_bump_revision``. Idempotent, additive, no rewrite.

    WHY THE INCREMENT IS DB-SIDE AND NOT IN ``_store_mutate``: an
    application-side bump is honoured only by processes running a version that
    HAS it. Measured 2026-07-30, this store's writers were simultaneously on
    0.13.5 / 0.17.5 / 0.18.0 / 0.22.0, and the same day produced two independent
    demonstrations that "every writer is current" cannot be established — a
    boot-time daemon serving old code for 12 minutes past its own install, and
    the stub distribution five minor versions behind its shim target. An old
    writer that UPDATEs without bumping leaves a current writer's stale
    ``revision = N`` still matching, so the lock is satisfied and the old
    writer's change is lost with no error anywhere. Unlike the blocked-check
    clock, an optimistic lock has NO safe direction to fail in: it reports
    SUCCESS while losing the write.

    WHY *ASSIGN* AND NOT *REJECT*. scitex-db proposed rejecting any write whose
    revision is not ``OLD.revision + 1`` — symmetrical across engines and clean
    to translate. It is also unusable here: an UPDATE from a writer that knows
    nothing about ``revision`` would ABORT, so fleet writes would fail until
    every container is current, which is the condition just shown to be
    unestablishable. Assign keeps old writers working AND bumps on their behalf,
    removing the lost-update hazard structurally instead of asking ~90
    independent writers to behave.

    ``WHEN NEW.revision = OLD.revision`` is load-bearing twice. It leaves an
    EXPLICITLY passed revision alone, so a writer holding a lock is not
    overwritten; and it makes the nested UPDATE non-recursive, because that write
    changes ``revision`` so the guard cannot match on a re-fire. Verified under
    ``PRAGMA recursive_triggers`` both 0 and 1 — correctness does not rest on a
    default someone may later flip.
    """
    execute_ddl(conn, REVISION_TRIGGER_SQL)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The column names actually present on ``table`` in THIS database file.

    The honest question a guard must ask. ``PRAGMA user_version`` is a STAMP —
    a number some code wrote — and a stamp is metadata, so it can outlive the
    thing it describes. The columns are the artifact itself.
    """
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add ``tasks.card_json`` to a v1 DB. Idempotent, additive, no rewrite.

    ``CREATE TABLE IF NOT EXISTS`` is a NO-OP on an existing table — it will not
    add a column — so a DB created before v2 keeps the old shape forever unless
    something ALTERs it. That silently-missing column is precisely the sort of
    thing a version stamp would have papered over.

    Existing rows get ``card_json = NULL``: the column is added, but NOT
    back-filled (a back-fill needs the YAML, which this layer does not have).
    Those NULLs are load-bearing — they are what makes the S2 read guard REFUSE a
    DB that has not been re-imported, instead of quietly serving cards with their
    unknown fields stripped. Nothing back-fills them: the importer was removed
    with the YAML tier, so a database carrying these NULLs must be replaced with
    one a current version wrote.
    """
    if "card_json" not in table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN card_json TEXT")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add ``record_json`` to users/notifications/messages. Idempotent, additive.

    Same contract as :func:`_migrate_v1_to_v2`: existing rows get NULL and are
    NOT back-filled here — the exporter REFUSES NULL payloads loudly, which is
    what forces the database to be replaced by a current one instead of silently
    exporting stripped records.
    """
    for table in ("users", "notifications", "messages"):
        if "record_json" not in table_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN record_json TEXT")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Add ``tasks.revision`` to a pre-v6 DB. Idempotent, additive, no rewrite.

    ``DEFAULT 0`` back-fills every existing row, and unlike the ``card_json``
    NULLs above that back-fill is CORRECT rather than a placeholder: revision is
    a counter of writes observed under the new model, so "no write has been
    observed yet" genuinely is 0 for every pre-existing card. Nothing is being
    invented.

    THE COLUMN MUST CROSS A STORE MIGRATION VERBATIM and belongs in any
    migration's checksummed column set — it is user-visible causal state, not
    backend bookkeeping. If a copy resets or re-sequences revisions, every lock
    held by a process that read the old store still MATCHES, so concurrent
    writers stop conflicting exactly when they should and last-write-wins
    silently. scitex-db's Postgres tool is built against that requirement.

    And preserving it is NOT sufficient. A writer that read revision=5 from
    SQLite and writes to a copied store finds 5 there and succeeds — its lock is
    satisfied — yet it computed against a read from a DIFFERENT store, so
    anything that landed after the copy point is gone with no error anywhere.
    Preserving the column makes the lock FUNCTION; only quiescing every writer
    makes it MEAN anything across a store swap, because "nothing changed under
    me since I read" is precisely what a swap violates.
    """
    if "revision" not in table_columns(conn, "tasks"):
        conn.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
