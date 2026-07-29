# 60 — PR-merge recording mandate (the recording-gap fix)

Sister doc to the **⚑ MANDATE — record evidence at PR-merge / issue-close
time** section in [SKILL.md](SKILL.md). Long-form rationale + the exact
recording verbs to call from every wire (CLI / Python API / MCP).

## Why this is load-bearing

The operator's **完了率 metric** (board "done" count vs real consumption)
is the #1 signal he uses to allocate fleet capacity. Without an evidence
link on every closed card, the board chronically under-reports
throughput — the 2026-06-13 reconciliation pass found 199 PRs merged in
24 hours but only ~5 board completions had been recorded in the same
window. That gap is **structural**, not a hygiene problem: cards mostly
don't carry `pr_url` pointers because writers never set them at merge
time, and the reconciliation-pass substring matcher can only catch the
small fraction of PRs whose body/title cites the card id verbatim.

This mandate closes the gap at the source.

## The exact verb (CLI)

When you merge a PR (or close an issue) that completes a board card:

```bash
scitex-cards done <card-id> --pr-url <merged-PR-URL>
```

That single call writes:

- `status: done`
- `pr_url: <URL>` (the evidence)
- `last_activity: <now>`
- `_log_meta.completed_at` + `_log_meta.completed_by` (atomic stamp)

…in one flock-safe transaction (`_store.complete_task` + the field
write through `update_task` share the same lock — see `_store.py`).
The reconciliation pass v2+ uses `pr_url` as Criterion A — your card
is closed-on-evidence the next time the pass runs, NOT closed-by-luck.

## Python API equivalent

```python
import scitex_cards as todo

todo.update_task(
    task_id="<card-id>",
    pr_url="https://github.com/<org>/<repo>/pull/<N>",
    status="done",
)
```

`update_task` writes both fields atomically under the same lock.
Equivalent to `done <id> --pr-url <URL>`; pick the wire that's
ergonomic at your call site.

## MCP equivalent

From inside an agent container, the MCP wire is preferred (it routes
through ONE scitex-cards subprocess per host, serialized writes):

```python
# From a Claude Code agent:
await update_task(
    task_id="<card-id>",
    pr_url="https://github.com/<org>/<repo>/pull/<N>",
    status="done",
)
```

(or via the `mcp__scitex-cards__update_task` tool name in the chat
session if you're calling it interactively).

## No-PR completions

A few completions don't have a PR — a config flip on the host,
an ad-hoc verification, an operator-side review. Record the evidence
as a comment immediately before the done flip:

```bash
scitex-cards comment <card-id> "no-PR completion: <one-line evidence>" \
    --author <your-agent-name>
scitex-cards done <card-id>
```

The comment is the substitute for `pr_url` — the reconciliation pass
won't auto-close on it, but the next human / lead pass has the trail.

## Bulk catch-up

If you realise a BATCH of past PRs was never recorded — e.g. you took
a break for half a day and merged 8 PRs without recording — the
catch-up verb is:

```bash
scitex-cards sync-github --since 2026-06-13T00:00Z -y
```

This walks the agent's recent merged PRs (per the agent's GitHub
identity) and writes the missing `pr-<repo>-<num>` done-records in
one transaction. scitex-cards used this overnight as the backfill
mechanism after the reconciliation pass surfaced the gap (lead a2a
`fbd15187`).

Use `sync-github` as an emergency catch-up, NOT as a substitute for
recording at merge time — the bulk verb's records are aggregate and
lose the per-card detail that an inline `done --pr-url` preserves.

## How the mandate is enforced

Today: **culturally**. The mandate lives in this skill, which is
propagated into every agent's `required_skills` via
`scitex-cards skills propagate` (the #161 mechanism), so every agent
reads it on boot. The expectation is internalised, not blocked.

Tomorrow (follow-up card, see board entry `rec-pr-merge-recording-
enforcement-hook`): a Stop-hook OR a PostToolUse hook on `Bash` calls
matching `gh pr merge` / `gh pr review --comment` could detect a
just-merged PR in the agent's recent git activity and emit a stderr
nudge if no matching `scitex-cards done --pr-url` call follows within
N turns. The skill-mandate is the floor; a hook is the ratchet.

## Anti-patterns

- **`scitex-cards done <id>` with NO `--pr-url`** — the recording-gap.
  The card looks closed on the board but the reconciliation pass
  can't verify the work landed. Don't.
- **Telling the operator on Telegram "I merged PR #N" without
  recording** — the chat trail isn't queryable; the board is. The
  operator's view is the board, not your chat backlog.
- **Recording later "when I get to it"** — by then, the operator
  already planned around a missing signal. The cost of recording at
  merge time is one CLI invocation (<1s); the cost of NOT recording
  is the operator under-allocating capacity to a project that's
  actually overconsuming.

## Provenance

Operator's standing direction on 完了率 (TG passim).
Lead a2a `0cdca03a4d9b4fe494b44ded87dc0827` (2026-06-13) approved
shipping this as fleet-adoption multiplier #3, sister to PR #160
(TaskCreate-redirect hook) and PR #161 (skill propagation manifest).
Diagnostic source: `/work/GITIGNORED/RECONCILE_TRACE.json` — the
2026-06-13 reconciliation pass that surfaced the gap.
