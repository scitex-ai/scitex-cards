---
description: |
  [TOPIC] Task harvest — blocker-driven backlog consumption on the shared board
  [DETAILS] The fleet's contract for keeping the shared task store
  fresh: every task is either BLOCKED (with a recorded reason +
  dependency, drawn from a closed enum) or RUNNABLE (no live blocker → a
  lead-driven escalation cycle dispatches it). Each harvest pass (1)
  re-checks every BLOCKED task to see if its blocker has cleared
  (auto-unblock), then (2) escalates every RUNNABLE task to the lead via
  a2a so the lead can dispatch the owning agent. Goal: keep CONSUMPTION
  rate > ARRIVAL rate so the board doesn't drift from the live codebase.
  Naming locked by operator TG 2026-06-07 msg 332 + 335: must carry
  "task" (you're consuming tasks, not branches); no "branch" / "graph"
  metaphors (those clash with git / knowledge-graph mental models).
  [HOW] Harvest the store: partition into BLOCKED vs RUNNABLE, re-check
  blockers, then a2a the lead with a punch-list. Lead-centric funnel —
  agents report new blockers BACK to the lead, the lead dispatches
  RUNNABLE work OUT.
tags: [scitex-cards-task-harvest, scitex-cards-blockers, scitex-cards-throughput]
---

# Task harvest — blocker-driven backlog consumption

The shared board (the SQLite task store, rendered live at
`http://127.0.0.1:8051/`) is only valuable when **consumption rate >
arrival rate**. Otherwise old entries drift away from the live codebase,
the operator stops trusting the map, and the SSoT decays. This skill
encodes the operator's directive (Telegram 2026-06-07 21:51 + 21:53):
keep tasks **fresh** by sweeping the board on a regular cadence,
unblocking what cleared, and escalating everything that can be done
RIGHT NOW.

## The two-state model

Every task on the shared board is **either**:

| state | meaning |
|---|---|
| BLOCKED | A specific, named blocker prevents progress. Record it. |
| RUNNABLE | No live blocker. The task can start now → escalate it. |

"Runnable" is the **default**. A task that cannot point at a concrete
blocker is RUNNABLE — and therefore eligible for immediate escalation.
"In progress" is just RUNNABLE that someone is currently executing; it
stays RUNNABLE until completion (status moves to `done`) or a new
blocker surfaces (status moves to `blocked`, blocker reason recorded).

The operator's framing (TG 21:53):

> "効率で浮いてるのはもうブロッカーないってことじゃないですか"
> ("If a task is just floating in the queue, that means it has no
> blocker — escalate it.")

## The sweep cycle

A **sweep** is one pass over the board. The lead runs the sweep
(centralized funnel — see "Routing" below); agents respond to dispatch.
The sweep has two phases, in this order:

### Phase 1 — Re-check existing blockers (unblock)

For every task with `status: blocked`:

1. **`compute`**: is the `depends_on:` job/task still pending? Query
   the upstream — Spartan job state, SIF build dir, GPU-lane scheduler.
   If the upstream is `done` (or its analogue), flip the blocked task
   to `pending` + clear `blocker`. Append a comment:
   `"[task-harvest YYYY-MM-DD] unblocked — compute dependency
   <id> resolved."`
2. **`quota`**: is the account / API limit reset? (Quotas typically
   reset on a daily / weekly / monthly cadence.) If yes, unblock.
3. **`user-pending`**: did the operator (or external reviewer) respond?
   If yes — record the decision in `comments[]`, unblock, dispatch.
   If no — leave blocked, but **bump it into the operator's "BLOCKING
   YOU" panel** by setting `blocker: operator-decision` (the
   existing LOUD-halo family) so the next operator-side glance at the
   board surfaces it.
4. **`task-dependency`**: is every `depends_on` id now `done`? If yes,
   unblock. (The board already flags blocked-by-done chains visually,
   but the harvest is what flips the `status` field.) If NOT all `done`,
   **walk the chain** (see "ROOT BLOCKER walk" above) so the escalation
   pressure lands on the leaf — the actual atomic blocker or RUNNABLE
   node holding up the dep-chain — instead of re-escalating intermediate
   nodes that just relay the block.

The board's `AutoRefresh.tsx` picks up the flip within 5s — the
operator sees the unblock live.

### Phase 2 — Escalate every RUNNABLE task

After Phase 1, the lead now has a clean RUNNABLE list — every task that
can start RIGHT NOW. The lead a2a-dispatches each to its owning agent
(see Routing). The dispatch message format:

```
[ESCALATE] <task-id>: "<title>" — RUNNABLE.
   No blocker. Owning agent: <agent-name>.
   You can do this now; report back with PR / a2a / comment.
```

The lead is **pushy on purpose** (operator TG 21:51):

> "やったほうがいいと思いますっていうのも定期的にプッシュして
> プレッシャーをかけてください"
> ("Push 'you should do this' on a regular cadence — apply pressure.")

The point isn't politeness; it's keeping consumption-rate > arrival-rate.

## Worked example — one sweep

Starting state (267 tasks):
- 5 `status: blocked`
- 18 `status: in_progress`
- 135 `status: pending` (= RUNNABLE)
- rest = `done` / `goal` / `deferred`

### Phase 1 (re-check blockers)

| id | blocker | depends_on | re-check result | action |
|---|---|---|---|---|
| paper-clew/sle-pac-fanout | compute | sif-build-202606 | `sif-build-202606.status=done` | UNBLOCK |
| scitex-dev/audit-wave-2 | task-dependency | scitex-dev/audit-1 | dep still in_progress | leave |
| neurovista/onsets-pull | quota | (none) | gh PAT reset overnight | UNBLOCK |
| ripple-wm/recompute | compute | sac-base.sif rebuild | rebuild still pending | leave |
| paper-clew/figure-3 | user-pending | operator-decision | no reply 4d | bump to `operator-decision` (loud halo) |

→ 2 unblocked, 1 bumped LOUD, 2 still blocked.

### Phase 2 (escalate RUNNABLE)

After Phase 1: 137 RUNNABLE (135 pending + 2 just-unblocked). The lead
filters to the **highest-priority N** per agent (e.g. top 3 per owning
agent) to avoid flooding inboxes, then a2a-dispatches:

```
→ scitex-clew: 3 ESCALATE messages (incl. sle-pac-fanout)
→ neurovista:        3 ESCALATE messages (incl. onsets-pull)
→ scitex-dev:        3 ESCALATE messages
→ scitex-hub:        3 ESCALATE messages
→ ...
```

Daily summary to operator:

```
[task-harvest 2026-06-08] pass N=267 → unblocked=2 dispatched=24 awaiting=3 net=-1
```

## What to do when an agent says "I can't"

If an ESCALATE-targeted agent replies with "難しいです" / "blocked",
the lead does NOT just leave it. The lead asks for the reason **and**
updates the YAML so the next sweep sees the same blocker:

1. **Get the blocker reason** from the agent (a2a follow-up). Must map
   to one of the four `blocker:` enum values.
2. **Get the dependency id** if applicable (the compute job, the
   upstream task, the credential the operator needs to refresh).
3. **Update the YAML** — flip `status → blocked`, fill `blocker:` +
   `depends_on:`, append the rationale into `comments[]`.

Operator framing (TG 21:53):

> "難しいですって言う回答が返ってきたら理由とディペンデンシーを
> アップデートするようにしてください."
> ("If 'difficult' comes back, update the reason + dependency.")

This is the loop-closer — without it, the board says RUNNABLE and the
sweep re-escalates next tick, frustrating the agent. The point of
asking for the reason is to **promote it to a first-class blocker**
the next sweep accounts for.

## What this skill does NOT cover

- **How the lead actually picks priority** within RUNNABLE — that's a
  lead-internal heuristic (deadline closeness, blast radius,
  high-leverage decisions per `decisionImpactCount`). This skill says
  "escalate all RUNNABLE"; the lead chooses ORDER + top-N per agent.
- **Compute-state-deps watchers** (the Spartan / SIF watchers that
  externally flip `kind: compute` rows) — see north-star pillar #1 +
  ADR-0006 (compute-state-deps) in `docs/adr/`. The sweep READS those
  rows; the watchers WRITE them.
- **The operator's `operator-decision` resolve loop** — that's
  documented in `11_adopting-from-a-project.md` (the BLOCKING YOU
  panel + GUI Resolve button). This skill points the sweep AT that
  panel; the panel itself is the operator's UI.

## Related skills

- [`11_adopting-from-a-project.md`](11_adopting-from-a-project.md) —
  the agent-side "make sure your tasks SHOW UP on the board" path. A
  task that isn't on the board can't be swept.
- [`30_two-tier-conventions-and-write-protocol.md`](30_two-tier-conventions-and-write-protocol.md)
  — the full project-vs-global tier contract, including which fields
  agents are allowed to write directly vs which the lead arbitrates
  during a sweep.
