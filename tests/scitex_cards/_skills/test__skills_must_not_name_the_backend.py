#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The boot-read skills must not tell agents WHICH engine the store is on.

WHAT WENT WRONG, measured 2026-08-16. Ten skill files described the canonical
store as "a the retired engine database". `resolve_store` on the live fleet reported
``backend: postgresql``. So every agent read a false sentence at boot, and the
`42_for-consuming-agents.md` sample output literally printed ``backend: the retired engine``
as the expected result of the very command whose job is to answer that question.

WHY THE FIX IS "NAME NO ENGINE" RATHER THAN "SAY POSTGRESQL". Swapping the word
would have re-armed the same trap with a fresher value: the backend is chosen by
the deployment via ``$SCITEX_CARDS_DB``, so ANY engine named in prose is a guess
about someone else's environment. The MCP server's own instructions already say
it -- "Do NOT assume a backend or a default path -- the deployment decides both".
The skills now say the same thing and point at `resolve-store` instead.

WHAT THIS PINS, and what it deliberately does NOT. It fails only on prose that
asserts the store IS a named engine ("the the retired engine database", "a PostgreSQL
store"). It stays silent on history and on migration notes that must be free to
name an engine to be intelligible -- a doc explaining that the zero-config the retired engine
tier was deleted needs the word "the retired engine" to say anything at all. Banning the
token outright would force those sentences into vagueness and would be a rule
that punishes accuracy.

THE POSITIVE CONTROL BELOW IS NOT DECORATION. A scanner that finds nothing
reports the same green as a scanner whose regex never matches anything, and this
whole family of bugs -- a check that cannot fail -- is why the false claims
survived ten files and several releases. `test_the_detector_actually_fires`
plants a violation and requires it to be caught.

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import re

import pytest

from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

#: Prose asserting the store IS a particular engine. The engine name must sit
#: directly in front of the noun ("the retired engine database", "Postgres store") -- that
#: adjacency is what makes it a claim about our store rather than a mention.
_BACKEND_CLAIM = re.compile(
    r"\b(SQLite|PostgreSQL|Postgres|MySQL|DuckDB)\s+(database|store|task store|db)\b",
    re.IGNORECASE,
)

#: The sample-output form, which is worse than prose: it shows a reader the
#: literal value they are told to go and read for themselves.
_BACKEND_LITERAL = re.compile(r"backend:\s*(sqlite|postgres|postgresql|mysql)\b", re.IGNORECASE)


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
        "A canonical SQLite task store with pluggable adapters.\n"
        "# -> prints {resolved: <path>, backend: sqlite, ...}\n"
    )
    # Act
    found = _violations(planted)
    # Assert
    assert len(found) >= 2, f"detector missed a known-bad sample: {found}"


def test_the_detector_does_not_fire_on_honest_history():
    # Arrange -- naming an engine to explain a retired tier is correct writing
    # and must stay allowed, or the rule pushes true sentences into vagueness.
    honest = (
        "That zero-config tier was deleted 2026-08-13 (operator ruling: SQLite "
        "is abolished fleet-wide) after it was found reachable in production.\n"
    )
    # Act
    found = _violations(honest)
    # Assert
    assert found == [], f"rule punishes an accurate historical note: {found}"


# EOF
