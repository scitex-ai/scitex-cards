#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag engine — per-owner digest, flat configurable cadence, per-card override.

Real fakes, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store for the
sidecar, a plain list-recorder ``enqueue``, a real ``resolve_key`` dict, and
an injected ``now`` so the cadence/escalation state machine is driven
deterministically without a clock or network. AAA pattern.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards._reminders import (
    DIGEST_CARD_ID,
    EVENT_CREATOR_ESCALATION,
    EVENT_DIGEST,
    EVENT_ESCALATION,
    load_reminder_state,
    sweep_reminders,
)

# === hermetic env + config =================================================


@pytest.fixture(autouse=True)
def _isolate_engine(monkeypatch):
    """Strip env knobs AND detach config resolution so a deployed container's
    settings (``SCITEX_TODO_REMINDER_OWNERS`` from the spec, a real
    ``~/.scitex/todo/config.yaml``) can never leak into these unit tests.
    Each test sets only what it needs via args."""
    for var in (
        "SCITEX_TODO_REMINDER_OWNERS",
        "SCITEX_TODO_REMINDER_ESCALATE_AFTER",
        "SCITEX_TODO_REMINDER_ESCALATE_PRIORITY",
        "SCITEX_TODO_OPERATOR",
        "SCITEX_TODO_STALE_ACTIVE_HOURS",
        "SCITEX_TODO_PENDING_NUDGE_HOURS",
    ):
        monkeypatch.delenv(var, raising=False)
    # No config files contribute anything unless a test opts in.
    monkeypatch.setattr("scitex_cards._config.config_paths", lambda: [])


# === helpers ===============================================================


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
            "actor": actor,
            "ts": ts,
            "supersede": supersede,
        }
        self.calls.append(rec)
        return rec


def _resolver(mapping):
    return lambda name: mapping.get(name, name)


def _t(
    *,
    id,
    owner,
    status="in_progress",
    hours_ago=10.0,
    priority=None,
    interval_minutes=None,
    now=None,
):
    now = now or _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)
    last = (now - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    card = {
        "id": id,
        "title": f"card {id}",
        "status": status,
        "agent": owner,
        "last_activity": last,
    }
    if priority is not None:
        card["priority"] = priority
    if interval_minutes is not None:
        card["reminder_interval_minutes"] = interval_minutes
    return card


_NOW = _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _digests(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_DIGEST]


def _escalations(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_ESCALATION]


def _creator_escs(rec):
    return [c for c in rec.calls if c["event_type"] == EVENT_CREATOR_ESCALATION]


def _user_resolver(mapping):
    """Fake ``resolve_user``: owner name → a user DICT (or None = not a user).

    A user dict carries a ``last_seen`` stamp so ``is_alive`` can classify it;
    ``None`` models a free-form (non-registered) owner. Mirrors the real
    ``resolve_user`` seam without touching the registry (real fake, no mock).
    """
    return lambda name: mapping.get(name)


def _seen(now, *, seconds_ago):
    ts = now - _dt.timedelta(seconds=seconds_ago)
    return {"last_seen": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}


# === sweep_reminders — one digest per owner ================================


#: One sweep, one owner, one stale card. Eight tests split what a single test
#: asserted about the resulting enqueue — the sweep's own report, that exactly
#: ONE note went out, and each field of it. Every field is load-bearing:
#: ``recipient`` must be the PRODUCER-matching key (a wrong one delivers to
#: nobody at all), ``card_id`` is what lets a later digest FIND its predecessor,
#: and ``supersede`` is the replay-storm fix. A compound assertion over all
#: eight would only ever report "the digest is wrong".
def _one_owner_sweep(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({"alice": "u_alice"}),
    )
    return out, rec


#: Three stale cards, one owner. The whole point of the refactor: N stale cards
#: → ONE digest, not N nags.
def _three_card_sweep(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="alice", hours_ago=20.0),
        _t(id="c3", owner="alice", status="deferred", hours_ago=99.0),
    ]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({}),
    )
    return out, rec


def test_sweep_reports_the_digested_owner(tmp_path):
    # Arrange
    out, _rec = _one_owner_sweep(tmp_path)
    # Act
    digested = out["digested"]
    # Assert
    assert digested == ["alice"]


