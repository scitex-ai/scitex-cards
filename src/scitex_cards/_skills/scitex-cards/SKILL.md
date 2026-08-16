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

A canonical SQLite task store with pluggable adapters. The database
(one `tasks` table) is the single source of truth; adapters render or
import it. The mermaid adapter (store → dependency PNG) and a read-only web
board ship today; org-mode and drag-to-reprioritize are on the roadmap.

The store identity is `$SCITEX_CARDS_DB` (explicit env var wins,
otherwise the user-canonical `~/.scitex/cards/cards.db` — see
`scitex-cards resolve-store`).

## ⚑ MANDATE — single source of truth (operator + lead, 2026-06-12)

scitex-cards is **THE fleet's single source of truth** for all durable /
cross-session / cross-agent task tracking. Every agent (workers + lead +
the operator) writes here; every viewer reads from here. This is the
binding rule:

- **USE the scitex-cards MCP** (`add_task`, `update_task`,
  `comment_task`, `list_tasks` — see [05_mcp-tools.md](05_mcp-tools.md))
  for every card. From a Claude-Code agent container, the MCP wire is
  the preferred path; the CLI is the equivalent fallback.
- **DO NOT create parallel card formats.** No private task-markdown, no
  per-agent `GITIGNORED/FUTURE/*.md` / `GITIGNORED/CARD.md` /
  `GITIGNORED/RUNNING/*.md` for durable tracking; **migrate them into
  the shared store** the moment the underlying task is actionable.
- **The harness `TaskList` is in-session SCRATCH ONLY.** Use it for a
  single turn's check-off list — it disappears when the turn ends.
  Anything that should persist to the next turn, be visible to the
  operator or a peer, or carry a deadline / blocker / dependency goes
  in scitex-cards.
- **When in doubt, write to scitex-cards.** A stale entry is cheap to
  update; a missing entry is invisible (operator + lead + every other
  agent can't react to what isn't on the board).

## ⚑ MANDATE — NEVER hand-edit the store (lead a2a `02c8a4ae`, 2026-06-13)

Real corruption episode: on 2026-06-13 the (then file-based) shared
store was found **truncated mid-string**. The board render broke, the
throughput script broke, AND every agent's read/write through
scitex-cards broke until the lead repaired it by hand. PR-#166's
post-dump round-trip-validate layer made the WRITER side safer going
forward — but only for writes through scitex-cards's API. The store
has since moved to SQLite, which removes that specific failure class,
but the underlying rule is unchanged: a hand-edit (direct SQL, a text
editor on the file, GUI save on a raw export) bypasses every safety
net the package provides.

The binding rule:

- **NEVER hand-edit the store directly.** No manual SQL. No editing a
  raw export and re-importing it as canonical. The store is a
  binary-style asset from your point of view: read via the API,
  mutate via the API, write via the API.
- **Always use one of**: the `scitex-cards` CLI verbs (`add`,
  `update`, `done`, `comment`, `close`, `delete`, `sync-github`,
  `migrate-*`), the MCP tools (`add_task`, `update_task`,
  `comment_task`, `complete_task`, `delete_task`, …), or the
  Python API (`from scitex_cards._store import add_task,
  update_task, …`). The MCP wire is preferred from inside an
  agent container — P3a wired it into every container's
  `.mcp.json` precisely so no agent needs to hand-edit.
- **Emergency repair exception**: a store that is ALREADY broken
  (won't open / validate) cannot be repaired through the API. In
  that single case a hand-repair is justified — but you MUST
  (a) back up the broken store first, (b) verify the repaired store
  validates cleanly before declaring done, (c) report the episode to
  the lead so the API-side safety net can be hardened against
  whatever caused the breakage. The lead's 2026-06-13 repair followed
  this exact protocol.

Rationale: the store is the fleet's single ledger. Hand-edits don't
just risk corruption — they also race with concurrent agent writes,
bypass `_validate_tasks` (so a typo lands as accepted schema), and
skip the audit trail (so the operator loses time-travel recovery on
the bad version). Use the API.

Enforcement: cultural for now (this skill is propagated to every
agent via `scitex-cards skills propagate` per PR #161, so every
agent reads it on boot).

## ⚑ MANDATE — record evidence at PR-merge / issue-close time (op-2026-06-13, lead a2a `0cdca03a`)

This is the load-bearing rule that closes the **board-recording gap**
the operator's 完了率 metric depends on. A card is **NOT done** until
its completion is recorded WITH the evidence link. Without the link,
the board can't show what consumption actually happened, and the
operator's view under-reports throughput by a wide margin (sweep
2026-06-13 found 199 PRs merged in 24 h vs ~5 board completions —
that gap is structural, this rule closes it).

The hard rule:

