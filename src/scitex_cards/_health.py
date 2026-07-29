#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package-level HEALTH check for scitex-cards (the ``health`` doctor).

One PURE function, :func:`health`, aggregates a fixed set of store / identity /
delivery checks and returns a machine-readable report in the cross-package
standard shape shared with sac/cct::

    {
      "package": "scitex-cards",
      "ok": <bool: true iff EVERY check ok>,
      "checks": [ {"name", "ok", "detail", "hint"}, ... ],
      "summary": <str>,
    }

Contract
--------
* Every FAILING check carries an ACTIONABLE ``hint`` (the exact next step). A
  passing check may leave ``hint`` ``None``.
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

import os
from pathlib import Path
from typing import Any, Callable

from . import _inbox
from ._health_channel_reach import check_channel_reaches_session
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

#: The exact drain-stuck remediation (kept verbatim per the cross-package spec).
_DRAIN_HINT = (
    "channel not draining — ensure `scitex-cards mcp start` is running for this "
    "agent with SCITEX_TODO_AGENT_ID set (needs >=0.7.32 where the poll loop no "
    "longer starves the handshake)"
)


# --------------------------------------------------------------------------- #
# Individual checks — each returns {ok, detail, hint}; may raise (wrapped).    #
# --------------------------------------------------------------------------- #
def _is_sqlite_db(path: Path) -> bool:
    """True when ``path`` begins with the SQLite file magic header."""
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _verify_db_store(path: Path) -> dict[str, Any]:
    """Confirm the canonical database opens and carries a ``tasks`` table."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            n = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "detail": f"canonical database {path} did not open/read ({exc})",
            "hint": (
                f"do NOT overwrite it — a database that fails to open may still "
                f"hold every card, and the recovery is to COPY IT ASIDE FIRST. "
                f"Check the snapshot repo for the newest good copy, and "
                f"`scitex-cards db verify` for the schema report. "
                f"`scitex-cards init-store` creates an EMPTY store and is "
                f"correct only when there is nothing to recover. "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    # WRITABILITY IS MEASURED, NEVER ASSERTED. This probe opens the database
    # `mode=ro`, so it learns nothing about writing — yet the detail string below
    # claims "writable". That word used to be a hardcoded literal, so it could
    # never be false: "a gate that cannot fail is not a gate ... the same as
    # deleting it, except worse: the config still lists it and everyone believes
    # it is working" (constitution §2). It is exactly how the 2026-07-28
    # create-path outage stayed invisible — `add` refused every card while health
    # cheerfully reported the same store readable AND writable. The sibling
    # file-store branch already measures this with `os.access`; this branch now
    # matches it. SQLite also writes `-wal` / `-journal` SIBLINGS, so the
    # DIRECTORY must be writable too: a writable file in a read-only directory
    # still fails every write.
    if not os.access(path, os.W_OK):
        return {
            "ok": False,
            "detail": (
                f"canonical store {path} is NOT writable "
                f"(SQLite, {n} cards, readable) — every card write will fail"
            ),
            "hint": f"fix permissions so {path} is writable (e.g. chmod u+w {path})",
        }
    if not os.access(path.parent, os.W_OK):
        return {
            "ok": False,
            "detail": (
                f"canonical store {path} is readable but its directory "
                f"{path.parent} is NOT writable (SQLite, {n} cards) — SQLite "
                f"cannot create the -wal/-journal siblings a write needs"
            ),
            "hint": (
                f"make the store's directory writable (e.g. chmod u+w {path.parent})"
            ),
        }
    return {
        "ok": True,
        "detail": f"canonical store {path} (SQLite, {n} cards, readable, writable)",
        "hint": None,
    }


def _check_store_canonical(store: str | Path | None) -> dict[str, Any]:
    """Resolve the task store and verify it is the canonical, healthy store.

    The canonical store is the SQLite database ($SCITEX_CARDS_DB). ok when it
    exists, opens, and carries a ``tasks`` table. An EXPLICIT file store (tests,
    ``--tasks <file>``) is taken as the intended target and checked as a
    serialized document with a top-level ``tasks`` key.
    """
    from ._db import resolve_db_path
    from ._paths import resolve_tasks_path

    db = Path(resolve_db_path(store))

    # The canonical store IS the database — verify it directly.
    if db.exists() and _is_sqlite_db(db):
        return _verify_db_store(db)

    # No database. An EXPLICIT file store (tests / `--tasks <file>`) is checked
    # as a serialized document; otherwise the store is genuinely absent.
    resolved = resolve_tasks_path(store)
    if store is not None and resolved.exists():
        if _is_sqlite_db(resolved):
            return _verify_db_store(resolved)
        if not os.access(resolved, os.R_OK):
            return {
                "ok": False,
                "detail": f"store {resolved} is not readable",
                "hint": f"fix permissions so {resolved} is readable (e.g. chmod u+r)",
            }
        if not os.access(resolved, os.W_OK):
            return {
                "ok": False,
                "detail": f"store {resolved} is not writable",
                "hint": f"fix permissions so {resolved} is writable (e.g. chmod u+w)",
            }
        from ._yaml import safe_load

        try:
            with resolved.open(encoding="utf-8") as handle:
                data = safe_load(handle) or {}
        except Exception as exc:  # noqa: BLE001 — a parse fail is a reportable state
            return {
                "ok": False,
                "detail": f"store {resolved} did not parse ({exc})",
                "hint": f"fix the document syntax in {resolved} ({type(exc).__name__}: {exc})",
            }
        if not isinstance(data, dict) or "tasks" not in data:
            return {
                "ok": False,
                "detail": f"store {resolved} has no top-level 'tasks' key",
                "hint": f"add a top-level `tasks:` list to {resolved}",
            }
        return {
            "ok": True,
            "detail": f"file store {resolved} (exists, readable, writable, parses)",
            "hint": None,
        }

    return {
        "ok": False,
        "detail": f"no store: the database {db} is absent",
        "hint": (
            "if this agent should have the FLEET board, the path is wrong — "
            "fix $SCITEX_CARDS_DB rather than creating a store, because a fresh "
            "empty one here becomes a SECOND store, which is how the board was "
            "destroyed on 2026-07-19. `scitex-cards db path` shows what resolved. "
            "Only when this agent genuinely owns a new, separate store is "
            "`scitex-cards init-store` correct. Restoring from a `scitex-cards "
            "db export` dump has NO CLI verb today — it is a Python-level "
            "operation (see scitex_cards._db_bootstrap) — so do not go looking "
            "for an import subcommand."
        ),
    }


def _check_agent_id(agent_id: str | None) -> dict[str, Any]:
    """Resolve the agent identity; fail on unset / 'unknown' / bare ``$VAR``."""
    try:
        resolved = resolve_agent_id(agent_id)
    except Exception as exc:  # noqa: BLE001 — unresolved id is a reportable state
        return {
            "ok": False,
            "detail": f"agent id unresolved ({exc})",
            "hint": (
                "set SCITEX_TODO_AGENT_ID=<your-agent-id> (not blank / 'unknown'); "
                'in .mcp.json use the brace form "${SCITEX_TODO_AGENT_ID}" — '
                "Claude Code does not expand bare $VAR"
            ),
        }
    return {"ok": True, "detail": f"agent id resolved: {resolved}", "hint": None}


def _check_notifyd_alive(store: str | Path | None) -> dict[str, Any]:
    """Check the notifyd delivery daemon via its pidfile — NAMESPACE-AGNOSTIC.

    The daemon stamps ``<store_dir>/runtime/notifyd.pid``, holds an flock for
    its lifetime, and REWRITES the file every tick (a heartbeat).

    The pid alone is not a portable liveness signal: notifyd runs on the bare
    host while fleet agents run in CONTAINERS that share the store by
    bind-mount, and **a pid is only meaningful inside the PID namespace that
    issued it**. Probing a foreign pid with ``os.kill`` raises
    ``ProcessLookupError`` and used to be reported as a stale pidfile — a
    permanent FALSE failure on a perfectly healthy daemon, which is worse than
    no check at all (it teaches the reader to ignore the channel).

    So: same namespace ⇒ probe the pid (sharpest signal, still fail-loud).
    Different namespace ⇒ judge by HEARTBEAT FRESHNESS and never by the pid.
    See :mod:`scitex_cards._delivery._pidfile` for the verdict logic.
    """
    from ._delivery._daemon import pidfile_path
    from ._delivery._pidfile import assess_liveness

    return assess_liveness(pidfile_path(store))


def _check_channel_drain(
    agent_id: str | None, store: str | Path | None, threshold: int
) -> dict[str, Any]:
    """Report unseen vs seen inbox counts for THIS agent; flag a stuck drain."""
    if not agent_id:
        return {
            "ok": True,
            "detail": "agent id unresolved — channel-drain check skipped",
            "hint": None,
        }
    keys = recipient_keys(agent_id, store=store)
    unseen = 0
    total = 0
    for key in keys:
        unseen += len(
            _inbox.poll_inbox(key, unseen_only=True, mark_seen=False, store=store)
        )
        total += len(
            _inbox.poll_inbox(key, unseen_only=False, mark_seen=False, store=store)
        )
    seen = total - unseen
    detail = f"unseen={unseen} seen={seen} (keys={keys})"
    # Working (or merely busy) when the backlog is small OR anything was ever
    # drained. Stuck only when a large backlog has NEVER been drained.
    if unseen <= threshold or seen > 0:
        return {"ok": True, "detail": detail, "hint": None}
    return {"ok": False, "detail": detail, "hint": _DRAIN_HINT}


def _check_channel_capable() -> dict[str, Any]:
    """ok when ``scitex_cards._mcp_channel`` imports and exposes ``_serve``/``_run``."""
    try:
        from . import _mcp_channel as channel
    except Exception as exc:  # noqa: BLE001 — import failure is a reportable state
        return {
            "ok": False,
            "detail": f"import scitex_cards._mcp_channel failed ({exc})",
            "hint": (
                "upgrade to scitex-cards>=0.7.32: pip install -U 'scitex-cards[mcp]'"
            ),
        }
    missing = [attr for attr in ("_serve", "_run") if not hasattr(channel, attr)]
    if missing:
        return {
            "ok": False,
            "detail": f"scitex_cards._mcp_channel missing {missing}",
            "hint": (
                "upgrade to scitex-cards>=0.7.32 (the unified tools+channel "
                "server): pip install -U 'scitex-cards[mcp]'"
            ),
        }
    return {
        "ok": True,
        "detail": "scitex_cards._mcp_channel present (_serve/_run)",
        "hint": None,
    }


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
def _run_check(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one check, coercing its result to the standard record + never raising.

    A check that raises is reported as ``ok=false`` with the error in ``hint``
    (never propagated). A failing check with an empty hint gets a fallback hint
    so the "every failing check carries an actionable hint" rule always holds.
    """
    try:
        res = fn()
        ok = bool(res.get("ok"))
        detail = str(res.get("detail", ""))
        hint = res.get("hint")
    except Exception as exc:  # noqa: BLE001 — health must NEVER raise out
        ok = False
        detail = f"{name} check errored: {type(exc).__name__}: {exc}"
        hint = f"internal error in the {name} check: {exc}"
    if not ok and not hint:
        hint = f"{name} failed: {detail}"
    return {"name": name, "ok": ok, "detail": detail, "hint": hint}


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
        Agent identity override. ``None`` resolves ``$SCITEX_TODO_AGENT_ID``.
    unseen_threshold : int
        Unseen-backlog ceiling for :func:`_check_channel_drain`.

    Returns
    -------
    dict
        ``{"package", "ok", "checks", "summary"}`` — ``ok`` is true iff every
        check is ok. NEVER raises.
    """
    soft_agent = _soft_agent_id(agent_id)
    checks = [
        _run_check("store_canonical", lambda: _check_store_canonical(store)),
        # Can this process WRITE at all? store_canonical answers the narrower
        # "does a parseable file exist there", and on 2026-07-19 it reported ok
        # while every MCP write was being refused for a store/DB identity
        # mismatch. A check whose name implies coverage it does not have is how
        # that outage stayed invisible.
        _run_check("store_identity", lambda: _check_store_identity_agrees(store)),
        _run_check("agent_id", lambda: _check_agent_id(agent_id)),
        _run_check("notifyd_alive", lambda: _check_notifyd_alive(store)),
        _run_check(
            "channel_drain",
            lambda: _check_channel_drain(soft_agent, store, unseen_threshold),
        ),
        _run_check("channel_capable", _check_channel_capable),
        # Does the far end ACCEPT what we send? channel_capable (can we push?)
        # and channel_drain (is the inbox consumed?) were both GREEN through the
        # 2026-07-24 outage in which the whole fleet was deaf to the board: the
        # scitex-cards -> scitex-cards rename left agent launch lines allowlisting
        # the OLD server name, so every push was discarded on arrival while the
        # drain kept marking records seen. Delivery here is fire-and-forget, so
        # a name the client does not know does not delay a notification, it
        # destroys it — silently. This is the only check that asks the far end.
        _run_check("channel_reaches_session", check_channel_reaches_session),
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
            "terminal_state_honest", lambda: _check_terminal_state_honest(store)
        ),
        _run_check("no_falsely_blocked", lambda: _check_no_falsely_blocked(store)),
    ]
    ok = all(c["ok"] for c in checks)
    n_ok = sum(1 for c in checks if c["ok"])
    failing = [c["name"] for c in checks if not c["ok"]]
    summary = f"{n_ok}/{len(checks)} checks passed"
    if failing:
        summary += "; failing: " + ", ".join(failing)
    return {
        "package": "scitex-cards",
        "ok": ok,
        "checks": checks,
        "summary": summary,
    }


__all__ = ["UNSEEN_BACKLOG_THRESHOLD", "health"]

# EOF
