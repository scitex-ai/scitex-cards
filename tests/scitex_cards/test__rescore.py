#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``rescore_task`` — the rank engine's contract (v5-lite, ADR-0011 §1/§8).

Pinned here, per the contract on card
``scitex-cards-schema-v5-axes-rank-rescore-verb-20260717``:

1. THE ORDER — quadrant II precedes III BY CONSTRUCTION (not by weight
   luck); score orders within a band; ties break by first-scored time
   (waiting never costs position) then id; terminal cards hold axes but
   never a rank; the ranks are a contiguous 1..N total order.
2. THE VERB — axes land, ``scored_at`` stamps ONCE (a re-drag never
   resets seniority), the audit comment carries the machine-readable
   old→new payload occupancy replay needs, and ONLY the rescored card's
   ``last_activity`` moves — neighbours re-rank silently (the
   priority.py inactivity-clock lesson, enforced by test).
3. THE EVENT — one ``rank_changed`` per rescore, captured via the
   documented in-process ``entry_points=`` seam (real fake handler, no
   mocks), carrying the same transition payload.
4. THE SEAM — ``rescore_task`` is a BACKEND_VERBS member callable on
   BOTH backends (the HubBackend-completeness walk added here is the
   gap the seam test's Local-only walk left open).
5. THE THRESHOLD PARITY — the engine's HIGH_THRESHOLD equals the matrix
   view's QUADRANT_THRESHOLD, read from the SHIPPED JS, so the drawn
   quadrants and the computed order can never disagree silently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from scitex_cards import _store
from scitex_cards._store_rescore import (
    HIGH_THRESHOLD,
    UNRANKED_STATUSES,
    recompute_ranks,
    rescore_task,
)

# === In-process injection seam (real fake handler, no mocks) ===============


class _Capturing:
    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _eps(sink: _Capturing) -> list[_FakeEP]:
    return [_FakeEP("captor", sink)]


@pytest.fixture()
def store(env):
    # Store is SQLite now; the harness bootstraps an empty canonical DB per test
    # and pins SCITEX_CARDS_TASKS_YAML_SHARED as the STORE IDENTITY. Return that
    # pinned store path (NOT a tmp_path file — a write stamped with any other
    # path fails the next read's ownership guard; see THE STORE-PATH RULE).
    env.set("SCITEX_TODO_AGENT_ID", "rank-tester")
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _add(store, card_id, status="deferred"):
    _store.add_task(
        store,
        id=card_id,
        title=f"Card {card_id}",
        status=status,
        assignee="alice",
        created_by="rank-tester",
    )


# === 1. the order (pure engine, no I/O) ====================================


def _card(cid, u, i, status="deferred", scored_at=None):
    out = {"id": cid, "status": status, "urgency": u, "importance": i}
    if scored_at:
        out["scored_at"] = scored_at
    return out


def test_quadrant_ii_precedes_iii_by_construction():
    """Worst-II (u=1,i=3, score 7) must outrank best-III (u=5,i=2, score 9):
    the guarantee lives in the order key, not the scalar weights."""
    # Arrange
    tasks = [_card("iii-best", 5, 2), _card("ii-worst", 1, 3)]
    # Act
    recompute_ranks(tasks)
    # Assert
    ranks = {t["id"]: t["rank"] for t in tasks}
    assert ranks["ii-worst"] < ranks["iii-best"]


def test_score_orders_within_a_band():
    # Arrange
    tasks = [_card("low", 1, 3), _card("high", 5, 5), _card("mid", 3, 4)]
    # Act
    recompute_ranks(tasks)
    # Assert
    ranks = {t["id"]: t["rank"] for t in tasks}
    assert ranks["high"] < ranks["mid"] < ranks["low"]


def test_aging_tie_break_older_scored_first():
    """Equal axes: the card scored EARLIER wins — waiting never costs."""
    # Arrange
    tasks = [
        _card("young", 3, 3, scored_at="2026-07-17T10:00:00Z"),
        _card("old", 3, 3, scored_at="2026-07-01T10:00:00Z"),
    ]
    # Act
    recompute_ranks(tasks)
    # Assert
    ranks = {t["id"]: t["rank"] for t in tasks}
    assert ranks["old"] < ranks["young"]


#: One live card and one terminal card, ranked together. The four tests below
#: split what a single test used to claim about the pair — that terminal cards
#: are excluded from the COUNT, that the live card therefore takes the only
#: rank, that the terminal card holds no rank, and that it nevertheless keeps
#: its axes. Finished work holds its scores; it just no longer competes.
def _live_and_terminal():
    tasks = [_card("live", 3, 3), _card("done", 5, 5, status="done")]
    ranked = recompute_ranks(tasks)
    return tasks, ranked


def test_terminal_cards_are_left_out_of_the_rank_count():
    # Arrange
    _tasks, ranked = _live_and_terminal()
    # Act
    total = ranked
    # Assert — one live card, so one rank issued.
    assert total == 1


def test_the_only_live_card_takes_the_first_rank():
    # Arrange
    tasks, _ranked = _live_and_terminal()
    # Act
    live = tasks[0]
    # Assert
    assert live["rank"] == 1


def test_terminal_cards_hold_no_rank():
    # Arrange
    tasks, _ranked = _live_and_terminal()
    # Act
    terminal = tasks[1]
    # Assert
    assert "rank" not in terminal


def test_terminal_cards_keep_their_axes():
    # Arrange
    tasks, _ranked = _live_and_terminal()
    # Act
    terminal = tasks[1]
    # Assert — losing the rank must not erase the scoring that earned it.
    assert terminal["urgency"] == 5


def test_ranks_are_a_contiguous_total_order():
    # Arrange
    tasks = [_card(f"c{i}", (i % 5) + 1, ((i * 2) % 5) + 1) for i in range(9)]
    # Act
    n = recompute_ranks(tasks)
    # Assert — 1..N with no gaps and no duplicates.
    assert sorted(t["rank"] for t in tasks) == list(range(1, n + 1))


def test_unscored_cards_are_never_ranked():
    # Arrange
    tasks = [_card("scored", 3, 3), {"id": "bare", "status": "deferred"}]
    # Act
    recompute_ranks(tasks)
    # Assert — no axes means no position, not a defaulted one.
    assert "rank" not in tasks[1]


def test_stale_rank_on_a_now_terminal_card_is_stripped():
    # Arrange
    tasks = [_card("was-ranked", 3, 3, status="done")]
    tasks[0]["rank"] = 7
    # Act
    recompute_ranks(tasks)
    # Assert
    assert "rank" not in tasks[0]


# === 2. the verb ===========================================================


def test_rescore_returns_the_rank_and_the_total(store):
    # Arrange
    _add(store, "r-a")
    # Act
    result = rescore_task(store, "r-a", urgency=4, importance=5)
    # Assert — the drag's answer: where this card now sits, out of how many.
    assert (result["rank"], result["of"]) == (1, 1)


def test_rescore_persists_the_axes_and_rank_to_disk(store):
    # Arrange
    _add(store, "r-a")
    # Act
    rescore_task(store, "r-a", urgency=4, importance=5)
    # Assert — the returned triple is not the only place it lands.
    on_disk = _store.get_task(store, "r-a")
    assert (on_disk["urgency"], on_disk["importance"], on_disk["rank"]) == (4, 5, 1)


def test_scored_at_stamps_once_never_resets(store):
    # Arrange
    _add(store, "r-age")
    rescore_task(store, "r-age", urgency=2, importance=2)
    first = _store.get_task(store, "r-age")["scored_at"]
    # Act
    rescore_task(store, "r-age", urgency=5, importance=5)
    # Assert — a re-drag must never reset seniority.
    assert _store.get_task(store, "r-age")["scored_at"] == first


#: The audit comment occupancy replay reads back. One rescore of an unscored
#: card by "dragger" produces one entry; the five tests below split what a
#: single test asserted about it — its kind and author, then each field of the
#: machine-readable old→new payload. A replay that silently loses ONE axis is
#: exactly the failure a compound assertion would have reported as "the audit
#: comment is wrong" without saying which part.
def _rescore_audit_entry(store):
    _add(store, "r-audit")
    rescore_task(store, "r-audit", urgency=2, importance=4, by="dragger")
    return _store.get_task(store, "r-audit")["comments"][-1]


def test_audit_comment_is_kinded_rescore_and_attributed(store):
    # Arrange
    entry = _rescore_audit_entry(store)
    # Act
    kind, author = entry["kind"], entry["author"]
    # Assert
    assert (kind, author) == ("rescore", "dragger")


def test_audit_comment_carries_the_urgency_transition(store):
    # Arrange
    entry = _rescore_audit_entry(store)
    # Act
    transition = entry["rescore"]["urgency"]
    # Assert — unscored → 2, as an explicit old→new pair.
    assert transition == [None, 2]


def test_audit_comment_carries_the_importance_transition(store):
    # Arrange
    entry = _rescore_audit_entry(store)
    # Act
    transition = entry["rescore"]["importance"]
    # Assert
    assert transition == [None, 4]


def test_audit_comment_carries_the_rank_transition(store):
    # Arrange
    entry = _rescore_audit_entry(store)
    # Act
    transition = entry["rescore"]["rank"]
    # Assert
    assert transition == [None, 1]


def test_audit_comment_carries_the_board_size(store):
    # Arrange
    entry = _rescore_audit_entry(store)
    # Act
    of = entry["rescore"]["of"]
    # Assert — rank 1 means nothing without the N it is out of.
    assert of == 1


#: Two cards, the second scored high enough to displace the first. The pair of
#: tests below split the one claim that used to be doubled up: the neighbour DID
#: move (so the re-rank really happened) and its ``last_activity`` did NOT (the
#: binding priority.py lesson — a re-rank must not reset every inactivity-nudge
#: clock on the board). Asserting only the first would pass a broken engine that
#: restamps everything; only the second would pass one that never re-ranks.
def _displaced_neighbour(store):
    _add(store, "r-n1")
    rescore_task(store, "r-n1", urgency=1, importance=1)
    before = _store.get_task(store, "r-n1")["last_activity"]
    _add(store, "r-n2")
    rescore_task(store, "r-n2", urgency=5, importance=5)  # displaces r-n1 to rank 2
    return _store.get_task(store, "r-n1"), before


def test_a_displaced_neighbour_moves_down_a_rank(store):
    # Arrange
    neighbour, _before = _displaced_neighbour(store)
    # Act
    rank = neighbour["rank"]
    # Assert
    assert rank == 2


def test_neighbours_rerank_silently_without_activity_restamp(store):
    # Arrange
    neighbour, before = _displaced_neighbour(store)
    # Act
    after = neighbour["last_activity"]
    # Assert — the neighbour was re-ranked, not touched.
    assert after == before


def test_rescoring_a_terminal_card_returns_null_rank(store):
    # Arrange
    _add(store, "r-done", status="done")
    # Act
    result = rescore_task(store, "r-done", urgency=3, importance=3)
    # Assert — finished work holds axes but never a position.
    assert result["rank"] is None


def test_rescoring_a_terminal_card_writes_no_rank(store):
    # Arrange
    _add(store, "r-done", status="done")
    # Act
    rescore_task(store, "r-done", urgency=3, importance=3)
    # Assert
    assert "rank" not in _store.get_task(store, "r-done")


def test_axis_validation_rejects_urgency_below_the_range(store):
    # Arrange
    _add(store, "r-bad")
    # Act
    refusal = pytest.raises(ValueError)
    # Assert — the axes are 1..5; 0 is not a quiet clamp.
    with refusal:
        rescore_task(store, "r-bad", urgency=0, importance=3)


def test_axis_validation_rejects_importance_above_the_range(store):
    # Arrange
    _add(store, "r-bad")
    # Act
    refusal = pytest.raises(ValueError)
    # Assert
    with refusal:
        rescore_task(store, "r-bad", urgency=3, importance=6)


def test_axis_validation_rejects_a_bool_as_an_axis(store):
    # Arrange
    _add(store, "r-bad")
    # Act
    refusal = pytest.raises(ValueError)
    # Assert — bool is an int subclass, so True would otherwise pass as 1.
    with refusal:
        rescore_task(store, "r-bad", urgency=True, importance=3)


def test_unknown_id_raises_tasknotfound(store):
    # Arrange
    ghost_id = "ghost"
    # Act
    refusal = pytest.raises(_store.TaskNotFoundError)
    # Assert
    with refusal:
        rescore_task(store, ghost_id, urgency=3, importance=3)


# === 3. the event ==========================================================


#: One rescore of "r-ev1" on a two-card board, captured through the documented
#: in-process seam. Returns ``(sink, rank_changed_events)``. The five tests
#: below split what one test asserted about it: that EXACTLY one event fired,
#: that it names the card and the actor, that it carries the axis transition
#: and the board size, and that the untouched neighbour emitted nothing. The
#: last is the contract's sharp edge — neighbours re-rank SILENTLY, so a
#: fan-out bug shows up as extra events, not as a wrong one.
def _rescore_events(store):
    _add(store, "r-ev1")
    _add(store, "r-ev2")
    sink = _Capturing()
    rescore_task(
        store, "r-ev1", urgency=2, importance=5, by="dragger", entry_points=_eps(sink)
    )
    events = [
        e
        for e in sink.events
        if e.get("kind") == "card-event" and e.get("type") == "rank_changed"
    ]
    return sink, events


def test_one_rank_changed_event_per_rescore(store):
    # Arrange
    _sink, events = _rescore_events(store)
    # Act
    fired = len(events)
    # Assert
    assert fired == 1


def test_rank_changed_event_names_the_card_and_actor(store):
    # Arrange
    _sink, events = _rescore_events(store)
    # Act
    event = events[0]
    # Assert
    assert (event["card_id"], event["actor"]) == ("r-ev1", "dragger")


def test_rank_changed_event_carries_the_importance_transition(store):
    # Arrange
    _sink, events = _rescore_events(store)
    # Act
    transition = events[0]["importance"]
    # Assert — the same old→new payload the audit comment records.
    assert transition == [None, 5]


def test_rank_changed_event_carries_the_board_size(store):
    # Arrange
    _sink, events = _rescore_events(store)
    # Act
    of = events[0]["of"]
    # Assert
    assert of == 1


def test_an_untouched_neighbour_emits_no_event(store):
    # Arrange
    sink, _events = _rescore_events(store)
    # Act
    neighbour_events = [e for e in sink.events if e.get("card_id") == "r-ev2"]
    # Assert — one event per rescore, for the rescored card only.
    assert neighbour_events == []


# === 4. the seam ===========================================================


def test_rescore_is_a_backend_verb():
    # Arrange
    from scitex_cards._backend import BACKEND_VERBS

    # Act
    verbs = BACKEND_VERBS
    # Assert
    assert "rescore_task" in verbs


def test_every_backend_verb_is_callable_on_both_backends():
    """Completeness on BOTH implementations — the Local-only walk in the seam
    tests left the Hub side uncovered; this closes it for every verb, not just
    rescore."""
    # Arrange
    from scitex_cards._backend import BACKEND_VERBS, LocalBackend
    from scitex_cards._backend_http import HubBackend

    hub = HubBackend("http://127.0.0.1:1")
    local = LocalBackend()
    # Act
    missing = [
        v
        for v in BACKEND_VERBS
        if not callable(getattr(local, v, None)) or not callable(getattr(hub, v, None))
    ]
    # Assert
    assert missing == []


# === 5. threshold parity with the shipped matrix view ======================


#: The SHIPPED matrix module's source. Read (not imported) on purpose: the
#: quadrants the operator DRAWS are whatever this file says, so parity has to be
#: checked against the artefact that ships, not against a Python mirror of it.
_MATRIX_JS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "14-matrix.js"
)


def _matrix_threshold_match():
    js = _MATRIX_JS.read_text(encoding="utf-8")
    return re.search(r"QUADRANT_THRESHOLD\s*=\s*(\d+)", js)


def test_the_shipped_matrix_module_declares_a_quadrant_threshold():
    # Arrange
    expected_symbol = "QUADRANT_THRESHOLD"
    # Act
    match = _matrix_threshold_match()
    # Assert — without it there is nothing to compare, so parity is unprovable.
    assert match, f"{expected_symbol} not found in the shipped matrix module"


def test_engine_threshold_equals_the_matrix_views():
    # Arrange
    match = _matrix_threshold_match()
    # Act
    drawn_threshold = int(match.group(1))
    # Assert — drawn quadrants and computed order can never disagree silently.
    assert drawn_threshold == HIGH_THRESHOLD


def test_unranked_statuses_are_the_terminal_set():
    # Arrange
    expected = {"done", "cancelled", "failed"}
    # Act
    actual = UNRANKED_STATUSES
    # Assert
    assert actual == expected


# EOF
