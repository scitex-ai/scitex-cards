#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CURRENCY gate — a stale or broken install ERRORS on a bare host, WARNS in an overlay.

Companion to the store-local MIN-CLIENT-VERSION FLOOR (``_min_client_version.py``,
"FLOOR #548") — that gate is the OFFLINE backstop: it enforces THIS process's
version against a floor stamped INTO the store, on every DB connection, with
zero network. THIS gate is the freshness+integrity check: it compares the
INSTALLED distribution against the latest release AND validates its payload
(ambiguous dist-info / missing RECORD files — the incident class this closes),
via scitex-dev's dedicated staleness module. It is applied at the two process
ENTRY points (CLI, MCP server) rather than at DB-open.

DECOUPLING. scitex-dev is an OPTIONAL dependency (the ``currency`` extra) —
a standalone scitex-cards install without scitex-dev keeps working exactly as
before; this gate is then simply a no-op. Never promote it to a hard
dependency.

WHERE THE WORDS LIVE. Every constant and pure text function this module emits
is in :mod:`scitex_cards._currency_text` and re-exported here. The split line
is STATE, not topic: this module keeps everything that holds module state (the
warn-once sentinels and the cached verdict), while ``_currency_text`` holds only
pure functions of their arguments. The split therefore says something true about
each side and survives being read from either one.

