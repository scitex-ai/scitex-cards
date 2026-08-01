#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo's OWN standalone channel-notification MCP server.

A long-running MCP **stdio** server that pushes unsolicited
``notifications/claude/channel`` messages into the Claude session, draining
THIS agent's scitex-todo inbox (:mod:`scitex_cards._inbox`). Claude renders
each push as ``<- scitex-todo`` in the agent's terminal — driven by
``meta.source = "scitex-todo"``.

Why a hand-rolled low-level server (NOT FastMCP)
------------------------------------------------
FastMCP constructs its ``ServerSession`` internally and never exposes it, so
a side channel (the inbox poll loop) would have no session handle to push
server-initiated ``notifications/claude/channel`` through. We therefore use
the LOW-LEVEL :class:`mcp.server.lowlevel.Server` + own the ``ServerSession``
ourselves so the poll loop can push. The initialization options MUST declare
the ``claude/channel`` experimental capability or Claude Code drops every
push ("server did not declare claude/channel capability").

The shape of this server (own-the-session + manual incoming-message drive)
is a standard MCP-channel pattern, but this module has ZERO external
runtime dependency: it drains the standalone :mod:`scitex_cards._inbox`
pull-inbox — reads scitex-todo's own inbox rows; no external runtime
import or shell-out. scitex-todo's delivery rail is fully self-contained.

Wire format (exact)
-------------------
JSON-RPC notification, method ``notifications/claude/channel``, params
``{"content": <str>, "meta": {<all-string-values>}}``. EVERY meta value MUST
be a string or Claude's Zod validator silently drops the pushed turn.

Size / burst guards (see :mod:`scitex_cards._channel_guard`). The SDK reads these
pushes through a stdio JSON reader with a hard 1 MB per-message buffer; on
2026-07-02, 180 solver containers died on boot with ``JSON message exceeded
maximum buffer size of 1048576 bytes`` when an oversized push overflowed it. Two
guards prevent that: :func:`build_channel_params` caps the body at
``MAX_CONTENT_BYTES`` (256 KiB) with a "see the card on the board" pointer, and
:func:`drain_once` pushes at most ``MAX_PUSH_PER_DRAIN`` (50) records per tick so
a backlog can never burst all at once on first connect.

Headless / solver capsules (no push): with NO ``$SCITEX_TODO_AGENT_ID`` set the
unified server (``scitex-todo mcp start``) runs TOOLS-ONLY — the poll loop is not
started and the session receives ZERO channel pushes (see
:func:`resolve_agent_id_optional`). Intended mode for solver / headless capsules
that must not receive unsolicited pushes: just do not export the id for them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from . import _inbox
from ._channel_drain_state import _DrainState, gated_drain_once
from ._channel_guard import (
    MAX_PUSH_PER_DRAIN,
    _bounded_content,
    _bounded_meta_value,
    _dm_wire_meta,
)

# Agent-identity resolution lives in a sibling module (keeps THIS orchestrator
# under its line budget); re-exported below so
# ``from scitex_cards._mcp_channel import resolve_agent_id`` keeps working.
from ._channel_identity import resolve_agent_id, resolve_agent_id_optional
from ._channel_log_sink import install_channel_log_sink

# Loop self-measurement, extracted so this module keeps ONE responsibility (the
# channel server) and the timing invariant lives beside the numbers it guards.
from ._channel_tick_timing import TickTimer, format_inconsistency, format_spans

# The cursor advance is a RECEIPT now, not a claim of delivery — see
# `_inbox_receipt` for what the MCP transport can and cannot tell us.
from ._inbox_receipt import record_push

logger = logging.getLogger(__name__)

#: Env var overriding ``meta.source`` (the ``<- stodo`` render name)
#: when ``--name`` is not passed explicitly. Precedence: CLI > env > default.
_ENV_SOURCE = "SCITEX_TODO_CHANNEL_SOURCE"

#: Env var overriding the poll interval (seconds) when ``--interval`` is not
#: passed explicitly. Precedence: CLI > env > default.
_ENV_INTERVAL = "SCITEX_TODO_CHANNEL_INTERVAL"

#: Default poll interval (seconds) between inbox drains.
_DEFAULT_INTERVAL = 5.0

