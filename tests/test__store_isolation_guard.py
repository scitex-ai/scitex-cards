#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard-of-the-guard: prove ``tests/conftest.py``'s isolation fixture is ACTIVE.

The pinning fixture in ``tests/conftest.py`` (``_store_env_stays_pinned`` +
the module-level ``_pin_to_scratch()`` it shares ``_point_env_at`` with) is
what stands between this suite and the fleet's real board (see that file's
module docstring for the incident history: three wipes, 2026-07-19 x2 and
2026-07-21). A future conftest refactor could silently narrow or drop that
fixture — rename it, scope it wrong, forget to call ``_point_env_at`` on one
of the paths it touches — and nothing would fail LOCALLY, because every test
that resolves a store would still get SOME path back, just possibly the real
one. This file is that trip-wire: it asserts on the RESULT (where the store
actually resolves to), not on the fixture's continued existence by name, so a
refactor that keeps the behaviour but renames the mechanism still passes, and
one that breaks the behaviour fails here FIRST rather than silently at some
unrelated test that happens to write a card.
"""

from __future__ import annotations

from pathlib import Path

from scitex_cards._db import ENV_DB, resolve_db_path
from scitex_cards._paths import _user_root

# Kept in sync BY HAND with tests/conftest.py's `_REAL_STORE_CANDIDATES` —
# duplicated rather than imported so this guard does not depend on the
# internals of the thing it is guarding still being named/shaped the same way.
_REAL_HOMES = ("/home/agent", "/home/ywatanabe")


def test_resolved_db_path_lives_under_the_pytest_tmp_root(tmp_path_factory):
    """``resolve_db_path(None)`` must resolve inside pytest's own tmp tree.

    ``tmp_path_factory.getbasetemp()`` is the root every ``tmp_path`` /
    ``tmp_path_factory.mktemp()`` call in this session nests under —
    including the scratch directory ``_store_env_stays_pinned`` points
    ``$SCITEX_CARDS_DB`` at for THIS test. If the isolation fixture stopped
    running (or stopped covering ``$SCITEX_CARDS_DB``), this would instead
    resolve to the real user-canonical store.
    """
    resolved = resolve_db_path(None).resolve()
    base_tmp = tmp_path_factory.getbasetemp().resolve()
    assert base_tmp in resolved.parents, (
        f"resolve_db_path(None) = {resolved} is NOT under pytest's tmp root "
        f"{base_tmp} — the session isolation fixture in tests/conftest.py "
        "does not appear to be pinning $SCITEX_CARDS_DB any more."
    )


def test_resolved_db_path_is_not_a_real_store_candidate():
    """Belt-and-braces companion to the tmp-root check above: also assert the
    resolved path is not literally one of the known real-store locations."""
    resolved = resolve_db_path(None).resolve()
    for home in _REAL_HOMES:
        for pkg in ("cards", "todo"):
            real = Path(home, ".scitex", pkg, "cards.db")
            assert resolved != real, (
                f"resolve_db_path(None) resolved to the REAL store {real} — "
                "the isolation fixture in tests/conftest.py is not active."
            )


def test_scitex_dir_fallback_is_also_pinned_under_tmp(tmp_path_factory):
    """``$SCITEX_DIR`` — the base for ``resolve_db_path``'s tier-4 fallback —
    must ALSO resolve under pytest's tmp root, not the real home.

    This guards the ``SCITEX_DIR`` pin added alongside the end-of-session
    real-store assert: a test that clears both ``$SCITEX_CARDS_DB`` and
    ``$SCITEX_TODO_DB`` (see ``tests/scitex_cards/test__paths.py``'s
    ``clean_store_env`` fixture) falls through to this path, and it must
    land in scratch even then.
    """
    base_tmp = tmp_path_factory.getbasetemp().resolve()
    scitex_dir_root = _user_root().resolve()
    assert base_tmp in scitex_dir_root.parents or scitex_dir_root == base_tmp, (
        f"$SCITEX_DIR resolves _user_root() to {scitex_dir_root}, not under "
        f"pytest's tmp root {base_tmp} — the SCITEX_DIR pin in "
        "tests/conftest.py does not appear to be active."
    )
    for home in _REAL_HOMES:
        assert not str(scitex_dir_root).startswith(home), (
            f"$SCITEX_DIR resolves under the REAL home {home} "
            f"({scitex_dir_root}) — the SCITEX_DIR pin is not active."
        )


def test_env_db_still_names_the_winning_precedence_tier():
    """Sanity: ``$SCITEX_CARDS_DB`` (the env var, not just the resolved path)
    is actually set — a fixture that stopped SETTING it (as opposed to one
    that set it to the wrong place) would pass the two tests above vacuously
    if ``resolve_db_path`` fell through to an explicit-arg-only code path
    that happened to still avoid the real store by luck."""
    import os

    assert os.environ.get(ENV_DB), (
        f"${ENV_DB} is unset — the session isolation fixture in "
        "tests/conftest.py is not pinning it."
    )


# EOF
