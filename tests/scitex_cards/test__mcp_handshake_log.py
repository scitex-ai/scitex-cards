#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The MCP handshake flight recorder — what it must record, and must survive.

`scitex-cards mcp start` answers its first `initialize` 7-14 seconds after
spawn, and the peer that suffers from it cannot produce the evidence: its stderr
sink is truncated on boot, so a disconnect that precedes a restart destroys its
own trace. These tests pin the properties that make a sink capable of holding
evidence about a boot:

* it is APPEND-ONLY — a start never removes what an earlier boot wrote, and a
  rotation renames rather than empties;
* it records an `initialize` that is received and NEVER ANSWERED. A recorder
  that only logged completed handshakes would be silent on precisely the failure
  it exists to catch;
* it FAILS OPEN — an unwritable sink disables the recorder, never the server.
  We are diagnosing an availability problem and must not create one.

Real anyio streams, real MCP types, a real `ClientSession` doing a real
`initialize` against the real `_serve` — no mocks (STX-NM / PA-306). The repo
has no pytest-asyncio, so async bodies run under `asyncio.run` like the sibling
channel tests.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from scitex_cards._mcp_channel import _serve
from scitex_cards._mcp_handshake_log import (
    ENV_LOG_PATH,
    LOG_FILENAME,
    MAX_BYTES,
    HandshakeLog,
    instrument_handshake,
    resolve_log_path,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _records(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _events(path):
    return [rec["event"] for rec in _records(path)]


def _first(path, event):
    return next(rec for rec in _records(path) if rec["event"] == event)


def _initialize_over_memory_streams():
    """Complete a real `initialize` against the real `_serve`; return the result."""
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
                        agent_id=None,  # tools-only: no store, no poll loop
                        source="scards",
                        interval=60.0,
                        server=None,
                    )

                tg.start_soon(run_server)

                async with ClientSession(c_read, c_write) as session:
                    captured["result"] = await asyncio.wait_for(
                        session.initialize(), timeout=10.0
                    )

                tg.cancel_scope.cancel()
        return captured["result"]

    return asyncio.run(body())


def _receive_initialize_without_answering():
    """Take an `initialize` off the transport and then vanish, as a killed
    process does. Nothing ever writes a response."""
    import anyio
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage, JSONRPCRequest

    async def body():
        in_send, in_recv = anyio.create_memory_object_stream(1)
        out_send, _out_recv = anyio.create_memory_object_stream(1)
        read_stream, _write_stream, log = instrument_handshake(in_recv, out_send)
        request = SessionMessage(
            JSONRPCMessage(
                JSONRPCRequest(
                    jsonrpc="2.0",
                    id=1,
                    method="initialize",
                    params={
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "probe", "version": "0"},
                    },
                )
            )
        )
        await in_send.send(request)
        await read_stream.receive()  # the session has the request in hand...
        log.close()  # ...and the process ends here, having answered nothing.

    asyncio.run(body())


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sink(tmp_path, env):
    """A per-test sink path, wired in the way a real deployment wires it."""
    path = tmp_path / "runtime" / "mcp-handshake.jsonl"
    env.set(ENV_LOG_PATH, str(path))
    return path


@pytest.fixture()
def completed_handshake(sink):
    """The sink after one full, successful handshake."""
    _initialize_over_memory_streams()
    return sink


@pytest.fixture()
def sink_written_by_an_earlier_boot(sink):
    """A sink that already holds a previous boot's line, then a server start."""
    sink.parent.mkdir(parents=True, exist_ok=True)
    sink.write_text('{"event": "line-from-a-previous-boot"}\n')
    _initialize_over_memory_streams()
    return sink


@pytest.fixture()
def unanswered_handshake(sink):
    """The sink after an `initialize` that was received and never answered."""
    _receive_initialize_without_answering()
    return sink


@pytest.fixture()
def handshake_with_unwritable_sink(tmp_path, env):
    """A handshake run with the sink path blocked by a regular file."""
    blocker = tmp_path / "this-is-a-file"
    blocker.write_text("not a directory\n")
    env.set(ENV_LOG_PATH, str(blocker / "runtime" / "mcp-handshake.jsonl"))
    return _initialize_over_memory_streams()


@pytest.fixture()
def switched_off_instrument(env):
    """`instrument_handshake` with the recorder disabled by env."""
    import anyio

    env.set(ENV_LOG_PATH, "off")
    send, recv = anyio.create_memory_object_stream(1)
    read_stream, write_stream, log = instrument_handshake(recv, send)
    return {
        "read_in": recv,
        "read_out": read_stream,
        "write_in": send,
        "write_out": write_stream,
        "log": log,
    }


@pytest.fixture()
def wrapped_streams(sink):
    """`instrument_handshake` with the recorder ENABLED — the wrapped case."""
    import anyio

    send, recv = anyio.create_memory_object_stream(1)
    read_stream, write_stream, log = instrument_handshake(recv, send)
    return {
        "read_in": recv,
        "read_out": read_stream,
        "write_in": send,
        "write_out": write_stream,
        "log": log,
    }


