#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django AppConfig for the scitex-cards board.

SDK inheritance and identity are explicit so the host can certify the
plugin from source before importing application code. The SDK is a required
dependency; a broken installation must fail instead of dropping host hooks.
"""

from scitex_sdk import app as _sdk_app


class ScitexCardsConfig(_sdk_app.embed.ScitexAppConfig):
    name = "scitex_cards._django"
    label = "scitex_cards_board"
    verbose_name = "SciTeX Card Board"


# EOF
