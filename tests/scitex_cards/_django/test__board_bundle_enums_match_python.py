#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The shipped board artifacts must equal the Python enums they mirror.

Two surfaces were fixed for the pending abolition (2026-07-10) but left
UNPINNED, while their siblings are pinned:

    board_v3/searchQuery.js  pinned by test__board_js_enums_match_python.py
    frontend/src/*.ts(x)     pinned by test__board_ts_enums_match_python.py
    static/.../assets/index.js  THE SHIPPED SPA BUNDLE — unpinned until now
    board_v3/02-card.css + 09-timeline.css — unpinned until now

The bundle is a CHECKED-IN artifact, not a build step CI runs: the operator's
React board serves ``assets/index.js`` verbatim. A source fix that never
reaches the bundle (or a hand edit to the bundle that never reaches the
source) ships a board whose status dropdown, search qualifiers, progress
chips, and card/timeline styling name a value the store REFUSES on write —
``status:pending`` matched zero cards — or omit one cards really carry
(``cancelled`` rendered with no style rule at all).

WHY THIS TEST IS IN PYTHON. Same reason as the two sibling enum tests: the
node suite is executed by nothing
(card `gui-node-tests-not-run-by-ci-20260815`). A test that does not run is
decoration. This check scans the shipped bytes in the language CI already
runs and fails in the pytest matrix the moment either side moves.

SCOPE NOTE. The bundle also carries ``case"pending":return"pending"`` in its
CI-overall mapper (``oL``) — that is GitHub's check-run vocabulary
(success/failure/pending/unknown), not a card status, and is deliberately
NOT matched: only string-array literals whose members are all card-status
(or all kind) values are treated as enum mirrors.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards._task import ABOLISHED_STATUSES, VALID_KINDS, VALID_STATUSES

_BUNDLE_JS = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "static"
    / "scitex_cards"
    / "assets"
    / "index.js"
)

_CSS_02_CARD = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "02-card.css"
)

_CSS_09_TIMELINE = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
    / "09-timeline.css"
)

_STATUS_VOCAB = frozenset(VALID_STATUSES) | frozenset(ABOLISHED_STATUSES)
_KIND_VOCAB = frozenset(VALID_KINDS)


def _bundle_string_arrays(source: str) -> list[tuple[str, ...]]:
    """Every ``["a","b",...]`` literal in the bundle with >=3 members.

    Accepts single- or double-quoted members so a minifier quote-style
    change moves the pin rather than silently vacating it (the vacuity
    guards below fail LOUD when nothing is found).
    """
    lits = re.findall(
        r"\[(?:['\"][a-z_]+['\"](?:,['\"][a-z_]+['\"])*)\]", source
    )
    out = []
    for lit in lits:
        members = tuple(re.findall(r"['\"]([a-z_]+)['\"]", lit))
        if len(members) >= 3:
            out.append(members)
    return out


@pytest.fixture(scope="module")
def bundle_source() -> str:
    return _BUNDLE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_sources() -> dict[str, str]:
    return {
        _CSS_02_CARD.name: _CSS_02_CARD.read_text(encoding="utf-8"),
        _CSS_09_TIMELINE.name: _CSS_09_TIMELINE.read_text(encoding="utf-8"),
    }


def _status_arrays(source: str) -> list[set[str]]:
    return [set(m) for m in _bundle_string_arrays(source) if set(m) <= _STATUS_VOCAB]


def _kind_arrays(source: str) -> list[set[str]]:
    return [set(m) for m in _bundle_string_arrays(source) if set(m) <= _KIND_VOCAB]


def test_the_shipped_bundle_status_enums_equal_the_python_one(bundle_source):
    # The bundle carries the search enum, the editor STATUSES, the export
    # order, and the progress-chip order as separate literals (display order
    # differs; as SETS each must equal VALID_STATUSES).
    # Arrange + Act
    found = _status_arrays(bundle_source)
    # Assert — non-vacuity first: the minifier must still emit recognisable
    # literals, else this pin evaporates on a build-tool change.
    assert len(found) >= 3, (
        f"expected >=3 status-enum literals in the shipped bundle, found "
        f"{len(found)} — the bundle shape moved under this pin"
    )
    for members in found:
        assert members == set(VALID_STATUSES), (
            f"shipped bundle status literal {sorted(members)} has drifted "
            f"from scitex_cards._task.VALID_STATUSES {sorted(VALID_STATUSES)}"
        )


def test_an_abolished_status_is_not_shipped_in_the_bundle(bundle_source):
    # Pinned by name so a future re-introduction of `pending` into any
    # shipped literal fails loudly rather than only shifting a set.
    # Arrange + Act
    found = _status_arrays(bundle_source)
    assert found, "no status-enum literal found in the shipped bundle"
    # Assert
    for members in found:
        assert members.isdisjoint(ABOLISHED_STATUSES), (
            f"the shipped board offers abolished status(es) "
            f"{sorted(members & set(ABOLISHED_STATUSES))}"
        )


def test_the_shipped_bundle_kind_enum_equals_the_python_one(bundle_source):
    # Arrange + Act
    found = _kind_arrays(bundle_source)
    # Assert
    assert len(found) >= 1, (
        "no kind-enum literal found in the shipped bundle — "
        "the bundle shape moved under this pin"
    )
    for members in found:
        assert members == set(VALID_KINDS), (
            f"shipped bundle kind literal {sorted(members)} has drifted "
            f"from scitex_cards._task.VALID_KINDS {sorted(VALID_KINDS)}"
        )


def test_the_shipped_board_css_names_no_abolished_status(css_sources):
    # A `data-status="pending"` selector (or its fill/stroke vars) styles a
    # value the store refuses — dead CSS that reads as support.
    # Arrange + Act + Assert
    for name, source in css_sources.items():
        assert 'data-status="pending"' not in source, (
            f"{name} still styles the abolished pending status"
        )
        assert "status-fill-pending" not in source, (
            f"{name} still references --status-fill-pending"
        )
        assert "status-stroke-pending" not in source, (
            f"{name} still references --status-stroke-pending"
        )


def test_the_shipped_board_css_covers_the_live_statuses(css_sources):
    # The positive mirror: every live status gets a selector in EACH file,
    # so a newly added status cannot silently render unstyled (the
    # `cancelled`-with-no-rule defect this sweep repaired).
    # Arrange + Act + Assert
    for name, source in css_sources.items():
        covered = set(re.findall(r'data-status="([a-z_]+)"', source))
        assert covered, f"no data-status selectors found in {name}"
        assert covered == set(VALID_STATUSES), (
            f"{name} styles {sorted(covered)}, not the live "
            f"{sorted(VALID_STATUSES)}"
        )
