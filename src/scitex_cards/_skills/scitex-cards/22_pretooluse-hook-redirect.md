# PreToolUse hook — redirect Claude `TaskCreate`/`TaskUpdate`/`TaskList` to scitex-todo

Operator op-12038 doctrine: **every fleet agent uses ONE shared task
store** — scitex-todo's YAML. Claude Code's built-in `TaskCreate` /
`TaskUpdate` / `TaskList` tools create per-session scratch state
that disappears when the turn ends; using them for durable work
fragments the fleet's source of truth.

This skill bundles a PreToolUse hook that intercepts those three
tool names and BLOCKS them with a redirect message naming the
scitex-todo CLI verb to use instead. The block is enforced
(non-zero exit), not just a warning — the operator's directive is
"redirect, don't allow drift."

## Install

`scitex-todo skills install --claude-symlink` symlinks the whole
skill tree under `~/.claude/skills/scitex/scitex-todo/`. From there,
drop a symlink into Claude's hooks dir:

```bash
ln -sf ~/.claude/skills/scitex/scitex-todo/hooks/pre-tool-use/redirect_claude_tasklist_to_scitex_cards.sh \
       ~/.claude/hooks/pre-tool-use/redirect_claude_tasklist_to_scitex_cards.sh
```

The script is shipped executable; no further setup is needed.

## Behavior

* Parses `tool_name` from the PreToolUse JSON event on stdin.
* `tool_name ∈ {TaskCreate, TaskUpdate, TaskList}` → write a
  redirect message to stderr (Claude sees it) and exit 2 (block).
* Any other `tool_name` → exit 0 fast.
* `CC_ALLOW_CLAUDE_TASKLIST=1` → exit 0 unconditionally (rare
  legit per-session scratch use).

## Redirect copy

The stderr message names the scitex-todo CLI verbs:

```
scitex-todo add <id> --title "..." --assignee <you>
scitex-todo update <id> --status in_progress
scitex-todo list-tasks --assignee <you> --status in_progress
```

…plus a pointer to the MCP wire (`add_task` / `update_task` /
`list_tasks` — see [05_mcp-tools.md](05_mcp-tools.md)).
