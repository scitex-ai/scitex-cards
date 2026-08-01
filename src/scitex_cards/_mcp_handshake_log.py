#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only record of every MCP `initialize` handshake — the flight recorder.

WHY THIS EXISTS. `scitex-cards mcp start` answers its first `initialize` 7-14
seconds after spawn (measured 2026-07-29: 6.67 / 7.04 / 7.19 / 8.49 / 9.76 s in
one run of five). Clients with a fixed handshake timeout mark the server "not
connected"; the peer agent scitex-agent-container has repeatedly lost its card
slice that way. The VARIANCE matters as much as the mean — a fixed timeout is a
coin flip, not a constant.

WHY A DEDICATED SINK RATHER THAN STDERR. The client's stderr sink is
TRUNCATE-ON-BOOT. A disconnect that precedes (or causes) a restart therefore
destroys its own evidence: the file is 0 bytes, stamped at the restart. A log
that is cleared on start is structurally incapable of retaining evidence about
anything that causes a start. So this sink is opened `O_APPEND | O_CREAT` and
NEVER `O_TRUNC`; when it grows past `MAX_BYTES` it ROTATES by rename (the base
file is moved aside, never emptied in place).

WHAT MAKES IT DIAGNOSTIC. Four facts per run, each written the moment it is
known rather than at the end:

    server_start          - the serve loop is about to read the transport,
                            `startup_s` after the process was exec'd. That
                            gap IS the import cost sitting in front of the
                            handshake.
    initialize_received   - the session took the request off the transport.
    initialize_answered   - the response was handed to the stdout writer;
                            `handshake_s` is the delta.
    server_exit           - the serve loop returned.

The failure we are hunting is `initialize` received and NEVER answered, or
answered after the client gave up. A sink that only recorded COMPLETED
handshakes would be silent on exactly that case — the same defect as an
auth-heal that logs only its actions and is therefore indistinguishable from one
that never ran. Because `initialize_received` is written (one unbuffered
`os.write`) BEFORE the server has any chance to answer, a run that is killed
mid-handshake leaves a `initialize_received` with no partner line. That orphan
IS the diagnosis.

FAIL OPEN, ALWAYS. Every path here is wrapped: an unwritable directory, a
read-only filesystem, a full disk — any of them disables the recorder and lets
the server start. We are diagnosing an availability problem and must not create
one. A recorder that refused to start would be worse than no recorder.

COST. One `os.write` of ~150 bytes per event, four events per process; the
per-message hook on the transport is one attribute lookup and a comparison, and
short-circuits to a single boolean test once the handshake is done. Setup (path
resolution, rotation check, open) is measured by the recorder itself and
reported in the `server_start` line as `setup_ms`, so the instrument's own
overhead is visible in its own output rather than asserted in a docstring.

STANDALONE. Resolves its path via `scitex_cards._paths.runtime_dir` — the same
`<store_dir>/runtime/` every other piece of cards runtime state uses — with a
pure `$SCITEX_DIR`/`$HOME` fallback if even that fails. Nothing here imports or
assumes `scitex_agent_container` or a sac-managed environment.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Override the sink location. Set it to one of :data:`_OFF_VALUES` to disable
#: the recorder entirely (the transport is then left completely unwrapped).
ENV_LOG_PATH = "SCITEX_CARDS_MCP_HANDSHAKE_LOG"

#: Sink filename inside ``<store_dir>/runtime/``.
LOG_FILENAME = "mcp-handshake.jsonl"

#: Rotate (by rename) once the sink passes this size. Never truncate.
MAX_BYTES = 2_000_000

#: How many rotated generations to retain (``.1`` … ``.3``).
KEEP = 3

_OFF_VALUES = frozenset({"", "0", "off", "no", "none", "false"})

#: Wall clock at first import of this module — the fallback "process start"
#: when ``/proc`` is unavailable. It is LATE (imports already happened), which
#: is why it is labelled ``approx`` in the record rather than passed off as the
#: real exec time.
_IMPORT_EPOCH = time.time()


