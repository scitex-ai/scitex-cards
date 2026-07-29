#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The notifyd tick tells the truth when delivery is broken.

Reproduces the 2026-07-28/29 silent outage AGAINST REAL FAILURES — no mocks,
no substituted functions. The two conditions are induced the way the incident
induced them:

* **the store cannot be read** — ``$SCITEX_CARDS_DB`` points at a database that
  does not exist, so the package's own fail-loud reader
  (``_store_canonical_read._read_canonical_db_or_raise``) refuses, the reminder
  sweep's guard swallows it, and delivery carries on exactly as it did on the
  day. That is the whole shape of the bug: the daemon kept ticking, the guard
  kept swallowing, and the summary kept printing ``sent=0 failed=0``.
* **the inbox cannot be read** — the inbox database path is occupied by a
  DIRECTORY, so SQLite genuinely cannot open it and ``poll_inbox`` raises. The
  pass then does not know what is pending, and must say so rather than count 0.

One assertion per test (STX-TQ007); each driver runs the scenario once and each
test checks a single property of the result.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging

import pytest

from scitex_cards._delivery import _daemon
from scitex_cards._delivery._liveness import assess_delivery, read_liveness
from scitex_cards._delivery._recipients import load_recipients
from scitex_cards._inbox import poll_inbox

from ._fakes import RecorderChannel

T0 = _dt.datetime(2026, 7, 28, 10, 0, 0, tzinfo=_dt.timezone.utc)

#: The logger every tick summary is emitted on.
NOTIFYD_LOGGER = "scitex_cards.delivery.notifyd"


def _write_recipients(tmp_path, mapping: dict) -> None:
    (tmp_path / "recipients.json").write_text(
        json.dumps({"users": mapping}), encoding="utf-8"
    )


def _rising_clock():
    """A deterministic clock that advances 30 s on every read."""
    ticks = {"n": 0}

    def _now():
        ticks["n"] += 1
        return T0 + _dt.timedelta(seconds=30 * ticks["n"])

    return _now


def _break_the_canonical_store(monkeypatch, tmp_path) -> None:
    """Point the canonical database at a path that does not exist.

    A REAL refusal, not a fake: ``load_tasks`` reaches
    ``_read_canonical_db_or_raise``, which raises rather than treat a missing
    database as an empty board. This is the same class of fault the live daemon
    hit ("REFUSING TO READ ... as the store") and it recurs on EVERY tick.
    """
    monkeypatch.setenv("SCITEX_CARDS_DB", str(tmp_path / "absent" / "cards.db"))


def _break_the_inbox(tmp_path) -> None:
    """Make the inbox genuinely unreadable — on EITHER inbox backend.

    Both are broken deliberately. The suite pins
    ``SCITEX_TODO_INBOX_BACKEND=yaml`` while production runs SQLite, so
    breaking only the one this harness happens to use would make the test pass
    for a reason that does not exist in production — and a test that cannot
    fail on the real path is not a test.

    * yaml backend: ``inboxes.json`` is not JSON, so ``json.load`` raises.
    * sqlite backend: the inbox database path is a DIRECTORY, which SQLite
      cannot open.

    :func:`test_the_broken_inbox_really_raises` is the positive control — it
    proves this function actually broke something, because "the inbox is empty"
    and "the instrument is broken" otherwise look identical from the outside.
    """
    (tmp_path / "inboxes.json").write_text("{ not json at all", encoding="utf-8")
    (tmp_path / "runtime" / "todo.db").mkdir(parents=True, exist_ok=True)


def _run_ticks(
    tmp_path,
    caplog,
    *,
    ticks: int = 1,
    escalate_after: int = 3,
    recipients: dict | None = None,
) -> dict:
    """Run notifyd for ``ticks`` iterations; return the run + its tick lines."""
    store = tmp_path / "tasks.yaml"
    _write_recipients(
        tmp_path,
        {"u_alice": {"channels": [{"kind": "log"}]}}
        if recipients is None
        else recipients,
    )
    recorder = RecorderChannel(name="log")
    with caplog.at_level(logging.INFO, logger=NOTIFYD_LOGGER):
        result = _daemon.run_notifyd(
            store=store,
            interval=60.0,
            channels={"log": recorder},
            sleep=lambda _s: None,
            now_fn=_rising_clock(),
            max_iterations=ticks,
            terminal_report_every=0,
            nudge_sweep_minutes=0.0,
            escalate_after=escalate_after,
        )
    lines = [
        record
        for record in caplog.records
        if record.getMessage().startswith("notifyd tick ")
    ]
    return {"result": result, "ticks": lines, "recorder": recorder, "store": store}


