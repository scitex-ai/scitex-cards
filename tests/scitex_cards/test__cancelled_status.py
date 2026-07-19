#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``cancelled`` status — GitHub "closed as not planned" terminal state.

A ``cancelled`` card is CLOSED: distinct from ``done`` (completed
successfully) and ``failed`` (attempted, did not succeed), but treated as
terminal everywhere a closed status is. It must drop out of every
open / actionable / stale / backlog view exactly like ``done`` does, must
not be nagged by the reminder sweep, must not be overdue, and must not
count toward runnable liveness.

Real fakes, NO mocks (STX-NM / PA-306): real ``tmp_path`` YAML stores,
real list-of-dicts inputs, a plain list-recorder ``enqueue``, a real
``resolve_key`` dict, and an injected ``now``. AAA structure.
"""

from __future__ import annotations

import datetime as _dt
import warnings

import pytest

from scitex_cards._model import VALID_STATUSES, is_overdue, load_tasks
from scitex_cards._reconcile_prs import (
    ACTION_SKIP_NOT_OPEN,
    MERGED,
    OPEN_STATUSES,
    decide_reconcile_action,
)
from scitex_cards._reminders import EVENT_DIGEST, sweep_reminders
from scitex_cards._stale_active import (
    detect_pending_backlog,
    detect_stale_active,
    is_stale_active,
)
from scitex_cards._throughput import TERMINAL_STATUSES, aggregate


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _utc(2026, 6, 30, 12, 0, 0)


def _write_store(tmp_path, text):
    store = tmp_path / "tasks.yaml"
    store.write_text(text, encoding="utf-8")
    return store


# --------------------------------------------------------------------------- #
# enum membership + validation                                               #
# --------------------------------------------------------------------------- #


def test_cancelled_is_in_valid_statuses():
    # Arrange
    statuses = VALID_STATUSES
    # Act
    known = "cancelled" in statuses
    # Assert — "cancelled" is a recognized lifecycle status.
    assert known


def test_cancelled_is_distinct_from_done_and_failed():
    # Arrange
    statuses = set(VALID_STATUSES)
    # Act
    trio = {"done", "failed", "cancelled"}
    # Assert — it is its OWN status, not an alias of done/failed.
    assert trio <= statuses


def test_load_tasks_accepts_cancelled_status(tmp_path):
    # Arrange — a store with a single cancelled card.
    store = _write_store(
        tmp_path, "tasks:\n  - {id: x, title: Abandoned, status: cancelled}\n"
    )
    # Act
    tasks = load_tasks(store)
    # Assert — validates OK and round-trips the status.
    assert tasks[0]["status"] == "cancelled"


def test_load_tasks_warns_on_unknown_status(tmp_path):
    # Guard, updated for the tolerant reader (2026-07-10 outage fix): an
    # unknown status VALUE no longer takes the whole store down — it may have
    # been written by a newer agent, and raising here is exactly how adding
    # `cancelled` bricked every older reader. It still warns loudly, naming
    # the card, and the closed enum stays enforced at the SOURCES (the CLI
    # --status Choice, the board handlers' 400).
    # Arrange
    store = _write_store(tmp_path, "tasks:\n  - {id: x, title: X, status: wibble}\n")
    # Act
    # Assert
    with pytest.warns(UserWarning, match="wibble"):
        load_tasks(store)


def test_load_tasks_keeps_an_unknown_status_value(tmp_path):
    """The tolerant reader must PRESERVE the value it warned about, not
    normalize or drop it — a newer agent's status has to survive a
    round-trip through an older reader."""
    # Arrange
    store = _write_store(tmp_path, "tasks:\n  - {id: x, title: X, status: wibble}\n")
    # Act
    # (the warning itself is pinned by the sibling test; silence it here so
    # this test asserts only the preserved value)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        tasks = load_tasks(store)
    # Assert
    assert tasks[0]["status"] == "wibble"


# --------------------------------------------------------------------------- #
# terminal / closed predicate                                                #
# --------------------------------------------------------------------------- #


def test_cancelled_is_a_terminal_status():
    # Arrange
    terminal = TERMINAL_STATUSES
    # Act
    is_terminal = "cancelled" in terminal
    # Assert — the central closed-status SSOT includes cancelled.
    assert is_terminal


def test_in_progress_is_not_a_terminal_status():
    # Arrange
    terminal = TERMINAL_STATUSES
    # Act
    is_terminal = "in_progress" in terminal
    # Assert — sanity: open statuses are not terminal.
    assert not is_terminal


def test_pending_is_not_a_terminal_status():
    # Arrange
    terminal = TERMINAL_STATUSES
    # Act
    is_terminal = "pending" in terminal
    # Assert
    assert not is_terminal


def test_cancelled_not_in_reconcile_open_statuses():
    # Arrange
    open_statuses = OPEN_STATUSES
    # Act
    treated_as_open = "cancelled" in open_statuses
    # Assert — the PR-reconciler never treats cancelled as open work.
    assert not treated_as_open


def test_reconcile_skips_cancelled_even_when_pr_merged():
    # Arrange — a cancelled card with a merged PR.
    task = {
        "status": "cancelled",
        "pr_url": "https://github.com/o/r/pull/1",
    }
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert — NOT auto-flipped; it is closed.
    assert action == ACTION_SKIP_NOT_OPEN


# --------------------------------------------------------------------------- #
# throughput — cancelled is NOT open backlog                                 #
# --------------------------------------------------------------------------- #


_MIXED_STATUS_TASKS = [
    {"id": "open1", "title": "o", "status": "in_progress", "agent": "a"},
    {"id": "done1", "title": "d", "status": "done", "agent": "a"},
    {"id": "cxl1", "title": "c", "status": "cancelled", "agent": "a"},
]


def test_aggregate_groups_one_owner_into_one_row():
    # Arrange — one open card + one done + one cancelled, same owner.
    tasks = [dict(t) for t in _MIXED_STATUS_TASKS]
    # Act
    rows = aggregate(tasks, by="agent")
    # Assert
    assert len(rows) == 1


def test_cancelled_excluded_from_open_count_like_done():
    # Arrange
    tasks = [dict(t) for t in _MIXED_STATUS_TASKS]
    # Act
    rows = aggregate(tasks, by="agent")
    # Assert — only the in_progress card counts as open. cancelled drops
    # out exactly like done.
    assert rows[0].open_count == 1


# --------------------------------------------------------------------------- #
# stale-active + pending-backlog detection                                   #
# --------------------------------------------------------------------------- #


def _card(cid, status, *, hours_ago):
    return {
        "id": cid,
        "title": cid,
        "status": status,
        "agent": "a",
        "last_activity": _iso(NOW - _dt.timedelta(hours=hours_ago)),
    }


def test_cancelled_is_never_stale_active():
    # Arrange — a long-untouched cancelled card.
    t = _card("x", "cancelled", hours_ago=999)
    # Act
    verdict = is_stale_active(t, now=NOW, stale_hours=2.0)
    # Assert — it is closed, not live work the owner is claiming.
    assert verdict is False


def test_cancelled_excluded_from_stale_active_detection():
    # Arrange — one genuinely stale in_progress + one stale cancelled.
    tasks = [
        _card("live", "in_progress", hours_ago=10),
        _card("cxl", "cancelled", hours_ago=10),
    ]
    # Act
    groups = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
    # Assert — only the live card surfaces; cancelled never does.
    surfaced = {sc.id for cards in groups.values() for sc in cards}
    assert surfaced == {"live"}


def test_cancelled_excluded_from_backlog():
    # Arrange — one old deferred (the backlog status since the pending
    # abolition) + one old cancelled.
    tasks = [
        _card("waiting", "deferred", hours_ago=99),
        _card("cxl", "cancelled", hours_ago=99),
    ]
    # Act
    groups = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
    # Assert — only the deferred card is backlog; cancelled is closed.
    surfaced = {sc.id for cards in groups.values() for sc in cards}
    assert surfaced == {"waiting"}


# --------------------------------------------------------------------------- #
# reminder sweep — cancelled is never nagged                                 #
# --------------------------------------------------------------------------- #


class _EnqueueRecorder:
    """A real ``enqueue`` callable — records each call, returns a record."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(
        self,
        recipient_key,
        *,
        event_type,
        card_id,
        body,
        actor,
        ts,
        store,
        supersede=False,
    ):
        rec = {
            "recipient": recipient_key,
            "event_type": event_type,
            "card_id": card_id,
            "body": body,
        }
        self.calls.append(rec)
        return rec


