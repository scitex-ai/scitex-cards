#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CURRENCY gate — an outdated or broken install ERRORS, never warns.

Companion to the store-local MIN-CLIENT-VERSION FLOOR (``_min_client_version.py``,
"FLOOR #548") — that gate is the OFFLINE backstop: it enforces THIS process's
version against a floor stamped INTO the store, on every DB connection, with
zero network. THIS gate is the freshness+integrity check: it compares the
INSTALLED distribution against the latest release AND validates its payload
(ambiguous dist-info / missing RECORD files — the incident class this closes),
via scitex-dev's dedicated staleness module. Operator directive: outdated or
broken invocations must ERROR, not warn — same ruling as FLOOR #548, applied
at the two process ENTRY points (CLI, MCP server) rather than at DB-open.

DECOUPLING. scitex-dev is an OPTIONAL dependency (the ``currency`` extra) —
a standalone scitex-cards install without scitex-dev keeps working exactly as
before; this gate is then simply a no-op. Never promote it to a hard
dependency.

THE GATE'S OWN REMEDY IS UNSAFE INSIDE A CONTAINER, AND THIS MODULE SAYS SO.
Measured by scitex-storage 2026-07-28 with a discriminating control:

    agent            overlay   whiteouts masked      dist-info at next boot
    grant            0.17.10   0.17.5 + 0.17.7       2   -> RAIL DEAD AT BOOT
    scitex-storage   0.17.10   0.17.7 + 0.17.9       1   -> fine

Same version, same base, both healthy at the time of measurement, OPPOSITE
restart-safety. The only difference is WHEN each ran the upgrade — i.e. which
base copy was underneath at that moment.

The mechanism: `pip install -U` inside an apptainer overlay writes the new
distribution into the WRITABLE layer and leaves a whiteout masking the copy in
the base underneath. An overlayfs whiteout masks exactly ONE NAME. When the base
image is next rebuilt, that whiteout covers a name that no longer exists while
the NEW base copy is masked by nothing — so two dist-info directories become
visible, metadata turns ambiguous, and the rail dies AT BOOT.

So the gate CLEARS the immediate condition and ARMS a latent one, and nothing
reports it until a base bump. Every agent it nudged into `pip install -U` became
restart-unsafe. That is very likely the source of the duplicate-dist-info
incidents this gate exists to catch — the control above is what makes that a
finding rather than a suspicion.

Neither agent could have seen this from inside their own container: whiteout
names are invisible in the merged view. Which is why the remedy has to be
qualified HERE, at the point of prescription, rather than left to the reader.

THE VISIBILITY ASYMMETRY (incident 2026-07-29, measured by agent ``grant``)
--------------------------------------------------------------------------
scitex-cards has TWO rails into the same store, and only ONE of them is
gated by :func:`check_currency`::

    scitex-cards --version     -> answered 0.17.7          (CLI rail, gated)
    scitex-todo  list-tasks    -> REFUSED "0.17.7 is behind latest 0.17.9"
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
from dataclasses import dataclass
from pathlib import Path

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

    Deliberately conservative: this only ever DOWNGRADES a remedy from
    "run pip install -U" to "ask for a rebake", so a false positive costs a
    cautious message while a false negative restores the trap. When the answer
    cannot be determined, say NO and leave the original remedy alone — claiming
    a container we are not in would misdirect a standalone user.
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


#: Appended to the gate's own error when we are demonstrably layered. Kept as a
#: module constant so a test can assert the wording without provoking the gate.
OVERLAY_REMEDY = (
    "\n\n"
    "!! DO NOT RUN `pip install -U` HERE. This process's site-packages is a "
    "LAYERED (overlay) filesystem, so a local install is not a fix — it is a "
    "deferred break.\n"
    "   An overlayfs whiteout masks exactly ONE NAME: the base copy that "
    "happens to be underneath right now. When the base image is next rebuilt, "
    "that whiteout covers a name that no longer exists, the NEW base copy is "
    "masked by nothing, and TWO dist-info directories become visible — "
    "ambiguous metadata, and this rail dies AT BOOT rather than now.\n"
    "   Measured 2026-07-28: two agents on the same version and the same base, "
    "both healthy, had OPPOSITE restart-safety purely because they upgraded at "
    "different times.\n"
    "   CORRECT REMEDY: ask for a BASE REBAKE (sac), then restart onto the new "
    "image. Fleet-managed packages arrive by rebake; they are not pip-installed "
    "into overlays.\n"
    "   If you must unblock yourself RIGHT NOW and accept that the next restart "
    "will need a rebake anyway, say so explicitly when you report it — do not "
    "leave the mortgage undocumented for whoever boots this container next."
)


