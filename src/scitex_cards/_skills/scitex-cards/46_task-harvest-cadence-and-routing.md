---
description: |
  [TOPIC] Task-harvest cadence and routing
  [DETAILS] Registering the harvest as a cron JobSpec, the lead-centric funnel routing, and what the lead sends to whom.
tags:
  [
    scitex-cards-task-harvest-cadence-and-routing,
    scitex-cards-task-harvest,
  ]
---

# Task-harvest cadence and routing

Split out of [40_task-harvest.md](40_task-harvest.md); that file is the entry point.
## Cadence — register with `scitex-dev cron`

The harvest is a **recurring** cycle, not a one-shot. Operator's
directive (TG msg 325): **don't roll a custom scheduler — register
with the ecosystem-wide `scitex-dev cron` plugin pattern** so the
fleet has ONE source of scheduled-job truth (alongside `watch-ci`,
`quota-keepalive`, etc.).

### Where the cron mechanism lives

The supervisor ships in `scitex-dev` (read by every agent container
via `/opt/venv-sac/lib/python3.12/site-packages/scitex_dev/_cli/cron/`):

| CLI verb | What it does |
|---|---|
| `scitex-dev cron list` | show the JobSpec registry + the currently-installed crontab lines |
| `scitex-dev cron install <n>` | materialize JobSpec `<n>` into the user crontab (idempotent — marker `# scitex-dev cron: <n>` pins exactly one line) |
| `scitex-dev cron remove <n>` | strip the named job from the crontab |
| `scitex-dev cron exec <n>` | execute the job body (this is what cron itself calls) |
| `scitex-dev cron status` | last-run / next-run hints for each registered job |

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

| Role | Reads | Writes |
|---|---|---|
| Agent | own project's tasks (filter `agent: <self>`) | own tasks' status + `comments[]`; a2a lead when reporting a new blocker. |
| Lead | the whole board | every task during sweeps; a2a each owning agent with ESCALATE notices. |
| Operator | the board UI + the lead's daily summary | resolves `user-pending` / `operator-decision` blockers via the "BLOCKING YOU" panel. |
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
