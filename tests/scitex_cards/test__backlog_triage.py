#!/usr/bin/env python3
"""Tests for the deferred-backlog triage sweep.

Pins the two properties the operator asked for by name:

* the pick-for-action draw favours RECENT cards, not old ones ("古い順に引くと
  価値が最も低い札から食わせることになる" — the backlog eats the agent);
* a kept-deferred card keeps ageing ("据え置きで last_activity を更新すると
  腐敗が隠れる").
"""

from __future__ import annotations

import datetime as _dt
import random

import pytest

from scitex_cards._backlog_triage import (
    DEFAULT_EXPIRY_DAYS,
    FIELD_DEFERRED_AT,
    FIELD_LAST_TRIAGED_AT,
    age_hours,
    build_triage_body,
    candidates,
    deferred_since,
    expired,
    is_expired,
    recency_weight,
    sample_for_triage,
)

NOW = _dt.datetime(2026, 7, 10, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - _dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _card(cid: str, *, days: float, status: str = "deferred", **kw) -> dict:
    t = {"id": cid, "title": f"card {cid}", "status": status, "agent": "a"}
    t[FIELD_DEFERRED_AT] = _iso(days)
    t.update(kw)
    return t


class TestAgeClock:
    def test_deferred_since_prefers_the_stamp(self):
        # Arrange
        t = _card("x", days=3)
        t["created_at"] = _iso(99)
        # Act
        got = deferred_since(t)
        # Assert
        assert got == NOW - _dt.timedelta(days=3)

    def test_deferred_since_falls_back_to_created_at(self):
        # Arrange — legacy card, deferred before the stamp existed.
        t = {"id": "x", "title": "t", "status": "deferred", "created_at": _iso(40)}
        # Act
        got = deferred_since(t)
        # Assert
        assert got == NOW - _dt.timedelta(days=40)

    def test_deferred_since_ignores_last_activity(self):
        # Arrange — THE anti-rot invariant. A comment or a re-defer touches
        # last_activity; reading it here would make old cards look new.
        t = {
            "id": "x",
            "title": "t",
            "status": "deferred",
            "created_at": _iso(60),
            "last_activity": _iso(0),
        }
        # Act
        age = age_hours(t, NOW)
        # Assert — still 60 days old, despite today's activity.
        assert age == pytest.approx(60 * 24, rel=1e-6)

    def test_undatable_card_has_no_age(self):
        # Arrange
        t = {"id": "x", "title": "t", "status": "deferred"}
        # Act
        age = age_hours(t, NOW)
        # Assert
        assert age is None


class TestRecencyWeighting:
    def test_newer_weighs_more_than_older(self):
        # Arrange
        half_life = 168.0
        # Act
        fresh = recency_weight(1.0, half_life)
        old = recency_weight(1000.0, half_life)
        # Assert — the direction the operator corrected.
        assert fresh > old

    def test_one_half_life_halves_the_weight(self):
        # Arrange
        half_life = 168.0
        # Act
        w = recency_weight(half_life, half_life)
        # Assert
        assert w == pytest.approx(0.5, rel=1e-9)

    def test_draw_favours_recent_cards(self):
        # Arrange — 1 fresh card, 20 nearly-expired ones. Over many draws the
        # fresh card must dominate.
        tasks = [_card("fresh", days=0.1)] + [
            _card(f"old{i}", days=25) for i in range(20)
        ]
        rng = random.Random(1234)
        # Act
        hits = 0
        for _ in range(200):
            drawn = sample_for_triage(
                tasks, n=1, now=NOW, rng=rng, half_life_hours=24.0
            )
            if drawn and drawn[0].id == "fresh":
                hits += 1
        # Assert — 1-in-21 by chance; recency weighting must beat that badly.
        assert hits > 120, f"fresh card drawn only {hits}/200 times"

    def test_sample_returns_distinct_cards(self):
        # Arrange
        tasks = [_card(f"c{i}", days=i * 0.1) for i in range(30)]
        # Act
        drawn = sample_for_triage(tasks, n=10, now=NOW, rng=random.Random(7))
        # Assert — sampling WITHOUT replacement.
        assert len({c.id for c in drawn}) == 10


class TestCandidatesAndExpiry:
    def test_only_deferred_cards_are_drawn(self):
        # Arrange
        tasks = [
            _card("d", days=1),
            _card("p", days=1, status="in_progress"),
            _card("b", days=1, status="blocked"),
            _card("x", days=1, status="done"),
        ]
        # Act
        got = {t["id"] for t in candidates(tasks, now=NOW)}
        # Assert
        assert got == {"d"}

    def test_expired_cards_are_not_offered_for_action(self):
        # Arrange — old cards are a reason to discard, not to re-triage.
        tasks = [_card("fresh", days=1), _card("rotten", days=DEFAULT_EXPIRY_DAYS + 5)]
        # Act
        drawable = {t["id"] for t in candidates(tasks, now=NOW)}
        # Assert
        assert drawable == {"fresh"}

    def test_expired_cards_are_reported_as_expired(self):
        # Arrange
        tasks = [_card("fresh", days=1), _card("rotten", days=DEFAULT_EXPIRY_DAYS + 5)]
        # Act
        rotten = {t["id"] for t in expired(tasks, now=NOW)}
        # Assert
        assert rotten == {"rotten"}

    def test_undatable_card_is_never_expired(self):
        # Arrange — refuse to propose destroying a card on a timestamp we
        # could not read.
        t = {"id": "x", "title": "t", "status": "deferred"}
        # Act
        verdict = is_expired(t, now=NOW)
        # Assert
        assert verdict is False

    def test_cooldown_suppresses_a_recently_triaged_card(self):
        # Arrange
        t = _card("c", days=1)
        t[FIELD_LAST_TRIAGED_AT] = _iso(0.5)  # 12h ago
        # Act
        got = candidates([t], now=NOW, cooldown_hours=72.0)
        # Assert
        assert got == []

    def test_cooldown_expires_and_the_card_is_drawable_again(self):
        # Arrange
        t = _card("c", days=10)
        t[FIELD_LAST_TRIAGED_AT] = _iso(5)
        # Act
        got = candidates([t], now=NOW, cooldown_hours=72.0)
        # Assert
        assert [x["id"] for x in got] == ["c"]

    def test_owner_filter_keeps_only_the_owners_cards(self):
        # Arrange
        tasks = [_card("mine", days=1), _card("theirs", days=1, agent="b")]
        # Act
        got = {t["id"] for t in candidates(tasks, owner="a", now=NOW)}
        # Assert
        assert got == {"mine"}


class TestNudgeBody:
    def test_body_states_that_keeping_does_not_reset_the_clock(self):
        # Arrange
        drawn = sample_for_triage(
            [_card("c", days=2)], n=1, now=NOW, rng=random.Random(0)
        )
        # Act
        body = build_triage_body(drawn, [])
        # Assert — the owner must know that "keep deferred" is not a free pass.
        assert "does not reset" in body

    def test_body_names_the_card_it_drew_for_triage(self):
        # Arrange
        drawn = sample_for_triage(
            [_card("c", days=2)], n=1, now=NOW, rng=random.Random(0)
        )
        # Act
        body = build_triage_body(drawn, [])
        # Assert
        assert "c" in body

    def test_body_flags_the_expired_section(self):
        # Arrange
        rotten = [_card("old", days=DEFAULT_EXPIRY_DAYS + 1)]
        # Act
        body = build_triage_body([], rotten)
        # Assert
        assert "EXPIRED" in body

    def test_body_names_cancellation_as_the_default_for_expired(self):
        # Arrange
        rotten = [_card("old", days=DEFAULT_EXPIRY_DAYS + 1)]
        # Act
        body = build_triage_body([], rotten)
        # Assert
        assert "cancellation" in body

    def test_body_names_each_expired_card(self):
        # Arrange
        rotten = [_card("old", days=DEFAULT_EXPIRY_DAYS + 1)]
        # Act
        body = build_triage_body([], rotten)
        # Assert
        assert "old" in body

    def test_empty_sweep_produces_no_noise(self):
        # Arrange
        nothing_drawn, nothing_expired = [], []
        # Act
        body = build_triage_body(nothing_drawn, nothing_expired)
        # Assert — a nudge with nothing to decide must not be sent.
        assert body == ""
