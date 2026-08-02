#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C6 — git-link event producers (emit committed/pushed/merged card-events).

The card-event/notification foundation epic, card
``cenf-c6-gitlink-event-producers-20260626``. Wires the two existing
git-fact producers to ALSO emit a canonical :class:`scitex_cards._events.Event`
onto the hook bus on a GENUINELY NEW link:

  * the built-in ``push`` handler — ``committed`` (trigger="commit") or
    ``pushed`` (trigger="push") on a freshly-recorded commit_sha.
  * ``reconcile_merged_prs`` — ``merged`` on a freshly auto-closed card.

EMIT-ONLY: there is intentionally NO consumer yet (C4 dispatcher is a
separate card). Tests capture the emitted card-event via the documented
in-process ``entry_points=`` injection seam (a real fake handler) — no
mocks, no monkeypatch (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

from conftest import seed_db_from_doc

from scitex_cards._hooks import dispatch_event, event_validate
from scitex_cards._reconcile_prs import MERGED, UNKNOWN, reconcile_merged_prs
from scitex_cards._store import add_task

# === In-process injection seam (real fake handler, no mocks) ===============


class _Capturing:
    """Concrete fake entry-point handler that records every event."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _card_events(sink: _Capturing) -> list[dict]:
    """Only the C1 canonical card-events (the dispatcher also fans the
    raw ``push`` event to the same plugin set — we filter to the
    card-event envelopes the C6 producers emit)."""
    return [e for e in sink.events if e.get("kind") == "card-event"]


def _store_with(tmp_path: Path) -> str:
    # SQLite store: `add_task` writes the canonical DB (the pinned test store);
    # the path arg is a label only, so drop it and let the pinned store resolve.
    # Return the pinned STORE-identity path (NOT the DB path — see the store-path
    # rule) so callers keep addressing the same store.
    add_task(id="card-1", title="x", assignee="agent:test-suite")
    return os.environ["SCITEX_TODO_TASKS_YAML_SHARED"]


# === push handler — commit trigger emits `committed` =======================


def test_new_commit_link_emits_one_committed_event(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    sink = _Capturing()
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "abc123def456",
            "trigger": "commit",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert — exactly one committed card-event.
    committed = [e for e in _card_events(sink) if e["type"] == "committed"]
    assert len(committed) == 1


def test_committed_event_carries_card_id_repo_sha(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    sink = _Capturing()
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "abc123def456",
            "trigger": "commit",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert
    got = [e for e in _card_events(sink) if e["type"] == "committed"][0]
    assert (got["card_id"], got["repo"], got["sha"]) == (
        "card-1",
        "owner/repo",
        "abc123def456",
    )


def test_relink_of_recorded_commit_emits_no_event(tmp_path: Path, env):
    # Arrange — first link records the sha; the second is idempotent.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "abc123def456",
            "trigger": "commit",
            "card_ids": ["card-1"],
        }
    )
    dispatch_event(event)  # first link (real entry points; harmless noop)
    sink = _Capturing()
    # Act — re-link the already-recorded commit.
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert — idempotent: no card-event emitted on the re-link.
    assert _card_events(sink) == []


# === push handler — push trigger emits `pushed` ============================


def test_push_trigger_emits_pushed_event(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    sink = _Capturing()
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "abc123def456",
            "trigger": "push",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert
    pushed = [e for e in _card_events(sink) if e["type"] == "pushed"]
    assert len(pushed) == 1


def test_absent_trigger_defaults_to_pushed(tmp_path: Path, env):
    # Arrange — an older producer omits `trigger`; default is `pushed`.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    sink = _Capturing()
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "abc123def456",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert
    assert [e["type"] for e in _card_events(sink)] == ["pushed"]


def test_pushed_event_carries_branch(tmp_path: Path, env):
    # Arrange
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    sink = _Capturing()
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "deadbeefcafe",
            "trigger": "push",
            "card_ids": ["card-1"],
        }
    )
    # Act
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert
    got = [e for e in _card_events(sink) if e["type"] == "pushed"][0]
    assert got["branch"] == "feat/some-card"


def test_unknown_card_id_emits_no_event(tmp_path: Path, env):
    # Arrange — producer hinted at a card that doesn't exist → no link.
    store = _store_with(tmp_path)
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    sink = _Capturing()
    event = event_validate(
        {
            "kind": "push",
            "repo": "owner/repo",
            "branch": "feat/some-card",
            "commit_sha": "abc",
            "trigger": "commit",
            "card_ids": ["never-existed"],
        }
    )
    # Act
    dispatch_event(event, entry_points=[_FakeEP("captor", sink)])
    # Assert — no genuine link → no card-event.
    assert _card_events(sink) == []


# === reconcile — newly-merged pr emits `merged` ============================


def _reconcile_store(tmp_path: Path) -> str:
    # SQLite store: seed the canonical DB from the in-memory doc the YAML held,
    # then hand back the pinned STORE-identity path so `reconcile_merged_prs`
    # resolves the same store it reads/writes (see the store-path rule).
    doc = {
        "tasks": [
            {
                "id": "merged-open-card",
                "title": "work that merged",
                "status": "in_progress",
                "pr_url": "https://github.com/o/r/pull/1",
            },
            {
                "id": "open-pr-card",
                "title": "still in review",
                "status": "blocked",
                "pr_url": "https://github.com/o/r/pull/2",
            },
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_TODO_TASKS_YAML_SHARED"]


def _fake_seam(mapping):
    def _fn(pr_url):
        return mapping.get(pr_url, UNKNOWN)

    return _fn


def test_newly_merged_pr_emits_merged_event(tmp_path: Path):
    # Arrange
    path = _reconcile_store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    sink = _Capturing()
    # Act
    reconcile_merged_prs(
        path, apply=True, merge_state_fn=seam, entry_points=[_FakeEP("captor", sink)]
    )
    # Assert
    merged = [e for e in _card_events(sink) if e["type"] == "merged"]
    assert len(merged) == 1


def test_merged_event_carries_card_id_and_pr_url(tmp_path: Path):
    # Arrange
    path = _reconcile_store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    sink = _Capturing()
    # Act
    reconcile_merged_prs(
        path, apply=True, merge_state_fn=seam, entry_points=[_FakeEP("captor", sink)]
    )
    # Assert
    got = [e for e in _card_events(sink) if e["type"] == "merged"][0]
    assert (got["card_id"], got["pr_url"]) == (
        "merged-open-card",
        "https://github.com/o/r/pull/1",
    )


def test_merged_event_repo_derived_from_pr_url(tmp_path: Path):
    # Arrange
    path = _reconcile_store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    sink = _Capturing()
    # Act
    reconcile_merged_prs(
        path, apply=True, merge_state_fn=seam, entry_points=[_FakeEP("captor", sink)]
    )
    # Assert
    got = [e for e in _card_events(sink) if e["type"] == "merged"][0]
    assert got["repo"] == "o/r"


def test_dry_run_emits_no_merged_event(tmp_path: Path):
    # Arrange — DRY-RUN mutates nothing, so it emits nothing.
    path = _reconcile_store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    sink = _Capturing()
    # Act
    reconcile_merged_prs(
        path, apply=False, merge_state_fn=seam, entry_points=[_FakeEP("captor", sink)]
    )
    # Assert
    assert _card_events(sink) == []


def test_already_done_card_does_not_reemit_on_second_run(tmp_path: Path):
    # Arrange — first apply closes the card + emits; a second run finds it
    # already done and emits NOTHING (idempotent).
    path = _reconcile_store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/1": MERGED})
    reconcile_merged_prs(path, apply=True, merge_state_fn=seam)
    sink = _Capturing()
    # Act
    reconcile_merged_prs(
        path, apply=True, merge_state_fn=seam, entry_points=[_FakeEP("captor", sink)]
    )
    # Assert
    assert _card_events(sink) == []


def test_open_pr_card_emits_no_merged_event(tmp_path: Path):
    # Arrange — an open PR is never closed, so never emits.
    path = _reconcile_store(tmp_path)
    seam = _fake_seam({"https://github.com/o/r/pull/2": "open"})
    sink = _Capturing()
    # Act
    reconcile_merged_prs(
        path, apply=True, merge_state_fn=seam, entry_points=[_FakeEP("captor", sink)]
    )
    # Assert
    assert _card_events(sink) == []


# EOF
