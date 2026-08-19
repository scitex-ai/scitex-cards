#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package-level HEALTH check for scitex-cards (the ``health`` doctor).

One PURE function, :func:`health`, aggregates a fixed set of store / identity /
delivery checks and returns a machine-readable report in the cross-package
standard shape shared with sac/cct::

    {
      "package": "scitex-cards",
      "ok": <bool: true iff NO check FAILED>,
      "checks": [ {"name", "ok", "detail", "hint"}, ... ],
      "summary": <str>,
    }

Contract
--------
* A check's ``ok`` is THREE-VALUED: ``True`` (pass), ``False`` (fail) or
  ``None`` (UNKNOWN — the check could not measure). "nothing is wrong" and "I
  cannot tell" are different answers and must not collapse into ``ok=true``;
  ``None`` is the only honest report for a check whose evidence is missing.
  The record keeps exactly the four standard fields — the third value rides in
  ``ok`` as JSON ``null`` rather than in a fifth key sac/cct would not read.
* An UNKNOWN does not fail the run (``report["ok"]`` counts only ``False``) but
  it is NAMED in ``summary``, so it can never read as a silent pass.
* Every FAILING **and** every UNKNOWN check carries an ACTIONABLE ``hint`` (the
  exact next step — for an unknown, how to make it measurable). A passing check
  may leave ``hint`` ``None``.
* :func:`health` NEVER raises: a check that errors internally is reported as
  ``ok=false`` with the error captured in its ``hint`` — no silent pass, no
  vague error, no exception out of the function.

Why this exists (0.7.32 incident)
---------------------------------
The unified ``mcp start`` server once starved its own ``initialize`` handshake
when the inbox poll loop ran blocking store IO inline on the event loop — every
fleet agent showed the ``scitex-cards`` server "not connected". The
``channel_drain`` check below (large unseen backlog with ``seen==0``) turns that
class of failure into a one-command diagnosis.

Testability
-----------
:func:`health` accepts explicit ``store`` and ``agent_id`` params so tests are
HERMETIC — a real ``tmp_path`` YAML store and a literal agent id, no dependence
on the process environment. The thin MCP / CLI wrappers pass ``None`` (resolve
from env).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import _inbox
from ._health_backend_mode import check_backend_mode
from ._health_channel_reach import check_channel_reaches_session
from ._health_delivery import check_delivery_confirmed
from ._health_gui import check_gui_resident
from ._health_hook_consumers import check_hook_consumers_registered
from ._health_placeholder_authors import check_no_placeholder_authors
from ._health_stranded_backlog import check_no_stranded_backlog
from ._health_store_identity import (  # noqa: F401  (re-export: import surface)
    _check_store_identity_agrees,
)
from ._health_write_target import check_single_write_target
from ._install_probe import check_install_honest
from ._mcp_channel import recipient_keys, resolve_agent_id

#: Unseen-notification backlog above which — combined with ``seen == 0`` (the
#: agent has NEVER drained) — the channel is judged stuck. A high unseen count
#: with any ``seen > 0`` is a working, merely-busy inbox, so it stays ``ok``.
UNSEEN_BACKLOG_THRESHOLD = 50

#: The exact drain-stuck remediation — DEFINED in `_health_probes`, beside the
#: drain check that is its only consumer, and re-exported here so any caller
#: doing `from scitex_cards._health import _DRAIN_HINT` is unaffected.
from ._health_probes import _DRAIN_HINT  # noqa: E402,F401  (re-export)


