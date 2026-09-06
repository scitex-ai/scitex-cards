#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the board AppConfig (real import, no mocks)."""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from scitex_cards._django.apps import ScitexCardsConfig  # noqa: E402


def test_app_config_uses_board_label():
    # Arrange
    config = ScitexCardsConfig
    # Act
    label = config.label
    # Assert
    assert label == "scitex_cards_board"


def test_app_config_points_at_django_package():
    # Arrange
    config = ScitexCardsConfig
    # Act
    name = config.name
    # Assert
    assert name == "scitex_cards._django"


# EOF
