#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the always-on delivery daemon (slice 2).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
notifications seeded via :func:`scitex_cards._inbox.enqueue`, a real
``recipients.yaml``, REAL fake channels, and a REAL ``threading.Event`` stop
seam + injected no-op ``sleep`` + injected ``now_fn`` so the loop never sleeps
for real and never depends on wall-clock.

Covers the spec's required cases:
* daemon runs N iterations (injected ``sleep`` no-op + ``max_iterations``) with
  a real fake channel — deliver runs each tick + notifications are delivered.
* single-instance: acquiring the daemon lock twice fails fast (the second call
  raises ``DaemonAlreadyRunning`` WITHOUT running the loop).
* flock release: after ``run_notifyd`` returns (stop tripped) AND after it
  raises mid-loop, the lock is free (a subsequent acquire succeeds) + the
  pidfile is gone — clean release on BOTH exit paths.
* terminal re-report: seed a ``failed_terminal`` ledger entry, run enough ticks
  to cross ``terminal_report_every``, assert ``report_terminal_misses`` returns
  it + the re-report WARNING fires AND is THROTTLED (not every tick).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import threading

import yaml

from scitex_cards._delivery._daemon import (
    DaemonAlreadyRunning,
    _SingleInstanceLock,
    pidfile_path,
    report_terminal_misses,
    run_notifyd,
)
from scitex_cards._delivery._ledger import TERMINAL_STATUS, Ledger, _make_key
from scitex_cards._delivery._pidfile import (
    assess_liveness,
    heartbeat_age_seconds,
    local_identity,
    writer_is_local,
)
from scitex_cards._delivery._pidfile import (
    parse as parse_pidfile,
)
from scitex_cards._inbox import enqueue, poll_inbox

from ._fakes import RecorderChannel


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_recipients(tmp_path, mapping: dict) -> None:
    path = tmp_path / "recipients.yaml"
    path.write_text(yaml.safe_dump({"users": mapping}), encoding="utf-8")


def _seed(store, recipient: str, *, card_id="c1", ts="2026-06-27T10:00:00Z"):
    rec = enqueue(
        recipient,
        event_type="reassigned",
        card_id=card_id,
        body=f"Card {card_id} reassigned to you",
        actor="bob",
        ts=ts,
        store=store,
    )
    assert rec is not None
    return rec["id"]


def _seed_terminal(store, recipient: str, note_id: str, channel: str) -> None:
    """Directly seed a ``failed_terminal`` ledger entry (a standing comm-miss)."""
    led = Ledger.load(store)
    led._entries[_make_key(recipient, note_id, channel)] = {
        "status": TERMINAL_STATUS,
        "attempts": 5,
        "last_ts": "2026-06-27T10:00:00Z",
        "next_eligible_ts": None,
        "detail": "transport permanently down",
    }
    led._save()


def _seeded_store(tmp_path, recipient: str):
    """A store with one pending notification and a log channel for ``recipient``."""
    store = _store(tmp_path)
    note_id = _seed(store, recipient)
    _write_recipients(tmp_path, {recipient: {"channels": [{"kind": "log"}]}})
    return store, note_id


# --------------------------------------------------------------------------- #
# (1) daemon runs N iterations, delivers each tick                            #
# --------------------------------------------------------------------------- #
def _run_three_ticks(tmp_path):
    """Run 3 ticks over a seeded store; returns the whole observable outcome.

    Returns ``(result, recorder, sleeps, note_id, store)``.
    """
    store, note_id = _seeded_store(tmp_path, "u_alice")
    recorder = RecorderChannel(name="log")
    sleeps: list[float] = []
    t0 = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)
    ticks = {"n": 0}

    def _now():
        # Advance the clock past any backoff window each tick (deterministic).
        ticks["n"] += 1
        return t0 + _dt.timedelta(hours=ticks["n"])

    result = run_notifyd(
        store=store,
        interval=120.0,
        channels={"log": recorder},
        sleep=lambda s: sleeps.append(s),
        now_fn=_now,
        max_iterations=3,
        terminal_report_every=0,
    )
    return result, recorder, sleeps, note_id, store


