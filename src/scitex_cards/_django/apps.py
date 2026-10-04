#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django AppConfig for the scitex-cards board.

Inherits ``scitex_sdk.app._django.ScitexAppConfig`` when scitex-sdk is
installed (so the board can register as a scitex-hub module), and falls back
to Django's plain ``AppConfig`` otherwise — keeping ``pip install
scitex-cards`` functional without a hard scitex-sdk dependency (the
``scitex-sdk`` distribution itself remains declared, so the fallback is
only a degraded-install guard, not a supported configuration).
"""

try:
    from scitex_sdk.app._django import ScitexAppConfig
except ImportError:  # scitex-sdk not installed — standalone still works
    from django.apps import AppConfig as ScitexAppConfig


class ScitexCardsConfig(ScitexAppConfig):
    name = "scitex_cards._django"
    label = "scitex_cards_board"
    verbose_name = "SciTeX Card Board"


# EOF
