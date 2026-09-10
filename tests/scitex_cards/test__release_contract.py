#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Cards release-stage promotion contract (own-ledger #224/#225).

These pin that the promotion contract is STRUCTURALLY enforceable: a fixed
stage order, named readiness criteria per rung, and the rule that a promotion
is one rung forward with every source-stage criterion satisfied. They also pin
that the contract does NOT make Cards public — the current stage is read from
the manifest (declared ``internal``), and reaching ``public`` requires a valid,
fully-evidenced ladder.

Hermetic: the contract is pure (stdlib only, no Django, no store, no hub), so
these run on a lean install and are the guaranteed-red regression if the stage
order or the "no skip" rule is ever broken. The current-stage read is asserted
against the committed manifest, which this branch declares as ``internal``.
"""

from __future__ import annotations

import pytest

from scitex_cards import _release_contract as rc  # noqa: E402


# --- the fixed stage order --------------------------------------------------


def test_release_stages_is_the_ordered_ladder():
    # Arrange
    # Act
    order = rc.RELEASE_STAGES
    # Assert
    assert order == (rc.INTERNAL, rc.DOGFOODING, rc.STABLE, rc.PUBLIC)


def test_each_stage_has_a_next_except_terminal_public():
    # Arrange
    # Act
    chain = [rc.next_stage(s) for s in rc.RELEASE_STAGES]
    # Assert — internal->dogfooding->stable->public->None (terminal)
    assert chain == [rc.DOGFOODING, rc.STABLE, rc.PUBLIC, None]


def test_an_unknown_stage_is_rejected():
    # Arrange
    # Act
    # Assert
    assert not rc.is_valid_stage("beta")


# --- the no-skip rule -------------------------------------------------------


def test_a_promotion_cannot_skip_a_rung():
    # Arrange — internal -> stable skips dogfooding.
    # Act
    # Assert — refused as a contract violation (the raise is the assertion).
    with pytest.raises(rc.ReleaseStageError):
        rc.assert_promotion(rc.INTERNAL, rc.STABLE, _all_satisfied(rc.INTERNAL))


def test_a_promotion_cannot_jump_straight_to_public():
    # Arrange — the dangerous case: internal -> public, skipping everything.
    # Act
    # Assert — refused as a contract violation (the raise is the assertion).
    with pytest.raises(rc.ReleaseStageError):
        rc.assert_promotion(rc.INTERNAL, rc.PUBLIC, _all_satisfied(rc.INTERNAL))


def test_an_invalid_target_stage_is_rejected():
    # Arrange — "beta" is not a stage in the ladder.
    # Act
    # Assert — refused as an unknown stage (the raise is the assertion).
    with pytest.raises(rc.ReleaseStageError):
        rc.assert_promotion(rc.INTERNAL, "beta", _all_satisfied(rc.INTERNAL))


# --- readiness criteria gate each rung --------------------------------------


def test_a_rung_promotes_when_all_criteria_are_satisfied():
    # Arrange
    # Act
    ok = rc.assert_promotion(rc.INTERNAL, rc.DOGFOODING, _all_satisfied(rc.INTERNAL))
    # Assert
    assert ok == rc.DOGFOODING


def test_an_unsatisfied_criterion_blocks_the_promotion():
    # Arrange — one internal criterion left un-evidenced.
    sat = _all_satisfied(rc.INTERNAL)
    first = next(iter(rc.criteria_for(rc.INTERNAL)))
    sat[first] = False
    # Act
    # Assert
    with pytest.raises(rc.ReleaseStageError):
        rc.assert_promotion(rc.INTERNAL, rc.DOGFOODING, sat)


def test_a_missing_criterion_verdict_is_not_a_pass():
    # Arrange — a criterion the evidence never answered.
    sat = _all_satisfied(rc.INTERNAL)
    first = next(iter(rc.criteria_for(rc.INTERNAL)))
    del sat[first]
    # Act
    met = rc.criteria_met(rc.INTERNAL, sat)
    # Assert
    assert met is False


def test_every_nonterminal_stage_has_at_least_one_criterion():
    # Arrange — a rung with no criteria could be promoted without evidence.
    # Act
    bare = [
        s
        for s in rc.RELEASE_STAGES
        if s != rc.PUBLIC and not rc.criteria_for(s)
    ]
    # Assert
    assert bare == []


# --- the contract does NOT make Cards public --------------------------------


def test_cards_current_stage_is_declared_internal_not_public():
    # Arrange
    # Act
    stage = rc.current_stage()
    # Assert — the manifest declares the CURRENT stage; this branch keeps it
    # internal. Reaching public is a later, fully-evidenced operator decision.
    assert stage == rc.INTERNAL


def test_stage_is_public_reflects_the_manifest_declaration():
    # Arrange
    # Act
    is_public = rc.stage_is_public()
    # Assert
    assert is_public is False


def test_a_full_valid_ladder_can_reach_public():
    # Arrange — the happy path, rung by rung, every criterion evidenced.
    # Act
    s = rc.INTERNAL
    s = rc.assert_promotion(s, rc.DOGFOODING, _all_satisfied(s))
    s = rc.assert_promotion(s, rc.STABLE, _all_satisfied(s))
    s = rc.assert_promotion(s, rc.PUBLIC, _all_satisfied(s))
    # Assert
    assert s == rc.PUBLIC


def test_public_is_terminal():
    # Arrange
    # Act
    nxt = rc.next_stage(rc.PUBLIC)
    # Assert
    assert nxt is None


# --- helper -----------------------------------------------------------------


def _all_satisfied(stage: str) -> dict:
    """A satisfaction map with every criterion for ``stage`` answered True —
    the evidence-verdict shape the contract consumes."""
    return {name: True for name in rc.criteria_for(stage)}
