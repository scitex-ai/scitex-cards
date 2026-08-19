#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read ONE card by id, without rebuilding the board.

The targeted counterpart to the whole-document reader, and the piece a write
verb needs before it can honestly report what it persisted.

WHY IT DID NOT EXIST AND WHY IT DOES NOW. ``update_task`` returns its
in-memory MERGE — what the caller asked for — not what the store holds. That
was measured on the live board 2026-08-18 (dotfiles: a `parked` value echoed
back and absent 30 minutes later; sac: 14 of 178 writes reported success and
did not persist), and the agreed fix is to return the PERSISTED row instead.
Both routes to that were blocked:

* re-reading inside the write transaction is impossible while
  ``_db_mirror.mirror_doc_incremental`` calls ``conn.commit()``
  unconditionally, even on a caller-supplied connection;
* re-reading after commit through ``get_task`` -> ``_model.load_tasks``
  rebuilds EVERY card to answer a question about one, which on a ~5,200-card
  board is the expensive path this package's caches and flock guards exist to
  avoid. Trading a truthful return value for that regression is not a trade
  worth making silently.

So the missing piece was never the re-read; it was an indexed single-row read.
This is it.

IT DECODES ``card_json``, NOT THE TYPED COLUMNS, and that is deliberate — see
:mod:`._db_payload`: "the typed columns are the INDEX; ``card_json`` is the
TRUTH." Reassembling a card from columns would silently drop any field this
module has never heard of; decoding the verbatim payload round-trips fields
nobody here knows about.
"""

from __future__ import annotations

from pathlib import Path

from ._db_payload import card_from_payload
from ._db_users import _db_target


def read_card(task_id: str, store: str | Path | None = None) -> dict | None:
    """The stored card with ``task_id``, or ``None`` if the store has no such row.

    ONE indexed lookup on the primary key. ``None`` means "no row", which a
    caller must distinguish from "a row whose payload is empty" — the second
    returns a dict.

    Deliberately NOT cached. Its reason to exist is answering "what does the
    store hold RIGHT NOW, after my write", and a cache would answer with what
    the store held earlier — the precise failure it is meant to close.
    """
    from ._db import open_db

    if not task_id:
        return None
    conn = open_db(_db_target(store))
    try:
        row = conn.execute(
            "SELECT card_json FROM tasks WHERE id = ?", (str(task_id),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    blob = row[0] if not isinstance(row, dict) else row.get("card_json")
    if not blob:
        # A row exists but carries no payload. Report the row's EXISTENCE
        # rather than inventing a card: `card_payload_json_or_raise` refuses
        # unserialisable writes at source, so an empty payload here is a
        # pre-guard row, and guessing its content from typed columns is what
        # this module's docstring says not to do.
        return {}
    return card_from_payload(blob)


__all__ = ["read_card"]

# EOF
