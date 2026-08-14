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

import sqlite3

from ._ddl import execute_ddl

__all__ = [
    "table_columns",
    "_migrate_v1_to_v2",
    "_migrate_v2_to_v3",
    "_migrate_v5_to_v6",
    "_migrate_v6_to_v7",
    "_migrate_v7_to_v8",
    "_migrate_v8_to_v9",
    "_migrate_v9_to_v10",
    "NOTIFICATION_RAIL_COLUMNS",
    "NOTIFICATION_ORDER_COLUMN",
    "NOTIFICATION_SYNC_COLUMNS",
    "NOTIFICATION_PAYLOAD_TRIGGER",
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


#: The v8 columns on ``notifications``, in the order the fresh-create script
#: declares them. ONE list, consulted by both paths, because a fresh store and a
#: migrated store disagreeing on shape is this repo's own recorded failure.
NOTIFICATION_RAIL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("msg_id", "TEXT"),
    ("pushed_at", "TEXT"),
    ("confirmed_at", "TEXT"),
)


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Give ``notifications`` the three columns the sidecar rail needs.

    Idempotent, additive, no rewrite — each column is added only if absent, and
    every existing row gets NULL, which is the honest value: these record facts
    (which message this notification carries, when it was pushed, when the
    RECIPIENT confirmed it) that were never observed for a pre-v8 row. Nothing
    is invented, unlike a back-filled default would be.

    WHY THIS TABLE AND NOT A NEW ONE. ``notifications`` already exists on the
    fresh-create path with the right shape and the right ``(recipient_id, seen)``
    index, and is currently VESTIGIAL — measured 0 rows on the live store, its
    only writers being the derived-mirror rebuild. So the rail can move into it
    rather than into a parallel table nobody else knows about.

    WHAT THIS DOES NOT DO, stated because the gap is the dangerous part.
    Installing the columns does NOT move the rail. ``_inbox_sqlite`` still writes
    ``runtime/todo.db``, and ``_db_mirror`` still issues ``DELETE FROM
    notifications`` as part of a mirror rebuild — harmless against a derived
    empty table, and DATA LOSS the moment this one becomes the store of record.
    That DELETE must be neutralised in the same change that flips the writers,
    or the migration turns a dead mirror into a silent deletion trigger.

    ``confirmed_at`` is the column that makes delivery provable at all. Today the
    drain acks on ``send()`` returning, which establishes only that our own
    writer accepted the bytes; the health check reports 53 notifications pushed
    and never confirmed for exactly that reason. A recipient-written stamp is
    what turns "dispatched" into "arrived".
    """
    present = table_columns(conn, "notifications")
    for column, sql_type in NOTIFICATION_RAIL_COLUMNS:
        if column not in present:
            conn.execute(f"ALTER TABLE notifications ADD COLUMN {column} {sql_type}")


#: The v9 arrival-order column. Plain ``BIGINT`` because :func:`execute_ddl`
#: translates ONLY ``CREATE TRIGGER`` — column types reach the backend verbatim,
#: so ``BIGSERIAL`` would be a SQLite syntax error and ``AUTOINCREMENT`` a
#: PostgreSQL one. The generator is attached per-backend in the migration below.
NOTIFICATION_ORDER_COLUMN = ("seq", "BIGINT")

#: PostgreSQL sequence backing ``notifications.seq``.
_SEQ_NAME = "notifications_seq_seq"


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Give ``notifications`` an ARRIVAL-ORDER column. Idempotent, additive.

    WHY A COLUMN AND NOT AN ORDER BY. The SQLite inbox delivers and acks by
    ``ORDER BY rowid`` — five call sites — and ``rowid`` has no PostgreSQL
    equivalent. Moving the rail without replacing it would silently lose
    delivery order: the SQL stays valid on both engines and the tests stay
    green, which is the worst possible shape for a correctness regression.

    WHY NOT ``ORDER BY ts, id``, which the export path already uses for this
    very table and justifies as "on append-only tables it is the same order
    rowid produced". MEASURED ON THE LIVE RAIL, 2026-08-02, and it is not:

        3496 rows; 1256 positions differ from rowid order
        1051 of those are same-second ties
        8 are genuine TIMESTAMP INVERSIONS against insertion order
        e.g. a row stamped 2026-08-02T00:00:00Z is followed by one
             stamped 2026-08-01T18:07:41Z -- six hours earlier

    The cause is in the signature: ``enqueue(ts=...)`` accepts a CALLER-SUPPLIED
    timestamp, so ``ts`` is not an insert-time clock and nothing keeps it
    monotonic. The export's assumption is safe for the export (which only needs
    a REPRODUCIBLE order) and false for delivery (which needs the ARRIVAL one).
    I nearly adopted it on the strength of the precedent; three minutes of
    measuring the real table is what stopped me.

    THE BACKFILL IS NOT DONE HERE, DELIBERATELY. Existing rows get NULL, which
    is honest: their arrival order lives in the SQLite ``rowid`` of the OTHER
    database and is only knowable while both are open -- i.e. during the carry
    (:mod:`scitex_cards._inbox_carry`). Inventing values here, from ``ts`` or
    from insertion order in this table, would manufacture an order that was
    never observed and make the missing data unrecoverable by looking correct.
    """
    column, sql_type = NOTIFICATION_ORDER_COLUMN
    if column not in table_columns(conn, "notifications"):
        conn.execute(f"ALTER TABLE notifications ADD COLUMN {column} {sql_type}")

    # PostgreSQL gets a real generator so future writers need not compute one.
    # A writer-computed MAX(seq)+1 is portable and WRONG here: ~90 agents share
    # this rail and two enqueues can read the same MAX, which defeats the total
    # order the column exists to provide.
    #
    # SQLite keeps ``rowid`` as its generator; the column is still populated by
    # the carry so a store migrated from SQLite carries its history's order.
    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    if not _is_postgres(conn):
        return
    conn.execute(f"CREATE SEQUENCE IF NOT EXISTS {_SEQ_NAME}")
    conn.execute(
        f"ALTER TABLE notifications ALTER COLUMN {column} "
        f"SET DEFAULT nextval('{_SEQ_NAME}')"
    )
    # Start the sequence above anything the carry already wrote, so a carry that
    # runs BEFORE this migration cannot collide with live enqueues after it.
    # setval with is_called=false makes the NEXT nextval() return exactly this.
    conn.execute(
        f"SELECT setval('{_SEQ_NAME}', "
        f"(SELECT COALESCE(MAX({column}), 0) + 1 FROM notifications), false)"
    )


