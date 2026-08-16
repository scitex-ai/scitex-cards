---
description: |
  [TOPIC] Two-tier conventions + write protocol — project-level vs global
  [DETAILS] How the fleet uses scitex-cards as the shared SSoT: per-project
  `<project>/.scitex/cards/` (each agent owns its own lane) rolls up into the
  global `~/.scitex/cards/` (fleet-wide aggregate the board renders). Who
  writes what, when, and with which conflict rules — the load-bearing
  contract for the fleet migration off the lead's in-memory TaskList onto
  the persistent board.
tags: [scitex-cards-conventions, scitex-cards-write-protocol, scitex-cards-fleet]
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

## Architectural backbone — `scitex-cards` is STANDALONE; the fleet plugs in via PORTS

Operator's defining rule (TG 9678, lead a2a `fae53b8e`):

> "scitex-cards はそれだけで独立したパッケージであるべきで、他を知らないが、
> extension port は持っている"
> (scitex-cards MUST be standalone, knows nothing about fleet/sac/
> scitex-specifics, but exposes extension ports through which
> fleet-specific behaviour plugs in.)

This skill describes the *conventions* an adopting agent must follow.
Those conventions are wired through extension ports — the **core
package never imports sac, a2a, SSH-fanout, or the 6-stream list**.
Reading this skill, mentally replace:

- "sync via git + GitHub" ⇒ "the TaskSyncPort impl your fleet installs"
- "publish on `scitex-cards:task:<id>`" ⇒ "NotificationPort.publish"
- "SSH-fanout to peer hosts for liveness" ⇒ "LivenessPort.list_agents"
- "sac fleet groups gate ACL" ⇒ "IdentityACLPort answers"

The fleet-specific implementations live in a SEPARATE package
(e.g. `scitex-cards-fleet` or in the scitex-agent-container glue),
not inside `scitex-cards`. ADR-0006 in `docs/adr/` has the four port
Protocol definitions + the dependency-injection wiring. This skill
documents the **adoption-side** conventions; the **core-side**
contract is in the ADR.

A standalone `pip install scitex-cards` ships with default
implementations (LocalFileSync / InProcessPubSub / NullLiveness /
OpenACL) so the package is independently usable. The conventions
below describe the FLEET deployment shape — operator-host
running aggregator + watcher + board, agents writing project
tiers, etc.

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

**What goes in `tasks/<task-id>/README.md`**:

The Issue body — what / why / how. Free-form markdown. Referenced
from the task's `note` field. Locked filename per operator TG 9511 /
lead a2a `dd1da069`.

**What goes in `tasks/<task-id>/adr.md`**:

Append-only ADR-template decision log per the SciTeX ADR convention
(see `~/.claude/skills/scitex/general/04_docs/05_adr.md`). One ADR
template entry per significant task-scoped decision (lifecycle flip,
plan change). Cross-cutting / repo-architectural decisions live in
the OWNING REPO's `docs/adr/NNNN-*.md` per the two-tier ADR
placement (HANDOFF.md SSoT-layout section); the per-task adr.md
carries a one-line cross-link.

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

| Actor          | Scope    | When                                                              | What                                                                       |
| -------------- | -------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------- |
| project agent  | project  | task create / status change / blocker change / comment            | OWN rows (`scope=agent:<self>`) + `tasks/<id>/README.md` + `tasks/<id>/adr.md` |
| project agent  | fleet    | rarely; only when explicitly asked by lead/operator               | own tasks the lead promoted to fleet-level visibility                      |
| lead           | fleet    | fleet-coordination tasks; resolving operator-blockers             | cross-project rows + ADR-template decision entries on cross-project rows  |
| operator (UI)  | fleet    | Resolve-button on BLOCKING YOU panel; re-prioritize via GUI       | status flips (status=done, blocker=null); priority changes; tag edits      |

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

## Cross-host reach — SSH-fanout liveness (ADR-0006)

The database itself is the single canonical store (no per-host copies
to reconcile). Cross-host pieces that DO still fan out over SSH:

- **Fleet liveness** (`agents.json`) — rebuilt every ~5s by the
  sac-status-writer sidecar from SSH-fanout polls of peer sac
  registries; feeds the board's `/agents` panel. A peer that doesn't
  answer is flagged UNREACHABLE, not silently omitted.
- **`db export`/`db import`** — the cross-host pull path for a peer
  that cannot reach the canonical database directly (see `sac db
  export` / `sac db import`).

## Task referencing / citation — `<project>/<local-id>` (ADR-0006)

Stable, fleet-wide task ids use a slash-separated two-segment form:

```
<project>/<local-id>
```

- `<project>` = the project's directory basename (matches `Task.project`).
- `<local-id>` = the agent's chosen string, unique within the project.

Examples:

```
paper-scitex-clew/cohort-a-rerun
scitex-hub/decide-prod-cutover-final-go
scitex-cards/proj-scitex-cards-fleet-liveness
```

