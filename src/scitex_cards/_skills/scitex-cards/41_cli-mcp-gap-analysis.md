---
description: |
  [TOPIC] CLI / MCP surface gap analysis for fleet adoption
  [DETAILS] Audit of the scitex-cards CLI + MCP surface against the Task
  dataclass + the fleet adoption skill (42_for-consuming-agents.md). Lists
  the verbs and field-level flags that consuming agents need but the
  surface doesn't expose yet, plus the bridge (Python API / hand-roll)
  available today. Drives the next 1–3 PRs of CLI / MCP closure work.
tags:
  [
    scitex-cards-cli-mcp-gap-analysis,
    scitex-cards-cli-roadmap,
    scitex-cards-fleet-adoption,
  ]
---

# CLI / MCP gap analysis — what's missing for fleet adoption

Status: audited 2026-06-07, re-audited 2026-06-13. The "today" column is
what an agent can already do through `scitex_cards._store.*` while the CLI
and MCP catch up.

## A. New verbs (CLI + MCP)

| Verb | Why | Today |
|---|---|---|
| `comment` | Append to `comments[]`, the append-only activity log | **SHIPPED** — `scitex-cards comment ID TEXT [--author X]` wraps `_store.comment_task` |
| `reopen` | Undo a `done` row; HTTP `/reopen` exists, no CLI parity | `_store.reopen_task(p, id)` (done → `blocked` + `operator-decision`, stamp cleared; CLI bridge: `update <id> --status blocked --blocker operator-decision`) |
| `body init` | Seed `tasks/<id>/README.md` + `adr.md` from templates | `mkdir` + copy templates by hand |
| `validate` | Run `_validate_tasks` on demand, without writing | `_model._validate_tasks(load_tasks(p))` |

`comment` was the load-bearing gap: patterns B and C of the consuming-agent
skill write to `comments[]` directly. The MCP `comment_task` tool was
already live (see [21_fleet-mcp-rollout.md](21_fleet-mcp-rollout.md)).
Consuming agents should drop any `update_task(comments=...)` hand-roll on
the next touch — the explicit verb is the canonical path.

## B. Missing flags on existing verbs

### `scitex-cards add`

Has today (all shipped; verified 2026-10-12 against `add --help`): `--status` · `--task` · `--project` · `--host` · `--agent` · `--group` · `--goal` · `--last-activity` · `--blocker` (closed enum) · `--pr-url` · `--issue-url` · `--kind` (closed enum, incl. `status`) · `--job-id` · `--command` · `--started-at` · `--finished-at` · `--scope` · `--assignee` · `--priority` · `--parent` · `--note` · `--depends-on` (rpt) · `--blocks` (rpt) · `--repo` · `--created-by` · `--json` · `--dry-run` · `-y` / `--yes`. `--tasks` never landed (no such flag in `add --help`).

Shipped (was Missing — Task dataclass fields, operator-co-designed surface TG 9667; every row landed):

