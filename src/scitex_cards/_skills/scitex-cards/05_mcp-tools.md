---
description: |
  [TOPIC] MCP Tools Reference
  [DETAILS] scitex-cards's FastMCP tool surface — Convention A (tool_name ==
  Python API name). Each tool is a thin async wrapper around the matching
  `scitex_cards.<api>` function and returns a JSON string of the same shape
  the Python API returns.
tags: [scitex-cards-mcp-tools]
---

# MCP Tools

```bash
scitex-cards mcp list-tools -vv     # enumerate registered tools
scitex-cards mcp doctor             # self-diagnose the install
scitex-cards mcp install            # print Claude Code config snippet
scitex-cards mcp start              # launch the FastMCP server (stdio)
```

## Tool surface (Convention A)

Tool name matches the Python API name 1:1 (no `scitex_cards_` prefix). Every
tool returns `json.dumps(...)` of the dict / list the matching Python API
returns.

| Tool | Python API | Purpose |
|---|---|---|
| `add_task` | `scitex_cards.add_task` | Append a new task to the store. |
| `update_task` | `scitex_cards.update_task` | Mutate fields of an existing task. |
| `complete_task` | `scitex_cards.complete_task` | Mark done + stamp `_log_meta.completed_{at,by}`. |
| `list_tasks` | `scitex_cards.list_tasks` | Filter the store by scope / assignee / status. |
| `summarize_tasks` | `scitex_cards.summarize_tasks` | Counts by status / scope / assignee. |
| `resolve_store` | `scitex_cards.resolve_store` | Resolved store path + the precedence chain. |
| `cards_skills_list` | (skills introspection) | List bundled agent skills (file names). |
| `cards_skills_get` | (skills introspection) | Get the content of one bundled skill by name. |

## ⚠️ A deadline is a VIEW, never a notifier

`add_task` / `update_task` accept `deadline` / `deadlines` / `scheduled`, with
an org-style recurring repeater (`+1d` / `+1w` / `+1m` / `+1y`). Read this
before you rely on it:

- **A deadline NEVER sends a notification.** Nothing fires when one arrives:
  no sweep, digest or nudge reads `deadline`. If you set one expecting to be
  pinged, you will wait forever.
- **A recurring deadline is not a recurring reminder** — and it is worse than
  just "silent". The repeater rolls the next occurrence *forward*, so it is
  always in the future, which means **`list_tasks(overdue=True)` never matches
  a recurring card either.** It drives neither rail; it is a date-pill on the
  board showing the next occurrence.
- A **one-off** deadline (no repeater) *does* match `list_tasks(overdue=True)`
  once it passes — but that is a PULL filter. Nothing pushes it to you; you
  have to run the query.

So:

- **To be NUDGED about ongoing work** — keep the card open and owned. Nudges
  key on INACTIVITY (`last_activity`, falling back to `created_at`): the
  stale-active sweep nudges the owner of any `in_progress` / `blocked` card
  untouched beyond the threshold, and the backlog sweep does the same for
  untouched `deferred` cards.
- **To act on deadlines** — poll `list_tasks(overdue=True)` yourself, on a
  one-off deadline.

## Store resolution (every tool)

`tasks_path` argument → `$SCITEX_CARDS_DB` → the user-canonical
database. See [20_env-vars.md](20_env-vars.md).

## Scope filtering

`list_tasks` / `summarize_tasks` honor `$SCITEX_CARDS_SCOPE` as the default
`scope` value. Pass `scope=""` (empty string) to opt out of that env
default and see every task. The agent-facing convention these tools
respect is documented in [02_quick-start.md](02_quick-start.md).

## Discovering tool names at runtime

```bash
scitex-cards mcp list-tools --json | jq '.[].name'
```

See `general/03_interface_03_mcp/` for the ecosystem-wide MCP tool grammar.
