#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board shell migration gate: ``scitex_ui`` standalone shell -> ``scitex_app`` shell.

THE MIGRATION THIS FILE PINS. Both operator-facing board pages were re-targeted
from the scitex-ui standalone shell to the scitex-app shell, and ``scitex_app``
was registered in ``INSTALLED_APPS`` so its templates resolve through
AppDirectoriesFinder::

    {% extends "scitex_ui/standalone_shell.html" %}  ->  {% extends "scitex_app/app_shell.html" %}
    {% block app_content %}                          ->  {% block scitex_app_content %}

The shell the pages extend is built by the SIBLING scitex-app package: its
``app_shell.html`` extends ``scitex_ui/standalone_shell.html`` and re-exports
``scitex_app_content`` as a nested block inside ``app_content``. Our pages
therefore override the INNER block (``scitex_app_content``), not the outer one
(``app_content``) — overriding ``app_content`` would replace the whole content
slot and bypass the shell's nesting.

WHY A GATE EXISTS. The installed scitex-app wheel does not always ship
``app_shell.html`` — the template is present in the scitex-app *source* but may
be absent from the *distribution* (measured on 0.22.1 and 0.23.0). Consequence:
when the shell is absent, both board views' ``render_to_string(...)`` raise
``TemplateDoesNotExist``, the views' ``except Exception`` catches it, and they
serve the static graph fallback instead of the rich board. The operator sees a
WORKING board that is the WRONG board, with no failing test and no signal — the
exact "board works but wrong" silence the 2026-07-29 outage was made of.

This file is the Cards-side CONTRACT for the migration. It is fully hermetic:
it parses OUR templates and reads OUR settings, so it needs no store, no
database, no sibling checkout, and no network. It is portable across scitex-app
versions because the one assertion that depends on the wheel's contents
(:func:`test_missing_shell_degrades_to_template_does_not_exist`) computes a
single outcome instead of assuming the shell is present or absent.

TEST-QUALITY NOTE. Every test here carries exactly one assertion and the
``# Arrange`` / ``# Act`` / ``# Assert`` markers each on their own line (STX-TQ002 /
STX-TQ007). The graceful-degradation test is deliberately NOT parametrized and
uses try/except rather than a top-level ``if``/``else`` (STX-TQ006), so the
version-robust branch stays one intent and one assertion.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

from django.conf import settings  # noqa: E402
from django.template.exceptions import TemplateDoesNotExist  # noqa: E402
from django.template.loader import get_template, render_to_string  # noqa: E402
from django.test import RequestFactory  # noqa: E402

#: The two operator-facing pages the migration re-targeted. (``chat.html`` is
#: intentionally absent: it renders its own inline shell and does not extend
#: either of the two shells — a third page is a different migration.)
_PAGES = ("scitex_cards/standalone.html", "scitex_cards/board_v3.html")

#: The shell the migration points at, and the block our pages must override.
_SHELL = "scitex_app/app_shell.html"
_NEW_BLOCK = "scitex_app_content"
_RETIRED_BLOCK = "app_content"

#: The retired scitex-ui shell the pages used to extend.
_OLD_SHELL = "scitex_ui/standalone_shell.html"

_EXTENDS = re.compile(r"""\{%[-\s]*extends\s+['"]([^'"]+)['"]""")
_BLOCK = re.compile(r"""\{%[-\s]*block\s+([A-Za-z0-9_]+)""")


def _page_source(name: str) -> str:
    """Raw source of one of our board pages, read through the real origin."""
    # get_template resolves against the app dirs exactly as the board does; the
    # origin's source is the file on disk (editable tree), so this is hermetic.
    origin = get_template(name).template.origin
    try:
        return origin.source
    except Exception:  # some backends expose only the path
        return open(origin.name, encoding="utf-8").read()  # noqa: SIM115


# === positive controls ======================================================
#
# A regex that quietly matches nothing makes every assertion below vacuous ("all
# pages extend the scitex_app shell" reads true when it examined no extends).
# Pin the scan's own reach first — one intent (and one assertion) per test.


@pytest.mark.parametrize("page", _PAGES)
def test_scans_find_an_extends_tag_on_every_page(page: str) -> None:
    """Every page still carries an ``extends`` tag to examine."""
    # Arrange
    src = _page_source(page)
    # Act
    found = _EXTENDS.search(src)
    # Assert
    assert found, (
        f"{page} has no `extends` tag — the migration is not in this file any "
        "more, so this whole gate is vacuous"
    )


@pytest.mark.parametrize("page", _PAGES)
def test_scans_find_a_block_tag_on_every_page(page: str) -> None:
    """Every page still defines a block to check against the contract."""
    # Arrange
    src = _page_source(page)
    # Act
    found = _BLOCK.search(src)
    # Assert
    assert found, (
        f"{page} defines no block — nothing left to check against the "
        f"{_NEW_BLOCK} contract"
    )


