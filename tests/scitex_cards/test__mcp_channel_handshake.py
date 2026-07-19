#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression guard: the channel poll loop must NOT starve the MCP handshake.

Bug (2026-07-04, fleet-wide "scitex-todo MCP not connected"): the unified
``scitex-todo mcp start`` server starts an inbox poll loop; its first
:func:`drain_once` ran SYNCHRONOUS blocking store IO (``recipient_keys`` +
``_inbox.poll_inbox``) inline on the event loop. While that ran the
``ServerSession`` could not answer the client's ``initialize`` request, so
Claude Code timed out and marked the server "not connected". It grew with
inbox size (surfaced once an inbox reached ~600 entries).

The fix off-loads every blocking store call to a worker thread
(``anyio.to_thread.run_sync``) so the loop stays free for the handshake. These
tests pin BOTH the invariant (drain yields before touching the store) and the
end-to-end behaviour (a real MCP client completes ``initialize`` while the poll
loop is active). Real store + real inbox + a real in-memory MCP client — no
mocks (STX-NM / PA-306). The repo has no pytest-asyncio, so the async bodies
run under ``asyncio.run`` like the sibling channel tests.
"""

from __future__ import annotations

import asyncio

import pytest

from scitex_cards import _inbox
from scitex_cards._mcp_channel import _serve, drain_once


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _seed(store, agent, n):
    for i in range(n):
        _inbox.enqueue(
            agent,
            event_type="test",
            card_id=f"c{i}",
            body=f"note {i}",
            actor="tester",
            store=str(store),
        )


def _run_drain_with_canary(store, agent):
    """Drain with a canary task racing it; return (pushed, canary-seen flags).

    We schedule a canary task and record whether it has been given a turn by the
    time each ``send`` fires. With the store IO off-loaded to a thread, the
    initial ``await`` yields and the canary runs before any send. If the reads
    were done inline again (the bug) the canary would not have run yet.
    """

    async def body():
        canary_ran = {"v": False}

        async def canary():
            canary_ran["v"] = True

        first_send_saw_canary: list[bool] = []

        async def send(params):
            first_send_saw_canary.append(canary_ran["v"])

        task = asyncio.ensure_future(canary())
        pushed = await drain_once(agent, send, store=str(store))
        await task
        return pushed, first_send_saw_canary

    return asyncio.run(body())


def _initialize_under_poll_loop(agent):
    """Complete ``initialize`` with a real in-memory MCP client while ``_serve``
    hammers its poll loop; return the initialize result."""
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    async def body():
        captured = {}
        async with create_client_server_memory_streams() as (
            client_streams,
            server_streams,
        ):
            c_read, c_write = client_streams
            s_read, s_write = server_streams

            async with anyio.create_task_group() as tg:

                async def run_server():
                    await _serve(
                        s_read,
                        s_write,
                        agent_id=agent,
                        source="stodo",
                        interval=0.05,  # hammer the poll loop to maximise contention
                        server=None,  # bare low-level server: exercises the handshake
                    )

                tg.start_soon(run_server)

                async with ClientSession(c_read, c_write) as session:
                    captured["result"] = await asyncio.wait_for(
                        session.initialize(), timeout=5.0
                    )

                tg.cancel_scope.cancel()
        return captured["result"]

    return asyncio.run(body())


@pytest.fixture()
def drain_with_canary(tmp_path):
    """Five seeded notifications drained with a canary task racing the loop."""
    store = _store(tmp_path)
    agent = "canary-agent"
    _seed(store, agent, 5)
    pushed, saw = _run_drain_with_canary(store, agent)
    return {"pushed": pushed, "saw": saw}


@pytest.fixture()
def initialize_result(tmp_path, monkeypatch):
    """``initialize`` completed against a server whose poll loop is running.

    Pre-fix this would hang: the poll loop's inline store reads blocked the loop
    so the initialize response was never sent.
    """
    store = _store(tmp_path)
    agent = "handshake-agent"
    # A non-trivial inbox so the drain has real work to do each tick.
    _seed(store, agent, 50)
    # The poll loop resolves the store from the environment.
    monkeypatch.setenv("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", agent)
    return _initialize_under_poll_loop(agent)


# --------------------------------------------------------------------------- #
# Invariant: drain_once yields to the loop BEFORE it does blocking store IO    #
# --------------------------------------------------------------------------- #
def test_drain_pushes_every_seeded_notification(drain_with_canary):
    # Arrange
    result = drain_with_canary
    # Act
    pushed = result["pushed"]
    # Assert
    assert pushed == 5, "all seeded notifications should be pushed"


def test_drain_calls_send_at_least_once(drain_with_canary):
    # Arrange
    result = drain_with_canary
    # Act
    saw = result["saw"]
    # Assert — without this the yield assertion below would be vacuous.
    assert saw, "send should have been called at least once"


def test_drain_yields_before_blocking_store_io(drain_with_canary):
    """``drain_once`` must hand control back to the event loop before its first
    push — i.e. the store reads run off-thread, not inline."""
    # Arrange
    result = drain_with_canary
    # Act
    saw = result["saw"]
    # Assert
    assert saw[0] is True, (
        "drain_once ran store IO inline (blocking the loop) before its first "
        "push — the poll loop would starve the MCP initialize handshake"
    )


# --------------------------------------------------------------------------- #
# End-to-end: a real MCP client completes initialize while the poll loop runs  #
# --------------------------------------------------------------------------- #
def test_initialize_completes_with_active_poll_loop(initialize_result):
    # Arrange
    result = initialize_result
    # Act
    protocol_version = result.protocolVersion
    # Assert — the handshake finished well within the tight timeout.
    assert protocol_version


def test_initialize_advertises_the_channel_capability(initialize_result):
    # Arrange
    result = initialize_result
    # Act
    experimental = result.capabilities.experimental or {}
    # Assert — without the advertisement the client drops every channel push.
    assert "claude/channel" in experimental
