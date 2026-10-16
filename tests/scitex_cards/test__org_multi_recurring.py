#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for P4 PR3 — _org.py multi/recurring export.

The _org.py adapter must emit recurring repeater suffixes verbatim
(1:1 with org-mode's native shape) and, when `deadlines:` is set,
one ``DEADLINE: <...>`` per entry on the heading stamp line.
"""

from __future__ import annotations

from scitex_cards._org import build_org


class TestExportRepeaterSuffix:
    def test_emits_weekly_repeater(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "X",
                "status": "deferred",
                "deadline": "2026-06-15 +1w",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-06-15 +1w>" in text

    def test_emits_catchup_monthly(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "X",
                "status": "deferred",
                "deadline": "2026-06-15 ++2m",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-06-15 ++2m>" in text


class TestExportMultipleDeadlines:
    def test_emits_two_deadline_tokens_on_same_line_text_contains(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "X",
                "status": "deferred",
                "deadlines": ["2026-06-15", "2026-07-01 +1m"],
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-06-15>" in text

    def test_emits_two_deadline_tokens_on_same_line_text_contains_2(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "X",
                "status": "deferred",
                "deadlines": ["2026-06-15", "2026-07-01 +1m"],
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-07-01 +1m>" in text

    def test_deadlines_takes_precedence_over_deadline_text_contains(self):
        # When both happen to be present (validator would normally
        # reject; assert the adapter is robust).
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "X",
                "status": "deferred",
                "deadline": "2026-09-01",
                "deadlines": ["2026-06-15"],
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-06-15>" in text

    def test_deadlines_takes_precedence_over_deadline_text_excludes(self):
        # When both happen to be present (validator would normally
        # reject; assert the adapter is robust).
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "X",
                "status": "deferred",
                "deadline": "2026-09-01",
                "deadlines": ["2026-06-15"],
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-09-01>" not in text


# EOF