- **The moment you MERGE a PR that completes a board card** — or
  CLOSE an issue that does the same — you MUST IMMEDIATELY call:

  ```bash
  scitex-cards done <card-id> --pr-url <merged-PR-URL>
  ```

  (or equivalently, via MCP / Python API:
  `update_task(task_id=<card-id>, pr_url=<url>, status="done")`).

- **The `--pr-url` is REQUIRED, not optional.** A bare `done <id>`
  without the evidence link is a recording-gap; the reconciliation
  pass can't verify completion later, so the card silently lingers
  as pending on the operator's view. If the work has NO PR (rare —
  e.g. a config flip on the host), record evidence as a
  `comment_task --text "no-PR completion: <one-line-evidence>"`
  immediately before the `done` flip.

- **Do this BEFORE you move on to the next task.** Treat the
  recording call as part of the merge sequence — not a follow-up
  CARD. Operator-stated rationale: a missing recording is a missing
  signal, and the fleet plans on the signal not on the work itself.

- **Bulk catch-up is also OK** when an agent realises a batch of
  past PRs was never recorded: `scitex-cards sync-github --since
  <date> -y` walks the agent's recent merged PRs and writes the
  `pr-<repo>-<num>` done-records in one shot (scitex-cards
  used this as the overnight backfill mechanism, lead a2a
  `fbd15187`).

Failure mode: a card whose work landed but never got the
`--pr-url` flip stays pending forever in the reconcile pass —
substring-luck is the only thing that catches it, and most cards
aren't named verbatim in PR titles/bodies. Don't be the gap; record
evidence at merge time.

### SSoT write-here rule (short form)

Same content, distilled to the one-screen lookup most agents need on
wake. Authoritative copy in HANDOFF.md `## SSoT DATA LAYOUT` and the
full write-protocol table in
[30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md).

| Actor          | Writes to                                                    | When                                                |
| -------------- | ------------------------------------------------------------ | --------------------------------------------------- |
| project agent  | own rows (`scope=agent:<self>`) in the shared database       | own tasks: create / status flip / blocker / comment |
| lead           | fleet-coordination rows in the same database                 | cross-project decisions                             |
| operator (UI)  | any row (Resolve button / re-priority / tag edits)            | unblocks `BLOCKING YOU`; sets `priority`            |
| sac-status-writer | `agents.json` (fleet liveness) — a separate machine artifact, never the task store | every 5 s on operator's host |

Three rules to internalize: (a) you own rows where `task.agent ==
<you>`; never edit another agent's fields — append a `comments[]`
entry instead. (b) Status flips on your own rows go via
`scitex-cards update --status` (or `comment_task` for activity-only).
(c) When in doubt, write to scitex-cards — a stale row is cheap to
update; a missing row is invisible to operator + lead + peers.

