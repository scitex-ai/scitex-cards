#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board ctx-menu status fallback must equal the Python status enum.

``board_v3.html`` derives the right-click menu statuses from the server's
``status_colors`` SSOT, with a hardcoded fallback that only fires for a
payload predating ``status_colors``. The derivation was fixed for the
pending abolition (2026-07-10) but the fallback literal still lists 6 of
the 7 live statuses — ``cancelled`` has no slot, so on a stale payload
the menu cannot set the GitHub "closed as not planned" state while every
sibling surface (mermaid STATUS_STYLE, org export, shipped bundle, CSS)
already carries it. Same missing-cancelled-slot class as those fixes.

WHY THIS TEST IS IN PYTHON. Same reason as the bundle/TS/JS enum
mirrors: the node suite is executed by nothing
(card `gui-node-tests-not-run-by-ci-20260815`). This scans the shipped
template bytes in the language CI already runs.

SCOPE NOTE. The ``.filter((s) => s !== "pending")`` on the derived path
is a legacy-payload guard (an old cached payload can still carry the
abolished value), not a card-status enum — it is pinned PRESENT, while
``pending`` is pinned ABSENT from the fallback literal itself.
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


@pytest.fixture(scope="module")
def template_source() -> str:
    return _BOARD_V3_HTML.read_text(encoding="utf-8")


def _ctx_fallback_members(source: str) -> set[str]:
    """Members of the ``statuses.push(...)`` ctx-menu fallback literal."""
    matches = re.findall(r"statuses\.push\(([^)]*)\)", source)
    lits = [
        set(re.findall(r"['\"]([a-z_]+)['\"]", m))
        for m in matches
        if len(re.findall(r"['\"]([a-z_]+)['\"]", m)) >= 3
    ]
    assert len(lits) == 1, (
        f"expected exactly 1 ctx-menu fallback statuses.push literal, found "
        f"{len(lits)} — the template shape moved under this pin"
    )
    return lits[0]


def test_the_ctx_menu_fallback_mirrors_the_python_status_enum(template_source):
    # Arrange + Act
    members = _ctx_fallback_members(template_source)
    # Assert — as SETS (display order is free; membership is not).
    assert members == set(VALID_STATUSES), (
        f"ctx-menu fallback offers {sorted(members)}, not the live "
        f"{sorted(VALID_STATUSES)}"
    )


def test_the_ctx_menu_fallback_carries_no_abolished_status(template_source):
    # Pinned by set so a future re-introduction of `pending` into the
    # fallback fails loudly rather than only shifting the mirror.
    # Arrange + Act
    members = _ctx_fallback_members(template_source)
    # Assert
    assert members.isdisjoint(ABOLISHED_STATUSES), (
        f"the ctx-menu fallback offers abolished status(es) "
        f"{sorted(members & set(ABOLISHED_STATUSES))}"
    )


def test_the_derived_path_still_filters_a_legacy_pending(template_source):
    # The derivation reads live payloads; a stale cached payload can still
    # carry the abolished value, so the guard must stay.
    # Arrange + Act + Assert
    assert '.filter((s) => s !== "pending")' in template_source, (
        "the ctx-menu derived path lost its legacy-pending guard — an old "
        "payload would offer a status the store refuses on write"
    )


def test_the_ctx_menu_derives_from_the_server_ssot(template_source):
    # The fallback must stay a fallback: the primary path derives from
    # status_colors, never a second hardcoded list.
    # Arrange + Act + Assert
    assert "status_colors" in template_source, (
        "the ctx-menu no longer derives statuses from the status_colors "
        "SSOT — the fallback pin above would be pinning the only path"
    )
