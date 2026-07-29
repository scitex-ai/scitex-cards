---
description: |
  [TOPIC] Quick Start
  [DETAILS] 30-second tour — load the task store, build mermaid source,
  render to PNG (Python), plus the equivalent CLI one-liners.
tags: [scitex-cards-quick-start]
---

# Quick Start

## Python

```python
# Audit §6 narrows the top-level surface to the six task-store APIs that
# match MCP tool names 1:1 (add_task/update_task/complete_task/list_tasks/
# summarize_tasks/resolve_store). The mermaid + render + model helpers
# remain available via submodule imports.
from scitex_cards._model   import load_tasks
from scitex_cards._diagram import build_mermaid
from scitex_cards._diagram  import render

tasks = load_tasks()                       # validates id / title / status
mermaid_src = build_mermaid(tasks)         # store -> flowchart TB
engine = render(mermaid_src, "tasks.png")
print(f"rendered via {engine}")            # 'mmdc' or 'kroki'
```

A minimal task list (the shape `load_tasks` returns):

```
tasks:
  - {id: design, title: Design, status: done}
  - {id: build, title: Build, status: in_progress, depends_on: [design]}
  - {id: ship, title: Ship, status: goal, depends_on: [build]}
```

## CLI

```bash
# default store: $SCITEX_CARDS_DB, else the user-canonical database
scitex-cards render-graph -o tasks.png

# inspect the generated mermaid without rendering
scitex-cards render-graph --print-mermaid

# list the resolved tasks (machine-readable with --json)
scitex-cards list-tasks --json

# read-only dependency-graph web board (needs the [web] extra)
scitex-cards board --port 8051
```
