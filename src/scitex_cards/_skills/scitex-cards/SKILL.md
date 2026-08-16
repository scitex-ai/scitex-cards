---
name: scitex-cards
description: |
  [WHAT] Canonical SQLite task store with pluggable adapters — validate
  task rows (id/title/status + depends_on/blocks/priority/parent) and
  render them as a mermaid dependency graph (PNG), a read-only React-Flow web
  board, or a plain task listing.
  [WHEN] **Use scitex-cards for EVERY durable / cross-session / cross-agent
  card.** When the user wants to "track tasks as a dependency graph",
  "render my card as a diagram", "show what blocks what", "list my
  tasks", or "launch the card board" — AND any time YOU are
  about to write a private CARD / FUTURE / notes file in your repo's
  `GITIGNORED/` for something that should persist or be operator- or
  peer-visible.
  [HOW] `import scitex_cards as card` for the Python API; `scitex-cards --help`
  for the CLI; **or the MCP tools** (`add_task`, `update_task`,
  `comment_task`, `list_tasks` — see [05_mcp-tools.md](05_mcp-tools.md)) —
  THE preferred wire from inside an agent container.
tags: [scitex-cards]
primary_interface: python
interfaces:
  python: 3
  cli: 2
  mcp: 0
  skills: 2
  http: 0
---

# scitex-cards

A canonical SQLite task store with pluggable adapters. The DB (one
`tasks` table) is the single source of truth; adapters render or import it.
Store identity is `$SCITEX_CARDS_DB` — run `scitex-cards resolve-store` to
see what this process resolved and which tier supplied it. There is no
zero-config default: an unconfigured target REFUSES rather than inventing a
file.

## ⚑ THE THREE MANDATES

**1. Single source of truth** (operator + lead, 2026-06-12). This is THE
fleet store for all durable / cross-session / cross-agent tracking. Use the
MCP tools for every card (CLI is the equivalent fallback). Do NOT create
parallel card formats — no private task-markdown, no per-agent
`GITIGNORED/FUTURE/*.md`. The harness `TaskList` is SCRATCH ONLY; anything
that must survive the turn, reach a peer, or carry a deadline / blocker /
dependency goes here. A stale row is cheap; a missing row is invisible.

**2. Never hand-edit the store** (lead a2a `02c8a4ae`, 2026-06-13). No
manual SQL, no editing a raw export and re-importing it. A hand-edit
bypasses `_validate_tasks`, races concurrent writers, and skips the audit
trail. Read and mutate through the API — CLI verbs, MCP tools, or
`scitex_cards._store`. Exception: a store that is ALREADY broken cannot be
repaired through the API; back it up, verify the repair validates, and
report it. Rationale and the corruption episode:
[30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md).

**3. Record evidence at PR-merge / issue-close time** (op-2026-06-13). A
card is NOT done until its completion is recorded WITH the evidence link:

```bash
scitex-cards done <card-id> --pr-url <merged-PR-URL>
```

`--pr-url` is REQUIRED, not optional — a bare `done <id>` is a gap the
reconcile pass cannot verify later. No PR? Record a `comment_task` naming the
evidence just before the flip, as part of the merge rather than a follow-up
card. Bulk catch-up: `scitex-cards sync-github --since <date> -y`. Rationale
(the 完了率 metric it feeds), the no-PR path and the per-wire verbs:
[60_pr-merge-recording-mandate.md](60_pr-merge-recording-mandate.md).

## Who writes what

You own rows where `task.agent == <you>` — never edit another agent's
fields, append a `comments[]` entry instead. Every write tags you via
`SCITEX_CARDS_AGENT_ID`; a missing tag is a config bug in the agent's spec,
not something to work around. Full protocol table in 30. The board IS your
work queue — the 7-step wake loop is in 32.

## Sub-skills

**Core (01–09)**
- [01_installation.md](01_installation.md) — install + import check
- [02_quick-start.md](02_quick-start.md) — load → build_mermaid → render
- [03_python-api.md](03_python-api.md) — public callables + schema
- [04_cli-reference.md](04_cli-reference.md) — `scitex-cards` subcommands
- [05_mcp-tools.md](05_mcp-tools.md) — the MCP tool surface (Convention A)

**Workflows (10+)**
- [10_campaign-tracking.md](10_campaign-tracking.md) — release campaigns
- [11_adopting-from-a-project.md](11_adopting-from-a-project.md) — 30-second adoption. **READ FIRST** if you are not on the board

**Meta (20+)**
- [20_env-vars.md](20_env-vars.md) — env vars and local state
- [21_fleet-mcp-rollout.md](21_fleet-mcp-rollout.md) — canonical `.mcp.json` block + MCP-only rule
- [22_pretooluse-hook-redirect.md](22_pretooluse-hook-redirect.md) — redirects private task files here
- [22_skills-propagation.md](22_skills-propagation.md) — `required_skills` propagation
- [23_stop-hook-second-delivery-rail.md](23_stop-hook-second-delivery-rail.md) — Stop hook, second delivery rail

**Architecture (30+)**
- [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md) — tiers, store resolution, write protocol (how-to: 11)
- [31_fleet-ports-sync-and-citation.md](31_fleet-ports-sync-and-citation.md) — ports, sync, citation, auto-merge poll
- [32_agent-self-consumption-loop.md](32_agent-self-consumption-loop.md) — the 7-step agent loop; all agents read this

**Operations (40+)**
- [40_task-harvest.md](40_task-harvest.md) — blocker-driven backlog consumption
- [45_blocker-taxonomy.md](45_blocker-taxonomy.md) — the four blocker values
- [46_task-harvest-cadence-and-routing.md](46_task-harvest-cadence-and-routing.md) — cron cadence + funnel routing
- [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md) — CLI / MCP / Python gap audit (partly done)
- [42_for-consuming-agents.md](42_for-consuming-agents.md) — **start here if told to use scitex-cards**
- [43_consuming-agent-schema-and-crud.md](43_consuming-agent-schema-and-crud.md) — closed-enum schema + CRUD verbs
- [44_consuming-agent-coordination.md](44_consuming-agent-coordination.md) — board coordination + the lead-worker wire
- [50_board-reconciliation-runbook.md](50_board-reconciliation-runbook.md) — reconciliation verbs and sweep
- [60_pr-merge-recording-mandate.md](60_pr-merge-recording-mandate.md) — long-form of mandate 3
