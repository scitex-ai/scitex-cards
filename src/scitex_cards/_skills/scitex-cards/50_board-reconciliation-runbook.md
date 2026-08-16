# Board Reconciliation Runbook — Canonical Verbs

**Audience:** every fleet agent (scitex-* workers, hub, journal, ripple-wm, dev, agent-container, lead, …).
**Owner:** scitex-cards.
**First landed:** 2026-06-13 (operator directive via lead).

## Why this exists

The board drifts when agents merge work but don't mark the corresponding card done, or when cards age past usefulness without being closed. Operator's framing (TG 12664/12739): *"乾燥させる"* — drain the pile, don't just stack. Honest reconciliation requires **mark done with PR pointer** + **close stale with reason**, never silent drop.

This runbook gives the **canonical command lines** every agent uses for the same sweep, so the lead can broadcast one set of instructions to the fleet.

## Convention

- Each agent reconciles **its own lane** (cards where `assignee` or `agent` = its own id, OR `project` = the package it owns).
- Mark done **with a PR pointer** so the lineage is traceable.
- Close stale **with a reason** so future audits know why.
- The operator is the only one who **deletes** cards (we surface stale lists, he decides). Agents do NOT auto-close cards they don't own.

---

## 1. List YOUR cards

```sh
# By assignee (the primary linking field):
scitex-cards list-tasks --assignee <your-agent-id> --json

# By project (if you own a package, not an agent identity):
scitex-cards list-tasks --project <package> --json

# Filter to actionable only:
scitex-cards list-tasks --project <package> --status pending --status in_progress --json

# All my pending cards (compact human view):
scitex-cards list-tasks --assignee <your-agent-id> --status pending
```