def _healthy_run(tmp_path, caplog, **kw) -> dict:
    """A tick with a readable store and an empty inbox — genuinely idle."""
    return _run_ticks(tmp_path, caplog, **kw)


def _broken_store_run(monkeypatch, tmp_path, caplog, **kw) -> dict:
    """A tick whose STORE READ raises on every iteration."""
    _break_the_canonical_store(monkeypatch, tmp_path)
    return _run_ticks(tmp_path, caplog, **kw)


def _broken_inbox_run(tmp_path, caplog, **kw) -> dict:
    """A tick whose INBOX READ raises, so pending is undeterminable."""
    _break_the_inbox(tmp_path)
    return _run_ticks(tmp_path, caplog, **kw)


# --------------------------------------------------------------------------- #
# (1) a tick whose store read raises reports FAILED, not zero-work            #
# --------------------------------------------------------------------------- #
class TestStoreReadFailureIsCounted:
    def test_the_tick_reports_failed(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog)
        # Assert
        assert "FAILED" in run["ticks"][0].getMessage()

    def test_the_tick_is_not_reported_as_idle(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog)
        # Assert
        # the exact lie the old line told: sent=0 with an exception.
        assert "IDLE" not in run["ticks"][0].getMessage()

    def test_the_tick_names_the_underlying_reason(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog)
        # Assert
        assert "reminder_sweep:" in run["ticks"][0].getMessage()

    def test_the_failing_tick_is_louder_than_info(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog)
        # Assert
        assert run["ticks"][0].levelno > logging.INFO

    def test_the_failure_is_persisted_for_another_process_to_read(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog)
        # Assert
        assert read_liveness(run["store"]).consecutive_failures == 1


# --------------------------------------------------------------------------- #
# (2) "nothing pending" and "cannot tell" are distinguishable                 #
# --------------------------------------------------------------------------- #
class TestPendingIsThreeValued:
    def test_the_broken_inbox_really_raises(self, tmp_path):
        # Arrange
        # POSITIVE CONTROL. Without it, an inbox that was never
        # broken and an inbox that is empty produce the same green test.
        _break_the_inbox(tmp_path)
        # Act
        with pytest.raises(Exception) as excinfo:
            poll_inbox(
                "u_alice",
                unseen_only=False,
                mark_seen=False,
                store=tmp_path / "tasks.yaml",
            )
        # Assert
        assert excinfo.value is not None

    def test_a_recipient_is_actually_configured(self, tmp_path):
        # Arrange
        # SECOND CONTROL: pending=0 with ZERO recipients would pass
        # the "nothing pending" test for the wrong reason entirely.
        _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})
        # Act
        recipients = load_recipients(tmp_path / "tasks.yaml")
        # Assert
        assert [r.user for r in recipients] == ["u_alice"]

    def test_nothing_pending_reports_a_zero_count(self, tmp_path, caplog):
        # Arrange
        # Act
        run = _healthy_run(tmp_path, caplog)
        # Assert
        assert "pending=0" in run["ticks"][0].getMessage()

    def test_an_unreadable_inbox_reports_pending_unknown(self, tmp_path, caplog):
        # Arrange
        # Act
        run = _broken_inbox_run(tmp_path, caplog)
        # Assert
        assert "pending=unknown" in run["ticks"][0].getMessage()

    def test_an_unreadable_inbox_never_claims_zero_pending(self, tmp_path, caplog):
        # Arrange
        # "do not report 0-pending when the answer is unknown" —
        # collapsing unknown into a pole is the bug named in the constitution.
        # Act
        run = _broken_inbox_run(tmp_path, caplog)
        # Assert
        assert "pending=0" not in run["ticks"][0].getMessage()

    def test_an_unreadable_inbox_is_a_failed_tick(self, tmp_path, caplog):
        # Arrange
        # Act
        run = _broken_inbox_run(tmp_path, caplog)
        # Assert
        assert "FAILED" in run["ticks"][0].getMessage()


