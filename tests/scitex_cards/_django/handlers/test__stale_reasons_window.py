#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the board /stale live-window reason strings.

``_stale_reasons`` (GET /stale, the HTTP twin of the CLI ``list-stale``
verb) hardcoded ``created_at>14d`` / ``last_activity>14d`` in its reason
strings while the endpoint's cutoff has been 7 days since the operator's
2026-08-19 instruction (14 -> 7) — and the endpoint accepts ``?days=``,
so any non-default window was misreported too. The CLI twin
(``_cli/_stale.py``) already reports the live window via
``{(now - cut).days}d``; the handler was the one twin left behind (same
dead-copy class as the BACKLOG_STATUS and archive-cancelled fixes).

These pins hold the handler to the live window without touching a
database (no Django test client, no store, no PostgreSQL — they run in
containers where the store-backed suite refuses via the fail-LOUD
guard). The store-backed ``test_stale.py`` carries the round-trip side
for CI-with-PG.
"""

from __future__ import annotations

import datetime
import inspect


def _reasons_source() -> str:
    from scitex_cards._django.handlers import stale as stale_handler

    return inspect.getsource(stale_handler._stale_reasons)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def test_reasons_name_live_default_window_created_at_case1():
    # Arrange — a card older than the default 7-day cutoff.
    # Arrange
    from scitex_cards._django.handlers.stale import _stale_reasons
    now = _utcnow()
    cut = now - datetime.timedelta(days=7)
    task = {
        "id": "t-old",
        "title": "a clearly owned old card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=30)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert any(r.startswith("created_at>7d ") for r in reasons)


def test_reasons_name_live_default_window_created_at_case2():
    # Arrange — a card older than the default 7-day cutoff.
    # Arrange
    from scitex_cards._django.handlers.stale import _stale_reasons
    now = _utcnow()
    cut = now - datetime.timedelta(days=7)
    task = {
        "id": "t-old",
        "title": "a clearly owned old card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=30)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert not any(">14d" in r for r in reasons)


def test_reasons_name_live_default_window_last_activity_case1():
    # Arrange — recent created_at, stale last_activity.
    # Arrange
    from scitex_cards._django.handlers.stale import _stale_reasons
    now = _utcnow()
    cut = now - datetime.timedelta(days=7)
    task = {
        "id": "t-quiet",
        "title": "a clearly owned quiet card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=2)),
        "last_activity": _iso(now - datetime.timedelta(days=30)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert any(r.startswith("last_activity>7d ") for r in reasons)


def test_reasons_name_live_default_window_last_activity_case2():
    # Arrange — recent created_at, stale last_activity.
    # Arrange
    from scitex_cards._django.handlers.stale import _stale_reasons
    now = _utcnow()
    cut = now - datetime.timedelta(days=7)
    task = {
        "id": "t-quiet",
        "title": "a clearly owned quiet card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=2)),
        "last_activity": _iso(now - datetime.timedelta(days=30)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert not any(">14d" in r for r in reasons)


def test_reasons_follow_custom_days_window_case1():
    # Arrange — the ?days= knob moves the cutoff; the strings must move
    # with it (a hardcoded number would misreport every custom window).
    # Arrange
    from scitex_cards._django.handlers.stale import _stale_reasons
    now = _utcnow()
    cut = now - datetime.timedelta(days=30)
    task = {
        "id": "t-custom",
        "title": "a clearly owned old card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=60)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert any(r.startswith("created_at>30d ") for r in reasons)


def test_reasons_follow_custom_days_window_case2():
    # Arrange — the ?days= knob moves the cutoff; the strings must move
    # with it (a hardcoded number would misreport every custom window).
    # Arrange
    from scitex_cards._django.handlers.stale import _stale_reasons
    now = _utcnow()
    cut = now - datetime.timedelta(days=30)
    task = {
        "id": "t-custom",
        "title": "a clearly owned old card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=60)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert not any(">14d" in r for r in reasons)


def test_reasons_source_uses_live_window_expression_case1():
    # Arrange
    # Arrange
    # Act
    src = _reasons_source()
    # Assert
    assert "(now - cut).days" in src


def test_reasons_source_uses_live_window_expression_case2():
    # Arrange
    # Arrange
    # Act
    src = _reasons_source()
    # Assert
    assert ">14d" not in src


def test_reasons_vacuity_fresh_card_has_no_age_reason():
    # Arrange — a young, owned card must not be flagged for age at all
    # (guards the pins above against a vacuous always-flag implementation).
    from scitex_cards._django.handlers.stale import _stale_reasons

    now = _utcnow()
    cut = now - datetime.timedelta(days=7)
    task = {
        "id": "t-fresh",
        "title": "a clearly owned fresh card",
        "assignee": "scitex-cards",
        "created_at": _iso(now - datetime.timedelta(days=2)),
        "last_activity": _iso(now - datetime.timedelta(days=1)),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert
    assert reasons == []


def test_reasons_match_cli_twin_format():
    # Arrange — the CLI twin formats ``created_at>{window}d ({date})``;
    # the board twin must emit the identical string for the same inputs.
    from scitex_cards._django.handlers.stale import _stale_reasons

    now = _utcnow()
    cut = now - datetime.timedelta(days=7)
    created = now - datetime.timedelta(days=30)
    task = {
        "id": "t-parity",
        "title": "a clearly owned old card",
        "assignee": "scitex-cards",
        "created_at": _iso(created),
    }
    # Act
    reasons = _stale_reasons(task, now, cut)
    # Assert: byte-identical to the CLI twin's format string.
    expected = f"created_at>{(now - cut).days}d ({created.date()})"
    assert expected in reasons
