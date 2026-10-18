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

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. Multi-step scenarios live in
``_`` helpers; each test asserts exactly one outcome.

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


def _refused_create_exc(title="Loser", expected_revision=0):
    """Seed c1, lose a fenced create against it, return the refusal."""
    _seeded()
    with pytest.raises(RevisionConflictError) as exc_info:
        _seeded(title=title, expected_revision=expected_revision)
    return exc_info.value


def _winner_and_loser():
    """A winner (comment + retitle) plus a refused fenced create.

    Returns the winner's revision: every assertion about the refused
    write's blast radius hangs off this one scenario.
    """
    from scitex_cards._store import comment_task, update_task

    _seeded()
    comment_task(None, "c1", text="winner's comment", by="tester")
    update_task(None, "c1", title="Winner")
    winner_rev = _revision()
    with pytest.raises(RevisionConflictError):
        _seeded(title="Loser", note="stale", expected_revision=0)
    return winner_rev


def _tombstone_and_refuse():
    """Seed, tombstone, refuse a fenced resurrect; return (exc, tomb_rev)."""
    _seeded()
    delete_task(None, "c1")
    tomb_rev = _revision()
    with pytest.raises(RevisionConflictError) as exc_info:
        _seeded(title="Resurrect", expected_revision=0)
    return exc_info.value, tomb_rev


def _stale_delete_exc():
    """Move c1, lose a fenced delete on the stale revision; return all three."""
    from scitex_cards._store import update_task

    _seeded()
    stale = _revision()
    update_task(None, "c1", title="Someone else moved it")
    current = _revision()
    with pytest.raises(RevisionConflictError) as exc_info:
        delete_task(None, "c1", expected_revision=stale)
    return exc_info.value, stale, current


def _refused_delete_with_dependent():
    """A lost fenced delete with a dependent; return the stale revision."""
    from scitex_cards._store import update_task

    _seeded()
    _seeded("c2", title="Dependent", depends_on=["c1"])
    stale = _revision("c1")
    update_task(None, "c1", title="Someone else moved it")
    with pytest.raises(RevisionConflictError):
        delete_task(None, "c1", expected_revision=stale)
    return stale


def _mirror_outcome(doc, **guards):
    """Run one guarded incremental mirror; report the outcome as a string.

    Returns ``"refused:<ExcName>"`` when the guard fires, ``"applied"``
    otherwise — a single string so the test holds one assertion while the
    connection is still unwound in ``finally``.
    """
    import os

    from scitex_cards._db import connect
    from scitex_cards._db_mirror import mirror_doc_incremental

    dsn = os.environ["SCITEX_STORE_DSN"]
    conn = connect(dsn)
    try:
        mirror_doc_incremental(doc, dsn, conn=conn, **guards)
    except Exception as exc:  # noqa: BLE001 -- the type name IS the outcome
        return f"refused:{type(exc).__name__}"
    finally:
        conn.rollback()
        conn.close()
    return "applied"


def _run_commit_race():
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

    Returns ``(outcome, race_rev)``: the loser's outcome dict plus the
    winner's revision for the tests to assert against.
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
        except Exception as exc:  # noqa: BLE001 -- any other outcome fails below
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
        if counts.get("revision_skipped"):
            raise AssertionError(
                "winner's own write was skipped -- the race setup is broken, "
                "not the fence."
            )
        main_conn.commit()
        thread.join(timeout=30)
    finally:
        try:
            main_conn.rollback()
        finally:
            main_conn.close()
    if thread.is_alive():
        raise AssertionError("loser thread never finished -- race wedged.")
    return outcome, _revision("c-race")


# --------------------------------------------------------------------------
# the surface exists and stays opt-in
# --------------------------------------------------------------------------


def test_add_task_accepts_a_keyword_only_expected_revision_defaulting_to_none():
    # Arrange
    params = inspect.signature(scitex_cards.add_task).parameters
    # Act
    fencing = params["expected_revision"]
    # Assert
    assert (
        fencing.kind is inspect.Parameter.KEYWORD_ONLY
        and fencing.default is None
    )


def test_delete_task_accepts_a_keyword_only_expected_revision_defaulting_to_none():
    # Arrange
    params = inspect.signature(scitex_cards.delete_task).parameters
    # Act
    fencing = params["expected_revision"]
    # Assert
    assert (
        fencing.kind is inspect.Parameter.KEYWORD_ONLY
        and fencing.default is None
    )


