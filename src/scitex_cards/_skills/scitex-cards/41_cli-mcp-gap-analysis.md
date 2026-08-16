---
description: |
  [TOPIC] CLI / MCP surface gap analysis for fleet adoption
  [DETAILS] Audit of the scitex-cards CLI + MCP surface against the Task
  dataclass + the fleet adoption skill (40_for-consuming-agents.md). Lists
  the verbs and field-level flags that consuming agents need but the
  surface doesn't expose yet, plus the bridge (Python API / hand-roll)
  available today. Drives the next 1–3 PRs of CLI / MCP closure work.
tags:
  [
    scitex-cards-gaps,
    scitex-cards-cli-roadmap,
    scitex-cards-fleet-adoption,
  ]
---

# CLI / MCP gap analysis — what's missing for fleet adoption

Status: **audited 2026-06-07** against develop @ `6093cf6`;
**re-audited 2026-06-13** — `comment` verb shipped in PR #144;
`kind: status` axis shipped in PR #146 (board card
`op-2026-06-12-04`). The "Today via Python API" column captures what
an agent can do TODAY through `scitex_cards._store.*` while the CLI /
MCP catches up.

## A. New verbs (CLI + MCP)

| Verb           | Why                                                                                | Status                                                              | PR slice               |
| -------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ---------------------- |
| `comment`      | Append to `comments[]` — the append-only fleet activity log; cross-lane patternA   | **SHIPPED** PR #144 — `scitex-cards comment TASK_ID TEXT [--author X] [--json] [--dry-run]` wraps `_store.comment_task` | done |
| `reopen`       | Undo a `done` / resolved row; HTTP `/reopen` exists (PR #61), no CLI parity        | `_store.update_task(p, id, status="pending")`                       | follow-up               |
| `body init`    | Seed `tasks/<id>/README.md` + `adr.md` with the locked filenames + ADR template    | `mkdir + cat templates` by hand                                     | follow-up               |
| `validate`     | Run `_validate_tasks` on demand (operator: "fail loud, fail fast") — without write | `_model._validate_tasks(load_tasks(p))`                             | follow-up               |

`comment` was the load-bearing gap for fleet adoption — patterns B +
C of the consuming-agent skill explicitly write to `comments[]`. PR
#144 closes it (CLI verb wraps `_store.comment_task`; MCP
`comment_task` was already live, see
[21_fleet-mcp-rollout.md](21_fleet-mcp-rollout.md) tool table).
Consuming agents should drop any `update_task(comments=...)`
hand-roll on the next touch — the explicit verb is now the canonical
path the operator's SSoT directive (TG 9494) called for.

## B. Missing flags on existing verbs

### `scitex-cards add`

Has today: `--status` · `--scope` · `--assignee` · `--priority` · `--parent` · `--note` · `--depends-on` (rpt) · `--blocks` (rpt) · `--repo` · `--json` · `--dry-run` · `-y` · `--tasks`.

Missing (Task dataclass fields — operator-co-designed surface TG 9667):

