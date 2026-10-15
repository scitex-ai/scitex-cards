#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board_v3 template's inline filter enum + stale comment must match Python.

``board_v3.html`` carries TWO shipped status surfaces the sibling pins do
not cover:

* the ``f-status`` filter array (``["goal", "done", ...]``) — the ctx-menu
  fallback is pinned by
  ``test__board_v3_ctx_fallback_enums_match_python.py``, the checked-in SPA
  bundle + CSS by ``test__board_bundle_enums_match_python.py``, the TS/JS
  sources by the ``board_ts/js_enums`` twins — but this inline literal had
  no pin, so a hand edit re-introducing the abolished ``pending`` (or
  dropping a live status the store writes) would ship a filter that can
  never match, silently;
* the Stale Review layout comment — it still described the section as
  surfacing "PENDING cards" after GET /stale was repointed to the
  ``deferred`` backlog (``pending`` was abolished 2026-07-10), the same
  dead-prose class as the runnable/reconcile CLI-help repoints.

WHY THIS TEST IS IN PYTHON. Same reason as the sibling enum mirrors: the
node suite is executed by nothing
(card `gui-node-tests-not-run-by-ci-20260815`). This scans the shipped
template bytes in the language CI already runs.

SCOPE NOTE. Only string-array literals whose members are ALL card-status
vocabulary count as enum mirrors (the bundle test's rule): the blocker's
``["compute", "dependency", ...]`` array and the ``["goal", t.goal]``
display pair are deliberately NOT matched. The ctx-menu derived path's
``.filter((s) => s !== "pending")`` is a legacy-payload guard pinned
PRESENT by the ctx-fallback twin, not an enum. The stale-comment pin is
scoped to the Stale Review layout block so the legitimate ``user-pending``
blocker value and the abolition notes elsewhere cannot trip it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_BOARD_V3_HTML = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)

_STATUS_VOCAB = frozenset(VALID_STATUSES) | frozenset(ABOLISHED_STATUSES)


@pytest.fixture(scope="module")
def template_source() -> str:
    return _BOARD_V3_HTML.read_text(encoding="utf-8")


def _status_arrays(source: str) -> list[set[str]]:
    """Every ``["a","b",...]`` literal with >=3 members, all status vocab."""
    lits = re.findall(
        r"\[\s*(?:['\"][a-z_]+['\"]\s*(?:,\s*['\"][a-z_]+['\"]\s*)*)\]",
        source,
    )
    out = []
    for lit in lits:
        members = tuple(re.findall(r"['\"]([a-z_]+)['\"]", lit))
        if len(members) >= 3 and set(members) <= _STATUS_VOCAB:
            out.append(set(members))
    return out


def _stale_layout_block(source: str) -> str:
    """The Stale Review layout comment block, between its header and code."""
    start = source.find("=== Stale Review layout")
    assert start != -1, (
        "the Stale Review layout header moved under this pin"
    )
    end = source.find("let _STALE_CACHE", start)
    assert end != -1 and end > start, (
        "the Stale Review layout block shape moved under this pin"
    )
    return source[start:end]


def test_the_filter_status_array_mirrors_the_python_status_enum(
    template_source,
):
    # Arrange + Act
    found = _status_arrays(template_source)
    # Assert — non-vacuity first: the template shape must still carry a
    # recognisable status-enum literal, else this pin evaporates.
    assert len(found) >= 1, (
        "no status-enum literal found in board_v3.html — the template "
        "shape moved under this pin"
    )
    for members in found:
        assert members == set(VALID_STATUSES), (
            f"board_v3.html filter offers {sorted(members)}, not the live "
            f"{sorted(VALID_STATUSES)}"
        )


def test_the_filter_status_array_carries_no_abolished_status(template_source):
    # Arrange + Act
    found = _status_arrays(template_source)
    assert found, "no status-enum literal found in board_v3.html"
    # Assert
    for members in found:
        assert members.isdisjoint(ABOLISHED_STATUSES), (
            f"board_v3.html filter offers abolished status(es) "
            f"{sorted(members & set(ABOLISHED_STATUSES))}"
        )


def test_the_stale_review_comment_names_the_deferred_backlog(
    template_source,
):
    # The section pulls GET /stale, which serves the deferred backlog —
    # naming PENDING here re-teaches the dead filter the server just shed.
    # Arrange + Act
    block = _stale_layout_block(template_source)
    # Assert
    assert "PENDING" not in block, (
        "the Stale Review layout comment still names PENDING cards while "
        "GET /stale serves the deferred backlog"
    )
    assert "deferred" in block, (
        "the Stale Review layout comment names no deferred backlog — "
        "the pin would pass on an emptied block"
    )
