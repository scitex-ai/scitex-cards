#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Markdown rendering for card exports."""

from __future__ import annotations

from collections.abc import Iterable

from ._task import VALID_STATUSES

_UNASSIGNED = "Unassigned"
_INLINE_MARKDOWN = "\\`*_[]<>|!"


def _inline_text(value: object) -> str:
    """Return one Markdown-safe line while preserving Unicode text."""
    text = " ".join(str(value or "").split())
    for character in _INLINE_MARKDOWN:
        text = text.replace(character, f"\\{character}")
    return text


def _inline_code(value: object) -> str:
    """Return a code span whose fence cannot be closed by the value."""
    text = " ".join(str(value or "").split())
    current = maximum = 0
    for character in text:
        if character == "`":
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    fence = "`" * (maximum + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _status_label(status: str) -> str:
    return status.replace("_", " ").title()


def _text_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _task_sort_key(task: dict) -> tuple[str, str, str]:
    title = str(task.get("title") or "")
    return (
        title.casefold(),
        title,
        str(task.get("id") or ""),
    )


def _task_lines(task: dict, detail: str) -> list[str]:
    checked = "x" if task.get("status") == "done" else " "
    title = _inline_text(task.get("title"))
    if detail == "summary":
        if task.get("task"):
            title = f"{title} — {_inline_text(task['task'])}"
    elif detail == "full":
        lines = [f"- [{checked}] {title}"]
        fields = (
            ("ID", "id"),
            ("Task", "task"),
            ("Status", "status"),
            ("Project", "project"),
            ("Assignee", "assignee"),
            ("Scope", "scope"),
            ("Note", "note"),
        )
        for label, key in fields:
            value = task.get(key)
            if value in (None, ""):
                continue
            rendered = _inline_code(value) if key == "id" else _inline_text(value)
            lines.append(f"  - **{label}:** {rendered}")
        comments = task.get("comments")
        if isinstance(comments, list) and comments:
            lines.append("  - **Comments:**")
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                author = _inline_text(comment.get("author") or "unknown")
                timestamp = _inline_text(comment.get("ts") or "")
                prefix = f"{author} ({timestamp})" if timestamp else author
                lines.append(f"    - {prefix}: {_inline_text(comment.get('text'))}")
        return lines
    elif detail != "title":
        raise ValueError(f"unsupported Markdown detail: {detail!r}")
    return [f"- [{checked}] {title}"]


def _groups(
    rows: list[dict], group_by: str
) -> list[tuple[str | None, list[dict]]]:
    if group_by == "status":
        present = {str(task.get("status") or "") for task in rows}
        status_keys = [status for status in VALID_STATUSES if status in present]
        status_keys.extend(sorted(present.difference(status_keys), key=_text_sort_key))
        return [
            (
                _status_label(status),
                [row for row in rows if str(row.get("status") or "") == status],
            )
            for status in status_keys
        ]
    if group_by in {"project", "assignee"}:
        named = sorted(
            {str(task.get(group_by)) for task in rows if task.get(group_by)},
            key=_text_sort_key,
        )
        field_keys: list[str | None] = [*named]
        if any(not task.get(group_by) for task in rows):
            field_keys.append(None)
        return [
            (
                key if key is not None else _UNASSIGNED,
                [row for row in rows if (row.get(group_by) or None) == key],
            )
            for key in field_keys
        ]
    if group_by == "none":
        return [(None, rows)]
    raise ValueError(f"unsupported Markdown grouping: {group_by!r}")


def _hierarchical_rows(rows: list[dict]) -> list[tuple[dict, int]]:
    ids = {str(row.get("id") or "") for row in rows}
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for row in rows:
        parent = str(row.get("parent") or "")
        if parent and parent in ids:
            children.setdefault(parent, []).append(row)
        else:
            roots.append(row)

    ordered: list[tuple[dict, int]] = []
    visited: set[str] = set()

    def visit(row: dict, depth: int) -> None:
        task_id = str(row.get("id") or "")
        if task_id in visited:
            return
        visited.add(task_id)
        ordered.append((row, depth))
        for child in sorted(children.get(task_id, []), key=_task_sort_key):
            visit(child, depth + 1)

    for root in sorted(roots, key=_task_sort_key):
        visit(root, 0)
    for row in sorted(rows, key=_task_sort_key):
        visit(row, 0)
    return ordered


def build_markdown(
    tasks: Iterable[dict],
    *,
    detail: str = "title",
    group_by: str = "status",
    hierarchy: bool = False,
) -> str:
    """Render cards as a stable Markdown task list."""
    rows = [dict(task) for task in tasks]
    if not rows:
        return ""
    sections: list[str] = []
    for heading, grouped_rows in _groups(rows, group_by):
        lines = [f"## {_inline_text(heading)}", ""] if heading is not None else []
        ordered_rows = (
            _hierarchical_rows(grouped_rows)
            if hierarchy
            else [(task, 0) for task in sorted(grouped_rows, key=_task_sort_key)]
        )
        for task, depth in ordered_rows:
            prefix = "  " * depth
            lines.extend(f"{prefix}{line}" for line in _task_lines(task, detail))
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")


__all__ = ["build_markdown"]

# EOF
