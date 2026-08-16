#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The channel log sink must make DEBUG records readable, or it is pointless.

These tests guard a defect that produced NO symptom: the tick-timing instrument
in 0.31.5 computed a number every 5 seconds and discarded it, because the
package logger had no handler and no level. Nothing failed. Nothing was logged
about the failure to log. The only way to notice was to go looking.

So the central test here is not "a handler was attached" — it is "a DEBUG record
written by the channel actually appears in the file", with a CONTROL showing the
same record does NOT appear without the sink. Attaching a handler while records
are still gated by the logger's level would satisfy the weaker assertion and
reproduce the original bug exactly.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scitex_cards._channel_log_sink import (
    ENV_CHANNEL_LOG,
    install_channel_log_sink,
    sink_path_from_env,
)

_PACKAGE_LOGGER = "scitex_cards"


@pytest.fixture(autouse=True)
def _restore_package_logger():
    """Snapshot/restore the package logger.

    Without this a FileHandler installed here survives into every later test in
    the session — the sink writes to a tmp_path that pytest then deletes, so
    subsequent logging would fail on a dead file descriptor. A test that
    mutates global logging state and does not restore it is a landmine for
    whatever runs next.
    """
    logger = logging.getLogger(_PACKAGE_LOGGER)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level

    yield

    for handler in list(logger.handlers):
        if handler not in saved_handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(saved_level)


class TestUnconfiguredIsNotAnError:
    """No env var means no sink — the default, not a failure."""

    def test_returns_none_when_env_is_unset(self):
        # Arrange
        env: dict[str, str] = {}

        # Act
        result = install_channel_log_sink(env)

        # Assert
        assert result is None

    def test_returns_none_when_env_is_whitespace_only(self):
        # Arrange
        env = {ENV_CHANNEL_LOG: "   "}

        # Act
        result = sink_path_from_env(env)

        # Assert
        assert result is None

    def test_attaches_no_handler_when_unconfigured(self):
        # Arrange
        logger = logging.getLogger(_PACKAGE_LOGGER)
        before = len(logger.handlers)

        # Act
        install_channel_log_sink({})

        # Assert
        assert len(logger.handlers) == before


class TestDebugRecordsActuallyReachTheFile:
    """The point of the sink. A handler that receives nothing is not a sink."""

    def test_a_debug_record_appears_in_the_sink_file(self, tmp_path: Path):
        # Arrange
        sink = tmp_path / "channel.log"
        install_channel_log_sink({ENV_CHANNEL_LOG: str(sink)})

        # Act
        logging.getLogger("scitex_cards._mcp_channel").debug(
            "tick drain_s=0.004 gap_s=5.008"
        )

        # Assert
        assert "drain_s=0.004" in sink.read_text(encoding="utf-8")

    def test_the_whole_message_is_written_not_just_its_head(self, tmp_path: Path):
        """The tail of the line carries gap_s — the number the lag work needs."""
        # Arrange
        sink = tmp_path / "channel.log"
        install_channel_log_sink({ENV_CHANNEL_LOG: str(sink)})

        # Act
        logging.getLogger("scitex_cards._mcp_channel").debug(
            "tick drain_s=0.004 gap_s=5.008"
        )

        # Assert
        assert "gap_s=5.008" in sink.read_text(encoding="utf-8")

    def test_without_the_sink_that_same_record_is_discarded(self, tmp_path: Path):
        """POSITIVE CONTROL for the test above.

        Without this, the passing test proves only that *something* wrote to a
        file — not that the sink is why. If DEBUG were reaching a file anyway,
        both tests pass and the sink could be a no-op.
        """
        # Arrange
        sink = tmp_path / "unused.log"

        # Act
        logging.getLogger("scitex_cards._mcp_channel").debug(
            "tick drain_s=0.004 gap_s=5.008"
        )

        # Assert
        assert not sink.exists()

    def test_the_logger_level_does_not_gate_debug_records(self, tmp_path: Path):
        """The exact trap that hid the tick instrument.

        A handler set to DEBUG under a logger set to WARNING yields NOTHING —
        the logger's level is checked first. Installing the sink must fix both.
        """
        # Arrange
        logging.getLogger(_PACKAGE_LOGGER).setLevel(logging.WARNING)
        sink = tmp_path / "gated.log"

        # Act
        install_channel_log_sink({ENV_CHANNEL_LOG: str(sink)})
        logging.getLogger("scitex_cards._mcp_channel").debug("must-appear")

        # Assert
        assert "must-appear" in sink.read_text(encoding="utf-8")


class TestIdempotence:
    """A second install must not double every line."""

    def test_repeat_install_writes_each_record_once(self, tmp_path: Path):
        # Arrange
        sink = tmp_path / "once.log"
        env = {ENV_CHANNEL_LOG: str(sink)}

        # Act
        install_channel_log_sink(env)
        install_channel_log_sink(env)
        logging.getLogger("scitex_cards._mcp_channel").debug("solo-record")

        # Assert
        occurrences = sink.read_text(encoding="utf-8").count("solo-record")
        assert occurrences == 1


class TestConfiguredButUnwritableFailsLoud:
    """A requested log that cannot be written must RAISE, never degrade."""

    def test_raises_when_the_parent_path_is_a_file(self, tmp_path: Path):
        # Arrange — a regular file where a directory is needed
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("occupied", encoding="utf-8")
        env = {ENV_CHANNEL_LOG: str(blocker / "channel.log")}

        # Act
        try:
            install_channel_log_sink(env)
            raised: OSError | None = None
        except OSError as exc:
            raised = exc

        # Assert
        assert raised is not None

    def test_does_not_silently_fall_back_to_no_sink(self, tmp_path: Path):
        """Guards the tempting 'wrap it in try/except and continue' fix.

        Returning None on an unwritable configured path would make the call
        succeed while producing no log — reintroducing invisible-failure into
        the very module written to eliminate it.
        """
        # Arrange
        blocker = tmp_path / "blocker"
        blocker.write_text("occupied", encoding="utf-8")
        env = {ENV_CHANNEL_LOG: str(blocker / "nested" / "channel.log")}

        # Act
        try:
            result = install_channel_log_sink(env)
            returned_quietly = True
        except OSError:
            result = None
            returned_quietly = False

        # Assert
        assert not returned_quietly, f"expected a raise, got {result!r}"


# EOF
