#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Does a broken notifyd tick TELL THE TRUTH? (``scitex_cards._delivery._liveness``)

The regression under test, measured on the live host 2026-07-28: the store read
raised inside the reminder sweep on EVERY tick for roughly a day, the guard
logged it and moved on, and the only line anyone reads stayed::

    notifyd tick 1196: sent=0 failed=0 skipped=0 failed_terminal=0 (0 recorded)

which is bit-for-bit what a healthy IDLE daemon prints. Four properties fix it
and are covered here:

1. a tick whose store read raises reports FAILED, not zero-work;
2. "nothing pending" and "cannot tell" are DISTINGUISHABLE in the emitted line;
3. consecutive failures ESCALATE to ERROR at the threshold;
4. a healthy idle tick stays QUIET — an alarm that fires on healthy idleness is
   an alarm everyone learns to ignore, which is how the outage stayed invisible.

NO mocks (STX-NM / PA-306): the failure is REAL — a store file whose bytes are
not parseable, so ``load_tasks`` genuinely raises inside the daemon's own guard
— and the channels are the repo's real fake transports. One assertion per test
(STX-TQ007); the drivers below run a scenario once and each test reads one
property of the result.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging

from scitex_cards._delivery import _daemon
from scitex_cards._delivery._liveness import (
    ESCALATE_AFTER_FAILURES,
    DeliveryLiveness,
    SweepOutcome,
    TickState,
    liveness_path,
    read_liveness,
    tick_health,
)

from ._fakes import RecorderChannel

T0 = _dt.datetime(2026, 7, 28, 9, 0, 0, tzinfo=_dt.timezone.utc)
LOGGER_NAME = "scitex_cards.delivery.notifyd"


# --------------------------------------------------------------------------- #
# drivers                                                                     #
# --------------------------------------------------------------------------- #
def _write_recipients(tmp_path, mapping: dict) -> None:
    (tmp_path / "recipients.json").write_text(
        json.dumps({"users": mapping}), encoding="utf-8"
    )


def _unreadable_store(tmp_path):
    """A REAL store the daemon cannot parse — ``load_tasks`` raises on it."""
    store = tmp_path / "tasks.yaml"
    store.write_text("{{{ not yaml at all", encoding="utf-8")
    return store


def _run_ticks(tmp_path, *, store, iterations, caplog, level=logging.INFO):
    """Run notifyd for N ticks on a fixed clock; return (result, records)."""
    ticks = {"n": 0}

    def _now():
        ticks["n"] += 1
        return T0 + _dt.timedelta(minutes=ticks["n"])

    with caplog.at_level(level, logger=LOGGER_NAME):
        result = _daemon.run_notifyd(
            store=store,
            interval=60.0,
            channels={"log": RecorderChannel(name="log")},
            sleep=lambda _s: None,
            now_fn=_now,
            max_iterations=iterations,
            terminal_report_every=0,
            nudge_sweep_minutes=0.0,
        )
    return result, list(caplog.records)


def _run_broken_store(tmp_path, caplog, *, iterations=1):
    """Ticks against a store nothing can read (the measured outage)."""
    store = _unreadable_store(tmp_path)
    _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})
    return _run_ticks(
        tmp_path, store=store, iterations=iterations, caplog=caplog, level=logging.DEBUG
    )


def _run_healthy_idle(tmp_path, caplog, *, iterations=1):
    """Ticks against a readable store with nothing pending — the quiet case."""
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    _write_recipients(tmp_path, {})
    return _run_ticks(
        tmp_path, store=store, iterations=iterations, caplog=caplog, level=logging.DEBUG
    )


def _tick_records(records) -> list:
    """The per-tick SUMMARY records only (``state=`` is their signature)."""
    return [
        r
        for r in records
        if "notifyd tick" in r.getMessage() and "state=" in r.getMessage()
    ]


def _tick_lines(records) -> list[str]:
    return [r.getMessage() for r in _tick_records(records)]


