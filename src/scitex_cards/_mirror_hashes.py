#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Content hashes: the mechanism that answers "which cards actually changed?".

Extracted from :mod:`scitex_cards._db_mirror` (PURE MOVE -- no behaviour change),
which re-exports every name here so no importer moves. The mirror's whole reason
for existing is that ``_save_doc_unlocked`` receives the WHOLE doc and does not
know which card the caller touched; these hashes are how that question gets
answered without asking the caller. See ``_db_mirror``'s module docstring for the
8.69 s measurement that made it necessary.

Split out because the orchestrator that USES these had reached the repo's
512-line ceiling, and a data-integrity path is the wrong place to start deleting
the comments that explain it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only -- no driver is imported at runtime
    from ._backend_connect import StoreConnection

import hashlib
import json

# Shape-agnostic row access. psycopg's dict_row is a real dict and raises
# KeyError on a positional index, and since #693 open_db can hand this
# module a PostgreSQL connection.
from ._schema_probe import row_values


#: Per-card content hashes, so a write can tell what actually changed.
HASH_TABLE = "mirror_hashes"

_HASH_DDL = f"""
CREATE TABLE IF NOT EXISTS {HASH_TABLE} (
    task_id TEXT PRIMARY KEY,
    hash    TEXT NOT NULL
)
"""



def _card_hash(card: dict) -> str:
    """Stable content hash of one card. ``default=str`` so a stray datetime or
    ruamel scalar cannot make an unchanged card look changed every write."""
    blob = json.dumps(card, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()  # noqa: S324


def _section_hash(value) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()  # noqa: S324


def _existing_hashes(conn: StoreConnection) -> dict[str, str]:
    conn.execute(_HASH_DDL)
    rows = conn.execute(f"SELECT task_id, hash FROM {HASH_TABLE}").fetchall()
    # row_values, NOT r[0]/r[1]: the annotation says StoreConnection, but an
    # annotation is a claim, not a guarantee -- this takes the CALLER's
    # connection, and since #693 that caller can be holding a psycopg one, whose
    # dict_row raises KeyError on a positional index.

    return {v[0]: v[1] for v in (row_values(r) for r in rows)}


__all__ = [
    "HASH_TABLE",
    "_HASH_DDL",
    "_card_hash",
    "_existing_hashes",
    "_section_hash",
]

# EOF
