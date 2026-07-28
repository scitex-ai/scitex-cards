#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE CANONICAL READ IS ONE SNAPSHOT — proved in BOTH directions.

The guard in :mod:`scitex_cards._store_canonical_read` cross-checks the export
against ``SELECT COUNT(*) FROM tasks``, because an export that silently
under-reports is the total-loss case: the difference is DELETED on write-back.
That check is right and stays.

What was wrong is that the two numbers came from two DIFFERENT connections, so
in WAL they were two INDEPENDENT snapshots taken an export-duration apart. Any
other agent writing in that window made them disagree with no card missing at
all, and the guard refused a perfectly healthy read — ``list_tasks`` blanked
fleet-wide at 2,374-vs-2,375 while ``scitex-cards db verify`` reported the
database ``quick_check=ok``.

THE TESTS HERE ARE TWO GROUPS AND NEITHER IS EVIDENCE ALONE.

``concurrent_read_result`` (a real second OS process writing while the guard
reads) proves the guard no longer FALSELY fires. On its own that also passes
for a guard that was simply deleted, or for a tolerance window swallowing real
mismatches.

``truncated_export_output`` (a real child process whose exporter genuinely
hands back fewer cards than the snapshot holds) proves the guard still fires on
the failure it exists for. On its own that passes for a guard wired to refuse
EVERYTHING — the always-red uselessness this package already shipped once (the
S2 read accelerator deleted in 256bc2d1, whose freshness check could never pass
again and served an empty board instead).

Together they say the thing that matters: the guard DISCRIMINATES.

No mocks and no ``monkeypatch``: a real temp SQLite database, real cards, real
second processes, and the real guard.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from scitex_cards import _store_backend
from scitex_cards._db import ENV_DB

#: Enough cards that the export takes real time. The false refusal is a race
#: between the export finishing and the verification reading the table, so the
#: window only exists if the export is not instantaneous.
_SEED_CARDS = 1200

#: Guarded reads attempted while the writer runs. On the old two-connection
#: code one read was usually already enough; several make it certain.
_READS_UNDER_LOAD = 8


def _seed(db, n_cards):
    """Write ``n_cards`` real cards into a real, self-owned canonical DB."""
    _store_backend.write_doc_to_db(
        {
            "tasks": [
                {
                    "id": f"seed-{i:05d}",
                    "title": f"seed card {i}",
                    "status": "deferred",
                    "assignee": "agent:test-suite",
                }
                for i in range(n_cards)
            ]
        },
        db.parent / "cosmetic.db",
    )


def _writer_rows(db) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        sql = "SELECT COUNT(*) FROM tasks WHERE id LIKE 'writer-%'"
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _child_env(db) -> dict:
    env = dict(os.environ)
    env[ENV_DB] = str(db)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    return env


@pytest.fixture
def store_db(tmp_path):
    """A real canonical DB, pointed at by a real (restored) env var."""
    db = tmp_path / "cards.db"
    previous = os.environ.get(ENV_DB)
    os.environ[ENV_DB] = str(db)
    try:
        yield db
    finally:
        if previous is None:
            os.environ.pop(ENV_DB, None)
        else:
            os.environ[ENV_DB] = previous


# --------------------------------------------------------------------------- #
# Direction 1 — a concurrent writer must NOT be able to make the read refuse    #
# --------------------------------------------------------------------------- #
#: A REAL second process hammering the same database. Separate OS process on
#: its own connection deliberately: the bug is about two snapshots of one WAL
#: database, which an in-process writer sharing our connection cannot produce.
_WRITER_SRC = textwrap.dedent(
    """
    import json, sqlite3, sys, time
    conn = sqlite3.connect(sys.argv[1], timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    i = 0
    while True:
        i += 1
        cid = "writer-%06d" % i
        payload = json.dumps(
            {"id": cid, "title": cid, "status": "deferred",
             "assignee": "agent:test-suite"}
        )
        try:
            conn.execute(
                "INSERT INTO tasks (id, title, status, card_json, row_order) "
                "VALUES (?, ?, 'deferred', ?, "
                "  (SELECT COALESCE(MAX(row_order), 0) + 1 FROM tasks))",
                (cid, cid, payload),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        time.sleep(0.002)
    """
)


