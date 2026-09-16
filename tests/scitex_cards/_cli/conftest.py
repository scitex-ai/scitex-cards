#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility exports needed by tests below the CLI directory.

The cross-module board-process fixtures live in the parent conftest so pytest's
explicit-file xdist invocation cannot omit them.  This local conftest only
keeps the historical ``seed_db_from_doc`` import surface available to CLI
tests whose unqualified ``from conftest`` resolves to the nearest ancestor.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path as _Path

# === `seed_db_from_doc` re-export ===========================================
#
# THE HELPER IS DEFINED IN tests/scitex_cards/conftest.py, BUT `from conftest
# import seed_db_from_doc` INSIDE _cli/** BINDS TO *THIS* FILE — pytest makes
# the NEAREST ancestor conftest the `conftest` module — and would miss it.
#
# Without this re-export the import raises at COLLECTION, so the affected tests
# do not fail, they never run at all:
#
#     test__db_snapshot_shrink_guard.py     test__db_snapshot_freshness_guard.py
#     test__verb_renames.py                 test__stale.py
#
# Measured 2026-08-16: all four ERROR on collection, on develop, unmodified.
# Each one guards something real — the snapshot rail refusing a collapsed card
# count, and refusing a stale export — so this is four disarmed guards, not
# four missing conveniences. A test that cannot run is the "gate that cannot
# fail" wearing test clothes.
#
# `_django/conftest.py` hit this first and solved it exactly this way; the
# pattern is copied rather than reinvented. Load the shared conftest BY PATH
# (not by name, which is the ambiguity being fixed) and re-export the symbol.
_shared = _Path(__file__).resolve().parent.parent / "conftest.py"
_spec = _ilu.spec_from_file_location("_scitex_cards_shared_conftest", _shared)
_mod = _ilu.module_from_spec(_spec)
_sys_modules_key = _spec.name
sys.modules[_sys_modules_key] = _mod  # register BEFORE exec (py3.12 dataclass lookup)
_spec.loader.exec_module(_mod)
seed_db_from_doc = _mod.seed_db_from_doc


# THERE IS DELIBERATELY NO `free_port` FIXTURE HERE.
#
# One existed, and it was both dead and wrong. Dead: the end-to-end tests ask
# for ``--port 0`` and let the KERNEL choose, so nothing consumed it. Wrong:
# it acquired a socket, bound it to find a free port, closed it and RETURNED
# the number — which STX-TQ005 flagged, correctly, as a fixture acquiring an
# external resource it can never tear down on a failing test.
#
# The remedy is not to `yield` the socket. A bind-then-release port is a
# TOCTOU race with a name: the port is free at the moment it is measured and
# any process may take it before the board binds. ``--port 0`` has neither
# problem, because the kernel picks and binds in one step. So the acquisition
# is gone rather than wrapped.


# EOF
