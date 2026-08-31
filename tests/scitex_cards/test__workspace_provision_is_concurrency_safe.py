#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provisioning one workspace twice at once must SETTLE, not raise.

``provision_workspace_store`` promised this in prose and did not do it. The
docstring read, verbatim: "``ON CONFLICT DO NOTHING`` and ``IF NOT EXISTS``
carry that, so two concurrent provisions of the same workspace settle rather
than racing into an error."

They do not carry it. ``IF NOT EXISTS`` CHECKS THEN CREATES, and PostgreSQL
does not make that pair atomic against a concurrent create: two sessions both
find the object absent, both issue the CREATE, and the loser takes a unique
violation on a SYSTEM CATALOGUE index -- which is why the failure never
mentions the caller's own table by name.

MEASURED, 2026-08-31, both halves and in this order:

    CI, python 3.11, xdist   pg_type_typname_nsp_index      (tasks)
    this file, 8 threads     pg_namespace_nspname_index     (ws_<digest>)

CI only ever showed the second race because it happened to win the first.
A fix scoped to what CI reported would have left the CREATE SCHEMA half live,
and the resulting green would have proved nothing -- so the schema-level race
is the one this file reproduces.

WHY THE IDENTITY IS RANDOM PER RUN, which is not incidental. ``_schema_for``
is a deterministic digest, so an identity is DATABASE-GLOBAL and outlives the
per-test schema: a fixed name would find the tenant already provisioned on the
second run, take the fast path, and pass without ever entering the create
window. The first version of this test did exactly that. A random identity
guarantees a cold start, which is the only state where the race exists.

WHAT THIS CANNOT COVER, named rather than implied: threads in one process
share a client but take separate connections, which is enough for the
catalogue race. It is NOT a substitute for the cross-PROCESS case, and it
cannot prove the lock is released on a crashed backend -- that rests on
PostgreSQL releasing session locks when the connection dies.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import uuid

import pytest

from scitex_cards._workspace import (
    ENV_WORKSPACE_DB,
    _schema_for,
    provision_workspace_store,
)

_CONCURRENCY = 8
_ABSENT = object()


@pytest.fixture
def cold_identity(new_store) -> tuple[str, tuple[str, ...]]:
    """A cluster plus a tenant identity NOTHING has provisioned yet.

    SETS THE VARIABLE BY HAND rather than with ``monkeypatch``, matching
    ``test__workspace_segments``. This repo forbids mocks (PA-306 §3) and the
    audit counts the fixture parameter itself, so the save/restore is written
    out; it is also the honest shape here, since the subject is a real
    environment variable read by real connections, not a patched one.
    """
    cluster = new_store()
    previous = os.environ.get(ENV_WORKSPACE_DB, _ABSENT)
    os.environ[ENV_WORKSPACE_DB] = cluster
    segments = (f"race-{uuid.uuid4().hex[:12]}", "proj")
    try:
        yield cluster, segments
    finally:
        if previous is _ABSENT:
            os.environ.pop(ENV_WORKSPACE_DB, None)
        else:
            os.environ[ENV_WORKSPACE_DB] = previous
        # The tenant schema is database-global, so it is NOT removed by the
        # per-test schema's CASCADE and would otherwise leak one schema per run.
        from scitex_cards._backend_connect import connect

        conn = connect(cluster, read_only=False, rows_by_name=True)
        try:
            conn.execute(f'DROP SCHEMA IF EXISTS "{_schema_for(segments)}" CASCADE')
            conn.commit()
        finally:
            conn.close()


def _provision_concurrently(segments: tuple[str, ...]) -> list[str]:
    with cf.ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
        futures = [
            pool.submit(provision_workspace_store, *segments)
            for _ in range(_CONCURRENCY)
        ]
        return [f.result() for f in futures]


def test_concurrent_provisions_of_one_identity_do_not_raise(cold_identity):
    # Arrange
    _cluster, segments = cold_identity

    # Act — every caller races for the same brand-new schema.
    results = _provision_concurrently(segments)

    # Assert — .result() re-raises, so arriving here is the claim.
    assert len(results) == _CONCURRENCY


def test_concurrent_provisions_of_one_identity_agree_on_the_store(cold_identity):
    # Arrange
    _cluster, segments = cold_identity

    # Act
    results = _provision_concurrently(segments)

    # Assert — settling on DIFFERENT DSNs would split one tenant across stores,
    # which is worse than the raise: it fails silently.
    assert len(set(results)) == 1

# EOF