**URL scheme** (the board's Django serves it):

```
http://<board-host>:8051/task/<project>/<local-id>       # canonical
http://<board-host>:8051/t/<project>/<local-id>          # short alias
```

Operator / lead / agents cite this URL in chat / a2a / comments.
Pasting it into a markdown comments[] entry auto-links via the
board's renderer. Citation in chat: "see
`paper-scitex-clew/cohort-a-rerun`" is unambiguous across the
fleet.

**Backward-compat**: existing single-segment ids
(`proj-scitex-cards-compute-state-deps`) carry through; the
aggregator stamps `_log_meta.canonical_id = "<project>/<id>"` on
read so the URL works on legacy rows.

## Update → subscriber notification — reuse a2a/channel push (ADR-0006)

When a task is updated (via the board's `_store.update_task`, an
agent's direct YAML edit + commit, or the operator's Resolve button),
the change is **published on the sac channel bus** the fleet already
uses for agent wake-ups:

```
event channel: scitex-cards:task:<project>/<local-id>
payload:       {task_id, changes, ts, actor}
```

**Subscription rules**:

| Subscriber       | Subscribes to                                            | Action on receive                                                            |
| ---------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------- |
| owning agent     | `scitex-cards:task:<own-project>/*`                       | wakes (empty-beacon-fix + wake-generalize) + acts on the change              |
| dependent agent  | `scitex-cards:task:<each-of-its-depends_on-ids>`          | wakes + re-evaluates readiness; auto-unblock if a dep flipped to done       |
| UI (every viewer)| `scitex-cards:task:*` (filtered client-side)              | re-fetches /graph + re-renders affected card / panel                         |
| lead             | `scitex-cards:task:*` firehose                            | logs into _log_meta; no auto-action                                          |
| operator         | (interacts via UI; UI is the subscriber)                 | UI surfaces the change visually                                              |

**Critical synergy**: this rides the SAME push infra being hardened
by the empty-beacon fix (proj-scitex-agent-container) + the
wake-generalize (any-channel-wakes-idle-agent). scitex-cards is one
of the loadiest consumers — every task update is a potential
agent-wake event. **Do NOT invent a parallel notification system.**

**Fallback to polling**: if the push bus is down, subscribers fall
back to the existing 5s `/rev` polling (AutoRefresh.tsx). Push is
the FAST path; poll is the durable path. Same shape as the
GitHub-vs-SSH-fanout split above.

## Cross-reference

- **HANDOFF.md** — SSoT DATA LAYOUT + NORTH STAR pillars #3
  (cross-host sync) and #4 (live/online/shared/machine-readable).
- **ADR-0002** — `kind` enum, fail-loud Literal pattern.
- **ADR-0003** — `kind: "decision"` for first-class decision-nodes.
- **ADR-0004** — `blocker` enum, orthogonal to `kind`.
- **ADR-0005** — fleet-liveness panel + SSH-fanout watcher.
- **ADR-0006** — full board UI spec + GUI→code wiring.
- **`tasks/proj-scitex-cards-quality-hygiene/README.md`** — Task
  dataclass = single schema source.
- **`scitex_dev` skill** `general/05_paths/01_local-state-dirs` —
  the ecosystem local-state directories convention this skill
  inherits from.

## Canonical auto-merge poll — CI-green = `{CLEAN, UNSTABLE}`

Lead a2a `9c4d3dc4` (2026-06-07): a wedge discovered on PR #52 + #55
because the auto-merge poll only treated `CLEAN` as terminal. Both
PRs sat at `UNSTABLE` for 85+ minutes — required checks were green,
but a NON-required check was failing, so the poll slept forever.

**Canonical rule** (every fleet auto-merge loop should treat this
the same — capture for clew / neurovista / ripple / etc. patterns):

> The operator's standing **"CI-green ⇒ auto-merge"** authorization
> applies when `mergeStateStatus` is in `{CLEAN, UNSTABLE}`.
> `UNSTABLE` means a NON-required check is red while all required
> checks pass, which is still mergeable per branch protection.

Bash shape (the bug-fixed version every auto-merge poll should use):

```bash
until s=$(gh pr view "$PR" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null); \
        [ "$s" = "CLEAN" ] \
     || [ "$s" = "UNSTABLE" ] \
     || [ "$s" = "DIRTY" ] \
     || [ "$s" = "BLOCKED" ] \
     || [ "$s" = "HAS_HOOKS" ] \
     || [ "$s" = "BEHIND" ]; do
    sleep 30
done
# Terminal-mergeable: CLEAN or UNSTABLE → gh pr merge --squash
# Non-mergeable terminal: DIRTY (conflicts) / BLOCKED (required check
# failed) / BEHIND (base moved) / HAS_HOOKS (commit hooks failing) →
# investigate, don't auto-merge.
```

**Hygiene caveat** (lead a2a `9c4d3dc4`): `UNSTABLE` hides WHICH
non-required check is failing. Auto-merge on it is correct, but if
the SAME check is unstable across many PRs, the check is probably a
real signal we're ignoring and should be PROMOTED to required.
Flag the pattern — don't let "always UNSTABLE" become invisible.

This pattern is canonical for every fleet auto-merge loop, not just
scitex-cards's. clew / neurovista / ripple / etc. that ship their own
wait-on-CI auto-merge loops should match this shape. Documented here
as the reference for the fleet's dogfood-of-scitex-cards adoption.
