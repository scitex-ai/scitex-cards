#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How one health check is RUN and GRADED — the severity axis.

Split out of ``_health`` when that file reached its 512-line cap. THE IMPORT
SURFACE DOES NOT MOVE: ``_health`` re-exports every name, so
``from scitex_cards._health import _run_check`` is the same object it always
was, defined next door. Same rule as the ``_health_store`` and ``_health_cards``
splits.

WHY SEVERITY EXISTS. On 2026-08-12 an agent read ``ok: false`` and "9/14 checks
passed" — where the failures were a file-sidecar inbox, a stopped notifyd, five
stale completion stamps and eight falsely-blocked cards — and concluded the
cards database was refusing its writes. It stopped carding for hours and worked
in prose. ``store_canonical`` said "readable, writable" in the same report.

A report that cannot distinguish "your database is unavailable" from "your board
has thirteen scruffy rows" will eventually be ignored in both directions.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["ADVISORY", "BLOCKING", "DELIVERY", "run_check"]


#: A failure here means THE CARDS DATABASE CANNOT BE USED AS EXPECTED. This is
#: the only class that may set the report's overall ``ok`` to false.
BLOCKING = "blocking"

#: A failure here means NOTIFICATION DELIVERY is degraded. Cards read and write
#: normally. Separated from BLOCKING because an agent asking "can I card this?"
#: gets yes, and separated from ADVISORY because something is actually broken.
DELIVERY = "delivery"

#: A failure here means THE BOARD'S CONTENTS ARE IMPERFECT — stale stamps,
#: falsely-blocked cards. Nothing is unavailable and nothing is blocked. These
#: name the offending card ids and are fixed by ordinary card edits.
ADVISORY = "advisory"


def _run_check(
    name: str,
    fn: Callable[[], dict[str, Any]],
    *,
    severity: str = BLOCKING,
) -> dict[str, Any]:
    """Run one check, coercing its result to the standard record + never raising.

    ``severity`` ANSWERS THE QUESTION THE CALLER ACTUALLY HAS, which is not "did
    every check pass" but "can I use this store". Defaults to BLOCKING so a new
    check is conservatively treated as availability-affecting until someone
    decides otherwise — the safe direction for a field nobody remembered to set.

    IT EXISTS BECAUSE THE ABSENCE OF IT COST AN AGENT A NIGHT. On 2026-08-12 an
    agent read `ok: false` and "9/14 checks passed" — where the four failures
    were a file-sidecar inbox, a stopped notifyd, five stale completion stamps
    and eight falsely-blocked cards — and concluded the cards database was
    refusing its writes. It stopped carding for hours and worked in prose.
    `store_canonical` said "readable, writable" in the very same report.

    A scoreboard implies every check weighs the same. These do not: one of them
    means the store is gone and another means thirteen rows are untidy.

    ``ok`` is preserved THREE-VALUED: a check that returns ``None`` means "I
    cannot tell" and keeps ``None`` here. Coercing that to ``False`` would
    manufacture an alarm out of a measurement nobody took; coercing it to
    ``True`` would hide it, which is the failure mode this whole PR exists for.

    A check that raises is reported as ``ok=false`` with the error in ``hint``
    (never propagated) — an exception is evidence of a fault, not an absence of
    evidence. Any non-passing check with an empty hint gets a fallback hint so
    the "every failing or unknown check carries an actionable hint" rule always
    holds.
    """
    try:
        res = fn()
        raw = res.get("ok")
        ok = None if raw is None else bool(raw)
        detail = str(res.get("detail", ""))
        hint = res.get("hint")
    except Exception as exc:  # noqa: BLE001 — health must NEVER raise out
        ok = False
        detail = f"{name} check errored: {type(exc).__name__}: {exc}"
        hint = f"internal error in the {name} check: {exc}"
    if ok is not True and not hint:
        verdict = "could not be evaluated" if ok is None else "failed"
        hint = f"{name} {verdict}: {detail}"
    # SEVERITY DOES NOT RIDE IN THE RECORD. The four fields are a CROSS-PACKAGE
    # contract that sac and cct parse, and this module's own docstring already
    # refused to add a fifth key for the three-valued `ok`. Adding one for
    # severity would be the same violation with a better excuse -- and the
    # contract tests catch it, which is how I found out.
    #
    # So the classification is returned ALONGSIDE, and `health()` consumes it to
    # decide `ok` and to write the summary. Both of those are inside the
    # contract: `ok` is still a bool, `summary` is still a free string.
    return {"name": name, "ok": ok, "detail": detail, "hint": hint}, severity