#: Default ``meta.source`` — drives the channel render name. Per the fleet
#: naming agreement (2026-07-07) source labels are SHORT sender-identity names
#: (sac / cct / stodo). Kept DISTINCT from the ``scitex-todo`` agent id — a
#: system push renders ``<- stodo`` (carries sender- AND task-identity).
_DEFAULT_SOURCE = "stodo"


# --------------------------------------------------------------------------- #
# Pure logic (tested directly — no live MCP session needed)                   #
# --------------------------------------------------------------------------- #
def build_channel_params(
    rec: dict[str, Any], *, source: str = _DEFAULT_SOURCE
) -> dict[str, Any]:
    """Project an inbox record onto the Claude channel notification shape.

    Returns ``{"content": <body str>, "meta": {<all-string-values>}}``. EVERY
    meta value is stringified — a non-string trips the client's Zod validator,
    silently dropping the pushed turn. ``meta.source`` drives the render label.

    Size-guarded: ``content`` capped at ``MAX_CONTENT_BYTES`` (256 KiB,
    UTF-8-boundary truncation + "see the card" pointer) so a push can never
    overflow the SDK's 1 MB stdio reader; each meta value is clamped too.
    DM records are lifted onto the a2a wire shape by :func:`_dm_wire_meta`.
    """
    card_id = str(rec.get("card_id") or "")
    meta = {
        "source": _bounded_meta_value(source),
        "ts": _bounded_meta_value(rec.get("ts") or ""),
        "event_type": _bounded_meta_value(rec.get("event_type") or ""),
        "card_id": _bounded_meta_value(card_id),
        "actor": _bounded_meta_value(rec.get("actor") or ""),
        "msg_id": _bounded_meta_value(rec.get("id") or ""),
    }
    return {
        "content": _bounded_content(rec.get("body"), card_id),
        "meta": _dm_wire_meta(rec, meta),
    }


def recipient_keys(agent_id: str, *, store: str | None = None) -> list[str]:
    """Inbox keys to drain for ``agent_id`` — MUST match the producer's keys.

    The notify dispatcher enqueues to ``_resolve_name_to_id(name, store)``:
    a REGISTERED name resolves to its stable user-id, an unregistered one
    stays the raw name. If the channel polled only the raw name it would miss
    every notification for an agent that IS a registered user (enqueued under
    the user-id) — the silent-drop we hit live. So the channel computes the
    SAME key the producer used (the consumer keys exactly like the
    producer) AND keeps the raw name for back-compat records keyed by name.
    Returns a de-duplicated, order-stable list (raw name first).
    """
    keys = [agent_id]
    try:
        from ._notify._resolver import _resolve_name_to_id

        resolved = _resolve_name_to_id(agent_id, store=store)
        if resolved and resolved not in keys:
            keys.append(resolved)
    except Exception as exc:  # noqa: BLE001 — resolution must never break the drain
        logger.warning(
            "scitex-todo channel: recipient-key resolution for %r failed: %s",
            agent_id,
            exc,
        )
    return keys


