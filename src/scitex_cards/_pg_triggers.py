#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PostgreSQL equivalents of the schema's inline-body guard triggers.

AN INLINE TRIGGER DOES NOT PORT AS A STRING SWAP. Its body is inline SQL with
``RAISE(ABORT)``; PostgreSQL needs a plpgsql FUNCTION plus a trigger that
calls it, with ``RAISE EXCEPTION``, ``IS DISTINCT FROM`` for null-safe
comparison, a parenthesised ``WHEN``, and an explicit ``RETURN`` in BEFORE
triggers. ``CREATE TRIGGER IF NOT EXISTS`` has no PostgreSQL form at all --
``CREATE OR REPLACE TRIGGER`` (PG >= 14) is the idempotent equivalent, which
is what lets the schema script be re-run safely.

THESE ARE NOT RE-DERIVED. They were read back out of the running PostgreSQL
with ``pg_get_triggerdef`` / ``pg_get_functiondef``, so what ships here is
byte-identical to DDL that is demonstrably enforcing on the live store. A
hand-retyped guard that looks right and silently permits the thing it was
meant to forbid is the failure this avoids.

SHIPPED PRE-SPLIT, as complete statements rather than one script, because a
plpgsql body is dollar-quoted and contains its own semicolons. Feeding that
to a ``;``-splitter tears the body apart mid-function. Teaching the splitter
dollar-quoting would be a second parser to keep correct; pre-splitting is the
same result with nothing to get wrong.
"""

from __future__ import annotations

#: Complete, individually-executable statements: each guard's function first,
#: then the trigger that calls it. Order matters -- a trigger cannot reference
#: a function that does not exist yet.
PG_TRIGGER_STATEMENTS: tuple[str, ...] = (
    # --- dm_messages_immutable ---
    """CREATE OR REPLACE FUNCTION dm_messages_immutable_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_messages rows are immutable except deleted_at/deleted_by';
END;
$function$""",
    """CREATE OR REPLACE TRIGGER dm_messages_immutable BEFORE UPDATE ON dm_messages FOR EACH ROW WHEN (((old.thread_id IS DISTINCT FROM new.thread_id) OR (old.sender IS DISTINCT FROM new.sender) OR (old.body IS DISTINCT FROM new.body) OR (old.ts IS DISTINCT FROM new.ts) OR (old.seq IS DISTINCT FROM new.seq) OR (old.origin_host IS DISTINCT FROM new.origin_host) OR (old.record_json IS DISTINCT FROM new.record_json))) EXECUTE FUNCTION dm_messages_immutable_fn()""",
    # --- dm_messages_no_delete ---
    """CREATE OR REPLACE FUNCTION dm_messages_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_messages is append-only: tombstone via deleted_at, never DELETE';
END;
$function$""",
    """CREATE OR REPLACE TRIGGER dm_messages_no_delete BEFORE DELETE ON dm_messages FOR EACH ROW EXECUTE FUNCTION dm_messages_no_delete_fn()""",
    # --- dm_receipts_no_delete ---
    """CREATE OR REPLACE FUNCTION dm_receipts_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_receipts is append-only: a read receipt is never withdrawn';
END;
$function$""",
    """CREATE OR REPLACE TRIGGER dm_receipts_no_delete BEFORE DELETE ON dm_receipts FOR EACH ROW EXECUTE FUNCTION dm_receipts_no_delete_fn()""",
    # --- dm_thread_member_events_no_delete ---
    """CREATE OR REPLACE FUNCTION dm_thread_member_events_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_thread_member_events is append-only: leaving is a leave event';
END;
$function$""",
    """CREATE OR REPLACE TRIGGER dm_thread_member_events_no_delete BEFORE DELETE ON dm_thread_member_events FOR EACH ROW EXECUTE FUNCTION dm_thread_member_events_no_delete_fn()""",
    # --- dm_threads_no_delete ---
    """CREATE OR REPLACE FUNCTION dm_threads_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_threads is append-only: rows are never removed';
END;
$function$""",
    """CREATE OR REPLACE TRIGGER dm_threads_no_delete BEFORE DELETE ON dm_threads FOR EACH ROW EXECUTE FUNCTION dm_threads_no_delete_fn()""",
    # --- schema_meta_retirement_is_one_way ---
    """CREATE OR REPLACE FUNCTION schema_meta_retirement_is_one_way_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION
        'store retirement is one-way: a retired store cannot become current';
