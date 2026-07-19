#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the standalone notification-delivery loop (slice 1).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store,
real notifications seeded via :func:`scitex_cards._inbox.enqueue`, a real
``recipients.yaml`` written into the store dir, and REAL fake channels
(``RecorderChannel`` / ``FlakyChannel``) injected through the
``channels=`` / ``extra_providers=`` seams.

Covers the spec's required cases:
* (a) a notification is delivered once and not re-delivered (ledger dedup).
* (b) a raising channel is recorded failed, becomes retry-eligible after
      backoff, and a later run re-attempts + can succeed.
* (c) ``should_deliver_now``=False yields ``skipped`` and does NOT consume
      the user's inbox ``seen`` cursor.
* (d) the loop NEVER flips inbox ``seen`` (unseen count unchanged after a run).

One assertion per test (STX-TQ007): the shared setup lives in the ``_arrange*``
helpers below so every split test carries the exact arrange it needs.
"""

from __future__ import annotations

import datetime as _dt

import yaml

from scitex_cards._delivery import _recipients
from scitex_cards._delivery._ledger import BASE_BACKOFF_SEC, MAX_ATTEMPTS, Ledger
from scitex_cards._delivery._loop import deliver_pending
from scitex_cards._inbox import enqueue, poll_inbox

from ._fakes import AlwaysFailChannel, FlakyChannel, RecorderChannel


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_recipients(tmp_path, mapping: dict) -> None:
    """Write ``recipients.yaml`` next to the store with ``{users: mapping}``."""
    path = tmp_path / "recipients.yaml"
    path.write_text(yaml.safe_dump({"users": mapping}), encoding="utf-8")


def _seed(store, recipient: str, *, card_id="c1", ts="2026-06-27T10:00:00Z"):
    """Enqueue one notification into ``recipient``'s inbox; return its id."""
    rec = enqueue(
        recipient,
        event_type="reassigned",
        card_id=card_id,
        body=f"Card {card_id} reassigned to you",
        actor="bob",
        ts=ts,
        store=store,
    )
    if rec is None:
        raise AssertionError("enqueue returned no notification record")
    return rec["id"]


def _arrange_log_recipient(tmp_path, user: str, **seed_kw):
    """Store + one seeded notification + a ``log`` channel wired for ``user``."""
    store = _store(tmp_path)
    note_id = _seed(store, user, **seed_kw)
    _write_recipients(tmp_path, {user: {"channels": [{"kind": "log"}]}})
    return store, note_id


def _deliver_with_policy_gate_closed(store, recorder):
    """One delivery pass with the loop's policy gate forced closed.

    A real callable swap on the module (NOT a mock), restored afterwards.
    """
    from scitex_cards._delivery import _loop as loop_mod

    original = loop_mod.should_deliver_now
    loop_mod.should_deliver_now = lambda user, note: False
    try:
        return deliver_pending(store=store, channels={"log": recorder})
    finally:
        loop_mod.should_deliver_now = original


def _drive_until_terminal(store, chan, *, start):
    """Deliver repeatedly, advancing past each backoff, until it goes terminal.

    Returns ``(summary, now)`` of the run that reported the terminal outcome,
    or ``(None, now)`` if it never happened within the attempt budget.
    """
    now = start
    for _ in range(MAX_ATTEMPTS + 3):
        summary = deliver_pending(store=store, channels={"log": chan}, now=now)
        if summary["failed_terminal"]:
            return summary, now
        now = now + _dt.timedelta(hours=2)
    return None, now


T0 = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)


