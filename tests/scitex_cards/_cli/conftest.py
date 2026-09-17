#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`_cli/**` conftest — the `seed_db_from_doc` re-export, and nothing else.

=== THE BOARD-LIFECYCLE FIXTURES MOVED OUT OF HERE (2026-09-17) ============

`pidfile_path`, `board_process`, `zombie_pid` and `reaped_pid` are defined in
``tests/_board_process_fixtures.py`` and registered as a PLUGIN by
``tests/conftest.py``.  They used to live here, and a conftest is the one place
they cannot safely live: pytest 9 binds a conftest's fixtures to the
``Directory`` node collected for THIS directory, and hands out a fresh such
node whenever a later argument's parent directory is re-collected — so an
ARGUMENT ORDER alone could make them invisible to the very files that need
them (``fixture 'pidfile_path' not found``, the py3.11 CI leg, 2026-09-15/16).
The mechanism, the three-argument reproduction, and why a plugin's
Session-scoped binding cannot be undone that way are documented in
``tests/_board_process_fixtures.py``.

The stub below is kept ONLY for `seed_db_from_doc`, which is a plain helper.
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


# EOF
