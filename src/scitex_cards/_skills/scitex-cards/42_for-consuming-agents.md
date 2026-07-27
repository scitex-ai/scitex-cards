---
description: |
  [TOPIC] For consuming agents — quick-onboard "how do I use scitex-cards
  as MY task SSoT?"
  [DETAILS] One-page protocol for any fleet agent: which CLI / MCP /
  Python entry point to use for create / list / update / comment /
  complete, the closed-enum (fail-loud) schema, the title-prefix
  convention, and the lead↔worker coordination wire. Read this first if
  you've just been told "use scitex-cards for your todos."
tags:
  [
    scitex-cards-consuming-agent,
    scitex-cards-onboarding,
    scitex-cards-fleet-protocol,
  ]
---

# For consuming agents — adopt scitex-cards as YOUR task SSoT

You are a fleet agent (a SAC peer) **OR the lead**.
The operator has made scitex-cards the **single canonical home** for
fleet task state — yours, the lead's, every other agent's, the
operator's own. This skill tells you exactly what to do.

This skill is the **teaching surface** — wire it as a `required_skill`
in your spec (see [§ Propagation](#propagation--the-path-mechanism)
below) and it auto-loads on every agent boot.

Three rules, in priority order:

1. **No memory.** Every task you accept lives in the shared store as a
   structured row from the moment you accept it. Never carry a
   commitment "in your head."
2. **Fail loud, fail fast.** `scitex-todo` validates the schema on
   every read + every write. If you set a status / kind / blocker
   value that isn't in the closed enum, the write RAISES. Don't
   catch-and-ignore — fix the input.
3. **Write through the API.** Use the CLI, the MCP tool, or the
   Python API. **Never edit the store's files by hand** (operator
   standing directive, TG 9494). Direct edits bypass the validator;
   the next legitimate write may roll back your change OR refuse to
   load the store.

---

## Store identity — one database, `$SCITEX_CARDS_DB`

The canonical store is a SQLite database. There is **one** identity
axis: `$SCITEX_CARDS_DB` (the resolved database path) — see
`src/scitex_cards/_paths.py`. There is no tiered legacy-sidecar
precedence chain anymore; older docs describing a "project root vs
user root" file precedence are historical and no longer apply.

Confirm where you're about to write BEFORE you write:

```bash
scitex-todo resolve-store
# → prints {resolved: <path>, backend: sqlite, ...}
```

See [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
for scope conventions (project-local vs fleet-shared work) — those
conventions live on as a `scope=`/`project=` field distinction inside
the single database, not as separate files.

---

## Your first task, in 30 seconds (fresh agent quick-start)

```bash
# 1. Confirm scitex-todo is installed + which store it resolves to.
scitex-todo --version
scitex-todo resolve-store                  # prints the resolved DB path

# 2. Add a smoke task to YOUR slice.
scitex-todo add <you>-smoke-$(date +%s) \
    '[P2] smoke: confirm I can write to the store' \
    --scope agent:<you> \
    --assignee <you> \
    --status deferred

# 3. List your slice + confirm the row is there.
scitex-todo list-tasks --scope agent:<you> --json | jq '.[].id'

# 4. Mark it done.
scitex-todo done <you>-smoke-<that-stamp> --by <you>

# 5. See yourself on the board.
# Open http://<board-host>:8051/  — your row is visible within 5s.
```

If any step fails: STOP and `reply` to your lead with the exact
failing command + full stderr. Don't retry-loop. ([§ Operating
discipline](#operating-discipline--what-not-to-do))

---

## Schema — closed enums, fail-loud

Every task is a `Task` dataclass (defined in `scitex_cards._model.Task`).
The validator REJECTS unknown values in the closed enums below.

**Required fields:**

- `id` (str, globally unique, kebab-case, **prefix with your project**:
  e.g. `scitex-todo-fleet-rollout`, `clew-cohort-a-rerun`).
- `title` (str, short scannable label, ≤ 80 chars).
- `status` (closed enum below).

**Closed enums (fail-loud):**

| Field     | Allowed values                                                                   |
| --------- | -------------------------------------------------------------------------------- |
| `status`  | `goal` · `pending` · `in_progress` · `blocked` · `done` · `deferred` · `failed`  |
| `kind`    | `task` (default if absent) · `compute` · `decision`                              |
| `blocker` | `compute` · `dependency` (alias `dep`) · `operator-decision` · `agent-wait` · `none` |

`blocker` is **only allowed when** `status == "blocked"`. Setting a
blocker on a non-blocked row raises.

**Recommended fields (operator-co-designed surface, TG 9667):**

- `assignee` (str) — **PRIMARY agent-linking field. Set this to YOUR
  agent name** (e.g. `scitex-todo`). `scitex-todo list-tasks --assignee
  <agent-id>` filters correctly — this is THE field that lets every
  consumer (lead, board, you) ask "show me agent X's open tasks."
  Forward-compat: the dataclass also has an `agent` field as the
  operator-co-designed long-term replacement; the migration is
  staged (CLI gains `--agent` as alias, deprecates `--assignee`)
  but TODAY you write `assignee`.
- `task` (str) — the 1-line CURRENT-task BIG text on the board card.
  Distinct from `title` (the short scannable label). Populate this for
  the card to read well.
- `project` (str) — your project's directory basename (e.g.
  `scitex-todo`). Matches the canonical id prefix.
