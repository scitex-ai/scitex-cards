#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the PR-merge card-freshness automation (`_reconcile_prs`).

No mocks (STX-NM / PA-306): the decision core is a pure function over
``(task, merge_state)``; the URL parser is pure; the orchestration uses a
real temp ``tasks.yaml`` and injects a FAKE merge-state callable via the
documented ``merge_state_fn=`` seam (NOT a network mock). AAA pattern.
"""

from __future__ import annotations

import os
import textwrap

import pytest

from scitex_cards._model import load_tasks
from scitex_cards._reconcile_prs import (
    ACTION_CLOSE,
    ACTION_SKIP_DONE,
    ACTION_SKIP_NO_PR,
    ACTION_SKIP_NOT_MERGED,
    ACTION_SKIP_NOT_OPEN,
    ACTION_SKIP_UNKNOWN,
    MERGED,
    OPEN,
    UNKNOWN,
    PrRef,
    decide_reconcile_action,
    parse_pr_url,
    reconcile_merged_prs,
)

# === parse_pr_url (pure) ==================================================


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/foo/bar/pull/42", PrRef("foo", "bar", 42)),
        ("http://github.com/o/r/pull/7/files", PrRef("o", "r", 7)),
        ("https://github.com/a-b/c.d/pull/123", PrRef("a-b", "c.d", 123)),
        ("git@github.com:o/r/pull/9", PrRef("o", "r", 9)),
    ],
)
def test_parse_pr_url_extracts_owner_repo_number(url, expected):
    # Arrange
    raw = url
    # Act
    ref = parse_pr_url(raw)
    # Assert
    assert ref == expected


@pytest.mark.parametrize(
    "url",
    ["", None, "https://github.com/foo/bar/issues/1", "not a url", "github.com/foo"],
)
def test_parse_pr_url_returns_none_for_unparseable(url):
    # Arrange
    raw = url
    # Act
    ref = parse_pr_url(raw)
    # Assert — unparseable is None, never a guessed PrRef.
    assert ref is None


# === decide_reconcile_action (pure) ======================================


def test_decide_close_when_open_card_with_merged_pr():
    # Arrange
    task = {"status": "in_progress", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_CLOSE


def test_decide_skip_done_for_already_done_card():
    # Arrange
    task = {"status": "done", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_DONE


def test_decide_skip_no_pr_when_pr_url_missing():
    # Arrange
    task = {"status": "in_progress"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_NO_PR


def test_decide_skip_no_pr_when_pr_url_unparseable():
    # Arrange
    task = {"status": "blocked", "pr_url": "not-a-pr-url"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_NO_PR


def test_decide_skip_not_open_for_deferred_card():
    # Arrange
    task = {"status": "deferred", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, MERGED)
    # Assert
    assert action == ACTION_SKIP_NOT_OPEN


def test_decide_skip_not_merged_when_pr_open():
    # Arrange
    task = {"status": "in_progress", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, OPEN)
    # Assert
    assert action == ACTION_SKIP_NOT_MERGED


def test_decide_skip_unknown_is_fail_soft():
    # Arrange
    task = {"status": "in_progress", "pr_url": "https://github.com/o/r/pull/1"}
    # Act
    action = decide_reconcile_action(task, UNKNOWN)
    # Assert — merge-state could not be determined, so it MUST NOT close.
    assert action == ACTION_SKIP_UNKNOWN


# === reconcile_merged_prs (real temp store + injected seam) ===============


def _store(tmp_path):
    """Seed the canonical DB from a small fixture doc; return the STORE path.

    The store is SQLite now: ``reconcile_merged_prs`` / ``load_tasks`` read and
    write the canonical DB and ignore the path (it survives only as the store
    identity). The fixture is still authored as readable YAML text; parse it,
    seed the DB, and return the pinned STORE identity path (NOT the DB path —
    a write stamped with any other path fails the next read).
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = (
        safe_load(
            textwrap.dedent(
                """\
                tasks:
                  - id: merged-open-card
                    title: work that merged
                    status: in_progress
                    pr_url: https://github.com/o/r/pull/1
                  - id: open-pr-card
                    title: still in review
                    status: blocked
                    pr_url: https://github.com/o/r/pull/2
                  - id: already-done-card
                    title: done long ago
                    status: done
                    pr_url: https://github.com/o/r/pull/3
                  - id: no-pr-card
                    title: no linked pr
                    status: in_progress
                  - id: unknown-pr-card
                    title: state unknowable
                    status: pending
                    pr_url: https://github.com/o/r/pull/5
                """
            )
        )
        or {}
    )
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _fake_seam(mapping):
    """Return a merge_state_fn that looks merge-state up by pr_url."""

    def _fn(pr_url):
        return mapping.get(pr_url, UNKNOWN)

    return _fn


