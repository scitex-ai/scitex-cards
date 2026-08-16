---
description: |
  [TOPIC] Blocker taxonomy — the closed enum and what each value obliges
  [DETAILS] The four blocker values, what each one means, who can clear it, and the obligations each places on the owning agent.
tags:
  [
    scitex-cards-blocker-taxonomy,
    scitex-cards-task-harvest,
  ]
---

# Blocker taxonomy — the closed enum and what each value obliges

Split out of [40_task-harvest.md](40_task-harvest.md) to keep both leaves inside the
skill-size budget. That file remains the entry point.

## Blocker taxonomy (closed enum)

When a task IS blocked, the blocker must come from one of these four
categories so the lead can route it without per-task interpretation:

| `blocker:` value | meaning | escalation route |
|---|---|---|
| `compute` | No live compute resource (SIF build pending, GPU lane full, Spartan job queued, host down). | wait on the resource; record `depends_on: [<job-or-job-task-id>]`. |
| `quota` | API quota / account credit exhausted (PyPI throttle, GH PAT scope, an account that hit its quota cap). | operator action: top up / change account. |
| `user-pending` | Awaiting a human decision (operator, collaborator, external reviewer). | operator action (the LOUD `operator-decision` blocker family in the board's "BLOCKING YOU" panel). |
| `task-dependency` | Another task in the graph must finish first; `depends_on` carries the id. | wait — clears automatically when the dep flips to `done`. |

Any blocker that doesn't fit one of these four MUST be coerced into
one (or surfaced as a fleet bug — the lead extends the enum, not the
agent inventing a fifth category ad-hoc).

### `task-dependency` cascades — the ROOT BLOCKER walk

Operator's clarification (TG 2026-06-07 msg 327):

> "ディペンズオンもブロッカーですよね。下のディペンディングデペン
> デントなものが片付かないとそのカードは片付かない。ブロッカーは
> カスケードのように下のほうに行く。1番下がブロッカーなんじゃない
> ですか？目標に対して枝がどんどん退縮していくようにプレッシャー
> をかけていきたい."

`task-dependency` is **transitive**: if task A is blocked because it
`depends_on: [B]`, and B is itself blocked because it `depends_on:
[C]`, then escalating A — or even B — is wasted noise. The actual
point where pressure can be applied is **C** (or whatever leaf C
points at, recursively, until we reach an atomic blocker:
`compute` / `quota` / `user-pending`, or a RUNNABLE node that's
just waiting for someone to start it).

Direction convention (so the routing is unambiguous):

```
                ┌─────────────────────┐
                │  goal (top)         │  ← what we want done
                └──────────┬──────────┘
                           │ depends_on
                ┌──────────▼──────────┐
                │  feature task       │  ← blocked-on-B
                └──────────┬──────────┘
                           │ depends_on
                ┌──────────▼──────────┐
                │  enabler task (B)   │  ← blocked-on-C
                └──────────┬──────────┘
                           │ depends_on
                ┌──────────▼──────────┐
                │  ROOT BLOCKER (C)   │  ← compute / quota / user-pending / RUNNABLE
                └─────────────────────┘   ← APPLY PRESSURE HERE
```

`A depends_on B` ⇒ B is the blocker of A. Goal at the top, deps
extend downward, leaves are where work actually happens. As leaves
resolve, the chain above auto-clears — the operator's intended
visual on the board: the dep-chain **退縮 (recedes / contracts)**
toward the top goal.

**Walking the chain** (the lead's algorithm during Phase 1):

For every task X with `status: blocked` and `blocker: task-dependency`:

1. Look at `X.depends_on` — find any dep that is NOT yet `done`.
2. If that dep is itself `status: blocked` with `blocker:
   task-dependency`, recurse into its `depends_on` (the unsatisfied
   subset).
3. Stop when either:
   - All deps in the chain are `done` ⇒ unblock X, cascade up.
   - You reach a node with an **atomic** blocker (`compute` /
     `quota` / `user-pending`) ⇒ that's the **root blocker**. The
     escalation/pressure goes THERE, not at X.
   - You reach a RUNNABLE node ⇒ that's the root blocker (someone
     just needs to start it). Escalate IT, not X.
4. Cycle guard: keep a `visited` set so a buggy YAML with a circular
   `depends_on` doesn't loop forever (raise a fleet-bug a2a if one
   is found; the validator should reject it at write time but the
   harvest should still survive a stale store).

**Escalation target is always the leaf**, never an intermediate
`task-dependency`-blocked node. This is the multiplier — one leaf
unblock can cascade-clear an entire dep-chain above it. ONE
pressure point per chain, not N.

**Why this matters for the board** (operator's "退縮" metaphor): a
healthy board over time shows dep-chains shortening as leaves
resolve upward — visible progress at the goal level driven by leaf
work. A board where the same intermediate node keeps getting
re-escalated without its leaf clearing means we're pushing the
wrong row, and the harvest needs to walk further down.

### Recording a blocker

When a task transitions RUNNABLE → BLOCKED, the agent (or lead, during
a sweep) writes the blocker + the dependency into the row:

```
- id: paper-scitex-clew/cohort-a-rerun
  title: "Cohort A rerun #50"
  status: blocked
  blocker: compute            # one of: compute | quota | user-pending | task-dependency
  depends_on:
    - sif-build-202606         # the upstream item this blocker points at
  comments:
    - author: scitex-clew
      ts: 2026-06-07T22:14:00Z
      text: "Blocked on sif-build-202606 — base SIF rebuild needed before re-run."
```

The `comments[]` append-only entry is the durable rationale — when the
blocker clears and the entry flips back to RUNNABLE, the comment stays
as audit trail.