@pytest.fixture(autouse=True)
def _isolate_engine(monkeypatch):
    for var in (
        "SCITEX_TODO_REMINDER_OWNERS",
        "SCITEX_TODO_STALE_ACTIVE_HOURS",
        "SCITEX_TODO_PENDING_NUDGE_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("scitex_cards._config.config_paths", lambda: [])


def _sweep(tasks, store):
    rec = _EnqueueRecorder()
    out = sweep_reminders(
        tasks,
        store=store,
        now=NOW,
        enqueue=rec,
        resolve_key=lambda name: name,
    )
    return out, rec


def test_reminder_sweep_digests_nothing_for_a_cancelled_card(tmp_path):
    # Arrange — a single, long-untouched cancelled card.
    store = tmp_path / "tasks.yaml"
    tasks = [_card("cxl", "cancelled", hours_ago=999)]
    # Act
    out, _rec = _sweep(tasks, store)
    # Assert
    assert out["digested"] == []


def test_reminder_sweep_enqueues_nothing_for_a_cancelled_card(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks = [_card("cxl", "cancelled", hours_ago=999)]
    # Act
    _out, rec = _sweep(tasks, store)
    # Assert — no digest enqueued for the cancelled card.
    assert rec.calls == []


def test_reminder_sweep_sends_exactly_one_digest_for_the_live_card(tmp_path):
    # Arrange — a stale in_progress card alongside a stale cancelled card,
    # same owner.
    store = tmp_path / "tasks.yaml"
    tasks = [
        _card("live", "in_progress", hours_ago=10),
        _card("cxl", "cancelled", hours_ago=10),
    ]
    # Act
    _out, rec = _sweep(tasks, store)
    # Assert
    digests = [c for c in rec.calls if c["event_type"] == EVENT_DIGEST]
    assert len(digests) == 1


def test_reminder_sweep_digest_mentions_the_live_card(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks = [
        _card("live", "in_progress", hours_ago=10),
        _card("cxl", "cancelled", hours_ago=10),
    ]
    # Act
    _out, rec = _sweep(tasks, store)
    # Assert
    body = [c for c in rec.calls if c["event_type"] == EVENT_DIGEST][0]["body"]
    assert "live" in body


def test_reminder_sweep_digest_omits_the_cancelled_sibling(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks = [
        _card("live", "in_progress", hours_ago=10),
        _card("cxl", "cancelled", hours_ago=10),
    ]
    # Act
    _out, rec = _sweep(tasks, store)
    # Assert
    body = [c for c in rec.calls if c["event_type"] == EVENT_DIGEST][0]["body"]
    assert "cxl" not in body


# --------------------------------------------------------------------------- #
# is_overdue — cancelled is closed, never overdue                            #
# --------------------------------------------------------------------------- #


def test_cancelled_with_past_deadline_is_not_overdue():
    # Arrange — a cancelled card with a deadline in the past.
    task = {"id": "x", "title": "x", "status": "cancelled", "deadline": "2026-06-01"}
    # Act
    verdict = is_overdue(task, now=_utc(2026, 6, 30, 12, 0, 0))
    # Assert — NOT overdue; it is closed, same as done/deferred/failed.
    assert verdict is False