def test_sweep_enqueues_one_digest_for_owner(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert
    assert sent == 1


def test_the_digest_goes_to_the_producer_matching_key(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    recipient = rec.calls[0]["recipient"]
    # Assert — the resolved key, not the raw owner name.
    assert recipient == "u_alice"


def test_the_digest_carries_the_digest_event_type(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    event_type = rec.calls[0]["event_type"]
    # Assert
    assert event_type == EVENT_DIGEST


def test_the_digest_uses_the_canonical_digest_card_id(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    card_id = rec.calls[0]["card_id"]
    # Assert — a stable id is what superseding matches on.
    assert card_id == DIGEST_CARD_ID


def test_the_digest_is_attributed_to_notifyd(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    actor = rec.calls[0]["actor"]
    # Assert
    assert actor == "notifyd"


def test_the_digest_body_names_the_stale_card(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    body = rec.calls[0]["body"]
    # Assert
    assert "c1" in body


def test_the_digest_supersedes_an_unseen_predecessor(tmp_path):
    # Arrange
    _out, rec = _one_owner_sweep(tmp_path)
    # Act
    supersede = rec.calls[0]["supersede"]
    # Assert — the cumulative digest replaces, never stacks (replay-storm fix).
    assert supersede is True


def test_a_multi_card_sweep_digests_the_owner_once(tmp_path):
    # Arrange
    out, _rec = _three_card_sweep(tmp_path)
    # Act
    digested = out["digested"]
    # Assert
    assert digested == ["alice"]


def test_digest_collapses_many_cards_into_one_note(tmp_path):
    # Arrange
    _out, rec = _three_card_sweep(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert — ONE note, not three.
    assert sent == 1


def test_the_collapsed_digest_names_every_stale_card(tmp_path):
    # Arrange
    _out, rec = _three_card_sweep(tmp_path)
    # Act
    body = rec.calls[0]["body"]
    # Assert — collapsing must not mean dropping.
    assert "c1" in body and "c2" in body and "c3" in body


# === flat cadence (default 5 min) ==========================================


#: Two sweeps four minutes apart — under the 5 min gap, so the second is NOT
#: due. Three tests split the claim: the owner is absent from `digested`, they
#: ARE present in `skipped` (surfaced, not silently dropped), and no second note
#: went out. The middle one is what stops "not due" from becoming "forgotten".
def _swept_before_the_interval(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)
    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=4), **kw)
    return out2, rec


#: The interval GATES a re-nag; changed content is what TRIGGERS one. (Before
#: 2026-07-11 the elapsed interval alone re-sent an identical digest — see the
#: suppression tests below.) A second stale card appears, so the digest's
#: content genuinely changed.
def _swept_after_the_interval_with_new_content(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)
    sweep_reminders(tasks, now=_NOW, **kw)
    tasks2 = tasks + [_t(id="c2", owner="alice", hours_ago=10.0)]
    out2 = sweep_reminders(tasks2, now=_NOW + _dt.timedelta(minutes=6), **kw)
    return out2, rec


#: An identical digest must NOT re-wake the owner every interval. Observed live
#: 2026-07-11: 26 identical digests in ~2h — same card list, only the counter
#: moving. That trains an agent to ignore the digest, which is the one signal
#: that must stay un-ignorable. Many intervals pass and nothing changes; the
#: per-iteration assertions become assertions over the collected reports.
def _swept_many_times_unchanged(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)
    sweep_reminders(tasks, now=_NOW, **kw)
    outs = [
        sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=minutes), **kw)
        for minutes in (6, 12, 60, 300)
    ]
    return outs, rec


def _swept_past_the_floor(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)
    sweep_reminders(tasks, now=_NOW, **kw)
    out = sweep_reminders(tasks, now=_NOW + _dt.timedelta(hours=25), **kw)
    return out, rec


#: Same card ids, different state — the owner must hear about it.
def _swept_with_a_status_change(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), interval_minutes=5.0)
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0, status="in_progress")]
    sweep_reminders(tasks, now=_NOW, **kw)
    tasks2 = [_t(id="c1", owner="alice", hours_ago=10.0, status="blocked")]
    out = sweep_reminders(tasks2, now=_NOW + _dt.timedelta(minutes=6), **kw)
    return out, rec


#: With no config and no arg the cadence falls back to the 5 min default. The
#: content GROWS on each later sweep, so only the cadence gate is under test
#: here — an unchanged digest is suppressed on purpose (see above).
def _swept_either_side_of_the_default_interval(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}))
    sweep_reminders(tasks, now=_NOW, **kw)
    grown = tasks + [_t(id="c2", owner="alice", hours_ago=10.0)]
    early = sweep_reminders(grown, now=_NOW + _dt.timedelta(minutes=4), **kw)
    late = sweep_reminders(grown, now=_NOW + _dt.timedelta(minutes=6), **kw)
    return early, late


#: One card asks for a 1-min nudge where the default would be 5 min. The second
#: sweep is 90 s later: under the default, over the card's override → due.
def _swept_with_a_card_level_override(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0, interval_minutes=1)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}))
    sweep_reminders(tasks, now=_NOW, **kw)
    grown = tasks + [_t(id="c2", owner="alice", hours_ago=10.0)]
    out2 = sweep_reminders(grown, now=_NOW + _dt.timedelta(seconds=90), **kw)
    return out2, rec


def test_sweep_does_not_renag_before_interval(tmp_path):
    # Arrange
    out2, _rec = _swept_before_the_interval(tmp_path)
    # Act
    digested = out2["digested"]
    # Assert — 4 min is under the 5 min gap, so nothing is due.
    assert digested == []


def test_an_undue_owner_is_reported_as_skipped(tmp_path):
    # Arrange
    out2, _rec = _swept_before_the_interval(tmp_path)
    # Act
    skipped = out2["skipped"]
    # Assert — not due is surfaced, never dropped out of the report.
    assert skipped == ["alice"]


