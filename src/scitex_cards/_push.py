#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo's self-contained push delivery wire.

Operator standing direction (lead a2a `8e51b1e072d14e2a81f5171cb5aca9f8`
+ `ffc6629c80e4462a8401fb7e4ebb7240`, 2026-06-12, operator TG12617): the
package must NOT depend on any external agent-runtime CLI for outbound
notifications. Push delivery lives inside scitex-todo itself; the
contract is HTTP, not Python imports (ecosystem doctrine).

Operator TG12618 follow-up: the long-term architecture is a dedicated
stdio MCP channel + a board-event poller per agent, wired via per-agent
`.mcp.json`, all owned by scitex-todo. That is queued as
PR (j) in the implementation roadmap. THIS module is the v1
**HTTP-push** half: a one-shot wake of the agent's turn URL with the
board-event body. Agents that read MCP inbox first (via the future
channel) will pick up the same event from both sides.

Configuration
-------------
``SCITEX_TODO_AGENT_TURN_URLS`` — JSON object mapping agent ids to
their turn URLs (any HTTP endpoint that accepts a JSON ``POST``):

    SCITEX_TODO_AGENT_TURN_URLS='{
        "scitex-todo": "https://agents.example/v1/turn/scitex-todo",
        "lead": "https://agents.example/v1/turn/lead"
    }'

Per-agent fallback ``SCITEX_TODO_TURN_URL_<AGENT_SLUG>`` (agent slug
upper-case + hyphens → underscores) — scitex-todo's own per-agent
turn-url env contract.

When neither env nor the user registry resolves a URL, delivery
returns ``no-turn-url-configured`` (fail-loud); scitex-todo has no
external-runtime fallback.

Loud-but-not-fatal policy (lead-confirmed)
------------------------------------------
* No URL for an agent → return ``{ok: False, reason:
  "no-turn-url-configured"}`` and log ERROR. Callers (UI / cron /
  CLI) decide what to surface; ``handle_nudge`` returns the
  ``ok=False`` to the UI toast and ``handle_comment``'s relay leaves
  a visible marker on the comment.
* HTTP 4xx/5xx → return ``{ok: False, reason: "http-error",
  status: <code>}``; never raise out into the request handler.
* Network exception / timeout → same shape, ``reason:
  "transport-error", error: <str>``.
* ``SCITEX_TODO_PUSH_DRY_RUN=1`` → no network; print the body to
  stdout for dev / test; returns ``ok=True, wire="dry-run"``.

Boot-time announcement (``announce_missing_at_boot``) lists the
agents in the store that don't have a configured URL so the
operator can fix the gap before the first event.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.request

# Turn-URL RESOLUTION (the "where do I deliver?" concern) lives in
# ``_turn_url``; this module owns the delivery WIRE. We re-export the
# resolution surface so existing ``from scitex_cards._push import
# turn_url_for / ENV_MAP / PER_AGENT_PREFIX / ...``
# imports keep resolving unchanged.
from ._turn_url import (
    ENV_MAP,
    PER_AGENT_PREFIX,
    _slug,
    turn_url_for,
)

logger = logging.getLogger(__name__)

ENV_DRY_RUN = "SCITEX_TODO_PUSH_DRY_RUN"

#: Default per-POST timeout in seconds. Env-overridable.
#:
#: Why 30 (not 5):
#:
#: The receiver's ``/v1/turn`` runs the agent's turn SYNCHRONOUSLY before
#: responding (up to its own ``timeout_s=120`` budget). With the prior
#: 5 s client cap, the cron's POST timed out before the receiver had
#: even queued the turn — proj-scitex-dev's ``session.jsonl`` had ZERO
#: nudge entries after the wire-shape fix landed (lead a2a
#: ``0b59485f`` 2026-06-13, P3a(c) pilot).
#:
#: There is no fast-ack path on ``/v1/turn`` (no ``wait=false`` /
#: ``dispatch_only=true`` / ``async=true`` flag — probed); so until
#: the receiver grows one, the pragmatic stopgap is a longer client
#: budget AND treating "we waited a long time + the response stream
#: never closed" as DISPATCHED rather than failure. 30 s lets typical
#: short turns complete cleanly; longer turns flip to
#: ``reason="dispatched"`` (still ok=True) so the cron doesn't fail
#: the whole batch over one slow turn.
#:
#: Callers can override per-call with ``deliver(..., timeout=120.0)``
#: when they need the receiver's full response payload.
ENV_PUSH_TIMEOUT_S = "SCITEX_TODO_PUSH_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 30.0

