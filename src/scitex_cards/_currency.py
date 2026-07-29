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
non-raising, warn-ONCE-per-process notice that names the SIBLING rail
explicitly, so the reader — an agent who does not yet know their other rail
is dead — learns it on the rail they are actually watching.

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
import threading
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

#: The distribution this gate speaks for.
_DIST_NAME = "scitex-cards"


def check_currency() -> None:
    """Raise if this install is stale or its payload is broken (CURRENCY gate).

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return
    ensure_current("scitex-cards")


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
    measurement (scitex-dev's ``ensure_current``), opposite failure mode: this
    one never raises, and never no-ops silently into a false "fine".

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
    try:
        import importlib

        staleness = importlib.import_module("scitex_dev.staleness")
        ensure_current = staleness.ensure_current
    except Exception:  # noqa: BLE001 — absent/broken tooling is UNKNOWN, not OK
        return CurrencyVerdict(state="unknown", detail=None, checked=False)

    stale_error = getattr(staleness, "StalenessError", Exception)
    try:
        ensure_current(_DIST_NAME)
    except stale_error as exc:  # the verdict: this install is refused
        return CurrencyVerdict(state="stale", detail=str(exc), checked=True)
    except Exception:  # noqa: BLE001 — scitex-dev malfunctioned; not a verdict
        return CurrencyVerdict(state="unknown", detail=None, checked=False)
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
_STALE_HEADER = (
    "scitex-cards CURRENCY: this Python call SUCCEEDED, but the CLI/MCP rail "
    "for this same package is currently REFUSING. Commands like "
    "'scitex-cards list-tasks' (and every other scitex-cards CLI command, and "
    "the scitex-cards MCP server) will FAIL until this install is fixed, "
    "while Python calls such as dm_send keep working. Nothing on this rail "
    "will error, so this warning is the only signal you get."
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

    1. IT NEVER RAISES — under any circumstance, including an unexpected
       exception from scitex-dev itself. This path exists to keep an agent's
       last working rail working; it must never be the thing that takes that
       rail down. Everything degrades to ``unknown``.
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
    except Exception:  # noqa: BLE001 — contract #1: this call cannot fail
        return CurrencyVerdict(state="unknown", detail=None, checked=False)


__all__ = [
    "STALE_REMEDY",
    "CurrencyVerdict",
    "check_currency",
    "currency_verdict",
    "stale_warning_text",
    "warn_if_stale_once",
]

# EOF