def test_no_second_note_goes_out_before_the_interval(tmp_path):
    # Arrange
    _out2, rec = _swept_before_the_interval(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert
    assert sent == 1


def test_sweep_renags_after_interval_when_content_changed(tmp_path):
    # Arrange
    out2, _rec = _swept_after_the_interval_with_new_content(tmp_path)
    # Act
    digested = out2["digested"]
    # Assert — the gap elapsed AND the card set grew.
    assert digested == ["alice"]


def test_the_renag_sends_a_second_note(tmp_path):
    # Arrange
    _out2, rec = _swept_after_the_interval_with_new_content(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert
    assert sent == 2


def test_unchanged_digest_is_suppressed_until_the_floor(tmp_path):
    # Arrange
    outs, _rec = _swept_many_times_unchanged(tmp_path)
    # Act
    digested = [out["digested"] for out in outs]
    # Assert — four intervals pass, nothing changes, nobody is re-woken.
    assert digested == [[], [], [], []]


def test_a_suppressed_owner_is_reported_as_skipped(tmp_path):
    # Arrange
    outs, _rec = _swept_many_times_unchanged(tmp_path)
    # Act
    skipped = [out["skipped"] for out in outs]
    # Assert — suppressed is not the same as forgotten.
    assert skipped == [["alice"], ["alice"], ["alice"], ["alice"]]


def test_a_suppressed_owner_gets_no_further_notes(tmp_path):
    # Arrange
    _outs, rec = _swept_many_times_unchanged(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert — one note across all five sweeps, not 26 in two hours.
    assert sent == 1


def test_unchanged_digest_still_fires_once_past_the_floor(tmp_path):
    # Arrange
    out, _rec = _swept_past_the_floor(tmp_path)
    # Act
    digested = out["digested"]
    # Assert — suppression is not silence; a stuck owner is still nudged daily.
    assert digested == ["alice"]


def test_the_past_floor_digest_sends_a_second_note(tmp_path):
    # Arrange
    _out, rec = _swept_past_the_floor(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert
    assert sent == 2


def test_status_change_alone_retriggers_the_digest(tmp_path):
    # Arrange
    out, _rec = _swept_with_a_status_change(tmp_path)
    # Act
    digested = out["digested"]
    # Assert — same ids, different state, so the owner hears about it.
    assert digested == ["alice"]


def test_the_status_change_digest_sends_a_second_note(tmp_path):
    # Arrange
    _out, rec = _swept_with_a_status_change(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert
    assert sent == 2


def test_the_default_interval_holds_fire_at_four_minutes(tmp_path):
    # Arrange
    early, _late = _swept_either_side_of_the_default_interval(tmp_path)
    # Act
    digested = early["digested"]
    # Assert — 4 min < the 5 min default.
    assert digested == []


def test_default_interval_is_five_minutes(tmp_path):
    # Arrange
    _early, late = _swept_either_side_of_the_default_interval(tmp_path)
    # Act
    digested = late["digested"]
    # Assert — 6 min ≥ the 5 min default.
    assert digested == ["alice"]


def test_card_level_override_tightens_owner_cadence(tmp_path):
    # Arrange
    out2, _rec = _swept_with_a_card_level_override(tmp_path)
    # Act
    digested = out2["digested"]
    # Assert — 90 s clears the card's 1 min override, not the 5 min default.
    assert digested == ["alice"]


def test_the_card_level_override_sends_a_second_note(tmp_path):
    # Arrange
    _out2, rec = _swept_with_a_card_level_override(tmp_path)
    # Act
    sent = len(rec.calls)
    # Assert
    assert sent == 2


def test_config_interval_knob_is_honored(tmp_path, monkeypatch):
    """reminders.interval_minutes in config.yaml sets the cadence."""
    # Arrange
    cfg = tmp_path / "config.yaml"
    cfg.write_text("reminders:\n  interval_minutes: 2\n", encoding="utf-8")
    monkeypatch.setattr("scitex_cards._config.config_paths", lambda: [cfg])
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}))
    sweep_reminders(tasks, now=_NOW, **kw)
    grown = tasks + [_t(id="c2", owner="alice", hours_ago=10.0)]
    # Act
    out2 = sweep_reminders(grown, now=_NOW + _dt.timedelta(minutes=3), **kw)
    # Assert — 3 min clears the configured 2 min, not the 5 min default.
    assert out2["digested"] == ["alice"]


# === escalation — high-priority overdue → operator (per card) ==============


#: Two sweeps on one high-priority stale card with `escalate_after=2`. The
#: first takes the digest count to 1 (below threshold, no escalation); the
#: second reaches the threshold and escalates. Four tests split what one
#: asserted: the sweep's report, that EXACTLY one escalation went out, that it
#: is addressed to the OPERATOR (not the owner, who is evidently not acting),
#: and that it names the specific stuck card rather than the digest.
def _escalated_after_threshold(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}),
        operator="operator",
        escalate_after=2,
        escalate_priority=1,
        interval_minutes=5.0,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)
    return out2, rec


def test_high_priority_escalates_to_operator_after_threshold(tmp_path):
    # Arrange
    out2, _rec = _escalated_after_threshold(tmp_path)
    # Act
    escalated = out2["escalated"]
    # Assert
    assert escalated == ["c1"]


def test_the_escalation_fires_exactly_once(tmp_path):
    # Arrange
    _out2, rec = _escalated_after_threshold(tmp_path)
    # Act
    fired = len(_escalations(rec))
    # Assert
    assert fired == 1


