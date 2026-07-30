# 32. Agent self-consumption loop

**The board IS your work queue.** Every fleet agent — `lead` and every
worker — runs the same loop: on wake, pick the top task from the
board, work it, comment progress, update status, repeat. Local
TODO/FUTURE files are scratch only (operator + lead 2026-06-12
doctrine, see SKILL.md MANDATE).

This sub-skill documents the 7-step loop, the supporting CLI verbs
(`scitex-cards next`, `scitex-cards update`), and how the wake
(`scitex-cards watch --push`) hands a fresh request off to the owning
agent.

---

## The 7-step loop

Boot pattern — every agent's harness implements this:

```bash
# 1. Read the next runnable task FOR THIS AGENT.
task_json="$(scitex-cards next --mine --auto-claim --json)" || {
  # Exit-1 = empty backlog. Idle.
  exit 0
}
task_id="$(echo "$task_json" | jq -r .id)"

# `--auto-claim` already flipped the task to status=in_progress and
# stamped a "starting (auto-claim by <me>)" comment in one atomic
# write, so two parallel agents on the same queue race on the
# YAML-file lock, NOT on the task. (See _cli/_loop.py.)

# 2. Read the task body — for richer context, the agent harness loads
#    the JSON via `--json` AND opens the per-task README.md if any:
#    ~/.scitex/todo/tasks/<task_id>/README.md

# 3. Work the task. As progress happens, comment back:
scitex-cards update "$task_id" --add-comment "step 1 done, starting step 2"

# 4. On completion:
scitex-cards update "$task_id" --status done --pr-url "$pr_url" \
  --add-comment "merged PR #<n>"

# 5. If blocked, name the blocker:
scitex-cards update "$task_id" --status blocked --blocker dependency \
  --add-comment "blocked on todo-pXX (a2a relay)"

# 6. Loop back to step 1 until the backlog is empty.

# 7. Idle. The next wake comes from `scitex-cards watch --push` (someone
#    commented on a task assigned to you, or a new task was added with
#    your agent name).
```

### Status transitions

| from        | to            | trigger                                       |
| ----------- | ------------- | --------------------------------------------- |
| pending     | in_progress   | agent claims via `next --auto-claim`          |
| in_progress | done          | work complete                                 |
| in_progress | blocked       | dependency / decision / compute wait surfaces |
| blocked     | pending       | operator / lead resolves the blocker          |
| any         | failed        | unrecoverable — annotate in `comments`        |
| any         | deferred      | operator-decided "not now"                    |

Closed enum mirrored from `_model.VALID_STATUSES`; the validator
rejects any other value.

### `next` predicate (one canonical rule, shared by all agents)

`scitex-cards next` filters by:

- `agent` (or legacy `assignee`) matches the requested name,
- `status` in `{pending, in_progress}`,
- `blocker` is None (a blocked row is NOT runnable; the operator or
  the lead must clear it first).

And sorts by:

1. `priority` ASC (lower = higher; `None` ranks last).
2. `last_activity` DESC — favours tasks the agent was already
   working on.
3. `created_at` DESC — newer requests beat older at equal priority.
4. `id` ASC — deterministic tiebreak.

See `scitex_cards._next.next_task` for the Python entry, used by the
CLI verb above.

### The wake side (`scitex-cards watch --push`)

The watcher runs once per ~2 seconds on the host that hosts the
canonical store (`$SCITEX_CARDS_DB`). On each tick:

- Diff against the previous snapshot.
- For every NEW task assigned to an agent, OR every newly-appended
  comment, OR every status flip: POST a small payload to that
  agent's a2a `http://127.0.0.1:<port>/v1/turn`.
- Per-agent debounce: at most one wake per ~30s per agent.

Wake payload (stable shape; future Gitea-webhook variant emits the
same):

```json
{
  "trigger": "scitex-cards-watcher",
  "trigger_kind": "task_added" | "comment" | "status_changed",
  "task_id": "todo-pXX-...",
  "task_title": "...",
  "summary": "comment by lead: please pick this up",
  "store_path": "/scitex-cards/cards.db"
}
```

The agent's harness receives the wake, runs the 7-step loop above,
and updates the board. The lead monitors the board state via a
separate `scitex-cards list-tasks` cron — stalled per-agent queues
or 3-consecutive abandonments escalate to the lead's a2a (NOT the
operator's; the lead is the single operator-facing voice).

Agent-registry resolution (where the watcher finds each peer's
a2a port):

- A top-level `agents:` list in `tasks.yaml`:
  `[{name: scitex-cards, a2a_port: 41234}, ...]`. This static list is
  scitex-cards's own SSoT for the agent port table — no external
  runtime is consulted.

---

## Why this matters

Without this loop, agents do work IF AND ONLY IF the operator or the
lead manually tasks them (via a2a, a Telegram nudge, etc.). The
board exists but doesn't drive anything. With it, the operator drops
a request, the watcher fires, the owning agent picks it up, status
flows back — the lead only coordinates and escalates.

The board is no longer just a place to write things down. It's the
fleet's WORK QUEUE.

(operator TG 12038, lead-approved 2026-06-12; this skill ships
alongside the `next` + `watch --push` verbs in PR #_).