Useful flags:
- `--blocking-me` — predicate (status=blocked + blocker=operator-decision). The "BLOCKING-YOU" panel feeder.
- `--blocker __none` — rows with no blocker set.
- `--id-prefix <prefix>` — cheap project-rollup lookup.
- `--kind status` — surfaces the `kind: status` axis (PR #146) when you want to filter the q-* quality-tracking cards.

---

## 2. Mark a card DONE — WITH a PR pointer

```sh
# Preferred (records the lineage):
scitex-cards update <task-id> --status done --pr-url https://github.com/<org>/<repo>/pull/<n>

# Optional but useful (mark who completed it):
scitex-cards update <task-id> --status done --pr-url <url> --agent <your-agent-id>

# Shorthand that stamps _log_meta.completed_{at,by}:
scitex-cards done <task-id> [--by <author>]
# (Use this when there's no PR — e.g. data tidy-up, doc fix in a sibling system.
#  Add a comment with context.)
```

Heuristic: if the card describes a deliverable that landed in a PR, **always use `update --status done --pr-url ...`**, not bare `done`. The `pr_url` field is what makes the board's "85 merges / 56 marked done" gap auditable.

---

## 3. Close a STALE card — WITH a REASON (no silent drop)

The `close` verb landed in PR #151 (2026-06-13). It records the reason in `comments[]` and flips status to `deferred` (the sentinel meaning "operator opted out, see reason" — no new status enum value cascading FE/test changes).

```sh
scitex-cards close <task-id> --reason "<short reason in imperative or past tense>"

# With author override (default chain: $SCITEX_CARDS_AGENT_ID -> $USER):
scitex-cards close <task-id> --reason "<text>" --by <author>

# Dry-run first (prints intent, does NOT mutate):
scitex-cards close <task-id> --reason "<text>" --dry-run
```

Good reasons (≤ 1 sentence each):
- `"superseded by PR #146 (kind=status shipped a different way)"`
- `"goal merged into <other-card-id> on operator decision <date>"`
- `"obsolete — depends on a feature we deprecated in <commit>"`
- `"no concrete deliverable; revisit if it resurfaces"`
- `"duplicate of <other-card-id>"`

Bad reasons (don't do this):
- `"old"`  — say WHAT made it old.
- `"won't fix"` — say WHY.
- `(blank)` — the verb refuses empty.

After `close`, the card is hidden from default action lenses (status=deferred), but the row + the comment chain + `_log_meta.closed_at` remain — fully auditable.

---

## 4. Add an activity comment (anytime)

`comments[]` is the established Issue-activity log. Use it for status updates, blocker notes, decisions that don't warrant an ADR.

```sh
scitex-cards comment <task-id> "<text>" [--author <agent-id>] [--json]
```

(Shipped via PR #144. Same write-lock as `close` / `done`.)

---

## 5. The reconciliation sweep — one agent's run

A complete sweep for ONE agent looks like:

```sh
# 0) Snapshot current state (saves a copy you can diff against later).
scitex-cards list-tasks --assignee $SCITEX_CARDS_AGENT_ID --json > /tmp/my-cards-before.json

# 1) For every recently-merged PR you owned: mark its card done with the PR pointer.
scitex-cards update <card-id> --status done --pr-url <pr-url>

# 2) For every pending card that's obsolete / superseded / no-longer-relevant:
scitex-cards close <card-id> --reason "<short why>"

# 3) For every pending card that's STILL valid but you have new context: comment it.
scitex-cards comment <card-id> "<update>"

# 4) Re-snapshot + diff to verify your sweep landed:
scitex-cards list-tasks --assignee $SCITEX_CARDS_AGENT_ID --json > /tmp/my-cards-after.json
diff <(jq -S . /tmp/my-cards-before.json) <(jq -S . /tmp/my-cards-after.json) | head -200
```

---

## 6. STALE candidates for OPERATOR review

The operator (not agents) decides which orphaned cards to archive. scitex-cards generates a periodic **stale-candidates list** at:

```
~/.scitex/cards/STALE_CARDS_FOR_REVIEW.md
```

Criteria for inclusion:
- `status=pending` AND `created_at > 14d` (or no `created_at`/`last_activity`), OR
- title/owner unclear/orphaned (no clear deliverable).

Format: per-project tables, oldest-first, `id | title | age | reasons`. Agents do NOT auto-close these — the operator scans + decides + applies `scitex-cards close <id> --reason "<text>"` himself (or the owning agent does it WITH his go-ahead).

Regenerate the list:
```sh
# scitex-cards refreshes this on operator demand. To trigger from any agent:
# (a2a "regen stale list" to scitex-cards) — there's no scheduled cron yet.
```

A future PR will wrap the generator in a `scitex-cards list-stale [--days 14]` CLI verb. Tracked as an in-board card.

---

## 7. Quick reference card (for the lead's broadcast)

```
LIST MY CARDS         scitex-cards list-tasks --assignee <me> --status pending
LIST BY PACKAGE       scitex-cards list-tasks --project <pkg> --status pending
MARK DONE + PR        scitex-cards update <id> --status done --pr-url <url>
CLOSE WITH REASON     scitex-cards close <id> --reason "<short why>"
ADD COMMENT           scitex-cards comment <id> "<text>"
DRY-RUN ANY MUTATION  add --dry-run before the real call
STORE OVERRIDE        --tasks <path>  OR  SCITEX_CARDS_TASKS_YAML_SHARED=<path>

# Fleet enablement (P3a, one-shot register the MCP server) ─ PR #155:
PREVIEW REGISTRATION  scitex-cards mcp install --apply --dry-run
REGISTER MCP SERVER   scitex-cards mcp install --apply -y
REGISTER PROJECT MCP  scitex-cards mcp install --apply --to ./.mcp.json -y
```

Fleet MCP enablement (the `mcp install --apply` rollout, its idempotence and
non-destructive guarantees, and the lead-coordinated broadcast shape) lives in
`21_fleet-mcp-rollout.md`. It was duplicated here in full; one copy drifts.

---

## 8. Gotchas

1. **Store resolution.** The store identity is `$SCITEX_CARDS_DB` (the SQLite database path). Check with `scitex-cards resolve-store`. Many agents bind only the user database; a project-scoped database can shadow it silently.
2. **Container store divergence (historical).** Older containers could bind from a different host snapshot than the operator's canonical store before the SQLite migration; that failure class no longer applies now that `$SCITEX_CARDS_DB` is the single store identity.
3. **`done` vs `update --status done`.** `done` is shorthand without PR-pointer recording. Prefer `update` when there's a PR.
4. **PR pointer field.** It's `pr_url` (string), not `pr-url` (the CLI flag).
5. **Close uses `deferred` today.** If/when `VALID_STATUSES` grows a dedicated `closed` value, `close` will switch over — the verb shape stays the same.

---

## 9. Provenance

- Operator directive 2026-06-13 (via lead a2a) — "make every agent reconcile their project's cards; 85 merges / 56 marked done is the drift signal".
- Operator "all agents use scitex-cards, no parallel card formats" → P3a fleet MCP rollout.
- Verb gap closure: PR #151 (`feat(cli): close verb`).
- Comment verb: PR #144.
- Skill bundle refresh: PR #149.
- Recurring stale-review board panel: PR #153 (backend `/stale` + `/archive`) + PR #154 (FE 🧹 Stale layout + Archive button).
- Fleet MCP enabler: PR #155 (`mcp install --apply`).
- Stale-list generator: ad-hoc Python at `scitex-cards` (CLI verb is a follow-up).

End-of-file.
