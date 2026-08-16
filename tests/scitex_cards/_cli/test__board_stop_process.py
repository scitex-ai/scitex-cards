#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``stop_board_process`` — the board's ONE stop sequence, and its answer shape.

The SIGTERM → poll → SIGKILL escalation used to be written inline in ``board
stop``'s click command body, which meant the only way to reach it was to type
that verb. When the operator asked for ``gui serve --force`` on 2026-08-14
(「stop するのめんどくさいので」), a second copy of that escalation was the
obvious shortcut — and two escalation sequences racing for one pidfile is the
exact failure the ``gui`` group was written to avoid. So the sequence moved to
:mod:`scitex_cards._cli._board_proc` and every door now calls it.

TWO THINGS ARE PINNED HERE:

  1. the extracted function's DECLARED SHAPE — a validated dataclass with
     named fields and a three-valued ``stopped``, per the constitution's
     answer-shape rule, rather than a bare bool a caller has to interpret;
  2. that ``board stop`` did not change a single byte of its output in the
     move. Its messages are what agents grep the fleet's logs for, so they
     are asserted EXACTLY, not by substring of a paraphrase.

No mocks (STX-NM / PA-306). The process states come from the real-process
fixtures in ``conftest.py``: a live board that exits, a zombie that will not,
and a reaped pid the kernel refuses to signal.
"""

from __future__ import annotations

from functools import partial

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._board_proc import (
    StopOutcome,
    StopResult,
    _board_write_pid,
    stop_board_process,
)

# === the graceful path ======================================================


class TestGracefulStop:
    """SIGTERM sufficed, and every field says so consistently."""

    def test_a_graceful_stop_reports_exited(self, pidfile_path, board_process):
        # Arrange
        pid = board_process.pid
        # Act
        outcome = stop_board_process(pid, 5.0)
        # Assert
        assert outcome.result is StopResult.EXITED

    def test_stopped_is_true(self, pidfile_path, board_process):
        # Arrange
        pid = board_process.pid
        # Act
        outcome = stop_board_process(pid, 5.0)
        # Assert
        assert outcome.stopped is True

    def test_did_not_escalate(self, pidfile_path, board_process):
        # Arrange
        pid = board_process.pid
        # Act
        outcome = stop_board_process(pid, 5.0)
        # Assert
        assert outcome.escalated_to_sigkill is False

    def test_names_the_pid_it_acted_on(self, pidfile_path, board_process):
        # Arrange
        pid = board_process.pid
        # Act
        outcome = stop_board_process(pid, 5.0)
        # Assert
        assert outcome.pid == pid

    def test_carries_no_error(self, pidfile_path, board_process):
        # Arrange
        pid = board_process.pid
        # Act
        outcome = stop_board_process(pid, 5.0)
        # Assert
        assert outcome.error is None

    def test_the_process_is_really_gone(self, pidfile_path, board_process):
        # Arrange
        pid = board_process.pid
        # Act
        stop_board_process(pid, 5.0)
        # Assert
        assert board_process.alive is False

    def test_clears_the_pidfile(self, pidfile_path, board_process):
        # Arrange — the pidfile names the process we are about to stop.
        _board_write_pid(board_process.pid)
        # Act
        stop_board_process(board_process.pid, 5.0)
        # Assert
        assert not pidfile_path.exists()


# === the escalation path ====================================================


class TestSigkillEscalation:
    """A process that will not die from SIGTERM escalates, and admits it."""

    def test_timeout_escalates_to_sigkill(self, pidfile_path, zombie_pid):
        # Arrange
        timeout = 0.3
        # Act
        outcome = stop_board_process(zombie_pid, timeout)
        # Assert
        assert outcome.result is StopResult.SIGKILL_SENT

    def test_sets_the_sigkill_flag(self, pidfile_path, zombie_pid):
        # Arrange
        timeout = 0.3
        # Act
        outcome = stop_board_process(zombie_pid, timeout)
        # Assert
        assert outcome.escalated_to_sigkill is True

    def test_leaves_stopped_unknown(self, pidfile_path, zombie_pid):
        """THE THREE-VALUED SIGNAL. SIGKILL was sent and the exit was never
        re-checked, so answering True would be reporting an observation the
        code did not make."""
        # Arrange
        timeout = 0.3
        # Act
        outcome = stop_board_process(zombie_pid, timeout)
        # Assert
        assert outcome.stopped is None

    def test_carries_no_error(self, pidfile_path, zombie_pid):
        """Unknown is not failure: only a REFUSED signal carries an error."""
        # Arrange
        timeout = 0.3
        # Act
        outcome = stop_board_process(zombie_pid, timeout)
        # Assert
        assert outcome.error is None


# === the refusal path =======================================================


class TestSignalRefused:
    """A kernel refusal is REPORTED, not raised, and never read as done."""

    def test_reports_signal_refused(self, pidfile_path, reaped_pid):
        # Arrange
        timeout = 0.3
        # Act
        outcome = stop_board_process(reaped_pid, timeout)
        # Assert
        assert outcome.result is StopResult.SIGNAL_REFUSED

    def test_stopped_is_false(self, pidfile_path, reaped_pid):
        # Arrange
        timeout = 0.3
        # Act
        outcome = stop_board_process(reaped_pid, timeout)
        # Assert
        assert outcome.stopped is False

    def test_names_the_signal_and_the_pid(self, pidfile_path, reaped_pid):
        # Arrange — the wording `board stop` has always printed.
        expected = f"could not SIGTERM pid {reaped_pid}: "
        # Act
        outcome = stop_board_process(reaped_pid, 0.3)
        # Assert
        assert outcome.error.startswith(expected)

    def test_leaves_the_pidfile_alone(self, pidfile_path, reaped_pid):
        """If we could not signal it, it may still be there — and so is the
        pidfile's claim about it. Deleting it would erase the only record of
        what has to be stopped by hand."""
        # Arrange
        _board_write_pid(reaped_pid)
        # Act
        stop_board_process(reaped_pid, 0.3)
        # Assert
        assert pidfile_path.exists()


# === the outcome validates itself ===========================================


class TestOutcomeValidator:
    """A malformed outcome fails where it is BUILT, not three layers on."""

    def test_exited_cannot_claim_escalation(self):
        # Arrange
        kwargs = dict(pid=1234, result=StopResult.EXITED, timeout=1.0)
        # Act
        build = partial(StopOutcome, escalated_to_sigkill=True, **kwargs)
        # Assert
        with pytest.raises(ValueError):
            build()

    def test_refusal_without_an_error_is_rejected(self):
        # Arrange
        kwargs = dict(pid=1234, result=StopResult.SIGNAL_REFUSED, timeout=1.0)
        # Act
        build = partial(StopOutcome, escalated_to_sigkill=False, **kwargs)
        # Assert
        with pytest.raises(ValueError):
            build()

    def test_success_carrying_an_error_is_rejected(self):
        # Arrange
        kwargs = dict(pid=1234, result=StopResult.EXITED, timeout=1.0)
        # Act
        build = partial(
            StopOutcome, escalated_to_sigkill=False, error="boom", **kwargs
        )
        # Assert
        with pytest.raises(ValueError):
            build()

    def test_a_non_result_verdict_is_rejected(self):
        # Arrange
        kwargs = dict(pid=1234, timeout=1.0, escalated_to_sigkill=False)
        # Act
        build = partial(StopOutcome, result="exited", **kwargs)
        # Assert
        with pytest.raises(TypeError):
            build()

    def test_a_nonsense_pid_is_rejected(self):
        # Arrange
        kwargs = dict(result=StopResult.EXITED, timeout=1.0)
        # Act
        build = partial(StopOutcome, pid=0, escalated_to_sigkill=False, **kwargs)
        # Assert
        with pytest.raises(ValueError):
            build()


# === `board stop` is byte-identical after the extraction ====================


class TestBoardStopUnchanged:
    """The sequence moved OUT of `board stop`; its wording must not move."""

    def test_no_board_message_is_unchanged(self, pidfile_path):
        # Arrange
        expected = "# board is not running (no pidfile / stale).\n"
        # Act
        result = CliRunner().invoke(main, ["board", "stop"])
        # Assert
        assert result.output == expected

    def test_stopped_message_is_unchanged(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        expected = f"# stopped board (pid {board_process.pid}).\n"
        # Act
        result = CliRunner().invoke(main, ["board", "stop"])
        # Assert
        assert result.output == expected

    def test_dry_run_message_is_unchanged(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        expected = (
            f"# dry-run: would SIGTERM pid {board_process.pid} "
            "(timeout 5.0s, then SIGKILL).\n"
        )
        # Act
        result = CliRunner().invoke(main, ["board", "stop", "--dry-run"])
        # Assert
        assert result.output == expected

    def test_dry_run_signals_nothing(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        CliRunner().invoke(main, ["board", "stop", "--dry-run"])
        # Assert
        assert board_process.alive is True

    def test_sigkill_escalation_message_is_unchanged(self, pidfile_path, zombie_pid):
        # Arrange — a zombie survives SIGTERM, so `stop` must escalate.
        _board_write_pid(zombie_pid)
        expected = f"# board did not exit in 0.3s; sent SIGKILL to pid {zombie_pid}.\n"
        # Act
        result = CliRunner().invoke(main, ["board", "stop", "--timeout", "0.3"])
        # Assert
        assert result.output == expected

    def test_sigkill_escalation_still_exits_zero(self, pidfile_path, zombie_pid):
        # Arrange
        _board_write_pid(zombie_pid)
        # Act
        result = CliRunner().invoke(main, ["board", "stop", "--timeout", "0.3"])
        # Assert
        assert result.exit_code == 0

    def test_stop_still_clears_the_pidfile(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        CliRunner().invoke(main, ["board", "stop"])
        # Assert
        assert not pidfile_path.exists()

    def test_stop_still_terminates_the_board(self, pidfile_path, board_process):
        # Arrange
        _board_write_pid(board_process.pid)
        # Act
        CliRunner().invoke(main, ["board", "stop"])
        # Assert
        assert board_process.alive is False


# EOF
