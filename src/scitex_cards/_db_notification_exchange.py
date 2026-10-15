#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schema rung v13 -> v14: one delivery exchange id per notification.

MOVED: the definition lives in
:mod:`scitex_cards._database.schema_rungs`. This module is a
compatibility shim — it re-exports the same object (identity holds both
ways) with its original ``__module__`` attribution preserved for
pickle and introspection.
"""

from __future__ import annotations

from ._database.schema_rungs import _migrate_v13_to_v14

__all__ = ["_migrate_v13_to_v14"]

# EOF
