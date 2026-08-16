#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry-point plugin discovery + bounded dispatch for the hook bus.

Split out of the original flat ``_hooks.py`` (C2 refactor). Holds the
entry-point group constant, the per-plugin wall-time budget + env knob,
the ordered/bounded :func:`_run_plugins`, and the cross-version
:func:`_iter_entry_points`.

## C2 — per-plugin wall-time budget (the headline fix)

The original ``_run_plugins`` called ``fn(event)`` with NO time budget,
so a slow/hung external entry-point plugin blocked the WHOLE producer
(the root cause behind the earlier comment-post slowness). C2 runs each
handler in a worker thread and ``join(timeout)``s it so a hung plugin
can NEVER hang dispatch.

What is PRESERVED (other code depends on it):

  * Sequential, ordered execution by ``(priority, name)``. The
    ``ci-result`` / owner-map chain relies on an early ``critical``
    handler MUTATING the shared ``event`` dict BEFORE a later handler
    reads it, so handlers still run ONE AT A TIME, in order — never
    parallelised. The handler mutates the shared dict IN its thread;
    after a successful ``join`` the main thread sees the mutations
    (fine under the GIL — the join is a happens-before edge).
  * On RAISE inside the thread: caught into ``plugin_errors``; if the
    handler is ``critical`` the chain ABORTS and the exception
    re-raises (byte-for-byte the original contract).
  * The ``(count, plugin_errors)`` return contract; ``count`` still
    counts loaded handlers.

What is NEW:

  * On TIMEOUT: a ``plugin_errors`` entry shaped like the existing ones
    plus ``"timeout": true``; a warning is logged; if the timed-out
    handler is ``critical`` the chain ABORTS and raises (delivering
    downstream off a handler that did not finish is worse than
    aborting).

### Daemon-thread limitation (documented, intentional)