def test_daemon_runs_the_requested_iterations(tmp_path):
    # Arrange
    # Act
    result, _recorder, _sleeps, _note_id, _store_path = _run_three_ticks(tmp_path)
    # Assert
    assert result["iterations"] == 3


def test_daemon_reports_max_iterations_as_the_stop_cause(tmp_path):
    # Arrange
    # Act
    result, _recorder, _sleeps, _note_id, _store_path = _run_three_ticks(tmp_path)
    # Assert
    assert result["stopped_by"] == "max_iterations"


def test_daemon_sends_each_notification_exactly_once(tmp_path):
    # Arrange
    # Act
    result, _recorder, _sleeps, _note_id, _store_path = _run_three_ticks(tmp_path)
    # Assert — the note is delivered on the FIRST tick; later ticks are ledger
    # no-ops (already sent) — deliver_pending ran every tick but sent once.
    assert result["totals"]["sent"] == 1


def test_the_channel_is_invoked_once_across_the_ticks(tmp_path):
    # Arrange
    # Act
    _result, recorder, _sleeps, _note_id, _store_path = _run_three_ticks(tmp_path)
    # Assert
    assert len(recorder.calls) == 1


def test_the_channel_receives_the_seeded_notification(tmp_path):
    # Arrange
    # Act
    _result, recorder, _sleeps, note_id, _store_path = _run_three_ticks(tmp_path)
    # Assert
    assert recorder.calls[0]["notification"]["id"] == note_id


def test_the_daemon_sleeps_for_the_configured_interval(tmp_path):
    # Arrange
    # Act
    _result, _recorder, sleeps, _note_id, _store_path = _run_three_ticks(tmp_path)
    # Assert — the injected sleep was a no-op (never blocked for real); between
    # the 3 ticks the loop did NOT sleep after the last (max_iterations
    # short-circuit).
    assert all(s == 120.0 for s in sleeps)


def test_the_pidfile_is_removed_on_normal_exit(tmp_path):
    # Arrange
    # Act
    _result, _recorder, _sleeps, _note_id, store = _run_three_ticks(tmp_path)
    # Assert
    assert not pidfile_path(store).exists()


def _run_until_stop_event(tmp_path):
    """Trip a stop event from inside the loop (via the sleep seam)."""
    store, _note_id = _seeded_store(tmp_path, "u_bob")
    recorder = RecorderChannel(name="log")
    stop = threading.Event()
    seen = {"ticks": 0}

    def _sleep(_s):
        # Trip the stop event after the 2nd tick — proves the cooperative
        # stop seam ends the loop without a real sleep.
        seen["ticks"] += 1
        if seen["ticks"] >= 2:
            stop.set()

    result = run_notifyd(
        store=store,
        channels={"log": recorder},
        stop=stop,
        sleep=_sleep,
        terminal_report_every=0,
    )
    return result, store


def test_daemon_stops_on_event_after_k_ticks(tmp_path):
    """A stop event tripped from inside (via sleep seam) ends the loop cleanly."""
    # Arrange
    # Act
    result, _store_path = _run_until_stop_event(tmp_path)
    # Assert
    assert result["stopped_by"] == "stop_event"


def test_a_stop_event_ends_the_loop_on_the_expected_tick(tmp_path):
    # Arrange
    # Act
    result, _store_path = _run_until_stop_event(tmp_path)
    # Assert
    assert result["iterations"] == 2


def test_the_pidfile_is_removed_after_a_stop_event(tmp_path):
    # Arrange
    # Act
    _result, store = _run_until_stop_event(tmp_path)
    # Assert
    assert not pidfile_path(store).exists()