def test_the_escalation_is_addressed_to_the_operator(tmp_path):
    # Arrange
    _out2, rec = _escalated_after_threshold(tmp_path)
    # Act
    recipient = _escalations(rec)[0]["recipient"]
    # Assert — the owner is evidently not acting, so it goes over their head.
    assert recipient == "u_op"


def test_the_escalation_names_the_specific_stuck_card(tmp_path):
    # Arrange
    _out2, rec = _escalated_after_threshold(tmp_path)
    # Act
    card_id = _escalations(rec)[0]["card_id"]
    # Assert — per-card, not the cumulative digest id.
    assert card_id == "c1"


def test_escalation_fires_once_per_streak(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({}),
        escalate_after=1,
        escalate_priority=1,
        interval_minutes=5.0,
    )
    sweep_reminders(tasks, now=_NOW, **kw)  # count1 >= 1 → escalate
    # Act
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)
    # Assert — latched: a second sweep in the same streak stays quiet.
    assert len(_escalations(rec)) == 1


def test_low_priority_card_never_escalates(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0, priority=5)]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({}),
        escalate_after=1,
        escalate_priority=1,
        interval_minutes=5.0,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    # Act
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)
    # Assert — however stale, low priority never reaches the operator.
    assert _escalations(rec) == []


def test_card_without_priority_never_escalates(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=99.0)]  # no priority
    kw = dict(store=store, enqueue=rec, resolve_key=_resolver({}), escalate_after=1)
    # Act
    sweep_reminders(tasks, now=_NOW, **kw)
    # Assert — an absent priority is not treated as a high one.
    assert _escalations(rec) == []


#: An owner's digest covers ALL their cards, but ONLY the high-priority one
#: escalates. Three tests: the owner was digested (so both cards were seen), the
#: report escalates only "hot", and only one escalation note went out. Checking
#: the report alone would miss an engine that reported one and sent two.
def _mixed_priority_sweep(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="hot", owner="alice", hours_ago=50.0, priority=0),
        _t(id="cold", owner="alice", hours_ago=50.0, priority=5),
    ]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({}),
        escalate_after=1,
        escalate_priority=1,
        interval_minutes=5.0,
    )
    out = sweep_reminders(tasks, now=_NOW, **kw)
    return out, rec


def test_a_mixed_priority_owner_is_still_digested(tmp_path):
    # Arrange
    out, _rec = _mixed_priority_sweep(tmp_path)
    # Act
    digested = out["digested"]
    # Assert — the digest covers all of alice's cards.
    assert digested == ["alice"]


def test_only_high_priority_card_in_a_mixed_digest_escalates(tmp_path):
    # Arrange
    out, _rec = _mixed_priority_sweep(tmp_path)
    # Act
    escalated = out["escalated"]
    # Assert
    assert escalated == ["hot"]


def test_the_mixed_digest_sends_one_escalation_note(tmp_path):
    # Arrange
    _out, rec = _mixed_priority_sweep(tmp_path)
    # Act
    escalated_ids = [c["card_id"] for c in _escalations(rec)]
    # Assert — the report and the wire agree; "cold" reached nobody.
    assert escalated_ids == ["hot"]


# === nag STOPS when work leaves the stale set (closed / touched) ===========


