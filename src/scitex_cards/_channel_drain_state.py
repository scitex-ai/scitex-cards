#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtime-gate for the channel inbox drain — the READ-side twin of PR #344.

The channel poll loop (:func:`scitex_cards._mcp_channel._poll_loop`) used to call
:func:`scitex_cards._mcp_channel.drain_once` every ``_DEFAULT_INTERVAL`` (5s)
UNCONDITIONALLY. Each drain calls ``recipient_keys`` + ``_inbox.poll_inbox``,
both of which parse the ENTIRE shared sidecar (the inbox lives in an
``inboxes:`` section of the SAME ~9 MB / ~930-card legacy sidecar as the cards).
A ~9 MB parse every 5s per agent × ~7 channel servers on a host = ~350% sustained
CPU — the read/poll analogue of the wake-watcher every-tick reload spiral that
PR #344 (`_wake_watcher.py`, 0.7.45) cured on the WRITE side.

The cure is identical: the inbox is only ever mutated through a store WRITE
(``_inbox.enqueue`` / ``poll_inbox(mark_seen=True)`` / ``ack``), so a new
notification CANNOT appear without the store file's mtime advancing. Before any
parse, ``os.stat`` the store and compare its mtime to the last processed tick;
when UNCHANGED, SKIP the whole drain — no ``recipient_keys``, no ``poll_inbox``,
no parse. Cost collapses to one ``stat()`` per 5s on a quiescent inbox.

This lives in its own module (not ``_mcp_channel``) purely to keep that file
under its line budget; the two are one logical unit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class _DrainState:
    """Per-loop mtime bookkeeping for the drain short-circuit.

    Deliberately per-loop state (created inside ``_poll_loop``, or by a test)
    — NEVER a module global — so concurrent loops / tests never share it.
    Mirrors :class:`scitex_cards._wake_watcher.WatcherState`'s
    ``seeded`` + ``last_mtime`` pair.
    """

    #: Set True once the first tick has run — the first tick ALWAYS drains
    #: (seeds ``last_mtime``), exactly like the watcher seeds its snapshot.
    seeded: bool = False
    #: Store mtime (float ``st_mtime``) processed on the last drained tick.
    #: ``None`` means "unknown / unstatable"; an unstatable store fails SAFE
    #: (drain), so a resolution glitch never silently drops a notification.
    last_mtime: Optional[float] = None


def _resolve_store_file(store: str | Path | None) -> Optional[Path]:
    """Resolve the ONE file whose mtime tracks new inbox activity, or ``None``.

    Must stat the EXACT file ``_inbox.poll_inbox`` / ``recipient_keys`` will
    read from — that routing lives in ``_inbox._use_sqlite()`` and has two
    outcomes:

    * SQLite (the DEFAULT backend) — no single sidecar file to gate on
      cheaply (WAL mode rewrites the DB file on plain READS too, so its
      mtime is not a reliable "did anything change" signal); reads are
      already indexed/fast, so the gate's original 9 MB-full-parse problem
      doesn't apply here. Returns ``None`` (fail-safe: every tick drains).
    * The break-glass file backend (``SCITEX_TODO_INBOX_BACKEND=yaml``) —
      its own ``inboxes.json`` sidecar (see ``_inbox._inboxes_path``); THIS
      is the file that must be stat'd for the gate to be sound.

    Returns ``None`` when the path cannot be resolved — the caller then fails
    SAFE and drains.
    """
    try:
        from ._inbox import _inboxes_path, _use_sqlite

        if _use_sqlite():
            return None
        return _inboxes_path(store)
    except Exception as exc:  # noqa: BLE001 — unresolvable ⇒ fail-safe drain
        logger.debug("scitex-todo channel: store path unresolved for gate: %s", exc)
        return None


def should_drain(state: _DrainState, *, store: str | Path | None = None) -> bool:
    """Decide whether this tick should actually drain (parse) the store.

    Returns ``True`` — and records the observed mtime — when the drain must run:
    the FIRST tick (seed), a tick whose store mtime ADVANCED, or a tick whose
    store path can't be resolved / stat'd (fail SAFE = drain, so correctness
    never regresses). Returns ``False`` — costing one ``stat()`` and nothing
    else — when the store is UNCHANGED since the last drained tick.

    Mirrors the ``_wake_watcher.run_watcher_once`` short-circuit
    ``if state.seeded and mtime is not None and mtime == state.last_mtime``.

    ack-write interaction: a drain that pushes+acks WRITES the store (to flip
    records ``seen``), advancing the mtime past the value recorded here. The
    NEXT tick therefore sees a changed mtime and drains once more, finds nothing
    new, and records the post-ack mtime; the tick after THAT sees it unchanged
    and skips. Net: exactly one extra parse after real activity, then a truly
    quiescent inbox (no new records, no acks) idles at one ``stat()`` per tick.
    """
    path = _resolve_store_file(store)
    try:
        mtime = path.stat().st_mtime if path is not None else None
    except OSError:
        mtime = None
    # Unchanged store since the last processed tick → skip before any parse.
    if state.seeded and mtime is not None and mtime == state.last_mtime:
        return False
    # First tick, an advanced mtime, or an unresolvable/unstatable path
    # (mtime is None ⇒ fail-safe drain). Record what we saw and drain.
    state.seeded = True
    state.last_mtime = mtime
    return True


async def gated_drain_once(
    agent_id: str,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    state: _DrainState,
    *,
    source: str,
    store: str | Path | None = None,
) -> int:
    """mtime-gate then drain: skip the parse when the store is UNCHANGED.

    The single entry point the poll loop uses per tick. When
    :func:`should_drain` says the store is unchanged this returns ``0``
    WITHOUT importing/calling ``drain_once`` — so ``recipient_keys`` and
    ``poll_inbox`` (the full-store parsers) are never touched on an idle tick.
    Otherwise it delegates to :func:`scitex_cards._mcp_channel.drain_once`,
    preserving ALL its behavior (recipient-key fan-out, unseen read,
    ack-after-push, ``MAX_PUSH_PER_DRAIN`` burst cap, fail-soft).
    """
    if not should_drain(state, store=store):
        return 0
    # Lazy import breaks the _mcp_channel ↔ _channel_drain_state cycle.
    from ._mcp_channel import drain_once

    return await drain_once(agent_id, send, source=source, store=store)


__all__ = ["_DrainState", "gated_drain_once", "should_drain"]

# EOF