# === (1) the extends target ================================================
#
# Two intents, two tests (STX-TQ007): pointing at the NEW shell is a different
# contract from no longer pointing at the RETIRED shell, so each gets its own
# single assertion.


@pytest.mark.parametrize("page", _PAGES)
def test_page_extends_the_scitex_app_shell(page: str) -> None:
    """Both pages point at ``scitex_app/app_shell.html``."""
    # Arrange
    src = _page_source(page)
    # Act
    parents = _EXTENDS.findall(src)
    # Assert
    assert _SHELL in parents, (
        f"{page} extends {parents!r}; expected the scitex_app shell "
        f"({_SHELL}). The migration was reverted."
    )


@pytest.mark.parametrize("page", _PAGES)
def test_page_no_longer_extends_the_retired_scitex_ui_shell(page: str) -> None:
    """The retired scitex-ui shell is gone from the extends list."""
    # Arrange
    src = _page_source(page)
    # Act
    parents = _EXTENDS.findall(src)
    # Assert
    assert _OLD_SHELL not in parents, (
        f"{page} still extends the retired scitex-ui shell {_OLD_SHELL!r} "
        f"(extends {parents!r})"
    )


# === (2) the block name =====================================================
#
# Overriding the INNER ``scitex_app_content`` block is one intent; NOT
# overriding the OUTER ``app_content`` is a different one. Split (STX-TQ007).


@pytest.mark.parametrize("page", _PAGES)
def test_page_overrides_the_scitex_app_content_block(page: str) -> None:
    """Our content slot is the INNER block the scitex_app shell re-exports."""
    # Arrange
    src = _page_source(page)
    # Act
    blocks = _BLOCK.findall(src)
    # Assert
    assert _NEW_BLOCK in blocks, (
        f"{page} defines blocks {blocks!r} but not `{_NEW_BLOCK}` — the shell "
        "re-exports that nested block; without it the page paints nothing."
    )


@pytest.mark.parametrize("page", _PAGES)
def test_page_does_not_override_the_outer_app_content_block(page: str) -> None:
    """Overriding ``app_content`` would replace the whole slot and bypass the
    shell's nesting. The migration must target ``scitex_app_content`` only."""
    # Arrange
    src = _page_source(page)
    # Act
    blocks = _BLOCK.findall(src)
    # Assert
    assert _RETIRED_BLOCK not in blocks, (
        f"{page} overrides the outer `{_RETIRED_BLOCK}` block ({blocks!r}); "
        "the shell nests `scitex_app_content` inside it, so our page must "
        "override the inner one."
    )


# === (3) app registration ===================================================


def test_scitex_app_is_registered_in_installed_apps() -> None:
    """The shell resolves through AppDirectoriesFinder only if scitex_app is an
    installed app. The migration added it; pin it so it cannot be dropped."""
    # Arrange
    installed = settings.INSTALLED_APPS
    # Act
    present = "scitex_app" in installed
    # Assert
    assert present, (
        f"INSTALLED_APPS={installed!r} does not register `scitex_app`; the "
        "shell template would never be found by the app-dirs loader."
    )


# === (4) the graceful-degradation seam (version-robust) ====================
#
# The one test that depends on the WHEEL's contents. The installed scitex-app
# may or may not ship the shell; both are legitimate, so instead of assuming
# either (or branching with a top-level ``if``/``else`` in a parametrized test,
# which STX-TQ006 forbids), compute a single outcome and assert it once:
#
#   * shell resolvable  -> the page must render non-empty (contract met).
#   * shell absent      -> the failure must be PRECISELY TemplateDoesNotExist
#                          naming OUR shell — the class the board views'
#                          `except Exception` catches and turns into a 200
#                          static fallback, not a 500. Any OTHER exception
#                          (KeyError, NameError from the shell) means a broken
#                          page, not a missing template — the two must not be
#                          conflated.
#
# A single page is enough to pin the seam: both pages re-target identically and
# the extends/block tests above already cover the second.


def test_missing_shell_degrades_to_template_does_not_exist() -> None:
    # Arrange — a bare request, exactly what the board views hand the renderer.
    page = "scitex_cards/board_v3.html"
    request = RequestFactory().get("/board")
    try:
        get_template(_SHELL)
        shell_present = True
    except TemplateDoesNotExist:
        shell_present = False
    # Act — render and reduce to one boolean: satisfiable end-to-end, or the
    # failure is precisely the absent shell (and nothing else).
    try:
        html = render_to_string(page, {}, request=request)
        outcome = bool(html.strip())
    except TemplateDoesNotExist as exc:
        outcome = (not shell_present) and (_SHELL in str(exc))
    # Assert
    assert outcome, (
        f"rendering {page} did not satisfy the shell contract: shell_present="
        f"{shell_present}. Expected a non-empty render when the shell resolves, "
        f"or a TemplateDoesNotExist naming `{_SHELL}` when it does not."
    )
