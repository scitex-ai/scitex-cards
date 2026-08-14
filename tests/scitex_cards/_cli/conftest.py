#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real-process fixtures for the board-lifecycle CLI tests.

The board lifecycle is made of exactly three process states, and each one is
awkward to produce by accident:

  * a live board that exits when told (:func:`board_process`)
  * a board that will NOT die from SIGTERM, so the escalation runs
    (:func:`zombie_pid`)
  * a pid the kernel refuses to signal at all (:func:`reaped_pid`)

They live here rather than in one test module because
``test__board_stop_process.py`` and ``test__board_force_takeover.py`` are one
subject split across two files (the repo's 512-line cap), and a stand-in
process whose semantics drift between the two halves would make the halves
disagree about what "stopped" means.

No mocks (STX-NM / PA-306): real subprocesses, a real ``os.fork`` zombie, and
a real reaped pid whose signal the kernel really refuses.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scitex_cards._cli._board_proc import _board_pid_alive

#: A supervisor that starts one sleeper, prints its pid, then blocks in
#: ``wait()`` on it — so the sleeper is REAPED THE INSTANT it exits.
_SUPERVISOR = (
    "import subprocess, sys\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(p.pid)\n"
    "sys.stdout.flush()\n"
    "p.wait()\n"
)


def terminate(proc: subprocess.Popen) -> None:
    """Best-effort shutdown of a helper subprocess. Never raises."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class BoardProcess:
    """A live stand-in for a running board, with a parent that reaps it.

    THE EXTRA PROCESS IS NOT CEREMONY, and leaving it out is a measured
    mistake rather than a hypothetical one. A plain ``subprocess.Popen`` child
    of the TEST process becomes a ZOMBIE the moment it exits and stays one
    until pytest happens to call ``.poll()`` — and ``kill(zombie, 0)``
    succeeds, so the stop sequence watches it "stay alive" for the whole
    timeout and escalates to SIGKILL every single time. The first draft of
    these tests did exactly that and reported the graceful path broken.

    That is an artefact of pytest's process tree, not of the board: the real
    board is never a child of whatever stops it, so its exit is reaped
    immediately by init or by its own launcher. Giving the stand-in a parent
    that sits in ``wait()`` restores the situation the code actually runs in.
    """

    def __init__(self) -> None:
        self.supervisor = subprocess.Popen(
            [sys.executable, "-c", _SUPERVISOR],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.pid = int(self.supervisor.stdout.readline().strip())

    @property
    def alive(self) -> bool:
        """Liveness by ``kill(pid, 0)`` — the same probe the code uses."""
        return _board_pid_alive(self.pid)

    def await_exit(self, tries: int = 200) -> None:
        for _ in range(tries):
            if not self.alive:
                return
            time.sleep(0.05)

    def cleanup(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass
        terminate(self.supervisor)


@pytest.fixture
def pidfile_path(env, tmp_path):
    """Redirect the board pidfile at a tmp path so tests never touch the real one."""
    pf = tmp_path / "board.pid"
    env.set("SCITEX_TODO_BOARD_PIDFILE", str(pf))
    yield pf


@pytest.fixture
def board_process():
    """A live process that really exits on SIGTERM (see :class:`BoardProcess`)."""
    board = BoardProcess()
    try:
        yield board
    finally:
        board.cleanup()


def _proc_state(pid: int) -> str | None:
    """The single-letter /proc state ('Z' for a zombie), or None if gone."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("State:"):
                return line.split()[1]
    except OSError:
        return None
    return None


@pytest.fixture
def zombie_pid():
    """A pid that ACCEPTS every signal and dies from none of them.

    A zombie is the only such process a test can create, and it is exactly
    the state the SIGKILL escalation exists for: ``kill(z, 0)`` keeps
    succeeding, so the aliveness poll runs to the timeout without any
    sleep-based approximation of "a board ignoring SIGTERM".

    ``os.fork`` rather than ``subprocess``: the subprocess module reaps its
    own finished children opportunistically whenever a new ``Popen`` is made,
    which would un-zombie the fixture mid-test.
    """
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child never runs test code
        os._exit(0)
    try:
        for _ in range(200):
            if _proc_state(pid) == "Z":
                break
            time.sleep(0.01)
        else:
            raise AssertionError(f"forked child {pid} never became a zombie")
        yield pid
    finally:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


@pytest.fixture
def reaped_pid() -> int:
    """A pid that is GUARANTEED dead: a child run to completion and reaped.

    Signalling it produces a real ``ProcessLookupError`` (ESRCH) from the
    kernel — the only signal refusal a single-user test process can honestly
    provoke, and therefore the only way to cover the refusal branch without
    mocking ``os.kill``.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# THERE IS DELIBERATELY NO `free_port` FIXTURE HERE.
#
# One existed, and it was both dead and wrong. Dead: the end-to-end tests ask
# for ``--port 0`` and let the KERNEL choose, so nothing consumed it. Wrong:
# it acquired a socket, bound it to find a free port, closed it and RETURNED
# the number — which STX-TQ005 flagged, correctly, as a fixture acquiring an
# external resource it can never tear down on a failing test.
#
# The remedy is not to `yield` the socket. A bind-then-release port is a
# TOCTOU race with a name: the port is free at the moment it is measured and
# any process may take it before the board binds. ``--port 0`` has neither
# problem, because the kernel picks and binds in one step. So the acquisition
# is gone rather than wrapped.


# EOF
