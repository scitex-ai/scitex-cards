#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A closed drawer must leave the TAB ORDER, not merely leave the screen.

FOUND 2026-07-28 by scitex-ui while harvesting this into a shared component.
Both defects below were live on the surface the operator is migrating onto, and
both are invisible to a screenshot — which is why they survived review.

DEFECT 1: the panel is hidden with ``transform: translateX(-105%)``. A transform
moves PIXELS. It does not remove an element from the tab order or the
accessibility tree. At phone width with the drawer shut, Tab from the header put
focus into the invisible agent list with no visible focus ring, and the next
Enter opened a thread the operator could not see. From their side the page
jumped on its own.

DEFECT 2: the drawer and its scrim were two bare ``classList.toggle("open")``
calls. ``toggle()`` flips whatever is there, so any path clearing one without
the other desynced them — and ``close()`` IS called from the thread-open
handler. Once diverged, one tap put them in opposite states, and the bad half is
a scrim with no drawer: greyed screen, nothing dismisses it, menu button behind
it, force-reload the only exit.

These are behavioural properties of a JS module, so they are asserted against
the SOURCE rather than a rendered page — this repo has no browser in CI, and a
test that silently could not observe the behaviour would be worse than none.

WHAT A SOURCE SCAN CANNOT SEE, added 2026-07-29 after defect 3.
Every assertion below stayed green for the whole life of defect 3, which blanked
the DESKTOP agent sidebar by applying these same two properties to a panel that
was not a drawer at the time. The lines the scan looks for were present and
correct; what was wrong was the CONDITION they ran under, and a text scan cannot
see a missing condition — only a missing string. So the scan is a floor, not a
ceiling. New drawer behaviour belongs in
``tests/scitex_cards/_django/static/scitex_cards/chat/test_chat_drawer.py``,
which RUNS the shipped module under node and reads back the properties it wrote.
Do not extend this file instead of that one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Resolved from the REPO TREE, not from ``views.__file__``.
# Via the installed package this reads whatever static files that install
# happens to carry, which is not necessarily the source under review: on this
# developer box the installed tree holds 2 of the 11 chat modules and no
# chat_drawer.js at all, so the scan errored on a file it was never really
# guarding. Every other JS test here resolves from the repo tree for that
# reason; this one now agrees with them.
_DRAWER = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_drawer.js"
)


@pytest.fixture
def source() -> str:
    """The drawer module as shipped."""
    return _DRAWER.read_text(encoding="utf-8")


@pytest.fixture
def code(source: str) -> str:
    """Source with block comments stripped.

    The comments deliberately QUOTE the defects they document, so a naive scan
    would match the explanation of the bug and call it the bug — the same
    vacuous shape as a guard reading its own docstring.
    """
    return re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)


def test_the_closed_panel_is_marked_inert(code: str):
    """`inert` is what removes it from the keyboard and assistive tech."""
    # Arrange
    needle = "panel.inert"

    # Act
    present = needle in code

    # Assert
    assert present


def test_the_closed_panel_is_visibility_hidden(code: str):
    """`visibility` is what removes it from the POINTER.

    Separate from inert on purpose: neither implies the other, so asserting
    only one would leave half the hole open and still look covered.
    """
    # Arrange
    needle = "panel.style.visibility"

    # Act
    present = needle in code

    # Assert
    assert present


def test_the_drawer_state_is_rendered_at_load(code: str):
    """The closed state must be established before the first interaction.

    That window — page loaded, menu never touched, operator presses Tab — is
    exactly when the original bug bit.
    """
    # Arrange
    lines = [ln.strip() for ln in code.splitlines()]

    # Act
    renders_at_mount = "render();" in lines

    # Assert
    assert renders_at_mount


def test_neither_element_is_toggled_without_state(code: str):
    """A bare two-argument-less toggle is what allowed the desync.

    `classList.toggle(x)` flips whatever is there; `classList.toggle(x, bool)`
    is derived from state and cannot diverge. This pins the fix rather than the
    symptom, so reintroducing the pattern fails here rather than in the
    operator's browser.
    """
    # Arrange
    bare_toggle = re.compile(r"classList\.toggle\(\s*\"open\"\s*\)")

    # Act
    offenders = bare_toggle.findall(code)

    # Assert
    assert offenders == []


def test_escape_closes_the_drawer(code: str):
    """Keyboard-openable but pointer-only-closable is a trap."""
    # Arrange
    needle = '"Escape"'

    # Act
    present = needle in code

    # Assert
    assert present


# EOF
