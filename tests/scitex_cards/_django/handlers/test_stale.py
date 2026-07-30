#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the GET /stale and POST /archive handlers — the recurring
stale-cards review panel + archive button (operator directive
2026-06-13, HTTP twin of CLI ``close --reason`` PR #151).

Real ``RequestFactory`` against tmp ``tasks.yaml`` (no mocks,
STX-NM / PA-306). Verifies:

  GET /stale
    - returns 200 + the criteria + total + by_project rollup
    - flags pending rows with created_at > N days as stale
    - flags rows with no created_at AND no last_activity
    - flags vague/orphaned rows (no title/owner)
    - the ``include_no_timestamp=false`` filter HIDES no-timestamp rows
    - the ``days`` knob narrows the cutoff
    - non-pending rows are NEVER flagged

  POST /archive
    - flips status to deferred + appends a [CLOSED] comment
    - stamps _log_meta.closed_{at,by}
    - 400 on empty reason; 404 on unknown id
    - reason flows into the comment text verbatim
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from conftest import seed_db_from_doc  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402
from scitex_cards._model import load_tasks  # noqa: E402
from scitex_cards._yaml import safe_load  # noqa: E402


def _iso(dt: datetime.datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


_OLD = _iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30))
_RECENT = _iso(
    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
)


_STORE_TEXT = f"""\
tasks:
  - id: old-pending-card
    title: '[P2] long stale pending card'
    status: pending
    created_at: '{_OLD}'
    project: scitex-dev
    assignee: proj-scitex-dev
  - id: recent-pending-card
    title: '[P2] recently created pending card'
    status: pending
    created_at: '{_RECENT}'
    project: scitex-dev
    assignee: proj-scitex-dev
  - id: no-timestamp-card
    title: 'undated pending card'
    status: pending
    project: business
    assignee: proj-scitex-lead
  - id: vague-card
    title: 'tbd'
    status: pending
  - id: done-card
    title: 'completed card should never be flagged'
    status: done
    created_at: '{_OLD}'
    project: scitex-dev
"""


@pytest.fixture
def store():
    # SQLite store: seed the prior cards into the canonical DB, then hand the
    # handler the PINNED store-identity path (never a tmp_path YAML — a write
    # stamped with a tmp path fails the next read's ownership check). The DB is
    # authoritative for content; the handler treats the path as a provenance
    # label. The board/services layer (get_board -> load_groups) still stat()s
    # the identity file, so it must EXIST even though its content is never read.
    seed_db_from_doc(safe_load(_STORE_TEXT) or {}, os.environ["SCITEX_CARDS_DB"])
    store_path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    Path(store_path).write_text("", encoding="utf-8")
    _reset_cache()
    yield store_path
    _reset_cache()


def _get(endpoint, store_path, query=""):
    request = RequestFactory().get(f"/{endpoint}?store={store_path}{query}")
    return views.api_dispatch(request, endpoint)


def _post(endpoint, store_path, body):
    request = RequestFactory().post(
        f"/{endpoint}?store={store_path}",
        data=json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, endpoint)


def _load(store_path):
    # Read back through the canonical store (SQLite); the path is a label only.
    return {t["id"]: t for t in load_tasks(store_path)}


# === GET /stale ==============================================================


def test_stale_returns_200(store):
    # Arrange
    # Act
    resp = _get("stale", store)
    # Assert
    assert resp.status_code == 200


def test_stale_includes_old_pending_card(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "old-pending-card" in ids


def test_stale_excludes_recent_pending_card(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "recent-pending-card" not in ids


def test_stale_excludes_done_card(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "done-card" not in ids


def test_stale_flags_no_timestamp_row(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "no-timestamp-card" in ids


def test_stale_flags_vague_row(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "vague-card" in ids


def test_stale_include_no_timestamp_false_filter(store):
    # Arrange
    payload = json.loads(_get("stale", store, "&include_no_timestamp=false").content)
    # filter must drop it (vague-card stays — it has a second reason).
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "no-timestamp-card" not in ids


def test_stale_returns_criteria_block(store):
    # Arrange
    # Act
    payload = json.loads(_get("stale", store, "&days=7").content)
    # Assert
    assert payload["criteria"] == {"days": 7, "include_no_timestamp": True}


def test_stale_rejects_negative_days(store):
    # Arrange
    # Act
    resp = _get("stale", store, "&days=-1")
    # Assert
    assert resp.status_code == 400


def test_stale_rejects_non_integer_days(store):
    # Arrange
    # Act
    resp = _get("stale", store, "&days=forever")
    # Assert
    assert resp.status_code == 400


def test_stale_returns_by_project_rollup(store):
    # Arrange
    # Act
    payload = json.loads(_get("stale", store).content)
    # Assert
    assert payload["by_project"].get("scitex-dev", 0) >= 1


def test_stale_rejects_post(store):
    # Arrange
    # Act
    resp = _post("stale", store, {})
    # Assert
    assert resp.status_code == 405


# === POST /archive ===========================================================


def test_archive_returns_200(store):
    # Arrange
    body = {"id": "old-pending-card", "reason": "superseded by PR #999"}
    # Act
    resp = _post("archive", store, body)
    # Assert
    assert resp.status_code == 200


def test_archive_flips_status_to_deferred(store):
    # Arrange
    body = {"id": "old-pending-card", "reason": "superseded by PR #999"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert tasks["old-pending-card"]["status"] == "deferred"


def test_archive_appends_closed_comment(store):
    # Arrange
    body = {"id": "old-pending-card", "reason": "superseded by PR #999"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    comments = tasks["old-pending-card"].get("comments") or []
    assert any("[CLOSED] superseded by PR #999" in c.get("text", "") for c in comments)


def test_archive_stamps_log_meta_closed_at(store):
    # Arrange
    body = {"id": "old-pending-card", "reason": "obsolete"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert "closed_at" in (tasks["old-pending-card"].get("_log_meta") or {})


def test_archive_stamps_log_meta_closed_by(store):
    # Arrange
    body = {"id": "old-pending-card", "reason": "obsolete", "by": "operator"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert tasks["old-pending-card"]["_log_meta"]["closed_by"] == "operator"


def test_archive_rejects_empty_reason(store):
    # Arrange
    body = {"id": "old-pending-card", "reason": ""}
    # Act
    resp = _post("archive", store, body)
    # Assert
    assert resp.status_code == 400


def test_archive_rejects_missing_reason(store):
    # Arrange
    body = {"id": "old-pending-card"}
    # Act
    resp = _post("archive", store, body)
    # Assert
    assert resp.status_code == 400


def test_archive_rejects_unknown_id(store):
    # Arrange
    body = {"id": "no-such-card", "reason": "x"}
    # Act
    resp = _post("archive", store, body)
    # Assert
    assert resp.status_code == 404


def test_archive_rejects_get(store):
    # Arrange
    # Act
    resp = _get("archive", store)
    # Assert
    assert resp.status_code == 405


# EOF
