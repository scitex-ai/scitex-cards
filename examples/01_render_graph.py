#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Example: a task set -> mermaid dependency-graph source.

Builds the mermaid ``flowchart TB`` source for a small set of cards and prints
it. Rendering to PNG (``scitex-cards render-graph``) additionally needs
``mmdc`` or ``kroki.io``; this example stops at the mermaid source so it runs
fully offline with no dependencies beyond ``scitex-cards`` itself.

The cards are DEFINED HERE rather than loaded from a store. This example used
to call ``bundled_example()`` and read a ``tasks.yaml`` shipped inside the
wheel — that fixture was removed on 2026-07-19 because the store resolver
treated it as a FALLBACK, which made a packaged demo file eligible to become
the fleet's live board. An example that needs a store to exist is also an
example that breaks the moment the store layer changes; carrying its own data
makes it independent of both.

Run:
    python 01_render_graph.py
"""

from __future__ import annotations

from scitex_cards._diagram import build_mermaid

#: A minimal dependency chain: two blockers feeding one deliverable.
TASKS = [
    {
        "id": "design",
        "title": "Design the thing",
        "status": "done",
        "assignee": "agent:alice",
    },
    {
        "id": "build",
        "title": "Build the thing",
        "status": "in_progress",
        "assignee": "agent:bob",
        "depends_on": ["design"],
    },
    {
        "id": "ship",
        "title": "Ship the thing",
        "status": "blocked",
        "blocker": "dependency",
        "assignee": "agent:alice",
        "depends_on": ["build"],
    },
]

if __name__ == "__main__":
    mermaid_src = build_mermaid(TASKS)

    print(f"# {len(TASKS)} tasks")
    print(mermaid_src)

# EOF
