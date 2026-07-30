#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DMs in the canonical store — the one public surface for the ``dm_*`` tables.

DESIGN: ``docs/design/dm-into-cards-db.md`` (schema, append-only rules) and
``docs/design/dm-into-cards-db-migration.md`` (migration, multi-host).

WHY THIS EXISTS. Until now, DMs lived in a ``threads.json`` SIDECAR — the only
fleet data the canonical store's protections did not cover. Cards got WAL,
store-identity stamping, tombstones, a no-shrink guard, export and snapshot;
the operator's actual conversation with the fleet got a JSON file and none of
it. Worse, appending one message rewrote every message in every thread, which
is the exact whole-document read-modify-write shape behind the 2026-07 wipes.

The ``messages`` table already in ``cards.db`` looks like a counterexample and
is not: it is a DERIVED MIRROR WITH NO LIVE WRITER. Its only writer,
``_db_sections._insert_messages``, has one caller behind a ``threads=``
argument nothing in ``src/`` passes — a fossil of the deleted YAML tier, whose
newest row predates this module by over a week. Refreshing it would have made
the row count look right while leaving the same trap armed. This module makes
the database the WRITE PATH instead.

Layout — this module is a FACADE and holds no logic of its own:

* :mod:`scitex_cards._dm_ids` — id shapes, host stamp, store resolution
* :mod:`scitex_cards._dm_write` — append, membership, receipts, tombstone
* :mod:`scitex_cards._dm_read` — the membership fold, ordering, unread
* :mod:`scitex_cards._dm_migrate` — backfill, merge, the A/B verify gate
* :mod:`scitex_cards._db_dm_schema` — the v5 DDL and its append-only triggers

WHAT THIS MODULE DOES NOT DO YET. The design stages the cutover, and this is
M0-M3: the schema exists, the history is backfilled, the verify gate is
available, and every NEW DM is written to the database authoritatively. Reads
still come from the sidecar (M4) and the sidecar is still written (M5).
Flipping reads before a backfill has run on a given host would show that host
an EMPTY conversation, which is the failure this whole design exists to avoid.
"""

from __future__ import annotations

from ._dm_ids import (
    derived_member_event_id,
    derived_message_id,
    is_pair_thread,
    new_group_thread_id,
    new_message_id,
    origin_host,
    pair_thread_id,
    peers_of_pair,
    resolve_dm_db,
    utc_now_iso,
)
from ._dm_migrate import (
    BACKFILL_SOURCE,
    MERGE_TABLES,
    backfill_from_sidecar,
    export_dm,
    merge_dm,
    verify_against_sidecar,
)
from ._dm_read import (
    list_members,
    message_count,
    messages_in,
    thread_ids,
    unread_for,
)
from ._dm_write import (
    add_member,
    append,
    append_pair,
    create_group_thread,
    mark_read,
    remove_member,
    tombstone,
)

__all__ = [
    "BACKFILL_SOURCE",
    "MERGE_TABLES",
    "add_member",
    "append",
    "append_pair",
    "backfill_from_sidecar",
    "create_group_thread",
    "derived_member_event_id",
    "derived_message_id",
    "export_dm",
    "is_pair_thread",
    "list_members",
    "mark_read",
    "merge_dm",
    "message_count",
    "messages_in",
    "new_group_thread_id",
    "new_message_id",
    "origin_host",
    "pair_thread_id",
    "peers_of_pair",
    "remove_member",
    "resolve_dm_db",
    "thread_ids",
    "tombstone",
    "unread_for",
    "utc_now_iso",
    "verify_against_sidecar",
]

# EOF
