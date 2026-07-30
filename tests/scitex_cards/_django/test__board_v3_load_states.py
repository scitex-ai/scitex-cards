#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_v3 load-failure UI STATES (hub-mount integration contract).

A failed /graph is not always an error — on the hub it can be a STATE the
board must render helpfully instead of the red banner:

- **signed-out**: the hub's auth middleware answers 401 with
  ``{"error": "signed-out", "login_url": …}`` → the board renders a
  signed-out panel linking ``login_url``.
- **no-active-project**: the hub's tenancy middleware
  (``TodoBoardTenancyMiddleware``) answers 404 with an ``{"error", "hint"}``
  payload whose error starts with "No active project" → the board renders a
  "No active project" panel linking the hint.
- **anything else**: the loud red error stays, now carrying the server's
  ``error`` field and the HTTP status (the body is READ before giving up).

Source-pin tests over the template JS, following the repo's
``test__board_v3_signatures.py`` convention (the fetch logic is browser-side;
the pins keep the contract from silently regressing in a squash-merge).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402

_TEMPLATE = (
    Path(views.__file__).resolve().parent
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)


@pytest.fixture
def board_source():
    """The template source (the load states live in its inline JS)."""
    return _TEMPLATE.read_text(encoding="utf-8")


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
        'throw new Error(`HTTP ${r.status}` + (err ? ` — ${escapeHtml(err)}` : ""))'
        in source
    )
    # Assert
    assert escalates_with_error_field


# EOF