# --------------------------------------------------------------------------- #
# (1) a tick whose store read raises reports FAILED, not zero-work            #
# --------------------------------------------------------------------------- #
class TestUnreadableStoreCountsAsFailed:
    def test_the_tick_state_is_failed(self, tmp_path, caplog):
        # Arrange
        # Act
        result, _records = _run_broken_store(tmp_path, caplog)
        # Assert
        assert result["liveness"]["state"] == TickState.FAILED.value

    def test_the_failing_tick_is_counted(self, tmp_path, caplog):
        # Arrange
        # Act
        result, _records = _run_broken_store(tmp_path, caplog, iterations=2)
        # Assert — the whole defect: 2 broken ticks used to count as 0.
        assert result["liveness"]["failed_ticks"] == 2

    def test_the_emitted_line_names_the_underlying_reason(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(tmp_path, caplog)
        # Assert
        assert "reminder_sweep:" in "".join(_tick_lines(records))

    def test_a_failing_tick_is_not_logged_at_info(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(tmp_path, caplog)
        levels = {r.levelno for r in _tick_records(records)}
        # Assert — INFO is what a healthy tick uses; a blind one must be louder.
        assert levels == {logging.WARNING}


# --------------------------------------------------------------------------- #
# (2) "nothing pending" and "cannot tell" are distinguishable                 #
# --------------------------------------------------------------------------- #
class TestPendingIsThreeValued:
    def test_a_healthy_idle_tick_reports_pending_zero(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_healthy_idle(tmp_path, caplog)
        # Assert
        assert "pending=0" in _tick_lines(records)[0]

    def test_a_blind_tick_reports_pending_unknown(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(tmp_path, caplog)
        # Assert — NOT pending=0: unknown is a third value, never a zero.
        assert "pending=unknown" in _tick_lines(records)[0]

    def test_the_two_lines_are_not_the_same_text(self, tmp_path, caplog):
        # Arrange
        idle_dir = tmp_path / "idle"
        idle_dir.mkdir()
        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        # Act
        _r1, idle_records = _run_healthy_idle(idle_dir, caplog)
        idle_line = _tick_lines(idle_records)[0]
        caplog.clear()
        _r2, broken_records = _run_broken_store(broken_dir, caplog)
        broken_line = _tick_lines(broken_records)[0]
        # Assert — the outage's signature was that these were IDENTICAL.
        assert idle_line != broken_line

    def test_an_unreadable_inbox_makes_the_pass_report_unknown(self, tmp_path):
        # Arrange — a recipient exists, but the store cannot be read at all.
        from scitex_cards._delivery._loop import deliver_pending

        store = _unreadable_store(tmp_path)
        _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})
        # Act
        summary = deliver_pending(
            store=store, channels={"log": RecorderChannel(name="log")}, now=T0
        )
        # Assert
        assert summary["pending"] is None

    def test_an_unreadable_inbox_is_counted(self, tmp_path):
        # Arrange
        from scitex_cards._delivery._loop import deliver_pending

        store = _unreadable_store(tmp_path)
        _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})
        # Act
        summary = deliver_pending(
            store=store, channels={"log": RecorderChannel(name="log")}, now=T0
        )
        # Assert
        assert summary["unreadable"] == 1


# --------------------------------------------------------------------------- #
# (3) consecutive failures escalate                                           #
# --------------------------------------------------------------------------- #
def _escalation_records(records):
    return [r for r in records if "DELIVERY OUTAGE" in r.getMessage()]


