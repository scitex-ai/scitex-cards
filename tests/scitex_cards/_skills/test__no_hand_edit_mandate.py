#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pin the NEVER-hand-edit mandate in the canonical scitex-cards skill.

The 2026-06-13 corruption episode (the then-canonical file-based store
truncated mid-string at line ~2784) traced to a hand-edit bypassing the
API. Lead a2a `02c8a4ae` directed the rule into the canonical skill so
every fleet agent reads it on boot (via the #161 `skills propagate`
mechanism). If a future refactor drops the phrase, every agent silently
loses the read-on-boot guard — pin it here so CI catches the drift.

The store has since moved to SQLite (`$SCITEX_CARDS_DB`); the assertion
below pins the CURRENT canonical identity, not the retired YAML path.

WHY THIS NOW PINS TWO FILES. It pinned four strings, all in SKILL.md, and
one of them was an exact section-header spelling. When SKILL.md was cut from
318 lines to an index (2026-08-16) — because it had grown past its size
budget by inlining a whole leaf — the mandate SURVIVED in short form while
two of the pinned strings moved out. The guard failed, which is exactly what
it is for; but a guard keyed to a header spelling would also have failed on a
pure rewording that changed nothing, and passed on a rewrite that kept the
header and gutted the paragraph.

So it now pins the CONTRACT in the place each half belongs: SKILL.md is the
read-on-boot artifact and must carry the RULE, and the write-protocol leaf
carries the LONG FORM — the emergency-repair exception and the PR-#166
audit-trail citation. Dropping either half still fails.

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

#: The read-on-boot artifact every agent loads.
SKILL_MD = _skills_root() / "SKILL.md"

#: Where the mandate's long form lives, linked from SKILL.md.
WRITE_PROTOCOL_MD = _skills_root() / "30_two-tier-conventions-and-write-protocol.md"


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _write_protocol_text() -> str:
    return WRITE_PROTOCOL_MD.read_text(encoding="utf-8")


def test_skill_md_forbids_hand_editing_the_store():
    # Arrange — matched case-insensitively on the RULE, not on a header
    # spelling, so a reword cannot fail this and a deletion cannot pass it.
    text = _skill_text().lower()
    # Act
    forbids = "hand-edit" in text
    # Assert
    assert forbids, "SKILL.md no longer forbids hand-editing the store"


def test_skill_md_states_the_rule_as_a_prohibition_not_a_preference():
    # Arrange — "avoid hand-editing" would satisfy the check above while
    # softening a mandate into advice, so the prohibition is pinned too.
    text = _skill_text().lower()
    # Act
    absolute = "never hand-edit" in text or "no manual sql" in text
    # Assert
    assert absolute, "SKILL.md softened the hand-edit mandate into a preference"


def test_skill_md_names_the_canonical_store_identity():
    # Arrange
    # Act
    text = _skill_text()
    # Assert
    assert "$SCITEX_CARDS_DB" in text


def test_skill_md_links_the_long_form():
    # Arrange — the index may carry the rule in short form ONLY if it points
    # at the full one; otherwise the detail is unreachable from boot.
    # Act
    text = _skill_text()
    # Assert
    assert WRITE_PROTOCOL_MD.name in text


def test_write_protocol_documents_the_emergency_repair_exception():
    # Arrange — an already-broken store cannot be repaired through the API,
    # so the one case where a hand-edit IS justified must stay written down.
    # Act
    text = _write_protocol_text()
    # Assert
    assert "Emergency repair exception" in text


def test_write_protocol_cites_the_pr_166_safety_net():
    # Arrange — keeps the audit trail for the writer-side fix discoverable.
    # Act
    text = _write_protocol_text()
    # Assert
    assert "PR-#166" in text or "PR #166" in text
