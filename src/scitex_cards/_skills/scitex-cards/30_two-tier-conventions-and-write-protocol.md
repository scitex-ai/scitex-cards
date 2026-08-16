---
description: |
  [TOPIC] Two-tier conventions + write protocol — project-level vs global
  [DETAILS] How the fleet uses scitex-cards as the shared SSoT: per-project
  `<project>/.scitex/cards/` (each agent owns its own lane) rolls up into the
  global `~/.scitex/cards/` (fleet-wide aggregate the board renders). Who
  writes what, when, and with which conflict rules — the load-bearing
  contract for the fleet migration off the lead's in-memory TaskList onto
  the persistent board.
tags: [scitex-cards-two-tier-conventions-and-write-protocol, scitex-cards-write-protocol, scitex-cards-fleet]
---

# Two-tier conventions + write protocol

scitex-cards is the fleet's **shared dependency map** (HANDOFF.md
NORTH STAR, operator 9501 + 9667 + 9671 + 9674). For that to work
without per-agent silos OR cross-agent drift, the package follows a
**two-tier convention**:

- **Project tier** — every project / agent owns its own
  `<project>/.scitex/cards/` directory, writes its tasks there.
- **Global tier** — `~/.scitex/cards/` is the fleet-wide aggregate; the
  board renders from it; the operator + lead write here when they
  coordinate cross-project.

This skill documents BOTH tiers + the write protocol that connects
them. Once a project adopts this convention, the fleet can read each
other's task state from one place + the board surfaces the whole map.

## Tier 1 — project-scoped rows

Historically each project had its own on-disk `<project>/.scitex/cards/`
directory. That layout is retired: the canonical store is now a single
SQLite database (`$SCITEX_CARDS_DB`), and "project tier" is a `scope`
value on rows in that one database, not a separate file. The agent
owning a project still writes its OWN tasks with that project's scope;
the per-task `tasks/<task-id>/` directory (`README.md` + `adr.md`
prose) is unchanged.

**What goes in a project-scoped row**:

- Tasks the project agent owns (its own work queue).
- Per-task `agent` field = the owning agent's name (matches the
  agent's sac spec name).
- Per-task `project` field = the project directory basename.
- Cross-project dependencies expressed via `depends_on: [<task-id>]`
  where the target id may live under ANOTHER project's scope — the
  graph builder is lenient on dangling refs until they resolve.

**`tasks/<task-id>/README.md`** is the Issue body — what / why / how, free
markdown, referenced from the task's `note`. Locked filename per operator
TG 9511 / lead a2a `dd1da069`.

**`tasks/<task-id>/adr.md`** is an append-only ADR-template decision log
(see `~/.claude/skills/scitex/general/04_docs/05_adr.md`), one entry per
significant task-scoped decision. Cross-cutting or repo-architectural
decisions live in the OWNING REPO's `docs/adr/NNNN-*.md`; the per-task
adr.md carries a one-line cross-link.

## Tier 2 — the fleet-shared database

