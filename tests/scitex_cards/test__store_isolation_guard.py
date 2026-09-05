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

from scitex_cards._db import ENV_DB
from scitex_cards._paths import _user_root
from scitex_cards._store_target import resolve_store_target

# Kept in sync BY HAND with tests/conftest.py's `_REAL_STORE_CANDIDATES` —
# duplicated rather than imported so this guard does not depend on the
# internals of the thing it is guarding still being named/shaped the same way.
_REAL_HOMES = ("/home/agent", "/home/ywatanabe")

#: Where a REAL board would live under each of those homes. This, not the home
#: itself, is the place the file fallback must never resolve into: a runner
#: whose whole work tree sits under its user's home (the Spartan self-hosted
#: runners: /home/ywatanabe/actions-runner-org/_work/_temp/...) is a legitimate
#: scratch location, and "not under the home" failed there by construction on
#: the v0.51.0 release run (2026-09-05) while the store stayed untouched.
_REAL_STORE_ROOTS = tuple(str(Path(h) / ".scitex") for h in _REAL_HOMES)

#: The schema the LIVE board's tables live in. A resolved target that reaches
#: it is the failure this whole file exists to catch.
_LIVE_SCHEMA = "public"


def _resolved_schema() -> str | None:
    """The schema the resolved store target is scoped to, or None if unscoped.

    THE ISOLATION BOUNDARY MOVED, so the guard reads a different thing. It used
    to compare FILE PATHS, because a stray resolve wrote to a stray file. The
    store is PostgreSQL now: every test resolves to the same server and the
    same database as the live board, and the ONLY thing keeping a test write
    out of the operator's cards is the `search_path` the pinning fixture puts
    on the DSN. So that is what has to be asserted.

    An unscoped DSN — no `options=-csearch_path=...` — is the dangerous state:
    it resolves to `public`, which is the live board.
    """
    target = str(resolve_store_target(None))
    marker = "search_path%3D"
    if marker not in target:
        return None
    return target.rsplit(marker, 1)[1].split("&")[0]


def test_the_resolved_store_is_scoped_to_a_schema_at_all():
    """An unscoped DSN IS the live board — there is no separate test database.

    The direct successor to the old tmp-root check. That one asked "is the
    resolved path inside pytest's scratch tree"; the equivalent question now is
    "is the resolved target confined to a schema of its own", because a DSN
    with no search_path lands in `public` alongside the real cards.
    """
    # Arrange
    # Act
    schema = _resolved_schema()
    # Assert
    assert schema is not None, (
        "the resolved store target carries no search_path, so it resolves to "
        f"the {_LIVE_SCHEMA} schema — the LIVE board. The session isolation "
        "fixture in tests/conftest.py does not appear to be pinning "
        f"${ENV_DB} to an ephemeral schema any more."
    )


def test_the_resolved_store_is_not_the_live_schema():
    """The same pin from the other direction, and not redundant.

    A target could carry a search_path and still name `public` — a refactor
    that kept the mechanism and lost the isolation. The test above would pass
    on that; this one is what fails. Same reasoning as the two SCITEX_DIR
    checks further down, which were split for exactly this asymmetry.
    """
    # Arrange
    # Act
    schema = _resolved_schema()
    # Assert
    assert schema != _LIVE_SCHEMA, (
        f"the resolved store target is scoped to {_LIVE_SCHEMA!r}, which is "
        "where the REAL board's tables live — the isolation fixture in "
        "tests/conftest.py is not active."
    )


def test_scitex_dir_fallback_is_also_pinned_under_tmp(tmp_path_factory):
    """``$SCITEX_DIR`` — the base for ``resolve_db_path``'s tier-4 fallback —
    must ALSO resolve under pytest's tmp root, not the real home.

    This guards the ``SCITEX_DIR`` pin added alongside the end-of-session
    real-store assert: a test that clears both ``$SCITEX_CARDS_DB`` and
    ``$SCITEX_CARDS_DB`` (see ``tests/scitex_cards/test__paths.py``'s
    ``clean_store_env`` fixture) falls through to this path, and it must
    land in scratch even then.
    """
    # Arrange
    # Act
    base_tmp = tmp_path_factory.getbasetemp().resolve()
    scitex_dir_root = _user_root().resolve()
    # Assert
    assert base_tmp in scitex_dir_root.parents or scitex_dir_root == base_tmp, (
        f"$SCITEX_DIR resolves _user_root() to {scitex_dir_root}, not under "
        f"pytest's tmp root {base_tmp} — the SCITEX_DIR pin in "
        "tests/conftest.py does not appear to be active."
    )


def test_scitex_dir_fallback_is_not_under_any_real_store_root(tmp_path_factory):
    """The same pin, asserted from the OTHER direction.

    Split from the test above under STX-TQ007 (one assertion per test), and the
    split is worth more than compliance here: "resolves under pytest's tmp" and
    "does not resolve into a real store root" are DIFFERENT claims. A tmp root
    that somehow sat inside a real ``~/.scitex`` would satisfy the first and
    violate the second, and while the two were one test the second assertion
    never ran unless the first passed — so the more dangerous condition was
    checked only when the safer one already held.

    The boundary is the STORE ROOT (``<home>/.scitex``), not the home: pytest's
    tmp root legitimately sits under the runner user's home on the self-hosted
    runners, and that layout never touches a real board.
    """
    # Arrange
    scitex_dir_root = _user_root().resolve()
    # Act
    offenders = [
        root for root in _REAL_STORE_ROOTS
        if str(scitex_dir_root) == root or str(scitex_dir_root).startswith(root + "/")
    ]
    # Assert
    assert offenders == [], (
        f"$SCITEX_DIR resolves into the REAL store root(s) {offenders} "
        f"({scitex_dir_root}) — the SCITEX_DIR pin is not active."
    )


def test_env_db_still_names_the_winning_precedence_tier():
    """Sanity: ``$SCITEX_CARDS_DB`` (the env var, not just the resolved path)
    is actually set — a fixture that stopped SETTING it (as opposed to one
    that set it to the wrong place) would pass the two tests above vacuously
    if ``resolve_db_path`` fell through to an explicit-arg-only code path
    that happened to still avoid the real store by luck."""
    # Arrange
    # Act
    import os

    # Assert
    assert os.environ.get(ENV_DB), (
        f"${ENV_DB} is unset — the session isolation fixture in "
        "tests/conftest.py is not pinning it."
    )


# EOF
