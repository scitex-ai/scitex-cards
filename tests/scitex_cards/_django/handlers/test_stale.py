#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the GET /stale and POST /archive handlers — the recurring
stale-cards review panel + archive button (operator directive
2026-06-13, HTTP twin of CLI ``close --reason`` PR #151).

Real ``RequestFactory`` against tmp ``tasks.yaml`` (no mocks,
STX-NM / PA-306). Verifies:

  GET /stale
    - returns 200 + the criteria + total + by_project rollup
    - flags deferred rows with created_at > N days as stale
    - flags rows with no created_at AND no last_activity
    - flags vague/orphaned rows (no title/owner)
    - the ``include_no_timestamp=false`` filter HIDES no-timestamp rows
    - the ``days`` knob narrows the cutoff
    - non-deferred rows are NEVER flagged

  POST /archive
    - flips status to cancelled (terminal, like the CLI twin) + appends a [CLOSED] comment
    - stamps _log_meta.closed_{at,by} + clears _log_meta.completed_{at,by}
    - pops the blocker gate when archiving a blocked card (validator rule (b))
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
  - id: old-deferred-card
    title: '[P2] long stale deferred card'
    status: deferred
    created_at: '{_OLD}'
    project: scitex-dev
    assignee: proj-scitex-dev
  - id: recent-deferred-card
    title: '[P2] recently created deferred card'
    status: deferred
    created_at: '{_RECENT}'
    project: scitex-dev
    assignee: proj-scitex-dev
  - id: no-timestamp-card
    title: 'undated deferred card'
    status: deferred
    project: business
    assignee: proj-scitex-lead
  - id: vague-card
    title: 'tbd'
    status: deferred
  - id: done-card
    title: 'completed card should never be flagged'
    status: done
    created_at: '{_OLD}'
    project: scitex-dev
    _log_meta: {{completed_at: '{_OLD}', completed_by: 'scitex-cards'}}
  - id: blocked-card
    title: 'gated card under operator decision'
    status: blocked
    blocker: operator-decision
    created_at: '{_OLD}'
    project: scitex-dev
    assignee: proj-scitex-dev
"""


@pytest.fixture
def store(postgres_dsn):
    # ``postgres_dsn`` is requested but is not the seed target. With no
    # writable PostgreSQL the fleet guard clears ``SCITEX_STORE_DSN`` from
    # the environment, so indexing it below would die with a bare
    # ``KeyError`` — the fixture fails first, LOUDLY, with the reason and
    # the remedy. (Same guard as the other ``store`` fixtures in _django/.)
    # The store is the database: seed the prior cards into it, then hand the
    # handler the PINNED store-identity path (never a tmp_path YAML — a write
    # stamped with a tmp path fails the next read's ownership check). The DB is
    # authoritative for content; the handler treats the path as a provenance
    # label. The board/services layer (get_board -> load_groups) still stat()s
    # the identity file, so it must EXIST even though its content is never read.
    seed_db_from_doc(safe_load(_STORE_TEXT) or {}, os.environ["SCITEX_STORE_DSN"])
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
    # Read back through the canonical store; the path is a label only.
    return {t["id"]: t for t in load_tasks(store_path)}


# === GET /stale ==============================================================


def test_stale_returns_200(store):
    # Arrange
    # Act
    resp = _get("stale", store)
    # Assert
    assert resp.status_code == 200


def test_stale_includes_old_deferred_card(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "old-deferred-card" in ids


def test_stale_excludes_recent_deferred_card(store):
    # Arrange
    payload = json.loads(_get("stale", store).content)
    # Act
    ids = [r["id"] for r in payload["stale"]]
    # Assert
    assert "recent-deferred-card" not in ids


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
    body = {"id": "old-deferred-card", "reason": "superseded by PR #999"}
    # Act
    resp = _post("archive", store, body)
    # Assert
    assert resp.status_code == 200


def test_archive_flips_status_to_cancelled(store):
    # Arrange — the CLI ``close --reason`` twin writes ``cancelled``
    # (terminal); ``deferred`` is OPEN (operator ruling 2026-07-10), so a
    # [CLOSED]-stamped deferred row is CLOSED-and-OPEN at once.
    body = {"id": "old-deferred-card", "reason": "superseded by PR #999"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert tasks["old-deferred-card"]["status"] == "cancelled"


def test_archive_appends_closed_comment(store):
    # Arrange
    body = {"id": "old-deferred-card", "reason": "superseded by PR #999"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    comments = tasks["old-deferred-card"].get("comments") or []
    assert any("[CLOSED] superseded by PR #999" in c.get("text", "") for c in comments)


def test_archive_stamps_log_meta_closed_at(store):
    # Arrange
    body = {"id": "old-deferred-card", "reason": "obsolete"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert "closed_at" in (tasks["old-deferred-card"].get("_log_meta") or {})


def test_archive_stamps_log_meta_closed_by(store):
    # Arrange
    body = {"id": "old-deferred-card", "reason": "obsolete", "by": "operator"}
    # Act
    _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert tasks["old-deferred-card"]["_log_meta"]["closed_by"] == "operator"


def test_archive_clears_completion_stamp_case1(store):
    # Arrange — done-card seeds WITH its completion stamp (see _STORE_TEXT):
    # archiving it must not mint a cancelled row throughput still counts
    # as delivered (fleet/timing.py + timeline.py read completed_at alone).
    # Arrange
    tasks = _load(store)
    row = tasks["done-card"]
    body = {"id": "done-card", "reason": "superseded after all"}
    # Act
    resp = _post("archive", store, body)
    # Assert
    meta = row.get("_log_meta") or {}
    tasks = _load(store)
    assert resp.status_code == 200


def test_archive_clears_completion_stamp_case2(store):
    # Arrange — done-card seeds WITH its completion stamp (see _STORE_TEXT):
    # archiving it must not mint a cancelled row throughput still counts
    # as delivered (fleet/timing.py + timeline.py read completed_at alone).
    # Arrange
    body = {"id": "done-card", "reason": "superseded after all"}
    resp = _post("archive", store, body)
    tasks = _load(store)
    # Act
    row = tasks["done-card"]
    # Assert
    meta = row.get("_log_meta") or {}
    assert row["status"] == "cancelled"


def test_archive_clears_completion_stamp_case3(store):
    # Arrange — done-card seeds WITH its completion stamp (see _STORE_TEXT):
    # archiving it must not mint a cancelled row throughput still counts
    # as delivered (fleet/timing.py + timeline.py read completed_at alone).
    # Arrange
    body = {"id": "done-card", "reason": "superseded after all"}
    resp = _post("archive", store, body)
    tasks = _load(store)
    row = tasks["done-card"]
    # Act
    meta = row.get("_log_meta") or {}
    # Assert
    assert "completed_at" not in meta


def test_archive_clears_completion_stamp_case4(store):
    # Arrange — done-card seeds WITH its completion stamp (see _STORE_TEXT):
    # archiving it must not mint a cancelled row throughput still counts
    # as delivered (fleet/timing.py + timeline.py read completed_at alone).
    # Arrange
    body = {"id": "done-card", "reason": "superseded after all"}
    resp = _post("archive", store, body)
    tasks = _load(store)
    row = tasks["done-card"]
    # Act
    meta = row.get("_log_meta") or {}
    # Assert
    assert "completed_by" not in meta


def test_archive_clears_completion_stamp_case5(store):
    # Arrange — done-card seeds WITH its completion stamp (see _STORE_TEXT):
    # archiving it must not mint a cancelled row throughput still counts
    # as delivered (fleet/timing.py + timeline.py read completed_at alone).
    # Arrange
    body = {"id": "done-card", "reason": "superseded after all"}
    resp = _post("archive", store, body)
    tasks = _load(store)
    row = tasks["done-card"]
    # Act
    meta = row.get("_log_meta") or {}
    # Assert
    assert "closed_at" in meta


def test_archive_clears_blocker_gate_on_leaving_blocked_case1(store):
    # Arrange — blocked-card seeds WITH its gate: archiving it must not
    # 400 on its own blocker (the validator rejects a real blocker
    # variant on a non-`blocked` row, `_validate.py` rule (b)), so the
    # handler pops the gate with the flip — same invariant the project
    # board and `handle_reopen` keep.
    # Arrange
    body = {"id": "blocked-card", "reason": "no longer needed"}
    # Act
    resp = _post("archive", store, body)
    # Assert
    tasks = _load(store)
    assert resp.status_code == 200


def test_archive_clears_blocker_gate_on_leaving_blocked_case2(store):
    # Arrange — blocked-card seeds WITH its gate: archiving it must not
    # 400 on its own blocker (the validator rejects a real blocker
    # variant on a non-`blocked` row, `_validate.py` rule (b)), so the
    # handler pops the gate with the flip — same invariant the project
    # board and `handle_reopen` keep.
    # Arrange
    body = {"id": "blocked-card", "reason": "no longer needed"}
    resp = _post("archive", store, body)
    tasks = _load(store)
    # Act
    row = tasks["blocked-card"]
    # Assert
    assert row["status"] == "cancelled"


def test_archive_clears_blocker_gate_on_leaving_blocked_case3(store):
    # Arrange — blocked-card seeds WITH its gate: archiving it must not
    # 400 on its own blocker (the validator rejects a real blocker
    # variant on a non-`blocked` row, `_validate.py` rule (b)), so the
    # handler pops the gate with the flip — same invariant the project
    # board and `handle_reopen` keep.
    # Arrange
    body = {"id": "blocked-card", "reason": "no longer needed"}
    resp = _post("archive", store, body)
    tasks = _load(store)
    # Act
    row = tasks["blocked-card"]
    # Assert
    assert "blocker" not in row


def test_archive_rejects_empty_reason(store):
    # Arrange
    body = {"id": "old-deferred-card", "reason": ""}
    # Act
    resp = _post("archive", store, body)
    # Assert
    assert resp.status_code == 400


def test_archive_rejects_missing_reason(store):
    # Arrange
    body = {"id": "old-deferred-card"}
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


# === Status-mirror pins (no store: stub board, runs without PostgreSQL) ======
#
# ``pending`` was abolished 2026-07-10, but this handler kept filtering on it —
# matching no card in any live store, so the /stale panel served zero rows
# while the CLI ``list-stale`` twin (``_backlog_triage.BACKLOG_STATUS``)
# reviewed the deferred backlog. These pins hold the HTTP twin to the same
# single source without touching a database.


class _StubBoard:
    def __init__(self, tasks):
        self.tasks = tasks
        self.store_path = "stub-board (no store)"


def _stale_ids(tasks, query=""):
    from scitex_cards._django.handlers import stale as stale_handler

    request = RequestFactory().get(f"/stale{query}")
    board = _StubBoard(tasks)
    payload = json.loads(stale_handler.handle_stale(request, board).content)
    return [r["id"] for r in payload["stale"]]


def _old(status):
    return {
        "id": f"old-{status}",
        "title": f"[P2] long stale {status} card",
        "status": status,
        "created_at": _OLD,
        "project": "scitex-dev",
        "assignee": "proj-scitex-dev",
    }


def test_stale_panel_flags_the_backlog_status_without_a_database():
    # Arrange
    # Act
    ids = _stale_ids([_old("deferred")])
    # Assert
    assert ids == ["old-deferred"]


def test_stale_panel_ignores_a_legacy_pending_row():
    # A ``pending`` row can still arrive as a tolerated read (abolished, not
    # unreadable) — but the review panel must not offer it: the store refuses
    # ``pending`` on write and the CLI twin never lists it.
    # Arrange
    # Act
    ids = _stale_ids([_old("pending")])
    # Assert
    assert ids == []


def test_stale_panel_status_mirror_equals_the_live_enum_case1():
    # Arrange
    # Arrange
    from scitex_cards._backlog_triage import BACKLOG_STATUS
    from scitex_cards._django.handlers import stale as stale_handler
    # Act
    mirrored = stale_handler.BACKLOG_STATUS
    # Assert
    assert mirrored == BACKLOG_STATUS


def test_stale_panel_status_mirror_equals_the_live_enum_case2():
    # Arrange
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler
    from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
    # Act
    mirrored = stale_handler.BACKLOG_STATUS
    # Assert
    assert mirrored in VALID_STATUSES


def test_stale_panel_status_mirror_equals_the_live_enum_case3():
    # Arrange
    # Arrange
    from scitex_cards._django.handlers import stale as stale_handler
    from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES
    # Act
    mirrored = stale_handler.BACKLOG_STATUS
    # Assert
    assert mirrored not in ABOLISHED_STATUSES


# EOF