# --------------------------------------------------------------------------- #
# Individual checks — each returns {ok, detail, hint}; may raise (wrapped).    #
# --------------------------------------------------------------------------- #
# The STORE checks — "is this the right store, and can we use it?" — moved to
# `_health_store` (this file reached the 512-line cap). THE IMPORT SURFACE DOES
# NOT MOVE: every name is re-exported here, so
# `from scitex_cards._health import _verify_db_store` is the SAME object it
# always was, defined next door. Same rule as the `_health_cards` split below.
#
# `_check_store_identity_agrees` is NOT in this list. It is imported at the
# top of the file from `_health_store_identity`, which is where the
# UUID-AWARE version lives -- the one that asks the identity first and only
# falls back to the path on ADOPT, mirroring the guard's own order. Taking
# it from `_health_store` instead binds `health()` to the stale path-only
# copy and leaves `_health_store_identity` imported by nothing, so the
# doctor reports a verdict the guard does not share: it either names a
# mismatch that is causing no refusals, or stays green through one that is.
# Exactly ONE definition of the name exists in the package; see below.
from ._health_store import (  # noqa: E402  (re-export)
    _check_store_canonical,
    _is_sqlite_db,  # noqa: F401
    _verify_db_store,  # noqa: F401
)


# THE IN-PROCESS PROBES — MOVED to `_health_probes` (this file hit the 512
# cap), and moved to match the convention every other check here already
# follows: one check per `_health_<subject>` module. These five were the last
# defined inline.
#
# THE IMPORT SURFACE DOES NOT MOVE: `from scitex_cards._health import
# _check_agent_id` is the SAME object it always was, defined next door. Same
# rule as the `_health_store` and `_health_cards` splits recorded above.
from ._health_probes import (  # noqa: E402  (re-export)
    _check_agent_id,
    _check_channel_capable,
    _check_channel_drain,
    _check_delivery_liveness,
    _check_notifyd_alive,
)




# --------------------------------------------------------------------------- #
# Card-data invariants — MOVED to `_health_cards` (this file hit the 512 cap).
#
# THE IMPORT SURFACE DOES NOT MOVE: the tests and every caller do
# `from scitex_cards._health import _check_terminal_state_honest`, and every name
# below is the SAME object it always was, defined next door. A split must leave the
# original module re-exporting its public API, or it is a rename with extra steps.
#
# `_health`       = "is the INSTALLATION wired up?" (store, identity, notifyd, channel)
# `_health_cards` = "do the CARDS CONTRADICT THEMSELVES?"
# Different inputs, different failure modes, different fixes.
# --------------------------------------------------------------------------- #
from ._health_cards import (  # noqa: E402,F401  (re-export)
    _CLOSURE_MARKERS,
    _COMPLETED_STATUS,
    _OPEN_STATUSES,
    _TERMINAL_STATUSES,
    _check_no_falsely_blocked,
    _check_terminal_state_honest,
)


# --------------------------------------------------------------------------- #
# Aggregator                                                                  #
# --------------------------------------------------------------------------- #
from ._health_severity import (  # noqa: F401  (re-export: import surface)
    ADVISORY,
    BLOCKING,
    DELIVERY,
    _run_check,
)


def _soft_agent_id(agent_id: str | None) -> str | None:
    """Resolve the agent id, returning ``None`` instead of raising (for drain)."""
    try:
        return resolve_agent_id(agent_id)
    except Exception:  # noqa: BLE001 — absence is handled downstream
        return None