Read [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
in full before designing any new fleet workflow that would touch
task state.

### Using scitex-cards (CLI + MCP) — concrete how-to

Every fleet agent uses the same surface. The CLI works without the
`[mcp]` extra; the MCP surface (`add_task` / `update_task` /
`comment_task` / `list_tasks`) ships as `pip install
'scitex-cards[mcp]>=0.5.2'` per the P3a dotfiles wave. Pick whichever
is more ergonomic — the wire is identical.

```bash
# Register a new task (operator drop / agent self-add).
scitex-cards add --title "fix CI red on develop" \
  --project scitex-cards --agent scitex-cards --priority 2

# Flip status as you work. --add-comment stamps an activity row.
scitex-cards update cards-pXX --status in_progress \
  --add-comment "starting; PR draft soon"

scitex-cards update cards-pXX --status done \
  --pr-url https://github.com/.../pull/123 \
  --add-comment "merged; tests green"

# Append a comment without changing any other field (PR #144).
scitex-cards comment cards-pXX "lead a2a: please rebase before merging" \
  --author scitex-cards

# List the tasks for a project / agent.
scitex-cards list-tasks --agent scitex-cards
scitex-cards list-tasks --status pending --project scitex-cards

# Filter by kind — e.g. hide non-actionable status-tracking cards
# (q-* quality flags etc., PR #146 `kind: status`).
scitex-cards list-tasks --kind task          # actionable only
scitex-cards list-tasks --kind status        # status-tracking only

# Set / clear an edge (depends_on).
scitex-cards update cards-pYY --depends-on cards-pXX

# Pick the next runnable task FOR THIS AGENT (single canonical rule).
SCITEX_CARDS_AGENT_ID=scitex-cards \
  scitex-cards next --mine --auto-claim --json

# Push side — wake the owning agent on new/commented/changed tasks.
scitex-cards watch --push --interval 2
```

Equivalent MCP tools (Claude-Code container with the P3a stanza
landed): `add_task`, `update_task`, `comment_task`, `list_tasks`,
plus the upcoming `next` (P3d). Schema in
[05_mcp-tools.md](05_mcp-tools.md).

Attribution: every write tags the agent via `SCITEX_CARDS_AGENT_ID`
(P3a env). A missing tag is a config bug — fix the agent's
`to_home/.mcp.json` rather than committing under a wrong name.

### The board is your work queue

[32_agent-self-consumption-loop.md](32_agent-self-consumption-loop.md)
documents the 7-step loop every fleet agent runs on wake. The
operator drops a request → it lands as a task → `scitex-cards watch
--push` wakes the owning agent → the agent runs `scitex-cards next
--mine --auto-claim` → works → comments + flips status → the lead
monitors. Read 32 before wiring up a new agent's harness.

## Sub-skills

### Core (01–09)
- [01_installation.md](01_installation.md) — install + import sanity check
- [02_quick-start.md](02_quick-start.md) — load → build_mermaid → render
- [03_python-api.md](03_python-api.md) — public callables and the schema
- [04_cli-reference.md](04_cli-reference.md) — `scitex-cards` subcommands
- [05_mcp-tools.md](05_mcp-tools.md) — the MCP tool surface (Convention A)

### Workflows (10+)
- [10_campaign-tracking.md](10_campaign-tracking.md) — companion tools
  (`check_releases.py`, `campaign_report.py`) under `~/.scitex/cards/`
  for multi-package release/audit campaigns
- [11_adopting-from-a-project.md](11_adopting-from-a-project.md) — the
  30-second adoption path: how a project agent (clew / neurovista /
  scitex-dev / scitex-hub / ripple-wm / scitex-orochi / scitex-agent-
  container / etc.) writes its tasks to the shared store so
  the operator's live board (http://127.0.0.1:8051/) auto-renders the
  agent's column. Operator-decision blockers + GUI Resolve loop are
  covered here too. **READ THIS FIRST** if your agent doesn't yet
  appear on the operator's board.

### Meta (20+)
- [20_env-vars.md](20_env-vars.md) — environment variables and local state
- [21_fleet-mcp-rollout.md](21_fleet-mcp-rollout.md) — **canonical
  `.mcp.json` block + binding "MCP-only durable cards" mandate** for
  the fleet-wide P3a rollout (agent-container `to_home/_base/.mcp.json`)
- [22_skills-propagation.md](22_skills-propagation.md) — **fleet-wide
  `required_skills` propagation** via the bundled manifest +
  `scitex-cards skills propagate --agents-dir <DIR>` idempotent sweep.
  Sister artifact to 21 (which covers the `.mcp.json` side).

### Architecture (30+)
- [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
  — full fleet spec: project tier vs global tier, write-protocol table
  (who writes when), cross-host sync (git-backed durable + SSH-fanout
  live), task referencing scheme, push-notification model, Core vs
  Extension Ports vs Fleet Adapters architectural backbone. Reference
  spec; for the short how-to, use 11.
- [32_agent-self-consumption-loop.md](32_agent-self-consumption-loop.md)
  — the **7-step agent loop**. Every fleet agent reads this. Pairs
  with the `scitex-cards next` (pull side) + `scitex-cards watch --push`
  (push side) CLI verbs to realize the operator's TG 12038
  central-command vision: the board IS the work queue, agents drain
  the backlog autonomously, the lead coordinates and escalates.

### Operations (40+)
- [40_task-harvest.md](40_task-harvest.md) — task harvest:
  blocker-driven backlog consumption. Two-state model (BLOCKED with
  reason+dependency from a 4-value enum vs RUNNABLE), 2-phase harvest
  pass (Phase 1 re-check blockers + walk `task-dependency` chains to
  their LEAF / root blocker; Phase 2 escalate every RUNNABLE task to
  its owning agent), lead-centric funnel routing, and registration as
  a `scitex-dev cron` JobSpec (the ecosystem plugin pattern, same
  shape as `watch-ci` / `quota-keepalive`). Keeps consumption rate >
  arrival rate so the board doesn't drift out of sync with the
  codebase. Name locked by operator TG 332 + 335: must carry "task";
  no "branch" / "graph" metaphors.
- [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md) — CLI /
  MCP / Python API gap audit. Several items have shipped since the
  original audit (comment verb in 0.5.x; multi-status + agent /
  project / host / blocker filters partially shipped via PR #102 /
  PR #104 search-qualifiers + the WRITE-side flags). Re-check before
  opening any "still missing" follow-up.

### For consuming agents (42+)
- [42_for-consuming-agents.md](42_for-consuming-agents.md) — **start
  here if you've been told "use scitex-cards for your cards."**
  One-page protocol for any fleet agent: CRUD verbs, closed enums,
  title-prefix convention, lead↔worker sync wire. (Was `40_…` in PR
  #63's source branch; renumbered to `42_` during rebase to avoid
  the slot collision with `40_task-harvest.md` that landed on
  develop first.)
