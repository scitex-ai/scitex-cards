---
description: |
  [TOPIC] Quick Start
  [DETAILS] Read configured PostgreSQL cards through the public Python API
  and CLI; inspect the dependency graph and identify rendering requirements.
tags: [scitex-cards-quick-start]
---

# Quick Start

Configure `SCITEX_STORE_DSN` for the intended PostgreSQL database before
running these examples. Store discovery belongs to scitex-dev; installation
does not provision a database. YAML exports are backup/interchange data.

## Python

```python
import scitex_cards as cards

rows = cards.list_tasks(status="in_progress")
print(f"{len(rows)} cards in progress")
```

Creating a card also requires a registered assignee and a creator identity.
See the public `add_task` signature in the API reference before writing data.
The current statuses are `goal`, `in_progress`, `blocked`, `done`, `deferred`,
`failed` and `cancelled`; `pending` is rejected.

## CLI

```bash
# inspect the generated Mermaid without rendering or writing a file
scitex-cards render-graph --print-mermaid

# list the resolved tasks (machine-readable)
scitex-cards list-tasks --json

# start the browser board; Django and scitex-sdk are core dependencies
scitex-cards board --port 8051
```

To render a PNG, use `scitex-cards render-graph -o tasks.png`. This writes a
file and uses local `mmdc` when available, with a network fallback to `kroki.io`.
