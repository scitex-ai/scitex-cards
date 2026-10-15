#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mermaid adapter — render a validated task list as ``flowchart TB`` source.

Edges:
    depends_on  ->  normal arrow      dep --> task
    blocks      ->  inhibition arrow  blocker -- blocks --x target

Status -> fill color:
    goal        gold     (#ffe082, gold border)
    done        green    (#c8e6c9)
    in_progress yellow   (#fff9c4)
    blocked     orange   (#fff3e0)
    cancelled   grey     (#eceff1)
    deferred    grey     (#f5f5f5, dashed border)
    failed      red      (#ffcdd2)
"""

from __future__ import annotations

import sys

# fill, stroke, stroke-dasharray (empty = solid border)
#
# Intensification 2026-06-06 (operator UX "ブロッカーが何かわからない" / lead
# a2a 2843279f): `blocked` was pale `#fff3e0` and `deferred` was near-invisible
# `#f5f5f5` — both got lost in the canvas + mermaid renderings. Bumped to a
# bright orange + a saturated amber so stuck threads jump out across BOTH the
# Django board (FE reads `_status_colors()` from /graph) AND the mermaid
# artifacts (Python `build_mermaid()` reads this same table directly), keeping
# the visual cue consistent everywhere. `goal` stays softer-amber so the new
# `deferred` doesn't collide with it — `deferred`'s dashed `5 3` border is the
# kept differentiator from `goal`'s solid border.
STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    "goal": ("#ffe082", "#ff6f00", ""),
    "done": ("#c8e6c9", "#2e7d32", ""),
    "in_progress": ("#fff9c4", "#f9a825", ""),
    "blocked": ("#ff8a65", "#bf360c", ""),
    # ``cancelled`` (closed as not planned, terminal) reuses the muted grey
    # the abolished ``pending`` (2026-07-10) formerly occupied: a closed node
    # reads inactive, distinct from ``done`` green (completed) and the
    # goal/deferred ambers. Matches the sticky-wall neutral precedent
    # (board_v3 11-sticky-wall.css ``.sw-note--cancelled``).
    "cancelled": ("#eceff1", "#90a4ae", ""),
    "deferred": ("#ffca28", "#ff8f00", "5 3"),
    "failed": ("#ffcdd2", "#c62828", ""),
}


def _sanitize_label(text: str) -> str:
    """Make a string safe inside a mermaid ``["..."]`` node label."""
    return str(text).replace('"', "'").replace("\n", " ").strip()


def build_mermaid(tasks: list[dict]) -> str:
    """Render the task list as a mermaid ``flowchart TB`` source string.

    Parameters
    ----------
    tasks : list of dict
        Validated tasks, typically from :func:`scitex_cards.load_tasks`.

    Returns
    -------
    str
        Mermaid ``flowchart TB`` source, newline-terminated. Includes node
        labels, ``depends_on`` and ``blocks`` edges, and per-status
        ``classDef`` styling.

    Notes
    -----
    Edges referencing an unknown task id are skipped with a warning on
    stderr rather than raising — a partial graph is more useful than none.

    Examples
    --------
    >>> src = build_mermaid([{"id": "a", "title": "A", "status": "done"}])
    >>> src.startswith("flowchart TB")
    True
    """
    ids = {task["id"] for task in tasks}
    lines: list[str] = ["flowchart TB"]

    # Nodes
    for task in tasks:
        tid = task["id"]
        label = _sanitize_label(task["title"])
        note = task.get("note")
        if note:
            label = f"{label}<br/>({_sanitize_label(note)})"
        lines.append(f'    {tid}["{label}"]')

    lines.append("")

    # depends_on edges: dependency --> task
    for task in tasks:
        tid = task["id"]
        for dep in task.get("depends_on", []) or []:
            if dep not in ids:
                sys.stderr.write(
                    f"WARN: task {tid!r} depends_on unknown id {dep!r}; skipping edge\n"
                )
                continue
            lines.append(f"    {dep} --> {tid}")

    # blocks edges: blocker -- blocks --x target (inhibition / cross arrowhead)
    for task in tasks:
        tid = task["id"]
        for target in task.get("blocks", []) or []:
            if target not in ids:
                sys.stderr.write(
                    f"WARN: task {tid!r} blocks unknown id {target!r}; skipping edge\n"
                )
                continue
            lines.append(f"    {tid} -- blocks --x {target}")

    lines.append("")

    # Per-status class definitions
    for status, (fill, stroke, dash) in STATUS_STYLE.items():
        style = f"fill:{fill},stroke:{stroke},stroke-width:1px,color:#222"
        if dash:
            style += f",stroke-dasharray:{dash}"
        lines.append(f"    classDef {status} {style}")

    # Class assignments grouped by status
    by_status: dict[str, list[str]] = {}
    for task in tasks:
        by_status.setdefault(task["status"], []).append(task["id"])
    for status, members in by_status.items():
        lines.append(f"    class {','.join(members)} {status}")

    return "\n".join(lines) + "\n"


# EOF
