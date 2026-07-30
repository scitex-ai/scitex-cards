---
description: |
  [TOPIC] CLI Reference
  [DETAILS] scitex-cards CLI subcommands (Click, noun-verb) — render-graph,
  list-tasks, board, plus the standard introspection / completion / skills
  commands and universal flags (--help-recursive, --json).
tags: [scitex-cards-cli-reference]
---

# CLI Reference

```bash
scitex-cards --help
scitex-cards --help-recursive    # flattened help for every subcommand
```

| Command | Purpose |
|---|---|
| `scitex-cards render-graph [-o PNG] [--tasks PATH] [--print-mermaid]` | Render the dependency graph to PNG (or print mermaid source). |
| `scitex-cards list-tasks [--tasks PATH] [--scope X] [--assignee X] [--status X] [--json]` | List resolved tasks (filter by scope / assignee / status). |
| `scitex-cards board [--port N] [--tasks PATH] [--no-browser]` | Launch the read-only web board (needs the `[web]` extra). |
| `scitex-cards add ID TITLE [--scope X] [--status X] [--assignee X] [--priority N] [--note ...] [--repo X] [--dry-run] [-y]` | Append a new task. |
| `scitex-cards update TASK_ID [--title X] [--status X] [--scope X] [...] [--dry-run] [-y]` | Mutate fields of an existing task. |
| `scitex-cards done TASK_ID [--by AGENT]` | Mark done + stamp `_log_meta.completed_{at,by}`. |
| `scitex-cards summary [--scope X] [--assignee X] [--json]` | Counts by status / scope / assignee. |
| `scitex-cards resolve-store [--tasks PATH] [--json]` | Print the resolved store path + precedence chain. |
| `scitex-cards init-store [--shared\|--project] [--dry-run] [-y]` | Materialize an empty `tasks: []` store at the chosen scope. |
| `scitex-cards sync-store [--dry-run\|--apply] [--remote X] [-y]` | Phase-1 stub — Phase-2 will `git pull --rebase --autostash && git push` the user-scope store. |
| `scitex-cards list-python-apis [-v/-vv/-vvv] [--json]` | Introspect the public Python API. |
| `scitex-cards mcp {start,doctor,list-tools,install}` | MCP server subcommands. |
| `scitex-cards skills {list,get,install}` | List / print / install the bundled agent skills. |
| `scitex-cards install-shell-completion [--shell bash\|zsh\|fish]` | Install tab-completion (cache-file pattern). |
| `scitex-cards print-shell-completion [--shell ...]` | Print the completion script for eval/sourcing. |

## Universal flags

- `-h`, `--help` — usage with an example (every command).
- `--help-recursive` — flatten help for all subcommands (top level).
- `--json` — machine-readable output on every data-reading command.
- `-V`, `--version` — print `scitex-cards/X.Y.Z`.

## Store resolution

Every command resolves the task store the same way: an explicit path →
`$SCITEX_CARDS_DB` → the user-canonical database. See
[20_env-vars.md](20_env-vars.md).

See `general/03_interface_02_cli/` for the ecosystem-wide CLI grammar.