Python cannot force-kill a thread. The worker thread is therefore a
DAEMON: when it times out we stop WAITING for it, but the hung plugin
keeps running in the background until it returns on its own or the
process exits (a daemon thread does not block interpreter shutdown).
The leak is bounded — it dies with the process — and is strictly
better than the alternative (hanging the producer forever). The env
knob lets an operator widen the budget if a legitimately-slow plugin
is being clipped.
"""

from __future__ import annotations

import functools
import importlib.metadata
import logging
import os
import threading
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


#: Entry-point group external producers register their plugins under.
ENTRY_POINT_GROUP = "scitex_cards.hooks"

#: Default per-plugin wall-time budget (seconds). Each entry-point
#: handler runs in a worker thread joined with this timeout, so a
#: slow/hung plugin can NEVER hang the producer/request that drove
#: dispatch. Override per-process via :data:`PLUGIN_TIMEOUT_ENV`. A
#: value <= 0 DISABLES bounding (runs the handler inline, legacy
#: behaviour) — useful for in-process tests that don't want the thread.
PLUGIN_TIMEOUT_S = 5.0

#: Env override for :data:`PLUGIN_TIMEOUT_S`. Parsed as a float; a value
#: <= 0 disables the wall-time budget (inline/legacy execution).
PLUGIN_TIMEOUT_ENV = "SCITEX_CARDS_HOOK_PLUGIN_TIMEOUT_S"


def _plugin_timeout_s() -> float:
    """Resolve the per-plugin wall-time budget (seconds).

    Reads :data:`PLUGIN_TIMEOUT_ENV` at call time (so a test can flip it
    via the ``env`` fixture without reimporting the module); falls back
    to :data:`PLUGIN_TIMEOUT_S`. A malformed env value is ignored
    (logged) and the default is used. A value <= 0 disables bounding.
    """
    raw = os.environ.get(PLUGIN_TIMEOUT_ENV)
    if raw is None or raw == "":
        return PLUGIN_TIMEOUT_S
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "scitex_cards._hooks: ignoring malformed %s=%r; using default %.1fs",
            PLUGIN_TIMEOUT_ENV,
            raw,
            PLUGIN_TIMEOUT_S,
        )
        return PLUGIN_TIMEOUT_S


def _run_plugins(
    event: dict, *, entry_points: Iterable | None = None
) -> tuple[int, list[dict]]:
    """Discover + invoke every plugin registered under
    :data:`ENTRY_POINT_GROUP`. Failures are caught + logged.

    ``entry_points`` overrides discovery with an explicit iterable of
    entry-point-shaped objects (``.name`` + ``.load()``); ``None`` reads
    the real group via :func:`_iter_entry_points`. See
    :func:`scitex_cards._hooks.dispatch_event` for the rationale
    (in-process injection seam, PA-306-compliant).

    Each handler runs under a per-plugin wall-time budget
    (:func:`_plugin_timeout_s`); see the module docstring for the
    threading model + the daemon-thread limitation.
    """
    plugin_errors: list[dict] = []
    eps = _iter_entry_points() if entry_points is None else entry_points
    # Materialize the entry-point list FIRST so we can sort by the
    # handler's declared (priority, name) before dispatch. Lead a2a
    # `0ab1d9fd` + dev coordination 2026-06-14 — the ci-result event
    # chain needs dev's owner-map handler (priority=10) to mutate
    # event["owner"] BEFORE SAC's delivery handler (priority=200)
    # reads it.
    #
    # Handlers declare ordering via two OPTIONAL function attributes:
    #
    #   on_event.priority = <int>     # default 100; lower = runs earlier
    #   on_event.critical = True      # default False; if True and the
    #                                 # handler raises (OR times out),
    #                                 # ABORT the chain and re-raise (the
    #                                 # producer's HTTP/CLI wrapper
    #                                 # translates to 500 / non-zero
    #                                 # exit). For ci-result the owner-map
    #                                 # MUST be critical — delivering a
    #                                 # verdict to a wrong-or-no agent is
    #                                 # worse than no delivery.
    #
    # Tie-break is the entry-point name (lex asc) so the order is
    # stable across packaging-metadata implementations.
    handlers: list[tuple[int, str, Callable[[dict], None]]] = []
    for ep in eps:
        name = ep.name
        try:
            fn: Callable[[dict], None] = ep.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scitex_cards.hooks plugin %r failed to load: %s",
                name,
                exc,
            )
            plugin_errors.append({"plugin": name, "error": f"load: {exc}"})
            continue
        prio = int(getattr(fn, "priority", 100))
        handlers.append((prio, name, fn))
    handlers.sort(key=lambda triple: (triple[0], triple[1]))
    count = len(handlers)
    timeout = _plugin_timeout_s()
    for prio, name, fn in handlers:
        critical = bool(getattr(fn, "critical", False))
        if timeout <= 0:
            # Bounding disabled — run inline (legacy behaviour). Keeps
            # the in-process/test path free of the threading wrapper.
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001
                _record_raise(plugin_errors, name, prio, critical, exc)
                if critical:
                    raise
            continue

        # Bounded path: run the handler in a worker thread and join with
        # the wall-time budget. The handler mutates the SHARED `event`
        # dict in-thread; a successful join is a happens-before edge so
        # the main thread (and the NEXT ordered handler) sees the
        # mutation — this preserves the ci-result owner-map → delivery
        # contract. Handlers still run STRICTLY one-at-a-time, in order.
        raised: list[BaseException] = []

        def _worker(_fn: Callable[[dict], None] = fn) -> None:
            try:
                _fn(event)
            except BaseException as exc:  # noqa: BLE001 — relay to main
                raised.append(exc)

        # Daemon: a hung plugin thread CANNOT be force-killed in Python
        # (see module docstring). We stop waiting on timeout; the leaked
        # daemon keeps running until it returns or the process exits, and
        # does not block interpreter shutdown.
        worker = threading.Thread(
            target=_worker,
            name=f"scitex-cards-hook-{name}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            # TIMEOUT — the handler did not finish within the budget.
            logger.warning(
                "scitex_cards.hooks plugin %r (priority=%d critical=%s) "
                "exceeded the %.3fs wall-time budget; abandoning the worker "
                "thread (it keeps running as a daemon until it returns or the "
                "process exits)",
                name,
                prio,
                critical,
                timeout,
            )
            plugin_errors.append(
                {
                    "plugin": name,
                    "priority": prio,
                    "critical": critical,
                    "error": f"timeout after {timeout:.3f}s",
                    "timeout": True,
                }
            )
            if critical:
                # A critical handler that didn't finish must abort the
                # chain — same contract as a critical RAISE. Delivering
                # downstream off a handler that didn't finish is worse
                # than aborting.
                raise HookPluginTimeoutError(
                    f"critical hook plugin {name!r} timed out after "
                    f"{timeout:.3f}s; aborting dispatch chain"
                )
            continue

        # Worker finished within the budget; surface any exception it
        # raised exactly as the inline path would.
        if raised:
            exc = raised[0]
            _record_raise(plugin_errors, name, prio, critical, exc)
            if critical:
                raise exc
    return count, plugin_errors


class HookPluginTimeoutError(RuntimeError):
    """A ``critical`` hook plugin exceeded its wall-time budget.

    Raised by :func:`_run_plugins` to abort the chain — the same
    contract as a ``critical`` handler RAISING, but for a handler that
    never finished. The producer's HTTP/CLI wrapper translates it the
    same way (500 / non-zero exit).
    """


def _record_raise(
    plugin_errors: list[dict],
    name: str,
    prio: int,
    critical: bool,
    exc: BaseException,
) -> None:
    """Append a (non-timeout) raise to ``plugin_errors`` + log it.

    Shapes the entry exactly like the original inline path so callers
    + HTTP/CLI responses that read ``plugin_errors`` are unchanged.
    """
    logger.warning(
        "scitex_cards.hooks plugin %r (priority=%d critical=%s) raised: %s",
        name,
        prio,
        critical,
        exc,
    )
    plugin_errors.append(
        {
            "plugin": name,
            "priority": prio,
            "critical": critical,
            "error": str(exc),
        }
    )


@functools.cache
def _iter_entry_points() -> Iterable:
    """Entry points for this group, discovered ONCE per process.

    ``importlib.metadata.entry_points()`` re-reads every installed package's
    ``entry_points.txt`` — ~126 files in a real fleet venv — on each call. This
    runs on EVERY card event via :func:`dispatch_event`, so uncached it was the
    single largest cost in a card write: sac profiled 2.18 s of a 3.24 s warm
    ``add_task`` HERE (card cards-reassign-all-bulk-primitive, 2026-07-14). It is
    FIXED overhead — paid in full on every write regardless of store size — so
    the cache makes every card write in the fleet ~2.2 s faster.

    Caching is correct: installed-package entry points do not change during a
    process's lifetime (installing a plugin requires a restart to load it
    anyway). A test or a live plugin reload that genuinely needs rediscovery
    calls ``_iter_entry_points.cache_clear()`` — which ``functools.cache``
    provides for free. Note ``dispatch_event`` also takes an explicit
    ``entry_points=`` override, so tests inject rather than depend on discovery.
    """
    try:
        eps = importlib.metadata.entry_points()
    except Exception:  # noqa: BLE001 — packaging surprises
        return []
    # 3.10+: eps is an EntryPoints, supports .select(group=)
    select = getattr(eps, "select", None)
    if callable(select):
        return list(select(group=ENTRY_POINT_GROUP))
    # 3.9 fallback: dict-like keyed by group.
    return list(eps.get(ENTRY_POINT_GROUP, []))


# EOF
