#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hit/miss tallies for the two load-bearing read caches.

WHY THESE COUNTERS EXIST: both caches fail SILENTLY. A cache that stops
serving does not raise — it returns the right answer every time and merely
pays, on every call, the cost it was built to avoid. Nothing in this package
could previously tell a working cache from a dead one.

That is not hypothetical. :func:`_db_users._cache_key` records a keying bug
that "broke notify dispatch a second time": an ambient read keyed on ``None``
while a write naming the same board keyed on its path, so the write never
retired the entry the reader was being served from. A wrong key is exactly a
100% miss rate, and a miss rate is exactly what nothing was measuring.

Both caches are worth the instrument because both are load-bearing, with the
regressions to prove it: dropping the registry cache took the suite from
3m36s to a projected ~16m, and ``/dm/threads`` cost 10.7 s per request before
the thread caches existed.

Tallies are process-lifetime and cumulative — a RATE instrument, not a state
one — so retiring a cache entry deliberately does not reset them. Reading is
free, opens nothing, and never raises.
"""

from __future__ import annotations

#: ``{cache name: {"hits": int, "misses": int}}``.
_TALLIES: dict[str, dict[str, int]] = {}


def _bump(name: str, field: str) -> None:
    tally = _TALLIES.get(name)
    if tally is None:
        tally = _TALLIES[name] = {"hits": 0, "misses": 0}
    tally[field] += 1


def record_hit(name: str) -> None:
    """Count one read SERVED from ``name``'s cache."""
    _bump(name, "hits")


def record_miss(name: str) -> None:
    """Count one read that ``name``'s cache could not serve."""
    _bump(name, "misses")


def cache_stats(name: str | None = None) -> dict:
    """Return the tallies — one cache's, or every cache's keyed by name.

    Always returns a COPY: these are the numbers used to judge the caches,
    and a caller able to mutate them could not be trusted to report them.
    An unseen cache name reads as zeroes rather than raising, so a probe run
    before the first read is not an error.
    """
    if name is not None:
        return dict(_TALLIES.get(name, {"hits": 0, "misses": 0}))
    return {key: dict(value) for key, value in _TALLIES.items()}


def reset_cache_stats() -> None:
    """Zero every tally. For tests that measure a single read window."""
    _TALLIES.clear()


# EOF