#: The full merge-state map for the fixture store: card 1 merged, card 2 still
#: open, card 5 unknowable. Used by the dry-run and apply paths alike so the two
#: only differ in the ``apply=`` flag.
_ALL_STATES = {
    "https://github.com/o/r/pull/1": MERGED,
    "https://github.com/o/r/pull/2": OPEN,
    "https://github.com/o/r/pull/5": UNKNOWN,
}


#: A dry run over that whole map. Returns ``(path, result)``. The three tests
#: below split what one test asserted about it — that the merged-open card is
#: REPORTED, that nothing was closed, and that the store on disk is untouched.
#: The last is the one that actually makes it a dry run; the first two are how
#: it stays useful. A single test could have passed on two of the three.
def _dry_run(tmp_path):
    path = _store(tmp_path)
    seam = _fake_seam(_ALL_STATES)
    return path, reconcile_merged_prs(path, apply=False, merge_state_fn=seam)


def test_dry_run_reports_the_merged_card_as_a_candidate(tmp_path):
    # Arrange
    _path, result = _dry_run(tmp_path)
    # Act
    ids = [c["id"] for c in result.would_close]
    # Assert
    assert ids == ["merged-open-card"]


def test_dry_run_closes_nothing(tmp_path):
    # Arrange
    _path, result = _dry_run(tmp_path)
    # Act
    closed = result.closed
    # Assert
    assert closed == []


def test_dry_run_leaves_the_store_untouched(tmp_path):
    # Arrange
    path, _result = _dry_run(tmp_path)
    # Act
    statuses = {t["id"]: t["status"] for t in load_tasks(path)}
    # Assert — the candidate is still open on disk; nothing was written.
    assert statuses["merged-open-card"] == "in_progress"


def test_apply_closes_merged_card_and_comments(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam(_ALL_STATES)
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert — only the merged card flipped to done.
    tasks = {t["id"]: t for t in load_tasks(path)}
    assert tasks["merged-open-card"]["status"] == "done"


def test_apply_leaves_open_pr_card_untouched(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/2": OPEN})
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    tasks = {t["id"]: t for t in load_tasks(path)}
    assert tasks["open-pr-card"]["status"] == "blocked"


def test_apply_appends_auto_close_comment(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    tasks = {t["id"]: t for t in load_tasks(path)}
    comments = tasks["merged-open-card"].get("comments") or []
    assert any("auto-closed" in (c.get("text") or "") for c in comments)


def test_already_done_card_is_skipped(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/3": MERGED})
    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert — even with its PR merged, a done card is never re-touched.
    assert result.skipped.get(ACTION_SKIP_DONE, 0) >= 1


def test_missing_pr_url_card_is_skipped(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({})
    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert
    assert result.skipped.get(ACTION_SKIP_NO_PR, 0) >= 1


def test_unknown_merge_state_never_closes(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/5": UNKNOWN})
    # Act
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert — fail-soft: an unknown state leaves the card open.
    tasks = {t["id"]: t for t in load_tasks(path)}
    assert tasks["unknown-pr-card"]["status"] == "pending"


def test_seam_exception_is_treated_as_unknown(tmp_path):
    # Arrange
    path = _store(tmp_path)

    def _boom(pr_url):
        raise RuntimeError("network down")

    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=_boom)
    # Assert — a raising seam must NOT close, fail-soft over the whole call.
    assert result.closed == []


def test_idempotent_second_run_closes_nothing_new(tmp_path):
    # Arrange
    path = _store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Act
    result = reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    # Assert — the second pass finds the card already done.
    assert result.closed == []


# EOF
