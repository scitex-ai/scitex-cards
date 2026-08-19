#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A transaction that LOST a race is retried; anything else is re-raised.

MEASURED, TWICE, ON THE LIVE BOARD (2026-08-18):

* handyman-03 ran two concurrent ``scitex-cards comment`` calls and one died
  with ``psycopg.errors.DeadlockDetected`` (SQLSTATE 40P01) — ``tasks`` and
  ``task_comments`` taken in opposite order by the two transactions. The
  IDENTICAL write succeeded seven minutes later.
* sac lost 14 of 178 SERIAL writes, and all 14 persisted on retry with
  unchanged input.

A deadlock victim is safe to retry by definition: Postgres rolls the loser
back in full, so there is no half-applied state to reason about. The mirror is
an upsert keyed by card id, so a repeat is idempotent even if the first
attempt had got part way.

WHAT THIS MUST NOT BECOME. ``_store_backend``'s module docstring is explicit
that the write path "must NOT take the mirror's best-effort, never-raise
posture: a failed write MUST raise and the caller MUST see it." The retry
obeys that: it re-raises everything it does not recognise, and re-raises the
retryable ones too once the attempts are spent. The tests below pin BOTH
halves, because a retry that quietly swallowed a real failure would be a far
worse defect than the one it fixes.

The fake errors carry ``sqlstate`` because that is exactly what the predicate
reads — psycopg sets it on every database error and SQLite errors have no such
attribute, so matching the CODE keeps a psycopg import out of a module that
must work on a sqlite-only install.
"""

from __future__ import annotations

import pytest

from scitex_cards import _store_backend


class _Boom(Exception):
    """A database error, carrying a SQLSTATE like psycopg's do."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"simulated {sqlstate}")
        self.sqlstate = sqlstate


class _MirrorThatFailsThenWorks:
    """A real callable with ``mirror_doc_incremental``'s contract.

    Raises ``failures`` times, then returns a summary. Counts its calls, so a
    test can state how many attempts actually happened rather than infer it.
    """

    def __init__(self, sqlstate: str, failures: int) -> None:
        self._sqlstate = sqlstate
        self._failures = failures
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise _Boom(self._sqlstate)
        return {"changed": 1, "removed": 0, "unchanged": 0, "full": False}


def _write(mirror):
    """Drive the retry loop with ``mirror`` standing in for the real one."""
    return _store_backend._retrying_mirror_write(
        mirror,
        {"tasks": []},
        "postgresql://example/db",
        deleted_ids=None,
        touched_ids=None,
        expected_revision=None,
    )


def test_a_deadlock_victim_is_retried_and_succeeds():
    # Arrange — one 40P01, exactly the code handyman-03 measured.
    mirror = _MirrorThatFailsThenWorks("40P01", failures=1)

    # Act
    summary = _write(mirror)

    # Assert
    assert summary["changed"] == 1


def test_the_retry_actually_re_ran_the_write():
    """The control: prove the success came from a SECOND attempt.

    Without this, a mirror that never failed would satisfy the test above and
    report the retry as working while it had nothing to recover from.
    """
    # Arrange
    mirror = _MirrorThatFailsThenWorks("40P01", failures=1)

    # Act
    _write(mirror)

    # Assert
    assert mirror.calls == 2


def test_a_serialization_failure_is_retried_too():
    # Arrange — 40001 is the same class of lost race.
    mirror = _MirrorThatFailsThenWorks("40001", failures=1)

    # Act
    summary = _write(mirror)

    # Assert
    assert summary["changed"] == 1


@pytest.fixture()
def refused_ordinary_write():
    """A 23505 unique_violation — a real, deterministic refusal.

    Anything without a retryable SQLSTATE must reach the caller AT ONCE. A
    write path that retried real failures would turn one loud error into three
    slow ones and still fail — and, worse, would invite widening the predicate
    until it swallowed something.
    """
    mirror = _MirrorThatFailsThenWorks("23505", failures=1)
    with pytest.raises(_Boom):
        _write(mirror)
    return mirror


def test_an_ordinary_error_reaches_the_caller(refused_ordinary_write):
    # Arrange
    # Act
    # Assert — the fixture's `pytest.raises` is the assertion: the error got out.
    assert refused_ordinary_write.calls >= 1


def test_an_ordinary_error_is_not_retried(refused_ordinary_write):
    # Arrange
    # Act
    # Assert — one attempt, not three.
    assert refused_ordinary_write.calls == 1


def test_an_error_with_no_sqlstate_is_re_raised():
    # Arrange — SQLite errors have no `sqlstate`; nothing may retry on them.
    def _plain_failure(*args, **kwargs):
        raise RuntimeError("disk went away")

    # Act
    # Assert
    with pytest.raises(RuntimeError):
        _write(_plain_failure)


@pytest.fixture()
def unrelenting_contention():
    """Fails more times than the loop will try.

    The attempts are BOUNDED and the last failure reaches the caller. If a
    card cannot be written in three tries the contention is a problem to
    report, not to outwait — and silence here would be the exact failure this
    package spent the day removing.
    """
    mirror = _MirrorThatFailsThenWorks("40P01", failures=99)
    with pytest.raises(_Boom):
        _write(mirror)
    return mirror


def test_persistent_contention_still_raises(unrelenting_contention):
    # Arrange
    # Act
    # Assert — the fixture's `pytest.raises` caught it; the caller was told.
    assert unrelenting_contention.calls >= 1


def test_persistent_contention_stops_at_the_attempt_limit(unrelenting_contention):
    # Arrange
    # Act
    # Assert — bounded, not unbounded.
    assert unrelenting_contention.calls == _store_backend._WRITE_ATTEMPTS


# EOF
