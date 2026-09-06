#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the PR-merge recording mandate in the canonical scitex-cards
skill (fleet-adoption multiplier #3, lead a2a `0cdca03a`).

The skill is propagated into every agent via `scitex-cards skills
propagate` (PR #161). If these load-bearing phrases drift, every
agent's read-on-boot mandate weakens — so we pin them to a test that
runs in every CI cycle. No mocks (STX-NM / PA-306); just reads the
shipped files.

WHAT MOVED, 2026-08-16. SKILL.md had grown past its size budget by inlining
this leaf wholesale, and was cut back to an index. The mandate survived in
short form, but two assertions here were keyed to SKILL.md text that
legitimately belongs in the leaf: the 完了率 rationale, which the leaf states
twice. So the split is now explicit — SKILL.md, the read-on-boot artifact,
must carry the RULE, the exact recording command, and the fact that the flag
is not optional; the leaf carries the rationale, the no-PR path, the bulk
catch-up verb and the provenance. A SKILL.md assertion also pins the LINK, so
the leaf cannot become unreachable from boot.

The header assertion was keyed to one exact spelling (`MANDATE — record
evidence at PR-merge`). That fails on a harmless reword and passes on a
rewrite that keeps the header and guts the paragraph, so it now matches the
RULE case-insensitively instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Resolve the skill file via the package install path so the test
# follows the actual file shipped in the wheel, not a stale checkout
# copy.
from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

SKILL_DIR = _skills_root()
SKILL_MD = SKILL_DIR / "SKILL.md"
LEAF_MD = SKILL_DIR / "60_pr-merge-recording-mandate.md"


# === SKILL.md contains the load-bearing mandate section ====================


def test_skill_md_carries_the_pr_merge_mandate():
    # Arrange — the RULE, matched case-insensitively, not a header spelling.
    # Act
    text = SKILL_MD.read_text(encoding="utf-8").lower()
    # Assert
    assert "record evidence at pr-merge" in text


def test_skill_md_mandate_specifies_done_with_pr_url():
    # Arrange
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # boot can grep it directly.
    # Assert
    assert "scitex-cards done <card-id> --pr-url" in text


def test_skill_md_mandate_states_pr_url_is_required():
    # Arrange
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert
    assert "REQUIRED, not optional" in text


def test_skill_md_links_the_long_form():
    # Arrange — the index may state the rule in short form ONLY if it points
    # at the full one; otherwise the rationale is unreachable from boot.
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert
    assert LEAF_MD.name in text


def test_leaf_doc_cites_completion_rationale():
    # Arrange — without the 完了率 metric the mandate reads as bureaucracy,
    # and an agent that does not know WHY will drop it under time pressure.
    # Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # Assert
    assert "完了率" in text


# === Leaf doc 60_pr-merge-recording-mandate.md exists =======================


def test_leaf_doc_exists():
    # Arrange
    # Act
    # Assert
    assert LEAF_MD.exists()


def test_leaf_doc_documents_no_pr_path():
    # Arrange
    # Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # Assert
    assert "no-PR completion" in text


def test_leaf_doc_documents_bulk_catchup_verb():
    # Arrange
    # Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # Assert
    assert "scitex-cards sync-github" in text


def test_leaf_doc_cites_lead_provenance():
    # Arrange
    # Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # the next maintainer / restart.
    # Assert
    assert "0cdca03a" in text