async def drain_once(
    agent_id: str,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    source: str = _DEFAULT_SOURCE,
    store: str | None = None,
) -> int:
    """Drain one batch of unseen notifications, pushing each via ``send``.

    The seam that makes the receive→push path testable without a live MCP
    session. Drains EVERY key in :func:`recipient_keys` (the raw agent name
    AND its resolved user-id) so it always finds what the producer enqueued.
    Reads UNSEEN records (``mark_seen=False`` — we record ONLY after a
    successful push so a push failure is retried next drain), builds the channel
    params, awaits ``send(params)``, and on success writes a PUSH RECEIPT on the
    SAME key via :func:`scitex_cards._inbox_receipt.record_push`.

    THE RECEIPT IS THE HONEST PART. ``await send(params)`` returning proves only
    that our own stdout writer took the bytes: the push is a JSON-RPC
    NOTIFICATION, which by spec has no reply, and Claude Code silently DISCARDS
    a push from a server missing from its launch-line allowlist. This drain used
    to ack there and call it delivered — which destroyed weeks of operator DMs
    with every health check green (2026-07-29). It now advances the cursor and
    stamps ``pushed_at`` in ONE atomic write, leaving ``confirmed_at`` for the
    recipient's own ``ack_notifications``, so an undelivered notification stays
    VISIBLE to the ``delivery_confirmed`` health check instead of vanishing.

    Fail-soft per record: one bad push (``send`` raises) leaves THAT record
    un-ack'd (retried next drain) and does not abort the rest of the batch. A
    receipt write that fails moves NEITHER the stamp nor the cursor, so that
    record is likewise retried — the same bound the old ack-failure path had.

    Burst-guarded: at most ``MAX_PUSH_PER_DRAIN`` records are pushed per call,
    across ALL recipient keys combined; the rest stay unseen and drain on the
    next tick — a huge backlog can never flood the session on first connect.

    Parameters
    ----------
    agent_id : str
        The agent identity; expanded to its producer-matching inbox keys.
    send : Callable[[dict], Awaitable[None]]
        Async callable that delivers one channel-params payload (the real
        server passes a closure over the MCP session's ``send_message``).
    source : str
        ``meta.source`` value (default ``"stodo"``).
    store : str | None
        Store path override (default: the resolved task store).

    Returns
    -------
    int
        The number of records successfully pushed AND ack'd this drain.

    Notes
    -----
    Every store touch (:func:`recipient_keys`, :func:`_inbox.poll_inbox`,
    :func:`_inbox.ack`) is SYNCHRONOUS blocking IO (it locks + parses the whole
    YAML store). Running it inline on the event loop starves the MCP session —
    the first drain would block the ``initialize`` handshake and Claude Code
    marks the server "not connected" (grew with inbox size). So every blocking
    store call is off-loaded to a worker thread via ``anyio.to_thread.run_sync``;
    only the ``await send(...)`` push runs on the loop (it needs the session).
    """
    from functools import partial

    import anyio

    pushed = 0
    keys = await anyio.to_thread.run_sync(
        partial(recipient_keys, agent_id, store=store)
    )
    for key in keys:
        if pushed >= MAX_PUSH_PER_DRAIN:
            break  # burst cap reached — remaining keys drain next tick
        records = await anyio.to_thread.run_sync(
            partial(
                _inbox.poll_inbox, key, unseen_only=True, mark_seen=False, store=store
            )
        )
        for rec in records:
            if pushed >= MAX_PUSH_PER_DRAIN:
                break  # burst cap reached — rest stay unseen for the next tick
            params = build_channel_params(rec, source=source)
            try:
                await send(params)
            except Exception as exc:  # noqa: BLE001 — one bad push must not kill the loop
                logger.warning(
                    "scitex-todo channel: pushing notification %s failed: %s",
                    rec.get("id"),
                    exc,
                )
                continue
            # Record the push ONLY after a successful send — a push failure
            # stays unseen and is retried on the next drain. Recorded on the
            # SAME key it came from.
            #
            # `record_push` is the old `_inbox.ack` plus the truth: it advances
            # the cursor AND stamps `pushed_at` in one atomic UPDATE, so the row
            # says "handed to the transport, confirmed by nobody" instead of
            # silently claiming delivery. Confirmation is a separate stamp only
            # the RECIPIENT can write (`ack_notifications`), because a JSON-RPC
            # notification has no reply for us to wait on.
            rec_id = rec.get("id")
            if rec_id:
                try:
                    await anyio.to_thread.run_sync(
                        partial(record_push, key, [rec_id], store=store)
                    )
                except Exception as exc:  # noqa: BLE001 — a receipt failure shouldn't kill the loop
                    logger.warning(
                        "scitex-todo channel: recording the push of %s failed — "
                        "the cursor did not move either, so it is retried next "
                        "drain: %s",
                        rec_id,
                        exc,
                    )
            pushed += 1
    return pushed