IT USED TO SAY SOMETHING ELSE, and the difference is worth keeping: the line was
originally drawn around what ``monkeypatch.setattr`` could reach, because
patching a RE-EXPORTED name does not change what the DEFINING module reads. That
rationale is GONE — the collaborators are now parameters
(:func:`check_currency`'s ``is_overlay`` and ``load_ensure_current``,
:func:`currency_verdict`'s ``load_checker``), so no test needs to rewrite this
module's attributes and no future split can neuter one. A structure justified by
a test mechanism outlives the mechanism silently; this one is now justified by
what the code IS.

BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT
-----------------------------------------------------------
That is the rule this module implements, and it is the whole shape of the gate.
On a BARE HOST an in-place upgrade genuinely repairs the install, so the actor
CAN remediate and refusing to run is correct — that path RAISES, and its message
carries scitex-dev's command verbatim. IN AN OVERLAY the actor CANNOT remediate:
the package comes from a READ-ONLY BASE IMAGE they do not control, the only real
repair is an operator REBAKE of that base, and the printed remedy ACTIVELY
CREATES the very fault the gate detects (the full chain is in
``_currency_text``'s docstring, with the measurements). Blocking there leaves an
agent with no working rail AND a harmful instruction. A gate that cannot be
satisfied is a trap, not a gate.

THE VISIBILITY ASYMMETRY (incident 2026-07-29, measured by agent ``grant``)
--------------------------------------------------------------------------
scitex-cards has TWO rails into the same store, and only ONE of them is
gated by :func:`check_currency`::

    scitex-cards --version     -> answered 0.17.7          (CLI rail, gated)
    scitex-cards  list-tasks    -> REFUSED "0.17.7 is behind latest 0.17.9"
    LocalBackend.dm_send(...)  -> SUCCEEDED                (Python rail, ungated)

Their card rail was dead for HOURS with no way to know it. They reach the
operator through the PYTHON path, which does not pass this gate, so DMs kept
arriving normally and nothing ever prompted them to suspect that cards was
broken. One rail dead, one rail alive, and they were watching the live one.

THE FIX IS NOT "ADD THE SAME GATE TO THE PYTHON RAIL". That would take the
LAST WORKING RAIL away from an agent whose CLI is already refusing — a
strictly worse failure than the one it fixes. SETTLED; do not revisit.

What the Python rail gets instead is :func:`warn_if_stale_once`: a
warn-ONCE-per-process notice that names the SIBLING rail explicitly, so the
reader — an agent who does not yet know their other rail is dead — learns it
on the rail they are actually watching. A failure inside that notice cannot
take the rail down with it (:data:`_RAIL_SAFE_ERRORS` states exactly what it
absorbs and what it deliberately still lets through).

THREE-VALUED, ALWAYS (constitution §2)
--------------------------------------
:func:`currency_verdict` answers in a FIXED, DECLARED SHAPE — the same
:class:`CurrencyVerdict` every time, each signal its own named field, and the
verdict itself THREE-valued: ``"current"`` / ``"stale"`` / ``"unknown"``.
``"unknown"`` is the case where scitex-dev is not installed (or could not
answer). ABSENT TOOLING IS NOT EVIDENCE OF CURRENCY: never collapse it into
``"current"``. Collapsing unknown into either pole is the most common bug we
ship.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ._currency_text import (
    CURRENCY_BYPASS_ENV,
    CURRENCY_SEVERITY_ENV,
    INSTALL_COMMAND_REDACTION,
    OVERLAY_HEADER,
    OVERLAY_REMEDY,
    OVERLAY_UPSTREAM_LEAD,
    STALE_REMEDY,
    UNSCRUBBABLE_NOTICE,
    overlay_warning_text,
    scrub_install_commands,
    stale_warning_text,
)

_LOGGER = logging.getLogger(__name__)

#: The distribution this gate speaks for.
_DIST_NAME = "scitex-cards"

#: WHAT A CURRENCY CHECK MAY SWALLOW — and what it must not. READ BEFORE EDITING.
#:
#: The requirement is narrow and worth stating exactly: THE CURRENCY CHECK MUST
#: NEVER TAKE DOWN THE RAIL IT IS ONLY REPORTING ON. It is a diagnostic; the
#: caller's DM, poll or list is the actual work. Both of the "obvious"
#: simplifications of the tuple below are wrong, in opposite directions:
#:
#: * ``Exception`` ALONE LEAKS. ``SystemExit`` derives from ``BaseException``,
#:   not from ``Exception``, so a ``sys.exit()`` (or bare ``raise SystemExit``)
#:   anywhere on the currency path walks straight out of this module. Measured
#:   end-to-end through the real seam: a ``SystemExit`` from the currency call
#:   propagated out of ``dm_send`` and the store was never touched — the DM did
#:   not go out. Reachable from ``ensure_current`` itself, from a PEP-562
#:   module ``__getattr__``, from a raising ``__str__`` on the staleness
#:   exception, and from a logging handler. scitex-dev is an OPTIONAL,
#:   INDEPENDENTLY VERSIONED dependency whose API this module deliberately does
#:   not pin, so "scitex-dev present but its API changed" is precisely the case
#:   this module claims to cover. A third-party library calling ``sys.exit()``
#:   inside a diagnostic helper is a LIBRARY BUG, and swallowing a library bug
#:   is correct.
#: * ``BaseException`` SWALLOWS TOO MUCH. It would eat ``KeyboardInterrupt``,
#:   so Ctrl-C would stop working for the operator whenever a currency check is
#:   in flight — trading one usability bug for another. It would also eat
#:   ``GeneratorExit`` and ``asyncio.CancelledError`` (a ``BaseException`` since
#:   3.8), i.e. unwinds already in progress. Those are not malfunctions to
#:   absorb; they are the USER'S (or the runtime's) INTENT, and intent must
#:   propagate.
#:
#: In one line: SWALLOW LIBRARY MISBEHAVIOUR, PROPAGATE "STOP NOW".
_RAIL_SAFE_ERRORS = (Exception, SystemExit)


def _stale_detail(exc: BaseException) -> str:
    """scitex-dev's message VERBATIM, with a fallback that keeps the VERDICT.

    ``str(exc)`` runs third-party code (the staleness exception's ``__str__``).
    If that blows up we still know the install is stale, and dropping a true
    STALE verdict merely because we could not format its detail would be the
    worst of the three outcomes: the reader's CLI rail really is down and this
    warning is their only signal. The detail degrades; the verdict does not.
    """
    try:
        return str(exc)
    except _RAIL_SAFE_ERRORS:
        return "(scitex-dev refused, but its message could not be rendered)"


def _running_over_overlay() -> bool:
    """True when this interpreter's site-packages sits on a layered filesystem.

    Deliberately conservative: a false positive costs a cautious message and a
    warn-instead-of-raise, while a false negative restores the trap. When the
    answer cannot be determined, say NO and leave the bare-host behaviour alone
    — claiming a container we are not in would misdirect a standalone user AND
    would silently downgrade a gate that should be refusing.
    """
    if os.environ.get("APPTAINER_CONTAINER") or os.environ.get("SINGULARITY_CONTAINER"):
        return True
    try:
        target = str(Path(next(p for p in sys.path if "site-packages" in p)).resolve())
    except (StopIteration, OSError):
        return False
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            mounts = [line.split() for line in fh]
    except OSError:
        return False
    # Longest matching mountpoint wins — "/" would otherwise match everything.
    best_fstype = ""
    best_len = -1
    for parts in mounts:
        if len(parts) < 3:
            continue
        mountpoint, fstype = parts[1], parts[2]
        if target.startswith(mountpoint) and len(mountpoint) > best_len:
            best_len, best_fstype = len(mountpoint), fstype
    return best_fstype == "overlay"


def _load_ensure_current():
    """Return scitex-dev's ``ensure_current``, or ``None`` when it is ABSENT.

    The seam :func:`check_currency` obtains its checker through, so a test can
    supply a hand-rolled one as an ARGUMENT instead of rewriting
    ``sys.modules`` (PA-306 §3). Catches ``ImportError`` and nothing else,
    deliberately: an absent scitex-dev is the documented no-op, but a scitex-dev
    that is PRESENT and malfunctioning must still propagate out of the gate
    rather than be silently downgraded to "no opinion".
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return None
    return ensure_current


def _load_currency_checker():
    """Return ``(ensure_current, stale_error)`` from scitex-dev, or ``None``.

    The seam :func:`currency_verdict` obtains its checker through. BOTH lookups
    stay inside this one guard on purpose: on a PEP-562 module the
    ``StalenessError`` lookup runs scitex-dev's own ``__getattr__``, i.e.
    third-party code that can fail exactly like ``ensure_current`` can. Unlike
    :func:`_load_ensure_current` this swallows the whole rail-safe set, because
    its caller answers UNKNOWN rather than raising.
    """
    try:
        import importlib

        staleness = importlib.import_module("scitex_dev.staleness")
        return staleness.ensure_current, getattr(
            staleness, "StalenessError", Exception
        )
    except _RAIL_SAFE_ERRORS:  # absent/broken tooling is UNKNOWN, not OK
        return None


def check_currency(
    *,
    is_overlay: Callable[[], bool] = _running_over_overlay,
    load_ensure_current: Callable[[], Callable[[str], None] | None]
    = _load_ensure_current,
) -> None:
    """Raise (bare host) or warn (overlay) when this install is stale or broken.

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).

    BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT:

    * BARE HOST — scitex-dev's exception propagates verbatim, install command
      and all, because there that command IS the repair and the actor can run
      it. Do not weaken this path.
    * OVERLAY — a ``logging.WARNING`` carrying :func:`overlay_warning_text`,
      and NO raise. The actor cannot repair a read-only base; refusing would
      leave them with no working rail and an instruction that harms. The
      emitted text is scrubbed of every in-place install command, INCLUDING
      scitex-dev's verbatim message.
    """
    ensure_current = load_ensure_current()
    if ensure_current is None:  # scitex-dev absent: the documented no-op
        return
    try:
        ensure_current(_DIST_NAME)
    except Exception as exc:  # noqa: BLE001 - re-raised or warned below
        if not is_overlay():
            raise
        _LOGGER.warning("%s", overlay_warning_text(_stale_detail(exc)))


# --------------------------------------------------------------------------- #
# The non-raising sibling — for the Python rail                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CurrencyVerdict:
    """The fixed, declared answer shape of a non-raising currency check.

    Attributes
    ----------
    state:
        ``"current"`` — scitex-dev checked and this install is fresh+intact.
        ``"stale"``   — scitex-dev checked and REFUSED; the CLI/MCP rail is
        therefore erroring right now for the same reason.
        ``"unknown"`` — no answer was obtained: scitex-dev is absent (the
        optional ``currency`` extra is not installed) or it failed in a way
        that is not a verdict. NOT evidence of currency.
    detail:
        scitex-dev's message VERBATIM when ``state == "stale"``; ``None``
        otherwise. Verbatim because the reader needs the *actual* versions
        scitex-dev computed, not our paraphrase of them.
    checked:
        Whether the check actually completed and produced a verdict. ``False``
        exactly when ``state == "unknown"`` — the named, separate signal for
        "we did not measure", so no caller has to infer absence from a magic
        string.
    """

    state: str
    detail: str | None
    checked: bool


def currency_verdict(
    *,
    load_checker: Callable[[], tuple | None] = _load_currency_checker,
) -> CurrencyVerdict:
    """Report this install's currency WITHOUT raising — the Python-rail read.

    The non-raising sibling of :func:`check_currency`. Same underlying
    measurement (scitex-dev's ``ensure_current``), opposite failure mode: a
    malfunction here yields a verdict instead of an exception, and never
    no-ops silently into a false "fine". The one thing that still propagates
    is a "stop now" signal that is not an ``Exception`` — Ctrl-C and friends;
    see :data:`_RAIL_SAFE_ERRORS` for why that asymmetry is deliberate.

    Three outcomes, per the constitution's three-valued rule:

    * scitex-dev absent / unimportable   -> ``unknown``  (``checked=False``)
    * ``ensure_current`` raises its own
      ``StalenessError``                 -> ``stale``    (``checked=True``)
    * ``ensure_current`` returns         -> ``current``  (``checked=True``)

    Anything else out of ``ensure_current`` — a ``TypeError`` from a changed
    signature, an internal failure inside scitex-dev — is NOT a verdict about
    us, so it degrades to ``unknown`` rather than being reported as either
    pole.

    ``StalenessError`` is resolved off the module rather than imported by
    name, and falls back to ``Exception`` on a build of scitex-dev that does
    not export it: on such a build, "raised at all" IS the staleness contract
    that :func:`check_currency` already relies on.
    """
    unknown = CurrencyVerdict(state="unknown", detail=None, checked=False)
    loaded = load_checker()
    if loaded is None:  # absent/broken tooling is UNKNOWN, not OK
        return unknown
    ensure_current, stale_error = loaded

    # NESTED ON PURPOSE. The outer guard covers three things the inner
    # ``except`` clause cannot: a non-verdict raise out of ``ensure_current``,
    # a ``TypeError`` from EVALUATING ``except stale_error`` when a changed
    # scitex-dev exports a non-exception under that name (an exception raised
    # while evaluating an except clause is not caught by that clause's
    # siblings), and anything raised inside the handler itself.
    try:
        try:
            ensure_current(_DIST_NAME)
        except stale_error as exc:  # the verdict: this install is refused
            return CurrencyVerdict(
                state="stale", detail=_stale_detail(exc), checked=True
            )
    except _RAIL_SAFE_ERRORS:  # scitex-dev malfunctioned; not a verdict about us
        return unknown
    return CurrencyVerdict(state="current", detail=None, checked=True)


# Warn-once state. ``_CACHED_VERDICT`` also bounds the COST: ``ensure_current``
# does real work (payload validation, a freshness lookup) and the Python rail
# calls this on every DM — so the measurement is taken at most ONCE per
# process, not once per message. The lock keeps "exactly once" true when two
# threads send concurrently. :func:`reset_currency_cache` clears both.
_STATE_LOCK = threading.Lock()
_CACHED_VERDICT: CurrencyVerdict | None = None
_WARNED_STALE = False


def reset_currency_cache() -> None:
    """Forget the cached verdict and the warn-once flag.

    The measurement in :func:`warn_if_stale_once` is taken at most ONCE per
    process, which is what makes it cheap enough for the Python rail to call on
    every DM — and which also makes a process that has already measured unable
    to measure again. This verb is that "again": it exists so a test can run a
    second, independent scenario in the SAME interpreter without rewriting this
    module's attributes (PA-306 §3).

    Takes the same lock as the writer, so a concurrent
    :func:`warn_if_stale_once` sees the cache either whole or cleared, never
    half of each.
    """
    global _CACHED_VERDICT, _WARNED_STALE
    with _STATE_LOCK:
        _CACHED_VERDICT = None
        _WARNED_STALE = False


def warn_if_stale_once(
    *,
    load_checker: Callable[[], tuple | None] = _load_currency_checker,
) -> CurrencyVerdict:
    """Warn ONCE per process that the sibling CLI/MCP rail is refusing.

    This is what the PYTHON rail calls. Contract, in order of importance:

    1. A FAILING CURRENCY CHECK CANNOT TAKE THIS RAIL DOWN. This path exists
       to keep an agent's last working rail working, so it swallows every
       ``Exception`` from the check — and ``SystemExit``, which is NOT an
       ``Exception`` and therefore leaks past the obvious guard — including
       anything unexpected out of scitex-dev itself. All of it degrades to
       ``unknown``.

       It is NOT true that this "never raises under any circumstance", and the
       precise limit is better than a false absolute: ``KeyboardInterrupt``
       and the other non-``Exception`` "stop now" signals (``GeneratorExit``,
       ``asyncio.CancelledError``) DELIBERATELY propagate, because Ctrl-C is
       the operator's intent rather than a library bug and must keep working.
       :data:`_RAIL_SAFE_ERRORS` carries the full reasoning.
    2. It emits exactly ONE ``logging.WARNING`` per process, and only when the
       verdict is ``"stale"``. Repeating it on every ``dm_send`` would be
       noise, against the operator's standing minimum-noise instruction.
    3. The warning names the SIBLING rail explicitly, quotes scitex-dev's
       message verbatim, and carries a BASE-REBAKE remedy (never an in-place
       pip upgrade — see :data:`STALE_REMEDY`).

    Returns the :class:`CurrencyVerdict` so a caller that wants to reason
    about the state, rather than merely surface it, need not measure twice.

    ``load_checker`` is forwarded verbatim to :func:`currency_verdict` — the
    one seam a test injects through, so no test has to rewrite ``sys.modules``
    to reach this path (PA-306 §3). Pair it with
    :func:`reset_currency_cache`, or the FIRST scenario's verdict is the only
    one this process will ever compute.
    """
    global _CACHED_VERDICT, _WARNED_STALE
    try:
        with _STATE_LOCK:
            verdict = _CACHED_VERDICT
            if verdict is None:
                verdict = currency_verdict(load_checker=load_checker)
                _CACHED_VERDICT = verdict
            should_warn = verdict.state == "stale" and not _WARNED_STALE
            if should_warn:
                # Flipped INSIDE the lock and BEFORE the emit: a logging
                # handler that itself blows up must not buy a second warning.
                _WARNED_STALE = True
        if should_warn:
            _LOGGER.warning("%s", stale_warning_text(verdict.detail))
        return verdict
    except _RAIL_SAFE_ERRORS:  # contract #1: this call cannot kill the rail
        return CurrencyVerdict(state="unknown", detail=None, checked=False)


__all__ = [
    "CURRENCY_BYPASS_ENV",
    "CURRENCY_SEVERITY_ENV",
    "INSTALL_COMMAND_REDACTION",
    "OVERLAY_HEADER",
    "OVERLAY_REMEDY",
    "OVERLAY_UPSTREAM_LEAD",
    "STALE_REMEDY",
    "UNSCRUBBABLE_NOTICE",
    "CurrencyVerdict",
    "check_currency",
    "currency_verdict",
    "reset_currency_cache",
    "overlay_warning_text",
    "scrub_install_commands",
    "stale_warning_text",
    "warn_if_stale_once",
]

# EOF
