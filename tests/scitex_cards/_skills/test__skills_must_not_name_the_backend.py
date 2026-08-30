#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The boot-read skills must not tell agents WHICH engine the store is on."""

from __future__ import annotations



import re

import pytest

from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

#: Prose asserting the store IS a particular engine. The engine name must sit
#: directly in front of the noun ("PostgreSQL database", "Postgres store") --
#: that adjacency is what makes it a claim about our store rather than a mention.
_BACKEND_CLAIM = re.compile(
    r"\b(PostgreSQL|Postgres|MySQL|DuckDB)\s+(database|store|task store|db)\b",
    re.IGNORECASE,
)

#: The sample-output form, which is worse than prose: it shows a reader the
#: literal value they are told to go and read for themselves.
_BACKEND_LITERAL = re.compile(r"backend:\s*(postgres|postgresql|mysql|duckdb)\b", re.IGNORECASE)


def _skill_files():
    return sorted(_skills_root().glob("*.md"))


def _violations(text: str) -> list[str]:
    return [m.group(0) for m in _BACKEND_CLAIM.finditer(text)] + [
        m.group(0) for m in _BACKEND_LITERAL.finditer(text)
    ]


def test_there_are_skill_files_to_scan():
    # Arrange -- a passing scan over an empty set proves nothing, so the
    # corpus itself is asserted before any result from it is trusted.
    root = _skills_root()
    # Act
    files = sorted(root.glob("*.md"))
    # Assert
    assert len(files) > 5, f"expected the skill corpus, found {len(files)} file(s)"


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.name)
def test_skill_does_not_assert_which_engine_the_store_is(path):
    # Arrange
    text = path.read_text(encoding="utf-8")
    # Act
    found = _violations(text)
    # Assert
    assert not found, (
        f"{path.name} tells agents the store is a specific engine: {found}. "
        "The deployment picks the backend via $SCITEX_CARDS_DB -- say so and "
        "point at `scitex-cards resolve-store` instead of naming one."
    )


def test_the_detector_actually_fires():
    # Arrange -- the exact sentences that shipped, so this control degrades
    # only if the real-world form changes rather than if the wording drifts.
    planted = (
        "A canonical PostgreSQL task store with pluggable adapters.\n"
        "# -> prints {resolved: <path>, backend: postgres, ...}\n"
    )
    # Act
    found = _violations(planted)
    # Assert
    assert len(found) >= 2, f"detector missed a known-bad sample: {found}"


def test_the_detector_does_not_fire_on_honest_history():
    # Arrange -- naming an engine to explain a retired tier is correct writing
    # and must stay allowed, or the rule pushes true sentences into vagueness.
    honest = (
        "That zero-config tier was deleted 2026-08-13 (operator ruling: MySQL "
        "is abolished fleet-wide) after it was found reachable in production.\n"
    )
    # Act
    found = _violations(honest)
    # Assert
    assert found == [], f"rule punishes an accurate historical note: {found}"


# EOF
