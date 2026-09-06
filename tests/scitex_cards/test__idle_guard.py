#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop-hook idle guard — block silent abandonment of claimed (in_progress) work.

Real fakes, NO mocks: plain task dicts, a real tmp_path store for the
load-path, an injected ``now``. AAA.
"""

from __future__ import annotations

import datetime as _dt
import io

import pytest

from scitex_cards import _idle_guard

_NOW = _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture(autouse=True)
def _hermetic_env(env):
    """Clear the ambient knobs these tests must decide for themselves.

    THE STORE VARIABLE IS DELIBERATELY NOT CLEARED. This list named the RETIRED
    twin of each of these three, so it only ever cleared legacy spellings and
    left the current ones alone — including the store variable that the root
    conftest pins at the scratch DB and that ``_store()`` below reads back. The
    rename turned each entry into its current-name counterpart, so the fixture
    began deleting the pinned store variable, and ``_store()`` raised KeyError
    on the very value the suite had just set for it.

    Clearing the other two is a real improvement over what the legacy-twin list
    actually achieved: an ambient ``SCITEX_CARDS_AGENT_ID`` (every agent
    container exports one) used to reach these tests untouched, because the
    only name being deleted was a spelling nothing sets any more.
    """
    for var in ("SCITEX_CARDS_STALE_ACTIVE_HOURS", "SCITEX_CARDS_AGENT_ID"):
        env.delete(var)


def _t(*, id, owner, status, hours_ago, now=_NOW):
    last = (now - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": id,
        "title": f"card {id}",
        "status": status,
        "agent": owner,
        "last_activity": last,
    }


# === stale_in_progress — the abandonment set =================================


def test_only_stale_in_progress_for_the_agent():
    # Arrange
    tasks = [
        _t(id="hot", owner="alice", status="in_progress", hours_ago=10),  # stale → IN
        _t(
            id="fresh", owner="alice", status="in_progress", hours_ago=0.1
        ),  # fresh → out
        _t(id="blkd", owner="alice", status="blocked", hours_ago=10),  # parked → out
        _t(id="pend", owner="alice", status="pending", hours_ago=99),  # backlog → out
        _t(
            id="other", owner="bob", status="in_progress", hours_ago=10
        ),  # not alice → out
    ]
    # Act
    cards = _idle_guard.stale_in_progress("alice", tasks, now=_NOW, stale_hours=2.0)
    # Assert
    assert [c.id for c in cards] == ["hot"]


def test_no_agent_yields_empty():
    # Arrange
    tasks = [_t(id="x", owner="alice", status="in_progress", hours_ago=10)]
    # Act
    cards = _idle_guard.stale_in_progress("", tasks, now=_NOW, stale_hours=2.0)
    # Assert — work that cannot be attributed cannot be claimed abandoned.
    assert cards == []


# === evaluate — (block, reason) ==============================================


def _store(tmp_path, tasks):
    # The store is the database now; load_tasks reads it and treats the path
    # as a label only. Seed the pinned canonical DB from the in-memory doc and
    # return the STORE-identity path (NOT the DB path — see the migration
    # playbook's store-path rule) for callers to pass as ``store=`` / the store
    # env var. ``tmp_path`` is retained for the call signature; nothing is
    # written under it any more.
    import os

    from conftest import seed_db_from_doc

    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def test_evaluate_blocks_on_stale_in_progress(tmp_path):
    # Arrange
    store = _store(
        tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)]
    )
    # Act
    block, _reason = _idle_guard.evaluate(
        "alice", store=store, now=_NOW, stale_hours=2.0
    )
    # Assert
    assert block is True


def test_evaluate_reason_names_the_stale_card(tmp_path):
    # Arrange
    store = _store(
        tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)]
    )
    # Act
    _block, reason = _idle_guard.evaluate(
        "alice", store=store, now=_NOW, stale_hours=2.0
    )
    # Assert — "you have stale work" without WHICH card is unactionable.
    assert "c1" in reason


def test_evaluate_reason_says_do_not_stop(tmp_path):
    # Arrange
    store = _store(
        tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)]
    )
    # Act
    _block, reason = _idle_guard.evaluate(
        "alice", store=store, now=_NOW, stale_hours=2.0
    )
    # Assert — the Stop hook reads this text; the instruction must be explicit.
    assert "DO NOT STOP" in reason


def test_evaluate_allows_when_no_stale_in_progress(tmp_path):
    # Arrange
    store = _store(
        tmp_path,
        [
            _t(id="fresh", owner="alice", status="in_progress", hours_ago=0.1),
            _t(id="pend", owner="alice", status="pending", hours_ago=99),
        ],
    )
    # Act
    block, _reason = _idle_guard.evaluate(
        "alice", store=store, now=_NOW, stale_hours=2.0
    )
    # Assert
    assert block is False


def test_evaluate_reason_is_empty_when_allowing(tmp_path):
    # Arrange
    store = _store(
        tmp_path,
        [
            _t(id="fresh", owner="alice", status="in_progress", hours_ago=0.1),
            _t(id="pend", owner="alice", status="pending", hours_ago=99),
        ],
    )
    # Act
    _block, reason = _idle_guard.evaluate(
        "alice", store=store, now=_NOW, stale_hours=2.0
    )
    # Assert — an allowed stop must not print a reason nobody needs to read.
    assert reason == ""


# === main — Stop-hook exit codes =============================================


@pytest.fixture
def silent_stdin():
    """Point `sys.stdin` at a REAL empty stream for the duration of a test.

    `main()` reads the Stop-hook payload from stdin; under pytest that is a
    captured object whose read blocks or errors. An empty `io.StringIO` is a
    genuine stream, not a stand-in that fakes behaviour — this is the same
    shape as the `redirect_stdout` / `redirect_stderr` context managers the
    standard library ships, which is why it needs no patching fixture. Prior
    stdin is restored on teardown.
    """
    prior = _idle_guard.sys.stdin
    _idle_guard.sys.stdin = io.StringIO("")
    try:
        yield
    finally:
        _idle_guard.sys.stdin = prior


def test_main_blocks_with_exit_2(tmp_path, env, silent_stdin, capsys):
    # Arrange
    store = _store(
        tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)]
    )
    env.set("SCITEX_CARDS_TASKS_YAML_SHARED", str(store))
    env.set("SCITEX_CARDS_STALE_ACTIVE_HOURS", "2")
    # Act
    rc = _idle_guard.main(["--agent", "alice"])
    # Assert — exit 2 is the Stop hook's "refuse to stop" code.
    assert rc == 2


def test_main_names_the_stale_card_on_stderr(tmp_path, env, silent_stdin, capsys):
    # Arrange
    store = _store(
        tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)]
    )
    env.set("SCITEX_CARDS_TASKS_YAML_SHARED", str(store))
    env.set("SCITEX_CARDS_STALE_ACTIVE_HOURS", "2")
    # Act
    _idle_guard.main(["--agent", "alice"])
    stderr = capsys.readouterr().err
    # Assert — the blocked agent is told WHICH card it is holding.
    assert "c1" in stderr


def test_main_allows_with_exit_0(tmp_path, env, silent_stdin):
    # No in_progress card → no claimed work to abandon → allow stop. Uses only a
    # pending card so the result is independent of the wall clock (main() reads
    # the real `now`, so an in_progress fixture anchored to a fixed past time
    # would flake once it aged past the stale threshold).
    # Arrange
    store = _store(
        tmp_path, [_t(id="pend", owner="alice", status="pending", hours_ago=99)]
    )
    env.set("SCITEX_CARDS_TASKS_YAML_SHARED", str(store))
    # Act
    rc = _idle_guard.main(["--agent", "alice"])
    # Assert
    assert rc == 0


def test_main_no_agent_allows(tmp_path, silent_stdin):
    # No --agent, no SCITEX_CARDS_AGENT_ID → cannot attribute work → allow stop.
    # Arrange
    # Act
    rc = _idle_guard.main([])
    # Assert
    assert rc == 0


def test_main_failsoft_allows_on_error(env, silent_stdin):
    # A broken store makes the load raise; the guard must NOT trap (exit 0).
    # Against the database store the failure mode is a MISSING canonical DB, not a
    # missing YAML file — the store path is a label now and a broken path is read
    # as the (empty) DB — so point $SCITEX_CARDS_DB at a database that does not
    # exist; the canonical read raises RuntimeError, which the guard fails soft on.
    # Arrange
    env.set("SCITEX_CARDS_DB", "/no/such/dir/cards.db")
    # Act
    rc = _idle_guard.main(["--agent", "alice"])
    # Assert — a guard that traps the agent on its own bug is worse than no guard.
    assert rc == 0


# EOF