# --------------------------------------------------------------------------- #
# Process start — the anchor every delta is measured from                     #
# --------------------------------------------------------------------------- #
def process_start_epoch() -> tuple[float, str]:
    """Return ``(epoch_seconds_of_exec, source)`` for THIS process.

    The anchor has to be the real ``exec`` time, not "when this module was
    imported": the whole hypothesis is that seconds are burned importing BEFORE
    anything of ours runs, and an anchor taken after the imports would hide
    exactly the interval we are trying to measure.

    On Linux, field 22 of ``/proc/self/stat`` is the process start in clock
    ticks since boot; ``/proc/uptime`` converts boot to a wall clock. Both are
    tiny reads (well under a millisecond). Anywhere else — or if either read
    fails — we fall back to this module's import time and SAY SO via the
    ``source`` element (``"approx"``), because a fallback silently presented as
    a measurement is worse than an honest gap.
    """
    try:
        # The comm field can contain spaces and parentheses, so split on the
        # LAST ')' — everything after it starts at field 3 (state).
        after = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        ticks = float(after[19])  # field 22 == index 19 counting from field 3
        hz = os.sysconf("SC_CLK_TCK")
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        return (time.time() - uptime) + ticks / hz, "proc"
    except Exception:  # noqa: BLE001 — fail open; an anchor is never worth a crash
        return _IMPORT_EPOCH, "approx"


# --------------------------------------------------------------------------- #
# Sink                                                                        #
# --------------------------------------------------------------------------- #
def resolve_log_path(explicit: str | Path | None = None) -> Path | None:
    """Resolve the sink path, or ``None`` when the recorder is switched off.

    Precedence: ``explicit`` → ``$SCITEX_CARDS_MCP_HANDSHAKE_LOG`` → the
    canonical ``<store_dir>/runtime/mcp-handshake.jsonl``. The store-derived
    default puts the record beside every other piece of cards runtime state, so
    an agent that knows where its store is knows where its handshake log is.

    The last resort (``$SCITEX_DIR``/``$HOME`` directly) exists because
    :func:`~scitex_cards._paths.runtime_dir` resolves through the database path,
    and a broken store resolution is precisely a moment when we still want the
    record. Returns ``None`` only when explicitly disabled or when even the
    home-relative fallback cannot be formed.
    """
    if explicit is not None:
        raw = str(explicit)
        return None if raw.strip().lower() in _OFF_VALUES else Path(raw).expanduser()

    env = os.environ.get(ENV_LOG_PATH)
    if env is not None:
        return None if env.strip().lower() in _OFF_VALUES else Path(env).expanduser()

    try:
        from ._paths import runtime_dir

        return runtime_dir(create=False) / LOG_FILENAME
    except Exception:  # noqa: BLE001 — store resolution is allowed to be broken
        pass
    try:
        from ._paths import _user_root

        return _user_root() / "runtime" / LOG_FILENAME
    except Exception:  # noqa: BLE001 — fail open into "no recorder"
        return None


def _rotate_if_needed(path: Path) -> None:
    """Rotate by SIZE, by RENAME, retaining :data:`KEEP` generations.

    Deliberately not "clear on start": the base file is moved to ``.1`` and the
    next open creates a fresh one, so no record is ever destroyed by a start.
    Rotation is checked once per process (at open), never per write.
    """
    try:
        if path.stat().st_size < MAX_BYTES:
            return
    except OSError:
        return  # missing (nothing to rotate) or unreadable — either way, leave it
    for i in range(KEEP, 0, -1):
        src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
        dst = path.with_name(f"{path.name}.{i}")
        try:
            if src.exists():
                os.replace(src, dst)
        except OSError:
            return  # a rotation we cannot do must not stop us appending