# --------------------------------------------------------------------------- #
# (3) consecutive failures escalate                                           #
# --------------------------------------------------------------------------- #
class TestConsecutiveFailuresEscalate:
    def test_the_first_failure_is_only_a_warning(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Assert
        assert run["ticks"][0].levelno == logging.WARNING

    def test_the_threshold_failure_escalates_to_error(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Assert
        assert run["ticks"][2].levelno == logging.ERROR

    def test_the_escalated_line_carries_the_consecutive_count(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Assert
        assert "consecutive_failures=3" in run["ticks"][2].getMessage()

    def test_the_escalated_line_says_how_long_it_has_been_failing(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Assert
        assert "failing_for=" in run["ticks"][2].getMessage()

    def test_the_streak_survives_a_daemon_restart(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # a bounce must not reset the alarm to zero — the outage
        # outlives the process.
        _break_the_canonical_store(monkeypatch, tmp_path)
        _run_ticks(tmp_path, caplog, ticks=2)
        # Act
        run = _run_ticks(tmp_path, caplog, ticks=1)
        # Assert
        assert read_liveness(run["store"]).consecutive_failures == 3


# --------------------------------------------------------------------------- #
# (4) a healthy idle tick still reports quietly                               #
# --------------------------------------------------------------------------- #
class TestHealthyIdleStaysQuiet:
    def test_an_idle_tick_stays_at_info(self, tmp_path, caplog):
        # Arrange
        # making an idle daemon noisy is how alarms get ignored —
        # which would reproduce this outage by a different route.
        # Act
        run = _healthy_run(tmp_path, caplog)
        # Assert
        assert run["ticks"][0].levelno == logging.INFO

    def test_an_idle_tick_is_not_marked_failed(self, tmp_path, caplog):
        # Arrange
        # Act
        run = _healthy_run(tmp_path, caplog)
        # Assert
        assert "FAILED" not in run["ticks"][0].getMessage()

    def test_an_idle_tick_carries_no_streak_noise(self, tmp_path, caplog):
        # Arrange
        # Act
        run = _healthy_run(tmp_path, caplog)
        # Assert
        assert "consecutive_failures" not in run["ticks"][0].getMessage()

    def test_an_idle_run_records_no_failures(self, tmp_path, caplog):
        # Arrange
        # Act
        run = _healthy_run(tmp_path, caplog, ticks=3)
        # Assert
        assert read_liveness(run["store"]).consecutive_failures == 0


# --------------------------------------------------------------------------- #
# (5) delivery liveness is visible where a human looks                        #
# --------------------------------------------------------------------------- #
class TestDeliveryLivenessIsExposed:
    def test_no_record_reports_unknown_rather_than_healthy(self, tmp_path):
        # Arrange
        # notifyd never ran here; inventing either verdict from an
        # absent measurement is the same lie as reporting zero pending.
        store = tmp_path / "tasks.yaml"
        # Act
        verdict = assess_delivery(store)
        # Assert
        assert verdict["state"] == "unknown"

    def test_no_record_is_not_reported_as_failing(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        verdict = assess_delivery(store)
        # Assert
        assert verdict["ok"]

    def test_a_healthy_run_reports_delivering(self, tmp_path, caplog):
        # Arrange
        run = _healthy_run(tmp_path, caplog)
        # Act
        verdict = assess_delivery(run["store"])
        # Assert
        assert verdict["state"] == "delivering"

    def test_a_broken_run_reports_failing(self, tmp_path, caplog, monkeypatch):
        # Arrange
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Act
        verdict = assess_delivery(run["store"])
        # Assert
        assert verdict["state"] == "failing"

    def test_a_sustained_outage_fails_the_health_check(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Act
        verdict = assess_delivery(run["store"], escalate_after=3)
        # Assert
        assert not verdict["ok"]

    def test_the_failing_verdict_carries_an_actionable_hint(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Act
        verdict = assess_delivery(run["store"])
        # Assert
        assert "systemctl --user restart scitex-todo-notifyd" in verdict["hint"]

    def test_the_failing_verdict_names_the_underlying_reason(
        self, tmp_path, caplog, monkeypatch
    ):
        # Arrange
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Act
        verdict = assess_delivery(run["store"])
        # Assert
        assert "reminder_sweep:" in verdict["detail"]


# --------------------------------------------------------------------------- #
# (6) resilience is preserved — the daemon still survives every fault         #
# --------------------------------------------------------------------------- #
class TestResilienceIsUnchanged:
    def test_a_broken_store_does_not_stop_the_loop(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Assert
        assert run["result"]["iterations"] == 3

    def test_every_failing_tick_still_emits_a_line(self, tmp_path, caplog, monkeypatch):
        # Arrange
        # the old code logged the summary INSIDE the tick guard, so a
        # tick that raised printed nothing at all.
        # Act
        run = _broken_store_run(monkeypatch, tmp_path, caplog, ticks=3)
        # Assert
        assert len(run["ticks"]) == 3


# EOF