END;
$function$""",
    """CREATE OR REPLACE TRIGGER schema_meta_retirement_is_one_way BEFORE UPDATE ON schema_meta FOR EACH ROW WHEN (((old.key = 'store_status'::text) AND (old.value = 'retired'::text) AND (new.value IS DISTINCT FROM 'retired'::text))) EXECUTE FUNCTION schema_meta_retirement_is_one_way_fn()""",
    # --- schema_meta_retirement_is_undeletable ---
    """CREATE OR REPLACE FUNCTION schema_meta_retirement_is_undeletable_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.key IN ('store_status', 'retired_at',
                   'retired_in_favour_of', 'retired_by')
       AND (SELECT value FROM schema_meta WHERE key = 'store_status') = 'retired'
    THEN
        RAISE EXCEPTION
            'a retirement record cannot be deleted: this store is retired';
    END IF;
    RETURN OLD;
END;
$function$""",
    """CREATE OR REPLACE TRIGGER schema_meta_retirement_is_undeletable BEFORE DELETE ON schema_meta FOR EACH ROW EXECUTE FUNCTION schema_meta_retirement_is_undeletable_fn()""",
    # --- schema_meta_version_never_regresses ---
    """CREATE OR REPLACE FUNCTION schema_meta_version_never_regresses_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE schema_meta SET value = OLD.value WHERE key = 'schema_version';

    INSERT INTO schema_meta(key, value)
        VALUES ('schema_version_downgrades_refused', '1')
        ON CONFLICT(key) DO UPDATE
            SET value = ((schema_meta.value)::bigint + 1)::text;

    INSERT INTO schema_meta(key, value)
        VALUES ('schema_version_downgrade_last_at',
                to_char(now() AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;

    INSERT INTO schema_meta(key, value)
        VALUES ('schema_version_downgrade_last_attempt',
                OLD.value || ' -> ' || NEW.value)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;

    RETURN NULL;
END;
$function$""",
    """CREATE OR REPLACE TRIGGER schema_meta_version_never_regresses AFTER UPDATE OF value ON schema_meta FOR EACH ROW WHEN (((new.key = 'schema_version'::text) AND (new.value ~ '^[0-9]+$'::text) AND (old.value ~ '^[0-9]+$'::text) AND ((new.value)::bigint < (old.value)::bigint))) EXECUTE FUNCTION schema_meta_version_never_regresses_fn()""",
    # --- tasks_bump_revision ---
    """CREATE OR REPLACE FUNCTION tasks_bump_revision_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF NEW."revision" = OLD."revision" THEN
    NEW."revision" := OLD."revision" + 1;
  END IF;
  RETURN NEW;
END;
$function$""",
    """CREATE OR REPLACE TRIGGER tasks_bump_revision BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION tasks_bump_revision_fn()""",
)

#: The guard names this module installs, for a non-vacuous completeness check.
PG_TRIGGER_NAMES: frozenset[str] = frozenset({
    "dm_messages_immutable",
    "dm_messages_no_delete",
    "dm_receipts_no_delete",
    "dm_thread_member_events_no_delete",
    "dm_threads_no_delete",
    "schema_meta_retirement_is_one_way",
    "schema_meta_retirement_is_undeletable",
    "schema_meta_version_never_regresses",
    "tasks_bump_revision",
})

#: Keyed by the schema constant's trigger name so ``execute_ddl`` can
#: SUBSTITUTE, not skip,
#: when it meets a ``CREATE TRIGGER IF NOT EXISTS`` while talking to PostgreSQL.
#: Substitution is what keeps the failure loud: an unknown name RAISES, whereas a
#: skip would leave the table present and its guard silently absent -- a database
#: that looks healthy and quietly accepts the thing it must refuse.
PG_TRIGGER_BY_NAME: dict[str, tuple[str, str]] = {
    "dm_messages_immutable": (
        """CREATE OR REPLACE FUNCTION dm_messages_immutable_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_messages rows are immutable except deleted_at/deleted_by';
END;
$function$""",
        """CREATE OR REPLACE TRIGGER dm_messages_immutable BEFORE UPDATE ON dm_messages FOR EACH ROW WHEN (((old.thread_id IS DISTINCT FROM new.thread_id) OR (old.sender IS DISTINCT FROM new.sender) OR (old.body IS DISTINCT FROM new.body) OR (old.ts IS DISTINCT FROM new.ts) OR (old.seq IS DISTINCT FROM new.seq) OR (old.origin_host IS DISTINCT FROM new.origin_host) OR (old.record_json IS DISTINCT FROM new.record_json))) EXECUTE FUNCTION dm_messages_immutable_fn()""",
    ),
    "dm_messages_no_delete": (
        """CREATE OR REPLACE FUNCTION dm_messages_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_messages is append-only: tombstone via deleted_at, never DELETE';
END;
$function$""",
        """CREATE OR REPLACE TRIGGER dm_messages_no_delete BEFORE DELETE ON dm_messages FOR EACH ROW EXECUTE FUNCTION dm_messages_no_delete_fn()""",
    ),
    "dm_receipts_no_delete": (
        """CREATE OR REPLACE FUNCTION dm_receipts_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_receipts is append-only: a read receipt is never withdrawn';
END;
$function$""",
        """CREATE OR REPLACE TRIGGER dm_receipts_no_delete BEFORE DELETE ON dm_receipts FOR EACH ROW EXECUTE FUNCTION dm_receipts_no_delete_fn()""",
    ),
    "dm_thread_member_events_no_delete": (
        """CREATE OR REPLACE FUNCTION dm_thread_member_events_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_thread_member_events is append-only: leaving is a leave event';
END;
$function$""",
        """CREATE OR REPLACE TRIGGER dm_thread_member_events_no_delete BEFORE DELETE ON dm_thread_member_events FOR EACH ROW EXECUTE FUNCTION dm_thread_member_events_no_delete_fn()""",
    ),
    "dm_threads_no_delete": (
        """CREATE OR REPLACE FUNCTION dm_threads_no_delete_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'dm_threads is append-only: rows are never removed';
END;
$function$""",
        """CREATE OR REPLACE TRIGGER dm_threads_no_delete BEFORE DELETE ON dm_threads FOR EACH ROW EXECUTE FUNCTION dm_threads_no_delete_fn()""",
    ),
    "schema_meta_retirement_is_one_way": (
        """CREATE OR REPLACE FUNCTION schema_meta_retirement_is_one_way_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION
        'store retirement is one-way: a retired store cannot become current';
END;
$function$""",
        """CREATE OR REPLACE TRIGGER schema_meta_retirement_is_one_way BEFORE UPDATE ON schema_meta FOR EACH ROW WHEN (((old.key = 'store_status'::text) AND (old.value = 'retired'::text) AND (new.value IS DISTINCT FROM 'retired'::text))) EXECUTE FUNCTION schema_meta_retirement_is_one_way_fn()""",
    ),
    "schema_meta_retirement_is_undeletable": (
        """CREATE OR REPLACE FUNCTION schema_meta_retirement_is_undeletable_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.key IN ('store_status', 'retired_at',
                   'retired_in_favour_of', 'retired_by')
       AND (SELECT value FROM schema_meta WHERE key = 'store_status') = 'retired'
    THEN
        RAISE EXCEPTION
            'a retirement record cannot be deleted: this store is retired';
    END IF;
    RETURN OLD;
END;
$function$""",
        """CREATE OR REPLACE TRIGGER schema_meta_retirement_is_undeletable BEFORE DELETE ON schema_meta FOR EACH ROW EXECUTE FUNCTION schema_meta_retirement_is_undeletable_fn()""",
    ),
    "schema_meta_version_never_regresses": (
        """CREATE OR REPLACE FUNCTION schema_meta_version_never_regresses_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE schema_meta SET value = OLD.value WHERE key = 'schema_version';

    INSERT INTO schema_meta(key, value)
        VALUES ('schema_version_downgrades_refused', '1')
        ON CONFLICT(key) DO UPDATE
            SET value = ((schema_meta.value)::bigint + 1)::text;

    INSERT INTO schema_meta(key, value)
        VALUES ('schema_version_downgrade_last_at',
                to_char(now() AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;

    INSERT INTO schema_meta(key, value)
        VALUES ('schema_version_downgrade_last_attempt',
                OLD.value || ' -> ' || NEW.value)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;

    RETURN NULL;
END;
$function$""",
        """CREATE OR REPLACE TRIGGER schema_meta_version_never_regresses AFTER UPDATE OF value ON schema_meta FOR EACH ROW WHEN (((new.key = 'schema_version'::text) AND (new.value ~ '^[0-9]+$'::text) AND (old.value ~ '^[0-9]+$'::text) AND ((new.value)::bigint < (old.value)::bigint))) EXECUTE FUNCTION schema_meta_version_never_regresses_fn()""",
    ),
    "tasks_bump_revision": (
        """CREATE OR REPLACE FUNCTION tasks_bump_revision_fn()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF NEW."revision" = OLD."revision" THEN
    NEW."revision" := OLD."revision" + 1;
  END IF;
  RETURN NEW;
END;
$function$""",
        """CREATE OR REPLACE TRIGGER tasks_bump_revision BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION tasks_bump_revision_fn()""",
    ),
}

__all__ = ["PG_TRIGGER_BY_NAME", "PG_TRIGGER_NAMES", "PG_TRIGGER_STATEMENTS"]

# EOF