# --------------------------------------------------------------------------- #
# (2) single-instance: second daemon fails fast, never runs the loop         #
# --------------------------------------------------------------------------- #
def _run_second_daemon_while_locked(tmp_path):
    """Hold the lock as a "running first daemon", then start a second one.

    Returns ``(caught, recorder, store)`` where ``caught`` is the exception the
    second daemon raised (or None).
    """
    store, _note_id = _seeded_store(tmp_path, "u_carol")
    first = _SingleInstanceLock(pidfile_path(store))
    first.acquire()
    recorder = RecorderChannel(name="log")
    caught = None
    try:
        try:
            run_notifyd(
                store=store,
                channels={"log": recorder},
                sleep=lambda _s: None,
                max_iterations=5,
            )
        except DaemonAlreadyRunning as exc:
            caught = exc
    finally:
        first.release()
    return caught, recorder, store


def test_single_instance_second_daemon_fails_fast(tmp_path):
    # Arrange
    # Act
    caught, _recorder, _store_path = _run_second_daemon_while_locked(tmp_path)
    # Assert — a second daemon must FAIL FAST.
    assert isinstance(caught, DaemonAlreadyRunning)


def test_the_already_running_error_says_it_is_already_running(tmp_path):
    # Arrange
    # Act
    caught, _recorder, _store_path = _run_second_daemon_while_locked(tmp_path)
    # Assert
    assert "already" in str(caught).lower()


def test_a_blocked_second_daemon_delivers_nothing(tmp_path):
    # Arrange
    # Act
    _caught, recorder, _store_path = _run_second_daemon_while_locked(tmp_path)
    # Assert — the loop NEVER ran.
    assert recorder.calls == []


def test_the_pidfile_is_gone_once_the_first_daemon_releases(tmp_path):
    # Arrange
    # Act
    _caught, _recorder, store = _run_second_daemon_while_locked(tmp_path)
    # Assert
    assert not pidfile_path(store).exists()


def test_the_lock_slot_is_reusable_after_release(tmp_path):
    # Arrange
    _caught, _recorder, store = _run_second_daemon_while_locked(tmp_path)
    second = _SingleInstanceLock(pidfile_path(store))
    # Act — a fresh acquire must succeed now the slot is free.
    acquired = second.acquire()
    second.release()
    # Assert
    assert acquired is not False


# --------------------------------------------------------------------------- #
# (3) flock release on BOTH exit paths (normal stop + exception mid-loop)     #
# --------------------------------------------------------------------------- #
def test_flock_released_after_normal_stop(tmp_path):
    # Arrange
    store, _note_id = _seeded_store(tmp_path, "u_dave")
    # Act
    run_notifyd(
        store=store,
        channels={"log": RecorderChannel(name="log")},
        sleep=lambda _s: None,
        max_iterations=1,
        terminal_report_every=0,
    )
    # Assert — pidfile gone after a normal stop.
    assert not pidfile_path(store).exists()


def test_the_lock_is_reacquirable_after_a_normal_stop(tmp_path):
    # Arrange
    store, _note_id = _seeded_store(tmp_path, "u_dave")
    run_notifyd(
        store=store,
        channels={"log": RecorderChannel(name="log")},
        sleep=lambda _s: None,
        max_iterations=1,
        terminal_report_every=0,
    )
    again = _SingleInstanceLock(pidfile_path(store))
    # Act
    acquired = again.acquire()
    again.release()
    # Assert
    assert acquired is not False


def _run_with_exploding_sleep(tmp_path):
    """Raise from the ``sleep`` seam — OUTSIDE the per-tick guard.

    The per-tick body self-heals (see ``test_tick_exception_self_heals_*``), so
    to exercise the finally-release guarantee we raise from a structural point
    that is intentionally NOT swallowed. Returns ``(caught, boom, store)``.
    """
    store, _note_id = _seeded_store(tmp_path, "u_eve")
    boom = RuntimeError("sleep blew up between ticks")

    def _explode(_s):
        raise boom

    caught = None
    try:
        run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=_explode,  # raises OUTSIDE the per-tick guard → propagates
            max_iterations=3,
        )
    except RuntimeError as exc:
        caught = exc
    return caught, boom, store