@pytest.fixture()
def rotated_sink(tmp_path):
    """A sink grown past `MAX_BYTES`, then opened by a fresh recorder."""
    path = tmp_path / "mcp-handshake.jsonl"
    path.write_text("EVIDENCE-FROM-BEFORE-ROTATION\n" + "x" * MAX_BYTES)
    HandshakeLog(path).close()
    return path


@pytest.fixture()
def default_sink_path(tmp_path, env):
    """The sink path resolved with no override — the deployed default."""
    env.delete(ENV_LOG_PATH)
    env.set("SCITEX_CARDS_DB", str(tmp_path / "cards.db"))
    return resolve_log_path()


# --------------------------------------------------------------------------- #
# What a completed handshake records                                          #
# --------------------------------------------------------------------------- #
def test_a_server_start_is_recorded(completed_handshake):
    # Arrange
    events = _events(completed_handshake)
    # Act
    present = "server_start" in events
    # Assert
    assert present, "the sink must show that a server process began serving"


def test_initialize_received_is_recorded(completed_handshake):
    # Arrange
    events = _events(completed_handshake)
    # Act
    present = "initialize_received" in events
    # Assert
    assert present, "the sink must show WHEN the handshake request arrived"


def test_initialize_answered_is_recorded(completed_handshake):
    # Arrange
    events = _events(completed_handshake)
    # Act
    present = "initialize_answered" in events
    # Assert
    assert present, "the sink must show WHEN the handshake request was answered"


def test_the_handshake_delta_is_recorded(completed_handshake):
    # Arrange
    answered = _first(completed_handshake, "initialize_answered")
    # Act
    delta = answered.get("handshake_s")
    # Assert — the delta is what makes the record diagnosable at a glance.
    assert isinstance(delta, (int, float)), "the answered record must carry a delta"


def test_the_answer_is_recorded_no_earlier_than_the_request(completed_handshake):
    # Arrange
    received = _first(completed_handshake, "initialize_received")
    answered = _first(completed_handshake, "initialize_answered")
    # Act
    ordered = answered["ts"] >= received["ts"]
    # Assert
    assert ordered, "the answer cannot predate the request it answers"


def test_the_startup_cost_before_the_serve_loop_is_recorded(completed_handshake):
    # Arrange
    started = _first(completed_handshake, "server_start")
    # Act
    startup = started.get("startup_s")
    # Assert — the gap between exec and serving is where the seconds go.
    assert isinstance(startup, (int, float)), "server_start must carry startup_s"


def test_the_process_start_anchor_precedes_the_first_event(completed_handshake):
    # Arrange
    started = _first(completed_handshake, "server_start")
    # Act
    anchored = started["proc_start"] < started["ts"]
    # Assert — an anchor taken after the imports would hide the interval.
    assert anchored, "process start must precede the event measured against it"


def test_the_instrument_reports_its_own_setup_cost(completed_handshake):
    # Arrange
    started = _first(completed_handshake, "server_start")
    # Act
    setup = started.get("setup_ms")
    # Assert — instrumenting latency by adding hidden latency is self-defeating.
    assert isinstance(setup, (int, float)), "server_start must publish setup_ms"


def test_the_serve_loop_ending_is_recorded(completed_handshake):
    # Arrange
    events = _events(completed_handshake)
    # Act
    present = "server_exit" in events
    # Assert — it separates "died mid-handshake" from "still hanging".
    assert present, "the sink must show that the serve loop returned"


# --------------------------------------------------------------------------- #
# The failure the sink exists to catch: received, never answered               #
# --------------------------------------------------------------------------- #
def test_an_unanswered_initialize_is_still_recorded(unanswered_handshake):
    # Arrange
    events = _events(unanswered_handshake)
    # Act
    present = "initialize_received" in events
    # Assert — recorded on arrival, so a process killed mid-handshake still
    # leaves the evidence behind.
    assert present, (
        "an initialize that was received and never answered must be recorded; "
        "a sink showing only completed handshakes is silent on the failure it "
        "exists to catch"
    )


def test_an_unanswered_initialize_records_no_answer(unanswered_handshake):
    # Arrange
    events = _events(unanswered_handshake)
    # Act
    present = "initialize_answered" in events
    # Assert — the orphaned record IS the diagnosis; a fabricated partner
    # would erase it.
    assert not present, "nothing answered, so nothing may claim an answer"


# --------------------------------------------------------------------------- #
# Append-only: the property a truncate-on-boot log structurally lacks          #
# --------------------------------------------------------------------------- #
def test_an_earlier_boots_record_survives_a_server_start(
    sink_written_by_an_earlier_boot,
):
    # Arrange
    text = sink_written_by_an_earlier_boot.read_text()
    # Act
    survived = "line-from-a-previous-boot" in text
    # Assert — a log cleared on start cannot retain evidence about a start.
    assert survived, "starting the server must not remove an earlier boot's record"