class TestConsecutiveFailuresEscalate:
    def test_below_the_threshold_there_is_no_escalation(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES - 1
        )
        # Assert
        assert _escalation_records(records) == []

    def test_at_the_threshold_it_escalates(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES
        )
        # Assert
        assert len(_escalation_records(records)) == 1

    def test_the_escalation_is_logged_at_error(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES
        )
        # Assert
        assert _escalation_records(records)[0].levelno == logging.ERROR

    def test_the_escalation_states_how_long_it_has_been_failing(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES
        )
        # Assert — the clock advances a minute per tick.
        assert "over 0:02:00" in _escalation_records(records)[0].getMessage()

    def test_it_gets_louder_not_quieter_past_the_threshold(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES + 2
        )
        # Assert — every further failing tick screams again; it never goes quiet.
        assert len(_escalation_records(records)) == 3

    def test_the_escalation_names_the_underlying_reason(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_broken_store(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES
        )
        # Assert
        assert "reason: reminder_sweep:" in _escalation_records(records)[0].getMessage()

    def test_a_healthy_tick_resets_the_consecutive_count(self):
        # Arrange
        tracker = DeliveryLiveness()
        tracker.observe(
            tick_health(None, faults=("reminder_sweep: RuntimeError: nope",)), now=T0
        )
        # Act
        tracker.observe(
            tick_health({"outcomes": [], "pending": 0}, faults=()),
            now=T0 + _dt.timedelta(minutes=1),
        )
        # Assert
        assert tracker.consecutive_failures == 0


# --------------------------------------------------------------------------- #
# (4) a healthy idle tick still reports QUIETLY                               #
# --------------------------------------------------------------------------- #
class TestHealthyIdleStaysQuiet:
    def test_an_idle_tick_reports_state_idle(self, tmp_path, caplog):
        # Arrange
        # Act
        result, _records = _run_healthy_idle(tmp_path, caplog, iterations=3)
        # Assert
        assert result["liveness"]["state"] == TickState.IDLE.value

    def test_an_idle_tick_is_logged_at_info(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_healthy_idle(tmp_path, caplog, iterations=3)
        levels = {r.levelno for r in _tick_records(records)}
        # Assert
        assert levels == {logging.INFO}

    def test_an_idle_daemon_never_escalates(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, records = _run_healthy_idle(
            tmp_path, caplog, iterations=ESCALATE_AFTER_FAILURES + 3
        )
        # Assert — an alarm that fires on healthy idleness is an ignored alarm.
        assert _escalation_records(records) == []

    def test_an_idle_daemon_records_no_failures(self, tmp_path, caplog):
        # Arrange
        # Act
        result, _records = _run_healthy_idle(tmp_path, caplog, iterations=3)
        # Assert
        assert result["liveness"]["failed_ticks"] == 0


# --------------------------------------------------------------------------- #
# the shape itself: a malformed answer must fail where it is BUILT            #
# --------------------------------------------------------------------------- #
class TestTickHealthValidator:
    def test_a_failed_tick_may_not_claim_to_know_what_is_pending(self):
        # Arrange
        from scitex_cards._delivery._liveness import TickHealth

        # Act
        try:
            TickHealth(state=TickState.FAILED, pending=0, faults=("x: y",))
        except ValueError as exc:
            captured = exc
        else:
            captured = None
        # Assert
        assert captured is not None

    def test_a_healthy_tick_may_not_carry_faults(self):
        # Arrange
        from scitex_cards._delivery._liveness import TickHealth

        # Act
        try:
            TickHealth(state=TickState.IDLE, pending=0, faults=("x: y",))
        except ValueError as exc:
            captured = exc
        else:
            captured = None
        # Assert
        assert captured is not None

    def test_a_failing_sweep_outcome_must_state_a_reason(self):
        # Arrange
        # Act
        try:
            SweepOutcome(name="reminder_sweep", ok=False)
        except ValueError as exc:
            captured = exc
        else:
            captured = None
        # Assert
        assert captured is not None


# --------------------------------------------------------------------------- #
# health surface: a human looks HERE, not in a log                            #
# --------------------------------------------------------------------------- #
class TestHealthSeesTheOutage:
    def test_the_record_is_persisted_for_health_to_read(self, tmp_path, caplog):
        # Arrange
        # Act
        _result, _records = _run_broken_store(tmp_path, caplog)
        record = read_liveness(liveness_path(_unreadable_store(tmp_path)))
        # Assert
        assert record is not None

    def test_health_fails_once_delivery_has_been_failing(self, tmp_path, caplog):
        # Arrange
        from scitex_cards._delivery._liveness import assess_delivery_liveness

        store = _unreadable_store(tmp_path)
        _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})
        _run_ticks(
            tmp_path,
            store=store,
            iterations=ESCALATE_AFTER_FAILURES,
            caplog=caplog,
            level=logging.DEBUG,
        )
        # Act
        verdict = assess_delivery_liveness(store)
        # Assert
        assert verdict["ok"] is False

    def test_health_stays_green_for_a_healthy_idle_daemon(self, tmp_path, caplog):
        # Arrange
        from scitex_cards._delivery._liveness import assess_delivery_liveness

        store = tmp_path / "tasks.yaml"
        store.write_text("tasks: []\n", encoding="utf-8")
        _write_recipients(tmp_path, {})
        _run_ticks(
            tmp_path, store=store, iterations=3, caplog=caplog, level=logging.DEBUG
        )
        # Act
        verdict = assess_delivery_liveness(store)
        # Assert
        assert verdict["state"] == "delivering"

    def test_health_says_unknown_when_no_record_exists_yet(self, tmp_path):
        # Arrange
        from scitex_cards._delivery._liveness import assess_delivery_liveness

        store = tmp_path / "tasks.yaml"
        store.write_text("tasks: []\n", encoding="utf-8")
        # Act
        verdict = assess_delivery_liveness(store)
        # Assert — absence is NOT reported as healthy delivery.
        assert verdict["state"] == "unknown"


# EOF