class HandshakeLog:
    """An append-only JSONL recorder that can always be called.

    A recorder whose sink could not be opened is not an error and not ``None``:
    it is this same object with ``enabled`` false, and every :meth:`record` on
    it is a no-op. Callers therefore never branch on whether logging works, and
    a broken sink cannot become a broken server.

    Writes go through raw :func:`os.write` on an ``O_APPEND`` descriptor: one
    syscall, no user-space buffering, so a line is durable the instant it is
    recorded — a process killed immediately afterwards still leaves it behind.
    ``O_APPEND`` also makes concurrent servers appending to one sink safe
    without a lock.
    """

    __slots__ = ("_fd", "path", "run_id", "proc_start", "proc_start_source")

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._fd: int | None = None
        self.run_id = f"{os.getpid()}-{int(time.time() * 1000) % 1_000_000}"
        self.proc_start, self.proc_start_source = process_start_epoch()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path)
            self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        except Exception:  # noqa: BLE001 — an unwritable sink disables, never raises
            self._fd = None

    @property
    def enabled(self) -> bool:
        return self._fd is not None

    def record(self, event: str, **fields: Any) -> None:
        """Append one event. Never raises, never blocks on anything but the fd.

        A write that fails permanently disables the recorder rather than
        retrying on every subsequent event: a sink that has started failing
        (full disk, revoked mount) will keep failing, and paying for it once per
        message is exactly the kind of self-inflicted latency this file is
        supposed to be measuring, not adding.
        """
        if self._fd is None:
            return
        now = time.time()
        rec = {
            "ts": now,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
            + f".{int((now % 1) * 1000):03d}",
            "event": event,
            "run": self.run_id,
            "pid": os.getpid(),
            "since_proc_start_s": round(now - self.proc_start, 4),
            **fields,
        }
        try:
            os.write(self._fd, (json.dumps(rec, default=str) + "\n").encode())
        except Exception:  # noqa: BLE001 — see docstring
            self.close()

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Transport observation                                                       #
# --------------------------------------------------------------------------- #
def _root(message: Any) -> Any:
    """The JSON-RPC payload inside a ``SessionMessage``, or ``None``.

    Deliberately total: the read stream also carries bare ``Exception`` objects
    (a line that failed to parse), and observing must never be the thing that
    raises.
    """
    return getattr(getattr(message, "message", None), "root", None)


class HandshakeObserver:
    """Watches the transport for the `initialize` request and its response.

    WHY AT THE TRANSPORT, not in our own message loop: ``ServerSession`` answers
    ``initialize`` INSIDE the SDK (``_received_request``) and never yields it to
    ``session.incoming_messages``, so a hook in ``_serve``'s loop would never see
    the one message that matters. Wrapping the streams sees every byte the
    session sees, and — importantly — sees the request even when no response
    ever follows.
    """

    __slots__ = ("_log", "_init_id", "_recv_ts", "_done")

    def __init__(self, log: HandshakeLog) -> None:
        self._log = log
        self._init_id: str | None = None
        self._recv_ts: float | None = None
        self._done = False

    def saw_incoming(self, message: Any) -> None:
        if self._done or self._init_id is not None:
            return
        root = _root(message)
        if getattr(root, "method", None) != "initialize":
            return
        self._init_id = str(getattr(root, "id", ""))
        self._recv_ts = time.time()
        params = getattr(root, "params", None) or {}
        client = {}
        if isinstance(params, dict):
            info = params.get("clientInfo") or {}
            if isinstance(info, dict):
                client = {"name": info.get("name"), "version": info.get("version")}
        self._log.record(
            "initialize_received",
            request_id=self._init_id,
            client=client or None,
        )

    def saw_outgoing(self, message: Any) -> None:
        if self._done or self._init_id is None:
            return
        root = _root(message)
        if str(getattr(root, "id", "\x00")) != self._init_id:
            return
        self._done = True
        now = time.time()
        self._log.record(
            "initialize_answered",
            request_id=self._init_id,
            ok=getattr(root, "error", None) is None,
            # THE number this file exists to produce: how long the client was
            # kept waiting once we had its request in hand.
            handshake_s=round(now - (self._recv_ts or now), 4),
        )


