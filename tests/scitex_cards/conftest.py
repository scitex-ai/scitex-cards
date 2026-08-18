#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared fixtures for ``tests/scitex_cards/`` and its subdirectories.

PA-306 forbids ``monkeypatch`` because pytest's monkeypatch fixture is treated
as a mock by the audit. This module ships an ``env`` fixture that does the
same job — set / clear environment variables with proper test-scoped cleanup —
without using monkeypatch under the hood.

The fixture is intentionally minimal: just ``set(key, value)`` and
``delete(key)``. Tests that previously did
``monkeypatch.setenv("SCITEX_CARDS_AGENT_ID", "agent:test")`` now do
``env.set("SCITEX_CARDS_AGENT_ID", "agent:test")``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest


@dataclass
class _EnvHelper:
    """Captures the original env-var state + cwd and restores them on teardown.

    Implements just the slice of monkeypatch's API the scitex-cards test
    suite actually uses: ``set`` / ``delete`` (env vars) and ``chdir``
    (process working directory). New keys are removed on teardown;
    previously-set keys are restored to their original value; cwd is
    restored to the directory active at fixture entry.
    """

    _saved: dict[str, str | None] = field(default_factory=dict)
    _cwd_saved: str | None = None

    def _remember(self, key: str) -> None:
        if key not in self._saved:
            self._saved[key] = os.environ.get(key)

    def set(self, key: str, value: str) -> None:
        """Set ``os.environ[key] = value`` for the duration of the test."""
        self._remember(key)
        os.environ[key] = value

    def delete(self, key: str) -> None:
        """Remove ``os.environ[key]`` if present (no-op when absent)."""
        self._remember(key)
        os.environ.pop(key, None)

    def chdir(self, path) -> None:
        """Switch process cwd to ``path``; restored on fixture teardown.

        Captures the cwd lazily on first call so tests that don't change
        cwd pay no setup cost.
        """
        if self._cwd_saved is None:
            self._cwd_saved = os.getcwd()
        os.chdir(str(path))

    def restore(self) -> None:
        """Restore every touched env var + cwd to its pre-fixture value."""
        for key, prior in self._saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
        self._saved.clear()
        if self._cwd_saved is not None:
            try:
                os.chdir(self._cwd_saved)
            finally:
                self._cwd_saved = None


@pytest.fixture
def env():
    """Test-scoped env-var helper (PA-306-compliant monkeypatch replacement).

    Yields an :class:`_EnvHelper` whose ``set`` / ``delete`` methods stay
    valid for the duration of the test; on teardown every touched key is
    restored to its pre-test value.
    """
    helper = _EnvHelper()
    try:
        yield helper
    finally:
        helper.restore()


# === Suite-wide lane-discovery isolation ====================================
#
# PR introducing the per-project lane UNION (lead a2a `1ceec0ef` /
# `40c0a42d`, operator-validated) made ``services.get_board`` glob
# ``~/proj/*/.scitex/cards/tasks.yaml`` by default. Without an opt-out,
# test harnesses that pass an explicit ``tmp_path`` global store would
# ALSO pick up the test runner's HOST ``~/proj`` lanes — contaminating
# fixture-pure assertions (e.g. the priority-endpoint test asserts a
# fixture-exact id set).
#
# This autouse fixture pins ``SCITEX_CARDS_LANE_GLOBS=""`` for every
# test in the suite so callers get the pre-union behavior unless they
# explicitly opt back in via the ``env`` fixture (the lane-union
# tests do exactly that). PA-306-compliant: uses :class:`_EnvHelper`
# directly, not ``monkeypatch``.


@pytest.fixture(autouse=True)
def _isolate_host_lane_globs():
    """Empty out ``SCITEX_CARDS_LANE_GLOBS`` for every test by default.

    Stacks safely with the ``env`` fixture — a test that opts back into
    lane discovery via ``env.set("SCITEX_CARDS_LANE_GLOBS", "...")``
    will see its later value during the test body; this fixture
    restores the pre-test value on teardown.
    """
    helper = _EnvHelper()
    helper.set("SCITEX_CARDS_LANE_GLOBS", "")
    try:
        yield
    finally:
        helper.restore()


