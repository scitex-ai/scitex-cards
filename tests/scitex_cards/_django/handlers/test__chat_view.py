#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/chat/<card_id>`` Django endpoint — fleet CHAT surface.

Lead a2a ``74db4f2d`` + ``10afa799`` greenlight (TRACK-2 Phase 6,
2026-06-14). The floor handler covers:

  - 200 + the card's ``comments[]`` on GET.
  - 404 on GET to an unknown card.
  - 200 + the appended comment on POST with a valid body.
  - 400 on POST with empty / missing text.
  - 404 on POST to an unknown card.
  - 405 on PUT / DELETE.
  - The POST genuinely persists into the underlying store (a second
    GET sees the new comment) — pins the "registry-sourced write" path.

Django RequestFactory; no mocks (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import json
import os

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.chat import chat_view
from scitex_cards._store import add_task, comment_task

# === fixtures ==============================================================


@pytest.fixture()
def store_with_chat_task() -> str:
    """Seed the canonical (SQLite) store with one task that already carries
    one comment, plus an empty-thread task, so the view's
    ``resolve_tasks_path(None)`` — pinned to the scratch store by the root and
    ``_django`` conftests — reads them back.

    The store is SQLite now: ``add_task`` / ``comment_task`` write the canonical
    DB, and passing ``store=None`` (the default) resolves the pinned store and
    round-trips (per THE STORE-PATH RULE — a ``tmp_path`` yaml would trip the
    "stamped for a DIFFERENT store" refusal). Returns the pinned STORE-identity
    path (never used for reads/writes now — only a provenance label).

    The pre-existing comment exercises the GET shape; the empty ``t-empty``
    task lets a different test exercise an empty-thread GET payload.
    """
    add_task(id="t-chat", title="Live chat thread", agent="agent-a")
    add_task(id="t-empty", title="Empty thread", agent="agent-b")
    comment_task(task_id="t-chat", text="hello from agent-a", by="agent-a")
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


# === GET ===================================================================


def test_chat_view_get_returns_200(store_with_chat_task):
    # Arrange
    rf = RequestFactory()
    req = rf.get("/chat/t-chat")
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 200


def test_chat_view_get_payload_shape(store_with_chat_task):
    """GET returns ``{card_id, title, comments: [...]}`` per the contract."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/chat/t-chat")
    # Act
    payload = json.loads(chat_view(req, card_id="t-chat").content)
    # Assert
    assert {"card_id", "title", "comments"} <= set(payload.keys())


def test_chat_view_get_includes_existing_comment(store_with_chat_task):
    """The seeded comment is reflected back verbatim — registry-sourced
    read, no parallel storage."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/chat/t-chat")
    # Act
    payload = json.loads(chat_view(req, card_id="t-chat").content)
    # Assert
    texts = [c["text"] for c in payload["comments"]]
    assert "hello from agent-a" in texts


def test_chat_view_get_empty_thread(store_with_chat_task):
    """A task with no comments yet returns an empty list — never None /
    missing."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/chat/t-empty")
    # Act
    payload = json.loads(chat_view(req, card_id="t-empty").content)
    # Assert
    assert payload["comments"] == []


def test_chat_view_get_unknown_card_404(store_with_chat_task):
    """Unknown card_id → 404 with a structured error — fail-loud, not a
    silent empty list."""
    # Arrange
    rf = RequestFactory()
    req = rf.get("/chat/does-not-exist")
    # Act
    response = chat_view(req, card_id="does-not-exist")
    # Assert
    assert response.status_code == 404


# === POST ==================================================================


def test_chat_view_post_returns_200(store_with_chat_task):
    """A valid POST appends a comment and returns 200 + the new entry."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "ping from operator", "author": "operator"}),
        content_type="application/json",
    )
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 200


