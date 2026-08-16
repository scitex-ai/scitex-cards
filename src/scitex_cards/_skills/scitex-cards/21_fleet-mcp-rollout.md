---
description: |
  [TOPIC] Fleet-wide scitex-cards MCP rollout — the canonical `.mcp.json`
  block and the binding "MCP-only for durable cards" mandate.
  [WHEN] Read on every agent boot. This is the leaf that locks in WHY
  scitex-cards is the single durable-card wire AND HOW to wire it from a
  Claude-Code container — one copy-pasteable JSON block, one env var,
  one smoke test. Agent-container drops this `.mcp.json` into every
  agent's `to_home/.mcp.json` per the P3a wave (lead-dispatched).
  [HOW] Copy the JSON below into your `~/.mcp.json` (or the
  agent-container `to_home/_base/.mcp.json` for fleet rollout). Set
  `SCITEX_CARDS_AGENT_ID=<your-peer-name>` in the agent's env. Verify with
  `scitex-cards mcp doctor`.
tags:
  [
    scitex-cards-fleet-mcp-rollout,
    scitex-cards-mcp-canonical,
    scitex-cards-p3a,
  ]
---

# Fleet MCP rollout — the canonical wire

scitex-cards is **THE fleet's single source of truth** for durable /
cross-session / cross-agent cards (operator standing directive +
lead-confirmed mandate in [SKILL.md](./SKILL.md#-mandate--single-source-of-truth-operator--lead-2026-06-12)).
This leaf is the **canonical fleet-rollout artifact**: one
copy-pasteable `.mcp.json` block, one mandatory env var, one smoke
test. Drop this into every agent's `to_home/.mcp.json` and the MCP
wire is live.

## ⚑ Mandate (binding)

1. **All durable cards go through the scitex-cards MCP.** Every
   every worker agent, the lead, and the operator write to the same
   shared SQLite store via the wire below. There is **one**
   shared store; there are **no** parallel formats.

2. **No private card files.** Do not create
   `GITIGNORED/FUTURE/*.md`, `GITIGNORED/CARD.md`,
   `GITIGNORED/RUNNING/*.md`, or any other markdown / YAML /
   JSON / SQLite stand-in for the durable backlog. The harness
   `TaskList` is in-session SCRATCH ONLY — anything that should
   survive the turn goes in scitex-cards.

3. **CLI is an equivalent fallback, not a parallel path.** Inside a
   Claude-Code agent container the MCP wire is preferred. The
   `scitex-cards` CLI executes the same Python API and writes the
   same store; pick whichever is more ergonomic, but never invent a
   third wire.

If you have legacy notes outside scitex-cards, migrate the ACTIONABLE
rows on the next turn that touches them (see [11_adopting-from-a-project.md](./11_adopting-from-a-project.md)).

## The canonical `.mcp.json` block

Paste this verbatim into your agent's `~/.mcp.json` (or, for the fleet
rollout, into `agent-container/to_home/_base/.mcp.json`):

```json
{
  "mcpServers": {
    "scitex-cards": {
      "command": "scitex-cards",
      "args": ["mcp", "start"]
    }
  }
}
```

That's the entire wire. The `scitex-cards` CLI ships in the
`scitex-cards[all]` extra (see [01_installation.md](./01_installation.md));
`mcp start` launches the FastMCP stdio server the Claude-Code harness
talks to.

If you already have other MCP servers in `~/.mcp.json`, merge the
`scitex-cards` key under the existing `mcpServers` map — do **not**
overwrite the file.

## Required + recommended env

Every write tags the agent via env vars. **Set `SCITEX_CARDS_AGENT_ID`
before the MCP server starts** — a missing tag is a config bug, not
a soft default.

| Var | Required? | Value | Effect |
|---|---|---|---|
| `SCITEX_CARDS_AGENT_ID` | **YES** | `<your-peer-name>` | Stamps every write's `_log_meta.created_by` / `updated_by`. The board's "by agent" lens, throughput stats, and notify routing all key off this. |
| `SCITEX_CARDS_SCOPE` | recommended | `agent:<your-peer-name>` | Default scope for `list_tasks` / `summarize_tasks` so the agent sees its own slice by default. Pass `scope=""` to opt out per-call. |
| `SCITEX_CARDS_DB` | only if non-default | Absolute path to the SQLite database | Pins the store. Default resolution (explicit → env → user-canonical) usually picks the right one without this. |

For agent-container's `to_home/_base/.mcp.json` rollout, the per-agent
`SCITEX_CARDS_AGENT_ID` value is templated from the agent's name; see the
P3a wiring on the agent-container side.

## Tool surface — 15 today (CLI parity reached via PR #144)

| Tool | Python API | Purpose |
|---|---|---|
| `add_task` | `scitex_cards.add_task` | Append a new task. |
| `update_task` | `scitex_cards.update_task` | Mutate fields of an existing task. |
| `complete_task` | `scitex_cards.complete_task` | Mark done + stamp `_log_meta.completed_*`. |
| `list_tasks` | `scitex_cards.list_tasks` | Filter by scope / assignee / status / etc. |
| `summarize_tasks` | `scitex_cards.summarize_tasks` | Counts by status / scope / assignee. |
| `resolve_store` | `scitex_cards.resolve_store` | Resolved path + the precedence chain. |
| `get_task` | `scitex_cards.get_task` | Return one task by id. |
| `delete_task` | `scitex_cards.delete_task` | Soft-delete (restorable). |
| `restore_task` | `scitex_cards.restore_task` | Undo `delete_task`. |
| `comment_task` | `scitex_cards.comment_task` | Append a comment to `comments[]`. |
| `set_edge` | `scitex_cards.set_edge` | Add / remove a `depends_on` / `blocks` edge. |
| `resolve_task` | `scitex_cards.resolve_task` | Mark a blocker resolved. |
| `reopen_task` | `scitex_cards.reopen_task` | Reopen a `done` / `failed` task. |
| `cards_skills_list` | (skills introspection) | List bundled skills. |
| `cards_skills_get` | (skills introspection) | Get one bundled skill by name. |
| `add_comment` *(deferred)* | `scitex_cards.add_comment` | Convention-A alias for `comment_task`. PR #64 not yet merged; CLI parity was reached via PR #144 (`scitex-cards comment`) which wraps the existing `comment_task` MCP tool — agents needing the activity-append do NOT have to wait. |

Discover the live set at runtime:

```bash
scitex-cards mcp list-tools --json | jq '.[].name'
```

## Install — one command

```bash
# Recommended (uv resolver, fast):
uv pip install -U 'scitex-cards[all]>=0.7.1'

# Plain pip equivalent:
pip install -U 'scitex-cards[all]>=0.7.1'
```

Version pin: **≥ 0.7.1** is the PR #115 floor — that's the release that
ships the 10-minute structural-nudge cron + `--nudge-quiet` flag the
fleet feedback loop depends on. Earlier `0.5.x` works but lacks the
nudge wire.

## Smoke test — 30 seconds end-to-end

```bash
# 1. The CLI resolves + the FastMCP server self-diagnoses.
scitex-cards --version
scitex-cards mcp doctor
# expected: status: ok / fastmcp: 3.x / tools: 15
# (PR #64 would add `add_comment` as #16; deferred — CLI parity reached via PR #144)

# 2. The MCP wire reaches the live store.
SCITEX_CARDS_AGENT_ID=<you> scitex-cards list-tasks --by-agent --json | head

# 3. The agent can write.
SCITEX_CARDS_AGENT_ID=<you> scitex-cards add \
    <you>-mcp-smoke-$(date +%s) \
    '[P2] smoke: confirm MCP wire' \
    --scope agent:<you> \
    --assignee <you> \
    --agent <you>

# 4. Tear it down.
SCITEX_CARDS_AGENT_ID=<you> scitex-cards done <you>-mcp-smoke-<stamp>
```

Pass all four = wire is live. Any failure: post the exact command +
full stderr to your lead via a2a, then stop. Do NOT retry-loop.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `mcp doctor` → "fastmcp: MISSING" | `[mcp]` extra not installed | `pip install -U 'scitex-cards[all]>=0.7.1'` |
| Writes land but `_log_meta.created_by` is unset / wrong | `SCITEX_CARDS_AGENT_ID` missing or stale in the agent's env | Set it BEFORE the MCP server boots; restart the harness. |
| `mcp doctor` → "tools: 0" | FastMCP version skew | Bump fastmcp to ≥ 3.0; rebuild the venv. |
| `list_tasks` returns the whole store, not your slice | `SCITEX_CARDS_SCOPE` unset | Export `SCITEX_CARDS_SCOPE=agent:<you>`. |
| Your `SCITEX_CARDS_TASKS_YAML_SHARED` points at a stale file | precedence chain picked an earlier tier | `scitex-cards resolve-store` prints the resolved path + the chain. |

## Cross-references

- [SKILL.md](./SKILL.md) — the entry leaf + the operator-mandate context.
- [05_mcp-tools.md](./05_mcp-tools.md) — full tool schema reference.
- [20_env-vars.md](./20_env-vars.md) — store-resolution chain detail.
- [42_for-consuming-agents.md](./42_for-consuming-agents.md) — full
  onboarding for a fresh consuming agent (CLI emphasis).
- `general/03_interface_03_mcp/` — ecosystem-wide MCP tool grammar.