The fleet aggregate is no longer a separate directory: it's the SAME
SQLite database, filtered to rows with a fleet-coordination `scope`
(vs a single project's scope). There is one canonical database, not
one-file-per-project rolled up into a second file.

The board (`scitex-cards board`) reads from this database. The mermaid
adapter, the MCP tools, every UI surface — all read from the resolved
`$SCITEX_CARDS_DB` as the canonical source.

Fleet-liveness data (`agents.json`, machine-written by the
sac-status-writer sidecar, ADR-0005) is a separate, purely
machine-generated artifact read by the board's `/agents` endpoint —
never human-edited, never part of the task store.

## Store resolution — one canonical database

Follows `src/scitex_cards/_paths.py` / `_db.py`: the store identity is
`$SCITEX_CARDS_DB` — an explicit env var wins, otherwise it resolves
to the user-canonical `~/.scitex/cards/cards.db` regardless of the
calling process's working directory. There is deliberately no
per-repo copy of the data store (a 2026-07-06 incident showed a
project-local shadow copy serving stale data); a legacy
`tasks.yaml` sidecar living beside the database still holds a few
non-task sections (`users:`, `groups:`, `inboxes:`) pending their own
migration into the database — see `_paths.resolve_tasks_path`.

PathManager handles resolution — agents use `from scitex_cards._db
import resolve_db_path` and never hand-construct the path.

## Write protocol — who writes when

The crux of the two-tier convention. Lead a2a `93e314b2` directly
captured this; the SQLite migration changed the STORAGE (one database
instead of per-project files rolled up by an aggregator) but not the
OWNERSHIP rules below — they still bind.

| Actor | Scope | When | What |
|---|---|---|---|
| project agent | project | task create / status change / blocker change / comment | OWN rows (`scope=agent:<self>`) + `tasks/<id>/README.md` + `tasks/<id>/adr.md` |
| project agent | fleet | rarely; only when explicitly asked by lead/operator | own tasks the lead promoted to fleet-level visibility |
| lead | fleet | fleet-coordination tasks; resolving operator-blockers | cross-project rows + ADR-template decision entries on cross-project rows |
| operator (UI) | fleet | Resolve-button on BLOCKING YOU panel; re-prioritize via GUI | status flips (status=done, blocker=null); priority changes; tag edits |

### Project-agent rules (the "owns its own lane" contract)

- An agent writes rows where `task.agent == <its-own-name>`. It does
  NOT write to OTHER agents' rows (no cross-lane writes).
- Status flips on its own tasks are FAIL-LOUD-validated against the
  Task dataclass (see `proj-scitex-cards-quality-hygiene/README.md`
  for the dataclass; ADR-0002/-0003/-0004 for the closed enums).
- If an agent wants to push a task to ANOTHER agent (e.g. "I need
  the SIF agent to rebuild"), it creates a row with
  `assignee = <other-agent>` + an entry in `comments[]` describing
  the ask. It does NOT directly edit the other agent's rows.

### Lead rules

- Writes fleet-coordination rows the operator and multiple agents
  care about (e.g. release-cutover, shared decisions).
- May resolve a row on behalf of the operator when the operator
  delegates; logs the resolution in `tasks/<id>/adr.md` with
  `Notes` provenance.

### Operator rules (via the UI)

- Sees the fleet view through the board.
- The Resolve button in the BLOCKING YOU panel:
  1. Writes `status: done`, `blocker: null` to the row.
  2. Fires an `a2a notify` to the row's `agent` field with the
     resolution payload.
  3. Optionally appends a `comments[]` entry capturing the
     resolution rationale.
- Re-priority via GUI: writes `priority: <int>` to the row.
- Tag edits via GUI (v1.1): writes `tags: [...]` to the row.

### Conflict / ownership rules

1. **Per-task ownership** is the value of `task.agent`. The owning
   agent has WRITE on every field. Other agents have READ access by
   default + can append `comments[]` but cannot mutate other fields.
2. **Operator + lead** have WRITE on every field on every row
   (sudo, basically).
3. **ACL** (when sac fleet groups land — task #2 / ADR-0006): a
   future `acl: {read: [<groups>], write: [<groups>]}` field gates
   per-task READ/WRITE access. v1 = open (every agent reads
   everything); ACL is the v1.1 hardening pass.
4. **Last-write-wins** at the field grain when two writers race — the
   database serializes concurrent writes, so this is now a rare,
   short-window race rather than a file-merge conflict.

## How a project adopts this convention

1. Add tasks via `scitex-cards add` (CLI) or the MCP `add_task` tool
   with `--scope agent:<you> --project <repo-basename>`; they land in
   the shared database tagged with your scope.
2. For any task that warrants long-form context: create
   `tasks/<task-id>/README.md` (Issue body).
3. For any task-scoped decision worth recording:
   `tasks/<task-id>/adr.md` (ADR template entry).
4. The row shows up on the operator's board within 5s.

## How to read this from another project / agent

```python
# In any agent's code, to read the FLEET map:
from scitex_cards import list_tasks

all_tasks = list_tasks()          # every row visible to this identity
mine = list_tasks(scope="agent:<you>")  # just your own slice
```

## NEVER hand-edit the store — the long form

Mandate 2 in [SKILL.md](SKILL.md), in full. On 2026-06-13 the then
file-based store was found TRUNCATED MID-STRING: board render, throughput
script and every agent's read/write broke until the lead repaired it by
hand. PR-#166's post-dump round-trip-validate layer made the WRITER side
safer, but only for writes through this package's API. The move to SQLite
removed that failure class; the rule is unchanged, because a hand-edit
(direct SQL, an editor, a GUI save on a raw export) bypasses every safety
net the package has.

**Emergency repair exception.** A store that is ALREADY broken — will not
open, will not validate — cannot be repaired through the API, so a
hand-repair is justified in that one case. You MUST (a) back up the broken
store, (b) verify the repair validates before declaring done, and (c) report
the episode so the API-side net can be hardened. The lead's 2026-06-13
repair followed this protocol exactly.

## Cross-reference

- **HANDOFF.md** — SSoT DATA LAYOUT + NORTH STAR pillars #3 and #4.
- **ADR-0002** — `kind` enum, fail-loud Literal pattern.
- **ADR-0003** — `kind: "decision"` for first-class decision-nodes.
- **ADR-0004** — `blocker` enum, orthogonal to `kind`.
- **ADR-0005** — fleet-liveness panel + SSH-fanout watcher.
- **ADR-0006** — full board UI spec + GUI→code wiring.
- **`scitex_dev` skill** `general/05_paths/01_local-state-dirs` — the
  ecosystem local-state convention this skill inherits from.
