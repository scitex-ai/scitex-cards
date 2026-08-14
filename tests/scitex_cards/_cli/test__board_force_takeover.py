#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``--force`` on the verbs that start a board: take over, don't nag.

OPERATOR REQUEST, 2026-08-14 (Telegram). He ran ``scitex-cards gui serve
--force``, got "No such option '--force'", and asked for it in two sentences
that are really two separate requirements:

  「stop するのめんどくさいので」 — if a board is already up, ``--force``
  stops it and serves. He should not have to remember ``gui stop`` first.

  「あとはなければ通すように。stop ではないので」 — if NOTHING is up,
  ``--force`` just serves. It is a TAKEOVER, not a stop verb, so an absent
  incumbent is the ordinary case and must not be an error. That is the half a
  "force" flag written carelessly gets wrong, and it is the half he spelled
  out, unprompted, in the same message.

BOTH DOORS ARE TESTED HERE ON PURPOSE. ``gui serve`` and ``board start`` are
two entrances to one Django app, and this repo has already paid for treating
them as one-off surfaces: the unconfigured-store refusal was written for
``gui serve`` alone and ``board start`` stayed unguarded for three days
(tests/scitex_cards/test__board_start_refuses_an_unconfigured_store.py). A
takeover implemented at one door will be missing, or subtly different, at the
other.

WHY THE END-TO-END TESTS RUN A CHILD INTERPRETER. Anything that reaches the
serve step cannot be exercised in-process: Django's runserver calls
``os._exit(1)`` on a bind error, which would take the whole pytest session
down instead of failing one test. The in-process tests therefore stop at the
takeover decision, and :class:`TestForceActuallyServes` runs the real CLI as a
subprocess for the half a dry run cannot show.

No mocks (STX-NM / PA-306); the process fixtures live in ``conftest.py``.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._board_force import (
    announce_takeover,
    force_stop_running_board,
)
from scitex_cards._cli._board_proc import _board_write_pid, stop_board_process

#: Any port at all — every in-process test below stops before binding, and
#: ``force_stop_running_board`` only consults the port when the pidfile is
#: stale, which these tests never make it.
UNUSED_PORT = 9


# === the shared takeover helper =============================================


class TestForceStopWithNothingRunning:
    """「なければ通すように」 — no incumbent is SUCCESS, not an error."""

    def test_returns_none_when_no_board_is_running(self, pidfile_path):
        # Arrange — no pidfile at all.
        port = UNUSED_PORT
        # Act
        stopped = force_stop_running_board(port, timeout=0.3)
        # Assert
        assert stopped is None

    def test_does_not_raise_when_no_board_is_running(self, pidfile_path):
        # Arrange
        port = UNUSED_PORT
        # Act — the whole point of the request: this must simply pass through.
        outcome = force_stop_running_board(port, timeout=0.3)
        # Assert — no exception AND nothing claimed stopped.
        assert outcome is None


