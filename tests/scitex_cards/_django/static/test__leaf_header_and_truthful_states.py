#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The leaf must say what it is, and a load must say which kind of wait it is.

OPERATOR REPORT that these pin (Hub mount, 2026-09-16/17): the Cards leaf was
identifiable only as "Board / SciTeX Card — dependency graph" — a VIEW name in
the app's slot — with no version and no project picker anywhere, and all three
non-happy states answered with one bare sentence ("Loading task graph…" /
"No graph."), which makes a hung first fetch, an API failure and an empty store
look identical. The operator's words: no leaf title/version/project picker,
ambiguous "loading...".

These are TEXT level guards over the shipped artifacts and the server context,
not a browser test: the SPA is built by vite into
``static/scitex_cards/assets/index.{js,css}`` and THAT is what the operator's
browser gets, so the artifact is the thing worth asserting on. Anything the
build drops is invisible to a source-only guard, and anything asserted only
here — but never built — would pass while the page stayed broken. Hence both:
source markers where the wiring lives, and the built bundle where the failure
would actually have to reproduce.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import RequestFactory

from scitex_cards import __version__
from scitex_cards._django import views

_APP = Path(views.__file__).parent
_TEMPLATES = _APP / "templates" / "scitex_cards"
_ASSETS = _APP / "static" / "scitex_cards" / "assets"
_FRONTEND_SRC = _APP / "frontend" / "src"

_STANDALONE = _TEMPLATES / "standalone.html"
_BUNDLE = _ASSETS / "index.js"
_STYLES = _ASSETS / "index.css"
_LEAF_SOURCE = _FRONTEND_SRC / "LeafHeader.tsx"

#: Identifiers that the leaf band and the named states must contribute to the
#: built bundle. Names, not prose: a test that pins sentences would fail on
#: every wording fix and teach the next author to weaken it.
_LEAF_MARKERS = (
    "stx-cards-leaf__title",
    "stx-cards-leaf__ver",
    "stx-cards-leaf__project",
    "stx-cards-leaf__timings",
    "stx-cards-status__retry",
)

#: The ambiguous strings the operator read. Their ABSENCE is the fix, so it is
#: asserted directly — a re-introduced "No graph." would otherwise pass every
#: other test in this file.
_RETIRED_STRINGS = ("Loading task graph", '"No graph."')


def test_the_served_shell_carries_the_server_version_on_the_mount_element():
    """The version the band prints is the SERVER's, and it reaches the page."""
    # Arrange
    request = RequestFactory().get("/")
    # Act
    html = views.board_page(request).content.decode("utf-8")
    # Assert
    assert re.search(
        r'id="app-mount"[^>]*data-app-version="[^"]+"', html
    ), (
        "the served board page no longer puts a version on "
        "#app-mount[data-app-version]; src/LeafHeader.tsx reads the version ONLY "
        "from that attribute, so the leaf band would render no version at all"
    )


def test_one_reader_answers_the_cards_version():
    """board-v3, DM and the SPA shell must not own separate version reads."""
    # Arrange
    reader = views._cards_version
    # Act
    served = reader()
    # Assert
    assert served == __version__


def test_the_shipped_bundle_renders_the_leaf_band_and_named_states():
    """Every marker the band and the states contribute must SURVIVE the build."""
    # Arrange
    bundle = _BUNDLE.read_text(encoding="utf-8", errors="ignore")
    # Act
    missing = [marker for marker in _LEAF_MARKERS if marker not in bundle]
    # Assert
    assert not missing, (
        f"the built bundle ({_BUNDLE.name}) is missing {missing} — the source "
        "may carry them while the artifact the operator loads does not"
    )


def test_the_ambiguous_state_strings_are_gone():
    """A retired ambiguity must not come back one sentence at a time."""
    # Arrange
    bundle = _BUNDLE.read_text(encoding="utf-8", errors="ignore")
    # Act
    resurrected = [text for text in _RETIRED_STRINGS if text in bundle]
    # Assert
    assert not resurrected, (
        f"the built bundle resurrects {resurrected} — all three states must stay "
        "distinguishable (loading is not empty is not failed)"
    )


def test_the_phone_width_rules_are_shipped_in_the_stylesheet():
    """390px is the reported width; the fix must exist in the BUILT CSS."""
    # Arrange
    styles = _STYLES.read_text(encoding="utf-8", errors="ignore")
    # Act
    has_phone_rule = "@media (max-width: 430px)" in styles
    has_leaf_wrap = re.search(r"@media \(max-width: 700px\)", styles) is not None
    # Assert
    assert has_phone_rule and has_leaf_wrap and ".stx-cards-toolbar__search" in styles


def test_the_project_picker_is_bound_to_the_boards_own_filter_state():
    """A picker that is not wired to the filter is a decoration, not a control."""
    # Arrange
    source = _LEAF_SOURCE.read_text(encoding="utf-8")
    # Act
    wired = "setRepos" in source and "activeRepos" in source
    # Assert
    assert wired, (
        "LeafHeader no longer reads activeRepos / calls setRepos, so the project "
        "picker would render and change nothing — the filter would silently stop "
        "following it"
    )


def test_the_identity_band_is_not_conditional_on_a_successful_load():
    """Which app / which version must be readable WHEN the board is broken."""
    # Arrange
    source = (_FRONTEND_SRC / "CardsBoard.tsx").read_text(encoding="utf-8")
    # Act
    bands_in_state_branches = source.count("{band}")
    # Assert
    assert bands_in_state_branches == 3, (
        f"expected the leaf band in all three non-happy states (loading / "
        f"failed / no answer), found {bands_in_state_branches} — the identity "
        "of the app is exactly what a reader needs while something is wrong"
    )


# EOF
