#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process/pidfile helpers for the ``scitex-todo board`` lifecycle CLI.

Extracted from :mod:`scitex_cards._cli._board` to keep that module under
the 512-line cap and to give the pidfile + port-resolution logic a
cohesive home. ``_board`` re-imports the public names so call sites
(and tests) are unchanged.

The port fallback (added for the stale-pidfile incident): when a board
is genuinely serving on the configured port but the pidfile is dead or
missing (e.g. an untracked board process holds the port), ``stop`` /
``restart`` / ``status`` can still find and act on the REAL process. The
cmdline-marker guard in :func:`_board_cmdline_is_board` is what makes
that safe — a foreign process holding the port is never reported, so it
is never signalled.

OS LIMIT (not a bug): a resolved PID can only be signalled if it is
owned by the SAME user as the caller. The kernel denies cross-user kill;
``stop`` surfaces that as a clear error rather than silently succeeding.

THE SIGTERM->poll->SIGKILL SEQUENCE LIVES HERE TOO, in
:func:`stop_board_process`, and it is deliberately NOT click-aware. It
used to be inline in ``board stop``'s command body, which meant the only
way to reach it was to type that verb: ``gui serve --force`` (the
operator asked for it on 2026-08-14 because typing ``gui stop`` first is
busywork) would otherwise have had to hand-roll a SECOND escalation, and
two escalation sequences racing for one pidfile is exactly the failure
the ``gui`` group was written to avoid. One implementation, every caller.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path as _Path

BOARD_PIDFILE = _Path.home() / ".scitex" / "todo" / "board.pid"

# Markers we expect in a board process's /proc/<pid>/cmdline. The board
# is launched via Django's ``call_command("scitex_cards_board", ...)``
# (see ``_board.board_run_server``), so the management-command name
# appears in the live process's argv. We require one of these before we
# ever signal a port-found PID — NEVER touch an unrelated process that
# merely happens to hold the port.
_BOARD_CMDLINE_MARKERS = ("scitex_cards_board", "scitex_cards._django")


def _board_pidfile() -> _Path:
    """Return the pidfile path (function so tests can override via env)."""
    import os as _os

    override = _os.environ.get("SCITEX_TODO_BOARD_PIDFILE")
    if override:
        return _Path(override)
    return BOARD_PIDFILE


