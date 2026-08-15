#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Globally-unique ids for ``comments[]`` elements — MINTED, never counted.

WHY THE FIELD NEEDS AN ID AT ALL
--------------------------------
``comments[]`` is the one card field that is genuinely APPEND-ONLY, which is
what makes it declarable under ``MergeRule.APPEND`` for multi-host replication
(ADR-0018 D2). APPEND unions elements BY THEIR OWN ID. An element carrying no
id cannot be unioned — only matched by position, and position is precisely what
diverges when two hosts append to the same card at the same time.

WHY A RANDOM TOKEN AND NOT A COUNTER
------------------------------------
An autoincrement primary key is WORSE than no id at all. Two hosts each append
a comment; both mint ``id = 8``; replay then treats two DIFFERENT elements as
the same one and DROPS one of them. That is a LOST WRITE presenting as
successful convergence, and every count still looks correct afterwards. So the
token is drawn from :mod:`secrets` at CREATION time, where two hosts cannot
agree by accident. (This is also why ``task_comments.id INTEGER PRIMARY KEY
AUTOINCREMENT`` in ``_db_schema_sql`` is NOT this id: that column numbers rows
in the local mirror. The id that survives replication lives in the payload.)

THE SHAPE IS THE HOUSE SHAPE, NOT A NEW ONE
-------------------------------------------
``<prefix>`` + 12 lowercase hex chars (48 bits), i.e. ``secrets.token_hex(6)``
— byte-identical in form to the user ids (``u_``, ``_users._store_write``), the
notification ids (``n_``, ``_inbox``), the legacy message ids (``m_``,
``_threads``) and the DM reaction-event ids (``dmr_``, ``_reactions``).

``c_`` is likewise NOT a new prefix: the live store already holds 8,359
comments carrying ``c_`` + 12 hex ids (measured 2026-08-14 against the fleet
store), alongside 1,010 that carry none. This module joins that convention; it
does not start one.

THE POPULATION IS MIXED, SO STAMPING NEVER OVERWRITES
-----------------------------------------------------
Because both kinds of comment are already on disk, :func:`stamp_comment_id`
mints ONLY into an entry that has no usable id. An id is an ADDRESS: rewriting
one orphans whatever already refers to it, which would be a worse bug than the
missing id this module exists to fix.
"""

from __future__ import annotations

import re
import secrets

#: Comment-element id prefix. Joins the existing ``u_`` / ``n_`` / ``m_`` /
#: ``dmr_`` family, and matches the ``c_`` ids already present in the store.
COMMENT_ID_PREFIX = "c_"

#: Hex chars in the random token portion of a comment id (48 bits of entropy).
COMMENT_ID_TOKEN_HEX = 12

#: The EXACT shape every minted comment id has — ``c_`` + 12 LOWERCASE hex
#: chars. ``secrets.token_hex`` emits lowercase, so a caller-supplied id can be
#: checked against the same pattern the random path could have produced.
COMMENT_ID_RE = re.compile(
    rf"^{COMMENT_ID_PREFIX}[0-9a-f]{{{COMMENT_ID_TOKEN_HEX}}}$"
)


def new_comment_id() -> str:
    """Mint a fresh, globally-unique comment-element id.

    Random, never sequential — see the module docstring for why a counter is a
    silent-data-loss bug rather than a merely inferior id.
    """
    return COMMENT_ID_PREFIX + secrets.token_hex(COMMENT_ID_TOKEN_HEX // 2)


def is_comment_id(value: object) -> bool:
    """Whether ``value`` is a well-formed comment id (``c_`` + 12 lower hex)."""
    return isinstance(value, str) and bool(COMMENT_ID_RE.match(value))


def stamp_comment_id(entry: dict) -> dict:
    """Give ``entry`` an ``id``, unless it already carries a usable one.

    THE ONE WAY a ``comments[]`` element is minted anywhere in this package.
    Every append site wraps its freshly-built entry in this call, so the
    field's id policy lives in one place instead of in eleven spellings that
    can drift apart one commit at a time.

    ADDITIVE AND IN-PLACE. No existing key is touched, none is reordered, and
    ``entry`` itself is returned so the call can wrap a dict literal at the
    point of construction.

    PRESERVES AN EXISTING ID. Any non-empty string ``id`` already on the entry
    is kept verbatim — including shapes this module would not mint, because an
    id written by an older or foreign writer is still the address other records
    use to refer to that comment. Only a missing / empty / non-string id is
    replaced.
    """
    existing = entry.get("id")
    if isinstance(existing, str) and existing:
        return entry
    entry["id"] = new_comment_id()
    return entry


__all__ = [
    "COMMENT_ID_PREFIX",
    "COMMENT_ID_RE",
    "COMMENT_ID_TOKEN_HEX",
    "is_comment_id",
    "new_comment_id",
    "stamp_comment_id",
]

# EOF
