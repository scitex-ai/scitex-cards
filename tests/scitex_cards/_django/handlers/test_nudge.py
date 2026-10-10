#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the POST /nudge handler — operator's one-click 催促 button wire.

Mirrors ``src/scitex_cards/_django/handlers/nudge.py::handle_nudge``. Real
``RequestFactory`` POST against a tmp ``tasks.yaml`` + the per-process
cooldown registry (no mocks — STX-NM / PA-306). Covers:

  - 405 for non-POST methods
  - 400 on missing / empty / non-string ``agent``
  - 400 on invalid JSON body
  - 200 with ok=False, reason="no-turn-url-configured" when the target
    has no resolvable turn URL (the loud-but-not-fatal contract from
    PR #118 / #120 / #123 / #128 — operator sees a toast, the wire
    doesn't 5xx)
  - 429 on the per-agent cooldown window (lead-spec 5 min) — the
    second consecutive nudge inside the window must NOT re-trigger
    the push wire.

Reference: lead a2a `f16b0d2acb8946f88f2daffc4038228d` 2026-06-12
(operator TG12608). The nudge handler is the operator-facing companion
to the */10 cron — same `_throughput.build_notify_body` source, same
`_push.deliver` wire.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import nudge as _nudge_mod  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402


_STORE_TEXT = (
    "tasks:\n"
    "  - id: t1\n"
    "    title: Pac line\n"
    "    status: in_progress\n"
    "    agent: proj-paper-scitex-clew\n"
    "    project: paper-scitex-clew\n"
    "  - id: t2\n"
    "    title: Other\n"
    "    status: pending\n"
    "    agent: proj-paper-scitex-clew\n"
)


@pytest.fixture
def store(tmp_path, postgres_dsn):
    # ``postgres_dsn`` is requested but is not the seed target. With no
    # writable PostgreSQL the fleet guard clears ``SCITEX_STORE_DSN`` from
    # the environment, so the ``?store=`` reads below would resolve
    # AMBIENTLY to the live board — a silent production read, not a bare
    # ``KeyError``. The fixture fails first, LOUDLY, with the reason and
    # the remedy. (Same guard as the other ``store`` fixtures in _django/.)
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    # Reset the per-process cooldown registry between tests so the
    # cooldown ones don't bleed into the success-path ones.
    _nudge_mod._LAST_SENT_AT.clear()
    yield str(path)
    _reset_cache()
    _nudge_mod._LAST_SENT_AT.clear()


def _post(store_path, body, *, raw=False):
    request = RequestFactory().post(
        f"/nudge?store={store_path}",
        data=body if raw else json.dumps(body),
        content_type="application/json",
    )
    return views.api_dispatch(request, "nudge")


def _get(store_path):
    request = RequestFactory().get(f"/nudge?store={store_path}")
    return views.api_dispatch(request, "nudge")


# === Method gating ==========================================================


class TestMethodGate:
    """Nudge is POST-only; other methods get a clean 405."""

    def test_get_returns_405(self, store):
        # Arrange
        # Act
        resp = _get(store)
        # Assert
        assert resp.status_code == 405


# === Body validation ========================================================


class TestBodyValidation:
    """Loud-but-not-fatal validation: 400 with a JSON error envelope."""

    def test_invalid_json_returns_400(self, store):
        # Arrange — raw bytes that aren't valid JSON.
        # Act
        resp = _post(store, b"{not json", raw=True)
        # Assert
        assert resp.status_code == 400

    def test_missing_agent_returns_400(self, store):
        # Arrange
        # Act
        resp = _post(store, {})
        # Assert
        assert resp.status_code == 400

    def test_empty_agent_returns_400(self, store):
        # Arrange
        # Act
        resp = _post(store, {"agent": "  "})
        # Assert
        assert resp.status_code == 400

    def test_non_string_agent_returns_400(self, store):
        # Arrange
        # Act
        resp = _post(store, {"agent": 12345})
        # Assert
        assert resp.status_code == 400


# === Successful dispatch (no URL → loud-but-not-fatal) =====================


class TestDispatchNoUrl:
    """When the agent has no resolvable turn URL, the handler returns
    200 with ``ok=False, reason="no-turn-url-configured"`` so the UI
    can render a toast. The 502 status is reserved for actual transport
    failures from a CONFIGURED URL."""

    def test_no_turn_url_returns_200(self, store, env):
        # Arrange — strip env precedence 1 + 2 so resolution fails.
        from scitex_cards._push import ENV_MAP, PER_AGENT_PREFIX

        env.delete(ENV_MAP)
        for k in list(__import__("os").environ):
            if k.startswith(PER_AGENT_PREFIX):
                env.delete(k)
        # Act
        resp = _post(store, {"agent": "proj-paper-scitex-clew"})
        # Assert
        assert resp.status_code == 200

    def test_no_turn_url_body_carries_reason(self, store, env):
        # Arrange
        from scitex_cards._push import ENV_MAP, PER_AGENT_PREFIX

        env.delete(ENV_MAP)
        for k in list(__import__("os").environ):
            if k.startswith(PER_AGENT_PREFIX):
                env.delete(k)
        # Act
        resp = _post(store, {"agent": "proj-paper-scitex-clew"})
        payload = json.loads(resp.content)
        # Assert
        assert payload["reason"] == "no-turn-url-configured"


# === Cooldown ===============================================================


class TestCooldown:
    """Lead-spec 5-min per-agent cooldown so operator button-mashing
    doesn't flood the receiver. Pre-loads the registry with a fresh
    timestamp; the next POST must hit the cooldown branch."""

    def test_second_call_within_window_returns_429(self, store):
        # Arrange — seed the in-memory cooldown registry so the next
        # POST hits the cooldown branch without us depending on the
        # network-side first-call result.
        import time

        _nudge_mod._LAST_SENT_AT["proj-paper-scitex-clew"] = time.time()
        # Act
        resp = _post(store, {"agent": "proj-paper-scitex-clew"})
        # Assert
        assert resp.status_code == 429

    def test_cooldown_response_carries_remaining_s(self, store):
        # Arrange
        import time

        _nudge_mod._LAST_SENT_AT["proj-paper-scitex-clew"] = time.time()
        # Act
        resp = _post(store, {"agent": "proj-paper-scitex-clew"})
        payload = json.loads(resp.content)
        # Assert — remaining should be roughly the cooldown window
        # minus a tiny sliver; just verify it's > 0 + bounded by the
        # spec'd window.
        assert 0 < payload["cooldown_remaining_s"] <= _nudge_mod.COOLDOWN_SECONDS
