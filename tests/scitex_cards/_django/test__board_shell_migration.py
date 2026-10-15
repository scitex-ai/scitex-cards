#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board shell migration gate: retired shells -> ``scitex_sdk`` shells.

THE MIGRATION THIS FILE PINS. Both operator-facing board pages were re-targeted
from the retired scitex-app / scitex-ui shells to the scitex-sdk shells, and
``scitex_sdk.app`` / ``scitex_sdk.ui`` were registered in ``INSTALLED_APPS``
so their templates resolve through AppDirectoriesFinder::

    {% extends "scitex_app/app_shell.html" %}  ->  {% extends "scitex_sdk/app/app_shell.html" %}
    {% block scitex_app_content %}              ->  {% block app_content %}

The shell the pages extend is built by the SIBLING scitex-sdk package: its
``app_shell.html`` extends ``scitex_sdk/ui/standalone_shell.html`` and keeps a
nested legacy block inside ``app_content`` for backwards compatibility. Our
pages override the OUTER block (``app_content``) — overriding the legacy inner
block would keep rendering today but re-pin the retired name the migration
removes.

WHY A GATE EXISTS. The installed SDK wheel does not always ship
``app_shell.html`` — the template is present in the SDK *source* but may
be absent from the *distribution*. Consequence:
when the shell is absent, both board views' ``render_to_string(...)`` raise
``TemplateDoesNotExist``, the views' ``except Exception`` catches it, and they
serve the static graph fallback instead of the rich board. The operator sees a
WORKING board that is the WRONG board, with no failing test and no signal — the
exact "board works but wrong" silence the 2026-07-29 outage was made of.

This file is the Cards-side CONTRACT for the migration. It is fully hermetic:
it parses OUR templates and reads OUR settings, so it needs no store, no
database, no sibling checkout, and no network. It is portable across SDK
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
#: intentionally absent: it extends the raw SDK standalone shell directly
#: rather than the app shell — a third page is a different migration.)
_PAGES = ("scitex_cards/standalone.html", "scitex_cards/board_v3.html")

#: The shell the migration points at, and the block our pages must override.
_SHELL = "scitex_sdk/app/app_shell.html"
_NEW_BLOCK = "app_content"
_RETIRED_BLOCK = "scitex_app_content"

#: The retired shells the pages used to extend.
_OLD_SHELLS = ("scitex_app/app_shell.html", "scitex_ui/standalone_shell.html")

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
# pages extend the SDK shell" reads true when it examined no extends).
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
# contract from no longer pointing at a RETIRED shell, so each gets its own
# single assertion.


@pytest.mark.parametrize("page", _PAGES)
def test_page_extends_the_sdk_app_shell(page: str) -> None:
    """Both pages point at ``scitex_sdk/app/app_shell.html``."""
    # Arrange
    src = _page_source(page)
    # Act
    parents = _EXTENDS.findall(src)
    # Assert
    assert _SHELL in parents, (
        f"{page} extends {parents!r}; expected the SDK shell "
        f"({_SHELL}). The migration was reverted."
    )


@pytest.mark.parametrize("page", _PAGES)
def test_page_extends_neither_retired_shell(page: str) -> None:
    """Both retired shells are gone from the extends list."""
    # Arrange
    src = _page_source(page)
    # Act
    parents = _EXTENDS.findall(src)
    # Assert
    assert not any(old in parents for old in _OLD_SHELLS), (
        f"{page} still extends a retired shell {_OLD_SHELLS!r} "
        f"(extends {parents!r})"
    )


# === (2) the block name =====================================================
#
# Overriding the OUTER ``app_content`` block is one intent; NOT
# overriding the retired legacy inner block is a different one. Split (STX-TQ007).


@pytest.mark.parametrize("page", _PAGES)
def test_page_overrides_the_app_content_block(page: str) -> None:
    """Our content slot is the outer block the SDK shell defines."""
    # Arrange
    src = _page_source(page)
    # Act
    blocks = _BLOCK.findall(src)
    # Assert
    assert _NEW_BLOCK in blocks, (
        f"{page} defines blocks {blocks!r} but not `{_NEW_BLOCK}` — the shell "
        "defines that content slot; without it the page paints nothing."
    )


@pytest.mark.parametrize("page", _PAGES)
def test_page_does_not_override_the_retired_inner_block(page: str) -> None:
    """Overriding the legacy inner block would re-pin the retired name.

    The migration must target ``app_content`` only."""
    # Arrange
    src = _page_source(page)
    # Act
    blocks = _BLOCK.findall(src)
    # Assert
    assert _RETIRED_BLOCK not in blocks, (
        f"{page} overrides the retired `{_RETIRED_BLOCK}` block ({blocks!r}); "
        "the SDK keeps it only for backwards compatibility, so our page must "
        "override the outer block."
    )


# === (3) app registration ===================================================


def test_sdk_apps_are_registered_in_installed_apps() -> None:
    """The shells resolve through AppDirectoriesFinder only if both SDK apps are
    installed apps. The migration added them; pin them so neither is dropped."""
    # Arrange
    installed = settings.INSTALLED_APPS
    # Act
    missing = [app for app in ("scitex_sdk.app", "scitex_sdk.ui") if app not in installed]
    # Assert
    assert not missing, (
        f"INSTALLED_APPS={installed!r} does not register {missing!r}; the "
        "shell templates would never be found by the app-dirs loader."
    )


# === (4) the graceful-degradation seam (version-robust) ====================
#
# The one test that depends on the WHEEL's contents. The installed SDK
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