# --------------------------------------------------------------------------- #
# Live MCP stdio server (own the session so we can push)                      #
# --------------------------------------------------------------------------- #
async def _poll_loop(
    agent_id: str,
    send: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    interval: float,
    source: str,
) -> None:
    """Background task: drain the inbox every ``interval`` seconds, forever.

    mtime-GATED (see :mod:`scitex_cards._channel_drain_state`): each tick first
    ``stat``s the shared store and drains ONLY when its mtime advanced since the
    last drained tick. A quiescent inbox therefore costs one ``stat()`` per
    ``interval`` — NOT a full ~9 MB YAML re-parse — which was the per-agent
    ~50% CPU drain (× ~7 servers ≈ ~350% fleet load) this fixes. The gate is
    the read-side twin of PR #344's wake-watcher short-circuit. The first tick
    always drains (seeds the mtime); an unresolvable store path fails SAFE
    (drains) so correctness never regresses. ``_DrainState`` is per-loop (not a
    module global) so multiple loops / tests never share mtime bookkeeping.

    Fail-soft: a drain that raises is logged and retried next tick — the loop
    is long-lived and must survive transient store/IO errors.
    """
    state = _DrainState()
    # TICK TIMING, because the outside view could not separate the causes.
    #
    # Measured 2026-08-02: DMs reach an agent 13-25s after they are written,
    # against a 5s interval. SEVEN candidates were eliminated from outside —
    # notifyd (wrong path), the mtime gate (fails safe, always drains), the
    # burst cap, PostgreSQL write latency (0.4-0.6s), drain work (poll_inbox
    # 0.02s), an overridden interval (the running code reads 5.0), and MCP
    # transport backpressure (an idle session was SLOWER, 20s vs 13s). Every
    # component measured fast and the composite stayed slow, which is precisely
    # the shape that outside observation cannot resolve.
    #
    # So record the three spans the loop actually controls. `drain_s` is the
    # work, `gap_s` is wall time since the previous tick STARTED — so
    # `gap_s - drain_s - interval` is the time the loop spent neither working
    # nor sleeping, which is the quantity none of the seven probes could see.
    timer = TickTimer(interval)
    while True:
        timer.start_tick()
        try:
            await gated_drain_once(agent_id, send, state, source=source)
        except Exception as exc:  # noqa: BLE001 — keep the long-lived loop alive
            logger.warning("scitex-todo channel: drain tick failed: %s", exc)
        spans = timer.end_tick()
        # REPORTED, NEVER RAISED — see _channel_tick_timing's docstring. A bare
        # assert here raises OUTSIDE the try above and kills this long-lived
        # task, stopping delivery outright.
        if spans.is_inconsistent:
            logger.warning("scitex-todo channel: %s", format_inconsistency(spans))
        # DEBUG, not INFO: this fires every `interval` on every agent, so at
        # INFO it would be ~17k lines a day per session for a diagnostic that
        # is only wanted while something is wrong.
        logger.debug("scitex-todo channel: %s", format_spans(spans))
        await asyncio.sleep(interval)


async def _serve(
    read_stream: Any,
    write_stream: Any,
    *,
    agent_id: str | None,
    source: str,
    interval: float,
    server: Any | None = None,
) -> None:
    """Drive the MCP session AND (when an agent id is known) the inbox poll loop.

    We deliberately do NOT call ``Server.run``: it constructs its
    ``ServerSession`` internally and never exposes it, so the poll loop would
    have no session handle to push ``notifications/claude/channel`` through.
    Owning the session here is the supported way to send server-initiated
    notifications with the low-level API.

    ``server`` lets a caller pass an EXISTING low-level server that already has
    tool handlers registered (e.g. FastMCP's ``mcp._mcp_server``) so ONE server
    serves tools AND pushes the digest — the unified ``scitex-todo mcp start``.
    When omitted, a bare push-only server is created (the standalone
    ``mcp channel``). ``agent_id`` may be ``None`` (tools-only, no push) so the
    tools surface still works when no identity is configured.

    The transport pair is wrapped by
    :func:`scitex_cards._mcp_handshake_log.instrument_handshake` before the
    session sees it, so WHEN ``initialize`` arrived and WHEN it was answered land
    in an append-only sink that survives a restart. The wrap must sit here, at
    the transport, because ``ServerSession`` answers ``initialize`` internally
    and never yields it to the message loop below — and because a request that is
    received and never answered has to be recorded on arrival to be recorded at
    all. Disabled or unwritable, it hands the original streams straight back.
    """
    from contextlib import AsyncExitStack

    import anyio
    from mcp.server.lowlevel import Server

    # Attach the log sink FIRST, before anything worth logging happens. Both
    # entry points (standalone ``mcp channel`` and unified ``mcp start``) reach
    # the server through here, so this is the one place that covers both.
    # No-op unless $SCITEX_CARDS_CHANNEL_LOG is set; raises if it is set and
    # unwritable, because a server that silently discards its own diagnostics
    # is what made the 0.31.5 tick instrument unreadable in production.
    sink = install_channel_log_sink()
    if sink is not None:
        logger.info("scitex-todo channel: logging to %s", sink)
    from mcp.server.session import ServerSession
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage, JSONRPCNotification

    from ._mcp_handshake_log import instrument_handshake

    if server is None:
        server = Server(name=f"scitex-todo-channel-{agent_id}")

    read_stream, write_stream, handshake_log = instrument_handshake(
        read_stream,
        write_stream,
        extra={"agent_id": agent_id, "push": bool(agent_id)},
    )

    async with AsyncExitStack() as stack:
        lifespan_context = await stack.enter_async_context(server.lifespan(server))
        session = await stack.enter_async_context(
            ServerSession(
                read_stream,
                write_stream,
                # Declare the `claude/channel` experimental capability in the
                # initialize response — without it Claude Code logs "Channel
                # notifications skipped: server did not declare claude/channel
                # capability" and drops every push.
                server.create_initialization_options(
                    experimental_capabilities={"claude/channel": {}},
                ),
            )
        )

        async def _send(params: dict[str, Any]) -> None:
            await session.send_message(
                SessionMessage(
                    JSONRPCMessage(
                        JSONRPCNotification(
                            jsonrpc="2.0",
                            method="notifications/claude/channel",
                            params=params,
                        )
                    )
                )
            )

        # Only run the drain→push loop when we know whose inbox to drain. With
        # no agent id we still serve tools (the loop is simply not started).
        poll_task: asyncio.Task[None] | None = None
        if agent_id:
            poll_task = asyncio.create_task(
                _poll_loop(agent_id, _send, interval=interval, source=source)
            )

        try:
            async with anyio.create_task_group() as tg:
                async for message in session.incoming_messages:
                    tg.start_soon(
                        server._handle_message,
                        message,
                        session,
                        lifespan_context,
                        False,
                    )
        finally:
            if poll_task is not None:
                poll_task.cancel()
            # An orphan `initialize_received` with a `server_exit` after it says
            # the process died mid-handshake; an orphan with NOTHING after it
            # says it is still hanging. Distinguishing those two is why the exit
            # is recorded at all.
            handshake_log.record("server_exit")
            handshake_log.close()