#: The card goes done → the owner has no stale cards → the cadence entry is
#: pruned and the nag stops. Returns ``(armed_after_first, out_after_done)``.
#: Three tests: the state WAS armed (else the pruning proves nothing), the
#: second sweep digests nobody, and the entry is gone from the sidecar.
def _swept_then_card_done(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    stale = [_t(id="c1", owner="alice", hours_ago=10.0)]
    sweep_reminders(
        stale, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    armed = "alice" in load_reminder_state(store)["owners"]

    done = [_t(id="c1", owner="alice", status="done", hours_ago=10.0)]
    out = sweep_reminders(
        done,
        store=store,
        now=_NOW + _dt.timedelta(hours=10),
        enqueue=rec,
        resolve_key=_resolver({}),
    )
    return armed, out, store


def test_the_first_sweep_arms_the_owner_cadence(tmp_path):
    # Arrange
    armed, _out, _store = _swept_then_card_done(tmp_path)
    # Act
    was_armed = armed
    # Assert — without this, the pruning below could be a no-op.
    assert was_armed is True


def test_a_done_card_leaves_the_owner_undigested(tmp_path):
    # Arrange
    _armed, out, _store = _swept_then_card_done(tmp_path)
    # Act
    digested = out["digested"]
    # Assert
    assert digested == []


def test_state_pruned_when_owner_has_no_stale_cards(tmp_path):
    # Arrange
    _armed, _out, store = _swept_then_card_done(tmp_path)
    # Act
    owners = load_reminder_state(store)["owners"]
    # Assert — the cadence entry is pruned, so the nag genuinely stops.
    assert "alice" not in owners


def _swept_then_hot_card_done(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    hot = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({}),
        escalate_after=1,
        escalate_priority=1,
    )
    sweep_reminders(hot, now=_NOW, **kw)
    latched = "c1" in load_reminder_state(store)["cards"]

    done = [_t(id="c1", owner="alice", status="done", hours_ago=50.0, priority=0)]
    sweep_reminders(done, now=_NOW + _dt.timedelta(hours=30), **kw)
    return latched, store


def test_the_first_sweep_latches_the_escalation(tmp_path):
    # Arrange
    latched, _store = _swept_then_hot_card_done(tmp_path)
    # Act
    was_latched = latched
    # Assert
    assert was_latched is True


def test_escalation_latch_pruned_when_card_no_longer_stale(tmp_path):
    # Arrange
    _latched, store = _swept_then_hot_card_done(tmp_path)
    # Act
    cards = load_reminder_state(store)["cards"]
    # Assert — a future stall must be able to re-escalate.
    assert "c1" not in cards


#: alice's stale cards: one in_progress (actionable) and one blocked-with-a-
#: blocker (parked, waiting on a dep). The digest must list ONLY the actionable
#: one — and must still be sent, which is why both halves are asserted.
def _swept_with_one_parked_card(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    actionable = _t(id="go", owner="alice", status="in_progress", hours_ago=10.0)
    parked = _t(id="wait", owner="alice", status="blocked", hours_ago=10.0)
    parked["blocker"] = "dependency"
    out = sweep_reminders(
        [actionable, parked],
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({}),
    )
    return out, rec


def test_an_owner_with_one_actionable_card_is_still_digested(tmp_path):
    # Arrange
    out, _rec = _swept_with_one_parked_card(tmp_path)
    # Act
    digested = out["digested"]
    # Assert
    assert digested == ["alice"]


def test_parked_blocked_card_is_excluded_from_digest(tmp_path):
    # Arrange
    _out, rec = _swept_with_one_parked_card(tmp_path)
    # Act
    body = rec.calls[0]["body"]
    # Assert — the actionable card is listed; the parked one is not.
    assert "go" in body and "wait" not in body


def _swept_with_only_parked_cards(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    parked = _t(id="c1", owner="alice", status="blocked", hours_ago=10.0)
    parked["blocker"] = "operator-decision"
    out = sweep_reminders(
        [parked], store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    return out, rec


def test_owner_with_only_parked_blocked_is_not_nagged(tmp_path):
    # Arrange
    out, _rec = _swept_with_only_parked_cards(tmp_path)
    # Act
    digested = out["digested"]
    # Assert — nothing they can move, so nothing to nag about.
    assert digested == []


def test_an_all_parked_owner_gets_no_note_at_all(tmp_path):
    # Arrange
    _out, rec = _swept_with_only_parked_cards(tmp_path)
    # Act
    sent = rec.calls
    # Assert — an empty digest must not be sent as an empty digest.
    assert sent == []


def test_blocked_without_blocker_is_still_digested(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    amb = _t(id="c1", owner="alice", status="blocked", hours_ago=10.0)
    # Act
    out = sweep_reminders(
        [amb], store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    # Assert — blocked with NO blocker is ambiguous, not clearly parked.
    assert out["digested"] == ["alice"]


#: No agent and no assignee, so the owner resolves to "(unassigned)" — a bucket
#: with nobody behind it. Neither the report nor the wire may address it.
def _swept_with_an_unassigned_card(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        {
            "id": "c1",
            "title": "x",
            "status": "in_progress",
            "last_activity": "2026-06-01T00:00:00Z",
        }
    ]
    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    return out, rec


def test_unassigned_cards_are_not_nagged(tmp_path):
    # Arrange
    out, _rec = _swept_with_an_unassigned_card(tmp_path)
    # Act
    digested = out["digested"]
    # Assert
    assert digested == []


def test_no_note_is_addressed_to_the_unassigned_bucket(tmp_path):
    # Arrange
    _out, rec = _swept_with_an_unassigned_card(tmp_path)
    # Act
    sent = rec.calls
    # Assert — "(unassigned)" is not a recipient anyone drains.
    assert sent == []


# === owner allowlist — phased rollout (nag only listed owners) ============


#: A phased rollout: only listed owners are nagged. Both the REPORT and the
#: WIRE are checked, in separate tests — an allowlist that filtered the report
#: but still enqueued for bob would be exactly the leak it exists to prevent.
def _swept_with_an_allowlist(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({}),
        owners={"alice"},
    )
    return out, rec


def test_owner_allowlist_arg_nags_only_listed_owner(tmp_path):
    # Arrange
    out, _rec = _swept_with_an_allowlist(tmp_path)
    # Act
    digested = out["digested"]
    # Assert — bob is left untouched.
    assert digested == ["alice"]


def test_the_allowlist_enqueues_only_for_the_listed_owner(tmp_path):
    # Arrange
    _out, rec = _swept_with_an_allowlist(tmp_path)
    # Act
    recipients = [c["recipient"] for c in rec.calls]
    # Assert — the filter reaches the wire, not just the report.
    assert recipients == ["alice"]


def test_owner_allowlist_env_scopes_the_sweep(tmp_path, monkeypatch):
    # Arrange
    from scitex_cards._reminders import ENV_REMINDER_OWNERS

    monkeypatch.setenv(ENV_REMINDER_OWNERS, "alice, carol")
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    # Act
    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    # Assert — carol is listed but has no cards; bob has cards but is not listed.
    assert out["digested"] == ["alice"]


def test_owner_allowlist_config_scopes_the_sweep(tmp_path, monkeypatch):
    # Arrange
    cfg = tmp_path / "config.yaml"
    cfg.write_text("reminders:\n  owners: [alice]\n", encoding="utf-8")
    monkeypatch.setattr("scitex_cards._config.config_paths", lambda: [cfg])
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    # Act
    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    # Assert — config-scoped to alice.
    assert out["digested"] == ["alice"]


def test_empty_allowlist_nags_all_owners(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _t(id="c1", owner="alice", hours_ago=10.0),
        _t(id="c2", owner="bob", hours_ago=10.0),
    ]
    # Act
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({}),
        owners=set(),
    )
    # Assert — empty means "no restriction", not "nobody".
    assert sorted(out["digested"]) == ["alice", "bob"]


# === legacy sidecar tolerance (per-card schema from the prior engine) ======


def test_legacy_cards_only_sidecar_loads_and_digests(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sidecar = tmp_path / "runtime" / "reminders.yaml"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    # The old engine wrote a bare ``cards:`` mapping with per-card cadence
    # fields and no ``owners:`` section at all.
    sidecar.write_text(
        "cards:\n  c1:\n    count: 2\n    last_at: 2026-06-01T00:00:00Z\n"
        "    escalated: true\n",
        encoding="utf-8",
    )
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=10.0)]
    # Act
    out = sweep_reminders(
        tasks, store=store, now=_NOW, enqueue=rec, resolve_key=_resolver({})
    )
    # Assert — it loads rather than crashing, and the owner is due immediately.
    assert out["digested"] == ["alice"]


# === liveness → creator escalation (Slice 3) ===============================
#
# Real fakes: ``resolve_user`` maps an owner name → a user DICT carrying a
# ``last_seen`` stamp (or None for a non-registered owner); ``is_alive``
# classifies it against the injected ``now``/ttl. No mocks, no clock.


def _task_with_creator(id, owner, creator, *, hours_ago=50.0, priority=None):
    card = _t(id=id, owner=owner, hours_ago=hours_ago, priority=priority)
    if creator is not None:
        card["created_by"] = creator
    return card


#: alice was last seen 2 h ago, well past the 600 s ttl, so she reads as
#: "stale" — the owner is not running and the card will rot unless somebody
#: else is told. Seven tests split what one asserted about the resulting
#: escalation: the report, that exactly one fired, and each field of it. The
#: recipient must be the CREATOR (bob) rather than the dead owner, the body must
#: NAME the dead owner (or the reader cannot tell why they were pulled in), and
#: `supersede` must be False — a per-card escalation is a distinct event, and
#: collapsing them would hide every card but the newest.
def _creator_escalated_for_stale_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )
    return out, rec


def test_creator_escalation_fires_when_owner_is_stale(tmp_path):
    # Arrange
    out, _rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    escalated = out["creator_escalated"]
    # Assert
    assert escalated == ["c1"]


def test_the_creator_escalation_fires_exactly_once(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    fired = len(_creator_escs(rec))
    # Assert
    assert fired == 1


def test_the_creator_escalation_is_addressed_to_the_creator(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    recipient = _creator_escs(rec)[0]["recipient"]
    # Assert — telling the dead owner again would reach nobody.
    assert recipient == "u_bob"


def test_the_creator_escalation_names_the_card(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    card_id = _creator_escs(rec)[0]["card_id"]
    # Assert
    assert card_id == "c1"


def test_the_creator_escalation_is_attributed_to_notifyd(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    actor = _creator_escs(rec)[0]["actor"]
    # Assert
    assert actor == "notifyd"


def test_the_creator_escalation_names_the_dead_owner(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    body = _creator_escs(rec)[0]["body"]
    # Assert — bob needs to know WHY this landed on him.
    assert "alice" in body


def test_the_creator_escalation_does_not_supersede(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_stale_owner(tmp_path)
    # Act
    supersede = _creator_escs(rec)[0]["supersede"]
    # Assert — per-card events must not collapse; only the digest does.
    assert supersede is False


#: alice IS a registered user but has never been seen (no last_seen), so
#: `is_alive` reads "unknown" — she is not demonstrably running, so escalate.
def _creator_escalated_for_unknown_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": {"id": "u_alice"}}),  # no last_seen
        liveness_ttl=600,
    )
    return out, rec


def test_creator_escalation_fires_when_owner_is_unknown(tmp_path):
    # Arrange
    out, _rec = _creator_escalated_for_unknown_owner(tmp_path)
    # Act
    escalated = out["creator_escalated"]
    # Assert — unknown is treated as not-running, not as fine.
    assert escalated == ["c1"]


def test_the_unknown_owner_escalation_reaches_the_creator(tmp_path):
    # Arrange
    _out, rec = _creator_escalated_for_unknown_owner(tmp_path)
    # Act
    recipient = _creator_escs(rec)[0]["recipient"]
    # Assert
    assert recipient == "u_bob"


#: alice was last seen 60 s ago, inside the 600 s ttl, so she is ALIVE and will
#: act — nobody else needs pulling in. The ordinary digest must still fire,
#: which is the third test: the liveness path adds an escalation, it does not
#: replace the owner nag.
def _swept_with_a_live_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=60)}),
        liveness_ttl=600,
    )
    return out, rec


def test_creator_escalation_does_not_fire_when_owner_is_alive(tmp_path):
    # Arrange
    out, _rec = _swept_with_a_live_owner(tmp_path)
    # Act
    escalated = out["creator_escalated"]
    # Assert
    assert escalated == []


def test_a_live_owner_sends_no_creator_note(tmp_path):
    # Arrange
    _out, rec = _swept_with_a_live_owner(tmp_path)
    # Act
    sent = _creator_escs(rec)
    # Assert — the report and the wire agree.
    assert sent == []


def test_a_live_owner_still_gets_the_ordinary_digest(tmp_path):
    # Arrange
    out, _rec = _swept_with_a_live_owner(tmp_path)
    # Act
    digested = out["digested"]
    # Assert — the liveness path is additive, not a replacement.
    assert digested == ["alice"]


#: alice resolves to no user at all (a free-form, non-registered owner), so
#: there is no liveness signal to act on. No signal is not a dead signal.
def _swept_with_a_non_registered_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({}),
        resolve_user=_user_resolver({}),
        liveness_ttl=600,
    )
    return out, rec


def test_creator_escalation_does_not_fire_for_non_registered_owner(tmp_path):
    # Arrange
    out, _rec = _swept_with_a_non_registered_owner(tmp_path)
    # Act
    escalated = out["creator_escalated"]
    # Assert — no liveness signal means no verdict, so no escalation.
    assert escalated == []


def test_a_non_registered_owner_sends_no_creator_note(tmp_path):
    # Arrange
    _out, rec = _swept_with_a_non_registered_owner(tmp_path)
    # Act
    sent = _creator_escs(rec)
    # Assert
    assert sent == []


#: Two sweeps 30 min apart on the same dead-owner card. Latched once per stale
#: streak — a nudge, not a per-sweep spam stream. Both the count and the
#: persisted latch are asserted: a latch that never persisted would still pass
#: a two-sweep count check on a longer run.
def _creator_escalated_twice(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=30), **kw)
    return rec, store


def test_creator_escalation_latches_once_per_streak(tmp_path):
    # Arrange
    rec, _store = _creator_escalated_twice(tmp_path)
    # Act
    fired = len(_creator_escs(rec))
    # Assert
    assert fired == 1


def test_the_creator_latch_is_persisted_on_the_card(tmp_path):
    # Arrange
    _rec, store = _creator_escalated_twice(tmp_path)
    # Act
    card_state = load_reminder_state(store)["cards"]["c1"]
    # Assert — the latch survives the process, not just the loop.
    assert card_state["creator_escalated"] is True


#: Creator == the (dead) owner, so escalating to a dead self is pointless; the
#: engine falls back to the operator.
def _creator_is_the_dead_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="alice")]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}),
        operator="operator",
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )
    return out, rec