def check_currency() -> None:
    """Raise if this install is stale or its payload is broken (CURRENCY gate).

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).

    When the gate fires INSIDE an overlay, its own remedy (`pip install -U`) is
    re-raised with :data:`OVERLAY_REMEDY` appended. We do not own that message —
    it is scitex-dev's — so we qualify it rather than rewrite it, and the
    original text is preserved verbatim above the addition.
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return
    try:
        ensure_current("scitex-cards")
    except Exception as exc:  # noqa: BLE001 - re-raised below, never swallowed
        if not _running_over_overlay():
            raise
        raise type(exc)(f"{exc}{OVERLAY_REMEDY}") from exc


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


def currency_verdict() -> CurrencyVerdict:
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
    try:
        import importlib

        staleness = importlib.import_module("scitex_dev.staleness")
        ensure_current = staleness.ensure_current
        # The ``StalenessError`` lookup belongs INSIDE the guard: on a PEP-562
        # module it runs scitex-dev's own ``__getattr__``, i.e. third-party
        # code that can fail exactly like ``ensure_current`` can.
        stale_error = getattr(staleness, "StalenessError", Exception)
    except _RAIL_SAFE_ERRORS:  # absent/broken tooling is UNKNOWN, not OK
        return unknown

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


#: The remedy WE author, and it is deliberately NOT an in-place pip upgrade.
#: Inside an apptainer overlay an in-place upgrade leaves a whiteout masking
#: exactly ONE dist-info name; on the next base rebake that whiteout covers a
#: name that no longer exists, the new base copy is masked by nothing, TWO
#: dist-info directories appear, and the rail is dead AT BOOT — before any
#: command runs. (Measured: two agents, same version, same base, both healthy,
#: OPPOSITE restart-safety, differing only in WHEN they upgraded.) The text
#: below therefore never spells an in-place upgrade command, not even to
#: forbid one: this constant is asserted free of it, so a later edit cannot
#: smuggle the bad remedy back in as an aside.
STALE_REMEDY = (
    "REMEDY - REBAKE THE CONTAINER BASE IMAGE with the current scitex-cards, "
    "then restart this agent onto the new base. Do NOT upgrade this package "
    "in place inside a running apptainer overlay: the overlay records a "
    "whiteout masking exactly ONE dist-info name, and on the next base rebake "
    "that whiteout covers a name that no longer exists - the new base copy is "
    "masked by nothing, TWO dist-info directories appear, and the rail is "
    "dead AT BOOT. (Measured: two agents, same version, same base, both "
    "healthy, OPPOSITE restart-safety, differing only in WHEN they upgraded.) "
    "Any in-place upgrade command in the scitex-dev message above is a "
    "bare-host remedy and does not apply inside a container overlay."
)

#: Names the SIBLING rail EXPLICITLY. The reader is an agent whose Python call
#: just succeeded and who therefore has no reason to suspect anything is
#: wrong — the whole job of this text is to tell them WHICH rail is down and
#: that it will stay silent about it.
#:
#: BOTH console-script names are spelled out, and that is not redundancy. The
#: command that actually refused in the 2026-07-29 incident was ``scitex-todo
#: list-tasks`` — the LEGACY script, which ``pyproject.toml`` still installs
#: alongside ``scitex-cards`` (both resolve to ``scitex_cards._cli:main``) and
#: which much of the fleet still types. A reader who types ``scitex-todo`` may
#: not recognise a warning phrased only in terms of ``scitex-cards``, which
#: would defeat the single purpose of this text. Name whichever form they use.
_STALE_HEADER = (
    "scitex-cards CURRENCY: this Python call SUCCEEDED, but the CLI/MCP rail "
    "for this same package is currently REFUSING. Both console scripts are "
    "affected - 'scitex-cards list-tasks' AND its still-installed legacy "
    "alias 'scitex-todo list-tasks' are the same program and will BOTH FAIL "
    "until this install is fixed, as will every other scitex-cards CLI "
    "command and the scitex-cards MCP server, while Python calls such as "
    "dm_send keep working. Nothing on this rail will error, so this warning "
    "is the only signal you get."
)


def stale_warning_text(detail: str | None) -> str:
    """Compose the warn-once text: which rail is down, why, and the remedy."""
    return "\n".join(
        (
            _STALE_HEADER,
            "",
            f"scitex-dev reports: {detail}",
            "",
            STALE_REMEDY,
        )
    )


# Warn-once state. ``_CACHED_VERDICT`` also bounds the COST: ``ensure_current``
# does real work (payload validation, a freshness lookup) and the Python rail
# calls this on every DM — so the measurement is taken at most ONCE per
# process, not once per message. The lock keeps "exactly once" true when two
# threads send concurrently. Tests reset both via ``monkeypatch.setattr``.
_STATE_LOCK = threading.Lock()
_CACHED_VERDICT: CurrencyVerdict | None = None
_WARNED_STALE = False


def warn_if_stale_once() -> CurrencyVerdict:
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
    """
    global _CACHED_VERDICT, _WARNED_STALE
    try:
        with _STATE_LOCK:
            verdict = _CACHED_VERDICT
            if verdict is None:
                verdict = currency_verdict()
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
    "OVERLAY_REMEDY",
    "STALE_REMEDY",
    "CurrencyVerdict",
    "check_currency",
    "currency_verdict",
    "stale_warning_text",
    "warn_if_stale_once",
]

# EOF
