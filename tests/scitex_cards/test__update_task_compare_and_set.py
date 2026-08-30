#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``update_task(..., expected_revision=N)`` -- the opt-in compare-and-set.

``_insert_tasks`` has had a working compare-and-set since PR #790 and
``_write_card`` forwards to it, but nothing above them could ask for it: the doc
path carried no parameter, so the guard existed and was unreachable from every
public verb. #790 refused the argument on ``update_task`` because that function
was a whole-document read-modify-write and a per-row guard would have overwritten
every other card from the same read. #872 made it declare ``touched_ids`` and the
mirror intersects the write set with that, so the write reaches exactly one row
and the premise is gone.

TWO HAZARDS HERE WERE FOUND BY TESTS, NOT BY READING, and both fail silently:

1. THE HASH TABLE. The mirror records a content hash per card so the next write
   can skip an unchanged one. Recording a REFUSED card's new hash would leave the
   mirror believing the row holds content it does not -- and the caller's natural
   response to a refusal (re-read, re-apply, retry) computes that same hash, is
   judged unchanged, and writes NOTHING. Permanently, with no exception anywhere.

2. THE FIRST-RUN FULL REBUILD. With no hash table yet the mirror rebuilds every
   card from the caller's doc and returns early -- before any guard. Silently
   ignoring ``expected_revision`` there is worse than not offering it: the caller
   believes they hold a per-row lock while the whole board is overwritten from
   their snapshot.

RAISE vs REPORT is keyed on THE OPT-IN, not on the layer. Passing a revision is
an assertion, and a violated explicit assertion that returns quietly is the
invisible lost update. A bulk reconciler that supplied nothing is counting
ordinary concurrency, where an exception would be slow and would mislabel a
routine outcome as a fault. So this verb raises; the mirror still reports.

Real the retired engine throughout: ``revision`` only moves because v7's
``tasks_bump_revision`` trigger moves it, so a mocked store would mock away the
one thing under test.
"""

from __future__ import annotations

from functools import partial

import pytest

from scitex_cards._store import add_task, get_task, update_task
from scitex_cards._store_errors import RevisionConflictError


def _revision(task_id="c1"):
    """The row's current revision, read straight from the isolated database.

    NO ``store`` ARGUMENT, DELIBERATELY. conftest's autouse fixture gives every
    test its own empty database and pins ``$SCITEX_CARDS_DB`` at it, and its
    docstring records that a test passing its OWN ``tmp_path`` store is refused
    by a database already stamped for a different one. So the ambient store IS
    the isolation here; naming one would fight it.

    ANTI-VACUITY: raises rather than returning None when the row is missing. A
    None would be passed as ``expected_revision=None``, which emits NO GUARD --
    so every test would write successfully and pass while asserting nothing
    about the compare-and-set. That is exactly what the first run of this file
    did: two tests went green with the guard never once engaged.
    """
    import os

    from scitex_cards._db import connect

    db = os.environ["SCITEX_CARDS_DB"]
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT revision FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["revision"] is None:
        raise AssertionError(
            f"no revision for task {task_id!r} in {db} -- a None would be passed "
            f"as expected_revision and silently disable the guard under test, "
            f"which is a vacuous pass rather than a failure."
        )
    return row["revision"]


@pytest.fixture()
def seeded():
    """One card in the test's own isolated store."""
    add_task(id="c1", title="Original", status="deferred", assignee="tester",
             created_by="tester")
    return "c1"


@pytest.fixture()
def contended(seeded):
    """A card a concurrent writer has already moved past the caller's revision."""
    stale = _revision()
    update_task(None, "c1", title="Winner")
    return stale


@pytest.fixture()
def refusal(contended):
    """The refused write, already performed. Returns (stale, exception)."""
    stale = contended
    try:
        update_task(None, "c1", title="Loser", expected_revision=stale)
    except RevisionConflictError as exc:
        return stale, exc
    raise AssertionError("the stale write was NOT refused")


