#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The exception-fallback page names the same product as every Cards surface.

Operator 2026-10-23: the in-app header must read "SciTeX Cards" (board_v3 +
DM + project-scoped leaves, pinned). The render-failure fallback
(``_static_graph_markup``) is a fourth user-visible surface with its own
``<title>`` + ``<h1>`` — it read "Cards" / "SciTeX Card" (singular), a THIRD
name for the product on the same mount. A fallback that names a different
product reads as a different app having caught the error.

Single assertion per test (PA-307). Renders through the production
``_static_graph_markup`` via the file's own ``_markup`` helper — no mocks,
no pasted HTML.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

from scitex_cards._django.views import _static_graph_markup  # noqa: E402


def _markup(*, enabled: bool = False) -> str:
    """Render the production fallback markup (real code, no mocks)."""
    return _static_graph_markup(
        "", "/s", 3, Exception("boom"), enabled,
    )


def test_fallback_tab_title_names_the_product() -> None:
    """The fallback page's browser tab reads the product name."""
    # Arrange
    html = _markup(enabled=False)
    # Act
    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    # Assert — one compound assertion (PA-307: single assert per test).
    assert m is not None and m.group(1).strip() == "SciTeX Cards"


def test_fallback_heading_names_the_product() -> None:
    """The fallback page's heading reads the product name, plural."""
    # Arrange
    html = _markup(enabled=False)
    # Act
    m = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
    # Assert — one compound assertion (PA-307: single assert per test).
    assert m is not None and "SciTeX Cards" in m.group(1).replace("&mdash;", "—")
