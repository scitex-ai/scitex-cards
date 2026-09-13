#!/usr/bin/env python3
"""The dedicated Cards DM surface obeys the shared-shell and user-scope contract."""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402


class _AuthenticatedUser:
    is_authenticated = True

    def get_username(self) -> str:
        return "alice"


def _render_dm() -> str:
    request = RequestFactory().get("/apps/cards/dm")
    request.user = _AuthenticatedUser()
    return views.chat_page(request).content.decode("utf-8")


def test_dm_page_uses_the_released_scitex_ui_workspace_shell() -> None:
    # Arrange
    body = _render_dm()
    # Act
    # Assert
    assert '<body class="workspace-page' in body


def test_dm_page_mounts_content_in_the_shared_module_pane() -> None:
    # Arrange
    body = _render_dm()
    # Act
    # Assert
    assert 'class="ws-module-pane"' in body


def test_dm_page_disables_all_project_workspace_panes() -> None:
    # Arrange
    body = _render_dm()
    # Act
    # Assert
    assert body.count("ws-pane-unused") == 3


def test_dm_page_has_no_project_switcher() -> None:
    # Arrange
    body = _render_dm()
    # Act
    # Assert
    assert "project-switcher" not in body.lower()


def test_dm_page_exposes_authenticated_user_scope() -> None:
    # Arrange
    body = _render_dm()
    # Act
    # Assert
    assert 'data-user-scope="alice"' in body


def test_dm_page_exposes_mount_aware_api_root() -> None:
    # Arrange
    body = _render_dm()
    # Act
    # Assert
    assert 'data-api-base="/apps/cards/"' in body


# EOF