| Flag                | Type                      | Notes                                                                                  |
| ------------------- | ------------------------- | -------------------------------------------------------------------------------------- |
| `--task`            | str                       | The BIG board-card text (distinct from `--title`'s short scannable label).             |
| `--project`         | str                       | Project / repo basename. Matches the canonical id prefix.                              |
| `--host`            | str                       | Where the work happens.                                                                |
| `--agent`           | str                       | Owning agent — forward-compat alias for `--assignee`. `assignee` STAYS the primary linking field today (lead empirical 2026-06-07: `list-tasks --assignee <agent-id>` filters correctly). `--agent` lands as a CLI alias once the dataclass migration completes. |
| `--goal`            | str                       | WHY (parent-goal text); rendered as 🎯 line.                                           |
| `--last-activity`   | ISO-8601                  | Drives card recency color.                                                             |
| `--blocker`         | closed enum               | One of VALID_BLOCKERS; CLI must reject unknowns (fail-loud parity with `_model`).      |
| `--pr-url`          | str                       | GH/Gitea PR link.                                                                      |
| `--issue-url`       | str                       | GH/Gitea issue link.                                                                   |
| `--kind`            | closed enum               | One of VALID_KINDS; absent ⇒ "task".                                                   |
| `--job-id`          | str                       | `kind: compute` metadata. Only valid when `--kind compute`.                            |
| `--command`         | str                       | `kind: compute` metadata.                                                              |
| `--started-at`      | ISO-8601                  | `kind: compute` metadata.                                                              |
| `--finished-at`     | ISO-8601                  | `kind: compute` metadata.                                                              |

### `scitex-cards update`

Same field set as `add` (replace-or-clear semantics — pass `''` to
clear). Plus: `--depends-on` / `--blocks` are missing from `update`
entirely (currently only on `add`). Update's `--depends-on` /
`--blocks` need ADD/REMOVE/REPLACE semantics — proposed shape:

```
--depends-on +X      add X (idempotent)
--depends-on -X      remove X (no-op if absent)
--depends-on =X,Y,Z  replace whole list
```

Also: `--comments` is intentionally OMITTED from `update` (use the
new `comment` verb; append-only contract is clearer that way).

### `scitex-cards list-tasks`

Has today: `--scope` · `--assignee` · `--status` (exact match each).

Missing filters:

| Flag                | Semantics                                                                          |
| ------------------- | ---------------------------------------------------------------------------------- |
| `--project`         | Match `project` exactly.                                                           |
| `--host`            | Match `host` exactly.                                                              |
| `--blocker`         | Match `blocker` exactly; `__none` for "no blocker".                                |
| `--kind`            | Match `kind` exactly; absent ⇒ "task" for filter purposes. Now includes `status` (PR #146) for non-actionable status-tracking rows (q-* quality flags etc.). |
| `--blocking-me`     | Predicate: `status == "blocked" AND blocker == "operator-decision"` (BLOCKING YOU). |
| `--status` (repeat) | Multi-status filter (e.g. `--status pending --status in_progress`).                |
| `--id-prefix`       | Substring/prefix match on `id` (cheap "find my project's rows").                   |
| `--agent`           | Forward-compat alias for `--assignee` once the dataclass migration completes. NOT a gap today — `--assignee` is already primary + works (lead empirical 2026-06-07). |

### `scitex-cards done`

Today: `--by`. No additions needed.

### `scitex-cards summary`

Today: `--scope`, `--assignee`. Should mirror `list-tasks`' filter
expansion (same flags) once `list-tasks` lands.

## C. MCP parity gaps

The MCP `add_task` / `update_task` mirror only the legacy CLI fields
(scope, assignee, priority, parent, note, repo). Once the CLI gains
the new flags above, the MCP tools must mirror them — Convention A
(`tool_name == python_api_name`) means the Python `_store.add_task` /
`_store.update_task` kwargs are the single source.

New tools needed (matching new CLI verbs):

- `comment_task(task_id, text, author=None, ts=None, tasks_path=None)` —
  append a `{ts, author, text}` entry to `comments[]`.
- `reopen_task(task_id, by=None, tasks_path=None)` — `_log_meta`
  stamp + `status: pending`; mirrors HTTP `/reopen`.

`list_tasks` MCP filter additions: same flag set as the CLI.

## D. Python API parity gaps

`scitex_cards.__all__` today:

```
__version__, ENV_AGENT, ENV_SCOPE, TaskNotFoundError, TaskValidationError,
add_task, complete_task, list_tasks, resolve_store, summarize_tasks,
update_task
```

`_store.update_task` already accepts every Task field via **kwargs
(needs audit — confirm in implementation). What's missing as PUBLIC
API:

- `add_comment(tasks_path, task_id, text, author=None, ts=None)` —
  Python helper that owns the read-modify-write of the append (vs
  forcing every caller to do load → mutate → save_tasks). Add to
  `__all__`.
- `reopen_task(tasks_path, task_id, by=None)` — mirrors complete_task
  semantics.
- `validate_store(tasks_path)` — run `_validate_tasks(load_tasks(p))`
  and return `{ok: bool, errors: [...]}` for the CLI `validate` verb.

## E. Schema / validator gaps (informational; lead a2a `28967019`)

These are TRACKED on the package's quality-hygiene arc, not blockers
for this skill's rollout. Listed here for cross-reference.

- `depends_on` / `blocks` **referential integrity** (today: graph
  builder silently drops dangling refs; the validator should reject
  them at write time per the same fail-loud pattern as `kind`).
- `depends_on` / `blocks` **cycle detection** (Tarjan SCC; raise on
  any cycle).
- `comments[]` `ts` **ISO-8601 enforcement** (today: any non-empty
  string passes). Lets agents render activity chronologically with
  no parse-time surprises.

These improve consumer ergonomics but are not strictly load-bearing
for the skill's adoption (consumers always go through `_store.*` →
the writer + validator stay honest).

## F. Roll-out staging

Proposed PR slicing for the lead to approve/reorder:

1. **THIS PR (#N)** — skill leaves only (`40_for-consuming-agents.md`
   + `41_cli-mcp-gap-analysis.md`) + SKILL.md update. No CLI / MCP /
   Python changes. Gives the lead a design checkpoint for the
   shape + propagation grammar (the @path mechanism in spec.yaml)
   BEFORE any code lands.
2. **PR #N+1** — `comment` verb (CLI + MCP + Python `add_comment`).
   Highest priority; load-bearing for fleet adoption pattern B/C.
3. **PR #N+2** — `add` / `update` field-flag expansion (all the
   missing operator-co-designed fields above; `--blocker` /
   `--kind` honor the closed-enum fail-loud rule on the CLI side).
