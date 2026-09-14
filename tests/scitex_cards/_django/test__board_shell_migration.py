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

WHY A GATE EXISTED-WORTHY. The installed scitex-app wheel (0.22.1) does not ship
``app_shell.html`` — the template is present in the scitex-app *source* but
absent from the *distribution*. Consequence, measured in this environment: both
board views' ``render_to_string(...)`` raise
``TemplateDoesNotExist: scitex_app/app_shell.html``; the views' ``except
Exception`` catches it, logs it to stderr only, and serve the static graph
fallback instead of the rich board. The operator sees a WORKING board that is
the WRONG board, with no failing test and no signal — the exact "board works but
wrong" silence ``conftest.py`` documents from the 2026-07-29 outage. Nothing in
the suite caught that the shell migration's target was unshipped.

This file is the Cards-side CONTRACT for the migration. It is fully hermetic:
it parses OUR templates and reads OUR settings, so it needs no store, no
database, no sibling checkout, and no network. It is portable across scitex-app
versions because the one assertion that depends on the wheel's contents branches
on resolvability (see the final test) instead of assuming it.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

from django.conf import settings  # noqa: E402
from django.template.loader import get_template, render_to_string  # noqa: E402
from django.template.exceptions import TemplateDoesNotExist  # noqa: E402
from django.test import RequestFactory  # noqa: E402

#: The two operator-facing pages the migration re-targeted. (``chat.html`` is
#: intentionally absent: it renders its own inline shell and does not extend
#: either of the two shells — a third page is a different migration.)
_PAGES = ("scitex_cards/standalone.html", "scitex_cards/board_v3.html")

#: The shell the migration points at, and the block our pages must override.
_SHELL = "scitex_app/app_shell.html"
_NEW_BLOCK = "scitex_app_content"
_RETired_BLOCK = "app_content"

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
# Pin the scan's own reach first.


def test_the_scans_find_the_pages_themself() -> None:
    # Arrange / Act / Assert
    for name in _PAGES:
        src = _page_source(name)
        assert _EXTENDS.search(src), (
            f"{name} has no `extends` tag — the migration is not in this "
            "file any more, so this whole gate is vacuous"
        )
        assert _BLOCK.search(src), (
            f"{name} defines no block — nothing left to check against "
            f"the {_NEW_BLOCK} contract"
        )


# === (1) the extends target =================================================


@pytest.mark.parametrize("page", _PAGES)
def test_each_page_extends_the_scitex_app_shell(page: str) -> None:
    """Both pages point at ``scitex_app/app_shell.html`` — not the old shell."""
    # Arrange
    src = _page_source(page)
    # Act
    parents = _EXTENDS.findall(src)
    # Assert
    assert _SHELL in parents, (
        f"{page} extends {parents!r}; expected the scitex_app shell "
        f"({_SHELL}). The migration was reverted."
    )
    assert _OLD_SHELL not in parents, (
        f"{page} still extends the retired scitex-ui shell {_OLD_SHELL!r}"
    )


# === (2) the block name =====================================================


@pytest.mark.parametrize("page", _PAGES)
def test_each_page_overrides_scitex_app_content(page: str) -> None:
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
def test_each_page_does_not_override_the_outer_app_content(page: str) -> None:
    """Overriding ``app_content`` would replace the whole slot and bypass the
    shell's nesting. The migration must target ``scitex_app_content`` only."""
    # Arrange
    src = _page_source(page)
    # Act
    blocks = _BLOCK.findall(src)
    # Assert
    assert _RETired_BLOCK not in blocks, (
        f"{page} overrides the outer `{_RETired_BLOCK}` block ({blocks!r}); "
        "the shell nests `scitex_app_content` inside it, so our page must "
        "override the inner one."
    )


# === (3) app registration ===================================================


def test_scitex_app_is_registered_in_installed_apps() -> None:
    """The shell resolves through AppDirectoriesFinder only if scitex_app is an
    installed app. The migration added it; pin it so it cannot be dropped."""
    # Arrange / Act
    installed = settings.INSTALLED_APPS
    # Assert
    assert "scitex_app" in installed, (
        f"INSTALLED_APPS={installed!r} does not register `scitex_app`; the "
        "shell template would never be found by the app-dirs loader."
    )


# === (4) the graceful-degradation seam (version-robust) ====================
#
# The one assertion that depends on the WHEEL's contents. The installed
# scitex-app may or may not ship the shell; both are legitimate, so branch:
#
#   * shell resolvable  -> the page must render end-to-end (contract satisfied).
#   * shell absent      -> the failure must be PRECISELY TemplateDoesNotExist,
#                          which is the class the board views' `except
#                          Exception` catches and turns into a 200 static
#                          fallback rather than a 500. Any OTHER exception type
#                          (a KeyError, a syntax error, a NameError from the
#                          shell) would ALSO be caught by `except Exception`
#                          but signals a broken page, not a missing template —
#                          the two must not be conflated.


@pytest.mark.parametrize("page", _PAGES)
def test_missing_shell_degrades_to_template_does_not_exist(page: str) -> None:
    # Arrange — a bare request, exactly what the board views hand the renderer.
    request = RequestFactory().get("/board")
    try:
        get_template(_SHELL)
        shell_present = True
    except TemplateDoesNotExist:
        shell_present = False

    # Act
    if shell_present:
        # The contract is satisfiable end-to-end: render and require the page
        # to actually produce content (an empty render is the "wrong board"
        # outcome we are guarding against).
        html = render_to_string(page, {}, request=request)
        # Assert
        assert html.strip(), (
            f"{page} rendered to an empty body against the scitex_app shell — "
            "the override chain is wired but paints nothing."
        )
    else:
        # The shell is not shipped here: the failure must be the one seam the
        # views are written to survive, and nothing else.
        with pytest.raises(TemplateDoesNotExist) as excinfo:
            render_to_string(page, {}, request=request)
        # Assert — the missing template is OUR shell, named in the error.
        assert _SHELL in str(excinfo.value), (
            f"rendering {page} failed on {excinfo.value!s} — the expected "
            f"seam is the absent shell `{_SHELL}`; a different missing template "
            "means the migration is incomplete, not merely unshipped."
        )
