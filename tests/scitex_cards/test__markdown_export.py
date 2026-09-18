#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Markdown export contract.

Tests use task dictionaries directly for the pure renderer and Click's real
runner for the command surface. No mocks.
"""

from __future__ import annotations

import os
import subprocess
import sys

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._markdown import build_markdown
from scitex_cards._store_list import _match


def test_default_export_is_a_title_only_task_list_grouped_by_status():
    # Arrange
    tasks = [
        {
            "id": "cards-secret-id",
            "title": "Write tests",
            "status": "done",
            "comments": [{"author": "agent", "text": "not in the default"}],
        },
        {
            "id": "cards-other-id",
            "title": "Ship release",
            "status": "in_progress",
        },
    ]

    # Act
    text = build_markdown(tasks)

    # Assert
    assert text == (
        "## In Progress\n\n"
        "- [ ] Ship release\n\n"
        "## Done\n\n"
        "- [x] Write tests\n"
    )


def test_export_order_is_independent_of_input_order():
    # Arrange
    tasks = [
        {"id": "z", "title": "Zulu", "status": "in_progress"},
        {"id": "a", "title": "Alpha", "status": "in_progress"},
    ]

    # Act
    forwards = build_markdown(tasks)
    backwards = build_markdown(reversed(tasks))

    # Assert
    assert forwards == backwards


def test_untrusted_card_text_cannot_inject_markdown_structure():
    # Arrange: titles, project names and comments are agent/user controlled.
    task = {
        "id": "card`id",
        "title": "Visible [label]\n## Forged heading\n- [x] forged *task*",
        "status": "blocked",
        "project": "Project\n## Forged group",
        "note": "line one\n- [x] forged note",
        "comments": [{"author": "agent*name", "text": "hello\n## forged comment"}],
    }

    # Act
    title_only = build_markdown([task], group_by="project")
    full = build_markdown([task], detail="full", group_by="none")

    # Assert: one card stays one checkbox; no supplied newline becomes structure.
    facts = {
        "checkboxes": title_only.count("- ["),
        "forged_heading": "\n## Forged" in title_only,
        "forged_checkbox": "\n- [x] forged" in title_only,
        "escaped_title": (
            "Visible \\[label\\] ## Forged heading - \\[x\\] forged \\*task\\*"
            in title_only
        ),
        "escaped_note": "line one - \\[x\\] forged note" in full,
        "escaped_comment": "agent\\*name: hello ## forged comment" in full,
    }
    assert facts == {
        "checkboxes": 1,
        "forged_heading": False,
        "forged_checkbox": False,
        "escaped_title": True,
        "escaped_note": True,
        "escaped_comment": True,
    }


def test_markdown_escaping_preserves_unicode_and_japanese():
    # Arrange
    task = {"id": "ja", "title": "研究 [進行中] — 結果", "status": "in_progress"}

    # Act
    text = build_markdown([task], group_by="none")

    # Assert
    assert text == "- [ ] 研究 \\[進行中\\] — 結果\n"


def test_group_by_project_uses_stable_project_headings():
    # Arrange
    tasks = [
        {"id": "b", "title": "No project", "status": "deferred"},
        {
            "id": "a",
            "title": "Cards work",
            "status": "in_progress",
            "project": "scitex-cards",
        },
    ]

    # Act
    text = build_markdown(tasks, group_by="project")

    # Assert
    assert text == (
        "## scitex-cards\n\n"
        "- [ ] Cards work\n\n"
        "## Unassigned\n\n"
        "- [ ] No project\n"
    )


def test_group_by_assignee_uses_the_assignee_field():
    # Arrange
    tasks = [
        {
            "id": "a",
            "title": "Owned work",
            "status": "blocked",
            "assignee": "agent:cards",
        }
    ]

    # Act
    text = build_markdown(tasks, group_by="assignee")

    # Assert
    assert text.startswith("## agent:cards\n\n- [ ] Owned work\n")


def test_group_by_none_emits_only_task_lines():
    # Arrange
    tasks = [{"id": "a", "title": "Flat work", "status": "deferred"}]

    # Act
    text = build_markdown(tasks, group_by="none")

    # Assert
    assert text == "- [ ] Flat work\n"


def test_summary_detail_adds_the_one_line_task_text():
    # Arrange
    tasks = [
        {
            "id": "a",
            "title": "Short title",
            "task": "One-line current task",
            "status": "in_progress",
        }
    ]

    # Act
    text = build_markdown(tasks, detail="summary", group_by="none")

    # Assert
    assert text == "- [ ] Short title — One-line current task\n"


def test_summary_detail_falls_back_to_title_when_task_text_is_absent():
    # Arrange
    tasks = [{"id": "a", "title": "Legacy card", "status": "deferred"}]

    # Act
    text = build_markdown(tasks, detail="summary", group_by="none")

    # Assert
    assert text == "- [ ] Legacy card\n"


def test_full_detail_includes_metadata_note_and_comments():
    # Arrange
    tasks = [
        {
            "id": "card-a",
            "title": "Detailed card",
            "task": "Do the work",
            "status": "blocked",
            "project": "scitex-cards",
            "assignee": "agent:cards",
            "scope": "fleet",
            "note": "Why this matters",
            "comments": [
                {
                    "author": "agent:reviewer",
                    "ts": "2026-09-18T01:00:00Z",
                    "text": "Please add coverage",
                }
            ],
        }
    ]

    # Act
    text = build_markdown(tasks, detail="full", group_by="none")

    # Assert
    assert text == (
        "- [ ] Detailed card\n"
        "  - **ID:** `card-a`\n"
        "  - **Task:** Do the work\n"
        "  - **Status:** blocked\n"
        "  - **Project:** scitex-cards\n"
        "  - **Assignee:** agent:cards\n"
        "  - **Scope:** fleet\n"
        "  - **Note:** Why this matters\n"
        "  - **Comments:**\n"
        "    - agent:reviewer (2026-09-18T01:00:00Z): Please add coverage\n"
    )


def test_hierarchy_nests_children_below_their_parent():
    # Arrange
    tasks = [
        {
            "id": "child",
            "title": "Child task",
            "status": "in_progress",
            "parent": "parent",
        },
        {"id": "parent", "title": "Parent task", "status": "in_progress"},
    ]

    # Act
    text = build_markdown(tasks, hierarchy=True)

    # Assert
    assert text == (
        "## In Progress\n\n"
        "- [ ] Parent task\n"
        "  - [ ] Child task\n"
    )


def test_hierarchy_keeps_cards_in_a_parent_cycle_visible():
    # Arrange
    tasks = [
        {"id": "a", "title": "Alpha", "status": "blocked", "parent": "b"},
        {"id": "b", "title": "Beta", "status": "blocked", "parent": "a"},
    ]

    # Act
    text = build_markdown(tasks, hierarchy=True)

    # Assert
    assert text.count("- [ ]") == 2


def test_cli_exports_markdown_to_stdout_by_default():
    # Arrange
    def load_cards(**_filters):
        return [{"id": "card-a", "title": "CLI card", "status": "deferred"}]

    # Act
    result = CliRunner().invoke(
        main,
        ["export", "--format", "markdown"],
        obj={"task_loader": load_cards},
    )

    # Assert
    assert result.exit_code == 0 and "- [ ] CLI card" in result.output, result.output


def test_cli_filters_compose_before_rendering():
    # Arrange
    cards = [
        {
            "id": "keep",
            "title": "Keep me",
            "status": "blocked",
            "project": "cards",
            "assignee": "agent:cards",
            "scope": "fleet",
        },
        {
            "id": "status",
            "title": "Wrong status",
            "status": "deferred",
            "project": "cards",
            "assignee": "agent:cards",
            "scope": "fleet",
        },
        {
            "id": "project",
            "title": "Wrong project",
            "status": "done",
            "project": "writer",
            "assignee": "agent:cards",
            "scope": "fleet",
        },
    ]

    def load_cards(**filters):
        return [card for card in cards if _match(card, **filters)]

    # Act
    result = CliRunner().invoke(
        main,
        [
            "export",
            "--format",
            "markdown",
            "--status",
            "blocked",
            "--status",
            "done",
            "--project",
            "cards",
            "--assignee",
            "agent:cards",
            "--scope",
            "fleet",
            "--group-by",
            "none",
        ],
        obj={"task_loader": load_cards},
    )

    # Assert
    assert result.output == "- [ ] Keep me\n"


def test_cli_forwards_detail_and_hierarchy_to_the_renderer():
    # Arrange
    def load_cards(**_filters):
        return [
            {"id": "parent", "title": "Parent", "status": "deferred"},
            {
                "id": "child",
                "title": "Child",
                "status": "deferred",
                "parent": "parent",
            },
        ]

    # Act
    result = CliRunner().invoke(
        main,
        [
            "export",
            "--format",
            "markdown",
            "--detail",
            "full",
            "--group-by",
            "none",
            "--hierarchy",
        ],
        obj={"task_loader": load_cards},
    )

    # Assert
    assert "  - [ ] Child\n    - **ID:** `child`" in result.output


def test_cli_output_writes_exact_markdown_bytes_to_a_file(tmp_path):
    # Arrange
    def load_cards(**_filters):
        return [{"id": "card-a", "title": "Saved card", "status": "done"}]

    output = tmp_path / "tasks.md"
    # Act
    result = CliRunner().invoke(
        main,
        [
            "export",
            "--format",
            "markdown",
            "--group-by",
            "none",
            "--output",
            str(output),
        ],
        obj={"task_loader": load_cards},
    )
    payload = output.read_bytes() if output.exists() else b""

    # Assert
    assert (
        result.exit_code == 0
        and result.output == ""
        and payload == b"- [x] Saved card\n"
    )


def test_empty_export_is_empty_for_every_grouping_mode():
    # Arrange
    groupings = ("status", "project", "assignee", "none")

    # Act
    outputs = [build_markdown([], group_by=grouping) for grouping in groupings]

    # Assert
    assert outputs == ["", "", "", ""]


def test_cli_status_filter_accepts_forward_compatible_values():
    # Arrange
    def load_cards(**filters):
        card = {"id": "future", "title": "Future", "status": "future_state"}
        return [card] if _match(card, **filters) else []

    # Act
    result = CliRunner().invoke(
        main,
        ["export", "--status", "future_state", "--group-by", "none"],
        obj={"task_loader": load_cards},
    )

    # Assert
    assert result.output == "- [ ] Future\n"


def test_group_order_does_not_depend_on_python_hash_seed():
    # Arrange
    script = """
from scitex_cards._markdown import build_markdown
cards = [
    {"id": "a", "title": "A", "status": "deferred", "project": "Alpha"},
    {"id": "b", "title": "B", "status": "deferred", "project": "alpha"},
]
print(build_markdown(cards, group_by="project"), end="")
"""

    # Act
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": seed},
            text=True,
        )
        for seed in ("1", "2")
    ]

    # Assert
    assert outputs[0] == outputs[1]
