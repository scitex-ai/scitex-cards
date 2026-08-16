---
description: |
  [TOPIC] Adopting the shared task store from your project
  [DETAILS] How a project agent (clew / neurovista / scitex-dev / scitex-hub
  / ripple-wm / scitex-orochi / scitex-agent-container / etc.) writes its
  own tasks into the shared fleet board (the SQLite store) so the operator's
  board (http://127.0.0.1:8051/) auto-renders your tasks as a labeled column.
  [HOW] Set `project` + `agent` on every task you create + write via the
  scitex-cards CLI or Python API. Three fields is the minimum for your work
  to surface on the live board.
tags: [scitex-cards-adopting]
---

# Adopting the shared task store from your project

You are a project agent. The operator's board lives at
`http://127.0.0.1:8051/` and renders from the shared SQLite store
(`$SCITEX_CARDS_DB`). This skill is the SHORTEST useful adoption path
so your tasks show up as your own column on the operator's board
within 5 seconds of your first write. The full convention +
write-protocol contract lives in
[`30_two-tier-conventions-and-write-protocol.md`](30_two-tier-conventions-and-write-protocol.md);
this skill is the how-to-adopt-NOW counterpart.

## The 30-second adoption

Three required fields per task — that's the minimum:

```
id: <stable-string-id>          # e.g. paper-scitex-clew/cohort-a-rerun
title: <short scannable label>
status: <see VALID_STATUSES>
# ----- and the three NEW fields the live board needs -----
project: <your-project-dir-basename>    # column header on the board
agent:   <your-agent-name>              # operator's "who is doing this"
task:    <one-line BIG-TEXT current-task description>
```

Set `project = "neurovista"` (or `"paper-scitex-clew"`, `"scitex-dev"`,
`"scitex-hub"`, `"ripple-wm"`, `"scitex-orochi"`, `"grant"`,
`"scitex-agent-container"`, …) and your column appears on the board
auto-magically — the operator's view auto-discovers columns from
distinct `project` values.

## Write paths

### From the CLI

```bash
scitex-cards add \
    --id paper-scitex-clew/cohort-a-rerun \
    --title "Cohort A rerun #50" \
    --status in_progress \
    --project paper-scitex-clew \
    --agent scitex-clew \
    --task "PAC SLE multi-lane GPU compute: a100 lane #38 of 50"
```

(See [`04_cli-reference.md`](04_cli-reference.md) for full flags.)

### From the Python API

```python
from scitex_cards import add_task

add_task(
    id="paper-scitex-clew/cohort-a-rerun",
    title="Cohort A rerun #50",
    status="in_progress",
    project="paper-scitex-clew",
    agent="scitex-clew",
    task="PAC SLE multi-lane GPU compute: a100 lane #38 of 50",
)
```

(See [`03_python-api.md`](03_python-api.md) for the full API.)

### From the MCP tool (agent → board)

```
add_task(
  id="paper-scitex-clew/cohort-a-rerun",
  title="Cohort A rerun #50",
  status="in_progress",
  project="paper-scitex-clew",
  agent="scitex-clew",
  task="PAC SLE multi-lane GPU compute: a100 lane #38 of 50"
)
```

(See [`05_mcp-tools.md`](05_mcp-tools.md) for all MCP verbs.)

## Update on status change

When your task progresses:

```bash
scitex-cards update <id> --status done                # finished
scitex-cards update <id> --status blocked \           # blocked, name the variant
    --blocker compute                                #   compute / dependency / operator-decision / agent-wait / none
scitex-cards update <id> --task "<new one-line>" \    # update what you're doing
    --last_activity "$(date -u +%FT%TZ)"
```

The board picks the change up via 5-second AutoRefresh (`/rev` mtime
poll). No further action on your side.

## What goes in `task` vs `title` vs `note` vs `goal`

| Field    | What it carries                                                 | UI placement        |
| -------- | --------------------------------------------------------------- | ------------------- |
| `title`  | short scannable label (the operator's table-view tag line)      | always              |
| `task`   | ONE-LINE BIG-TEXT description of what you're doing RIGHT NOW    | huge text on card   |
| `goal`   | the WHY — parent goal / why this matters                        | 🎯 italic line above task |
| `note`   | optional markdown detail (long-form context)                    | drawer body         |

Set `task` SHORT (it's the eye-magnet); set `goal` to the higher-order
intent; use `note` for long-form (or use the per-task-dir
`tasks/<id>/README.md` per skill 30).

## Operator-decision blockers (the LOUD lens)

If you NEED the operator to decide something, model the decision as
its OWN row with `kind: decision` + `blocker: operator-decision`:

```bash
scitex-cards add \
    --id decide-paper-scitex-clew-a-b-inline-dag \
    --title "decide: clew (a)/(b) inline-DAG depth" \
    --status blocked \
    --kind decision \
    --blocker operator-decision \
    --project paper-scitex-clew \
    --agent scitex-clew \
    --task "decide: clew (a)/(b) inline-DAG depth — awaiting operator" \
    --goal "DAG visualization for clew claim-tracing"
```

Your dependent task then `depends_on: [decide-paper-scitex-clew-a-b-inline-dag]`.
The operator sees it in the GOLD `🚧 BLOCKING YOU` panel on the board,
clicks Resolve, the decision flips to `done`, your dependent
auto-unblocks within 5 seconds. See ADR-0003 + ADR-0004 in
`docs/adr/` for the full decision-node semantics.

## Scope note

For drafts you don't want on the fleet board's default view yet, write
with a project-scoped `scope=agent:<you>` value — it's still the SAME
database, just filtered differently by consumers. Spec in
`30_two-tier-conventions-and-write-protocol.md`.

## Store resolution

```
explicit path  →  $SCITEX_CARDS_DB  →  ~/.scitex/cards/cards.db
```

Set `$SCITEX_CARDS_DB` to point every call in this process at a
specific database (useful for tests / scratch stores); unset it to use
the user-canonical default.

## Operator's open questions = `kind: decision` + `blocker: operator-decision`

The board's BLOCKING-YOU panel is the operator's #1 view. Every time
your agent needs the operator's input — decision, sign-off, GO/HOLD,
A vs B pick — model it as a decision-node row (see the snippet above).
The operator sees a SINGLE list of "what needs you" + clicks Resolve;
all dependent agent work auto-resumes. This is the structural answer
to "返事が来ない＝死んだのと同じ" (operator TG 9576).

## What NOT to do

- Do NOT silently update other agents' tasks (own-lane only — see
  skill 30 write-protocol).
- Do NOT use legacy `blocker: "dep"` for new rows; use canonical
  `blocker: "dependency"` (the validator still accepts both during the
  deprecation window — see ADR-0007).
- Do NOT cram prose into the `note` field — long-form goes in
  `tasks/<id>/README.md`; ADR-shaped decisions go in
  `tasks/<id>/adr.md`. See skill 30.

## Quick verification

After your first write:

```bash
curl -s http://127.0.0.1:8051/rev          # confirm task count incremented
```

Then open the board at http://127.0.0.1:8051/ — your project column
should appear within 5 seconds via AutoRefresh; your task carries
your project's color rail + the BIG text.

## Cross-references

- [`02_quick-start.md`](02_quick-start.md) — `import scitex_cards as card`.
- [`03_python-api.md`](03_python-api.md) — full Python API surface.
- [`04_cli-reference.md`](04_cli-reference.md) — full CLI verbs.
- [`05_mcp-tools.md`](05_mcp-tools.md) — MCP tool surface for agent callers.
- [`30_two-tier-conventions-and-write-protocol.md`](30_two-tier-conventions-and-write-protocol.md) — full fleet spec: project tier / global tier / write-protocol / cross-host sync / citation / push-notification / Core-vs-Ports-vs-Adapters.
- `docs/adr/0006-full-board-ui-spec-*.md` + `0007-task-dataclass-*.md` — the dataclass + board UI ADRs the live board renders from.
