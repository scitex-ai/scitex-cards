#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configure Django for the _django board tests (real settings, no mocks).

Skips the whole _django test package cleanly when Django is not installed
(the web extra is optional), so the core suite still runs on a lean install.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

import pytest

django = pytest.importorskip("django")


def pytest_configure(config):  # noqa: ARG001
    """Point Django at the standalone board settings and call setup() once."""
    import os

    from django.conf import settings

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")
    if not settings.configured:
        django.setup()


# === Post-cutover test plumbing for _django/** ==============================
#
# Two things every _django test needs under the database-only store, provided
# HERE (once) so the per-file migrations stay to plain store-path normalization.
#
# 1. `seed_db_from_doc` re-export. The helper is defined in
#    tests/scitex_cards/conftest.py, but `from conftest import seed_db_from_doc`
#    inside _django/** binds to THIS conftest (the nearest ancestor) and would
#    miss it. Load the shared conftest by path and re-export the symbol so every
#    _django test — at any depth — can `from conftest import seed_db_from_doc`.
_shared = _Path(__file__).resolve().parent.parent / "conftest.py"
_spec = _ilu.spec_from_file_location("_scitex_cards_shared_conftest", _shared)
_mod = _ilu.module_from_spec(_spec)
_sys.modules[_spec.name] = _mod  # register BEFORE exec (py3.12 dataclass lookup)
_spec.loader.exec_module(_mod)
seed_db_from_doc = _mod.seed_db_from_doc


# 2. NO ``tasks.yaml`` MARKER FIXTURE. There used to be an autouse fixture here
#    that CREATED an empty ``tasks.yaml`` beside every test's scratch database,
#    "to keep the board in its normal store-exists state". Deleted, and its
#    absence is now load-bearing.
#
#    THAT FIXTURE IS WHY THE 2026-07-29 OUTAGE HAD NO FAILING TEST. The board
#    gated its CARD read on that file's existence (``services.get_board``:
#    ``tasks = _load_global_tasks(resolved) if store_exists else []``). Under
#    the cutover nothing creates it, so on the operator's live board the gate was
#    permanently shut and /tasks served 0 cards while 2,654 sat in the database
#    — for over a day. Every test passed throughout, because this fixture
#    manufactured, before each one, the exact file production did not have.
#
#    A harness that supplies a missing precondition does not test the system;
#    it tests the harness. The board now reads the database unconditionally, so
#    no test needs the file — and any future re-introduction of a
#    file-existence gate on the card read fails here instead of on the
#    operator's board.
