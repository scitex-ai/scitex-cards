"""Synthetic store integration through a scoped in-memory canonical row port.

This fixture exercises actual public verbs and the delivery kernel, not SQL
transactions or deployed evidence. Scoped monkeypatch remains a qualified
PA-306 failure; it is not evidence that the real managed fixture passed.
"""

from contextlib import nullcontext
from copy import deepcopy

import pytest

from scitex_cards import _help_wait, _liveness, _model, _store
from scitex_cards import _store_add, _store_delivery, _store_lifecycle, _store_mutate
from scitex_cards import _store_single_card, _store_target, _store_upsert
from scitex_cards._store_delivery import DeliveryGuardError
from scitex_cards._store_errors import RevisionConflictError
from scitex_cards._validate import WRITE_SOURCE, _validate_tasks

NOW = "2026-10-03T10:00:00Z"


def delivery(required=True):
    return {
        "version": 1, "required": required, "generation": 1,
        "started_at": "2026-10-03T09:00:00Z",
        "checkpoints": {"old": {"receipt_sha256": "a" * 64}},
        "lease": {"owner": "agent:deployer"}, "completion": {"complete": True},
    }


def card(*, status="in_progress", namespace=True):
    task = {
        "id": "unit-card", "title": "Unit card", "status": status,
        "agent": "agent:unit", "assignee": "agent:unit", "created_by": "agent:unit",
        "repo": "fixture/application", "pr_url": "https://github.com/fixture/application/pull/1",
        "_log_meta": {"other": "preserve"},
    }
    if namespace:
        task["_log_meta"]["delivery"] = delivery()
    if status == "blocked":
        task["blocker"] = "operator-decision"
    if status == "done":
        task["_log_meta"].update(completed_at="2026-10-03T09:30:00Z", completed_by="agent:unit")
    return task


class MemoryRows:
    """An explicit test port; its revision check is synthetic, never SQL proof."""

    def __init__(self):
        self.cards = {}
        self.revisions = {}
        self.writes = []
        self.events = []
        self.before_write = None

    def seed(self, task, revision=4):
        self.cards[task["id"]] = deepcopy(task)
        self.revisions[task["id"]] = revision

    def read(self, target, task_id):
        return deepcopy(self.cards.get(task_id)), self.revisions.get(task_id)

    def write(self, target, task, *, expected_revision):
        if self.before_write is not None:
            self.before_write()
        found = self.revisions.get(task["id"])
        if found != expected_revision:
            raise RevisionConflictError(task["id"], expected_revision, found)
        _validate_tasks([task], source=WRITE_SOURCE, strict=True)
        self.seed(task, found + 1)
        self.writes.append((deepcopy(task), expected_revision))
        return {"tasks": 1}

    def document(self, path):
        doc = {"tasks": deepcopy(list(self.cards.values()))}
        return doc, doc["tasks"]

    def save_document(self, doc, path, *, tasks, touched_ids, **kwargs):
        _validate_tasks(tasks, source=WRITE_SOURCE, strict=True)
        for task in tasks:
            if task["id"] in touched_ids:
                self.seed(task, self.revisions.get(task["id"], 0) + 1)
                self.writes.append((deepcopy(task), None))

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))


@pytest.fixture
def rows(monkeypatch, tmp_path):
    """Scoped fake ports leave validation, guard decisions and callbacks real."""
    result = MemoryRows()
    monkeypatch.setenv("SCITEX_STORE_DSN", "postgresql://unit@127.0.0.1:1/unit")
    monkeypatch.setenv("SCITEX_CARDS_AGENT_ID", "agent:unit")
    monkeypatch.delenv(_store_delivery.FRESHNESS_ENV, raising=False)
    monkeypatch.setattr(_store_target, "resolve_store_target", lambda *a: "postgresql://unit@127.0.0.1:1/unit")
    monkeypatch.setattr(_store_single_card, "read_card_or_raise", result.read)
    monkeypatch.setattr(_store_single_card, "write_card_or_raise", result.write)
    monkeypatch.setattr(_store_delivery, "_now", lambda: NOW)
    monkeypatch.setattr(_store, "_utc_now_iso", lambda: NOW)
    monkeypatch.setattr(_store, "_read_write_doc", result.document)
    monkeypatch.setattr(_model, "_store_lock", lambda *a: nullcontext())
    monkeypatch.setattr(_model, "_save_doc_unlocked", result.save_document)
    monkeypatch.setattr(_help_wait, "_resolved_store", lambda *a: tmp_path / "unit-store")
    monkeypatch.setattr(_help_wait, "_utc_now_iso", lambda: NOW)
    monkeypatch.setattr(_help_wait, "_store_lock", lambda *a: nullcontext())
    if hasattr(_help_wait, "load_tasks"):
        monkeypatch.setattr(_help_wait, "load_tasks", lambda *a: result.document(None)[1])
        monkeypatch.setattr(
            _help_wait, "_save_tasks_unlocked",
            lambda tasks, path: result.save_document(
                {}, path, tasks=tasks, touched_ids=[task["id"] for task in tasks],
            ),
        )
    if hasattr(_help_wait, "_read_write_doc"):
        monkeypatch.setattr(_help_wait, "_read_write_doc", result.document)
        monkeypatch.setattr(_help_wait, "_save_doc_unlocked", result.save_document)
    for module in (_store_add, _store_lifecycle, _store_mutate, _store_upsert):
        monkeypatch.setattr(module, "_resolved_store", lambda *a: tmp_path / "unit-store")
        monkeypatch.setattr(module, "_emit_card_event", result.event)
    for module in (_store_lifecycle, _store_upsert, _help_wait):
        if hasattr(module, "read_card_or_raise"):
            monkeypatch.setattr(module, "read_card_or_raise", result.read)
            monkeypatch.setattr(module, "resolve_store_target", lambda *a: "postgresql://unit@127.0.0.1:1/unit")
    for module in (_store_add, _store_upsert):
        monkeypatch.setattr(module, "_store_lock", lambda *a: nullcontext())
        monkeypatch.setattr(module, "_save_doc_unlocked", result.save_document)
    for module in (_store_lifecycle, _store_mutate):
        monkeypatch.setattr(module, "_emit_unblock_for_dependents", result.event)
    monkeypatch.setattr(_liveness, "_heartbeat", lambda *a: None)
    monkeypatch.setattr(_liveness, "_assignee_liveness", lambda *a: None)
    return result