def test_creator_escalation_falls_back_to_operator_when_creator_is_owner(tmp_path):
    # Arrange
    out, _rec = _creator_is_the_dead_owner(tmp_path)
    # Act
    escalated = out["creator_escalated"]
    # Assert — the escalation still happens; only its recipient changes.
    assert escalated == ["c1"]


def test_the_self_creator_fallback_is_addressed_to_the_operator(tmp_path):
    # Arrange
    _out, rec = _creator_is_the_dead_owner(tmp_path)
    # Act
    recipient = _creator_escs(rec)[0]["recipient"]
    # Assert
    assert recipient == "u_op"


#: No ``created_by`` on the card at all, so there is no creator to escalate to;
#: the engine falls back to the operator rather than dropping the escalation.
def _creator_is_missing(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator=None)]
    out = sweep_reminders(
        tasks,
        store=store,
        now=_NOW,
        enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}),
        operator="operator",
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )
    return out, rec


def test_creator_escalation_falls_back_to_operator_when_creator_missing(tmp_path):
    # Arrange
    out, _rec = _creator_is_missing(tmp_path)
    # Act
    escalated = out["creator_escalated"]
    # Assert — a missing creator must not silently swallow the escalation.
    assert escalated == ["c1"]


def test_the_missing_creator_fallback_is_addressed_to_the_operator(tmp_path):
    # Arrange
    _out, rec = _creator_is_missing(tmp_path)
    # Act
    recipient = _creator_escs(rec)[0]["recipient"]
    # Assert
    assert recipient == "u_op"