class TestForceStopWithABoardRunning:
    """An incumbent is stopped, and the caller learns which pid went."""

    def test_stops_the_running_board(self, pidfile_path, board_process):
        # Arrange — the pidfile names a real live process.
        _board_write_pid(board_process.pid)
        # Act
        force_stop_running_board(UNUSED_PORT, timeout=5.0)
        # Assert
        assert board_process.alive is False

    def test_returns_the_pid_it_stopped(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        stopped = force_stop_running_board(UNUSED_PORT, timeout=5.0)
        # Assert
        assert stopped == board_process.pid

    def test_clears_the_pidfile_before_serving(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        force_stop_running_board(UNUSED_PORT, timeout=5.0)
        # Assert
        assert not pidfile_path.exists()


class TestAnnounceTakeoverRefusal:
    """A refused stop RAISES; the caller must never go on to bind the port.

    Driven with a REAL ``StopOutcome`` built by ``stop_board_process`` from a
    real ESRCH, because the refusal is unreachable through a CLI invocation:
    ``_board_resolve_pid`` drops every pid that ``kill(pid, 0)`` rejects, so
    the cross-user case needs a second user and the resolve-then-vanish race
    needs a scheduler hook. Mocking the outcome would assert a belief about
    our own dataclass; this asserts the policy that reads it.
    """

    @pytest.fixture
    def refused(self, reaped_pid):
        """A REAL refusal: the kernel's ESRCH for a child already reaped."""
        return stop_board_process(reaped_pid, 0.3)

    def test_a_refused_stop_raises(self, refused):
        # Arrange
        port = 8051
        # Act
        announce = partial(announce_takeover, refused, port)
        # Assert
        with pytest.raises(click.ClickException):
            announce()

    def test_the_refusal_names_the_pid(self, refused):
        # Arrange
        port = 8051
        # Act
        announce = partial(announce_takeover, refused, port)
        # Assert
        with pytest.raises(click.ClickException, match=re.escape(str(refused.pid))):
            announce()

    def test_the_refusal_names_the_next_step(self, refused):
        """An error that only states what broke is half-written."""
        # Arrange
        port = 8051
        # Act
        announce = partial(announce_takeover, refused, port)
        # Assert
        with pytest.raises(click.ClickException, match=re.escape("ps -o user=")):
            announce()

    def test_the_refusal_names_the_port_it_will_not_bind(self, refused):
        # Arrange
        port = 8051
        # Act
        announce = partial(announce_takeover, refused, port)
        # Assert
        with pytest.raises(click.ClickException, match=re.escape("8051")):
            announce()


# === gui serve --force ======================================================


class TestGuiServeForceFlag:
    """The exact option the operator typed, on the verb he typed it on."""

    def test_serve_accepts_the_force_option(self):
        # Arrange — the invocation that printed "No such option '--force'".
        argv = ["gui", "serve", "--force", "--dry-run"]
        # Act
        result = CliRunner().invoke(main, argv)
        # Assert
        assert result.exit_code == 0

    def test_force_with_nothing_running_does_not_error(self, pidfile_path):
        # Arrange — 「なければ通すように」: no pidfile, no board.
        argv = ["gui", "serve", "--force", "--dry-run"]
        # Act
        result = CliRunner().invoke(main, argv)
        # Assert
        assert result.exit_code == 0

    def test_force_with_nothing_running_says_it_would_just_serve(self, pidfile_path):
        # Arrange
        argv = ["gui", "serve", "--force", "--dry-run"]
        # Act
        result = CliRunner().invoke(main, argv)
        # Assert
        assert "no board is running" in result.output

    def test_force_with_nothing_running_claims_no_kill(self, pidfile_path):
        # Arrange
        argv = ["gui", "serve", "--force", "--dry-run"]
        # Act
        result = CliRunner().invoke(main, argv)
        # Assert
        assert "would stop pid" not in result.output


class TestGuiServeWithoutForce:
    """REGRESSION GUARD: the old refusal is untouched when --force is absent."""

    def test_a_running_board_still_refuses(self, pidfile_path):
        # Arrange — a live pidfile (this test runner).
        _board_write_pid(os.getpid())
        # Act
        result = CliRunner().invoke(main, ["gui", "serve"])
        # Assert
        assert result.exit_code != 0

    def test_the_refusal_still_names_the_pid(self, pidfile_path):
        # Arrange
        _board_write_pid(os.getpid())
        expected = f"already running (pid {os.getpid()})"
        # Act
        result = CliRunner().invoke(main, ["gui", "serve"])
        # Assert
        assert expected in result.output

    def test_the_refusal_now_also_mentions_force(self, pidfile_path):
        """The remedy line should name the flag that skips the two-step."""
        # Arrange
        _board_write_pid(os.getpid())
        # Act
        result = CliRunner().invoke(main, ["gui", "serve"])
        # Assert
        assert "--force" in result.output


class TestGuiServeForceDryRun:
    """`--force --dry-run` kills NOTHING and says what it would have killed."""

    def test_dry_run_leaves_the_board_alive(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        CliRunner().invoke(main, ["gui", "serve", "--force", "--dry-run"])
        # Assert
        assert board_process.alive is True

    def test_dry_run_names_the_pid_it_would_stop(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        expected = f"would stop pid {board_process.pid}"
        # Act
        result = CliRunner().invoke(main, ["gui", "serve", "--force", "--dry-run"])
        # Assert
        assert expected in result.output

    def test_dry_run_still_reports_the_serve(self, pidfile_path, board_process):
        # Arrange — the stop is an ADDITION to the dry run, not a takeover of
        # it; the operator still needs to see which port it would bind.
        _board_write_pid(board_process.pid)
        # Act
        result = CliRunner().invoke(main, ["gui", "serve", "--force", "--dry-run"])
        # Assert
        assert "would serve the board" in result.output

    def test_dry_run_leaves_the_pidfile_intact(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        CliRunner().invoke(main, ["gui", "serve", "--force", "--dry-run"])
        # Assert
        assert pidfile_path.exists()


# === board start --force (the OTHER door) ===================================


class TestBoardStartForceFlag:
    """Same semantics at the other entrance, out of the same helper."""

    def test_start_accepts_the_force_option(self):
        # Arrange
        argv = ["board", "start", "--force", "--dry-run"]
        # Act
        result = CliRunner().invoke(main, argv)
        # Assert
        assert result.exit_code == 0

    def test_force_with_nothing_running_does_not_error(self, pidfile_path):
        # Arrange — 「なければ通すように」 at the second door too.
        argv = ["board", "start", "--force", "--dry-run"]
        # Act
        result = CliRunner().invoke(main, argv)
        # Assert
        assert result.exit_code == 0

    def test_dry_run_leaves_the_board_alive(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        CliRunner().invoke(main, ["board", "start", "--force", "--dry-run"])
        # Assert
        assert board_process.alive is True

    def test_dry_run_names_the_pid_it_would_stop(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        expected = f"would stop pid {board_process.pid}"
        # Act
        result = CliRunner().invoke(main, ["board", "start", "--force", "--dry-run"])
        # Assert
        assert expected in result.output


class TestBoardStartWithoutForce:
    """REGRESSION GUARD at the second door."""

    def test_a_running_board_still_refuses(self, pidfile_path):
        # Arrange
        _board_write_pid(os.getpid())
        # Act
        result = CliRunner().invoke(main, ["board", "start", "--dry-run"])
        # Assert
        assert result.exit_code != 0

    def test_the_refusal_still_names_the_pid(self, pidfile_path):
        # Arrange
        _board_write_pid(os.getpid())
        expected = f"already running (pid {os.getpid()})"
        # Act
        result = CliRunner().invoke(main, ["board", "start", "--dry-run"])
        # Assert
        assert expected in result.output


# === end to end: it actually SERVES afterwards ==============================

#: Run the REAL cli in a child interpreter — see the module docstring for why
#: the serve step can never be reached in-process.
_CHILD_CLI = "from scitex_cards._cli import main; main()"

_needs_django = pytest.mark.skipif(
    importlib.util.find_spec("django") is None,
    reason="the board needs the web extra: pip install scitex-cards[all]",
)


def _terminate(proc: subprocess.Popen) -> None:
    """Best-effort shutdown of the served child. Never raises.

    Duplicated from ``conftest.terminate`` rather than imported: the tests
    tree carries no ``__init__.py``, so ``from .conftest import ...`` is not
    an available spelling and a conftest is not an import target.
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _serve_child(argv: list[str], log: Path) -> subprocess.Popen:
    """Start ``scitex-cards <argv>`` for real, logging to ``log``.

    Inherits ``os.environ``, which already carries the tmp
    ``SCITEX_TODO_BOARD_PIDFILE`` from the ``pidfile_path`` fixture and the
    suite's scratch store — so the child writes its pidfile where the test can
    see it and touches no real board. Output goes to a FILE rather than a
    pipe nobody drains, which would deadlock the child at 64 KB.
    """
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD_CLI, *argv],
        env=os.environ.copy(),
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
    )


def _await_pidfile(pidfile: Path, expected: int, timeout: float = 120.0) -> bool:
    """Wait for ``pidfile`` to name ``expected``.

    ``_board_run_server`` writes the pidfile immediately before handing off to
    Django's serve loop, so this landing IS "it went on to serve" — observable
    without waiting for, or racing against, a live socket.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pidfile.read_text().strip() == str(expected):
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


@_needs_django
class TestForceActuallyServes:
    """The half a dry run cannot show: after the takeover, a board comes up.

    Everything above stops at the takeover decision. That is the right seam
    for most of the contract, but the operator asked for ONE command that
    leaves him with a running board — so at least one test has to watch a real
    ``gui serve --force`` reach the serve step, with and without an incumbent.
    """

    def test_force_serves_when_nothing_is_running(self, pidfile_path, tmp_path):
        # Arrange — 「なければ通すように」, end to end: no board anywhere.
        argv = ["gui", "serve", "--force", "--port", "0"]
        child = _serve_child(argv, tmp_path / "serve.log")
        # Act
        try:
            served = _await_pidfile(pidfile_path, child.pid)
        finally:
            _terminate(child)
        # Assert
        assert served is True

    def test_force_stops_the_incumbent_board(
        self, pidfile_path, board_process, tmp_path
    ):
        # Arrange — a real live process recorded as the running board.
        _board_write_pid(board_process.pid)
        argv = ["gui", "serve", "--force", "--port", "0"]
        child = _serve_child(argv, tmp_path / "serve.log")
        # Act
        try:
            board_process.await_exit()
        finally:
            _terminate(child)
        # Assert
        assert board_process.alive is False

    def test_force_serves_after_stopping_the_incumbent(
        self, pidfile_path, board_process, tmp_path
    ):
        # Arrange
        _board_write_pid(board_process.pid)
        argv = ["gui", "serve", "--force", "--port", "0"]
        child = _serve_child(argv, tmp_path / "serve.log")
        # Act — the pidfile ends up naming the NEW process, not the old one.
        try:
            served = _await_pidfile(pidfile_path, child.pid)
        finally:
            _terminate(child)
        # Assert
        assert served is True


# EOF
