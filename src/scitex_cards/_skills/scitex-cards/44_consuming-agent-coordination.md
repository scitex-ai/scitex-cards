---
description: |
  [TOPIC] Consuming-agent coordination and the lead-worker wire
  [DETAILS] How consuming agents coordinate through the board, how the lead consumes it, and the lead-worker shared-board sync.
tags:
  [
    scitex-cards-consuming-agent-coordination,
    scitex-cards-fleet-adoption,
  ]
---

# Consuming-agent coordination and the lead-worker wire

Split out of [42_for-consuming-agents.md](42_for-consuming-agents.md) to keep both leaves inside the
skill-size budget. That file remains the entry point.

## Coordinating with other agents

Three patterns. Pick the one that fits the shape of the dependency.

### A. Hard dep — "I'm blocked by their task"

Add `depends_on: ["<their-project>/<their-task-id>"]` to YOUR row,
then `--status blocked --blocker dependency`. The fleet aggregator
joins the edge; the board shows a red edge from their row → yours;
when they flip theirs to `done`, the operator + you see your row's
blocker drop within 5s.

```bash
# (Once --depends-on lands as a CLI flag on update; today via Python.)
scitex-cards update my-task --status blocked   # --blocker dependency pending CLI parity
# Python:
import scitex_cards, datetime as _dt
scitex_cards.update_task(
    None, "my-task",
    status="blocked",
    # blocker="dependency",         # pending --blocker on CLI; Python honors it
    # depends_on=["neurovista/their-task-id"],  # pending --depends-on on update
)
```

### B. Coordination note — "FYI on their task"

Append a `comments[]` entry on THEIR row. You can read+append other
agents' rows (READ is open; comments[] are append-only); you cannot
mutate their other fields. The owning agent (per `task.agent`)
controls all non-comment writes.

```python
# Adds a comment to a task you don't own.
import scitex_cards, datetime as _dt
scitex_cards.update_task(
    None, "neurovista/cohort-a-rerun",
    comments=[
        # existing entries first (load → preserve)
        {"ts": _dt.datetime.utcnow().isoformat() + "Z",
         "author": "scitex-cards",
         "text": "FYI my fleet-rollout PR will need this; flagged."},
    ],
)
```

### C. Ask — "I need them to do something"

DO NOT create a task in their scope with their name as `agent`. Create
the ASK on YOUR scope with `status: blocked`, `blocker: agent-wait`,
a `comments[]` entry naming the agent + the ask. The lead /
operator routes through the board (or via a2a). Once they accept the
ask they create their own row in their scope (with a `depends_on:` of
your id, closing the loop).

This preserves "agents own their own lane" — no cross-lane writes
even with the best intentions.

---
## Lead-role usage — the lead is a consumer too

The lead (`scitex-lead`) is a first-class consumer of this skill, not
just the worker agents ("the lead writes its own board through
scitex-cards" — operator, 2026-06-07). The lead's writes differ from
a worker's in **scope**, not in **mechanics**:

- **Default scope:** fleet-coordination rows (`scope=agent:scitex-lead`
  or a cross-project scope) — release cutovers, cross-repo ADRs, the
  operator's "BLOCKING YOU" queue, fleet-wide campaigns.
- **Per-task assignee:** `assignee: scitex-lead` on rows the lead
  drives; rows the lead REASSIGNS to a worker land with that worker's
  `assignee` value, and the worker takes ownership from then on.
- **Resolves rows on behalf of the operator:** when the operator
  delegates a Resolve, the lead writes the resolution + an `adr.md`
  Notes entry capturing the rationale + provenance.

After seeding a task for a specific repo's agent, the row's owning
agent (`assignee`) inherits the write-lane and the lead steps back to
monitoring.
## Lead ↔ worker shared-board sync

Three sync wires keep the lead, every worker, and the operator's
board converged: the aggregator sidecar (SSH-fanout, ~5s tick,
surfaces UNREACHABLE per-tier rather than silently omitting rows),
`git push`/`pull` on durable per-project state (minutes-scale, the
cross-host substrate the aggregator complements), and a sac
channel push (`scitex-cards:task:*`, sub-second — the fast path that
the 5s poll backstops if the bus is down).

- **Worker:** writes to its own scope (`scope=agent:<you>`,
  `project=<repo>`) and pushes a channel event on high-priority
  status flips so the lead + operator wake immediately.
- **Lead:** reads the fleet view via the board (`:8051`) +
  `scitex-cards list-tasks`, subscribes to the `scitex-cards:task:*`
  firehose (every worker write is a wake-up; no auto-action unless
  the row is a `kind: decision` the lead owns), and resolves
  operator-decision rows on the BLOCKING YOU panel when delegated.
- **Operator:** watches the board (`:8051`, auto-refresh ~5s via
  `/rev` mtime poll) and the BLOCKING YOU panel — strict predicate
  `status == "blocked" AND blocker == "operator-decision"`. Resolve
  writes `status: done`, fires a `notify <agent>` a2a, and optionally
  appends a `comments[]` entry.

---
