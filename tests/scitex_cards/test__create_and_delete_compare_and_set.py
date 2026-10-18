#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fenced creation and deletion by expected revision.

``update_task`` grew its compare-and-set first; creation and deletion kept
last-writer-wins. The hole this closes is cross-reader, not same-process:
two writers on different containers share PostgreSQL but not the file
lock, so both pass the in-doc duplicate check and the second create
silently overwrites the first's card, while a delete can tombstone a card
its caller never saw. These tests pin the two fences:

* ``add_task(..., expected_revision=N)`` — asserts ABSENCE (a create has no
  prior revision to compare; the int arms the fence and rides the conflict
  report). A held id — live row OR tombstone — raises RevisionConflictError
  and nothing is written, not even the winner's comments.
* ``delete_task(..., expected_revision=N)`` — asserts the live row is still
  at N. A lost race raises RevisionConflictError and the batch rolls back,
  so the target stays live AND the dependents keep pointing at it.

Both stay OPT-IN with a ``None`` default (REJECT-by-default was ruled
unusable for a mixed-version fleet): unarmed calls behave byte-identically
to before.

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture
(bare-param pattern): every seed/read below touches the ambient per-test
store, which is refused without a writable cluster.
"""

from __future__ import annotations

import inspect

import pytest

import scitex_cards
from scitex_cards._store import add_task, delete_task, get_task
from scitex_cards._store_errors import RevisionConflictError
from scitex_cards._store_single_card import read_card_or_raise
from scitex_cards._store_target import resolve_store_target
from scitex_cards._task import TaskValidationError


def _revision(task_id="c1"):
    """The row's current revision, read straight from the isolated database.

    Raises when the row is missing (same anti-vacuity rule as the update
    CAS tests): a None would be passed as ``expected_revision`` and
    silently disable the guard under test.
    """
    import os

    from scitex_cards._db import connect

    db = os.environ["SCITEX_STORE_DSN"]
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT revision FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["revision"] is None:
        raise AssertionError(
            f"no revision for task {task_id!r} in {db} -- a None would be "
            f"passed as expected_revision and silently disable the guard "
            f"under test, which is a vacuous pass rather than a failure."
        )
    return row["revision"]


def _seeded(task_id="c1", **fields):
    """One card in the ambient store, owned so the owner gate passes."""
    base = {
        "id": task_id,
        "title": "Original",
        "status": "deferred",
        "assignee": "tester",
        "created_by": "tester",
    }
    base.update(fields)
    return add_task(**base)


# --------------------------------------------------------------------------
# the surface exists and stays opt-in
# --------------------------------------------------------------------------


def test_add_task_accepts_a_keyword_only_expected_revision_defaulting_to_none():
    params = inspect.signature(scitex_cards.add_task).parameters
    assert "expected_revision" in params
    assert params["expected_revision"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["expected_revision"].default is None


def test_delete_task_accepts_a_keyword_only_expected_revision_defaulting_to_none():
    params = inspect.signature(scitex_cards.delete_task).parameters
    assert "expected_revision" in params
    assert params["expected_revision"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["expected_revision"].default is None


def test_unarmed_create_behaves_as_before(postgres_dsn):
    merged = _seeded()
    assert merged["title"] == "Original"


def test_unarmed_duplicate_create_still_raises_task_validation_error(postgres_dsn):
    _seeded()
    with pytest.raises(TaskValidationError):
        _seeded(title="Second")


def test_unarmed_delete_still_tombstones(postgres_dsn):
    _seeded()
    out = delete_task(None, "c1")
    assert out["refs"] == []
    with pytest.raises(Exception):
        get_task(None, "c1")


# --------------------------------------------------------------------------
# fenced create: absence, or a loud refusal carrying the winner's revision
# --------------------------------------------------------------------------


def test_fenced_create_on_a_fresh_id_lands(postgres_dsn):
    merged = _seeded(expected_revision=0)
    assert merged["title"] == "Original"


def test_fenced_create_against_a_live_row_is_refused(postgres_dsn):
    _seeded()
    winner_rev = _revision()
    with pytest.raises(RevisionConflictError) as exc_info:
        _seeded(title="Loser", expected_revision=0)
    assert exc_info.value.found == winner_rev


def test_the_refused_create_leaves_the_winner_untouched(postgres_dsn):
    from scitex_cards._store import comment_task, update_task

    _seeded()
    comment_task(None, "c1", text="winner's comment", by="tester")
    update_task(None, "c1", title="Winner")
    winner_rev = _revision()
    # Act: a fenced create racing the winner.
    with pytest.raises(RevisionConflictError):
        _seeded(title="Loser", note="stale", expected_revision=0)
    # Assert: row, revision and comments are exactly the winner's.
    assert _revision() == winner_rev
    live = get_task(None, "c1")
    assert live["title"] == "Winner"
    assert any(
        c.get("text") == "winner's comment" for c in live.get("comments", [])
    )


def test_fenced_create_against_a_tombstoned_id_is_refused(postgres_dsn):
    _seeded()
    delete_task(None, "c1")
    tomb_rev = _revision()
    with pytest.raises(RevisionConflictError) as exc_info:
        _seeded(title="Resurrect", expected_revision=0)
    assert exc_info.value.found == tomb_rev
    # The tombstone is intact: still cancelled, still stamped.
    _card, _rev = read_card_or_raise(resolve_store_target(None), "c1")
    assert _card["status"] == "cancelled"
    assert _card["_log_meta"]["deleted_at"]


def test_fenced_create_refuses_a_non_int_fencing_token(postgres_dsn):
    with pytest.raises(TypeError):
        _seeded(expected_revision="0")
    with pytest.raises(TypeError):
        _seeded(expected_revision=True)


# --------------------------------------------------------------------------
# fenced delete: the tombstone lands only on the revision it asserts
# --------------------------------------------------------------------------


def test_fenced_delete_on_the_current_revision_tombstones(postgres_dsn):
    _seeded(depends_on=None)
    current = _revision()
    out = delete_task(None, "c1", expected_revision=current)
    assert out["refs"] == []
    _card, tomb_rev = read_card_or_raise(resolve_store_target(None), "c1")
    assert _card["status"] == "cancelled"
    assert tomb_rev == current + 1


def test_fenced_delete_on_a_stale_revision_is_refused(postgres_dsn):
    from scitex_cards._store import update_task

    _seeded()
    stale = _revision()
    update_task(None, "c1", title="Someone else moved it")
    current = _revision()
    assert current != stale
    with pytest.raises(RevisionConflictError) as exc_info:
        delete_task(None, "c1", expected_revision=stale)
    assert exc_info.value.expected == stale
    assert exc_info.value.found == current


def test_the_refused_delete_changes_nothing_including_dependents(postgres_dsn):
    from scitex_cards._store import update_task

    _seeded()
    _seeded("c2", title="Dependent", depends_on=["c1"])
    stale = _revision("c1")
    update_task(None, "c1", title="Someone else moved it")
    # Act: the fenced tombstone loses the race.
    with pytest.raises(RevisionConflictError):
        delete_task(None, "c1", expected_revision=stale)
    # Assert: target live at the winner's revision, dependent still points.
    assert _revision("c1") != stale
    assert get_task(None, "c1")["title"] == "Someone else moved it"
    assert get_task(None, "c2")["depends_on"] == ["c1"]


def test_fenced_delete_refuses_a_non_int_fencing_token(postgres_dsn):
    _seeded()
    with pytest.raises(TypeError):
        delete_task(None, "c1", expected_revision="0")
    with pytest.raises(TypeError):
        delete_task(None, "c1", expected_revision=True)


# --------------------------------------------------------------------------
# the mirror-level multi-row guards behind the verbs
# --------------------------------------------------------------------------


def test_mirror_refuses_a_guard_on_a_row_the_write_does_not_touch(postgres_dsn):
    import os

    from scitex_cards._db import connect
    from scitex_cards._db_mirror import mirror_doc_incremental

    _seeded()
    doc = {"tasks": [get_task(None, "c1")]}
    conn = connect(os.environ["SCITEX_STORE_DSN"])
    try:
        with pytest.raises(ValueError):
            mirror_doc_incremental(
                doc,
                os.environ["SCITEX_STORE_DSN"],
                conn=conn,
                touched_ids=["c1"],
                expected_revisions={"ghost": 3},
            )
    finally:
        conn.rollback()
        conn.close()


def test_mirror_refuses_combined_guard_forms(postgres_dsn):
    import os

    from scitex_cards._db import connect
    from scitex_cards._db_mirror import mirror_doc_incremental

    _seeded()
    doc = {"tasks": [get_task(None, "c1")]}
    conn = connect(os.environ["SCITEX_STORE_DSN"])
    try:
        with pytest.raises(ValueError):
            mirror_doc_incremental(
                doc,
                os.environ["SCITEX_STORE_DSN"],
                conn=conn,
                touched_ids=["c1"],
                expected_revision=0,
                expected_revisions={"c1": 0},
            )
    finally:
        conn.rollback()
        conn.close()


def test_absent_guard_reports_the_winner_instead_of_exploding(postgres_dsn):
    import os

    from scitex_cards._db import connect
    from scitex_cards._db_bootstrap import _insert_tasks

    _seeded()
    winner_rev = _revision()
    conn = connect(os.environ["SCITEX_STORE_DSN"])
    try:
        counts = _insert_tasks(
            conn,
            [
                {
                    "id": "c1",
                    "title": "Loser",
                    "status": "deferred",
                    "payload": "{}",
                }
            ],
            expect_absent=True,
        )
    finally:
        conn.rollback()
        conn.close()
    assert counts.get("revision_skipped") == 1
    assert counts.get("revision_found") == winner_rev


def test_a_true_commit_race_refuses_gracefully(postgres_dsn):
    """Two fenced creates, one id, genuinely overlapping in one transaction.

    The loser's INSERT must lose on the PRIMARY KEY and be reported as a
    skip — never explode as a UNIQUE violation mid-transaction. The
    interleaving is forced, not hoped for: the winner's connection holds
    ``LOCK TABLE tasks IN EXCLUSIVE MODE`` (SELECTs pass, the loser's
    INSERT blocks), writes the winner, waits until the loser is actually
    blocked in its INSERT (bounded poll — and if the poll never fires the
    loser simply takes the sequential path, whose end state is asserted
    identically), then commits. Either interleaving ends the same way, so
    the test cannot flake on timing.
    """
    import os
    import threading
    import time

    from scitex_cards._db import connect
    from scitex_cards._mirror_rows import _write_card

    dsn = os.environ["SCITEX_STORE_DSN"]
    winner = {"id": "c-race", "title": "Winner", "status": "deferred"}
    outcome: dict = {}

    def _loser():
        try:
            add_task(
                None,
                id="c-race",
                title="Loser",
                status="deferred",
                assignee="tester",
                created_by="tester",
                expected_revision=0,
            )
        except RevisionConflictError as exc:
            outcome["refused"] = exc
        except Exception as exc:  # noqa: BLE001 — any other outcome fails below
            outcome["exploded"] = exc

    main_conn = connect(dsn)
    try:
        main_conn.execute("LOCK TABLE tasks IN EXCLUSIVE MODE")
        thread = threading.Thread(target=_loser, daemon=True)
        thread.start()
        # Give the loser time to reach its INSERT and block there (it needs
        # ~100ms of SELECTs first). How far it gets does not matter: blocked
        # in INSERT it takes the SAVEPOINT path, still in SELECTs it takes
        # the sequential path, and both end in the same refusal asserted
        # below. Bounded so a stuck loser cannot wedge the suite.
        deadline = time.time() + 2
        while time.time() < deadline and not outcome and thread.is_alive():
            time.sleep(0.05)
        counts = _write_card(main_conn, dict(winner))
        assert not counts.get("revision_skipped")
        main_conn.commit()
        thread.join(timeout=30)
    finally:
        try:
            main_conn.rollback()
        finally:
            main_conn.close()
    assert thread.is_alive() is False, "loser thread never finished"
    assert "exploded" not in outcome, (
        f"loser exploded instead of refusing: {outcome.get('exploded')!r}"
    )
    refused = outcome.get("refused")
    assert isinstance(refused, RevisionConflictError), (
        f"loser was not refused: {outcome!r}"
    )
    assert refused.found == _revision("c-race")
    live = get_task(None, "c-race")
    assert live["title"] == "Winner"


# EOF