# --------------------------------------------------------------------------
# the guard is reachable, and opting out changes nothing
# --------------------------------------------------------------------------


def test_a_write_holding_the_current_revision_lands(seeded):
    # Arrange
    current = _revision()
    # Act
    merged = update_task(None, "c1", title="Updated", expected_revision=current)
    # Assert
    assert merged["title"] == "Updated"


def test_omitting_the_guard_still_writes(seeded):
    """The opt-in property. REJECT-by-default was ruled unusable for a
    mixed-version fleet (`_migrate_v6_to_v7`)."""
    # Arrange
    call = partial(update_task, None, "c1")
    # Act
    merged = call(title="Plain")
    # Assert
    assert merged["title"] == "Plain"


# --------------------------------------------------------------------------
# a lost race RAISES here, because the caller asserted a revision
# --------------------------------------------------------------------------


def test_a_stale_revision_is_refused(contended):
    # Arrange
    stale = contended
    call = partial(update_task, None, "c1", title="Loser", expected_revision=stale)
    # Act
    raised = pytest.raises(RevisionConflictError)
    # Assert
    with raised:
        call()


def test_the_refused_write_changes_nothing(refusal):
    # Arrange
    _stale, _exc = refusal
    # Act
    current = get_task(None, "c1")
    # Assert
    assert current["title"] == "Winner"


def test_the_error_names_the_card(refusal):
    """A reconciler retries the losers; it cannot retry a row it cannot name."""
    # Arrange
    _stale, exc = refusal
    # Act
    named = exc.task_id
    # Assert
    assert named == "c1"


def test_the_error_carries_the_revision_the_store_actually_holds(refusal):
    """So the caller can log the gap without a second query."""
    # Arrange
    _stale, exc = refusal
    # Act
    found = exc.found
    # Assert
    assert found == _revision()


def test_the_error_carries_the_revision_the_caller_expected(refusal):
    # Arrange
    stale, exc = refusal
    # Act
    expected = exc.expected
    # Assert
    assert expected == stale


def test_the_error_says_retrying_this_payload_cannot_succeed(refusal):
    """The message has to stop the obvious wrong move -- retrying verbatim --
    which is exactly what the hash hazard would have made permanent."""
    # Arrange
    _stale, exc = refusal
    # Act
    message = str(exc)
    # Assert
    assert "cannot succeed" in message


# --------------------------------------------------------------------------
# HAZARD 1: the refused write must not poison the hash table
# --------------------------------------------------------------------------


def test_a_retry_after_a_refusal_can_actually_land(refusal):
    """THE TEST THAT GOES RED WITHOUT THE FIX.

    If the refused write had recorded its content hash, this retry -- with the
    CORRECT revision -- would compute that same hash, be judged unchanged, and
    write nothing. Forever, silently, with the caller believing they recovered.
    """
    # Arrange
    _stale, _exc = refusal
    # Act
    merged = update_task(None, "c1", title="Loser", expected_revision=_revision())
    # Assert
    assert merged["title"] == "Loser"


# --------------------------------------------------------------------------
# HAZARD 2: the guard must not be silently dropped on a full rebuild
# --------------------------------------------------------------------------


def test_the_guard_is_refused_on_a_first_run_full_rebuild(new_store):
    """A full rebuild rewrites every card from the caller's doc, so it cannot
    honour a per-row guard. Refusing is the only honest option -- ignoring it
    would overwrite the board while the caller believed they held a lock.

    ``bootstrap=False`` is what makes this the FIRST-RUN path: the rebuild is
    chosen because the store holds no row hashes to diff against, and a
    provisioned store would already carry them."""
    # Arrange
    from scitex_cards._db_mirror import mirror_doc_incremental

    doc = {"tasks": [{"id": "c1", "title": "A", "status": "deferred"}]}
    fresh = new_store("cards_cas_fresh", bootstrap=False)
    # Act
    call = partial(mirror_doc_incremental, doc, fresh, expected_revision=0)
    # Assert
    with pytest.raises(ValueError, match="full rebuild"):
        call()
