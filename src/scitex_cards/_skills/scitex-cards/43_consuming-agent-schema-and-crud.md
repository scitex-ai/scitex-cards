---
description: |
  [TOPIC] Consuming-agent schema and CRUD reference
  [DETAILS] The closed-enum schema and the CRUD verbs a consuming agent calls, in full. Split out of 42 so the entry-point leaf stays a one-pager.
tags:
  [
    scitex-cards-consuming-agent-schema-and-crud,
    scitex-cards-fleet-adoption,
    scitex-cards-cli-roadmap,
  ]
---

# Consuming-agent schema and CRUD reference

Split out of [42_for-consuming-agents.md](42_for-consuming-agents.md) to keep both leaves inside the
skill-size budget. That file remains the entry point.

## Schema — closed enums, fail-loud

Every task is a `Task` dataclass (defined in `scitex_cards._model.Task`).
The validator REJECTS unknown values in the closed enums below.

**Required fields:**

- `id` (str, globally unique, kebab-case, **prefix with your project**:
  e.g. `scitex-cards-fleet-rollout`, `clew-cohort-a-rerun`).
- `title` (str, short scannable label, ≤ 80 chars).
- `status` (closed enum below).

**Closed enums (fail-loud):**

| Field | Allowed values |
|---|---|
| `status` | `goal` · `pending` · `in_progress` · `blocked` · `done` · `deferred` · `failed` |
| `kind` | `task` (default if absent) · `compute` · `decision` |
| `blocker` | `compute` · `dependency` (alias `dep`) · `operator-decision` · `agent-wait` · `none` |

`blocker` is **only allowed when** `status == "blocked"`. Setting a
blocker on a non-blocked row raises.

**Recommended fields (operator-co-designed surface, TG 9667):**

- `assignee` (str) — **PRIMARY agent-linking field. Set this to YOUR
  agent name** (e.g. `scitex-cards`). `scitex-cards list-tasks --assignee
  <agent-id>` filters correctly — this is THE field that lets every
  consumer (lead, board, you) ask "show me agent X's open tasks."
  Forward-compat: the dataclass also has an `agent` field as the
  operator-co-designed long-term replacement; the migration is
  staged (CLI gains `--agent` as alias, deprecates `--assignee`)
  but TODAY you write `assignee`.
- `task` (str) — the 1-line CURRENT-task BIG text on the board card.
  Distinct from `title` (the short scannable label). Populate this for
  the card to read well.
- `project` (str) — your project's directory basename (e.g.
  `scitex-cards`). Matches the canonical id prefix.
- `host` (str) — where the work happens (`spartan` / `ywata-note-win`
  / etc.).
- `goal` (str) — WHY (parent-goal text); rendered as the 🎯 line on the
  card. One short sentence.
- `priority` (int) — lower = higher priority. Within your project
  tier, set a tight 1..N rank; the operator can re-rank globally on
  the board.
- `last_activity` (ISO-8601 UTC) — recency drives green/amber/red
  card coloring on the board.
- `depends_on: list[str]` / `blocks: list[str]` / `parent: str` —
  graph edges. Use the canonical `<project>/<local-id>` form for
  cross-project deps.
- `pr_url` / `issue_url` — GH/Gitea links.
- `comments: list[{ts, author, text}]` — append-only activity log
  (see "Coordinating with other agents" below).

**Title-prefix convention** (the operator's at-a-glance scan):

| Prefix | Meaning |
|---|---|
| `[P0]` | Highest business priority / live blocker |
| `[P1]` | Momentum (paper, infra in-flight) |
| `[P2]` | Parallel queue / hygiene |
| `[CI]` | CI hygiene |
| `[CAL]` | Calendar / commitment |
| `[GOAL]` | `status: goal` umbrella (north-star objective) |
| `[PKG]` | Per-package umbrella in the 66-pkg ecosystem-quality tree |
| `[strategy]` | Secondary tag (catalogs / GTM) |

So a typical title looks like `[P1] (PR #334 follow-up) verify bun
child survives restart fleetwide`.

---
## CRUD — the verbs you'll actually use

Examples are CLI; MCP tool names match 1:1 (Convention A); Python API
names match too.

### CREATE — `scitex-cards add`

```bash
scitex-cards add \
  scitex-cards-fleet-rollout \
  '[P1] Fleet rollout of scitex-cards skill across agents' \
  --status pending \
  --scope agent:scitex-cards \
  --assignee scitex-cards \
  --priority 10 \
  --note 'See tasks/scitex-cards-fleet-rollout/README.md'
```

> **CLI gap (in flight, see [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md)):**
> `add` does not yet accept `--task` / `--project` / `--host` / `--agent`
> / `--goal` / `--blocker` / `--pr-url` / `--issue-url` / `--kind` —
> the legacy `--scope` / `--assignee` are the bridge until those land.
> Use the Python API until the CLI catches up.

MCP equivalent: tool `add_task` (same kwargs, returns JSON).

Python equivalent:

```python
from scitex_cards import add_task
add_task(
    None,                           # tasks_path; None = resolve default
    id="scitex-cards-fleet-rollout",
    title="[P1] Fleet rollout of scitex-cards skill across agents",
    status="pending",
    scope="agent:scitex-cards",
    assignee="scitex-cards",
    priority=10,
    note="See tasks/scitex-cards-fleet-rollout/README.md",
)
```

### LIST — `scitex-cards list-tasks`

```bash
scitex-cards list-tasks --scope agent:scitex-cards --json
```

Filters today: `--scope` / `--assignee` / `--status` (exact match).
Use `--json` for machine output.

> **CLI gap (see gap analysis):** `--agent` / `--project` / `--host` /
> `--blocker` / `--kind` / `--blocking-me` filters are NOT in yet.
> Pipe through `jq` on the `--json` output for now.

### UPDATE — `scitex-cards update`

```bash
scitex-cards update scitex-cards-fleet-rollout \
  --status in_progress \
  --priority 5 \
  --note 'Skill draft pushed PR #N; gap closures next'
```

Pass an empty string (`--scope ''`) to CLEAR a field.

> **CLI gap (see gap analysis):** same field set as `add` — operator-
> co-designed fields are missing. Bridge via Python API.

### COMMENT (activity log)

The `comments: list[{ts, author, text}]` array is the append-only activity
log every agent writes when coordinating cross-lane. It has a first-class
verb — this leaf used to say there was none and to hand-roll the append
through `update_task(comments=[...])`, which was true only before PR #144.

```bash
scitex-cards comment <card-id> "lead a2a: rebase before merging" \
  --author scitex-cards
```

MCP: `comment_task`. Python: `_store.comment_task`. Do NOT hand-roll the
append — the read-modify-write drops concurrent comments, and the closed
ts/author/text shape is validated so missing keys raise.

### COMPLETE — `scitex-cards done`

```bash
scitex-cards done scitex-cards-fleet-rollout --by scitex-cards
```

Stamps `_log_meta.completed_at` (UTC ISO-8601) + `completed_by` (the
`--by` value, defaults to `$SCITEX_CARDS_AGENT_ID` then `$USER`).
Idempotent (re-doneing a `done` task keeps the original stamp).

MCP: `complete_task`. Python: `scitex_cards.complete_task`.

### RE-OPEN (undo a done / resolve)

There's no `reopen` CLI verb today. Use `update --status pending`:

```bash
scitex-cards update scitex-cards-fleet-rollout --status pending
```

The web board's `/reopen` HTTP endpoint (PR #61) is operator-facing;
the CLI parity is on the gap list.

---
