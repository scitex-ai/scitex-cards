#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_cards._stale_active`` (pure detector).

Real list-of-dicts inputs (no mocks; STX-NM / PA-306). AAA structure,
one behavioural assertion per test where practical.

Covers:
  * :func:`is_stale_active` — status gate + threshold + fallback
  * :func:`detect_stale_active` — owner grouping, fresh/stale split,
    in_progress/blocked only, created_at fallback, oldest-first order
  * :func:`stale_active_nudge_line` — concise line + id cap
"""

from __future__ import annotations

import datetime as _dt

from scitex_cards._stale_active import (
    DEFAULT_STALE_ACTIVE_HOURS,
    ENV_STALE_ACTIVE_HOURS,
    NUDGE_ID_CAP,
    detect_stale_active,
    is_stale_active,
    stale_active_nudge_line,
)


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _utc(2026, 6, 25, 12, 0, 0)


def _card(cid, status, *, hours_ago=None, created_hours_ago=None, agent="a"):
    t = {"id": cid, "status": status, "title": cid, "agent": agent}
    if hours_ago is not None:
        t["last_activity"] = _iso(NOW - _dt.timedelta(hours=hours_ago))
    if created_hours_ago is not None:
        t["created_at"] = _iso(NOW - _dt.timedelta(hours=created_hours_ago))
    return t


# --------------------------------------------------------------------------- #
# is_stale_active                                                             #
# --------------------------------------------------------------------------- #


class TestIsStaleActive:
    def test_in_progress_old_is_stale(self):
        # Arrange
        t = _card("x", "in_progress", hours_ago=5)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert
        assert stale is True

    def test_in_progress_recent_is_fresh(self):
        # Arrange
        t = _card("x", "in_progress", hours_ago=1)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert
        assert stale is False

    def test_blocked_old_is_stale(self):
        # Arrange
        t = _card("x", "blocked", hours_ago=5)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert
        assert stale is True

    def test_pending_never_stale_active(self):
        # Arrange
        t = _card("x", "pending", hours_ago=99)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert — pending is open but NOT active, so it is out of scope.
        assert stale is False

    def test_done_never_stale_active(self):
        # Arrange
        t = _card("x", "done", hours_ago=99)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert
        assert stale is False

    def test_fallback_to_created_at_when_no_last_activity(self):
        # Arrange
        t = _card("x", "in_progress", created_hours_ago=10)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert
        assert stale is True

    def test_fresh_created_at_fallback_is_not_stale(self):
        # Arrange
        t = _card("x", "in_progress", created_hours_ago=1)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert
        assert stale is False

    def test_no_timestamp_treated_as_stale(self):
        # Arrange
        t = {"id": "x", "status": "in_progress", "agent": "a"}
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert — an untimed active card is the most suspicious kind.
        assert stale is True

    def test_boundary_exactly_threshold_is_fresh(self):
        # Arrange
        t = _card("x", "in_progress", hours_ago=2)
        # Act
        stale = is_stale_active(t, now=NOW, stale_hours=2.0)
        # Assert — age == threshold is NOT > threshold.
        assert stale is False


# --------------------------------------------------------------------------- #
# detect_stale_active                                                         #
# --------------------------------------------------------------------------- #


class TestDetectStaleActive:
    def test_groups_by_owner(self):
        # Arrange
        tasks = [
            _card("a1", "in_progress", hours_ago=5, agent="a"),
            _card("b1", "blocked", hours_ago=5, agent="b"),
        ]
        # Act
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        # Assert — one bucket per owner, so each hears only about their own work.
        assert set(out.keys()) == {"a", "b"}

    def test_fresh_card_excluded(self):
        # Arrange
        tasks = [
            _card("a1", "in_progress", hours_ago=5, agent="a"),
            _card("a2", "in_progress", hours_ago=1, agent="a"),
        ]
        # Act
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        # Assert — a stale owner is not reported with their fresh cards in tow.
        assert [c.id for c in out["a"]] == ["a1"]

    def test_only_in_progress_and_blocked(self):
        # Arrange
        tasks = [
            _card("p", "pending", hours_ago=99, agent="a"),
            _card("d", "done", hours_ago=99, agent="a"),
            _card("ip", "in_progress", hours_ago=99, agent="a"),
            _card("bl", "blocked", hours_ago=99, agent="a"),
        ]
        # Act
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        # Assert
        assert sorted(c.id for c in out["a"]) == ["bl", "ip"]

    def test_owner_with_no_stale_card_absent(self):
        # Arrange
        tasks = [
            _card("a1", "in_progress", hours_ago=1, agent="a"),
            _card("b1", "in_progress", hours_ago=5, agent="b"),
        ]
        # Act
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        # Assert — an owner with nothing stale gets no bucket at all.
        assert "a" not in out

    def test_oldest_first_within_owner(self):
        # Arrange
        tasks = [
            _card("young", "in_progress", hours_ago=3, agent="a"),
            _card("old", "in_progress", hours_ago=20, agent="a"),
        ]
        # Act
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        # Assert
        assert [c.id for c in out["a"]] == ["old", "young"]

    def test_no_timestamp_sorts_first(self):
        # Arrange
        tasks = [
            _card("timed", "in_progress", hours_ago=20, agent="a"),
            {"id": "untimed", "status": "in_progress", "agent": "a"},
        ]
        # Act
        out = detect_stale_active(tasks, now=NOW, stale_hours=2.0)
        # Assert — unknown age sorts as the oldest, never quietly last.
        assert out["a"][0].id == "untimed"

    def test_assignee_fallback_owner(self):
        # Arrange
        t = {
            "id": "x",
            "status": "in_progress",
            "assignee": "z",
            "last_activity": _iso(NOW - _dt.timedelta(hours=9)),
        }
        # Act
        out = detect_stale_active([t], now=NOW, stale_hours=2.0)
        # Assert — with no `agent`, `assignee` names the owner.
        assert "z" in out

    def test_owner_less_card_gets_an_unassigned_bucket(self):
        # Arrange
        t = {
            "id": "x",
            "status": "in_progress",
            "last_activity": _iso(NOW - _dt.timedelta(hours=9)),
        }
        # Act
        out = detect_stale_active([t], now=NOW, stale_hours=2.0)
        # Assert — nobody to nudge is still something to surface.
        assert "(unassigned)" in out

    def test_created_at_fallback_counts(self):
        # Arrange
        t = _card("x", "blocked", created_hours_ago=10, agent="a")
        # Act
        out = detect_stale_active([t], now=NOW, stale_hours=2.0)
        # Assert
        assert out["a"][0].id == "x"

    def test_default_threshold_via_env(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_STALE_ACTIVE_HOURS, "1")
        t = _card("x", "in_progress", hours_ago=1.5, agent="a")
        # Act
        out = detect_stale_active([t], now=NOW)
        # Assert — 1.5 h beats the env-set 1 h, which the 2 h default would not.
        assert out["a"][0].id == "x"

    def test_default_threshold_constant(self):
        # Arrange
        expected_hours = 2.0
        # Act
        actual_hours = DEFAULT_STALE_ACTIVE_HOURS
        # Assert
        assert actual_hours == expected_hours


# --------------------------------------------------------------------------- #
# stale_active_nudge_line                                                     #
# --------------------------------------------------------------------------- #


#: Owner "a"'s detected bucket, holding exactly one 5-hours-stale card. The two
#: wording tests below split what one test used to assert about the same line —
#: that it names the count and the ids, and that it states the threshold it
#: judged them against — so they share this arrangement.
def _one_stale_card():
    return detect_stale_active(
        [_card("c1", "in_progress", hours_ago=5, agent="a")],
        now=NOW,
        stale_hours=2.0,
    )["a"]


class TestNudgeLine:
    def test_line_contains_count_and_ids(self):
        # Arrange
        cards = _one_stale_card()
        # Act
        line = stale_active_nudge_line("a", cards, stale_hours=2.0)
        # Assert — the reader learns how many, and which.
        assert "1 stale card(s)" in line and "c1" in line

    def test_line_mentions_threshold(self):
        # Arrange
        cards = _one_stale_card()
        # Act
        line = stale_active_nudge_line("a", cards, stale_hours=2.0)
        # Assert — the line says what "stale" meant on this sweep.
        assert ">2h" in line

    def test_id_cap_collapses_remainder(self):
        # Arrange
        cards = detect_stale_active(
            [
                _card(f"c{i}", "in_progress", hours_ago=5, agent="a")
                for i in range(NUDGE_ID_CAP + 4)
            ],
            now=NOW,
            stale_hours=2.0,
        )["a"]
        # Act
        line = stale_active_nudge_line("a", cards, stale_hours=2.0)
        # Assert — a long list is summarised, never dumped in full.
        assert "+4 more" in line
