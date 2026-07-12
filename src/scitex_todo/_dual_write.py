#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 DUAL-WRITE — mirror every card write into SQLite, YAML still canonical.

WHY THIS EXISTS — THE NUMBER (measured on the live store, 1,257 cards, 2026-07-12)
---------------------------------------------------------------------------------
    full YAML rewrite  : 11,176 ms   <- THE COST OF EVERY CARD WRITE, TODAY
    full SQLite rebuild:  1,243 ms   <- what this module adds
    ONE row update     :      4.71 ms  <- what S2 buys

**Every card write takes ELEVEN SECONDS while holding a fleet-wide lock.**

That is the whole convoy, explained, with no theory left over: two agents writing
means the second waits 11 s; ten means the last waits 110 s. A single comment
waiting ~4 minutes (2026-07-11) is exactly what this predicts. The lock is not at
fault — the lock is correct. What we DO while holding it is at fault.

So this migration is not a performance nicety. **The store is already broken, and
SQLite is the repair**: 11,176 ms -> 4.71 ms, a 2,375x reduction.

THE DESIGN QUESTION MEASUREMENT SETTLED
---------------------------------------
The write chokepoint (:func:`scitex_todo._model._save_doc_unlocked`) receives the
WHOLE doc — it does not know which card changed. So a mirror must rebuild all
1,257 rows on every write: O(n), the very thing we are escaping. I expected that
to make the convoy WORSE, and planned a row-diffing engine to avoid it.

That was wrong, and only measuring showed it: **SQLite's full rebuild (1.2 s) is
NINE TIMES FASTER than the YAML rewrite (11.2 s) it sits beside.** Dual-write
costs +11% on top of an already-catastrophic 11 s. It is effectively free — and a
full rebuild is far simpler, and far safer, than a diff engine I would have had to
get exactly right on the fleet's critical store.

THE THREE RULES THIS MODULE ENFORCES
------------------------------------
1. **YAML STAYS CANONICAL.** The mirror is a different file and cannot corrupt it.
   A mirror failure must NEVER fail the user's write — by the time we run, their
   card is already safely on disk. Raising here would turn a cosmetic problem into
   data loss.

2. **BUT IT MUST NEVER BE SILENT.** A mirror that fails quietly lets the DB rot out
   of sync, and S2 would then cut over to a store that is confidently wrong. That
   is the exact disease this codebase spent 2026-07-11/12 digging out of: a signal
   that reads healthy and carries no information. So every failure is logged LOUD,
   counted, and surfaced in ``scitex-todo health``.

3. **IT MUST BE KILLABLE WITHOUT A RELEASE.** ``SCITEX_TODO_DUAL_WRITE`` gates it.
   Default OFF while S1 is proven under real traffic; flipped on per-agent first.
   A store this critical does not get a flag day.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Gate for the S1 mirror. OFF by default: this touches the write path of the
#: fleet's critical store, so it is proven on one agent under real traffic before
#: it becomes the default for everyone. "1"/"true"/"yes"/"on" enable it.
ENV_DUAL_WRITE = "SCITEX_TODO_DUAL_WRITE"

#: Process-local failure counter. Read by ``health`` so a silently-rotting mirror
#: cannot hide: a nonzero count means the DB has DIVERGED from the YAML and S2
#: must not cut over until it is explained.
_failures: list[str] = []


def enabled() -> bool:
    """True when the S1 mirror is switched on for this process."""
    raw = os.environ.get(ENV_DUAL_WRITE, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def failure_count() -> int:
    """How many mirror writes have failed in this process."""
    return len(_failures)


def failures() -> list[str]:
    """The recorded mirror failures (most recent last)."""
    return list(_failures)


def reset_failures() -> None:
    """Clear the counter (tests; and after a successful re-bootstrap)."""
    _failures.clear()


def mirror_after_save(doc: dict, store_path: str | Path) -> bool:
    """Mirror ``doc`` into the shadow DB. NEVER raises. Returns True on success.

    Called from the store's ONE write chokepoint, AFTER the canonical YAML write
    has succeeded and while the store lock is STILL HELD — so the mirror cannot
    interleave with another writer and needs no lock of its own.

    A failure here is NOT the user's problem: their card is already durably on
    disk. So we swallow the exception rather than turning a mirror hiccup into a
    failed card write.

    But we do NOT swallow it QUIETLY. It is logged at ERROR with the exception,
    counted, and exposed through ``health`` — because a mirror that fails in
    silence lets the DB rot out of sync while every check reports green, and S2
    would then cut the fleet over to a store that is confidently wrong.
    """
    if not enabled():
        return False

    try:
        from ._db_bootstrap import mirror_doc

        mirror_doc(doc)
        return True
    except Exception as exc:  # noqa: BLE001 - a mirror must never break the write
        msg = f"{type(exc).__name__}: {exc}"
        _failures.append(msg)
        logger.error(
            "!! DUAL-WRITE MIRROR FAILED (%d failure(s) this process) — the YAML "
            "write SUCCEEDED and your card is safe, but the SQLite mirror is now "
            "OUT OF SYNC with it: %s. The DB must be re-bootstrapped "
            "(`scitex-todo db import`) before S2 cutover; do NOT trust it until "
            "then. Store: %s",
            len(_failures),
            msg,
            store_path,
        )
        return False


def check_mirror_healthy() -> dict[str, object]:
    """Health-doctor check: has the mirror stayed in sync with the canonical YAML?

    ``ok`` is False as soon as ONE mirror write has failed — because a single
    failure means the DB no longer matches the YAML, and there is no partial
    credit for a store that is only mostly right.
    """
    if not enabled():
        return {
            "ok": True,
            "detail": f"dual-write mirror is OFF ({ENV_DUAL_WRITE} unset)",
            "hint": None,
        }
    n = failure_count()
    if n == 0:
        return {
            "ok": True,
            "detail": "dual-write mirror ON; every write mirrored successfully",
            "hint": None,
        }
    return {
        "ok": False,
        "detail": (
            f"dual-write mirror ON but {n} write(s) FAILED to mirror — the SQLite "
            f"DB has DIVERGED from the canonical YAML. Last: {_failures[-1]}"
        ),
        "hint": (
            "Your cards are safe (the YAML write is canonical and succeeded). But "
            "the DB is now unreliable and MUST NOT be cut over to. Re-bootstrap it "
            "from the YAML: `scitex-todo db import`. Then investigate why the "
            "mirror failed — a mirror that fails under real traffic is exactly the "
            "thing S1 exists to discover BEFORE S2 trusts the DB."
        ),
    }
