#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django AppConfig for the scitex-cards board.

Inherits ``scitex_app._django.ScitexAppConfig`` when scitex-app is installed
(so the board can register as a scitex-hub module), and falls back to Django's
plain ``AppConfig`` otherwise — keeping ``pip install scitex-cards[all]``
functional without a hard scitex-app dependency.
"""

try:
    from scitex_app._django import ScitexAppConfig
except ImportError:  # scitex-app not installed — standalone still works
    from django.apps import AppConfig as ScitexAppConfig


class ScitexCardsConfig(ScitexAppConfig):
    name = "scitex_cards._django"
    label = "scitex_cards_board"
    verbose_name = "SciTeX Card Board"


# EOF