def _board_pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` is the POSIX 'is this PID up?' probe."""
    import os as _os

    try:
        _os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def _board_read_pid() -> int | None:
    """Read the pidfile; return None when absent/unreadable/dead."""
    pf = _board_pidfile()
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _board_pid_alive(pid):
        # Stale pidfile from a crashed process — clean it up.
        try:
            pf.unlink()
        except OSError:
            pass
        return None
    return pid


def _board_write_pid(pid: int) -> None:
    """Write the pidfile, creating parent dirs as needed."""
    pf = _board_pidfile()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid))


def _board_cmdline_is_board(pid: int) -> bool:
    """True iff ``/proc/<pid>/cmdline`` looks like a scitex-todo board.

    The cmdline-marker guard is what makes the port fallback SAFE: a
    foreign process holding the configured port is NOT ours and must
    never be signalled. We read the NUL-separated argv and require one
    of :data:`_BOARD_CMDLINE_MARKERS`. If /proc is unavailable (non-Linux)
    or unreadable, we conservatively return False — better to under-claim
    than to kill a stranger.
    """
    try:
        raw = _Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return False
    cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
    return any(m in cmdline for m in _BOARD_CMDLINE_MARKERS)


def _board_pid_on_port(port: int) -> int | None:
    """Return the PID of the scitex-todo board listening on ``port``.

    Tries the available port-introspection tools in order — ``lsof``,
    ``ss``, then ``fuser`` — and tolerates any of them being absent
    (returns None rather than raising). The found PID is only returned
    after :func:`_board_cmdline_is_board` confirms it is OUR board, so a
    stranger holding the port is never reported (and so never killed by
    the stop/restart fallback).

    OS LIMIT (not a bug): the resolved PID can only later be signalled if
    it is owned by the SAME user as the caller — the kernel denies
    cross-user kill. We may still *find* such a PID here; the SIGTERM
    itself is what fails, surfaced as a clear error by ``stop``.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    def _run(argv: list[str]) -> str | None:
        try:
            out = _subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, _subprocess.SubprocessError):
            return None
        return out.stdout

    pids: list[int] = []

    if _shutil.which("lsof"):
        # `lsof -ti tcp:PORT` → one PID per line (listeners + clients).
        out = _run(["lsof", "-ti", f"tcp:{port}"])
        if out:
            for line in out.split():
                try:
                    pids.append(int(line))
                except ValueError:
                    continue

    if not pids and _shutil.which("ss"):
        # `ss -ltnp` lines look like:
        #   ... *:8051 ... users:(("python",pid=1234,fd=7))
        out = _run(["ss", "-ltnp"])
        if out:
            import re as _re

            for line in out.splitlines():
                if f":{port}" not in line:
                    continue
                for m in _re.finditer(r"pid=(\d+)", line):
                    try:
                        pids.append(int(m.group(1)))
                    except ValueError:
                        continue

    if not pids and _shutil.which("fuser"):
        # `fuser PORT/tcp` → whitespace-separated PIDs on stdout.
        out = _run(["fuser", f"{port}/tcp"])
        if out:
            for tok in out.split():
                try:
                    pids.append(int(tok))
                except ValueError:
                    continue

    # Return the first PID whose cmdline proves it's a board (guard).
    for pid in pids:
        if _board_cmdline_is_board(pid):
            return pid
    return None


def _board_resolve_pid(port: int) -> tuple[int | None, bool]:
    """Resolve the live board PID, with a port fallback.

    Returns ``(pid, untracked)``:
      - ``(pid, False)`` — the pidfile is valid and live (current path,
        unchanged behaviour).
      - ``(pid, True)``  — the pidfile is dead/missing but a verified
        board is serving on ``port``; the stale pidfile is cleaned up.
      - ``(None, False)`` — nothing running anywhere we can see.
    """
    pid = _board_read_pid()
    if pid is not None:
        return pid, False
    # Pidfile dead/missing — fall back to the port (cmdline-verified).
    found = _board_pid_on_port(port)
    if found is not None:
        # Clean up any stale pidfile left behind (`_board_read_pid`
        # already removes a dead one, but a leftover unreadable file or a
        # race could persist — be defensive).
        pf = _board_pidfile()
        try:
            if pf.exists():
                pf.unlink()
        except OSError:
            pass
        return found, True
    return None, False


# === stopping a board =======================================================


class StopResult(enum.Enum):
    """How a stop attempt ended. Three-valued because the truth is.

    ``SIGKILL_SENT`` is NOT a success and NOT a failure: SIGKILL cannot be
    caught, but we do not re-poll after sending it, so the process's exit
    is genuinely UNOBSERVED. Reporting it as ``EXITED`` would be inventing
    a signal the code never measured; reporting it as a failure would be
    equally wrong. The constitution's rule -- every signal is three-valued,
    and collapsing *unknown* into either pole is the most common bug we
    ship -- is what this third member is for.
    """

    #: The process was observed gone within the timeout (SIGTERM worked).
    EXITED = "exited"
    #: SIGTERM timed out; SIGKILL was sent but the exit was NOT re-checked.
    SIGKILL_SENT = "sigkill-sent"
    #: The kernel refused the signal (cross-user kill, vanished pid, ...).
    SIGNAL_REFUSED = "signal-refused"


@dataclass(frozen=True)
class StopOutcome:
    """The fixed shape :func:`stop_board_process` answers in.

    Never a bare bool and never a tuple: a caller reading ``stopped`` has to
    know whether SIGKILL was involved (``gui serve --force`` prints a louder
    line for that) and, on refusal, WHICH signal the kernel rejected -- so
    each of those is its own named field rather than a positional the next
    caller has to count out.
    """

    pid: int
    result: StopResult
    timeout: float
    escalated_to_sigkill: bool
    #: The ready-to-print refusal, e.g. ``"could not SIGTERM pid 42: ..."``.
    #: Set if and only if ``result is SIGNAL_REFUSED``.
    error: str | None = None
    _validated: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        # Validate at construction so a malformed outcome fails HERE, where
        # it is built, rather than in the caller that trusted its shape.
        if not isinstance(self.result, StopResult):
            raise TypeError(
                f"result must be StopResult, got {type(self.result).__name__}"
            )
        if self.pid <= 0:
            raise ValueError(f"pid must be a positive int, got {self.pid!r}")
        if self.timeout < 0:
            raise ValueError(f"timeout must be >= 0, got {self.timeout!r}")
        refused = self.result is StopResult.SIGNAL_REFUSED
        if refused and not self.error:
            raise ValueError(
                "SIGNAL_REFUSED requires the error that names the refused signal"
            )
        if not refused and self.error:
            raise ValueError(f"only SIGNAL_REFUSED carries an error, got {self.error!r}")
        if self.result is StopResult.SIGKILL_SENT and not self.escalated_to_sigkill:
            raise ValueError("SIGKILL_SENT requires escalated_to_sigkill=True")
        if self.result is StopResult.EXITED and self.escalated_to_sigkill:
            raise ValueError(
                "EXITED means SIGTERM sufficed; it cannot have escalated to SIGKILL"
            )

    @property
    def stopped(self) -> bool | None:
        """Three-valued: True / False / ``None`` for *unknown*.

        ``None`` is the ``SIGKILL_SENT`` case and callers MUST handle it as
        its own branch -- ``if outcome.stopped:`` silently treats unknown as
        failure and ``if outcome.stopped is not False:`` silently treats it
        as success. Both are the collapse this property exists to prevent.
        """
        if self.result is StopResult.EXITED:
            return True
        if self.result is StopResult.SIGNAL_REFUSED:
            return False
        return None


def _board_clear_pidfile() -> None:
    """Remove the pidfile if present. Idempotent, never raises."""
    pf = _board_pidfile()
    try:
        if pf.exists():
            pf.unlink()
    except OSError:
        pass


def stop_board_process(pid: int, timeout: float) -> StopOutcome:
    """SIGTERM ``pid``, wait up to ``timeout`` seconds, then SIGKILL.

    THE ONE implementation of the board's stop sequence, shared by
    ``board stop``, ``board start --force`` and ``gui serve --force``. It
    takes an ALREADY-RESOLVED pid: resolution is :func:`_board_resolve_pid`'s
    job, and keeping the two apart is what stops a caller from ever passing
    a raw port here and killing whoever happens to hold it.

    Returns a :class:`StopOutcome` instead of raising, because the two
    callers need different words for the same refusal -- ``board stop`` says
    "could not SIGTERM", ``--force`` has to add "so I will not bind a port
    that is still held". Formatting a click exception here would force one
    of them to re-parse the other's message.

    Clears the pidfile on every path EXCEPT a refused signal: if the kernel
    would not let us signal the process, the process is still there and the
    pidfile is still true. (The foreground server's own ``finally`` also
    unlinks it; both are idempotent.)

    OS LIMIT (not a bug): a cross-user kill is denied by the kernel. That
    surfaces as ``SIGNAL_REFUSED`` with the OS error in ``error``.
    """
    import os as _os
    import signal as _signal
    import time as _time

    try:
        _os.kill(pid, _signal.SIGTERM)
    except OSError as exc:
        return StopOutcome(
            pid=pid,
            result=StopResult.SIGNAL_REFUSED,
            timeout=timeout,
            escalated_to_sigkill=False,
            error=f"could not SIGTERM pid {pid}: {exc}",
        )

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if not _board_pid_alive(pid):
            _board_clear_pidfile()
            return StopOutcome(
                pid=pid,
                result=StopResult.EXITED,
                timeout=timeout,
                escalated_to_sigkill=False,
            )
        _time.sleep(0.1)

    try:
        _os.kill(pid, _signal.SIGKILL)
    except OSError as exc:
        return StopOutcome(
            pid=pid,
            result=StopResult.SIGNAL_REFUSED,
            timeout=timeout,
            escalated_to_sigkill=True,
            error=f"could not SIGKILL pid {pid}: {exc}",
        )
    _board_clear_pidfile()
    return StopOutcome(
        pid=pid,
        result=StopResult.SIGKILL_SENT,
        timeout=timeout,
        escalated_to_sigkill=True,
    )


# EOF