| Flag | Type | Notes |
|---|---|---|
| `--task` | str | The BIG board-card text (distinct from `--title`'s short scannable label). |
| `--project` | str | Project / repo basename. Matches the canonical id prefix. |
| `--host` | str | Where the work happens. |
| `--agent` | str | Owning agent — forward-compat alias for `--assignee` (SHIPPED). `assignee` STAYS the primary linking field (lead empirical 2026-06-07: `list-tasks --assignee <agent-id>` filters correctly). |
| `--goal` | str | WHY (parent-goal text); rendered as 🎯 line. |
| `--last-activity` | ISO-8601 | Drives card recency color. |
| `--blocker` | closed enum | One of VALID_BLOCKERS; CLI must reject unknowns (fail-loud parity with `_model`). |
| `--pr-url` | str | GH/Gitea PR link. |
| `--issue-url` | str | GH/Gitea issue link. |
| `--kind` | closed enum | One of VALID_KINDS; absent ⇒ "task". |
| `--job-id` | str | `kind: compute` metadata. Only valid when `--kind compute`. |
| `--command` | str | `kind: compute` metadata. |
| `--started-at` | ISO-8601 | `kind: compute` metadata. |
| `--finished-at` | ISO-8601 | `kind: compute` metadata. |

### `scitex-cards update`

Same field set as `add` (replace-or-clear semantics — pass `''` to
clear). `--depends-on` / `--blocks` SHIPPED on `update` with REPLACE
semantics (repeat the flag per id; pass once with `''` to clear;
verified 2026-10-12 against `update --help`). `+/-` delta semantics
remain the follow-up.

Also: `--comments` is intentionally OMITTED from `update` (use the
new `comment` verb; append-only contract is clearer that way).

### `scitex-cards list-tasks`

Has today (all shipped; verified 2026-10-12 against `list-tasks --help`): `--scope` · `--assignee` · `--agent` (alias) · `--project` · `--host` · `--blocker` (`__none` for no blocker) · `--kind` (incl. `status`, PR #146) · `--id-prefix` · `--blocking-me` · `--blocking-operator` · `--overdue` · `--status` (repeatable, e.g. `--status deferred --status in_progress`) · `--json`.

Shipped filters (was Missing — every row landed):

| Flag | Semantics |
|---|---|
| `--project` | Match `project` exactly. |
| `--host` | Match `host` exactly. |
| `--blocker` | Match `blocker` exactly; `__none` for "no blocker". |
| `--kind` | Match `kind` exactly; absent ⇒ "task" for filter purposes. Now includes `status` (PR #146) for non-actionable status-tracking rows (q-* quality flags etc.). |
| `--blocking-me` | Predicate: `status == "blocked" AND blocker == "operator-decision"` (BLOCKING YOU). |
| `--status` (repeat) | Multi-status filter (e.g. `--status deferred --status in_progress`). |
| `--id-prefix` | Substring/prefix match on `id` (cheap "find my project's rows"). |
| `--agent` | Forward-compat alias for `--assignee` (SHIPPED). `--assignee` stays primary + works (lead empirical 2026-06-07). |

### `scitex-cards done`

Today: `--by`. No additions needed.

### `scitex-cards summary`

Today: `--scope`, `--assignee` (verified 2026-10-12 against `summary --help`). Mirroring `list-tasks`' wider filters remains the follow-up now that `list-tasks` has landed.

## C. MCP parity gaps — CLOSED

SHIPPED (verified 2026-10-14 against `_mcp_write.py` / `_mcp_server.py`):
MCP `add_task` / `update_task` carry the full field surface, not the
legacy six — `status` · `blocker` (closed enums) · `kind` · `project` ·
`host` · `agent` · `task` · `goal` · `pr_url` · `issue_url` ·
`depends_on` · `blocks` · `deadline` / `deadlines` / `scheduled` ·
compute metadata (`job_id` · `command` · `started_at` · `finished_at`) ·
`parked` · `created_by`, over the legacy `scope` · `assignee` ·
`priority` · `parent` · `note` · `repo`. Convention A holds
(`tool_name == python_api_name`): the Python kwargs are the single
source. The verb tools are live too: `comment_task(task_id, text,
by=None)`, `reopen_task(task_id, by=None)`, and `list_tasks` with the
CLI's filter set plus cursor pagination (`blocking_me`, `overdue`,
`statuses` multi).

## D. Python API parity gaps — CLOSED bar one

SHIPPED: `scitex_cards.__all__` now exports `comment_task` and
`reopen_task` (plus `delete_task`, `restore_task`, `reassign_task` and
the DM/notify verbs), and `_store.update_task` accepts every Task field
via `**fields` (confirmed in implementation). The one genuine
follow-up: `validate_store(tasks_path)` — run
`_validate_tasks(load_tasks(p))` returning `{ok, errors}` for a future
CLI `validate` verb (today only `validate-health` exists).

## E. Schema / validator gaps (informational)

Tracked on the package's quality-hygiene arc, not blockers for this skill.
Listed for cross-reference: `depends_on` / `blocks` referential integrity
(the graph builder silently drops dangling refs; the validator should reject
them at write time, same fail-loud pattern as `kind`); cycle detection over
the same edges; and ISO-8601 enforcement on `comments[].ts`, where any
non-empty string passes today. Consumers always go through `_store.*`, so
the writer and validator stay honest regardless.

## F. Propagation (the @path mechanism)

Section F used to be a PR-slicing plan ("THIS PR (#N)", then #N+1 …) for
landing the verbs above. Every slice in it has since shipped or been
re-planned on the board, and it referenced a leaf filename that no longer
exists — a rollout plan that outlived its rollout. The board is the live
ordering; this file is the gap list.

1. `scitex-cards skills install --claude-symlink` exposes the bundled
   skills at `~/.claude/skills/scitex/scitex-cards/` (operator side).
2. For container-side agents, the spec.yaml gains a
   `required_skills:` entry referencing the package-bundled path:

   ```yaml
   required_skills:
     - "@scitex_cards:_skills/scitex-cards/42_for-consuming-agents.md"
   ```

3. New agents pip-install `scitex-cards>=N.M.K` so the bundled skill
   is on their PYTHONPATH; the spec.yaml reference resolves at
   container boot. `scitex-cards skills install` back-fills hosts that
   already carry an older release.

The skill is **version-pinned via the package**: editing one leaf does NOT
propagate until the consumer pip-bumps. That gives a deterministic rollout —
pin one agent, watch it adopt, broaden once stable.

---

## Addendum — the `kind: status` axis (SHIPPED)

The `q-*` family (one card per fleet package) carries quality-CI status as
one-liner notes. That is a status DB, not a card list, so those rows on the
actionable board are noise. Resolution, now in the schema: `VALID_KINDS`
includes `"status"`, `add` / `update --kind status` accept it, and
`list-tasks --kind status` selects only those rows. Default `list-tasks`
behaviour is UNCHANGED — hiding by default is a board-frontend decision, not
a CLI policy. Re-flagging existing `q-*` rows stays an operator-driven data
migration.
