#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Owner-SSOT + fail-loud owner/creator resolution (operator mandate 2026-06-26).

Covers the three deliverables that have NO mocks anywhere (PA-306 / STX-NM):

* :func:`scitex_cards._owner.card_owner` — the single owner rule (``agent``
  falling back to ``assignee``, ``None`` for neither).
* :func:`scitex_cards._store.add_task` FAIL-LOUD — raises (with an actionable
  hint) when the assignee/owner is missing OR the creator is unresolvable;
  succeeds with both and keeps ``agent`` == ``assignee`` in lock-step + stamps
  a real ``created_by``.
* :func:`scitex_cards._django.handlers._comment_relay.comment_inbox_toast`
  — the comment toast now reflects the standalone INBOX QUEUE (the
  recipient names the ``commented`` notification was enqueued to) rather
  than the old direct-POST result; ``target`` resolves via the owner SSOT
  (``agent`` falling back to ``assignee``), and the author (actor) is never
  in ``queued``. No network.

The store tests drive the env directly via the ``env`` fixture (the
PA-306-compliant monkeypatch replacement from ``conftest.py``), never a mock.
"""

from __future__ import annotations

import pytest

from scitex_cards import _store
from scitex_cards._model import TaskValidationError
from scitex_cards._owner import card_owner


# --------------------------------------------------------------------------- #
# card_owner — the owner SSOT                                                 #
# --------------------------------------------------------------------------- #
def test_card_owner_prefers_agent():
    # Arrange
    card = {"agent": "alice", "assignee": "bob"}
    # Act
    owner = card_owner(card)
    # Assert
    assert owner == "alice"


def test_card_owner_falls_back_to_assignee():
    # Arrange
    card = {"assignee": "bob"}
    # Act
    owner = card_owner(card)
    # Assert
    assert owner == "bob"


def test_card_owner_none_for_neither():
    # Arrange
    card = {"id": "x", "title": "t"}
    # Act
    owner = card_owner(card)
    # Assert
    assert owner is None


#: WHY the two `card_owner` whitespace tests below are split but share this
#: rationale: whitespace handling is two rules pulling in opposite directions
#: — a field holding ONLY blanks is no owner at all, while a real name wearing
#: stray blanks is a real owner that must be stripped. Collapsing them into
#: one test lets a resolver that strips everything to `None` pass the half
#: that matters least.
def test_card_owner_treats_a_blank_owner_as_none():
    # Arrange
    card = {"agent": "  ", "assignee": "  "}
    # Act
    owner = card_owner(card)
    # Assert
    assert owner is None


def test_card_owner_strips_surrounding_whitespace():
    # Arrange
    card = {"agent": "  alice  "}
    # Act
    owner = card_owner(card)
    # Assert
    assert owner == "alice"


#: WHY the two `non_mapping` tests below are split but share this rationale:
#: `card_owner` is called on whatever a caller happens to hold, so it must
#: survive both a missing card (`None`) and a card-shaped-but-not-a-mapping
#: value (a bare string). They fail through different branches.
def test_card_owner_returns_none_for_a_missing_card():
    # Arrange
    card = None
    # Act
    owner = card_owner(card)
    # Assert
    assert owner is None


def test_card_owner_returns_none_for_a_non_mapping_card():
    # Arrange
    card = "not-a-card"
    # Act
    owner = card_owner(card)
    # Assert
    assert owner is None


# --------------------------------------------------------------------------- #
# add_task — FAIL LOUD on missing owner / unresolvable creator                #
# --------------------------------------------------------------------------- #
def test_add_task_raises_when_owner_missing(tmp_path, env):
    # Arrange — a resolvable creator is set (suite default), but NO
    # assignee/agent.
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    # Act
    ctx = pytest.raises(TaskValidationError, match="assignee is required")
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A")


def test_add_task_raises_when_creator_unresolvable(tmp_path, env):
    # Arrange — owner IS supplied, but the creator cannot be resolved (no env,
    # no arg).
    env.delete("SCITEX_TODO_AGENT_ID")
    store = tmp_path / "tasks.yaml"
    # Act
    ctx = pytest.raises(TaskValidationError, match="creator unresolved")
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")


def test_add_task_creator_unknown_sentinel_also_raises(tmp_path, env):
    # Arrange — the "unknown" placeholder is NOT a real creator: fail loud, no
    # silent stamping of "unknown".
    env.set("SCITEX_TODO_AGENT_ID", "unknown")
    store = tmp_path / "tasks.yaml"
    # Act
    ctx = pytest.raises(TaskValidationError, match="creator unresolved")
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")


def test_add_task_succeeds_and_stamps_created_by(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(store, id="a", title="A", assignee="agent:owner")
    # Assert
    assert inserted["created_by"] == "agent:creator"


def test_add_task_deprecated_agent_env_fails_loud(tmp_path, env):
    """The renamed-away $SCITEX_TODO_AGENT must never be silently honoured: if
    it is still exported, creator resolution fails LOUD pointing at the new
    name so a stale export can't quietly mis-attribute a card's creator."""
    # Arrange: a valid NEW-name creator is present AND the deprecated old name
    # is set — the guard must still fire (never silently prefers the new one).
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    env.set("SCITEX_TODO_AGENT", "legacy-agent")
    store = tmp_path / "tasks.yaml"
    # Act
    ctx = pytest.raises(RuntimeError, match="SCITEX_TODO_AGENT_ID")
    # Assert
    with ctx:
        _store.add_task(store, id="a", title="A", assignee="agent:owner")


def test_add_task_explicit_created_by_wins(tmp_path, env):
    # Arrange
    env.delete("SCITEX_TODO_AGENT_ID")
    store = tmp_path / "tasks.yaml"
    # Act
    inserted = _store.add_task(
        store, id="a", title="A", assignee="agent:owner", created_by="agent:explicit"
    )
    # Assert
    assert inserted["created_by"] == "agent:explicit"


#: WHY the three `lockstep_assignee_only` tests below are split but share this
#: rationale: an assignee-only call must stamp `agent` to the SAME owner
#: (lock-step) so the board/relay/notify never see an owner-less or half-owned
#: card. Lock-step is exactly the property that half-fails: the field you
#: passed is trivially right, the MIRRORED field is the one that regresses,
#: and `card_owner` reading back the same name is what the rest of the system
#: actually depends on.
@pytest.fixture()
def lockstep_from_assignee(tmp_path, env):
    """add_task called with `assignee` only."""
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    return _store.add_task(store, id="a", title="A", assignee="agent:owner")


def test_add_task_lockstep_assignee_only_keeps_the_assignee(lockstep_from_assignee):
    # Arrange
    inserted = lockstep_from_assignee
    # Act
    assignee = inserted["assignee"]
    # Assert
    assert assignee == "agent:owner"


def test_add_task_lockstep_assignee_only_sets_agent(lockstep_from_assignee):
    # Arrange
    inserted = lockstep_from_assignee
    # Act
    agent = inserted["agent"]
    # Assert — the mirrored field, stamped in lock-step.
    assert agent == "agent:owner"


def test_add_task_lockstep_assignee_only_resolves_through_the_ssot(
    lockstep_from_assignee,
):
    # Arrange
    inserted = lockstep_from_assignee
    # Act
    owner = card_owner(inserted)
    # Assert — what the board/relay/notify actually read.
    assert owner == "agent:owner"


#: WHY the two `lockstep_agent_only` tests below are split but share this
#: rationale: the mirror image of the pair above — an agent-only call must
#: stamp `assignee` to the SAME owner (lock-step).
@pytest.fixture()
def lockstep_from_agent(tmp_path, env):
    """add_task called with `agent` only."""
    env.set("SCITEX_TODO_AGENT_ID", "agent:creator")
    store = tmp_path / "tasks.yaml"
    return _store.add_task(store, id="a", title="A", agent="agent:owner")


def test_add_task_lockstep_agent_only_keeps_the_agent(lockstep_from_agent):
    # Arrange
    inserted = lockstep_from_agent
    # Act
    agent = inserted["agent"]
    # Assert
    assert agent == "agent:owner"


def test_add_task_lockstep_agent_only_sets_assignee(lockstep_from_agent):
    # Arrange
    inserted = lockstep_from_agent
    # Act
    assignee = inserted["assignee"]
    # Assert — the mirrored field, stamped in lock-step.
    assert assignee == "agent:owner"


# --------------------------------------------------------------------------- #
# _default_agent — FAIL LOUD actor/author resolution (operator mandate)       #
#                                                                             #
# Completion/comment authorship used to fall back to getpass.getuser() /      #
# "unknown", which mis-attributed an unresolved acting agent on the board.    #
# It now fails loud EXACTLY like the creator resolver (constitution rule 2,   #
# "NO silent fallbacks") — it delegates to _resolve_creator_or_raise.         #
# --------------------------------------------------------------------------- #
#: WHY the two `raises_when_unresolved` tests below are split but share this
#: rationale: no explicit arg AND no $SCITEX_TODO_AGENT_ID → fail loud (no
#: getuser()). The raise is only ACTIONABLE if the message names BOTH ways out
#: — the env var to export and the `by=` argument to pass — so each half of
#: the hint is pinned on its own.
def test_default_agent_raise_names_the_identity_env_var(env):
    # Arrange
    env.delete("SCITEX_TODO_AGENT_ID")
    # Act
    ctx = pytest.raises(TaskValidationError, match="SCITEX_TODO_AGENT_ID")
    # Assert
    with ctx:
        _store._default_agent(None)


def test_default_agent_raise_names_the_by_argument(env):
    # Arrange
    env.delete("SCITEX_TODO_AGENT_ID")
    # Act
    ctx = pytest.raises(TaskValidationError, match="by=")
    # Assert
    with ctx:
        _store._default_agent(None)


def test_default_agent_unknown_sentinel_also_raises(env):
    # Arrange — the "unknown" placeholder is NOT a real actor: fail loud,
    # never stamp it.
    env.set("SCITEX_TODO_AGENT_ID", "unknown")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _store._default_agent(None)


def test_default_agent_resolves_from_env(env):
    # Arrange
    env.set("SCITEX_TODO_AGENT_ID", "agent:actor-env")
    # Act
    resolved = _store._default_agent(None)
    # Assert
    assert resolved == "agent:actor-env"


def test_default_agent_explicit_arg_wins(env):
    # Arrange — an explicit by=/actor is used even with the env unset: the
    # raise only fires when NOTHING is resolvable (legitimate callers are
    # unaffected).
    env.delete("SCITEX_TODO_AGENT_ID")
    # Act
    resolved = _store._default_agent("agent:explicit-actor")
    # Assert
    assert resolved == "agent:explicit-actor"


#: WHY the three `matches_creator_resolver` tests below are split but share
#: this rationale: DRY — `_default_agent` now shares the SSOT fail-loud
#: behaviour with `_resolve_creator_or_raise` (both resolve/raise
#: identically). "Identical" is three claims: they agree on the RESOLVED
#: value, and each raises on its own when nothing is resolvable. Two functions
#: that agree while resolvable but diverge on the failure path are exactly the
#: drift this pins.
def test_default_agent_matches_creator_resolver(env):
    # Arrange
    env.set("SCITEX_TODO_AGENT_ID", "agent:same")
    # Act
    resolved = _store._default_agent(None)
    # Assert
    assert resolved == _store._resolve_creator_or_raise(None)


def test_default_agent_raises_when_unresolved(env):
    # Arrange
    env.delete("SCITEX_TODO_AGENT_ID")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        _store._default_agent(None)


def test_creator_resolver_raises_when_unresolved(env):
    # Arrange
    env.delete("SCITEX_TODO_AGENT_ID")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert — the sibling resolver fails the same way, not just similarly.
    with ctx:
        _store._resolve_creator_or_raise(None)


# --------------------------------------------------------------------------- #
# comment toast — INBOX QUEUE shape + SSOT target; actor never queued           #
# --------------------------------------------------------------------------- #
def _toast():
    from scitex_cards._django.handlers._comment_relay import comment_inbox_toast

    return comment_inbox_toast


#: WHY the four `toast_no_owner` tests below are split but share this
#: rationale: the card has NEITHER agent NOR assignee — no owner to target.
#: The toast still reports the inbox wire (delivery is the always-works rail)
#: with an empty target + empty queue. No network involved (no deliver()).
#: "It reported success", "over the inbox rail", "at nobody", "queuing
#: nothing" are four independent ways an owner-less card goes wrong — most
#: dangerously by quietly queuing to an empty-string recipient.
@pytest.fixture()
def toast_for_owner_less_card():
    """Toast a card that has no owner at all."""
    return _toast()({"id": "orphan", "title": "x"}, "operator")


def test_toast_no_owner_still_reports_sent(toast_for_owner_less_card):
    # Arrange
    result = toast_for_owner_less_card
    # Act
    sent = result["sent"]
    # Assert
    assert sent is True


def test_toast_no_owner_uses_the_inbox_wire(toast_for_owner_less_card):
    # Arrange
    result = toast_for_owner_less_card
    # Act
    wire = result["wire"]
    # Assert — the always-works rail, not a network POST.
    assert wire == "inbox"


def test_toast_no_owner_has_an_empty_target(toast_for_owner_less_card):
    # Arrange
    result = toast_for_owner_less_card
    # Act
    target = result["target"]
    # Assert
    assert target == ""


def test_toast_no_owner_queues_nobody(toast_for_owner_less_card):
    # Arrange
    result = toast_for_owner_less_card
    # Act
    queued = result["queued"]
    # Assert
    assert queued == []


#: WHY the two `toast_assignee_only` tests below are split but share this
#: rationale: an assignee-only card resolves its owner (the toast TARGET) via
#: the owner SSOT (card_owner: agent → assignee). The wire and the resolved
#: target are separate claims. No network.
@pytest.fixture()
def toast_for_assignee_only_card():
    """Toast a card whose owner is reachable only through `assignee`."""
    return _toast()({"id": "c1", "title": "x", "assignee": "bob"}, "operator")


def test_toast_assignee_only_uses_the_inbox_wire(toast_for_assignee_only_card):
    # Arrange
    result = toast_for_assignee_only_card
    # Act
    wire = result["wire"]
    # Assert
    assert wire == "inbox"


def test_toast_target_is_assignee_when_no_agent(toast_for_assignee_only_card):
    # Arrange
    result = toast_for_assignee_only_card
    # Act
    target = result["target"]
    # Assert — resolved through the owner SSOT.
    assert target == "bob"


#: WHY the two `toast_agent_present` tests below are split but share this
#: rationale: the mirror of the pair above — when `agent` IS present it is the
#: target, over the same inbox wire.
@pytest.fixture()
def toast_for_agent_owned_card():
    """Toast a card that carries an explicit `agent` owner."""
    return _toast()({"id": "c1", "title": "x", "agent": "alice"}, "operator")


def test_toast_agent_owned_uses_the_inbox_wire(toast_for_agent_owned_card):
    # Arrange
    result = toast_for_agent_owned_card
    # Act
    wire = result["wire"]
    # Assert
    assert wire == "inbox"


def test_toast_target_is_agent_when_present(toast_for_agent_owned_card):
    # Arrange
    result = toast_for_agent_owned_card
    # Act
    target = result["target"]
    # Assert
    assert target == "alice"


#: WHY the two `toast_self_comment` tests below are split but share this
#: rationale: the author (actor) is never notified of their own comment — when
#: they are the sole recipient (owner==author), `queued` is empty. The target
#: still resolves to the owner via the SSOT, so "who this is about" and "who
#: gets pinged" must be asserted separately: dropping the target along with
#: the ping would satisfy the exclusion half alone.
@pytest.fixture()
def toast_for_self_comment():
    """alice comments on the card alice owns."""
    return _toast()({"id": "c1", "title": "x", "agent": "alice"}, "alice")


def test_toast_self_comment_still_targets_the_owner(toast_for_self_comment):
    # Arrange
    result = toast_for_self_comment
    # Act
    target = result["target"]
    # Assert
    assert target == "alice"


def test_toast_self_comment_does_not_queue_the_author(toast_for_self_comment):
    # Arrange
    result = toast_for_self_comment
    # Act
    queued = result["queued"]
    # Assert
    assert "alice" not in queued


# EOF
