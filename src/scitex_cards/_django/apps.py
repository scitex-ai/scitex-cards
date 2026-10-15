#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django AppConfig for the scitex-cards board.

Inherits ``scitex_sdk.app.apps.ScitexAppConfig`` when scitex-sdk is installed
(so the board can register as a SciTeX leaf module), and falls back to Django's
plain ``AppConfig`` otherwise — keeping ``pip install scitex-cards[all]``
functional without a hard scitex-sdk dependency.
"""

try:
    # Import the MODULE, not the class: Django auto-detects the default
    # AppConfig by scanning this namespace for AppConfig subclasses, and any
    # name bound to the base class object would count as a second candidate
    # next to ``ScitexCardsConfig`` (the SDK base declares ``default = True``,
    # which the subclass inherits — "more than one default AppConfig"). The
    # base is therefore referenced only as ``_sdk_apps.ScitexAppConfig`` at
    # class-definition time and never bound to a name here, leaving exactly
    # one candidate, which Django then uses.
    from scitex_sdk.app import apps as _sdk_apps

    class ScitexCardsConfig(_sdk_apps.ScitexAppConfig):
        name = "scitex_cards._django"
        label = "scitex_cards_board"
        verbose_name = "SciTeX Card Board"
except ImportError:  # scitex-sdk not installed — standalone still works
    import django.apps as _django_apps

    class ScitexCardsConfig(_django_apps.AppConfig):
        name = "scitex_cards._django"
        label = "scitex_cards_board"
        verbose_name = "SciTeX Card Board"


# EOF