def test_an_exception_outside_the_tick_guard_propagates(tmp_path):
    # Arrange
    # Act
    caught, boom, _store_path = _run_with_exploding_sleep(tmp_path)
    # Assert — it propagates unchanged, not swallowed or wrapped.
    assert caught is boom


def test_flock_released_after_exception_midloop(tmp_path):
    """An exception OUTSIDE the per-tick guard still releases the lock."""
    # Arrange
    # Act
    _caught, _boom, store = _run_with_exploding_sleep(tmp_path)
    # Assert — CRITICAL: even on a crash the pidfile is removed so a restart
    # is never blocked by a stale lock.
    assert not pidfile_path(store).exists()


def test_the_lock_is_reacquirable_after_a_midloop_exception(tmp_path):
    # Arrange
    _caught, _boom, store = _run_with_exploding_sleep(tmp_path)
    again = _SingleInstanceLock(pidfile_path(store))
    # Act
    acquired = again.acquire()
    again.release()
    # Assert
    assert acquired is not False


def _run_with_exploding_tick(tmp_path, caplog):
    """A tick that raises every time; returns ``(result, store)``.

    ``now_fn()`` runs inside the per-tick guard, so a tick that raises must not
    kill the daemon — it logs and proceeds (tick resilience, scitex-dev review
    note).
    """
    store, _note_id = _seeded_store(tmp_path, "u_judy")
    boom = RuntimeError("clock blew up mid-tick")

    def _explode():
        raise boom  # inside the per-tick guard → caught + logged + continue

    with caplog.at_level(logging.ERROR, logger="scitex_cards.delivery.notifyd"):
        result = run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=lambda _s: None,
            now_fn=_explode,
            max_iterations=3,
            terminal_report_every=0,
        )
    return result, store


def test_tick_exception_self_heals_and_continues(tmp_path, caplog):
    """A raising tick does not kill the daemon — it finishes all its ticks."""
    # Arrange
    # Act
    result, _store_path = _run_with_exploding_tick(tmp_path, caplog)
    # Assert — every tick raised, yet the daemon ran all 3.
    assert result["iterations"] == 3


def test_a_self_healed_daemon_still_stops_cleanly(tmp_path, caplog):
    # Arrange
    # Act
    result, _store_path = _run_with_exploding_tick(tmp_path, caplog)
    # Assert
    assert result["stopped_by"] == "max_iterations"


def test_each_failing_tick_is_logged_with_a_traceback(tmp_path, caplog):
    # Arrange
    # Act
    _result, _store_path = _run_with_exploding_tick(tmp_path, caplog)
    tick_errors = [
        r for r in caplog.records if "continuing to next tick" in r.getMessage()
    ]
    # Assert — not silently swallowed.
    assert len(tick_errors) == 3


def test_the_lock_is_cleaned_up_despite_failing_ticks(tmp_path, caplog):
    # Arrange
    # Act
    _result, store = _run_with_exploding_tick(tmp_path, caplog)
    # Assert
    assert not pidfile_path(store).exists()


# --------------------------------------------------------------------------- #
# (4) terminal re-report: throttled re-surfacing of standing comm-misses       #
# --------------------------------------------------------------------------- #
def _seeded_terminal_miss(tmp_path):
    """One standing ``failed_terminal`` ledger entry; returns the misses list."""
    store = _store(tmp_path)
    _seed(store, "u_frank")  # touch the store so the dir resolves
    _seed_terminal(store, "u_frank", "n_deadbeef", "telegram")
    return report_terminal_misses(store)


def test_report_terminal_misses_returns_seeded_entry(tmp_path):
    # Arrange
    # Act
    misses = _seeded_terminal_miss(tmp_path)
    # Assert
    assert len(misses) == 1


