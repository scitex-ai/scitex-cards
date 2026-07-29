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

| state    | meaning                                                          |
| -------- | ---------------------------------------------------------------- |
| BLOCKED  | A specific, named blocker prevents progress. Record it.          |
| RUNNABLE | No live blocker. The task can start now → escalate it.           |

"Runnable" is the **default**. A task that cannot point at a concrete
blocker is RUNNABLE — and therefore eligible for immediate escalation.
"In progress" is just RUNNABLE that someone is currently executing; it
stays RUNNABLE until completion (status moves to `done`) or a new
blocker surfaces (status moves to `blocked`, blocker reason recorded).

The operator's framing (TG 21:53):

> "効率で浮いてるのはもうブロッカーないってことじゃないですか"
> ("If a task is just floating in the queue, that means it has no
> blocker — escalate it.")

## Blocker taxonomy (closed enum)

When a task IS blocked, the blocker must come from one of these four
categories so the lead can route it without per-task interpretation:

| `blocker:` value     | meaning                                                                  | escalation route                                |
| -------------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| `compute`            | No live compute resource (SIF build pending, GPU lane full, Spartan job queued, host down). | wait on the resource; record `depends_on: [<job-or-job-task-id>]`. |
| `quota`              | API quota / account credit exhausted (PyPI throttle, GH PAT scope, an account that hit its quota cap). | operator action: top up / change account.       |
| `user-pending`       | Awaiting a human decision (operator, collaborator, external reviewer).   | operator action (the LOUD `operator-decision` blocker family in the board's "BLOCKING YOU" panel). |
| `task-dependency`    | Another task in the graph must finish first; `depends_on` carries the id. | wait — clears automatically when the dep flips to `done`. |

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

## Routing — lead-centric funnel

The fleet does **not** have agents directly dispatching each other.
All escalation flows through the lead:

```
              ┌────────────────────────────┐
              │   the shared task store    │ ← SSoT (operator + lead + agents write)
              └────────────┬───────────────┘
                           │
                           ▼  sweep
                ┌──────────────────────┐
                │       LEAD           │
                │  (sweeps + dispatch) │
                └─────┬────────┬───────┘
                      │        │
            a2a ESCALATE  ▼    ▼  a2a REPORT new blocker
            ┌──────────────┐  ┌──────────────┐
            │ owning agent │  │ owning agent │
            │ (consumes)   │  │ (reports up) │
            └──────────────┘  └──────────────┘
```

**Why the funnel** (operator TG 21:53 + lead resume note `870cbe71`):

- Single source of dispatch decisions = no double-escalation when two
  observers both decide to push the same task.
- Lead holds the cross-project context — Phase-1 unblock checks often
  need to read another project's state (e.g. a paper task blocked on a
  `scitex-dev` PR), which is the lead's natural lane.
- Agents stay focused on their own project's lane; their only
  cross-project communication is "report new blocker UP to lead."

### Where each role writes / reads

| Role  | Reads                       | Writes                                                          |
| ----- | --------------------------- | --------------------------------------------------------------- |
| Agent | own project's tasks (filter `agent: <self>`) | own tasks' status + `comments[]`; a2a lead when reporting a new blocker. |
| Lead  | the whole board             | every task during sweeps; a2a each owning agent with ESCALATE notices. |
| Operator | the board UI + the lead's daily summary | resolves `user-pending` / `operator-decision` blockers via the "BLOCKING YOU" panel. |

## Cadence — register with `scitex-dev cron`

The harvest is a **recurring** cycle, not a one-shot. Operator's
directive (TG msg 325): **don't roll a custom scheduler — register
with the ecosystem-wide `scitex-dev cron` plugin pattern** so the
fleet has ONE source of scheduled-job truth (alongside `watch-ci`,
`quota-keepalive`, etc.).

### Where the cron mechanism lives

The supervisor ships in `scitex-dev` (read by every agent container
via `/opt/venv-sac/lib/python3.12/site-packages/scitex_dev/_cli/cron/`):

| CLI verb                     | What it does                                                   |
| ---------------------------- | -------------------------------------------------------------- |
| `scitex-dev cron list`       | show the JobSpec registry + the currently-installed crontab lines |
| `scitex-dev cron install <n>`| materialize JobSpec `<n>` into the user crontab (idempotent — marker `# scitex-dev cron: <n>` pins exactly one line) |
| `scitex-dev cron remove <n>` | strip the named job from the crontab                           |
| `scitex-dev cron exec <n>`   | execute the job body (this is what cron itself calls)          |
| `scitex-dev cron status`     | last-run / next-run hints for each registered job              |

Cadence format: **standard Unix cron** (5-field `minute hour
day-of-month month day-of-week`). Log location:
`~/.scitex/dev/logs/cron-<name>.log` (per-job, operator-facing).

### The 4-step plugin pattern

To add the task-harvest as a registered cron job:

1. **Body** — implement `run_once(...)` in a new module:

   ```
   scitex_dev/_cli/cron/_task_harvest.py
       def run_once() -> None:
           # 1. load the task store (resolve via the standard scitex-cards
           #    store resolver)
           # 2. Phase 1 — re-check every blocked task, walking the
           #    task-dependency chain to its root (see "ROOT BLOCKER
           #    walk" above)
           # 3. Phase 2 — for every RUNNABLE task, a2a-send an
           #    ESCALATE to the owning agent
           # 4. append the audit line to the lead's running log
   ```

2. **Register** in `scitex_dev/_cli/cron/_jobs.py` (`JOB_REGISTRY`):

   ```python
   "task-harvest": JobSpec(
       name="task-harvest",
       schedule="0 */6 * * *",   # q6h default — operator-tunable
       command=(
           "mkdir -p $HOME/.scitex/dev/logs; "
           "scitex-dev cron exec task-harvest "
           ">> $HOME/.scitex/dev/logs/cron-task-harvest.log 2>&1"
       ),
       description="scitex-cards task-harvest (Phase 1 unblock + Phase 2 escalate).",
   ),
   ```

3. **Wire the dispatch** in `scitex_dev/_cli/cron/run.py` — extend
   the `exec_cmd` branch table so `scitex-dev cron exec task-harvest`
   invokes `_task_harvest.run_once()`.

4. **Pin with a test** in `tests/scitex_dev/_cli/cron/test__jobs.py`
   — assert the `JOB_REGISTRY["task-harvest"]` entry exists with
   the expected `schedule` + `command` so a future refactor can't
   silently drop it.

### Existing scitex-dev cron jobs to pattern-match against

- **`watch-ci`** — polls each sac agent's repo for CI failures and
  dispatches A2A fix-forward turns. (`*/10 * * * *`.)
- **`quota-keepalive`** — fires every 30 min at the cron level, self-
  gates to ~2.5h actual fires, pre-starts Claude's rolling quota
  window so quota caps don't surprise the fleet.

`task-harvest` slots into the same family: a fleet-wide
scheduled job that mutates the shared state (here: the
SSoT task store) on a fixed cadence.

### Auxiliary triggers (NOT in cron)

Two extra triggers beyond the cron tick:

- **on every new task creation** — lightweight one-task pass (just
  Phase 2 for the new task) so the lead dispatches it the moment it
  lands. Wire via a hook on `save_tasks` (or the future Gitea
  adapter's webhook), NOT via cron.
- **on demand** — when the operator pings the lead with "what's
  unblocked right now?", the lead invokes `run_once()` directly
  (same body, ad-hoc trigger).

The cron tick is the **default** drumbeat; the auxiliary triggers
keep latency low for new arrivals + operator nudges.

### Tunability

`schedule` lives in ONE place (`JOB_REGISTRY`); changing it is one
diff + one test. Operator may want q1h during a busy phase or q12h
during a quiet phase. Re-install (`scitex-dev cron remove
task-harvest && scitex-dev cron install task-harvest`) picks up
the new schedule.

## What the lead a2a's to whom

After a sweep, the lead sends three kinds of a2a messages:

### 1. To each owning agent — ESCALATE

For every RUNNABLE task owned by `agent: <name>`:

```
[ESCALATE scitex-cards] task-id "Title" — RUNNABLE, no blocker.
   You can do this now. Report PR # / a2a / comment when picked up.
```

The agent's expected response: either **pick it up** (status →
`in_progress`, add a `comments[]` entry naming the worker) or
**bounce it back with a blocker** (status → `blocked`, fill the
4-category enum, a2a lead so the next sweep accounts for it).

### 2. To the operator — daily summary

Once per day, the lead sends ONE Telegram summary to the operator:

```
[task-harvest YYYY-MM-DD] pass:
   - unblocked: N tasks (auto-cleared blockers)
   - dispatched: M tasks to <K> agents
   - awaiting you: P (`operator-decision` / `user-pending`)
   - net board delta: +<arrival> / -<consumption> = <Δ>
```

A NEGATIVE delta is good — consumption > arrival. The summary is the
operator's compact view; the BLOCKING YOU panel on the board is the
detailed view (one click → resolve the awaiting items).

### 3. To self — sweep log

The lead appends a one-line audit entry to its own running log
(`~/proj/scitex-lead/GITIGNORED/RUNNING/task-harvest.md` or equivalent):

```
2026-06-08T06:00Z sweep N=267 → unblocked=4 dispatched=18 awaiting=2 net=-3
```

So the operator-summary delta is reproducible from history.

## Worked example — one sweep

Starting state (267 tasks):
- 5 `status: blocked`
- 18 `status: in_progress`
- 135 `status: pending` (= RUNNABLE)
- rest = `done` / `goal` / `deferred`

### Phase 1 (re-check blockers)

| id                              | blocker          | depends_on           | re-check result                | action     |
| ------------------------------- | ---------------- | -------------------- | ------------------------------ | ---------- |
| paper-clew/sle-pac-fanout       | compute          | sif-build-202606     | `sif-build-202606.status=done` | UNBLOCK    |
| scitex-dev/audit-wave-2         | task-dependency  | scitex-dev/audit-1   | dep still in_progress          | leave      |
| neurovista/onsets-pull          | quota            | (none)               | gh PAT reset overnight         | UNBLOCK    |
| ripple-wm/recompute             | compute          | sac-base.sif rebuild | rebuild still pending          | leave      |
| paper-clew/figure-3             | user-pending     | operator-decision    | no reply 4d                    | bump to `operator-decision` (loud halo) |

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