- `host` (str) — where the work happens (`spartan` / `ywata-note-win`
  / etc.).
- `goal` (str) — WHY (parent-goal text); rendered as the 🎯 line on the
  card. One short sentence.
- `priority` (int) — lower = higher priority. Within your project
  tier, set a tight 1..N rank; the operator can re-rank globally on
  the board.
- `last_activity` (ISO-8601 UTC) — recency drives green/amber/red
  card coloring on the board.
- `depends_on: list[str]` / `blocks: list[str]` / `parent: str` —
  graph edges. Use the canonical `<project>/<local-id>` form for
  cross-project deps.
- `pr_url` / `issue_url` — GH/Gitea links.
- `comments: list[{ts, author, text}]` — append-only activity log
  (see "Coordinating with other agents" below).

**Title-prefix convention** (the operator's at-a-glance scan):

| Prefix       | Meaning                                                          |
| ------------ | ---------------------------------------------------------------- |
| `[P0]`       | Highest business priority / live blocker                         |
| `[P1]`       | Momentum (paper, infra in-flight)                                |
| `[P2]`       | Parallel queue / hygiene                                         |
| `[CI]`       | CI hygiene                                                       |
| `[CAL]`      | Calendar / commitment                                            |
| `[GOAL]`     | `status: goal` umbrella (north-star objective)                   |
| `[PKG]`      | Per-package umbrella in the 66-pkg ecosystem-quality tree        |
| `[strategy]` | Secondary tag (catalogs / GTM)                                   |

So a typical title looks like `[P1] (PR #334 follow-up) verify bun
child survives restart fleetwide`.

---

## CRUD — the verbs you'll actually use

Examples are CLI; MCP tool names match 1:1 (Convention A); Python API
names match too.

### CREATE — `scitex-todo add`

```bash
scitex-todo add \
  scitex-todo-fleet-rollout \
  '[P1] Fleet rollout of scitex-todo skill across agents' \
  --status deferred \
  --scope agent:scitex-todo \
  --assignee scitex-todo \
  --priority 10 \
  --note 'See tasks/scitex-todo-fleet-rollout/README.md'
```

> **CLI gap (in flight, see [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md)):**
> `add` does not yet accept `--task` / `--project` / `--host` / `--agent`
> / `--goal` / `--blocker` / `--pr-url` / `--issue-url` / `--kind` —
> the legacy `--scope` / `--assignee` are the bridge until those land.
> Use the Python API until the CLI catches up.

MCP equivalent: tool `add_task` (same kwargs, returns JSON).

Python equivalent:

```python
from scitex_cards import add_task
add_task(
    None,                           # tasks_path; None = resolve default
    id="scitex-todo-fleet-rollout",
    title="[P1] Fleet rollout of scitex-todo skill across agents",
    status="pending",
    scope="agent:scitex-todo",
    assignee="scitex-todo",
    priority=10,
    note="See tasks/scitex-todo-fleet-rollout/README.md",
)
```

### LIST — `scitex-todo list-tasks`

```bash
scitex-todo list-tasks --scope agent:scitex-todo --json
```

Filters today: `--scope` / `--assignee` / `--status` (exact match).
Use `--json` for machine output.

> **CLI gap (see gap analysis):** `--agent` / `--project` / `--host` /
> `--blocker` / `--kind` / `--blocking-me` filters are NOT in yet.
> Pipe through `jq` on the `--json` output for now.

### UPDATE — `scitex-todo update`

```bash
scitex-todo update scitex-todo-fleet-rollout \
  --status in_progress \
  --priority 5 \
  --note 'Skill draft pushed PR #N; gap closures next'
```

Pass an empty string (`--scope ''`) to CLEAR a field.

> **CLI gap (see gap analysis):** same field set as `add` — operator-
> co-designed fields are missing. Bridge via Python API.

### COMMENT (activity log) — Python API only TODAY

The `comments: list[{ts, author, text}]` array is the **append-only
activity log** every agent writes to when coordinating cross-lane.
There is no `scitex-todo comment` verb today (that's the #1 CLI gap
— see gap analysis). Use the Python API:

```python
import scitex_cards
from scitex_cards import _store
import datetime as _dt
_store.update_task(
    None,
    "scitex-todo-fleet-rollout",
    comments=[
        # ... existing comments preserved by load → append → save ...
        {
          "ts": _dt.datetime.utcnow().isoformat() + "Z",
          "author": "scitex-todo",
          "text": "Skill draft pushed PR #N; awaiting lead design ACK.",
        },
    ],
)
```

> **Don't** hand-edit the `comments[]` field outside the API. The
> closed ts/author/text shape is validated; missing keys raise.

### COMPLETE — `scitex-todo done`

```bash
scitex-todo done scitex-todo-fleet-rollout --by scitex-todo
```

Stamps `_log_meta.completed_at` (UTC ISO-8601) + `completed_by` (the
`--by` value, defaults to `$SCITEX_TODO_AGENT_ID` then `$USER`).
Idempotent (re-doneing a `done` task keeps the original stamp).

MCP: `complete_task`. Python: `scitex_cards.complete_task`.

### RE-OPEN (undo a done / resolve)

There's no `reopen` CLI verb today. Re-open by setting a status that
carries a decision — `deferred` if it can wait, `in_progress` if you are
picking it back up:

```bash
scitex-todo update scitex-todo-fleet-rollout --status deferred
```

The MCP `reopen_task` verb is not equivalent: it is the Resolve→Undo
partner and flips `done` back to `blocked` / `blocker=operator-decision`.

The web board's `/reopen` HTTP endpoint (PR #61) is operator-facing;
the CLI parity is on the gap list.

---

## Long-form prose — `tasks/<id>/README.md` + `adr.md`

Whenever a task has substantive context, seed the per-task dir:

```bash
mkdir -p tasks/scitex-todo-fleet-rollout
$EDITOR tasks/scitex-todo-fleet-rollout/README.md   # what / why / how
$EDITOR tasks/scitex-todo-fleet-rollout/adr.md      # ADR-template decisions
```

- `README.md` is the **Issue BODY** — free-form markdown. Reference it
  from the task's `note` field (one-line "see ...").
- `adr.md` is the **append-only decision log** in the SciTeX ADR
  template (`~/.claude/skills/scitex/general/04_docs/05_adr.md`):
  six sections (Status / Context / Decision / Consequences / Notes),
  immutable once accepted, superseded by a new entry.

NO sidecar `metadata.json` in README.md — the per-task dir is
**prose only**; the database row is the structured-metadata SSoT
(operator TG 9513, lead a2a `45488600`).

---

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
scitex-todo update my-task --status blocked   # --blocker dependency pending CLI parity
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
         "author": "scitex-todo",
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
channel push (`scitex-todo:task:*`, sub-second — the fast path that
the 5s poll backstops if the bus is down).

- **Worker:** writes to its own scope (`scope=agent:<you>`,
  `project=<repo>`) and pushes a channel event on high-priority
  status flips so the lead + operator wake immediately.
- **Lead:** reads the fleet view via the board (`:8051`) +
  `scitex-todo list-tasks`, subscribes to the `scitex-todo:task:*`
  firehose (every worker write is a wake-up; no auto-action unless
  the row is a `kind: decision` the lead owns), and resolves
  operator-decision rows on the BLOCKING YOU panel when delegated.
- **Operator:** watches the board (`:8051`, auto-refresh ~5s via
  `/rev` mtime poll) and the BLOCKING YOU panel — strict predicate
  `status == "blocked" AND blocker == "operator-decision"`. Resolve
  writes `status: done`, fires a `notify <agent>` a2a, and optionally
  appends a `comments[]` entry.

---

## Operating discipline — what NOT to do

- **Don't hand-edit the store's files with `sed` / `awk` / a text
  editor.** Through the CLI/MCP/Python every time.
- **Don't catch-and-ignore validator errors.** `TaskValidationError`
  is the schema telling you the input is wrong. Fix the input.
- **Don't write to other agents' scopes.** Only `comments[]` is
  append-only-cross-lane.
- **Don't put prose in the `note` field.** `note` is one short line;
  full prose lives in `tasks/<id>/README.md`.
- **Don't invent new statuses / kinds / blockers.** The validator
  REJECTS them. If a new value is needed, propose it in `adr.md` for
  the package owner (`scitex-todo`) to add to the enum.

---

## Sanity-check yourself once you've adopted

```bash
scitex-todo resolve-store                  # confirm the DB path you expect
scitex-todo list-tasks --scope agent:<your-agent-name> --json | jq length
scitex-todo add smoke-$(date +%s) '[P2] smoke from <your-agent-name>'
scitex-todo done smoke-<that-stamp>
# Open http://<board-host>:8051/ — your row should appear in <5s.
```

If any of the above fails: STOP and `reply` to your lead with the
exact failing command + full stderr. The package's "fail loud" rule
applies to you using it too — silent dropouts are the bug we're
trying to eliminate.

---

## Propagation — the @path mechanism

This skill is the **teaching surface**: the operator's directive
(2026-06-07) is that every fleet agent auto-loads it on boot, so
"how do I file a TODO" is the same answer everywhere.

1. **Pip-install pins the version** — `pip install
   scitex-todo>=<version>` lands the bundled skills under
   `<site-packages>/scitex_cards/_skills/scitex-todo/`.
2. **Agent's spec references the bundled path** under a
   `required_skills:` entry — exact grammar is the SAC container
   glue's domain; the canonical reference shape is:
   `"@scitex_cards:_skills/scitex-todo/40_for-consuming-agents.md"`.
   (See [41_cli-mcp-gap-analysis.md § G](41_cli-mcp-gap-analysis.md#g-propagation-the-path-mechanism)
   for the wiring rationale.)
3. **Container boot resolves the reference** — the skill text loads
   into the agent's context; the agent now knows the protocol.
4. **Operator host: `scitex-todo skills install --claude-symlink`**
   back-fills the symlink under `~/.claude/skills/scitex/` so
   Claude Code on the operator's host sees the same skill.

**Versioning**: the skill is **version-pinned via the package**, NOT
edited live. Editing one skill leaf does NOT propagate to a consuming
agent's spec until the consumer pip-bumps `scitex-todo`. That gives
the lead a deterministic rollout — pin the version on one agent at a
time, watch it adopt, broaden once stable.

## Reference

- [04_cli-reference.md](04_cli-reference.md) — full CLI surface.
- [05_mcp-tools.md](05_mcp-tools.md) — MCP tool surface.
- [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
  — write protocol contract (who writes when, conflicts, ACL).
- [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md) — the
  known surface gaps you'll hit while this skill rolls out; tracks
  what's bridged via Python API today + what's in flight.
- `Task` dataclass: `src/scitex_cards/_model.py` (the single schema
  source).
- Operator directives: HANDOFF.md NORTH STAR + Telegram 9494 ("no
  direct writes; validator + dataclass; fail loud, fail fast,
  SSoT").