#: The v10 SYNC columns on ``notifications``. Present FROM CREATION, not
#: retrofitted, because retrofitting them onto a replicated table is a rewrite:
#: every existing row would need an origin and a uuid it never had, and there is
#: no honest value to invent for either.
#:
#: WHAT EACH ONE IS FOR
#:
#: ``origin_node``  which node wrote the row. The repo already spells this
#:                  ``origin_host`` on the four ``dm_*`` tables and populates it
#:                  from :func:`scitex_cards._dm.ids.origin_host`; this is the
#:                  same fact under the sync vocabulary's name, and it is NOT a
#:                  new identity scheme. Store identity remains the PAIR of the
#:                  logical id and PostgreSQL's own ``system_identifier``, which
#:                  lives in ``schema_meta`` where it belongs — a per-row column
#:                  cannot carry a fact about the database.
#: ``row_uuid``     a 128-bit identity for the ROW. ``id`` is ``n_`` + 12 hex =
#:                  48 bits, which is fine as a local key and NOT fine as the
#:                  merge key for rows generated independently on many hosts:
#:                  at fleet volume a 48-bit birthday collision is a delivered
#:                  message silently replacing another one.
#: ``revision``     mutation counter, for a sync protocol to detect concurrent
#:                  edits at all.
#: ``updated_at``   when this row last changed. FOR AUDIT, NEVER FOR MERGE —
#:                  see the conflict rule below.
#: ``deleted_at``   tombstone. The rail never hard-deletes (``supersede`` marks
#:                  seen; nothing else removes a row), so this exists so that a
#:                  future retention policy is expressible without a schema
#:                  change — and so no one reaches for ``DELETE``.
#:
#: THE CONFLICT RULE FOR THIS CLASS, stated here because a blind
#: ``ON CONFLICT DO UPDATE`` is prohibited and a wall clock is never the
#: arbiter. A notification is IMMUTABLE except for three MONOTONE LATCHES:
#: ``seen`` (0 -> 1), ``pushed_at`` (NULL -> first stamp) and ``confirmed_at``
#: (NULL -> first stamp). So merging two divergent copies of one row is not a
#: choice between them: it is the OR of the flags and the EARLIEST non-null
#: stamp, which is order-independent, commutative, and cannot destroy either
#: side's work. That is why a real 2026-08-07 split that diverged in BOTH
#: directions would have been merged correctly by this rule and destroyed by
#: last-writer-wins.
NOTIFICATION_SYNC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("origin_node", "TEXT"),
    ("row_uuid", "TEXT"),
    ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ("updated_at", "TEXT"),
    ("deleted_at", "TEXT"),
)