def test_chat_view_post_returns_new_comment_text(store_with_chat_task):
    """The 200 payload carries the appended comment so the FE can
    optimistic-append without a follow-up GET."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "ping", "author": "operator"}),
        content_type="application/json",
    )
    # Act
    payload = json.loads(chat_view(req, card_id="t-chat").content)
    # Assert
    assert payload["comment"]["text"] == "ping"


def test_chat_view_post_returns_new_comment_author(store_with_chat_task):
    """The 200 payload carries the appended comment so the FE can
    optimistic-append without a follow-up GET."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "ping", "author": "operator"}),
        content_type="application/json",
    )
    # Act
    payload = json.loads(chat_view(req, card_id="t-chat").content)
    # Assert
    assert payload["comment"]["author"] == "operator"


def test_chat_view_post_persists_to_store(store_with_chat_task):
    """A successful POST genuinely persists — a second GET sees the new
    comment. Pins the "registry-sourced write" path (no parallel state)."""
    # Arrange
    rf = RequestFactory()
    post_req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "second message", "author": "operator"}),
        content_type="application/json",
    )
    # Act
    chat_view(post_req, card_id="t-chat")
    get_req = rf.get("/chat/t-chat")
    payload = json.loads(chat_view(get_req, card_id="t-chat").content)
    # Assert
    texts = [c["text"] for c in payload["comments"]]
    assert "second message" in texts


def test_chat_view_post_empty_text_400(store_with_chat_task):
    """Empty ``text`` → 400, never silently swallowed."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "   "}),
        content_type="application/json",
    )
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 400


def test_chat_view_post_missing_text_400(store_with_chat_task):
    """A POST without a ``text`` field → 400."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"author": "operator"}),
        content_type="application/json",
    )
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 400


def test_chat_view_post_non_string_text_400(store_with_chat_task):
    """A non-string ``text`` (number, null) → 400 — fail-loud."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": 123}),
        content_type="application/json",
    )
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 400


def test_chat_view_post_invalid_json_400(store_with_chat_task):
    """A POST with a malformed body → 400 with a structured error."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data="not-json{{{",
        content_type="application/json",
    )
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 400


def test_chat_view_post_unknown_card_404(store_with_chat_task):
    """POST to an unknown card → 404; never silently creates the row."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/does-not-exist",
        data=json.dumps({"text": "ping"}),
        content_type="application/json",
    )
    # Act
    response = chat_view(req, card_id="does-not-exist")
    # Assert
    assert response.status_code == 404


def test_chat_view_post_default_author_used_when_missing_isinstance(
    store_with_chat_task,
):
    """When ``author`` is omitted the view uses the fallback sentinel
    (display token, not a literal name) — no hardcoded proper nouns
    leak into the wire."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "ping"}),
        content_type="application/json",
    )
    # Act
    payload = json.loads(chat_view(req, card_id="t-chat").content)
    # Assert — the author value is at minimum a non-empty string; we don't
    # pin the exact sentinel here so the underlying $USER fallback
    # (when the env happens to be set) still passes.
    assert isinstance(payload["comment"]["author"], str)


def test_chat_view_post_default_author_used_when_missing_len(
    store_with_chat_task,
):
    """When ``author`` is omitted the view uses the fallback sentinel
    (display token, not a literal name) — no hardcoded proper nouns
    leak into the wire."""
    # Arrange
    rf = RequestFactory()
    req = rf.post(
        "/chat/t-chat",
        data=json.dumps({"text": "ping"}),
        content_type="application/json",
    )
    # Act
    payload = json.loads(chat_view(req, card_id="t-chat").content)
    # Assert — the author value is at minimum a non-empty string; we don't
    # pin the exact sentinel here so the underlying $USER fallback
    # (when the env happens to be set) still passes.
    assert len(payload["comment"]["author"]) > 0


# === method violations =====================================================


def test_chat_view_put_returns_405(store_with_chat_task):
    # Arrange
    rf = RequestFactory()
    req = rf.put("/chat/t-chat")
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 405


def test_chat_view_delete_returns_405(store_with_chat_task):
    # Arrange
    rf = RequestFactory()
    req = rf.delete("/chat/t-chat")
    # Act
    response = chat_view(req, card_id="t-chat")
    # Assert
    assert response.status_code == 405