def test_unarmed_create_behaves_as_before(postgres_dsn):
    # Arrange
    # Act
    merged = _seeded()
    # Assert
    assert merged["title"] == "Original"


def test_unarmed_duplicate_create_still_raises_task_validation_error(
    postgres_dsn,
):
    # Arrange
    _seeded()
    # Act
    def act():
        _seeded(title="Second")

    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_unarmed_delete_returns_no_dependent_refs(postgres_dsn):
    # Arrange
    _seeded()
    # Act
    out = delete_task(None, "c1")
    # Assert
    assert out["refs"] == []


def test_unarmed_delete_makes_the_card_unreadable(postgres_dsn):
    # Arrange
    _seeded()
    delete_task(None, "c1")
    # Act
    def act():
        get_task(None, "c1")

    # Assert
    with pytest.raises(Exception):
        act()


# --------------------------------------------------------------------------
# fenced create: absence, or a loud refusal carrying the winner's revision
# --------------------------------------------------------------------------


def test_fenced_create_on_a_fresh_id_lands(postgres_dsn):
    # Arrange
    # Act
    merged = _seeded(expected_revision=0)
    # Assert
    assert merged["title"] == "Original"


def test_fenced_create_against_a_live_row_is_refused(postgres_dsn):
    # Arrange
    _seeded()
    # Act
    def act():
        _seeded(title="Loser", expected_revision=0)

    # Assert
    with pytest.raises(RevisionConflictError):
        act()


def test_the_refused_create_reports_the_winners_revision(postgres_dsn):
    # Arrange
    # Act
    exc = _refused_create_exc()
    # Assert
    assert exc.found == _revision()


def test_the_refused_create_leaves_the_winners_revision(postgres_dsn):
    # Arrange
    # Act
    winner_rev = _winner_and_loser()
    # Assert
    assert _revision() == winner_rev


def test_the_refused_create_leaves_the_winners_title(postgres_dsn):
    # Arrange
    # Act
    _winner_and_loser()
    # Assert
    assert get_task(None, "c1")["title"] == "Winner"


def test_the_refused_create_leaves_the_winners_comments(postgres_dsn):
    # Arrange
    # Act
    _winner_and_loser()
    # Assert
    assert any(
        c.get("text") == "winner's comment"
        for c in get_task(None, "c1").get("comments", [])
    )


def test_fenced_create_against_a_tombstoned_id_is_refused(postgres_dsn):
    # Arrange
    _seeded()
    delete_task(None, "c1")
    # Act
    def act():
        _seeded(title="Resurrect", expected_revision=0)

    # Assert
    with pytest.raises(RevisionConflictError):
        act()


def test_the_refused_create_reports_the_tombstones_revision(postgres_dsn):
    # Arrange
    # Act
    exc, tomb_rev = _tombstone_and_refuse()
    # Assert
    assert exc.found == tomb_rev


def test_the_refused_create_leaves_the_tombstone_intact(postgres_dsn):
    # Arrange
    # Act
    _tombstone_and_refuse()
    # Assert
    _card, _rev = read_card_or_raise(resolve_store_target(None), "c1")
    assert (_card["status"], bool(_card["_log_meta"]["deleted_at"])) == (
        "cancelled",
        True,
    )


def test_fenced_create_refuses_a_string_fencing_token(postgres_dsn):
    # Arrange
    # Act
    def act():
        _seeded(expected_revision="0")

    # Assert
    with pytest.raises(TypeError):
        act()


def test_fenced_create_refuses_a_bool_fencing_token(postgres_dsn):
    # Arrange
    # Act
    def act():
        _seeded(expected_revision=True)

    # Assert
    with pytest.raises(TypeError):
        act()


# --------------------------------------------------------------------------
# fenced delete: the tombstone lands only on the revision it asserts
# --------------------------------------------------------------------------


def test_fenced_delete_on_the_current_revision_returns_no_refs(postgres_dsn):
    # Arrange
    _seeded(depends_on=None)
    current = _revision()
    # Act
    out = delete_task(None, "c1", expected_revision=current)
    # Assert
    assert out["refs"] == []


