#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board's store-change stamp — the ``/rev`` wire contract's ``mtime``.

Extracted from :mod:`scitex_cards._django.services` so the two backends can
each answer "has the store changed?" in their own terms without that question
being tangled into ``get_board``'s caching.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["store_change_stamp"]


def store_change_stamp(generation: str) -> float:
    """A float that CHANGES when the cards change. Not a clock.

    ``/rev`` answers ``{mtime, count}`` and the board's ``AutoRefresh`` keys on
    ``f"{mtime}:{count}"`` — an EQUALITY comparison and nothing else. So the
    only property this value must have is: different content, different value.
    It is typed float because that is the established wire shape.

    Parameters
    ----------
    generation
        The store's logical-content hash from
        :func:`scitex_cards._store_write.store_generation`, or ``"absent"``.

    Returns
    -------
    float
        On a FILE store, the database's real mtime. On a SERVER store, a
        content fingerprint derived from ``generation``.

    Notes
    -----
    THE STORE'S mtime IS THE DATABASE'S, never the sidecar's. Reporting the
    sidecar's meant a permanent ``0.0`` on any real deployment, so the open
    pane only refreshed when the card COUNT changed: a status flip, a priority
    reorder, a reassignment, an edited title — none of those move the count, so
    none of them ever reached the operator's screen.

    ON A SERVER STORE THERE IS NO FILE, and ``resolve_db_path`` REFUSES a DSN
    rather than coercing it (coercion would manufacture an empty store file
    at a mangled path and serve 0 cards while reporting healthy). Calling it
    unconditionally is what made the board answer 500 to every data request
    after the PostgreSQL cutover. The generation hash is already computed on
    this path for the cache key, so the fingerprint costs nothing extra.

    THE SERVER VALUE IS NOT A TIMESTAMP. Nothing may compare it against a
    clock, subtract two of them, or read it as an age — it is only ever equal
    or not equal to its predecessor. It is deliberately far outside plausible
    epoch-seconds range so that anything which does treat it as a time is
    obviously, loudly wrong rather than subtly skewed.

    WAL can move a FILE store's mtime without a card change, so that branch may
    tick spuriously. That costs exactly ONE extra ``/graph`` fetch: the
    frontend's ``skipIfUnchanged`` compares the fresh payload against the last
    rendered one and returns before re-rendering, so there is no flash and no
    scroll jump. A spurious refresh is invisible; a refresh that never happens
    is what the operator had been living with.
    """
    from .._db import resolve_db_path
    from .._store_target import resolve_store_target
    from .._store_url import is_postgres_url

    if is_postgres_url(resolve_store_target(None)):
        if generation == "absent":
            return 0.0
        return float(int(generation[:12], 16))

    db_path = Path(resolve_db_path(None))
    return db_path.stat().st_mtime if db_path.exists() else 0.0
