#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board's internal chrome must not reach a public viewer (L478 / L479).

COMPASS §13 L478 ("hide internal/operator terminology from public users") and
L479 ("hide schema/ADR/debug-facing information from normal UI") — corroborated
by §21 L643 ("current UI visibly exposes internal/operator concepts"). The
2026-09-10 evidence audit (card compass-audit-scitex-cards-20260910) found the
board's server-rendered footer names ``operator schema (ADR-0007)``,
``GUI→code (ADR-0006)`` and ``v3 LIVE`` to EVERY viewer, and renders the
deployment's store path into ``<code id="store-path">`` — information a
stranger has no business seeing.

THE FIX THIS TEST PINS. The footer is gated on ``settings.DEBUG`` via the
``show_internal_chrome`` context var ``board_v3_page`` injects. That is the
board's ONE existing operator-vs-external split: a loopback board runs
``DEBUG=true`` (the operator keeps the line), and ``settings.py`` FORCES
``DEBUG`` off the moment ``SCITEX_CARDS_PUBLIC_HOST`` is set (so any publicly
reachable deployment hides it). It is the same switch
``_store_errors.public_summary`` already uses, so "who is looking" has one
answer, not two.

WHY THE VIEW, NOT THE TEMPLATE. The contract that matters is
``settings.DEBUG -> what the page shows``, which only the view expresses
(template-level tests would have to guess the view's mapping and would miss a
regression where the view stops passing the flag at all). ``override_settings``
re-reads ``settings.DEBUG`` at call time, so both directions are asserted
against the real view.

HERMETIC. Renders the real ``board_v3.html`` against the installed shell and
touches no store: the footer is pure template + one context var, and the
board's ``try/except`` never 500s — the two assertions below are about WHAT
is in the page, not that it rendered at all (the status check guards the
latter, so a fallback page cannot masquerade as a pass).
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.test import RequestFactory, override_settings  # noqa: E402

from scitex_cards._django import views  # noqa: E402

# The internal-only strings the footer carries. Each must be ABSENT from a
# public (DEBUG=false) render and PRESENT from an operator (DEBUG=true) render.
# Pinned to the exact literals so a rewording that drops one of them is caught.
_INTERNAL_FOOTER_MARKERS = (
    "operator schema",  # L479: schema-facing, names the operator
    "ADR-0007",  # L479: an ADR reference in the normal UI
    "GUI→code",  # L479: internal provenance
    'id="store-path"',  # L479: the deployment's store path, rendered
)


def _render_board(debug: bool) -> str:
    """Render the real board view with ``settings.DEBUG`` set, return the HTML."""
    # Arrange — a bare request, exactly what the board view hands the renderer.
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=debug):
        resp = views.board_v3_page(request)
    # Assert — the page rendered (200) and is the real board, not the static
    # fallback: a fallback would let every marker assertion below pass vacuously.
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", "replace")
    assert "SciTeX Card" in body, "rendered the fallback, not the board page"
    return body


def test_public_board_hides_internal_footer() -> None:
    """A public deployment (DEBUG forced off) shows none of the internal chrome.

    Asserts the FOOTER IS GONE, not merely empty: the gate wraps the whole
    ``<div class="foot">`` (not just its contents) because the div's CSS carries
    padding + border-top + background — an empty footer div would still paint a
    stray horizontal bar at the bottom of the board.
    """
    # Arrange
    body = _render_board(debug=False)
    # Assert — the div itself is absent (no stray bar), and so is its chrome.
    assert 'class="foot"' not in body, (
        "public board still renders an empty footer bar — the gate wraps only "
        "the contents, not the .foot div; its CSS (padding/border-top/ "
        "background) paints a stray horizontal stripe at the bottom"
    )
    for marker in _INTERNAL_FOOTER_MARKERS:
        assert marker not in body, (
            f"public board still exposes internal chrome {marker!r} — "
            "compass §13 L478/L479, §21 L643: a stranger sees the store path / "
            "ADR refs / schema line"
        )


def test_operator_board_keeps_internal_footer() -> None:
    """The loopback board (DEBUG on) keeps the line — the operator still gets it."""
    # Arrange
    body = _render_board(debug=True)
    # Assert
    for marker in _INTERNAL_FOOTER_MARKERS:
        assert marker in body, (
            f"operator board lost internal footer marker {marker!r} — the "
            "gate hid it from the operator it was meant for"
        )


def test_store_path_js_is_null_guarded() -> None:
    """The footer's JS must not throw when the element is absent in public mode.

    ``board_v3.html``'s load handler writes ``document.getElementById(
    "store-path").textContent = ...``. When the footer is gated off the element
    does not exist, so an unguarded write throws inside the render path and
    would kill the whole board for a public viewer. Pin the guard so a future
    edit that removes the ``{% if %}`` (restoring the element) is safe, and one
    that removes the JS guard is caught.
    """
    # Arrange / Act
    body = _render_board(debug=False)
    # Assert — the guard is present and the unguarded direct write is gone.
    assert 'const _storePathEl = document.getElementById("store-path")' in body, (
        "store-path write is not null-guarded — a public board throws in the "
        "load handler when the footer element is absent"
    )
    assert 'document.getElementById("store-path").textContent' not in body, (
        "unguarded store-path write is back — it throws when the footer is "
        "hidden in public mode"
    )