def health(
    *,
    store: str | Path | None = None,
    agent_id: str | None = None,
    unseen_threshold: int = UNSEEN_BACKLOG_THRESHOLD,
) -> dict[str, Any]:
    """Run every scitex-cards health check and return the standard report.

    Parameters
    ----------
    store : str | pathlib.Path | None
        Task-store override. ``None`` resolves via the package precedence chain
        (and enables project-shadow detection); an explicit path is taken as the
        intended store (hermetic tests, ``--tasks``).
    agent_id : str | None
        Agent identity override. ``None`` resolves ``$SCITEX_CARDS_AGENT_ID``.
    unseen_threshold : int
        Unseen-backlog ceiling for :func:`_check_channel_drain`.

    Returns
    -------
    dict
        ``{"package", "ok", "checks", "summary"}`` — EXACTLY these four keys,
        and each check record has exactly ``{name, ok, detail, hint}``. That
        shape is a cross-package contract sac and cct parse; severity is
        therefore expressed through ``ok`` and ``summary`` rather than through
        keys they would not read.

        ``ok`` IS TRUE IFF NO **BLOCKING** CHECK FAILED — not iff every check
        passed. It answers "can I use this cards database", which is the
        question every caller actually has. Delivery and advisory failures are
        named in ``summary`` and keep their own ``ok: false`` in ``checks``, so
        nothing is hidden; they simply no longer decide availability. Before
        this, thirteen untidy rows could report the store as broken, and on
        2026-08-12 an agent believed it and stopped working for hours.

        NEVER raises.
    """
    soft_agent = _soft_agent_id(agent_id)
    graded = [
        _run_check("store_canonical", lambda: _check_store_canonical(store)),
        # Can this process WRITE at all? store_canonical answers the narrower
        # "does a parseable file exist there", and on 2026-07-19 it reported ok
        # while every MCP write was being refused for a store/DB identity
        # mismatch. A check whose name implies coverage it does not have is how
        # that outage stayed invisible.
        _run_check("store_identity", lambda: _check_store_identity_agrees(store)),
        # WHICH ENGINE, on BOTH rails? store_canonical names the card store's
        # engine; nothing named the notification inbox's, and the two can
        # differ — the inbox is a SQLite sidecar located from the store PATH, so
        # pointing the store at a server does not move it. That split is what
        # let a DM commit to the store on 2026-08-01 while no notification was
        # ever created, with every card-side check green. Reported as a FAILURE
        # rather than an info line, because a split is not a normal state.
        _run_check("backend_mode", lambda: check_backend_mode(store), severity=DELIVERY),
        _run_check("agent_id", lambda: _check_agent_id(agent_id)),
        # Did a BACKEND CUTOVER leave undelivered messages behind? Measured
        # 2026-08-14: the rail moved from SQLite to PostgreSQL on 08-11 and
        # stranded 149 unseen notifications — 0 of them migrated — including an
        # answer the operator was waiting on and another agent's retraction of a
        # false outage report. It sat for THREE DAYS with every call reporting
        # success, because the writes and the reads were about different
        # databases. Nothing detected it; someone had to go looking.
        _run_check(
            "no_stranded_backlog",
            lambda: check_no_stranded_backlog(store),
            severity=DELIVERY,
        ),
        # Is a card-event consumer stranded in the DEAD entry-point group? On
        # 2026-08-17 the fleet's only registered consumer sat under the
        # pre-rename group while dispatch read the new one: nothing raised,
        # nothing logged, every check green, the push rail silent. The
        # 2026-08-18 hard cut (alias removed on the operator's ruling) puts any
        # unmigrated producer back into exactly that state. The dispatch-side
        # report is already ERROR level, which is NOT enough — sac's words:
        # "an ERROR with no reader is the same silence in a louder font",
        # after watching four periodic jobs fail every tick for weeks while
        # health stayed green. A condition worth logging loudly is worth an
        # instrument.
        _run_check(
            "hook_consumers_registered",
            check_hook_consumers_registered,
            severity=DELIVERY,
        ),
        # Do any cards record an unexpanded ${VAR} as their author? The
        # 2026-07-19 incident wrote the literal env-var text into created_by.
        # It was repaired on 07-21 and the card closed on "0 rows carry the
        # literal env var" — then a RESTORE brought them back, and the 15 rows
        # were found only because someone re-measured a MONTH later. The
        # write-side guard (_store_identity) stops new ones; nothing was
        # counting what arrives by restore or import, which is precisely what
        # a write guard cannot see. ADVISORY: attribution is damaged, but
        # nothing is currently failing.
        _run_check(
            "no_placeholder_authors",
            lambda: check_no_placeholder_authors(store),
            severity=ADVISORY,
        ),
        _run_check("notifyd_alive", lambda: _check_notifyd_alive(store), severity=DELIVERY),
        # Is anything actually being DELIVERED? notifyd_alive answers the
        # narrower "is the process ticking", and it was green throughout the
        # 2026-07-28 outage in which every tick failed to read the store and
        # the operator's DMs went undelivered for a day. A liveness signal that
        # only proves the loop is spinning is not a signal for what it exists
        # to do.
        _run_check("delivery_liveness", lambda: _check_delivery_liveness(store), severity=DELIVERY),
        _run_check(
            "channel_drain",
            lambda: _check_channel_drain(soft_agent, store, unseen_threshold),
            severity=DELIVERY,
        ),
        _run_check("channel_capable", _check_channel_capable, severity=DELIVERY),
        # Is the BOARD ITSELF reachable on this host? Every check above asks
        # about the store or the notification rail; on 2026-08-14 all of them
        # were green while the operator's browser got ERR_CONNECTION_REFUSED,
        # because nothing was serving :8051 anywhere and no instrument was
        # looking. He had been told that night that the board is the fleet's
        # primary channel. DELIVERY, not BLOCKING: the cards are perfectly
        # readable, they are just not reaching the person they are for —
        # which is the same class of fault as an undelivered notification.
        _run_check("gui_resident", check_gui_resident, severity=DELIVERY),
        # Does the far end ACCEPT what we send? channel_capable (can we push?)
        # and channel_drain (is the inbox consumed?) were both GREEN through the
        # 2026-07-24 outage in which the whole fleet was deaf to the board: the
        # package rename left agent launch lines allowlisting the OLD server
        # name while we registered under the new one, so every push was
        # discarded on arrival while the
        # drain kept marking records seen. Delivery here is fire-and-forget, so
        # a name the client does not know does not delay a notification, it
        # destroys it — silently. This is the only check that asks the far end.
        _run_check("channel_reaches_session", check_channel_reaches_session, severity=DELIVERY),
        # Did anything we pushed ever get CONFIRMED? channel_reaches_session
        # reads the launch line, which only exists when a Claude launcher is in
        # our ancestry — under a container runtime that supplies the allowlist
        # some other way, it reports "not applicable" and the fleet is blind
        # again. This one asks the inbox instead: rows stamped `pushed_at` with
        # no `confirmed_at` are notifications we handed to a transport that
        # never said they arrived. That is the residue of the 2026-07-29 outage
        # (228 rows enqueued and consumed for this agent, ZERO unseen, weeks of
        # operator DMs destroyed) and it is visible with no /proc and no config.
        _run_check(
            "delivery_confirmed",
            lambda: check_delivery_confirmed(soft_agent, store),
            severity=DELIVERY,
        ),
        # Is our own reported version actually TRUE? An orphaned/stale .dist-info
        # reports a version that outlived the code it describes — and the fleet's
        # drift detector reads exactly that string, so a fossil silently turns the
        # detector off. Verified BY CONTENT, never by the version alone.
        # (Incident 2026-07-12: metadata said 0.7.26 while the code ran 0.8.7.)
        _run_check("install_honest", check_install_honest),
        # SQLite is the ONLY write target (the dual-write mirror toggle was
        # DELETED 2026-07-21, not defaulted off — see `_health_write_target`).
        # This replaces the old `dual_write_mirror` sync-check: there is no
        # mirror left to fall out of sync, so the question is now "did the
        # deletion hold" rather than "did the mirror keep up".
        _run_check("single_write_target", check_single_write_target),
        # Is any card CLOSED and OPEN at the same time? A card carrying
        # _log_meta.closed_at that still sits in `deferred` is a ZOMBIE: finished
        # work that nags its owner in every digest, forever, and is invisible
        # precisely because it looks like ordinary backlog. It happened twice and
        # went unnoticed for two days — the comments SAID they were closed; the
        # status field never took it. A conclusion in a comment is not a decision.
        _run_check(
            "terminal_state_honest",
            lambda: _check_terminal_state_honest(store),
            severity=ADVISORY,
        ),
        _run_check(
            "no_falsely_blocked",
            lambda: _check_no_falsely_blocked(store),
            severity=ADVISORY,
        ),
    ]
    # THREE-VALUED aggregation. An UNKNOWN (`ok is None`) is not a fault, so it
    # must not fail the run — but it is not a pass either, so it is counted out
    # of `n_ok` and NAMED in the summary. Collapsing it either way is how a
    # check that measured nothing gets read as a check that found nothing.
    # _run_check returns (record, severity); split them here so `checks` holds
    # ONLY the four-field records the cross-package contract allows.
    checks = [rec for rec, _sev in graded]
    severity_of = {rec["name"]: sev for rec, sev in graded}

    unknown = [c["name"] for c in checks if c["ok"] is None]
    n_ok = sum(1 for c in checks if c["ok"] is True)

    # SEVERITY-AWARE AGGREGATION. `ok` answers the question every caller
    # actually asks -- "can I use this cards database?" -- so ONLY a blocking
    # failure may set it false. Previously any failure did, which meant the
    # answer to "is my store available" was decided by the tidiness of thirteen
    # rows the caller does not own, and on 2026-08-12 an agent read that as an
    # outage and stopped working for hours.
    def _failed(level: str) -> list[str]:
        return [
            c["name"]
            for c in checks
            if c["ok"] is False and severity_of[c["name"]] == level
        ]

    blocked_by, degraded, advisories = (
        _failed(BLOCKING),
        _failed(DELIVERY),
        _failed(ADVISORY),
    )

    # `ok` KEEPS ITS DOCUMENTED MEANING: true iff NO check failed, at any
    # severity. Narrowing it to blocking-only is what this incident argues for,
    # and I wrote that, and then reverted it here.
    #
    # `test_report_ok_is_true_iff_no_check_actually_failed` caught it and was
    # RIGHT to. Those semantics are a CROSS-PACKAGE contract sac and cct both
    # parse, and silently redefining a shared boolean is exactly the
    # no-surprises violation this package spent the night finding in other
    # people's code. A consumer branching on `ok` would begin seeing `true` for
    # a state it currently treats as a fault, with no announcement.
    #
    # So the severity work lands where it is unambiguously safe -- `summary`,
    # a free-text field -- and the `ok` narrowing is raised as a decision WITH
    # the consumers rather than taken from them. That is also the half that
    # fixes the incident: the agent read the summary and quoted "9/14 checks
    # passed".
    ok = not [c for c in checks if c["ok"] is False]

    # THE SUMMARY CARRIES THE SEVERITY, because the top-level keys cannot. It is
    # a free string in the contract, and it is what a human or an agent actually
    # reads first -- the incident was someone reading "9/14 checks passed" and
    # inferring an outage. A scoreboard implies every check weighs the same.
    # The head answers USABILITY, which is NOT the same question as `ok`
    # while `ok` still counts every failure. Reading it from blocked_by
    # keeps the sentence true regardless of which contract `ok` carries.
    head = "cards database USABLE" if not blocked_by else "cards database NOT USABLE"
    summary = f"{head} — {n_ok}/{len(checks)} checks passed"
    if blocked_by:
        summary += "; BLOCKING: " + ", ".join(blocked_by)
    if degraded:
        summary += "; delivery degraded (cards unaffected): " + ", ".join(degraded)
    if advisories:
        summary += "; advisory, board contents only, nothing blocked: " + ", ".join(
            advisories
        )
    if unknown:
        summary += "; unknown: " + ", ".join(unknown)
    return {
        "package": "scitex-cards",
        "ok": ok,
        "checks": checks,
        "summary": summary,
    }


__all__ = ["UNSEEN_BACKLOG_THRESHOLD", "health"]

# EOF