def test_a_server_start_appends_after_the_earlier_content(
    sink_written_by_an_earlier_boot,
):
    # Arrange
    lines = sink_written_by_an_earlier_boot.read_text().splitlines()
    # Act
    first = json.loads(lines[0])["event"]
    # Assert
    assert first == "line-from-a-previous-boot", "new records must be APPENDED"


def test_an_oversized_sink_is_rotated_to_a_numbered_generation(rotated_sink):
    # Arrange
    generation = rotated_sink.with_name(rotated_sink.name + ".1")
    # Act
    exists = generation.exists()
    # Assert
    assert exists, "a sink past MAX_BYTES must rotate by rename"


def test_rotation_preserves_the_rotated_generations_content(rotated_sink):
    # Arrange
    generation = rotated_sink.with_name(rotated_sink.name + ".1")
    # Act
    head = generation.read_text(errors="replace")[:29]
    # Assert — rotation moves evidence aside; it never empties it.
    assert head == "EVIDENCE-FROM-BEFORE-ROTATION"


# --------------------------------------------------------------------------- #
# Fail open: the recorder must never become the outage                        #
# --------------------------------------------------------------------------- #
def test_an_unwritable_sink_does_not_stop_the_handshake(
    handshake_with_unwritable_sink,
):
    # Arrange
    result = handshake_with_unwritable_sink
    # Act
    protocol_version = result.protocolVersion
    # Assert — diagnosing an availability problem must not create one.
    assert protocol_version, "an unwritable sink must not prevent the handshake"


def test_a_recorder_with_no_sink_can_still_be_called():
    # Arrange
    log = HandshakeLog(None)
    # Act
    log.record("server_start", note="no sink, no branch at the call site")
    # Assert — callers never guard on whether logging works.
    assert not log.enabled, "a sinkless recorder is inert, not an error"


# --------------------------------------------------------------------------- #
# Switched off: exactly zero transport overhead                               #
# --------------------------------------------------------------------------- #
def test_switching_the_recorder_off_returns_the_original_read_stream(
    switched_off_instrument,
):
    # Arrange
    streams = switched_off_instrument
    # Act
    unwrapped = streams["read_out"] is streams["read_in"]
    # Assert — off means no wrapper, no per-message hook at all.
    assert unwrapped, "a disabled recorder must not wrap the read stream"


def test_switching_the_recorder_off_returns_the_original_write_stream(
    switched_off_instrument,
):
    # Arrange
    streams = switched_off_instrument
    # Act
    unwrapped = streams["write_out"] is streams["write_in"]
    # Assert
    assert unwrapped, "a disabled recorder must not wrap the write stream"


def test_switching_the_recorder_off_leaves_it_disabled(switched_off_instrument):
    # Arrange
    log = switched_off_instrument["log"]
    # Act
    enabled = log.enabled
    # Assert
    assert not enabled, "the env off-switch must actually disable the recorder"


# --------------------------------------------------------------------------- #
# Switched on: a wrapper that is still the stream it wraps                    #
# --------------------------------------------------------------------------- #
def test_the_read_wrapper_delegates_unknown_attributes_to_the_stream(wrapped_streams):
    # Arrange
    wrapper = wrapped_streams["read_out"]
    # Act
    buffer_size = wrapper.statistics().max_buffer_size
    # Assert — observing must not remove any of the transport's own surface.
    assert buffer_size == 1


def test_the_write_wrapper_delegates_unknown_attributes_to_the_stream(wrapped_streams):
    # Arrange
    wrapper = wrapped_streams["write_out"]
    # Act
    buffer_size = wrapper.statistics().max_buffer_size
    # Assert
    assert buffer_size == 1


def test_an_enabled_recorder_wraps_the_read_stream(wrapped_streams):
    # Arrange
    streams = wrapped_streams
    # Act
    wrapped = streams["read_out"] is not streams["read_in"]
    # Assert — the complement of the switched-off tests, so those cannot pass
    # vacuously by the recorder never wrapping anything at all.
    assert wrapped, "an enabled recorder must observe the read stream"


# --------------------------------------------------------------------------- #
# Path resolution: standalone, and where every other runtime file lives       #
# --------------------------------------------------------------------------- #
def test_the_default_sink_lives_in_the_store_runtime_dir(default_sink_path):
    # Arrange
    path = default_sink_path
    # Act
    parent = path.parent.name
    # Assert — same <store_dir>/runtime as every other cards runtime file.
    assert parent == "runtime"


def test_the_default_sink_is_named_for_the_handshake(default_sink_path):
    # Arrange
    path = default_sink_path
    # Act
    name = path.name
    # Assert
    assert name == LOG_FILENAME


def test_the_default_sink_follows_the_configured_store(default_sink_path, tmp_path):
    # Arrange
    path = default_sink_path
    # Act
    under_store = str(path).startswith(str(tmp_path))
    # Assert — an agent that knows its store knows its handshake log.
    assert under_store, "the sink must track the store the agent actually uses"


# EOF
