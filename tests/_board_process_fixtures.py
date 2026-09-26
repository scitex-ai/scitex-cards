#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real-process fixtures for the board-lifecycle CLI tests — a PLUGIN, not a conftest.

The board lifecycle is made of exactly three process states, and each one is
awkward to produce by accident:

  * a live board that exits when told (:func:`board_process`)
  * a board that will NOT die from SIGTERM, so the escalation runs
    (:func:`zombie_pid`)
  * a pid the kernel refuses to signal at all (:func:`reaped_pid`)
  * and the pidfile redirection every one of them needs (:func:`pidfile_path`)

No mocks (STX-NM / PA-306): real subprocesses, a real ``os.fork`` zombie, and a
real reaped pid whose signal the kernel really refuses.

=== WHY THIS IS A PLUGIN AND NOT A ``_cli/conftest.py`` FIXTURE =============

These four fixtures used to live in ``tests/scitex_cards/_cli/conftest.py``.
They no longer can, and the reason is a mechanism rather than a taste:

pytest 9 parses a conftest's fixtures LAZILY, when the ``Directory`` collector
for the conftest's directory is collected, and binds every ``FixtureDef`` it
finds to THAT ``Directory`` NODE OBJECT
(``FixtureManager.pytest_make_collect_report`` → ``parsefactories(holder,
node=collector)``).  Resolution then matches by NODE IDENTITY:
``FixtureManager._matchfactories`` keeps a def only if
``fixturedef.node in set(item.iter_parents())``.

When pytest is handed explicit FILE arguments, ``Session.collect()``
re-collects the argument's parent directory with ``handle_dupes=False``
(``len(matchparts) == 1 and matchparts[0].is_file()``) every single time —
so ONE argument that sits directly in ``tests/scitex_cards/`` creates a FRESH
``Directory`` node for ``tests/scitex_cards/_cli``.  The pending-conftest pop
has already happened by then (the plugin is registered once, so no new pending
entry is added), so the fixtures stay bound to the first ``_cli`` Directory
node and every item collected under a later one looks up a fixture that exists
but is invisible to it:

    ERROR at setup of TestGracefulStop.test_a_graceful_stop_reports_exited
    E   fixture 'pidfile_path' not found
    >   available fixtures: ...env, monkeypatch, new_store, tmp_path, ...
        (not one of them from _cli/conftest.py)

Reproduced 2026-09-17 with three arguments and no xdist, on pytest 9.1.1:

    pytest tests/scitex_cards/_cli/test__board_force_takeover.py \\
           tests/scitex_cards/test__release_workflow_uses_xdist.py \\
           tests/scitex_cards/_cli/test__board_stop_process.py -p no:anyio

It is the shape CI hits: the org matrix builds its argument list ordered by
descending test count, so ``tests/scitex_cards/test__*.py`` and
``tests/scitex_cards/_cli/test__*.py`` interleave, and the py3.11 leg died with
exactly this error in exactly these two files.

Fixtures registered by a NON-conftest plugin are parsed with ``node=session``
(``FixtureManager.pytest_plugin_registered`` → ``parsefactories(holder=plugin,
node=self.session)``), and the Session node is the ancestor of every item in
every collection generation.  Session-scoped binding is therefore the only
placement that cannot be undone by an argument order — which is also why the
suite-wide fixtures in ``tests/conftest.py`` kept working through the same
failure.  ``tests/conftest.py`` registers this module as a plugin in
``pytest_collection`` (after the fixture manager exists, before collection).
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
    env.set("SCITEX_CARDS_BOARD_PIDFILE", str(pf))
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


# EOF