4. **PR #N+3** — `list-tasks` filter expansion + `summary` parity.
5. **PR #N+4** — `reopen` verb (CLI + MCP + Python).
6. **PR #N+5** — `body init` + `validate` verbs (smaller convenience
   verbs; nice-to-have, defer until adoption demands).
7. **Separate arc** — schema/validator gap closures (E above) on the
   package's quality-hygiene roadmap. Already tracked as
   `proj-scitex-cards-quality-hygiene`.

Lead drives the ordering. If you want the rollout to wait until the
`comment` verb lands so the skill doesn't direct agents at the Python
API, push (2) ahead of propagating the skill reference into other
agents' spec.yaml.

## G. Propagation (the @path mechanism)

Once the skill is on develop + the lead green-lights:

1. `scitex-cards skills install --claude-symlink` exposes the bundled
   skills at `~/.claude/skills/scitex/scitex-cards/` (operator side).
2. For container-side agents, the spec.yaml gains a
   `required_skills:` entry referencing the package-bundled path:

   ```yaml
   required_skills:
     - "@scitex_cards:_skills/scitex-cards/40_for-consuming-agents.md"
   ```

   (Confirm the spec.yaml `required_skills` grammar matches what the
   container glue actually consumes — this is the lead's domain, not
   `scitex-cards`'s. If the grammar is different I'll align the doc.)
3. New agents pip-install `scitex-cards>=N.M.K` so the bundled skill
   is on their PYTHONPATH; the spec.yaml reference resolves at
   container boot.
4. `scitex-cards skills install` is the back-fill for hosts that
   already have older `scitex-cards`.

The skill itself is **version-pinned via the package** (`pip install
scitex-cards==N.M.K`); editing one skill leaf does NOT propagate via
spec.yaml until the consumer pip-bumps. That gives the lead a
deterministic rollout: pin the version on one agent at a time, watch
it adopt, broaden once stable.

---

## Addendum — `kind: status` axis (board card `scitex-cards-relocate-q-status-tracking`)

Per lead a2a `60a1a93d` (operator direction): the `q-*` family
(~66 cards, one per fleet package: `q-gen` / `q-io` / `q-ml` / ...)
carries quality-CI status as one-liner notes (audit-debt counts,
green flags). That's a status DB — not a ToDo list — so surfacing
those rows on the actionable board generates noise.

**Resolution (option b):** keep the rows on the board, but graduate
them with a new closed-enum value `kind: status`. The board's
filter UI (separate frontend PR) can then default-hide
`kind: status` from the actionable lens.

- Schema: `VALID_KINDS` now includes `"status"` (orthogonal — no
  compute-fields constraint; just a flag).
- CLI: `add` / `update --kind status` accept it; `list-tasks
  --kind status` selects only the flagged rows.
- Default `list-tasks` behavior is UNCHANGED — hiding by default
  is a board-frontend decision, not a CLI policy.
- Bulk re-flagging the existing ~47 `q-*` cards is an operator-
  driven data migration, NOT part of this schema PR.
