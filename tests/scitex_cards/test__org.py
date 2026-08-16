#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the org-mode export adapter (P4 PR2, lead-approved 2026-06-12).

No mocks (STX-NM / PA-306); fixtures are plain dicts in the wire shape
``scitex_cards.load_tasks`` returns. AAA pattern; one assertion per test
where reasonable; descriptive names (TQ001 / TQ002 / TQ007).
"""

from __future__ import annotations

from scitex_cards._org import build_org


class TestOrgPreamble:
    def test_contains_title_directive(self):
        # Arrange
        # Act
        text = build_org([])
        # Assert
        assert "#+TITLE: scitex-cards export" in text

    def test_declares_todo_state_keywords(self):
        # Arrange
        # Act
        text = build_org([])
        # Assert
        assert "#+TODO: TODO INPROGRESS WAITING | DONE CANCELLED SOMEDAY" in text


class TestSingleTaskHeading:
    def test_renders_todo_heading(self):
        # Arrange
        tasks = [{"id": "a", "title": "Ship it", "status": "pending"}]
        # Act
        text = build_org(tasks)
        # Assert
        assert "* TODO Ship it" in text

    def test_maps_in_progress_to_inprogress(self):
        # Arrange
        tasks = [{"id": "a", "title": "Mid", "status": "in_progress"}]
        # Act
        text = build_org(tasks)
        # Assert
        assert "* INPROGRESS Mid" in text

    def test_maps_blocked_to_waiting(self):
        # Arrange
        tasks = [{"id": "a", "title": "B", "status": "blocked"}]
        # Act
        text = build_org(tasks)
        # Assert
        assert "* WAITING B" in text

    def test_maps_done_to_done(self):
        # Arrange
        tasks = [{"id": "a", "title": "D", "status": "done"}]
        # Act
        text = build_org(tasks)
        # Assert
        assert "* DONE D" in text


class TestDeadlineLine:
    def test_emits_bare_date_timestamp(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "deadline": "2026-06-15",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-06-15>" in text

    def test_truncates_iso_datetime_to_date(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "deadline": "2026-06-15T18:00:00+09:00",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE: <2026-06-15>" in text

    def test_skips_when_absent(self):
        # Arrange
        tasks = [{"id": "a", "title": "x", "status": "pending"}]
        # Act
        text = build_org(tasks)
        # Assert
        assert "DEADLINE:" not in text


class TestScheduledLine:
    def test_emits_when_present(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "scheduled": "2026-06-10",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "SCHEDULED: <2026-06-10>" in text

    def test_co_exists_with_deadline_on_same_line(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "deadline": "2026-06-15",
                "scheduled": "2026-06-10",
            }
        ]
        # Act
        text = build_org(tasks)
        # Both on one line per org convention.
        # Assert
        assert "DEADLINE: <2026-06-15> SCHEDULED: <2026-06-10>" in text


class TestPropertiesDrawer:
    def test_drawer_includes_id_property(self):
        # Arrange
        tasks = [{"id": "task-42", "title": "x", "status": "pending"}]
        # Act
        text = build_org(tasks)
        # Assert
        assert ":ID: task-42" in text

    def test_includes_project_when_set(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "project": "scitex-cards",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert ":PROJECT: scitex-cards" in text

    def test_omits_unset_fields(self):
        # Arrange
        tasks = [{"id": "a", "title": "x", "status": "pending"}]
        # Act
        text = build_org(tasks)
        # Project absent → drawer should not have the line.
        # Assert
        assert ":PROJECT:" not in text

    def test_includes_parent_pointer(self):
        # Arrange
        tasks = [
            {
                "id": "child",
                "title": "c",
                "status": "pending",
                "parent": "umbrella",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert ":PARENT: umbrella" in text


class TestNoteBody:
    def test_indents_note_under_heading_text_contains(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "note": "line1\nline2",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "  line1" in text

    def test_indents_note_under_heading_text_contains_2(self):
        # Arrange
        tasks = [
            {
                "id": "a",
                "title": "x",
                "status": "pending",
                "note": "line1\nline2",
            }
        ]
        # Act
        text = build_org(tasks)
        # Assert
        assert "  line2" in text


class TestEmptyTaskList:
    def test_renders_preamble_only_endswith(self):
        # Arrange
        # Act
        text = build_org([])
        # Assert
        # No headings emitted.
        assert text.endswith("\n")

    def test_renders_preamble_only_text_excludes(self):
        # Arrange
        # Act
        text = build_org([])
        # Assert
        # No headings emitted.
        assert "* " not in text


# EOF