async def _run(
    *,
    agent_id: str | None,
    source: str,
    interval: float,
    server: Any | None = None,
) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await _serve(
            read_stream,
            write_stream,
            agent_id=agent_id,
            source=source,
            interval=interval,
            server=server,
        )


def _resolve_source(name: str | None) -> str:
    """Resolve ``meta.source``: explicit ``name`` → ``$SCITEX_TODO_CHANNEL_SOURCE``
    → the built-in default. Fully env-configurable so the ``.mcp.json`` entry
    needs zero config args."""
    if name is not None:
        return name
    return os.environ.get(_ENV_SOURCE) or _DEFAULT_SOURCE


def _resolve_interval(interval: float | None) -> float:
    """Resolve the poll interval (seconds): explicit ``interval`` →
    ``$SCITEX_TODO_CHANNEL_INTERVAL`` → the built-in default. A malformed env
    value falls back to the default rather than crashing the server."""
    if interval is not None:
        return float(interval)
    env_val = os.environ.get(_ENV_INTERVAL)
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            logger.warning(
                "%s=%r is not a number; using default %s",
                _ENV_INTERVAL,
                env_val,
                _DEFAULT_INTERVAL,
            )
    return _DEFAULT_INTERVAL


def main(
    name: str | None = None,
    interval: float | None = None,
    agent: str | None = None,
) -> None:
    """CLI entry point — run the channel server in the foreground (stdio).

    All three params are optional overrides; each falls back to an env var
    then a built-in default so the ``.mcp.json`` entry can carry zero config
    args (``args: ["mcp", "channel"]``). Precedence for every param is
    explicit-value > env var > default:

    * ``name`` sets ``meta.source`` — env ``$SCITEX_TODO_CHANNEL_SOURCE``,
      default ``"scitex-todo"``.
    * ``interval`` is the poll seconds — env ``$SCITEX_TODO_CHANNEL_INTERVAL``,
      default ``5.0``.
    * ``agent`` overrides the agent id; otherwise resolved from
      ``$SCITEX_TODO_AGENT_ID`` (fail-loud when unresolved).
    """
    agent_id = resolve_agent_id(agent)
    source = _resolve_source(name)
    poll_interval = _resolve_interval(interval)
    asyncio.run(_run(agent_id=agent_id, source=source, interval=poll_interval))


__all__ = [
    "build_channel_params",
    "drain_once",
    "main",
    "recipient_keys",
    "resolve_agent_id",
    "resolve_agent_id_optional",
]

# EOF
