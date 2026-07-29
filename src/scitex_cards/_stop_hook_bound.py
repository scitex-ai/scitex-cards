#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE BOUND — a hook that can refuse forever is a new outage, not a fix.

The Stop hook blocks an agent's turn from ending while an operator message is
waiting unconfirmed. That is the point. But every blocking mechanism needs an
answer to "and what if the release condition never arrives", because without
one the failure mode of the SAFETY device is an agent that can never finish a
turn — strictly worse than the silent-loss bug it was built to prevent.

THE FOUR FAILURE QUESTIONS, ANSWERED (these are the contract, not commentary):

* **The ack itself fails.** Nothing is lost and nothing is special-cased: an
  unconfirmed record is still unseen, so the next poll returns it and the next
  turn presents it again. The counter below is what stops that from being
  forever.
* **The store is unreadable / absent.** The hook allows the stop and says why
  on stderr. Detection failing must never become the reason an agent cannot
  work — an agent wedged because the board had a bad day is invisible and
  self-inflicted; an agent that stopped early is caught by the failure-net
  sweep.
* **The same message is presented N times without an ack.** After
  :data:`MAX_PRESENTATIONS` the hook STOPS DEMANDING that id, warns on stderr
  naming the id, and lets the turn end. The message is NOT marked seen — it
  stays in the store for the operator and for any other rail. We stop
  escalating, we do not stop remembering.
* **There is no state store at all** (this file cannot be written). Then the
  counter cannot bound the loop, so the harness's own signal does:
  ``stop_hook_active`` is true whenever Claude Code is already continuing
  because of a stop hook, and the caller treats "cannot count + already
  continuing" as exhausted. Degrading to block-once is the safe direction.

Never raises. A bound that can throw is not a bound.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: How many turns in a row one message may block before the hook gives up on
#: it. Three is a deliberate compromise: one is indistinguishable from a
#: dropped block, and anything larger risks a long wedge if an agent has
#: genuinely lost the ability to call the ack verb.
MAX_PRESENTATIONS = 3

#: Sessions retained in the state file. Bounded so an always-on fleet does not
#: grow this file without limit.
_MAX_SESSIONS = 32

#: Counter key when the harness gave us no session id. A GLOBAL counter is the
#: conservative choice here: notification ids are unique, so the only effect is
#: that a given message is presented at most MAX_PRESENTATIONS times ever
#: rather than per session. Under-blocking is the safe direction.
NO_SESSION_KEY = "(no-session)"

_FILENAME = "stop_hook_presented.json"


def state_path(store: str | Path | None = None) -> "Path | None":
    """``<store_dir>/runtime/stop_hook_presented.json``, or None if unresolvable."""
    try:
        from ._paths import runtime_dir

        return runtime_dir(store) / _FILENAME
    except Exception as exc:  # noqa: BLE001 — resolution must never break the bound
        logger.debug("stop-hook bound: cannot resolve state path: %s", exc)
        return None


def _load(path: "Path | None") -> dict:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("stop-hook bound: unreadable state at %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _prune(sessions: dict) -> dict:
    """Keep the most recently touched sessions only."""
    if len(sessions) <= _MAX_SESSIONS:
        return sessions
    ordered = sorted(
        sessions.items(),
        key=lambda kv: str((kv[1] or {}).get("ts") or ""),
        reverse=True,
    )
    return dict(ordered[:_MAX_SESSIONS])


def _session_counts(doc: dict, key: str) -> dict:
    entry = (doc.get("sessions") or {}).get(key)
    counts = (entry or {}).get("counts") if isinstance(entry, dict) else None
    return dict(counts) if isinstance(counts, dict) else {}


def counts_for(
    session_id: "str | None", store: str | Path | None = None
) -> dict[str, int]:
    """How many times each id has ALREADY been presented in this session.

    A pure read, so the caller can decide what to present BEFORE incrementing
    anything. Counting an id we then decide not to show would burn its retry
    budget on a turn where it was never delivered — the same "charged for a
    message you never saw" error this whole rail exists to eliminate, just one
    level up. Never raises; an unreadable state file reads as "no history".
    """
    raw = _session_counts(_load(state_path(store)), str(session_id or NO_SESSION_KEY))
    out: dict[str, int] = {}
    for nid, n in raw.items():
        try:
            out[str(nid)] = int(n)
        except (TypeError, ValueError):
            continue
    return out


def record_presented(
    session_id: "str | None",
    ids: list[str],
    store: str | Path | None = None,
) -> dict:
    """Count one presentation of each id and report the running totals.

    Call this ONLY for ids actually delivered this turn. Returns
    ``{"counts": {id: n_after_this_presentation}, "durable": bool}``.
    ``durable`` is False when the count could not be persisted — the caller
    must then fall back to the harness's ``stop_hook_active`` signal, because
    an un-persisted counter cannot bound anything.

    Never raises.
    """
    key = str(session_id or NO_SESSION_KEY)
    wanted = [str(i) for i in (ids or []) if i]
    if not wanted:
        return {"counts": {}, "durable": True}
    path = state_path(store)
    doc = _load(path)
    sessions = doc.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
        doc["sessions"] = sessions
    counts = _session_counts(doc, key)
    for nid in wanted:
        try:
            counts[nid] = int(counts.get(nid, 0)) + 1
        except (TypeError, ValueError):
            counts[nid] = 1
    sessions[key] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "counts": counts,
    }
    doc["sessions"] = _prune(sessions)
    durable = _write(path, doc)
    return {"counts": {nid: counts[nid] for nid in wanted}, "durable": durable}


def _write(path: "Path | None", doc: dict) -> bool:
    """Persist atomically. Returns False on ANY failure — never raises."""
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("stop-hook bound: cannot persist state at %s: %s", path, exc)
        return False


def exhausted(counts: dict, limit: int = MAX_PRESENTATIONS) -> set:
    """Ids ALREADY presented ``limit`` times — do not demand these again.

    Takes PRIOR counts (from :func:`counts_for`), so the comparison is ``>=``:
    an id with ``limit`` presentations behind it has had its chances, and the
    turn about to start must not be a fourth.
    """
    out: set = set()
    for nid, n in (counts or {}).items():
        try:
            if int(n) >= int(limit):
                out.add(str(nid))
        except (TypeError, ValueError):
            continue
    return out


__all__ = [
    "MAX_PRESENTATIONS",
    "NO_SESSION_KEY",
    "counts_for",
    "exhausted",
    "record_presented",
    "state_path",
]

# EOF
