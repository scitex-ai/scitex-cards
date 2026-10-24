#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The project-scoped board pages name the same PRODUCT as the shared header.

Operator 2026-10-23: the in-app header must read "SciTeX Cards" (board_v3
and DM were fixed in 8d62a509). The project-scoped siblings —
``project_board.html`` / ``project_card.html`` — still carry a bare ``Cards``
``<h1 data-stx-leaf-title>``, which reads as a different product on the same
mount. The leaf-title h1 is the product band on those pages, so it names the
product the same way the shared header does.

Mocks-free: read the source files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_TEMPLATES = Path(views.__file__).resolve().parent / "templates" / "scitex_cards"


def _leaf_title(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    for line in source.splitlines():
        if "data-stx-leaf-title" in line:
            return line.strip()
    raise AssertionError(f"{path.name} has no data-stx-leaf-title element")


@pytest.mark.parametrize("template", ["project_board.html", "project_card.html"])
def test_project_scoped_leaf_title_names_the_product(template: str) -> None:
    """The project-scoped leaf band reads "SciTeX Cards", not bare "Cards"."""
    # Arrange
    line = _leaf_title(_TEMPLATES / template)
    # Act — the h1 text between the tags is the product name the page shows
    inner = line.split(">", 1)[1].rsplit("<", 1)[0].strip()
    # Assert — one compound assertion (PA-307: single assert per test)
    assert inner == "SciTeX Cards", (
        f"{template} leaf title reads {inner!r} — the band must name "
        f"the product 'SciTeX Cards' like the shared header"
    )