def test_a_reported_miss_names_its_recipient(tmp_path):
    # Arrange
    # Act
    misses = _seeded_terminal_miss(tmp_path)
    # Assert
    assert misses[0]["recipient"] == "u_frank"


def test_a_reported_miss_names_its_note_id(tmp_path):
    # Arrange
    # Act
    misses = _seeded_terminal_miss(tmp_path)
    # Assert
    assert misses[0]["note_id"] == "n_deadbeef"


def test_a_reported_miss_names_its_channel(tmp_path):
    # Arrange
    # Act
    misses = _seeded_terminal_miss(tmp_path)
    # Assert
    assert misses[0]["channel"] == "telegram"


def test_a_reported_miss_carries_its_attempt_count(tmp_path):
    # Arrange
    # Act
    misses = _seeded_terminal_miss(tmp_path)
    # Assert
    assert misses[0]["attempts"] == 5


def test_a_reported_miss_carries_the_failure_detail(tmp_path):
    # Arrange
    # Act
    misses = _seeded_terminal_miss(tmp_path)
    # Assert
    assert misses[0]["detail"] == "transport permanently down"


def _run_with_terminal_report(tmp_path, caplog, *, every, iterations, recipient):
    """Run the daemon over a standing comm-miss with a given report cadence."""
    store = _store(tmp_path)
    _seed(store, recipient)
    _write_recipients(tmp_path, {recipient: {"channels": [{"kind": "log"}]}})
    _seed_terminal(store, recipient, "n_cafebabe", "telegram")

    with caplog.at_level(logging.WARNING, logger="scitex_cards.delivery.notifyd"):
        run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=lambda _s: None,
            max_iterations=iterations,
            terminal_report_every=every,
        )
    return [r for r in caplog.records if "OUTSTANDING terminal comm-miss" in r.message]


def test_terminal_re_report_fires_and_is_throttled(tmp_path, caplog):
    # Arrange
    # Act
    headers = _run_with_terminal_report(
        tmp_path, caplog, every=3, iterations=7, recipient="u_grace"
    )
    # Assert — the re-report header WARNING fires only on ticks divisible by
    # `every` (ticks 3 and 6 over 7 iterations) — THROTTLED, not every tick.
    assert len(headers) == 2, f"expected 2 throttled re-reports, got {len(headers)}"


def test_the_re_report_names_the_undelivered_note(tmp_path, caplog):
    # Arrange
    # Act
    _run_with_terminal_report(
        tmp_path, caplog, every=3, iterations=7, recipient="u_grace"
    )
    detail_lines = [r for r in caplog.records if "n_cafebabe" in r.getMessage()]
    # Assert — the per-miss WARNING names the note so the operator can act.
    assert detail_lines, "the outstanding comm-miss note id must be re-surfaced"


def test_terminal_report_every_zero_disables(tmp_path, caplog):
    # Arrange
    # Act
    headers = _run_with_terminal_report(
        tmp_path, caplog, every=0, iterations=5, recipient="u_heidi"
    )
    # Assert
    assert headers == [], "terminal_report_every=0 must disable the re-report"


# --------------------------------------------------------------------------- #
# (5) the daemon NEVER flips the inbox seen cursor (read-only contract)        #
# --------------------------------------------------------------------------- #
def test_a_seeded_notification_starts_unseen(tmp_path):
    # Arrange
    store, _note_id = _seeded_store(tmp_path, "u_ivan")
    # Act
    before = poll_inbox("u_ivan", unseen_only=True, mark_seen=False, store=store)
    # Assert
    assert len(before) == 1


def test_daemon_never_flips_inbox_seen(tmp_path):
    # Arrange
    store, _note_id = _seeded_store(tmp_path, "u_ivan")
    # Act
    run_notifyd(
        store=store,
        channels={"log": RecorderChannel(name="log")},
        sleep=lambda _s: None,
        max_iterations=2,
        terminal_report_every=0,
    )
    after = poll_inbox("u_ivan", unseen_only=True, mark_seen=False, store=store)
    # Assert — delivery is seen-independent.
    assert len(after) == 1


