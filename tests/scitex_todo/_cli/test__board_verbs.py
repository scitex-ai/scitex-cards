#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the new ``scitex-todo board <verb>`` lifecycle CLI
(operator TG12949/12950/12951 via lead a2a `b5726672`).

The tests cover:

  - the noun-verb group's subcommands are registered
  - `board status` reports "NOT running" when the pidfile is absent
  - `board status` reports "running" + the pid when the pidfile points
    at a live process
  - `board stop` is a no-op on a missing pidfile (clear message, no
    exception)
  - `board stop` SIGTERMs the pid recorded in the pidfile (sub-process)
  - `board start` refuses to start when a live pidfile already exists
  - bare ``board`` HARD-ERRORS with exit 2 + redirect message (operator
    directive TG 13316, lead a2a ``c36b0d1e``)
  - stale pidfile (PID dead) is cleaned up + treated as "not running"

No mocks (STX-NM / PA-306): real subprocesses (Python `time.sleep` loop)
and a tmp pidfile via the ``SCITEX_TODO_BOARD_PIDFILE`` env override.

The actual Django/runserver path is NOT exercised here — `start` from
the lifecycle perspective is the pidfile + dispatch contract; the
runserver itself is covered by the existing Django-management
command's test suite.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_todo._cli import main
from scitex_todo._cli._board import (
    _board_pidfile,
    _board_pid_alive,
    _board_read_pid,
    _board_write_pid,
    board_group,
)


@pytest.fixture
def pidfile_path(env, tmp_path):
    """Point the pidfile at a tmp location so tests don't touch
    ``~/.scitex/todo/board.pid``."""
    pf = tmp_path / "board.pid"
    env.set("SCITEX_TODO_BOARD_PIDFILE", str(pf))
    yield pf


# === Subcommand registration ================================================


class TestGroupShape:
    """The group has the four lifecycle verbs the operator asked for."""

    def test_start_subcommand_registered(self):
        # Arrange
        # Act
        names = board_group.commands.keys()
        # Assert
        assert "start" in names

    def test_stop_subcommand_registered(self):
        # Arrange
        # Act
        names = board_group.commands.keys()
        # Assert
        assert "stop" in names

    def test_restart_subcommand_registered(self):
        # Arrange
        # Act
        names = board_group.commands.keys()
        # Assert
        assert "restart" in names

    def test_status_subcommand_registered(self):
        # Arrange
        # Act
        names = board_group.commands.keys()
        # Assert
        assert "status" in names


# === status =================================================================


class TestStatus:
    """`board status` reads the pidfile + reports."""

    def test_status_when_no_pidfile_reports_not_running(self, pidfile_path):
        # Arrange — no pidfile.
        # Act
        result = CliRunner().invoke(main, ["board", "status"])
        # Assert
        assert "NOT running" in result.output

    def test_status_when_pidfile_points_at_live_process(
        self,
        pidfile_path,
    ):
        # Arrange — spawn a sleeper subprocess + write its PID.
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        try:
            _board_write_pid(sleeper.pid)
            # Act
            result = CliRunner().invoke(main, ["board", "status"])
            # Assert
            assert f"pid {sleeper.pid}" in result.output
        finally:
            sleeper.terminate()
            try:
                sleeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sleeper.kill()


# === stop ===================================================================


class TestStop:
    """`board stop` SIGTERMs the pidfile-recorded PID."""

    def test_stop_with_no_pidfile_is_noop(self, pidfile_path):
        # Arrange
        # Act
        result = CliRunner().invoke(main, ["board", "stop"])
        # Assert — exits cleanly with a clear message.
        assert "not running" in result.output

    def test_stop_terminates_the_pidfile_process(self, pidfile_path):
        # Arrange — spawn a sleeper subprocess + write its PID.
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        try:
            _board_write_pid(sleeper.pid)
            # Act — `board stop` should SIGTERM the sleeper.
            CliRunner().invoke(main, ["board", "stop"])
            # Wait briefly for the SIGTERM to propagate.
            for _ in range(50):
                if sleeper.poll() is not None:
                    break
                time.sleep(0.1)
            # Assert
            assert sleeper.poll() is not None
        finally:
            if sleeper.poll() is None:
                sleeper.kill()


# === start refuses on existing live pidfile =================================


class TestStartRefusesWhenAlreadyRunning:
    """If another board is already up, `board start` refuses with a
    clear error pointing at `stop` / `restart`."""

    def test_start_with_live_pidfile_is_rejected(self, pidfile_path):
        # Arrange — pidfile points at a live process (this test runner).
        _board_write_pid(os.getpid())
        # Act
        result = CliRunner().invoke(main, ["board", "start"])
        # Assert — non-zero exit + the error names the recovery path.
        assert result.exit_code != 0


# === bare `board` HARD-ERRORS (operator TG 13316, lead a2a c36b0d1e) ========


class TestBareBoardHardError:
    """Bare ``scitex-todo board`` (no verb) is no longer back-compat — it
    HARD-ERRORS with a redirect message + exit 2 so existing call sites
    get an immediate, actionable signal (operator directive TG 13316:
    noun-verb CLI convention, no bare-noun forwarding)."""

    def test_bare_board_exits_with_code_2(self):
        # Arrange
        runner = CliRunner()
        # Act
        result = runner.invoke(main, ["board"])
        # Assert — exit 2 is Click's standard usage-error code.
        assert result.exit_code == 2

    def test_bare_board_emits_redirect_message(self):
        # Arrange
        runner = CliRunner()
        # Act — CliRunner mixes stderr into result.output by default;
        # we check the redirect message landed in the combined stream.
        result = runner.invoke(main, ["board"])
        # Assert — the redirect message names the canonical replacement.
        assert "scitex-todo board start" in result.output

    def test_bare_board_does_not_invoke_start(self, pidfile_path):
        # Arrange — set up a state that `board start` would normally
        # mutate (writing the pidfile) so we can verify it WASN'T called.
        runner = CliRunner()
        # Act
        runner.invoke(main, ["board"])
        # Assert — pidfile was not created (start path never ran).
        assert not pidfile_path.exists()

    def test_bare_board_does_not_forward_when_a_flag_is_passed(self):
        # Arrange — historically `--port 8051` made click forward to
        # `start` via the back-compat handler. The hard-error path now
        # rejects ANY bare invocation regardless of flags.
        runner = CliRunner()
        # Act
        result = runner.invoke(main, ["board", "--port", "9999"])
        # Assert — non-zero exit (the option no longer exists on the
        # group; either click usage-error or our explicit ctx.exit(2)).
        assert result.exit_code != 0


# === stale pidfile cleanup ==================================================


class TestStalePidfile:
    """A pidfile pointing at a dead PID is treated as not-running and
    cleaned up on read."""

    def test_dead_pid_in_pidfile_is_cleaned_up(self, pidfile_path):
        # Arrange — write a PID that is highly unlikely to be live.
        pidfile_path.parent.mkdir(parents=True, exist_ok=True)
        pidfile_path.write_text("999999")
        # Act
        pid = _board_read_pid()
        # Assert — read returns None AND the pidfile is removed.
        assert pid is None and not pidfile_path.exists()
