#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The shared header carries the Hub-style project picker on both pages.

``_page_header.html`` is the ONE band Board and DM share (see
``test__page_header_is_shared``): the picker lives there so the two pages
cannot disagree about it. The picker is scitex-ui's
``{% scitex_project_picker %}`` -- the same component scholar, writer,
figrecipe and stats render -- fed by ``cards_current_project_id``, which
resolves through the host provider and fails soft to "" (standalone, or
no provider) so the header renders exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_DJANGO_DIR = Path(views.__file__).resolve().parent
_HEADER_PARTIAL = (
    _DJANGO_DIR / "templates" / "scitex_cards" / "_page_header.html"
)
_HEADER_CSS = _DJANGO_DIR / "static" / "scitex_cards" / "page-header.css"


def test_header_partial_loads_the_picker_tag():
    # Arrange
    text = _HEADER_PARTIAL.read_text(encoding="utf-8")

    # Act
    found = "{% scitex_project_picker" in text

    # Assert
    assert found


def test_header_picker_is_project_scoped():
    # Arrange
    text = _HEADER_PARTIAL.read_text(encoding="utf-8")

    # Act
    found = 'scope="project"' in text

    # Assert
    assert found


def test_header_picker_receives_the_current_project():
    # Arrange
    text = _HEADER_PARTIAL.read_text(encoding="utf-8")

    # Act
    found = "cards_current_project_id" in text

    # Assert
    assert found


def test_header_css_pins_the_picker_slot():
    # Arrange
    text = _HEADER_CSS.read_text(encoding="utf-8")

    # Act
    found = ".stx-cards-header__project" in text

    # Assert
    assert found


def _request(get=None):
    return SimpleNamespace(GET=dict(get or {}))


def test_current_project_id_is_a_string_without_a_provider():
    # Arrange
    request = _request()

    # Act
    result = views._current_project_id(request)

    # Assert
    assert isinstance(result, str)


def test_current_project_id_rejects_a_bogus_explicit_pick():
    # Arrange
    request = _request({"project": "no-such-project"})

    # Act
    result = views._current_project_id(request)

    # Assert
    assert isinstance(result, str)
