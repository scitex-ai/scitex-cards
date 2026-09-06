#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for git-branch -> board-card soft linking (pure parser; no mocks).

Phase P3 of the task-driven-feedback epic (card tcfb-p3-git-to-card).
Every function under test is pure, so the tests need neither env fixtures
nor tmp repos -- just real string inputs. STRICT scitex doctrine: no
unittest.mock, no monkeypatch, AAA markers each on its own line, one
assertion per test.
"""

from __future__ import annotations

from scitex_cards._git_link import (
    build_push_event,
    extract_card_id,
    extract_card_id_from_message,
    resolve_card_id,
)

# === extract_card_id: typed branch -> card id ===============================


def test_feat_branch_yields_card_id():
    # Arrange
    branch = "feat/tcfb-p3-git-to-card"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id == "tcfb-p3-git-to-card"


def test_fix_branch_yields_multi_token_card_id():
    # Arrange
    branch = "fix/scitex-io-clew-tracker-wiring"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id == "scitex-io-clew-tracker-wiring"


def test_chore_branch_yields_card_id():
    # Arrange
    branch = "chore/full-green-card"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id == "full-green-card"


def test_refactor_branch_yields_card_id():
    # Arrange
    branch = "refactor/scitex-quality"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id == "scitex-quality"


def test_card_id_includes_numeric_tokens():
    # Arrange
    branch = "perf/board-v3-timeline"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id == "board-v3-timeline"


# === extract_card_id: ad-hoc / malformed -> None ===========================


def test_branch_without_type_prefix_is_adhoc():
    # Arrange
    branch = "develop"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


def test_main_branch_is_adhoc():
    # Arrange
    branch = "main"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


def test_unknown_type_prefix_is_adhoc():
    # Arrange
    branch = "wip/some-thing"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


def test_single_token_slug_is_adhoc():
    # Arrange
    branch = "feat/wip"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


def test_empty_branch_is_adhoc():
    # Arrange
    branch = ""
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


def test_none_branch_is_adhoc():
    # Arrange
    branch = None
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


def test_nested_ref_keeps_only_first_segment():
    # Arrange
    branch = "feat/card-one/extra-segment"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id == "card-one"


def test_uppercase_token_is_rejected_as_adhoc():
    # Arrange
    branch = "feat/NotASlug"
    # Act
    card_id = extract_card_id(branch)
    # Assert
    assert card_id is None


# === extract_card_id_from_message: Card: trailer ===========================


def test_card_trailer_in_message_is_extracted():
    # Arrange
    message = "Fix the flaky timer\n\nCard: tcfb-p3-git-to-card"
    # Act
    card_id = extract_card_id_from_message(message)
    # Assert
    assert card_id == "tcfb-p3-git-to-card"


def test_card_trailer_key_is_case_insensitive():
    # Arrange
    message = "subject\n\ncard: scitex-quality"
    # Act
    card_id = extract_card_id_from_message(message)
    # Assert
    assert card_id == "scitex-quality"


def test_last_card_trailer_wins():
    # Arrange
    message = "subject\n\nCard: first-id\nCard: second-id"
    # Act
    card_id = extract_card_id_from_message(message)
    # Assert
    assert card_id == "second-id"


def test_message_without_trailer_is_none():
    # Arrange
    message = "just a normal commit message, no trailer"
    # Act
    card_id = extract_card_id_from_message(message)
    # Assert
    assert card_id is None


def test_empty_message_is_none():
    # Arrange
    message = ""
    # Act
    card_id = extract_card_id_from_message(message)
    # Assert
    assert card_id is None


# === resolve_card_id: branch first, then message fallback ==================


def test_resolve_prefers_branch_over_trailer():
    # Arrange
    branch = "feat/branch-card"
    message = "subject\n\nCard: trailer-card"
    # Act
    card_id = resolve_card_id(branch, message)
    # Assert
    assert card_id == "branch-card"


def test_resolve_falls_back_to_trailer_for_adhoc_branch():
    # Arrange
    branch = "wip"
    message = "Quick fix\n\nCard: scitex-quality"
    # Act
    card_id = resolve_card_id(branch, message)
    # Assert
    assert card_id == "scitex-quality"


def test_resolve_returns_none_when_neither_source_has_id():
    # Arrange
    branch = "wip"
    message = "no trailer present"
    # Act
    card_id = resolve_card_id(branch, message)
    # Assert
    assert card_id is None


# === build_push_event: canonical wire shape ================================


def test_build_event_card_ids_from_branch():
    # Arrange
    kwargs = dict(
        repo="owner/repo", branch="feat/tcfb-p3-git-to-card", commit_sha="abc123"
    )
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["card_ids"] == ["tcfb-p3-git-to-card"]


def test_build_event_kind_is_push():
    # Arrange
    kwargs = dict(repo="owner/repo", branch="feat/some-card", commit_sha="abc123")
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["kind"] == "push"


def test_build_event_carries_commit_sha():
    # Arrange
    kwargs = dict(repo="owner/repo", branch="feat/some-card", commit_sha="deadbeef")
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["commit_sha"] == "deadbeef"


def test_build_event_none_for_adhoc_branch():
    # Arrange
    kwargs = dict(repo="owner/repo", branch="wip", commit_sha="abc123")
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event is None


def test_build_event_none_when_commit_sha_missing():
    # Arrange
    kwargs = dict(repo="owner/repo", branch="feat/some-card", commit_sha="")
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event is None


def test_build_event_uses_trailer_when_branch_adhoc():
    # Arrange
    kwargs = dict(
        repo="owner/repo",
        branch="wip",
        commit_sha="abc123",
        message="subject\n\nCard: trailer-card",
    )
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["card_ids"] == ["trailer-card"]


def test_build_event_explicit_card_id_overrides_parsing():
    # Arrange
    kwargs = dict(
        repo="owner/repo", branch="wip", commit_sha="abc123", card_id="explicit-id"
    )
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["card_ids"] == ["explicit-id"]


# === build_push_event: trigger field (C6 — commit vs push) =================


def test_build_event_default_trigger_is_push():
    # Arrange
    kwargs = dict(repo="owner/repo", branch="feat/some-card", commit_sha="abc123")
    # Act
    event = build_push_event(**kwargs)
    # Assert — historical default keeps the `push` behaviour.
    assert event["trigger"] == "push"


def test_build_event_carries_commit_trigger():
    # Arrange
    kwargs = dict(
        repo="owner/repo",
        branch="feat/some-card",
        commit_sha="abc123",
        trigger="commit",
    )
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["trigger"] == "commit"


def test_build_event_unknown_trigger_coerced_to_push():
    # Arrange — a hint, never a hard contract: bogus → fail-soft `push`.
    kwargs = dict(
        repo="owner/repo",
        branch="feat/some-card",
        commit_sha="abc123",
        trigger="bogus",
    )
    # Act
    event = build_push_event(**kwargs)
    # Assert
    assert event["trigger"] == "push"


def test_validator_carries_trigger_through():
    # Arrange — the validator must preserve `trigger` for the push handler.
    from scitex_cards._hooks import event_validate

    event = build_push_event(
        repo="owner/repo",
        branch="feat/some-card",
        commit_sha="abc123",
        trigger="commit",
    )
    # Act
    normalized = event_validate(event)
    # Assert
    assert normalized["trigger"] == "commit"


# === wire-contract: built event passes the real consumer validator =========


def test_built_event_passes_consumer_validator():
    """The event build_push_event produces must satisfy the real
    scitex_cards._hooks.event_validate -- proves the two halves agree on the
    wire shape (no mocks; calls the actual validator)."""
    # Arrange
    from scitex_cards._hooks import event_validate

    event = build_push_event(
        repo="owner/repo",
        branch="feat/tcfb-p3-git-to-card",
        commit_sha="abc123def456",
        author="agent",
        message="a commit message",
    )
    # Act
    normalized = event_validate(event)
    # Assert
    assert normalized["card_ids"] == ["tcfb-p3-git-to-card"]


# EOF