#: PostgreSQL trigger that back-fills ``record_json`` from the row's own columns
#: whenever an INSERT leaves it NULL.
NOTIFICATION_PAYLOAD_TRIGGER = "notifications_fill_payload"


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Sync columns on ``notifications``, and a payload no client can omit.

    TWO CHANGES, ONE REASON: a fact that MUST be on every row cannot be left to
    the writer, because the writers are not all the same version and never will
    be. Measured 2026-07-30, the fleet ran 0.13.5 / 0.17.5 / 0.18.0 / 0.22.0
    simultaneously; v7 already concluded from that "application-side
    incrementing would require every one of ~90 agent containers to be current
    for the lock to mean anything, and that condition is not establishable".

    THE PAYLOAD TRIGGER IS THE STRUCTURAL END OF A LOSS CLASS. ``record_json``
    was omitted by ``_inbox_postgres.enqueue`` (fixed in #803), by
    ``_inbox_carry.carry_rows`` and by ``_inbox_migrate_postgres`` (both fixed
    alongside this) — three writers, the same omission, found one at a time
    while the fleet was down. Four MORE payload-less rows appeared on the live
    rail after the enqueue fix landed, at 19:02, 22:03, 22:17, 22:50 and 23:27,
    because merged is not deployed and the containers still ran the old client.
    Each was an undelivered operator DM, repaired by hand within a minute.

    A NULL DEFAULT cannot fix that (a default only applies when the column is
    omitted, and it cannot see the other values), and a NOT NULL constraint
    would be WORSE than the disease: it turns a repairable row into a REFUSED
    INSERT, and a refused insert on this table is a message that was never
    enqueued at all. The trigger fills the gap instead: any client, of any
    version, that inserts the nine columns it knows about gets a complete row.

    Idempotent and additive throughout; no row is rewritten. ``origin_node`` and
    ``row_uuid`` stay NULL on pre-existing rows, which is the honest value —
    nobody observed which node wrote them, and inventing one would make the
    missing data unrecoverable by looking correct.
    """
    present = table_columns(conn, "notifications")
    for column, sql_type in NOTIFICATION_SYNC_COLUMNS:
        if column not in present:
            conn.execute(f"ALTER TABLE notifications ADD COLUMN {column} {sql_type}")

    from ._schema_probe import _is_postgres  # noqa: PLC0415 -- import cycle

    if not _is_postgres(conn):
        # SQLite gets the columns but not the trigger. `json_object()` is only
        # enabled by default from SQLite 3.38 and the live host runs 3.37.2 —
        # this repo has already lost 36 hours to SQL that parsed everywhere
        # except on the one machine that mattered. The rail's SQLite writers are
        # in-process and current by construction; the multi-version fleet is on
        # PostgreSQL, which is where the guard is needed and where it works.
        return
    conn.execute(_PAYLOAD_TRIGGER_FN_SQL)
    conn.execute(f"DROP TRIGGER IF EXISTS {NOTIFICATION_PAYLOAD_TRIGGER} ON notifications")
    conn.execute(
        f"CREATE TRIGGER {NOTIFICATION_PAYLOAD_TRIGGER} "
        "BEFORE INSERT ON notifications FOR EACH ROW "
        f"EXECUTE FUNCTION {NOTIFICATION_PAYLOAD_TRIGGER}_fn()"
    )


#: The fill function. ``jsonb_build_object`` -> ``::json`` keeps the key ORDER
#: the enqueue path writes (jsonb would sort them), so a filled row and a
#: written one are byte-comparable.
_PAYLOAD_TRIGGER_FN_SQL = f"""
CREATE OR REPLACE FUNCTION {NOTIFICATION_PAYLOAD_TRIGGER}_fn() RETURNS trigger AS $$
BEGIN
  IF NEW.record_json IS NULL THEN
    NEW.record_json := json_build_object(
      'id', NEW.id,
      'event_type', NEW.event_type,
      'card_id', NEW.card_id,
      'body', NEW.body,
      'actor', NEW.actor,
      'ts', NEW.ts,
      'seen', (NEW.seen <> 0),
      'msg_id', NEW.msg_id
    )::text;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The column names actually present on ``table`` in THIS database file.

    The honest question a guard must ask. ``PRAGMA user_version`` is a STAMP —
    a number some code wrote — and a stamp is metadata, so it can outlive the
    thing it describes. The columns are the artifact itself.

    Delegated to :func:`scitex_cards._schema_probe.column_names` so the artifact
    can be read on PostgreSQL too. ``PRAGMA table_info`` does not exist there,
    and every migration guard below asks this question first — so on that
    backend they would each have seen an empty set and concluded the column was
    missing, then tried to ADD a column that is already present.
    """
    from ._schema_probe import column_names  # noqa: PLC0415 -- import cycle

    return {str(name) for name in column_names(conn, table)}


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
