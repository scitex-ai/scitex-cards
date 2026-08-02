#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the keyed-dedup delivery ledger (slice 1).

Real JSON round-trips against a real ``tmp_path`` store — NO mocks. Verifies
the ledger is a KEYED MAP (not an append-log), persists at
``<store_dir>/runtime/delivery_ledger.json``, and that ``already_done`` /
``retry_eligible`` / ``record`` behave per spec (exponential backoff, capped
attempts).
"""

from __future__ import annotations

import datetime as _dt
import json

from scitex_cards._delivery._channel import DeliveryResult, Status
from scitex_cards._delivery._ledger import (
    BASE_BACKOFF_SEC,
    MAX_ATTEMPTS,
    MAX_BACKOFF_SEC,
    TERMINAL_STATUS,
    Ledger,
    ledger_path,
)

_NOW = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)
_FAIL = DeliveryResult(status=Status.FAILED, channel="log")


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _raw_ledger(tmp_path):
    """The persisted ledger JSON, parsed."""
    return json.loads((tmp_path / "runtime" / "delivery_ledger.json").read_text())


def _ledger_with_one_sent(tmp_path):
    """A ledger holding a single SENT record."""
    led = Ledger.load(_store(tmp_path))
    led.record(
        "u_a",
        "n_1",
        "log",
        DeliveryResult(status=Status.SENT, channel="log"),
        _NOW,
    )
    return led


def _ledger_with_one_failure(tmp_path):
    """A ledger holding a single FAILED record at ``_NOW``."""
    led = Ledger.load(_store(tmp_path))
    led.record("u_a", "n_1", "log", _FAIL, _NOW)
    return led


def _ledger_with_two_failures(tmp_path):
    """A ledger holding two FAILED records; returns ``(led, second_ts)``."""
    led = _ledger_with_one_failure(tmp_path)
    later = _NOW + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    led.record("u_a", "n_1", "log", _FAIL, later)
    return led, later


def _ledger_with_attempts_exhausted(tmp_path):
    """Keep failing until MAX_ATTEMPTS is spent; returns ``(led, last_ts)``."""
    led, later = _ledger_with_two_failures(tmp_path)
    t = later + _dt.timedelta(seconds=2 * BASE_BACKOFF_SEC + 1)
    while True:
        entry = led._get("u_a", "n_1", "log")
        if entry["attempts"] >= MAX_ATTEMPTS:
            break
        t = t + _dt.timedelta(hours=1)
        led.record("u_a", "n_1", "log", _FAIL, t)
    return led, t


def _ledger_driven_terminal(tmp_path):
    """Record MAX_ATTEMPTS failures, far enough apart each is its own attempt."""
    led = Ledger.load(_store(tmp_path))
    fail = DeliveryResult(status=Status.FAILED, channel="log", detail="boom")
    for i in range(MAX_ATTEMPTS):
        led.record("u_a", "n_1", "log", fail, _NOW + _dt.timedelta(hours=i))
    return led


def _next_eligible(led, recipient, note_id, channel):
    """Parse the stored ``next_eligible_ts`` back to an aware datetime."""
    entry = led._get(recipient, note_id, channel)
    return _dt.datetime.fromisoformat(entry["next_eligible_ts"].replace("Z", "+00:00"))


def test_ledger_path_under_store_runtime_dir(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    path = ledger_path(store)
    # Assert
    assert path == tmp_path / "runtime" / "delivery_ledger.json"


# --------------------------------------------------------------------------- #
# a SENT record is terminal                                                    #
# --------------------------------------------------------------------------- #
def test_record_sent_marks_the_delivery_done(tmp_path):
    # Arrange
    # Act
    led = _ledger_with_one_sent(tmp_path)
    # Assert
    assert led.already_done("u_a", "n_1", "log") is True


def test_record_sent_survives_a_reload_from_disk(tmp_path):
    # Arrange
    _ledger_with_one_sent(tmp_path)
    # Act — reload from disk; state persisted as a keyed map.
    reloaded = Ledger.load(_store(tmp_path))
    # Assert
    assert reloaded.already_done("u_a", "n_1", "log") is True


def test_the_persisted_ledger_is_a_mapping(tmp_path):
    # Arrange
    _ledger_with_one_sent(tmp_path)
    # Act
    raw = _raw_ledger(tmp_path)
    # Assert
    assert isinstance(raw, dict)


def test_the_persisted_ledger_holds_one_keyed_entry(tmp_path):
    # Arrange
    _ledger_with_one_sent(tmp_path)
    # Act
    raw = _raw_ledger(tmp_path)
    # Assert — ONE keyed entry, not an append list.
    assert len(raw) == 1


def test_the_persisted_sent_entry_records_its_status(tmp_path):
    # Arrange
    _ledger_with_one_sent(tmp_path)
    # Act
    (entry,) = _raw_ledger(tmp_path).values()
    # Assert
    assert entry["status"] == "sent"


def test_the_persisted_sent_entry_counts_one_attempt(tmp_path):
    # Arrange
    _ledger_with_one_sent(tmp_path)
    # Act
    (entry,) = _raw_ledger(tmp_path).values()
    # Assert
    assert entry["attempts"] == 1


# --------------------------------------------------------------------------- #
# failure → exponential backoff, capped attempts                               #
# --------------------------------------------------------------------------- #
def test_a_failure_is_recorded_as_a_failure(tmp_path):
    # Arrange
    # Act
    led = _ledger_with_one_failure(tmp_path)
    # Assert
    assert led.has_failure("u_a", "n_1", "log") is True


def test_a_failure_is_not_retryable_within_its_backoff(tmp_path):
    # Arrange
    led = _ledger_with_one_failure(tmp_path)
    # Act
    eligible = led.retry_eligible("u_a", "n_1", "log", _NOW)
    # Assert
    assert eligible is False


def test_a_failure_is_retryable_after_the_base_backoff(tmp_path):
    # Arrange
    led = _ledger_with_one_failure(tmp_path)
    later = _NOW + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    # Act
    eligible = led.retry_eligible("u_a", "n_1", "log", later)
    # Assert
    assert eligible is True


def test_a_second_failure_doubles_the_backoff(tmp_path):
    # Arrange
    led, later = _ledger_with_two_failures(tmp_path)
    not_yet = later + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    # Act — one BASE window is no longer enough.
    eligible = led.retry_eligible("u_a", "n_1", "log", not_yet)
    # Assert
    assert eligible is False


def test_a_second_failure_retries_after_the_doubled_backoff(tmp_path):
    # Arrange
    led, later = _ledger_with_two_failures(tmp_path)
    much_later = later + _dt.timedelta(seconds=2 * BASE_BACKOFF_SEC + 1)
    # Act
    eligible = led.retry_eligible("u_a", "n_1", "log", much_later)
    # Assert
    assert eligible is True


def test_exhausted_attempts_are_never_retryable_again(tmp_path):
    # Arrange
    led, t = _ledger_with_attempts_exhausted(tmp_path)
    far = t + _dt.timedelta(days=1)
    # Act
    eligible = led.retry_eligible("u_a", "n_1", "log", far)
    # Assert — not eligible even far in the future.
    assert eligible is False


# --------------------------------------------------------------------------- #
# exhausting MAX_ATTEMPTS → a TERMINAL comm-miss that round-trips              #
# --------------------------------------------------------------------------- #
def test_exhausting_attempts_becomes_terminal(tmp_path):
    """A failure that exhausts MAX_ATTEMPTS becomes a TERMINAL comm-miss."""
    # Arrange
    # Act
    led = _ledger_driven_terminal(tmp_path)
    # Assert
    assert led.is_terminal("u_a", "n_1", "log") is True


def test_a_terminal_entry_is_no_longer_a_plain_failure(tmp_path):
    # Arrange
    # Act
    led = _ledger_driven_terminal(tmp_path)
    # Assert
    assert led.has_failure("u_a", "n_1", "log") is False


def test_a_terminal_entry_is_not_retryable(tmp_path):
    # Arrange
    led = _ledger_driven_terminal(tmp_path)
    # Act
    eligible = led.retry_eligible("u_a", "n_1", "log", _NOW + _dt.timedelta(days=365))
    # Assert
    assert eligible is False


def test_a_terminal_entry_does_not_count_as_done(tmp_path):
    # Arrange
    # Act
    led = _ledger_driven_terminal(tmp_path)
    # Assert — terminal is a MISS, not a delivery.
    assert led.already_done("u_a", "n_1", "log") is False


def test_a_terminal_entry_persists_as_one_key(tmp_path):
    # Arrange
    _ledger_driven_terminal(tmp_path)
    # Act — the 0x1f composite key round-trips through PyYAML.
    raw = _raw_ledger(tmp_path)
    # Assert
    assert len(raw) == 1


def test_the_persisted_terminal_entry_records_its_status(tmp_path):
    # Arrange
    _ledger_driven_terminal(tmp_path)
    # Act
    (entry,) = _raw_ledger(tmp_path).values()
    # Assert
    assert entry["status"] == TERMINAL_STATUS


def test_the_persisted_terminal_entry_spent_every_attempt(tmp_path):
    # Arrange
    _ledger_driven_terminal(tmp_path)
    # Act
    (entry,) = _raw_ledger(tmp_path).values()
    # Assert
    assert entry["attempts"] == MAX_ATTEMPTS


def test_terminal_state_survives_a_reload_from_disk(tmp_path):
    # Arrange
    _ledger_driven_terminal(tmp_path)
    # Act
    reloaded = Ledger.load(_store(tmp_path))
    # Assert
    assert reloaded.is_terminal("u_a", "n_1", "log") is True


# --------------------------------------------------------------------------- #
# a Retry-After hint drives the backoff                                        #
# --------------------------------------------------------------------------- #
def _ledger_with_retry_after(tmp_path, retry_after):
    """Record one FAILED telegram delivery carrying a ``retry_after`` hint."""
    led = Ledger.load(_store(tmp_path))
    led.record(
        "u_a",
        "n_1",
        "telegram",
        DeliveryResult(
            status=Status.FAILED, channel="telegram", retry_after=retry_after
        ),
        _NOW,
    )
    return led


def test_retry_after_hint_drives_backoff_instead_of_exponential(tmp_path):
    """A failed result with ``retry_after`` HONORS the hint as the backoff.

    Slice 3: a 429 ``Retry-After`` should set ``next_eligible_ts`` to
    ``now + min(retry_after, MAX_BACKOFF_SEC)`` rather than the exponential
    default.
    """
    # Arrange
    led = _ledger_with_retry_after(tmp_path, 900)
    expected = _NOW + _dt.timedelta(seconds=min(900, MAX_BACKOFF_SEC))
    # Act
    actual = _next_eligible(led, "u_a", "n_1", "telegram")
    # Assert — 900 < cap, so next_eligible == now + 900.
    assert actual == expected


def test_a_retry_after_hint_still_costs_one_attempt(tmp_path):
    # Arrange
    led = _ledger_with_retry_after(tmp_path, 900)
    # Act
    entry = led._get("u_a", "n_1", "telegram")
    # Assert — the hint did NOT cost extra budget.
    assert entry["attempts"] == 1


def test_a_hinted_failure_is_retryable_once_the_window_elapses(tmp_path):
    # Arrange
    led = _ledger_with_retry_after(tmp_path, 900)
    expected = _NOW + _dt.timedelta(seconds=min(900, MAX_BACKOFF_SEC))
    # Act
    eligible = led.retry_eligible(
        "u_a", "n_1", "telegram", expected + _dt.timedelta(seconds=1)
    )
    # Assert — still retryable (not terminal).
    assert eligible is True


def test_retry_after_hint_is_clamped_to_max_backoff(tmp_path):
    """A retry hint larger than MAX_BACKOFF_SEC is clamped to the cap."""
    # Arrange
    led = _ledger_with_retry_after(tmp_path, MAX_BACKOFF_SEC * 10)
    expected = _NOW + _dt.timedelta(seconds=MAX_BACKOFF_SEC)
    # Act
    actual = _next_eligible(led, "u_a", "n_1", "telegram")
    # Assert
    assert actual == expected


def test_no_retry_after_keeps_exponential_default(tmp_path):
    """Without a hint, the existing exponential backoff still applies."""
    # Arrange — `_FAIL` carries no retry_after.
    led = _ledger_with_one_failure(tmp_path)
    expected = _NOW + _dt.timedelta(seconds=BASE_BACKOFF_SEC)
    # Act
    actual = _next_eligible(led, "u_a", "n_1", "log")
    # Assert — first attempt → BASE_BACKOFF_SEC * 2**0 == BASE.
    assert actual == expected


# --------------------------------------------------------------------------- #
# a SKIPPED record spends no budget                                            #
# --------------------------------------------------------------------------- #
def _ledger_with_one_skip(tmp_path):
    """A ledger holding a single SKIPPED record."""
    led = Ledger.load(_store(tmp_path))
    led.record(
        "u_a",
        "n_1",
        "log",
        DeliveryResult(status=Status.SKIPPED, channel="log"),
        _NOW,
    )
    return led


def test_a_skip_is_recorded_as_skipped(tmp_path):
    # Arrange
    led = _ledger_with_one_skip(tmp_path)
    # Act
    entry = led._get("u_a", "n_1", "log")
    # Assert
    assert entry["status"] == "skipped"


def test_skipped_does_not_consume_attempts(tmp_path):
    # Arrange
    led = _ledger_with_one_skip(tmp_path)
    # Act
    entry = led._get("u_a", "n_1", "log")
    # Assert — skipped is non-terminal, no budget spent.
    assert entry["attempts"] == 0


def test_a_skip_does_not_count_as_done(tmp_path):
    # Arrange
    # Act
    led = _ledger_with_one_skip(tmp_path)
    # Assert
    assert led.already_done("u_a", "n_1", "log") is False


def test_a_skip_does_not_count_as_a_failure(tmp_path):
    # Arrange
    # Act
    led = _ledger_with_one_skip(tmp_path)
    # Assert
    assert led.has_failure("u_a", "n_1", "log") is False


# EOF
