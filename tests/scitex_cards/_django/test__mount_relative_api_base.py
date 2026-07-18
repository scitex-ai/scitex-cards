#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mount-relative API base — the board must work under ANY mount prefix.

Root cause (hub card hub-cards-board-data-404): the board/chat frontends
fetched site-root absolute URLs ("/graph", "/update", "/dm/threads", ...).
Standalone (app mounted at "/") that worked; mounted by a host project under
a prefix (scitex-hub uses ``path("apps/cards/", include(...))``) every fetch
went to the HOST's site root and 404'd — the chrome rendered, the data never
arrived.

The fix injects a server-derived ``api_base`` ("/" standalone, "/<prefix>/"
mounted — see ``views._api_base``) into board_v3.html and chat.html as
``window.SCITEX_CARDS_API_BASE``, and every API fetch joins onto it. These
tests pin both mounts end to end: the standalone RequestFactory path AND a
real prefixed ``include()`` driven through the Django test client.
"""

from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("django")

from django.test import Client, RequestFactory  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from django.urls import clear_url_caches, include, path  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal}\n"
)

#: The exact prefix scitex-hub mounts the board under; any prefix would do —
#: the assertion is that the injected base EQUALS the mount, not this value.
_MOUNT_PREFIX = "apps/cards/"

_HOST_URLCONF_NAME = "_test_host_urlconf_apps_cards"


@pytest.fixture
def store(tmp_path):
    """Write a real tmp task store and reset the board cache around the test."""
    path_ = tmp_path / "tasks.yaml"
    path_.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path_)
    _reset_cache()


@pytest.fixture
def mounted_client():
    """Django test client with the board ``include()``d under a prefix.

    Builds a throwaway host urlconf module (the shape scitex-hub uses) and
    points ROOT_URLCONF at it for the test, so requests resolve through a
    real prefixed include — namespace, resolver_match and all.
    """
    host = types.ModuleType(_HOST_URLCONF_NAME)
    host.urlpatterns = [
        path(_MOUNT_PREFIX, include("scitex_cards._django.urls")),
    ]
    sys.modules[_HOST_URLCONF_NAME] = host
    with override_settings(ROOT_URLCONF=_HOST_URLCONF_NAME):
        clear_url_caches()
        yield Client()
    clear_url_caches()
    sys.modules.pop(_HOST_URLCONF_NAME, None)


# --- server-side helper -----------------------------------------------------


def test_api_base_helper_returns_root_when_standalone():
    # Arrange — RequestFactory has no resolver_match, so the helper falls
    # through to the bare "board" name the standalone ROOT_URLCONF resolves.
    request = RequestFactory().get("/")
    # Act
    base = views._api_base(request)
    # Assert
    assert base == "/"


# --- standalone injection ----------------------------------------------------


def test_board_v3_page_injects_root_api_base_when_standalone(store):
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert 'window.SCITEX_CARDS_API_BASE = "/"' in body


def test_chat_page_injects_root_api_base_when_standalone():
    # Arrange
    request = RequestFactory().get("/chat")
    # Act
    body = views.chat_page(request).content.decode("utf-8")
    # Assert
    assert 'window.SCITEX_CARDS_API_BASE = "/"' in body


# --- mounted-prefix simulation (the scitex-hub shape) -------------------------


def test_board_v3_page_injects_prefixed_api_base_when_mounted(
    mounted_client, store
):
    # Arrange
    url = f"/{_MOUNT_PREFIX}?store={store}"
    # Act
    body = mounted_client.get(url).content.decode("utf-8")
    # Assert — the injected base IS the mount prefix, derived (not hardcoded).
    assert f'window.SCITEX_CARDS_API_BASE = "/{_MOUNT_PREFIX}"' in body


def test_chat_page_injects_prefixed_api_base_when_mounted(mounted_client):
    # Arrange
    url = f"/{_MOUNT_PREFIX}chat"
    # Act
    body = mounted_client.get(url).content.decode("utf-8")
    # Assert
    assert f'window.SCITEX_CARDS_API_BASE = "/{_MOUNT_PREFIX}"' in body


def test_graph_endpoint_reachable_under_mounted_prefix(mounted_client, store):
    # Arrange — the exact request the fixed frontend now issues when mounted.
    url = f"/{_MOUNT_PREFIX}graph?store={store}"
    # Act
    response = mounted_client.get(url)
    # Assert
    assert response.status_code == 200


# --- no site-root absolute fetch left anywhere --------------------------------


def _read(rel):
    """Read one file under the installed _django app dir."""
    from pathlib import Path

    import scitex_cards

    return (
        Path(scitex_cards.__file__).parent / "_django" / rel
    ).read_text(encoding="utf-8")


def test_board_template_contains_no_site_root_fetch():
    # Arrange
    text = _read("templates/scitex_cards/board_v3.html")
    # Act
    hits = 'fetch("/' in text
    # Assert — every fetch goes through apiUrl(); a bare fetch("/... would
    # 404 under any non-root mount (the original bug).
    assert not hits


def test_chat_js_contains_no_site_root_fetch():
    # Arrange
    text = _read("static/scitex_cards/chat/chat.js")
    # Act
    hits = ('fetch("/' in text) or ('getJSON("/' in text)
    # Assert
    assert not hits


def test_timeline_js_contains_no_site_root_fetch():
    # Arrange
    text = _read("static/scitex_cards/board_v3/timeline.js")
    # Act
    hits = 'fetch("/' in text
    # Assert
    assert not hits


# EOF