# === Suite-wide resolvable-creator default ==================================
#
# add_task now FAILS LOUD when the card CREATOR cannot be resolved
# (operator mandate 2026-06-26: "blank creator -> fail loud", no silent
# fallback to a blank/"unknown" creator — see _store._resolve_creator_or_raise).
# The CI/dev environment running the suite has no SCITEX_CARDS_AGENT_ID set, so
# without a default EVERY add_task in the suite would raise. This autouse
# fixture pins a real resolvable creator so the bulk of the suite (which
# tests OTHER behaviour and doesn't care who created the card) keeps working.
#
# The dedicated fail-loud test for the unresolved-creator path opts BACK OUT
# via ``env.delete("SCITEX_CARDS_AGENT_ID")`` to prove the raise — this fixture
# restores the value on teardown, so the two stack safely.


@pytest.fixture(autouse=True)
def _default_resolvable_creator():
    """Set a resolvable ``SCITEX_CARDS_AGENT_ID`` for every test by default.

    Mirrors a real fleet agent's environment (agents MUST set
    ``SCITEX_CARDS_AGENT_ID``); a test that needs to prove the unresolved-creator
    raise deletes it via the ``env`` fixture for the scope of that test.
    """
    helper = _EnvHelper()
    helper.set("SCITEX_CARDS_AGENT_ID", "agent:test-suite")
    try:
        yield
    finally:
        helper.restore()


# === Suite-wide: never let the deprecated store var leak in ==================
#
# ``SCITEX_CARDS_TASKS`` was renamed to ``SCITEX_CARDS_TASKS_YAML_SHARED``
# (2026-07-02) and is now REJECTED fail-loud by ``resolve_tasks_path`` if set.
# A dev/agent shell may still export the old name; without this, any test that
# resolves the store in such an environment would raise. Clear it for every
# test by default. The dedicated fail-loud test opts BACK IN (sets the old var)
# to prove the raise; this fixture restores the pre-test value on teardown.


@pytest.fixture(autouse=True)
def _reject_deprecated_tasks_env():
    """Unset the deprecated ``SCITEX_CARDS_TASKS`` for every test by default."""
    helper = _EnvHelper()
    helper.delete("SCITEX_CARDS_TASKS")
    try:
        yield
    finally:
        helper.restore()


# === Suite-wide: never let the deprecated agent var leak in =================
#
# ``SCITEX_CARDS_AGENT`` was renamed to ``SCITEX_CARDS_AGENT_ID`` (2026-07-02)
# and is now REJECTED fail-loud by the identity resolvers
# (``_store._reject_deprecated_agent_env`` / ``_mcp_channel.resolve_agent_id``)
# if set. A dev/agent shell may still export the old name; without this, any
# test that resolves the acting agent in such an environment would raise.
# Clear it for every test by default. The dedicated fail-loud test opts BACK
# IN (sets the old var) to prove the raise; this fixture restores the pre-test
# value on teardown.


@pytest.fixture(autouse=True)
def _reject_deprecated_agent_env():
    """Unset the deprecated ``SCITEX_CARDS_AGENT`` for every test by default."""
    helper = _EnvHelper()
    helper.delete("SCITEX_CARDS_AGENT")
    try:
        yield
    finally:
        helper.restore()


# === Suite-wide: pin the YAML inbox backend by default ======================
#
# The inbox storage backend DEFAULT flipped to SQLite (operator decision
# 2026-07-09: SQLite is ON, YAML is explicit break-glass). But the bulk of the
# suite asserts the YAML on-disk inbox format / semantics (the ``inboxes:``
# section shape, tasks:/users: coexistence, the digest-collapse maintenance
# path, etc.). Pin the (still-supported) YAML break-glass backend for every
# test by default so those assertions keep exercising the path they were
# written for. The dedicated SQLite-backend tests opt BACK OUT via
# ``env.delete("SCITEX_CARDS_INBOX_BACKEND")`` (to prove the real default) or
# set it explicitly; this fixture restores the pre-test value on teardown, so
# the two stack safely. Production agents set NEITHER var and therefore get the
# real SQLite default.


