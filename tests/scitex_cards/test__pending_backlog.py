#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the PENDING-backlog detector.

Pure detector (:func:`detect_pending_backlog` + nudge line) in
:mod:`scitex_cards._stale_active`. Real list-of-dicts inputs (no mocks;
STX-NM / PA-306), AAA structure, one behavioural assertion per test
where practical.

Covers:
  * pending cards older than threshold surfaced, grouped by owner,
    oldest-first
  * fresh / recent pending NOT surfaced
  * non-pending statuses NOT surfaced by the pending detector
  * threshold env override respected; missing timestamp → stale
  * distinct nudge wording from stale-active
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards._stale_active import (
    DEFAULT_PENDING_NUDGE_HOURS,
    ENV_PENDING_NUDGE_HOURS,
    NUDGE_ID_CAP,
    detect_pending_backlog,
    pending_backlog_nudge_line,
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
# detect_pending_backlog                                                      #
# --------------------------------------------------------------------------- #


class TestDetectPendingBacklog:
    def test_old_pending_surfaced(self):
        # Arrange
        tasks = [_card("p1", "deferred", hours_ago=30, agent="a")]
        # Act
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        # Assert
        assert [c.id for c in out["a"]] == ["p1"]

    def test_fresh_pending_excluded(self):
        # Arrange
        tasks = [_card("p1", "deferred", hours_ago=2, agent="a")]
        # Act
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        # Assert
        assert "a" not in out

    def test_groups_by_owner(self):
        # Arrange
        tasks = [
            _card("p1", "deferred", hours_ago=30, agent="a"),
            _card("p2", "deferred", hours_ago=30, agent="b"),
        ]
        # Act
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        # Assert
        assert set(out.keys()) == {"a", "b"}

    def test_oldest_first_within_owner(self):
        # Arrange
        tasks = [
            _card("young", "deferred", hours_ago=25, agent="a"),
            _card("old", "deferred", hours_ago=100, agent="a"),
        ]
        # Act
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        # Assert
        assert [c.id for c in out["a"]] == ["old", "young"]

    def test_non_pending_statuses_excluded(self):
        # Arrange
        tasks = [
            _card("ip", "in_progress", hours_ago=99, agent="a"),
            _card("bl", "blocked", hours_ago=99, agent="a"),
            _card("dn", "done", hours_ago=99, agent="a"),
            _card("pd", "deferred", hours_ago=99, agent="a"),
        ]
        # Act
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        # Assert — only the deferred card is the pending detector's business.
        assert [c.id for c in out["a"]] == ["pd"]

    def test_created_at_fallback_counts(self):
        # Arrange
        t = _card("x", "deferred", created_hours_ago=48, agent="a")
        # Act
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        # Assert
        assert out["a"][0].id == "x"

    def test_no_timestamp_treated_as_stale(self):
        # Arrange
        t = {"id": "x", "status": "deferred", "agent": "a", "title": "x"}
        # Act
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        # Assert
        assert out["a"][0].id == "x"

    def test_no_timestamp_sorts_first(self):
        # Arrange
        tasks = [
            _card("timed", "deferred", hours_ago=48, agent="a"),
            {"id": "untimed", "status": "deferred", "agent": "a", "title": "u"},
        ]
        # Act
        out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
        # Assert
        assert out["a"][0].id == "untimed"

    def test_assignee_fallback_owner(self):
        # Arrange
        t = {
            "id": "x",
            "status": "deferred",
            "assignee": "z",
            "title": "x",
            "last_activity": _iso(NOW - _dt.timedelta(hours=48)),
        }
        # Act
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        # Assert
        assert "z" in out

    def test_unassigned_bucket_collects_ownerless_cards(self):
        # Arrange — no agent and no assignee.
        t = {
            "id": "x",
            "status": "deferred",
            "title": "x",
            "last_activity": _iso(NOW - _dt.timedelta(hours=48)),
        }
        # Act
        out = detect_pending_backlog([t], now=NOW, pending_hours=24.0)
        # Assert
        assert "(unassigned)" in out

    def test_threshold_env_override(self, monkeypatch):
        # Arrange — a 1h threshold makes a 2h-old card stale.
        monkeypatch.setenv(ENV_PENDING_NUDGE_HOURS, "1")
        t = _card("x", "deferred", hours_ago=2, agent="a")
        # Act
        out = detect_pending_backlog([t], now=NOW)
        # Assert
        assert out["a"][0].id == "x"

    def test_default_threshold_constant(self):
        # Arrange
        expected_hours = 24.0
        # Act
        actual_hours = DEFAULT_PENDING_NUDGE_HOURS
        # Assert
        assert actual_hours == expected_hours

    def test_default_threshold_more_lenient_than_stale_active(self):
        # Arrange — a 5h-old pending card is FRESH under the 24h default
        # (it would be stale under the 2h stale-active clock).
        t = _card("x", "deferred", hours_ago=5, agent="a")
        # Act
        out = detect_pending_backlog([t], now=NOW)
        # Assert
        assert "a" not in out


# --------------------------------------------------------------------------- #
# pending_backlog_nudge_line                                                  #
# --------------------------------------------------------------------------- #


class TestPendingNudgeLine:
    #: WHY the three `wording_distinct_from_stale_active` tests below are split
    #: but share this rationale: pending = "start or triage", NOT the
    #: stale-active "reconcile". Distinct wording is three claims — the line
    #: must name the BACKLOG sweep, tell the reader what to actually do, and
    #: NOT carry the stale-active label. A line that says nothing at all
    #: satisfies the negative claim while failing both positive ones, which is
    #: precisely how a nudge decays into noise.
    @pytest.fixture()
    def nudge_line(self):
        """The nudge line for one owner holding a single stale deferred card."""
        out = detect_pending_backlog(
            [_card("c1", "deferred", hours_ago=30, agent="a")],
            now=NOW,
            pending_hours=24.0,
        )
        return pending_backlog_nudge_line("a", out["a"], pending_hours=24.0)

    def test_line_contains_count_and_ids(self, nudge_line):
        # Arrange
        line = nudge_line
        # Act
        summary = line
        # Assert
        assert "1 untouched deferred card(s)" in summary and "c1" in summary

    def test_line_mentions_threshold(self, nudge_line):
        # Arrange
        line = nudge_line
        # Act
        summary = line
        # Assert
        assert ">24h" in summary

    def test_wording_distinct_from_stale_active(self, nudge_line):
        # Arrange
        line = nudge_line
        # Act
        summary = line
        # Assert — the line names the BACKLOG sweep.
        assert "BACKLOG" in summary

    def test_wording_tells_the_reader_to_start_or_triage(self, nudge_line):
        # Arrange
        line = nudge_line
        # Act
        summary = line
        # Assert — pending asks you to start or triage, not to reconcile.
        assert "start or triage" in summary

    def test_wording_never_claims_to_be_stale_active(self, nudge_line):
        # Arrange
        line = nudge_line
        # Act
        summary = line
        # Assert — the two sweeps must stay distinguishable in the channel.
        assert "STALE-ACTIVE" not in summary

    def test_id_cap_collapses_remainder(self):
        # Arrange
        cards = detect_pending_backlog(
            [
                _card(f"c{i}", "deferred", hours_ago=30, agent="a")
                for i in range(NUDGE_ID_CAP + 4)
            ],
            now=NOW,
            pending_hours=24.0,
        )["a"]
        # Act
        line = pending_backlog_nudge_line("a", cards, pending_hours=24.0)
        # Assert
        assert "+4 more" in line