# --------------------------------------------------------------------------- #
# (a) delivered once, not re-delivered — ledger dedup                         #
# --------------------------------------------------------------------------- #
def test_first_delivery_run_reports_one_notification_sent(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    # Act
    summary = deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert summary["sent"] == 1


def test_first_delivery_run_reports_no_failures(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    # Act
    summary = deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert summary["failed"] == 0


def test_first_delivery_run_calls_the_channel_once(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert len(recorder.calls) == 1


def test_first_delivery_run_passes_the_recipient_to_the_channel(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert recorder.calls[0]["recipient"] == "u_alice"


def test_first_delivery_run_passes_the_notification_to_the_channel(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert recorder.calls[0]["notification"]["id"] == note_id


def test_second_delivery_run_sends_nothing_more(tmp_path):
    # Arrange
    # the ledger already records the first delivery as done.
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    deliver_pending(store=store, channels={"log": recorder})
    # Act
    summary = deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert summary["sent"] == 0


def test_second_delivery_run_reports_no_outcomes_at_all(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    deliver_pending(store=store, channels={"log": recorder})
    # Act
    summary = deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert summary["outcomes"] == []


def test_second_delivery_run_never_calls_the_channel_again(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    deliver_pending(store=store, channels={"log": recorder})
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    # unchanged from the single call of the first run.
    assert len(recorder.calls) == 1


def test_ledger_records_the_delivered_notification_as_done(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    deliver_pending(store=store, channels={"log": recorder})
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.already_done("u_alice", note_id, "log") is True


# --------------------------------------------------------------------------- #
# (b) raising channel → failed, retry-eligible after backoff, later succeeds  #
# --------------------------------------------------------------------------- #
def test_raising_channel_run_reports_one_failure(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    # Act
    summary = deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Assert
    assert summary["failed"] == 1


def test_raising_channel_run_reports_nothing_sent(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    # Act
    summary = deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Assert
    assert summary["sent"] == 0


def test_raising_channel_is_not_recorded_as_already_done(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.already_done("u_bob", note_id, "log") is False


def test_raising_channel_is_recorded_as_a_failure(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.has_failure("u_bob", note_id, "log") is True


def test_failed_item_is_not_retry_eligible_within_the_backoff(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.retry_eligible("u_bob", note_id, "log", T0) is False


def test_run_inside_the_backoff_window_produces_no_outcomes(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Act
    summary = deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Assert
    assert summary["outcomes"] == []


def test_run_inside_the_backoff_window_does_not_re_attempt(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Act
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    # Assert
    # still only the single first attempt.
    assert flaky.attempts == 1


def test_failed_item_is_retry_eligible_after_the_backoff(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    later = T0 + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.retry_eligible("u_bob", note_id, "log", later) is True


def test_recovered_channel_sends_on_the_retry_run(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    later = T0 + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    # Act
    summary = deliver_pending(store=store, channels={"log": flaky}, now=later)
    # Assert
    assert summary["sent"] == 1


def test_retry_run_really_re_attempts_the_channel(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    later = T0 + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    # Act
    deliver_pending(store=store, channels={"log": flaky}, now=later)
    # Assert
    assert flaky.attempts == 2


def test_successful_retry_is_recorded_as_already_done(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_bob")
    flaky = FlakyChannel(name="log", fail_times=1)
    deliver_pending(store=store, channels={"log": flaky}, now=T0)
    later = T0 + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    deliver_pending(store=store, channels={"log": flaky}, now=later)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.already_done("u_bob", note_id, "log") is True


# --------------------------------------------------------------------------- #
# (b2) permanently-down channel → TERMINAL comm-miss, surfaced not dropped     #
# --------------------------------------------------------------------------- #
def test_permanently_failing_channel_reaches_a_terminal_outcome(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    # Act
    summary, _now = _drive_until_terminal(store, chan, start=T0)
    # Assert
    assert summary is not None


def test_terminal_run_reports_exactly_one_terminal_failure(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    # Act
    summary, _now = _drive_until_terminal(store, chan, start=T0)
    # Assert
    assert summary["failed_terminal"] == 1


def test_terminal_run_lists_a_failed_terminal_outcome(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    # Act
    summary, _now = _drive_until_terminal(store, chan, start=T0)
    # Assert
    assert any(o["outcome"] == "failed_terminal" for o in summary["outcomes"])


def test_terminal_channel_is_tried_exactly_max_attempts_times(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    # Act
    _drive_until_terminal(store, chan, start=T0)
    # Assert
    # exactly MAX tries, then it gives up.
    assert chan.attempts == MAX_ATTEMPTS


def test_terminal_comm_miss_is_surfaced_loudly_on_stderr(tmp_path, capsys):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    _drive_until_terminal(store, chan, start=T0)
    # Act
    err = capsys.readouterr().err
    # Assert
    assert "TERMINAL comm-miss" in err


def test_terminal_comm_miss_warning_names_the_notification(tmp_path, capsys):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    _drive_until_terminal(store, chan, start=T0)
    # Act
    err = capsys.readouterr().err
    # Assert
    assert note_id in err


def test_ledger_marks_the_exhausted_item_terminal(tmp_path):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    _drive_until_terminal(store, chan, start=T0)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.is_terminal("u_frank", note_id, "log") is True


def test_later_run_produces_no_outcomes_for_a_terminal_item(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    _summary, now = _drive_until_terminal(store, chan, start=T0)
    later = now + _dt.timedelta(days=1)
    # Act
    after = deliver_pending(store=store, channels={"log": chan}, now=later)
    # Assert
    assert after["outcomes"] == []


def test_later_run_never_re_attempts_a_terminal_item(tmp_path):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    _summary, now = _drive_until_terminal(store, chan, start=T0)
    later = now + _dt.timedelta(days=1)
    # Act
    deliver_pending(store=store, channels={"log": chan}, now=later)
    # Assert
    # unchanged: the terminal item is noop'd.
    assert chan.attempts == MAX_ATTEMPTS


def test_later_run_does_not_re_warn_about_a_terminal_item(tmp_path, capsys):
    # Arrange
    # drain the stderr emitted while driving to terminal.
    store, _note_id = _arrange_log_recipient(tmp_path, "u_frank")
    chan = AlwaysFailChannel(name="log")
    _summary, now = _drive_until_terminal(store, chan, start=T0)
    capsys.readouterr()
    later = now + _dt.timedelta(days=1)
    # Act
    deliver_pending(store=store, channels={"log": chan}, now=later)
    # Assert
    # no per-run spam; the comm-miss stays a queryable ledger fact.
    assert "TERMINAL" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# (c) should_deliver_now=False → skipped, does NOT consume seen cursor        #
# --------------------------------------------------------------------------- #
def test_closed_policy_gate_reports_one_skipped(tmp_path, env):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    # Act
    summary = _deliver_with_policy_gate_closed(store, recorder)
    # Assert
    assert summary["skipped"] == 1


def test_closed_policy_gate_reports_nothing_sent(tmp_path, env):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    # Act
    summary = _deliver_with_policy_gate_closed(store, recorder)
    # Assert
    assert summary["sent"] == 0


def test_closed_policy_gate_never_reaches_the_channel(tmp_path, env):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    # Act
    _deliver_with_policy_gate_closed(store, recorder)
    # Assert
    # the gate is before transport.
    assert recorder.calls == []


def test_closed_policy_gate_does_not_mark_the_item_done(tmp_path, env):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    _deliver_with_policy_gate_closed(store, recorder)
    # Act
    led = Ledger.load(store)
    # Assert
    assert led.already_done("u_carol", note_id, "log") is False


def test_closed_policy_gate_does_not_record_a_failure(tmp_path, env):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    _deliver_with_policy_gate_closed(store, recorder)
    # Act
    led = Ledger.load(store)
    # Assert
    # skipped is neither sent nor failed, so it is never terminal.
    assert led.has_failure("u_carol", note_id, "log") is False


def test_closed_policy_gate_leaves_the_note_unseen(tmp_path, env):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    _deliver_with_policy_gate_closed(store, recorder)
    # Act
    unseen = poll_inbox("u_carol", unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert len(unseen) == 1


def test_closed_policy_gate_preserves_the_unseen_notification_id(tmp_path, env):
    # Arrange
    store, note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    _deliver_with_policy_gate_closed(store, recorder)
    # Act
    unseen = poll_inbox("u_carol", unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert unseen[0]["id"] == note_id


def test_closed_policy_gate_keeps_the_seen_flag_false(tmp_path, env):
    # Arrange
    store, _note_id = _arrange_log_recipient(tmp_path, "u_carol")
    recorder = RecorderChannel(name="log")
    _deliver_with_policy_gate_closed(store, recorder)
    # Act
    unseen = poll_inbox("u_carol", unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert unseen[0]["seen"] is False


# --------------------------------------------------------------------------- #
# (d) the loop NEVER flips inbox seen (unseen count unchanged)                #
# --------------------------------------------------------------------------- #
def _arrange_two_notes(tmp_path):
    """Store with TWO unseen notifications for ``u_dave`` and a log channel."""
    store = _store(tmp_path)
    _seed(store, "u_dave", card_id="c1", ts="2026-06-27T10:00:00Z")
    _seed(store, "u_dave", card_id="c2", ts="2026-06-27T11:00:00Z")
    _write_recipients(tmp_path, {"u_dave": {"channels": [{"kind": "log"}]}})
    return store


def test_both_seeded_notifications_start_unseen(tmp_path):
    # Arrange
    store = _arrange_two_notes(tmp_path)
    # Act
    before = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert len(before) == 2


def test_delivery_run_sends_both_pending_notifications(tmp_path):
    # Arrange
    store = _arrange_two_notes(tmp_path)
    recorder = RecorderChannel(name="log")
    # Act
    summary = deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert summary["sent"] == 2


def test_loop_leaves_the_unseen_count_unchanged(tmp_path):
    # Arrange
    store = _arrange_two_notes(tmp_path)
    recorder = RecorderChannel(name="log")
    deliver_pending(store=store, channels={"log": recorder})
    # Act
    after = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    # Assert
    # the seen cursor is delivery-independent.
    assert len(after) == 2


def test_loop_leaves_the_same_notifications_unseen(tmp_path):
    # Arrange
    store = _arrange_two_notes(tmp_path)
    before = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    recorder = RecorderChannel(name="log")
    deliver_pending(store=store, channels={"log": recorder})
    # Act
    after = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert {n["id"] for n in before} == {n["id"] for n in after}


# --------------------------------------------------------------------------- #
# missing recipients.yaml → no recipients, no crash                          #
# --------------------------------------------------------------------------- #
def test_missing_recipients_file_yields_an_empty_summary(tmp_path):
    # Arrange
    # a notification exists, but no recipients.yaml.
    store = _store(tmp_path)
    _seed(store, "u_eve")
    recorder = RecorderChannel(name="log")
    # Act
    summary = deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert summary == {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "failed_terminal": 0,
        "outcomes": [],
    }


def test_missing_recipients_file_never_touches_the_channel(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _seed(store, "u_eve")
    recorder = RecorderChannel(name="log")
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert recorder.calls == []


# --------------------------------------------------------------------------- #
# load_recipients parsing (address optional, malformed dropped)              #
# --------------------------------------------------------------------------- #
def _arrange_recipients_fixture(tmp_path):
    """A recipients.yaml exercising the drop rules; returns the parsed list."""
    store = _store(tmp_path)
    # Touch the store so the parent dir resolves to tmp_path.
    enqueue(
        "u_x",
        event_type="reassigned",
        card_id="c1",
        body="x",
        actor="a",
        ts="2026-06-27T10:00:00Z",
        store=store,
    )
    _write_recipients(
        tmp_path,
        {
            "u_x": {
                "channels": [
                    {"kind": "log"},
                    {"kind": "telegram", "address": "12345"},
                    {"address": "no-kind-dropped"},  # missing kind → dropped
                ]
            },
            "u_empty": {"channels": []},  # no channels → recipient dropped
        },
    )
    return store


def test_load_recipients_drops_a_recipient_with_no_channels(tmp_path):
    # Arrange
    store = _arrange_recipients_fixture(tmp_path)
    # Act
    recips = _recipients.load_recipients(store)
    # Assert
    assert [r.user for r in recips] == ["u_x"]


def test_load_recipients_drops_a_channel_without_a_kind(tmp_path):
    # Arrange
    store = _arrange_recipients_fixture(tmp_path)
    # Act
    recips = _recipients.load_recipients(store)
    # Assert
    assert [c.kind for c in recips[0].channels] == ["log", "telegram"]


def test_load_recipients_defaults_a_missing_address_to_empty(tmp_path):
    # Arrange
    store = _arrange_recipients_fixture(tmp_path)
    # Act
    recips = _recipients.load_recipients(store)
    # Assert
    # the log channel needs no address.
    assert recips[0].channels[0].address == ""


def test_load_recipients_parses_an_explicit_channel_address(tmp_path):
    # Arrange
    store = _arrange_recipients_fixture(tmp_path)
    # Act
    recips = _recipients.load_recipients(store)
    # Assert
    assert recips[0].channels[1].address == "12345"


# EOF