#: Own attributes of the stream wrappers. ``__getattr__`` must refuse these by
#: name: with ``__slots__``, an unset slot also lands in ``__getattr__``, and
#: delegating ``_stream`` through ``self._stream`` would recurse until the stack
#: gave out — inside a transport, on the boot path. Guarding is cheaper than
#: reasoning about who might touch the object before ``__init__`` finishes.
_WRAPPER_OWN = frozenset({"_stream", "_obs"})


class _ObservedReceive:
    """Pass-through receive stream that shows each message to the observer."""

    __slots__ = ("_stream", "_obs")

    def __init__(self, stream: Any, obs: HandshakeObserver) -> None:
        self._stream = stream
        self._obs = obs

    def __getattr__(self, name: str) -> Any:
        if name in _WRAPPER_OWN:
            raise AttributeError(name)
        return getattr(self._stream, name)

    async def __aenter__(self) -> "_ObservedReceive":
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._stream.__aexit__(*exc)

    def __aiter__(self) -> "_ObservedReceive":
        return self

    async def __anext__(self) -> Any:
        message = await self._stream.__anext__()
        self._obs.saw_incoming(message)
        return message

    async def receive(self) -> Any:
        message = await self._stream.receive()
        self._obs.saw_incoming(message)
        return message


class _ObservedSend:
    """Pass-through send stream that shows each message to the observer.

    The observation happens AFTER ``send`` returns. The underlying memory stream
    is unbuffered, so by then the stdout writer has taken the message and is
    about to write and flush it — "answered" therefore means handed to the
    transport, which is the honest claim we can make from inside the process.
    """

    __slots__ = ("_stream", "_obs")

    def __init__(self, stream: Any, obs: HandshakeObserver) -> None:
        self._stream = stream
        self._obs = obs

    def __getattr__(self, name: str) -> Any:
        if name in _WRAPPER_OWN:
            raise AttributeError(name)
        return getattr(self._stream, name)

    async def __aenter__(self) -> "_ObservedSend":
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._stream.__aexit__(*exc)

    async def send(self, item: Any) -> None:
        await self._stream.send(item)
        self._obs.saw_outgoing(item)


def instrument_handshake(
    read_stream: Any,
    write_stream: Any,
    *,
    path: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[Any, Any, HandshakeLog]:
    """Wrap a transport pair so the `initialize` handshake is recorded.

    Emits ``server_start`` immediately — carrying ``startup_s``, the gap between
    the process being exec'd and the serve loop being ready, which is where the
    seconds actually go — then returns the (wrapped) streams and the recorder.

    When the recorder is disabled the ORIGINAL stream objects come back
    untouched, so switching it off costs exactly nothing: no wrapper, no extra
    frame, no attribute lookup per message.

    The instrument's own setup cost is timed here and reported as ``setup_ms``
    in the very line it writes. Instrumenting a latency problem by adding
    latency would be self-defeating, so the overhead is published in the same
    place as the measurement rather than claimed in a comment.
    """
    t0 = time.perf_counter()
    try:
        log = HandshakeLog(resolve_log_path(path))
    except Exception:  # noqa: BLE001 — belt and braces; must never break a start
        return read_stream, write_stream, HandshakeLog(None)
    if not log.enabled:
        return read_stream, write_stream, log
    setup_ms = round((time.perf_counter() - t0) * 1000, 3)
    log.record(
        "server_start",
        proc_start=round(log.proc_start, 4),
        proc_start_source=log.proc_start_source,
        # Everything before we could read the transport: interpreter boot plus
        # every import. This is the number the startup-cost card must move.
        startup_s=round(time.time() - log.proc_start, 4),
        setup_ms=setup_ms,
        **(extra or {}),
    )
    obs = HandshakeObserver(log)
    return _ObservedReceive(read_stream, obs), _ObservedSend(write_stream, obs), log


# EOF