# --------------------------------------------------------------------------- #
# HEARTBEAT — the only liveness signal that crosses a PID-namespace boundary   #
# --------------------------------------------------------------------------- #
def _heartbeat_snapshots(tmp_path):
    """Read the LIVE pidfile from inside the loop (via the sleep seam).

    After the run the pidfile is deliberately unlinked, so the only way to
    observe the heartbeat is from within. Returns ``(snapshots, verdicts, t0)``.
    """
    store = _store(tmp_path)
    _write_recipients(tmp_path, {})

    t0 = _dt.datetime(2026, 7, 13, 9, 0, 0, tzinfo=_dt.timezone.utc)
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        return t0 + _dt.timedelta(seconds=calls["n"])

    snapshots: list[dict] = []
    verdicts: list[dict] = []

    def _sleep(_s):
        text = pidfile_path(store).read_text(encoding="utf-8")
        snapshots.append(parse_pidfile(text))
        verdicts.append(assess_liveness(pidfile_path(store)))

    run_notifyd(
        store=store,
        interval=120.0,
        channels={},
        sleep=_sleep,
        now_fn=_now,
        max_iterations=3,
        terminal_report_every=0,
        nudge_sweep_minutes=0,
    )
    return snapshots, verdicts, t0


def test_the_heartbeat_is_written_once_per_sleeping_tick(tmp_path):
    # Arrange
    # Act
    snapshots, _verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert — no sleep after the final (short-circuited) tick.
    assert len(snapshots) == 2


def test_the_heartbeat_records_our_pid(tmp_path):
    # Arrange
    # Act
    snapshots, _verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert
    assert all(s["pid"] == os.getpid() for s in snapshots)


def test_the_heartbeat_records_our_host(tmp_path):
    # Arrange
    # Act
    snapshots, _verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert
    assert all(s["host"] == local_identity()["host"] for s in snapshots)


def test_the_heartbeat_records_our_pid_namespace(tmp_path):
    # Arrange
    # Act
    snapshots, _verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert
    assert all(s["pid_ns"] == local_identity()["pid_ns"] for s in snapshots)


def test_the_heartbeat_records_the_configured_interval(tmp_path):
    # Arrange
    # Act
    snapshots, _verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert
    assert all(float(s["interval"]) == 120.0 for s in snapshots)


def test_the_heartbeat_writer_is_recognised_as_local(tmp_path):
    # Arrange
    # Act
    snapshots, _verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert
    assert all(writer_is_local(s) is True for s in snapshots)


def test_daemon_refreshes_the_pidfile_heartbeat_every_tick(tmp_path):
    """The pidfile is REWRITTEN each tick with a fresh stamp.

    A reader in another PID namespace (a container sharing the store by
    bind-mount) cannot interpret our pid at all — freshness is all it has. So
    the stamp must actually advance, tick over tick, not just be written once.
    """
    # Arrange
    # Act
    snapshots, _verdicts, t0 = _heartbeat_snapshots(tmp_path)
    ages = [heartbeat_age_seconds(s, t0 + _dt.timedelta(hours=1)) for s in snapshots]
    # Assert — a frozen stamp would read as a dead daemon to every
    # cross-namespace checker.
    assert ages[0] > ages[1], f"heartbeat did not advance between ticks: {ages}"


def test_a_running_daemon_probes_as_alive(tmp_path):
    # Arrange
    # Act
    _snapshots, verdicts, _t0 = _heartbeat_snapshots(tmp_path)
    # Assert — a health probe taken WHILE the daemon runs sees it as alive.
    assert all(v["ok"] and v["state"] == "alive" for v in verdicts)


# EOF