#: A dead owner fires the creator escalation even when the digest is NOT yet
#: due — it does not wait for the escalate_after count. The first sweep digests
#: and creator-escalates; the second, one minute later, finds the digest not due
#: (5 min interval) so alice drops into the early-continue path — and the
#: escalation, already latched, stays a single nudge.
def _swept_while_the_digest_is_not_due(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
        interval_minutes=5.0,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=1), **kw)
    return out2, rec


def test_the_digest_is_not_due_one_minute_later(tmp_path):
    # Arrange
    out2, _rec = _swept_while_the_digest_is_not_due(tmp_path)
    # Act
    skipped = out2["skipped"]
    # Assert — without this, the latch test below proves nothing.
    assert skipped == ["alice"]


def test_creator_escalation_ignores_digest_cadence(tmp_path):
    # Arrange
    _out2, rec = _swept_while_the_digest_is_not_due(tmp_path)
    # Act
    fired = len(_creator_escs(rec))
    # Assert — it fired on sweep 1 despite the cadence, then latched.
    assert fired == 1


#: The card goes done → it leaves the stale set → the creator latch resets so a
#: future stall re-escalates (the same pruning contract as the operator latch).
def _creator_latched_then_card_done(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_task_with_creator("c1", owner="alice", creator="bob")]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({"bob": "u_bob"}),
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=7200)}),
        liveness_ttl=600,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    latched = "c1" in load_reminder_state(store)["cards"]

    done = [_task_with_creator("c1", owner="alice", creator="bob", hours_ago=50.0)]
    done[0]["status"] = "done"
    sweep_reminders(done, now=_NOW + _dt.timedelta(hours=1), **kw)
    return latched, store