#: Short per-POST timeout for INTERACTIVE callers (the board's comment
#: relay) that run INSIDE an HTTP request the operator is waiting on. The
#: cron/nudge path keeps the long :data:`DEFAULT_TIMEOUT_S`; the relay
#: runs in-line on ``POST /comment`` so a 30 s receiver stall = a 30 s
#: board hang (operator-reported P1, 2026-06-25). 2 s covers a same-host
#: loopback; past that we return FAST and toast "could not notify".
NOTIFY_TIMEOUT_S = 2.0


def _default_timeout_s() -> float:
    """Lookup the per-POST timeout, honoring ``ENV_PUSH_TIMEOUT_S``.

    Done at call-time (not module-import time) so tests can override
    the env between cases without re-importing the module.
    """
    raw = os.environ.get(ENV_PUSH_TIMEOUT_S)
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def _timeout_result(
    agent: str,
    kind: str,
    url: str,
    err: BaseException,
    timeout: float,
    dispatched_is_ok: bool,
) -> dict:
    """Result dict for a client read-timeout, shared by both except-branches.

    ``dispatched_is_ok=True`` (background/cron) → ``ok=True,
    reason="dispatched"`` (request body fully sent; receiver mid-turn).
    ``dispatched_is_ok=False`` (interactive relay) → ``ok=False,
    reason="timeout"`` — surfaced FAST so the board toasts loud.
    """
    reason = "dispatched" if dispatched_is_ok else "timeout"
    logger.warning(
        "[scitex-todo._push] %s read-timeout after %.1fs for agent=%s → reason=%s. %s",
        url,
        timeout,
        agent,
        reason,
        err,
    )
    return {
        "ok": dispatched_is_ok,
        "agent": agent,
        "wire": "http",
        "kind": kind,
        "url": url,
        "status": None,
        "reason": reason,
        "error": str(err),
    }