@pytest.fixture(scope="module")
def concurrent_read_result(tmp_path_factory):
    """Run the guarded read repeatedly WHILE a real writer commits rows.

    All the work happens here so each test below can assert exactly one thing
    about the outcome without paying for the setup again. The env var is set
    and restored inside this fixture, so nothing leaks to other tests.
    """
    from scitex_cards._store import _read_canonical_db_or_raise

    tmp_path = tmp_path_factory.mktemp("concurrent")
    db = tmp_path / "cards.db"
    previous = os.environ.get(ENV_DB)
    os.environ[ENV_DB] = str(db)
    writer = None
    try:
        _seed(db, _SEED_CARDS)

        writer_py = tmp_path / "writer.py"
        writer_py.write_text(_WRITER_SRC)
        writer = subprocess.Popen(
            [sys.executable, str(writer_py), str(db)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # Wait until the writer is demonstrably committing, so the reads below
        # really do overlap concurrent writes rather than racing an idle DB.
        deadline = time.time() + 30
        while _writer_rows(db) < 5:
            if writer.poll() is not None:
                err = writer.stderr.read().decode(errors="replace")
                pytest.fail(f"the concurrent writer died: {err}")
            if time.time() > deadline:
                pytest.fail("the concurrent writer never committed a row")
            time.sleep(0.05)

        before = _writer_rows(db)
        errors: list[str] = []
        sizes: list[int] = []
        for _ in range(_READS_UNDER_LOAD):
            try:
                sizes.append(len(_read_canonical_db_or_raise()["tasks"]))
            except RuntimeError as exc:
                errors.append(str(exc))
        after = _writer_rows(db)
    finally:
        if writer is not None:
            writer.kill()
            writer.wait(timeout=30)
            writer.stderr.close()
        if previous is None:
            os.environ.pop(ENV_DB, None)
        else:
            os.environ[ENV_DB] = previous

    return {
        "errors": errors,
        "smallest_read": min(sizes) if sizes else 0,
        "rows_written_during_reads": after - before,
    }


def test_a_concurrent_writer_never_makes_the_canonical_read_refuse(
    concurrent_read_result,
):
    """The outage, closed: another agent writing must not blank this read.

    On the old code (export on one connection, ``COUNT(*)`` re-counted on a
    second) this fails: the count is the table as it is NOW while the export
    describes the table as it was when the export STARTED, so every row the
    writer committed in between reads as a "missing card". Reproduced 10/10
    against a copy of the live board with a writer running, 0/10 without.
    """
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert concurrent_read_result["errors"] == []


def test_reads_under_concurrent_writes_return_the_whole_store(
    concurrent_read_result,
):
    """Not merely "no exception" — the document is real.

    A guard "fixed" by making the read lenient would also stop raising, so the
    CONTENT is what is pinned: every read saw at least the seeded cards.
    """
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert concurrent_read_result["smallest_read"] >= _SEED_CARDS


def test_the_concurrency_test_actually_had_a_concurrent_writer(
    concurrent_read_result,
):
    """Guards the guard's test: without this the pair above silently degrades
    into "reads work on an idle database", which is the always-green shape
    this whole file exists to argue against."""
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert concurrent_read_result["rows_written_during_reads"] > 0


# --------------------------------------------------------------------------- #
# Direction 2 — a genuinely under-reporting export must STILL be refused        #
# --------------------------------------------------------------------------- #
#: THE FAULT IS INJECTED AT THE EXPORTER, IN A REAL CHILD PROCESS, because that
#: is the only place it CAN be: after the fix both numbers come from the same
#: snapshot of the same table on the same connection, so NO state you can put
#: in the database will make them differ. (Every DB-level trick — a filtered
#: view, a renamed table, a LIMIT — is seen identically by the export and by
#: COUNT(*), which is precisely the fix working.) So the child rebinds
#: `_db_export.export_doc` to a wrapper around the REAL exporter that drops one
#: REAL card from a REAL database, then calls the REAL guard end to end.
_TRUNCATED_EXPORT_SRC = textwrap.dedent(
    """
    import sys
    from scitex_cards import _db_export

    real_export_doc = _db_export.export_doc

    def drops_one_card(*args, **kwargs):
        doc, threads = real_export_doc(*args, **kwargs)
        doc["tasks"] = doc["tasks"][:-1]
        return doc, threads

    _db_export.export_doc = drops_one_card

    from scitex_cards._store import _read_canonical_db_or_raise

    try:
        _read_canonical_db_or_raise()
    except RuntimeError as exc:
        print("REFUSED: %s" % exc)
        sys.exit(0)
    print("ACCEPTED A TRUNCATED EXPORT")
    sys.exit(1)
    """
)


@pytest.fixture(scope="module")
def truncated_export_output(tmp_path_factory):
    """Drive the real guard against a real DB with a genuinely partial export."""
    tmp_path = tmp_path_factory.mktemp("truncated")
    db = tmp_path / "cards.db"
    previous = os.environ.get(ENV_DB)
    os.environ[ENV_DB] = str(db)
    try:
        _seed(db, 12)
    finally:
        if previous is None:
            os.environ.pop(ENV_DB, None)
        else:
            os.environ[ENV_DB] = previous

    script = tmp_path / "truncated_export.py"
    script.write_text(_TRUNCATED_EXPORT_SRC)
    done = subprocess.run(
        [sys.executable, str(script)],
        env=_child_env(db),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if done.returncode != 0:
        pytest.fail(
            f"the guard did not refuse a truncated export.\n"
            f"stdout: {done.stdout}\nstderr: {done.stderr}"
        )
    return done.stdout


def test_an_export_that_under_reports_the_same_snapshot_is_still_refused(
    truncated_export_output,
):
    """The detection the guard exists for is INTACT — not weakened, not gated.

    Sharing one connection removed the FALSE positives; it must not have
    removed the TRUE one. On write-back the difference between the export and
    the table is DELETED, so under-reporting has to stay fatal.
    """
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert "is INCOMPLETE" in truncated_export_output


def test_the_refusal_still_names_both_counts(truncated_export_output):
    """Both numbers stay in the message: naming 2,374 AND 2,375 is what made
    the false-positive outage diagnosable instead of a mystery."""
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert "returned 11 cards but the tasks table holds 12" in (
        truncated_export_output
    )


def test_the_refusal_still_names_what_would_have_been_deleted(
    truncated_export_output,
):
    """The consequence, spelled out — the reason refusing beats continuing."""
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert "1 missing cards would be DELETED" in truncated_export_output


# --------------------------------------------------------------------------- #
# Must-not-fire baselines                                                       #
# --------------------------------------------------------------------------- #
def test_a_quiet_store_reads_every_card_back(store_db):
    """With nothing else touching the DB the read PASSES and is complete.

    Paired with the refusals above so the guard is observed DISCRIMINATING
    rather than merely firing.
    """
    # Arrange
    from scitex_cards._store import _read_canonical_db_or_raise

    _seed(store_db, 25)

    # Act
    doc = _read_canonical_db_or_raise()

    # Assert
    assert [t["id"] for t in doc["tasks"]] == [f"seed-{i:05d}" for i in range(25)]


def test_a_genuinely_empty_store_is_still_a_legitimate_read(store_db):
    """Zero-vs-zero AGREES — the check is equality, not truthiness.

    A fresh store legitimately holds no cards; refusing it would make every new
    install unusable, which is the always-refusing shape this fix is against.
    """
    # Arrange
    from scitex_cards._store import _read_canonical_db_or_raise

    _seed(store_db, 0)

    # Act
    doc = _read_canonical_db_or_raise()

    # Assert
    assert doc["tasks"] == []


def test_the_canonical_read_leaves_no_transaction_open(store_db):
    """The read transaction is ROLLED BACK — a reader must never hold a lock.

    ``BEGIN DEFERRED`` pins a WAL snapshot; leaving it open would pin the WAL
    file's growth and block checkpointing for every writer on the box. The
    guard rolls back in a ``finally``, so the write lock is free the instant
    the read returns — asserted by actually taking it.
    """
    # Arrange
    from scitex_cards._store import _read_canonical_db_or_raise

    _seed(store_db, 5)
    _read_canonical_db_or_raise()

    # Act
    conn = sqlite3.connect(str(store_db), timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        acquired = True
        conn.rollback()
    finally:
        conn.close()

    # Assert
    assert acquired


# EOF
