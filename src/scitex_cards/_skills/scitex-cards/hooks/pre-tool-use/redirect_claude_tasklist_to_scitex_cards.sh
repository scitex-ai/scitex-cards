#!/bin/bash
# -*- coding: utf-8 -*-
# Timestamp: "2026-06-14 (proj-scitex-todo)"
# File: src/scitex_cards/_skills/scitex-todo/hooks/pre-tool-use/redirect_claude_tasklist_to_scitex_cards.sh
#
# Description: PreToolUse hook — when ANY Claude-Code agent calls the
# built-in `TaskCreate` / `TaskUpdate` / `TaskList` tool, BLOCK the call
# and instruct the agent to use scitex-todo's shared YAML store
# instead. Realizes the operator's single-shared-store doctrine
# (op-12038): fleet tasks live in scitex-todo, NOT in Claude's
# per-session list.
#
# Install: drop into ~/.claude/hooks/pre-tool-use/ (the `scitex-todo
# skills install` verb symlinks the whole skill tree, so this hook
# ships with the bundle automatically).
#
# Behavior contract:
#   * Parses `tool_name` from stdin JSON (Claude-Code PreToolUse event).
#   * tool_name ∈ {TaskCreate, TaskUpdate, TaskList} → write a redirect
#     message to stderr and EXIT 2 (block the tool call).
#   * Any other tool → exit 0 fast (<50ms target).
#   * Opt-out: `CC_ALLOW_CLAUDE_TASKLIST=1` lets the call through
#     (for rare legit per-session scratch use).

set -u

# Escape hatch — for the rare legit use of Claude's per-session list.
[[ "${CC_ALLOW_CLAUDE_TASKLIST:-}" == "1" ]] && exit 0

input=$(cat)

tool_name=$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    print(json.load(sys.stdin).get("tool_name", ""))
except Exception:
    print("")
' 2>/dev/null)

case "$tool_name" in
    TaskCreate|TaskUpdate|TaskList) ;;
    *) exit 0 ;;
esac

cat >&2 <<EOF
[scitex-cards redirect] you called ${tool_name}. Project doctrine
(operator op-12038): fleet tasks live in scitex-cards' shared
store, NOT Claude's per-session list.

Use instead:
  scitex-cards add <id> --title "..." --assignee <you>
  scitex-cards update <id> --status in_progress
  scitex-cards list-tasks --assignee <you> --status in_progress

Or use the scitex-cards MCP server (already in .mcp.json on every
container — see skill \`scitex-cards-usage\`).

Opt-out (rare): CC_ALLOW_CLAUDE_TASKLIST=1
EOF
exit 2

# EOF
