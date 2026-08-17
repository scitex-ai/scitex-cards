#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_v3 load-failure UI STATES (hub-mount integration contract).

A failed /graph is not always an error — on the hub it can be a STATE the
board must render helpfully instead of the red banner:

- **signed-out**: the hub's auth middleware answers 401 with
  ``{"error": "signed-out", "login_url": …}`` → the board renders a
  signed-out panel linking ``login_url``.
- **no-active-project**: the hub's tenancy middleware
  (``CardsBoardTenancyMiddleware``) answers 404 with an ``{"error", "hint"}``
  payload whose error starts with "No active project" → the board renders a
  "No active project" panel linking the hint.
- **anything else**: the loud red error stays, now carrying the server's
  ``error`` field and the HTTP status (the body is READ before giving up).

Source-pin tests over the board's CLIENT source, following the repo's
``test__board_v3_signatures.py`` convention (the fetch logic is browser-side;
the pins keep the contract from silently regressing in a squash-merge).

WHY THE FIXTURE READS TWO FILES. These states used to live in the template's
inline JS and the pins read only ``board_v3.html``. On 2026-08-17 the four
renderers moved into ``board_v3/boardStates.js`` and all five location-bound
pins went red at once — correctly: they asserted WHERE the contract lived, not
THAT it held. The contract is a property of the board's client code, so the
fixture is the concatenation of the template and the extracted module. A pin
that matches in either file is satisfied, because either file is shipped to the
same browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_DJANGO_DIR = Path(views.__file__).resolve().parent

_TEMPLATE = _DJANGO_DIR / "templates" / "scitex_cards" / "board_v3.html"

_BOARD_STATES_JS = (
    _DJANGO_DIR / "static" / "scitex_cards" / "board_v3" / "boardStates.js"
)


@pytest.fixture
def board_source():
    """The board's client source: the template plus the extracted states module."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in (_TEMPLATE, _BOARD_STATES_JS)
    )


def test_load_graph_reads_body_before_giving_up(board_source):
    """The non-OK branch must read the response JSON before any throw —
    the server's named reason drives which panel renders."""
    # Arrange
    source = board_source
    # Act
    non_ok_branch_reads_body = "await _readJsonBody(r)" in source
    # Assert
    assert non_ok_branch_reads_body


def test_signed_out_state_matches_the_hub_401_payload(board_source):
    """The signed-out panel keys on the exact middleware contract
    (error === "signed-out" plus a login_url to link)."""
    # Arrange
    source = board_source
    # Act
    pins_contract = 'payload.error === "signed-out" && payload.login_url' in source
    # Assert
    assert pins_contract


def test_signed_out_state_links_login_url(board_source):
    """The panel links the server-provided login_url (escaped)."""
    # Arrange
    source = board_source
    # Act
    links_login = "escapeHtml(payload.login_url)" in source
    # Assert
    assert links_login


def test_no_active_project_state_matches_the_hub_404_payload(board_source):
    """The no-active-project panel keys on the tenancy middleware's existing
    404 shape: an {"error", "hint"} payload, error starting with
    "No active project"."""
    # Arrange
    source = board_source
    # Act
    pins_contract = "/^no active project/i.test(err)" in source
    # Assert
    assert pins_contract


def test_no_active_project_state_links_hint(board_source):
    """The panel links the server-provided hint (escaped)."""
    # Arrange
    source = board_source
    # Act
    links_hint = "escapeHtml(payload.hint)" in source
    # Assert
    assert links_hint


def test_unrecognized_failure_keeps_loud_error_with_server_error_field(
    board_source,
):
    """Anything unrecognized escalates loudly WITH the server's error field
    appended to the HTTP status (never a silent or bare failure)."""
    # Arrange
    source = board_source
    # Act
    escalates_with_error_field = (
        'throw new Error(`HTTP ${r.status}` + (err ? ` — ${err}` : ""))' in source
    )
    # Assert
    assert escalates_with_error_field


def test_the_thrown_message_is_not_escaped_on_the_way_into_the_error(
    board_source,
):
    """An Error message is a STRING, not markup, so the throw must NOT escape.

    This pin previously required ``escapeHtml(err)`` INSIDE the throw. That was
    pinning a defect: the render layer escapes too, so the text was escaped
    twice and `&#39;` appeared literally in the panel — observed in a browser
    2026-08-17, not deduced. Escape where you interpolate, exactly once. The
    pin is inverted rather than deleted so the double-escape cannot come back
    unnoticed.
    """
    # Arrange
    throw_line = [
        line for line in board_source.splitlines() if "throw new Error(`HTTP" in line
    ]
    # Act
    escapes_on_the_way_in = any("escapeHtml" in line for line in throw_line)
    # Assert
    assert not escapes_on_the_way_in


def test_the_render_layer_escapes_the_message_exactly_once(board_source):
    """The escape belongs at the point the string becomes HTML — and there
    the FULL text is escaped, so nothing reaches the DOM unescaped."""
    # Arrange
    source = board_source
    # Act
    escapes_at_interpolation = "<pre>${escapeHtml(full)}</pre>" in source
    # Assert
    assert escapes_at_interpolation


def test_the_error_lead_strips_the_http_status_prefix(board_source):
    """The headline must be the server's own first sentence, never a status code.

    MEASURED, NOT GUESSED: the first version cut at the first em-dash, which on
    the real message "HTTP 500 — Cannot read the task store: …" produced the
    headline "HTTP 500" — the uninformative lead the whole panel exists to
    remove. Found by rendering it and reading the screen.
    """
    # Arrange
    source = board_source
    # Act
    strips_transport_prefix = (
        r'm.replace(/^HTTP\s+\d{3}\s*(?:—|-|:)?\s*/i, "")' in source
    )
    # Assert
    assert strips_transport_prefix


# EOF
