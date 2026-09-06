#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concrete delivery channels shipped inside scitex-cards.

Slice 1 ships exactly one: :class:`scitex_cards._delivery._channels.log.\
LogChannel` — a creds-free stdlib-logging sink registered under the
``scitex_cards.delivery_channels`` entry-point group. Later slices add real
transports (telegram, email) as additional channels in this package.
"""

# EOF