@pytest.fixture(autouse=True)
def _default_inbox_backend_yaml():
    """Pin ``SCITEX_CARDS_INBOX_BACKEND=yaml`` for every test by default."""
    helper = _EnvHelper()
    helper.set("SCITEX_CARDS_INBOX_BACKEND", "yaml")
    try:
        yield
    finally:
        helper.restore()


def _refuse_swapped_args(doc, db_path) -> None:
    """Fail loudly when the two positional arguments are swapped.

    THE ARGUMENT ORDER IS (doc, db_path) AND GETTING IT BACKWARDS USED TO BE
    SILENT — measured 2026-08-17, by me, in this repo. `seed_db_from_doc(db,
    {"tasks": []})` handed the DICT to SQLite as a path, and SQLite did exactly
    what it is asked to: it CREATED A DATABASE at a path whose name is the
    repr of the dict. A 225 KB file called `{'tasks': []}` landed at the repo
    root, `git add -A` committed it, and it was the CI quality gate that
    noticed — three commits later, on a PR whose test suite was green.

    The shape is the one this branch exists to fix, one layer up: a store
    argument that is not a path gets USED as a path and manufactures a store,
    quietly. `_db_users._db_target` prevents it in the product; nothing
    prevented it in the harness, so the harness got it.

    Two sanity conditions, both cheap:
      * a doc is a mapping — a Path or str here means the args are swapped;
      * a db_path is a path — a mapping here means the same.
    Either way the call is wrong in a way no assertion downstream would
    attribute correctly, so it dies here naming the fix.
    """
    from pathlib import Path as _Path

    if isinstance(doc, (str, _Path)) or isinstance(db_path, dict):
        raise TypeError(
            "seed_db_from_doc(doc, db_path) — the arguments look SWAPPED.\n"
            f"  doc     = {type(doc).__name__}\n"
            f"  db_path = {type(db_path).__name__}\n"
            "Passing a mapping as db_path makes SQLite CREATE a database at a "
            "path named after the dict's repr (measured: a 225 KB file called "
            "\"{'tasks': []}\" at the repo root, committed, caught only by the "
            "CI quality gate). Call it as seed_db_from_doc({'tasks': [...]}, "
            "tmp_path / 'cards.db')."
        )


def seed_db_from_doc(doc, db_path, *, threads=None):
    """Populate a fresh database from an IN-MEMORY document. Returns the summary.

    THE REPLACEMENT FOR ``import_from_yaml`` IN TESTS. That function read a doc
    off a YAML file and rebuilt the DB from it; it is deleted, because SQLite is
    the only store and there is no YAML to read. Tests that used it to *seed* a
    database (build a doc, write YAML, import) now build the same doc and call
    this — which reaches the SAME surviving primitive (``_rebuild_from_doc``),
    so every downstream assertion about schema / columns / counts is unchanged.

    Use this to SEED. Do NOT use it to test importing — the import path is gone;
    a test whose subject was "importing YAML" has no subject and should be
    deleted, not rerouted here.

    ``threads`` (the ``{thread_key: [msgs]}`` map, i.e. ``threads_doc["threads"]``)
    additionally rebuilds the ``messages`` table, exactly as the old import did
    when it loaded the ``threads.yaml`` sidecar.

    Returns ``{"db_path", "tasks", "comments", ...}``. NOTE: there is no
    ``"yaml_path"`` key — nothing was read from YAML. A test that asserted on
    ``summary["yaml_path"]`` is asserting a fact that no longer exists; drop that
    line (it is a removed assertion, not a weakened one).
    """
    from scitex_cards._db import connect, init_schema
    from scitex_cards._db_bootstrap import _rebuild_from_doc, _stamp_meta

    _refuse_swapped_args(doc, db_path)
    conn = connect(str(db_path))
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        summary = _rebuild_from_doc(conn, doc, threads=threads)
        summary["db_path"] = str(db_path)
        _stamp_meta(conn, "test-seed")
        conn.commit()
    finally:
        conn.close()
    return summary


# EOF