def test_fenced_delete_on_the_current_revision_tombstones(postgres_dsn):
    # Arrange
    _seeded(depends_on=None)
    current = _revision()
    # Act
    delete_task(None, "c1", expected_revision=current)
    # Assert
    _card, _rev = read_card_or_raise(resolve_store_target(None), "c1")
    assert (_card["status"], _rev) == ("cancelled", current + 1)


def test_fenced_delete_on_a_stale_revision_is_refused(postgres_dsn):
    # Arrange
    _seeded()
    stale = _revision()
    from scitex_cards._store import update_task

    update_task(None, "c1", title="Someone else moved it")
    # Act
    def act():
        delete_task(None, "c1", expected_revision=stale)

    # Assert
    with pytest.raises(RevisionConflictError):
        act()


def test_the_refused_delete_reports_both_revisions(postgres_dsn):
    # Arrange
    # Act
    exc, stale, current = _stale_delete_exc()
    # Assert
    assert (exc.expected, exc.found) == (stale, current)


def test_the_refused_delete_leaves_the_target_live(postgres_dsn):
    # Arrange
    # Act
    stale = _refused_delete_with_dependent()
    # Assert
    assert _revision("c1") != stale


def test_the_refused_delete_keeps_the_winners_title(postgres_dsn):
    # Arrange
    # Act
    _refused_delete_with_dependent()
    # Assert
    assert get_task(None, "c1")["title"] == "Someone else moved it"


def test_the_refused_delete_keeps_the_dependent_pointing(postgres_dsn):
    # Arrange
    # Act
    _refused_delete_with_dependent()
    # Assert
    assert get_task(None, "c2")["depends_on"] == ["c1"]


def test_fenced_delete_refuses_a_string_fencing_token(postgres_dsn):
    # Arrange
    _seeded()
    # Act
    def act():
        delete_task(None, "c1", expected_revision="0")

    # Assert
    with pytest.raises(TypeError):
        act()


def test_fenced_delete_refuses_a_bool_fencing_token(postgres_dsn):
    # Arrange
    _seeded()
    # Act
    def act():
        delete_task(None, "c1", expected_revision=True)

    # Assert
    with pytest.raises(TypeError):
        act()


# --------------------------------------------------------------------------
# the mirror-level multi-row guards behind the verbs
# --------------------------------------------------------------------------


def test_mirror_refuses_a_guard_on_a_row_the_write_does_not_touch(
    postgres_dsn,
):
    # Arrange
    _seeded()
    doc = {"tasks": [get_task(None, "c1")]}
    # Act
    outcome = _mirror_outcome(
        doc, touched_ids=["c1"], expected_revisions={"ghost": 3}
    )
    # Assert
    assert outcome == "refused:ValueError"


def test_mirror_refuses_combined_guard_forms(postgres_dsn):
    # Arrange
    _seeded()
    doc = {"tasks": [get_task(None, "c1")]}
    # Act
    outcome = _mirror_outcome(
        doc,
        touched_ids=["c1"],
        expected_revision=0,
        expected_revisions={"c1": 0},
    )
    # Assert
    assert outcome == "refused:ValueError"


def test_absent_guard_reports_the_winner_instead_of_exploding(postgres_dsn):
    # Arrange
    _seeded()
    winner_rev = _revision()
    # Act
    import os

    from scitex_cards._db import connect
    from scitex_cards._db_bootstrap import _insert_tasks

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
    # Assert
    assert (counts.get("revision_skipped"), counts.get("revision_found")) == (
        1,
        winner_rev,
    )


def test_a_true_commit_race_refuses_the_loser_gracefully(postgres_dsn):
    # Arrange
    # Act
    outcome, _race_rev = _run_commit_race()
    # Assert
    assert isinstance(outcome.get("refused"), RevisionConflictError) and (
        "exploded" not in outcome
    ), f"loser was not cleanly refused: {outcome!r}"


def test_a_true_commit_race_reports_the_winners_revision(postgres_dsn):
    # Arrange
    # Act
    outcome, race_rev = _run_commit_race()
    # Assert
    assert outcome["refused"].found == race_rev


def test_a_true_commit_race_leaves_the_winner_live(postgres_dsn):
    # Arrange
    # Act
    _run_commit_race()
    # Assert
    assert get_task(None, "c-race")["title"] == "Winner"


# EOF
