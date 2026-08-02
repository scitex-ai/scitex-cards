#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex_cards._stale_active_nudge`` (delivery sweep).

No mocks (STX-NM / PA-306): the sweep delivers on the REAL pull-inbox rail
(:func:`scitex_cards._inbox.enqueue`) against a ``tmp_path``-scoped store, and
every delivery assertion reads the OWNER'S REAL INBOX back with
:func:`scitex_cards._inbox.poll_inbox` — the same call an agent's drain makes.
The fail-soft tests inject a REAL failing enqueue function (the documented seam,
like ``sweep_reminders``'s), never a mock of the sweep itself.

This is the 2026-07-12 fix: the nudge used to push on the turn-url wire, which
is not provisioned for almost any agent — the scheduled sweep delivered to
NOBODY (every owner ``ERR ... reason=no-turn-url-configured``). It now rides the
SAME rail the owner digest does.

The sweep persists its deliver-on-change state in a sidecar under the store's
``runtime/`` dir, so every test runs against a REAL but ``tmp_path``-scoped
store (``_isolated_store``) — never the user's canonical one.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scitex_cards._db import ENV_DB
from scitex_cards._inbox import enqueue as real_enqueue
from scitex_cards._inbox import poll_inbox
from scitex_cards._push import ENV_DRY_RUN
from scitex_cards._stale_active import (
    ENV_PENDING_NUDGE_HOURS,
    ENV_STALE_ACTIVE_HOURS,
)
from scitex_cards._stale_active_nudge import (
    ENV_NUDGE_FLOOR_HOURS,
    ENV_NUDGE_PUSH,
    KIND_PENDING_BACKLOG,
    KIND_STALE_ACTIVE,
    NUDGE_CARD_ID,
    load_nudge_state,
    sweep_and_nudge,
)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the store (and therefore the inbox + nudge sidecar) at a tmp dir."""
    from scitex_cards._db import connect, init_schema

    db = tmp_path / "cards.db"
    monkeypatch.setenv(ENV_DB, str(db))
    conn = connect(str(db))
    try:
        init_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return db


@contextlib.contextmanager
def _local_receiver():
    """A REAL local turn-url receiver (no mocks): answers 200, records bodies."""
    received: list[bytes] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's contract.
            length = int(self.headers.get("Content-Length") or 0)
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):  # keep the test output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/turn", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _iso_hours_ago(h):
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    return (now - _dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale(cid, agent, status="in_progress"):
    return {
        "id": cid,
        "status": status,
        "title": cid,
        "agent": agent,
        "last_activity": _iso_hours_ago(10),
    }


def _pending(cid, agent):
    return {
        "id": cid,
        "status": "deferred",
        "title": cid,
        "agent": agent,
        "last_activity": _iso_hours_ago(48),
    }


def _fresh(cid, agent):
    return {
        "id": cid,
        "status": "in_progress",
        "title": cid,
        "agent": agent,
        "last_activity": _iso_hours_ago(0.1),
    }


#: A fixed sweep instant. The deliver-on-change tests run TWO sweeps and the
#: inbox dedups on ``(event_type, card_id, ts, actor)`` at second resolution, so
#: back-to-back wall-clock sweeps inside one second would dedup and look like a
#: failed re-delivery. Real sweeps are ~30 min apart; the tests pin the clock.
T0 = _dt.datetime(2026, 7, 11, 9, 0, tzinfo=_dt.timezone.utc)
T1 = T0 + _dt.timedelta(minutes=30)


def _at(base, hours):
    return (base - _dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stale_at(cid, agent, *, base=T0, hours=10):
    return {
        "id": cid,
        "status": "in_progress",
        "title": cid,
        "agent": agent,
        "last_activity": _at(base, hours),
    }


def _pending_at(cid, agent, *, base=T0, hours=48):
    return {
        "id": cid,
        "status": "deferred",
        "title": cid,
        "agent": agent,
        "last_activity": _at(base, hours),
    }


def _fresh_at(cid, agent, *, base=T0):
    return _stale_at(cid, agent, base=base, hours=0.1)


def _drain(owner):
    """Read + ACK an owner's unseen inbox, exactly like an agent's drain."""
    return poll_inbox(owner, unseen_only=True, mark_seen=True)


def _failing_enqueue(*owners):
    """A REAL enqueue that RAISES for ``owners`` and delivers for everyone else.

    The injectable ``enqueue`` seam (same one ``sweep_reminders`` exposes), not
    a mock of the sweep: healthy owners still land in the REAL inbox, so the
    fail-soft assertions read the real store.
    """
    bad = set(owners)

    def _enqueue(recipient_id, **kwargs):
        if recipient_id in bad:
            raise RuntimeError(f"inbox write refused for {recipient_id}")
        return real_enqueue(recipient_id, **kwargs)

    return _enqueue


#: One sweep, two owners, one stale card each. Six tests split what a single
#: test claimed about it: alpha got EXACTLY one record, of the right kind, under
#: the canonical card id, naming alpha's card and NOT beta's — and beta got
#: their own. The two cross-contamination checks are the load-bearing ones: a
#: sweep that fanned every owner's cards to every owner would sail through a
#: bare "did alpha get something?" and be worthless in production.
def _sweep_two_owners(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    sweep_and_nudge([_stale("a1", "alpha"), _stale("b1", "beta")])


def _sweep_one_pending(monkeypatch):
    monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
    sweep_and_nudge([_pending("p1", "alpha")])
    return poll_inbox("alpha", unseen_only=True)


def _sweep_one_fresh_owner(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    return sweep_and_nudge([_fresh("a1", "alpha")])


def _sweep_one_unassigned(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    tasks = [
        {
            "id": "x",
            "status": "blocked",
            "title": "x",
            "last_activity": _iso_hours_ago(10),
        }
    ]
    return sweep_and_nudge(tasks)


class TestNudgeLandsInTheOwnerInbox:
    """The whole point: the nudge must REACH the owner, on the rail they drain."""

    def test_stale_nudge_lands_in_that_owners_inbox(self, monkeypatch):
        # Arrange
        _sweep_two_owners(monkeypatch)
        # Act
        alpha = poll_inbox("alpha", unseen_only=True)
        # Assert — one record, not zero and not a per-card storm.
        assert len(alpha) == 1

    def test_the_stale_nudge_carries_the_stale_active_kind(self, monkeypatch):
        # Arrange
        _sweep_two_owners(monkeypatch)
        # Act
        rec = poll_inbox("alpha", unseen_only=True)[0]
        # Assert
        assert rec["event_type"] == KIND_STALE_ACTIVE

    def test_the_stale_nudge_uses_the_canonical_card_id(self, monkeypatch):
        # Arrange
        _sweep_two_owners(monkeypatch)
        # Act
        rec = poll_inbox("alpha", unseen_only=True)[0]
        # Assert — a stable id is what lets the next nudge supersede this one.
        assert rec["card_id"] == NUDGE_CARD_ID[KIND_STALE_ACTIVE]

    def test_the_stale_nudge_names_that_owners_card(self, monkeypatch):
        # Arrange
        _sweep_two_owners(monkeypatch)
        # Act
        rec = poll_inbox("alpha", unseen_only=True)[0]
        # Assert
        assert "a1" in rec["body"]

    def test_the_stale_nudge_omits_another_owners_card(self, monkeypatch):
        # Arrange
        _sweep_two_owners(monkeypatch)
        # Act
        rec = poll_inbox("alpha", unseen_only=True)[0]
        # Assert — it went to ALPHA about ALPHA, not to everybody about everybody.
        assert "b1" not in rec["body"]

    def test_each_owner_gets_their_own_stale_nudge(self, monkeypatch):
        # Arrange
        _sweep_two_owners(monkeypatch)
        # Act
        beta = poll_inbox("beta", unseen_only=True)
        # Assert
        assert len(beta) == 1 and "b1" in beta[0]["body"]

    def test_pending_backlog_nudge_lands_in_the_owner_inbox(self, monkeypatch):
        # Arrange
        recs = _sweep_one_pending(monkeypatch)
        # Act
        kinds = [r["event_type"] for r in recs]
        # Assert
        assert kinds == [KIND_PENDING_BACKLOG]

    def test_the_pending_nudge_uses_its_own_card_id(self, monkeypatch):
        # Arrange
        recs = _sweep_one_pending(monkeypatch)
        # Act
        card_id = recs[0]["card_id"]
        # Assert — a separate id, so the two kinds supersede independently.
        assert card_id == NUDGE_CARD_ID[KIND_PENDING_BACKLOG]

    def test_the_pending_nudge_names_the_backlog_card(self, monkeypatch):
        # Arrange
        recs = _sweep_one_pending(monkeypatch)
        # Act
        body = recs[0]["body"]
        # Assert
        assert "p1" in body

    def test_both_kinds_are_distinct_records(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        sweep_and_nudge([_stale("s1", "alpha"), _pending("p1", "alpha")])
        # Act
        kinds = sorted(r["event_type"] for r in poll_inbox("alpha"))
        # Assert — one owner, two concerns, two records.
        assert kinds == [KIND_PENDING_BACKLOG, KIND_STALE_ACTIVE]

    def test_fresh_owner_gets_nothing(self, monkeypatch):
        # Arrange
        _sweep_one_fresh_owner(monkeypatch)
        # Act
        recs = poll_inbox("alpha")
        # Assert
        assert recs == []

    def test_a_fresh_only_sweep_reports_zero_delivered(self, monkeypatch):
        # Arrange
        lines = _sweep_one_fresh_owner(monkeypatch)
        # Act
        zero_lines = [ln for ln in lines if "0 delivered" in ln]
        # Assert — silence on the rail is still reported in the summary.
        assert zero_lines != []

    def test_unassigned_surfaced_not_delivered(self, monkeypatch):
        # Arrange
        _sweep_one_unassigned(monkeypatch)
        # Act
        recs = poll_inbox("(unassigned)")
        # Assert — there is no owner to drain that inbox, so nothing is queued.
        assert recs == []

    def test_unassigned_cards_are_surfaced_in_the_summary(self, monkeypatch):
        # Arrange
        lines = _sweep_one_unassigned(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — undeliverable must not mean invisible.
        assert "(unassigned)" in joined and "no owner" in joined

    def test_summary_counts_the_inbox_rail(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        # Act
        lines = sweep_and_nudge([_stale("a1", "alpha"), _stale("b1", "beta")])
        # Assert
        assert (
            "# stale-active: 2 owner(s) detected, 2 delivered (inbox), "
            "0 suppressed, 0 failed" in lines
        )


#: Two sweeps over an UNCHANGED stale set, with the agent draining in between.
#: Returns ``(first_drain, second_sweep_lines)``. Four tests split what one
#: asserted: the first sweep really did deliver (without which the suppression
#: below would be indistinguishable from a sweep that never worked), the second
#: delivered nothing new, the owner is still NAMED with a reason, and the
#: summary counts it as suppressed rather than as a silent zero.
def _two_sweeps_unchanged(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    tasks = [_stale_at("a1", "alpha")]
    sweep_and_nudge(tasks, now=T0)
    first_drain = _drain("alpha")
    lines = sweep_and_nudge(tasks, now=T1)
    return first_drain, lines


#: A genuinely stuck owner must still be nudged once the FLOOR elapses, even
#: though nothing about their card set changed. Returns the unseen inbox at
#: three points: after the first sweep + drain, inside the floor, and past it.
def _three_sweeps_across_the_floor(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    monkeypatch.setenv(ENV_NUDGE_FLOOR_HOURS, "24")
    tasks = [_stale_at("a1", "alpha")]

    sweep_and_nudge(tasks, now=T0)
    first_drain = _drain("alpha")

    sweep_and_nudge(tasks, now=T0 + _dt.timedelta(hours=6))
    inside_floor = poll_inbox("alpha", unseen_only=True)

    sweep_and_nudge(tasks, now=T0 + _dt.timedelta(hours=25))
    past_floor = poll_inbox("alpha", unseen_only=True)
    return first_drain, inside_floor, past_floor


class TestDeliverOnChange:
    """The sweep is SCHEDULED, so an unchanged stale set must not re-deliver."""

    def test_the_first_sweep_delivers_one_nudge(self, monkeypatch):
        # Arrange
        first_drain, _lines = _two_sweeps_unchanged(monkeypatch)
        # Act
        delivered = len(first_drain)
        # Assert — the agent read it, so the suppression below means something.
        assert delivered == 1

    def test_unchanged_set_is_suppressed_on_second_sweep(self, monkeypatch):
        # Arrange
        _first_drain, _lines = _two_sweeps_unchanged(monkeypatch)
        # Act
        pending = poll_inbox("alpha", unseen_only=True)
        # Assert — nothing new landed on the second sweep.
        assert pending == []

    def test_a_suppressed_owner_is_still_named_with_a_reason(self, monkeypatch):
        # Arrange
        _first_drain, lines = _two_sweeps_unchanged(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — suppressed, but NOT silent.
        assert "alpha" in joined and "suppressed (unchanged since" in joined

    def test_the_summary_counts_the_suppression(self, monkeypatch):
        # Arrange
        _first_drain, lines = _two_sweeps_unchanged(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — a suppressed 0 must be distinguishable from a failed 0.
        assert "0 delivered (inbox), 1 suppressed" in joined

    def test_added_card_redelivers(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T0)
        _drain("alpha")
        # Act
        sweep_and_nudge([_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T1)
        # Assert — the set grew, so the owner hears about it.
        fresh = poll_inbox("alpha", unseen_only=True)
        assert len(fresh) == 1 and "a2" in fresh[0]["body"]

    def test_removed_card_redelivers(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T0)
        _drain("alpha")
        # Act
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T1)
        # Assert — shrinking is a change too.
        assert len(poll_inbox("alpha", unseen_only=True)) == 1

    def test_touched_card_leaving_the_bucket_redelivers(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T0)
        _drain("alpha")
        # Act
        sweep_and_nudge(
            [_stale_at("a1", "alpha"), _fresh_at("a2", "alpha", base=T1)], now=T1
        )
        # Assert — a2 was touched, so it drops out and the set CHANGED.
        assert len(poll_inbox("alpha", unseen_only=True)) == 1

    def test_owner_state_pruned_when_no_cards_left(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T0)
        # Act
        sweep_and_nudge([_fresh_at("a1", "alpha", base=T1)], now=T1)
        # Assert — no stale cards left, so the sidecar keeps no entry either.
        assert "alpha" not in load_nudge_state()[KIND_STALE_ACTIVE]

    def test_the_sweep_before_the_floor_delivers_once(self, monkeypatch):
        # Arrange
        first_drain, _inside, _past = _three_sweeps_across_the_floor(monkeypatch)
        # Act
        delivered = len(first_drain)
        # Assert
        assert delivered == 1

    def test_inside_the_floor_an_unchanged_set_stays_suppressed(self, monkeypatch):
        # Arrange
        _first, inside_floor, _past = _three_sweeps_across_the_floor(monkeypatch)
        # Act
        pending = inside_floor
        # Assert — six hours is not yet worth re-waking a stuck owner.
        assert pending == []

    def test_floor_elapsed_redelivers_the_unchanged_set(self, monkeypatch):
        # Arrange
        _first, _inside, past_floor = _three_sweeps_across_the_floor(monkeypatch)
        # Act
        pending = past_floor
        # Assert — suppression has a ceiling; a stuck owner is nudged again.
        assert len(pending) == 1

    def test_unseen_nudge_is_superseded_not_stacked(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        sweep_and_nudge([_stale_at("a1", "alpha")], now=T0)
        # Act
        sweep_and_nudge([_stale_at("a1", "alpha"), _stale_at("a2", "alpha")], now=T1)
        # Assert — a drain-down owner must not accumulate a replay storm.
        pending = poll_inbox("alpha", unseen_only=True)
        assert len(pending) == 1 and "a2" in pending[0]["body"]

    def test_pending_backlog_is_suppressed_independently(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        tasks = [_stale_at("s1", "alpha"), _pending_at("p1", "alpha")]
        sweep_and_nudge(tasks, now=T0)
        _drain("alpha")
        # Act
        sweep_and_nudge([*tasks, _stale_at("s2", "alpha")], now=T1)
        # Assert — only the stale-active set changed, so only it re-delivers.
        kinds = [r["event_type"] for r in poll_inbox("alpha", unseen_only=True)]
        assert kinds == [KIND_STALE_ACTIVE]


#: One sweep where ONE owner's enqueue raises. Four tests split what one
#: asserted: the healthy owner was still delivered (fail-SOFT), the bad owner
#: got nothing, the failure is LOUD in the log, and the summary counts one of
#: each. The last two are the fail-LOUD half — a batch that swallowed the error
#: and reported a bland zero is the shipped bug this file exists for.
def _sweep_with_one_bad_owner(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    return sweep_and_nudge(
        [_stale("s1", "badowner"), _stale("s2", "goodowner")],
        enqueue=_failing_enqueue("badowner"),
    )


#: A failed enqueue must NOT arm the suppression, so the NEXT sweep retries.
#: Returns ``(armed_after_failure, retry_lines)``.
def _failed_then_retried(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    tasks = [_stale_at("a1", "alpha")]
    sweep_and_nudge(tasks, now=T0, enqueue=_failing_enqueue("alpha"))
    armed = "alpha" in load_nudge_state()[KIND_STALE_ACTIVE]
    lines = sweep_and_nudge(tasks, now=T1)  # real enqueue this time
    return armed, lines


#: The exact shape of the shipped bug: detected > 0, delivered == 0. It must be
#: UNMISTAKABLE in the log, not a bland "0 sent" — hence three separate checks
#: on the alert's wording.
def _sweep_with_every_owner_failing(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    return sweep_and_nudge(
        [_stale("a1", "alpha"), _stale("b1", "beta")],
        enqueue=_failing_enqueue("alpha", "beta"),
    )


#: 0 delivered because everyone is SUPPRESSED is the healthy steady state — it
#: must NOT cry wolf, and it must still say why it was zero.
def _sweep_twice_all_suppressed(monkeypatch):
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    tasks = [_stale("a1", "alpha")]
    sweep_and_nudge(tasks)
    return sweep_and_nudge(tasks)


class TestFailSoftAndFailLoud:
    """A bad owner never aborts the batch — and never passes for delivered."""

    def test_one_owner_raising_does_not_starve_the_other(self, monkeypatch):
        # Arrange
        _sweep_with_one_bad_owner(monkeypatch)
        # Act
        good = poll_inbox("goodowner", unseen_only=True)
        # Assert — the healthy owner is delivered, read back off the real inbox.
        assert len(good) == 1 and "s2" in good[0]["body"]

    def test_the_failing_owner_receives_nothing(self, monkeypatch):
        # Arrange
        _sweep_with_one_bad_owner(monkeypatch)
        # Act
        bad = poll_inbox("badowner")
        # Assert — a raising enqueue must not half-land a record.
        assert bad == []

    def test_the_failing_owner_is_reported_loudly(self, monkeypatch):
        # Arrange
        lines = _sweep_with_one_bad_owner(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — the loss is named, not swallowed into a quiet 0.
        assert "ERR" in joined and "badowner" in joined

    def test_the_summary_counts_delivered_and_failed_apart(self, monkeypatch):
        # Arrange
        lines = _sweep_with_one_bad_owner(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert
        assert "1 delivered (inbox)" in joined and "1 failed" in joined

    def test_failed_enqueue_does_not_arm_suppression(self, monkeypatch):
        # Arrange
        armed, _lines = _failed_then_retried(monkeypatch)
        # Act
        suppression_armed = armed
        # Assert — nothing delivered means no state, so the next sweep RETRIES.
        assert suppression_armed is False

    def test_the_retry_sweep_reports_a_fresh_delivery(self, monkeypatch):
        # Arrange
        _armed, lines = _failed_then_retried(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — the retry is a delivery, not a suppressed repeat.
        assert "1 delivered (inbox), 0 suppressed" in joined

    def test_the_retry_sweep_lands_in_the_owner_inbox(self, monkeypatch):
        # Arrange
        _failed_then_retried(monkeypatch)
        # Act
        pending = poll_inbox("alpha", unseen_only=True)
        # Assert
        assert len(pending) == 1

    def test_every_owner_failing_screams(self, monkeypatch):
        # Arrange
        lines = _sweep_with_every_owner_failing(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert
        assert "!! ALERT stale-active" in joined

    def test_the_total_failure_alert_says_it_reached_nobody(self, monkeypatch):
        # Arrange
        lines = _sweep_with_every_owner_failing(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — plain English, not a number the reader has to interpret.
        assert "reached NOBODY" in joined

    def test_the_total_failure_alert_counts_the_attempts(self, monkeypatch):
        # Arrange
        lines = _sweep_with_every_owner_failing(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert
        assert "0 of 2 attempted" in joined

    def test_all_suppressed_is_not_an_alert(self, monkeypatch):
        # Arrange
        lines = _sweep_twice_all_suppressed(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — the healthy steady state must not cry wolf.
        assert "ALERT" not in joined

    def test_an_all_suppressed_sweep_still_says_why(self, monkeypatch):
        # Arrange
        lines = _sweep_twice_all_suppressed(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — quiet, but never unexplained.
        assert "1 suppressed" in joined

    def test_sweep_never_raises_on_a_broken_enqueue(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        # Act
        lines = sweep_and_nudge(
            [_stale("s1", "alpha"), _pending("p1", "alpha")],
            enqueue=_failing_enqueue("alpha"),
        )
        # Assert — the call returned, and the stale-active kind still summarised.
        assert any(ln.startswith("# stale-active:") for ln in lines)

    def test_a_broken_enqueue_does_not_abort_the_other_kind(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "24")
        # Act
        lines = sweep_and_nudge(
            [_stale("s1", "alpha"), _pending("p1", "alpha")],
            enqueue=_failing_enqueue("alpha"),
        )
        # Assert — the backlog kind is summarised too; neither aborted the other.
        assert any(ln.startswith("# pending-backlog:") for ln in lines)


#: The default rail is the inbox. With no turn URL configured and no dry-run,
#: the sweep must still DELIVER — the old code reported
#: `reason=no-turn-url-configured` and delivered to nobody. Four tests: the
#: inbox landed, the summary says so, and NEITHER the push wording nor the old
#: excuse appears anywhere in the log.
def _sweep_with_no_push_configured(monkeypatch):
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    return sweep_and_nudge([_stale("a1", "nourlowner")])


#: The opt-in echo, against a REAL local receiver. Returns
#: ``(received_bodies, lines)`` once the receiver has shut down. Both rails must
#: fire — the echo is secondary, never a substitute for the inbox.
def _sweep_with_opt_in_echo(monkeypatch):
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    monkeypatch.setenv(ENV_NUDGE_PUSH, "1")
    with _local_receiver() as (url, received):
        monkeypatch.setenv("SCITEX_TODO_TURN_URL_ECHOOWNER", url)
        lines = sweep_and_nudge([_stale("s1", "echoowner")])
    return received, lines


#: No silent fallback BETWEEN rails: a dead push receiver must not make a landed
#: inbox nudge look failed, must still be reported, and must not disturb the
#: suppression state the inbox delivery armed. ``deliver`` slugs the agent name
#: as UPPER with '-'→'_'; a URL with no scheme makes the echo raise.
def _sweep_with_a_broken_echo(monkeypatch):
    monkeypatch.delenv(ENV_DRY_RUN, raising=False)
    monkeypatch.delenv("SCITEX_TODO_AGENT_TURN_URLS", raising=False)
    monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "2")
    monkeypatch.setenv(ENV_NUDGE_PUSH, "1")
    monkeypatch.setenv("SCITEX_TODO_TURN_URL_BADECHO", "noscheme-url")
    return sweep_and_nudge([_stale("s1", "badecho")])


class TestOptionalPushEcho:
    """``_push`` survives ONLY as an opt-in, strictly-secondary echo."""

    def test_no_push_by_default(self, monkeypatch):
        # Arrange
        lines = _sweep_with_no_push_configured(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — the push rail is not mentioned because it was not used.
        assert "push echo" not in joined

    def test_an_owner_with_no_turn_url_still_gets_the_inbox_nudge(self, monkeypatch):
        # Arrange
        _sweep_with_no_push_configured(monkeypatch)
        # Act
        pending = poll_inbox("nourlowner", unseen_only=True)
        # Assert — the whole 2026-07-12 fix, in one assertion.
        assert len(pending) == 1

    def test_the_summary_reports_the_inbox_delivery(self, monkeypatch):
        # Arrange
        lines = _sweep_with_no_push_configured(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert
        assert "1 delivered (inbox)" in joined

    def test_the_old_no_turn_url_excuse_is_gone(self, monkeypatch):
        # Arrange
        lines = _sweep_with_no_push_configured(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — the exact string the broken sweep used to log for every owner.
        assert "no-turn-url-configured" not in joined

    def test_opt_in_echo_reaches_a_real_receiver(self, monkeypatch):
        # Arrange
        received, _lines = _sweep_with_opt_in_echo(monkeypatch)
        # Act
        posted = len(received)
        # Assert — a real HTTP body reached a real listening socket.
        assert posted == 1

    def test_opt_in_echo_reaches_a_real_receiver_and_the_inbox(self, monkeypatch):
        # Arrange
        _sweep_with_opt_in_echo(monkeypatch)
        # Act
        recs = poll_inbox("echoowner")
        # Assert — the echo is additive; the inbox still landed.
        assert len(recs) == 1

    def test_the_summary_reports_the_successful_echo(self, monkeypatch):
        # Arrange
        _received, lines = _sweep_with_opt_in_echo(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert
        assert "push echo ok=True" in joined

    def test_a_broken_echo_never_fails_the_inbox_delivery(self, monkeypatch):
        # Arrange
        _sweep_with_a_broken_echo(monkeypatch)
        # Act
        pending = poll_inbox("badecho", unseen_only=True)
        # Assert — the primary rail is untouched by the secondary one's failure.
        assert len(pending) == 1

    def test_a_broken_echo_is_not_counted_as_a_failed_delivery(self, monkeypatch):
        # Arrange
        lines = _sweep_with_a_broken_echo(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — a landed nudge must not be reported as failed.
        assert "1 delivered (inbox)" in joined and "0 failed" in joined

    def test_a_broken_echo_is_still_reported(self, monkeypatch):
        # Arrange
        lines = _sweep_with_a_broken_echo(monkeypatch)
        # Act
        joined = "\n".join(lines)
        # Assert — secondary does not mean invisible.
        assert "push echo raised" in joined

    def test_a_broken_echo_leaves_the_suppression_armed(self, monkeypatch):
        # Arrange
        _sweep_with_a_broken_echo(monkeypatch)
        # Act
        state = load_nudge_state()[KIND_STALE_ACTIVE]
        # Assert — the inbox delivery armed it; the echo must not disarm it.
        assert "badecho" in state


# EOF
