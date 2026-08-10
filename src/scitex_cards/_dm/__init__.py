#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The direct-message rail: storage, read path, write path, identity.

Grouped out of the package root on 2026-08-10. These eight modules shared a
prefix and a responsibility while sitting flat among 134 siblings, which
PS-108b flags at a threshold of 15 — and which had become a gate on adding
ANY new module to this package.

Deliberately EMPTY of re-exports. Nothing here was exposed from
``scitex_cards/__init__.py`` before the move (verified: zero matches for
``^from \\._dm`` there), so re-exporting now would CREATE a public surface
the move was not authorised to add. Import the submodule you need.
"""