def deliver(
    agent: str,
    body: str,
    *,
    kind: str = "notify",
    task_id: str | None = None,
    store_path: str | None = None,
    timeout: float | None = None,
    dispatched_is_ok: bool = True,
) -> dict:
    """Deliver ``body`` to ``agent`` via the configured turn URL.

    ``timeout`` defaults to :data:`DEFAULT_TIMEOUT_S` (env-overridable
    via ``SCITEX_TODO_PUSH_TIMEOUT_S``). The receiver's ``/v1/turn``
    runs the turn synchronously, so a client read-timeout does NOT
    imply the request was lost — the receiver may still be processing
    it. We treat the timeout case as DISPATCHED success (``ok=True,
    reason="dispatched"``) so a single slow turn can't fail the whole
    nudge batch. Callers that need the receiver's full response payload
    can pass an explicit long ``timeout`` (e.g. 120.0).

    ``dispatched_is_ok`` (default ``True``) governs the read-timeout
    verdict. Background callers keep the default (timeout → ``ok=True,
    reason="dispatched"``). INTERACTIVE callers (the comment relay) pass
    ``False`` + a short ``timeout`` (:data:`NOTIFY_TIMEOUT_S`) so a
    timeout returns FAST with ``ok=False, reason="timeout"`` and the
    board toasts a LOUD failure instead of hanging ~30 s and silently
    claiming success (operator P1, 2026-06-25; fail-fast/fail-loud).

    Returns a result dict suitable for inclusion in a JsonResponse:

      {
        "ok": bool,
        "agent": str,
        "wire": "http" | "dry-run",
        "kind": str,
        "url": "<url>" | None,
        "status": <http-status> or None,
        "reason": <one of "delivered" | "dispatched" |
                          "no-turn-url-configured" |
                          "http-error" | "transport-error" |
                          "dry-run">,
      }
    """
    if timeout is None:
        timeout = _default_timeout_s()
    # Dev / test escape hatch.
    if os.environ.get(ENV_DRY_RUN) == "1":
        print(
            f"\n=== scitex-todo PUSH dry-run → {agent} ({kind}) ===\n"
            f"{body}\n=== end {agent} ===\n",
            flush=True,
        )
        return {
            "ok": True,
            "agent": agent,
            "wire": "dry-run",
            "kind": kind,
            "url": None,
            "status": None,
            "reason": "dry-run",
        }

    url = turn_url_for(agent)
    if not url:
        logger.error(
            "[scitex-todo._push] no turn URL configured for agent=%r (set %s or %s%s)",
            agent,
            ENV_MAP,
            PER_AGENT_PREFIX,
            _slug(agent),
        )
        return {
            "ok": False,
            "agent": agent,
            "wire": "http",
            "kind": kind,
            "url": None,
            "status": None,
            "reason": "no-turn-url-configured",
        }

    # ``text`` is the field the turn-url receiver expects; ``body`` is
    # scitex-todo's historical name. We send BOTH so the wire is
    # back-compat: consumers that key off ``text`` succeed, and any older
    # consumer keying off ``body`` still works. Without this alias a
    # text-keyed receiver returns
    # ``HTTP 400 missing or empty 'text' field`` and the whole nudge chain
    # is dead on arrival (proj-scitex-todo P3a(c) pilot, 2026-06-13 — see
    # lead a2a ``8afe659e``).
    payload = {
        "agent": agent,
        "kind": kind,
        "source": "scitex-todo",
        "text": body,
        "body": body,
        "task_id": task_id,
        "store_path": store_path,
        "ts": _now_iso(),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "scitex-todo/_push (channel)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        logger.error(
            "[scitex-todo._push] HTTP %s from %s for agent=%s: %s",
            e.code,
            url,
            agent,
            e.reason,
        )
        return {
            "ok": False,
            "agent": agent,
            "wire": "http",
            "kind": kind,
            "url": url,
            "status": e.code,
            "reason": "http-error",
        }
    except TimeoutError as e:
        # Receiver's /v1/turn runs the turn synchronously up to ~120 s;
        # we wait `timeout` (default 30 s, env-overridable). If we hit
        # the cap, the request body was already fully sent in the first
        # second or two of that budget — the receiver is mid-turn, NOT
        # unreachable. Background callers treat that as DISPATCHED
        # success so the cron doesn't fail the whole nudge batch over
        # one slow turn (lead a2a `0b59485f`, 2026-06-13). Interactive
        # callers (`dispatched_is_ok=False`) fail loud instead.
        return _timeout_result(agent, kind, url, e, timeout, dispatched_is_ok)
    except (urllib.error.URLError, OSError) as e:
        # Distinguish a wrapped TimeoutError (the urlopen socket layer
        # often raises a TimeoutError nested inside URLError) from a real
        # transport failure (connection refused / DNS / SSL handshake).
        nested = getattr(e, "reason", None)
        if isinstance(nested, TimeoutError) or "timed out" in str(e):
            return _timeout_result(agent, kind, url, e, timeout, dispatched_is_ok)
        logger.error(
            "[scitex-todo._push] transport error to %s for agent=%s: %s",
            url,
            agent,
            e,
        )
        return {
            "ok": False,
            "agent": agent,
            "wire": "http",
            "kind": kind,
            "url": url,
            "status": None,
            "reason": "transport-error",
            "error": str(e),
        }

    return {
        "ok": True,
        "agent": agent,
        "wire": "http",
        "kind": kind,
        "url": url,
        "status": status,
        "reason": "delivered",
    }


def announce_missing_at_boot(tasks: list[dict]) -> list[str]:
    """Log a WARN listing the distinct agents in ``tasks`` that have no
    turn URL configured. Called once at board startup.

    Returns the list of missing agents so callers (tests, the boot
    hook) can assert / surface the gap themselves.

    Enumerates the OWNER SSOT (:func:`scitex_cards._owner.card_owner` =
    ``agent`` falling back to ``assignee``) so an assignee-only card's owner
    is included — the same target the comment relay / nudge resolve, so the
    boot warning never misses a relay target.
    """
    from ._owner import card_owner

    agents = sorted({owner for t in tasks if (owner := card_owner(t))})
    missing = [a for a in agents if turn_url_for(a) is None]
    if missing:
        logger.warning(
            "[scitex-todo._push] %d agent(s) without turn URL — "
            "nudge + comment-relay will return ok=False for them: %s",
            len(missing),
            ", ".join(missing),
        )
    return missing


__all__ = [
    "ENV_MAP",
    "ENV_DRY_RUN",
    "ENV_PUSH_TIMEOUT_S",
    "PER_AGENT_PREFIX",
    "DEFAULT_TIMEOUT_S",
    "NOTIFY_TIMEOUT_S",
    "turn_url_for",
    "deliver",
    "announce_missing_at_boot",
]