def test_the_first_sweep_latches_the_creator_escalation(tmp_path):
    # Arrange
    latched, _store = _creator_latched_then_card_done(tmp_path)
    # Act
    was_latched = latched
    # Assert
    assert was_latched is True


def test_creator_escalation_latch_pruned_when_card_no_longer_stale(tmp_path):
    # Arrange
    _latched, store = _creator_latched_then_card_done(tmp_path)
    # Act
    cards = load_reminder_state(store)["cards"]
    # Assert — a future stall must be able to re-escalate.
    assert "c1" not in cards


#: When the owner is ALIVE, the pre-existing high-priority→operator escalation
#: must still fire unchanged and NO creator escalation may appear. Three tests,
#: because "the liveness path broke the operator path" and "the liveness path
#: fired when it should not have" are different regressions.
def _swept_with_a_live_high_priority_owner(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [
        _task_with_creator(
            "c1", owner="alice", creator="bob", hours_ago=50.0, priority=0
        )
    ]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}),
        operator="operator",
        resolve_user=_user_resolver({"alice": _seen(_NOW, seconds_ago=60)}),
        escalate_after=1,
        escalate_priority=1,
        interval_minutes=5.0,
        liveness_ttl=600,
    )
    out = sweep_reminders(tasks, now=_NOW, **kw)
    return out, rec


def test_liveness_path_does_not_disturb_operator_escalation(tmp_path):
    # Arrange
    out, _rec = _swept_with_a_live_high_priority_owner(tmp_path)
    # Act
    escalated = out["escalated"]
    # Assert — the operator path is intact.
    assert escalated == ["c1"]


def test_the_operator_escalation_still_reaches_the_operator(tmp_path):
    # Arrange
    _out, rec = _swept_with_a_live_high_priority_owner(tmp_path)
    # Act
    recipient = _escalations(rec)[0]["recipient"]
    # Assert
    assert recipient == "u_op"


def test_a_live_owner_adds_no_creator_escalation(tmp_path):
    # Arrange
    out, _rec = _swept_with_a_live_high_priority_owner(tmp_path)
    # Act
    creator_escalated = out["creator_escalated"]
    # Assert — the owner is alive, so the creator path stays out of it.
    assert creator_escalated == []


#: Suppressing the OWNER's digest must never silence the OPERATOR escalation.
#: The first cut of deliver-on-change ``continue``d past the escalation block,
#: so a high-priority card could rot forever with nobody told. The digest tick
#: advances on the cadence; only the owner-facing enqueue is conditional. Four
#: tests, because the whole point is that these four move INDEPENDENTLY.
def _swept_with_the_digest_suppressed(tmp_path):
    store = tmp_path / "tasks.yaml"
    rec = _EnqueueRecorder()
    tasks = [_t(id="c1", owner="alice", hours_ago=50.0, priority=0)]
    kw = dict(
        store=store,
        enqueue=rec,
        resolve_key=_resolver({"operator": "u_op"}),
        operator="operator",
        escalate_after=2,
        escalate_priority=1,
        interval_minutes=5.0,
    )
    sweep_reminders(tasks, now=_NOW, **kw)
    out2 = sweep_reminders(tasks, now=_NOW + _dt.timedelta(minutes=6), **kw)
    return out2, rec


def test_the_owner_digest_is_suppressed_on_unchanged_content(tmp_path):
    # Arrange
    out2, _rec = _swept_with_the_digest_suppressed(tmp_path)
    # Act
    digested = out2["digested"]
    # Assert — the owner is not re-woken.
    assert digested == []


def test_the_suppressed_owner_is_reported_as_skipped(tmp_path):
    # Arrange
    out2, _rec = _swept_with_the_digest_suppressed(tmp_path)
    # Act
    skipped = out2["skipped"]
    # Assert
    assert skipped == ["alice"]


def test_escalation_still_fires_while_the_digest_is_suppressed(tmp_path):
    # Arrange
    out2, _rec = _swept_with_the_digest_suppressed(tmp_path)
    # Act
    escalated = out2["escalated"]
    # Assert — the owner went quiet, but the operator IS told.
    assert escalated == ["c1"]


def test_the_suppressed_sweep_sends_one_escalation_note(tmp_path):
    # Arrange
    _out2, rec = _swept_with_the_digest_suppressed(tmp_path)
    # Act
    fired = len(_escalations(rec))
    # Assert — the report and the wire agree.
    assert fired == 1


# EOF
