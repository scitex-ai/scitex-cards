# ADR-0007: Task dataclass = single schema source (feeds validator + UI + Gitea + future frontmatter)

## Status

Accepted (2026-06-07)

## Context

scitex-cards's schema has been accreting across the operator-co-design
loops (TG 9517 / 9667 / 9671 / 9678 — lead a2a `28967019` / `2bd37bd2`
/ `a62db48c` / `6d9b6073`). Each loop added closed-enum fields (ADR-
0002 `kind`, ADR-0003 `kind:"decision"`, ADR-0004 `blocker`) +
additive optional fields (host / created_at / agent / goal / pr_url /
issue_url / task). The validator (`_model._validate_tasks`) was
keeping up; the FE types (`types/board.ts` `GraphNode`) and the
backend graph payload (`handlers/graph.py`) were updating in lockstep;
the Gitea field-map in HANDOFF.md tracks every field. **Four places**
encoding the same schema. Drift is inevitable.

Operator TG 9517 ("promise the shape", translated) + lead a2a `28967019` named this
explicitly: the defined schema must be **structurally enforceable**,
and the validator + UI render + Gitea field-map + future README-
frontmatter (HANDOFF.md SSoT DATA LAYOUT pivot) must read from
**one source**. The Python dataclass is the natural choice — it's
the simplest expression of "fields + types + defaults" that Python
ships natively, and every other surface (validator / FE types / Gitea
adapter / frontmatter parser) can derive its shape from the dataclass.

## Decision

Define `scitex_cards._model.Task` as the canonical `@dataclass(slots=True)`
schema source. Every consumer reads from it:

1. **The validator** — `_validate_tasks` checks each task dict against
   the dataclass field set + applies field-scoped rules (closed enums,
   compute-only fence, blocked-status-only fence). The dataclass IS
   the shape; the validator is the type-and-rule check ON that shape.

2. **The UI render contract** (ADR-0006) — each card field maps to
   exactly one dataclass attribute. The `Task → card` table in
   ADR-0006's "Region 2" section is the contract; the FE's
   `types/board.ts` `GraphNode` mirrors the dataclass shape over the
   wire (same field names, same types modulo TS / Python differences).

3. **The Gitea field-map** (HANDOFF.md) — each dataclass field maps
   to a Gitea-issue field via label / milestone / assignee / body.
   When the Gitea adapter lands (LONG-ARC EXIT roadmap item), it
   reads from the dataclass to drive the translation.

4. **The future README-frontmatter** (HANDOFF.md SSoT-layout pivot) —
   when the per-task-dir layout becomes the SSoT (the "FUTURE OPTION"
   in HANDOFF.md), the frontmatter parser deserializes into the
   dataclass + writes via `to_dict()`. Same wire as today's YAML
   load/save path.

### Construction + persistence

- `Task.from_dict(d)` — defensive constructor. Unknown keys silently
  dropped (forward-compat). Missing keys fill with the dataclass
  default. Legacy blocker spelling `"dep"` normalizes to canonical
  `"dependency"` via `_BLOCKER_ALIASES`. Does NOT raise on schema
  violations — that's `_validate_tasks`'s job. Safe to call on any
  task-shaped dict from any source.

- `Task.to_dict()` — round-trip back to the dict shape the ruamel
  writer in `save_tasks` consumes. Required fields (`id`, `title`,
  `status`) ALWAYS emit so a to_dict-then-from_dict-then-validate
  cycle never fails. Default-equal optional fields + empty containers
  OMIT so the YAML stays compact.

### Field layout (operator-co-designed order, TG 9667)

```python
@dataclass(slots=True)
class Task:
    # CORE — operator's named fields
    id: str
    title: str
    task: str | None = None         # the BIG 1-line for the card
    project: str | None = None      # directory / repo basename
    host: str | None = None         # where the work happens
    created_at: str | None = None   # ISO-8601 UTC
    goal: str | None = None         # 🎯 line on the card

    # UI — drives color + filters
    status: str = "pending"         # see VALID_STATUSES
    agent: str | None = None        # owning agent
    last_activity: str | None = None  # ISO-8601; recency drives color
    blocker: str | None = None      # see VALID_BLOCKERS; only on status=blocked
    pr_url: str | None = None       # optional GH/Gitea PR link
    issue_url: str | None = None    # optional GH/Gitea issue link

    # GRAPH wiring (preserved from pre-PR-#52)
    depends_on: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    parent: str | None = None       # nested drill-down
    priority: int | None = None
    note: str | None = None
    comments: list[dict] = field(default_factory=list)

    # KIND discriminator + compute-only metadata (ADR-0002 / 0003)
    kind: str | None = None         # see VALID_KINDS
    job_id: str | None = None       # compute only
    command: str | None = None      # compute only
    started_at: str | None = None   # compute only
    finished_at: str | None = None  # compute only

    # LEGACY shared-fleet additive (Phase-1 SSoT)
    scope: str | None = None
    assignee: str | None = None     # legacy; `agent` is the operator-co-designed replacement
    _log_meta: dict | None = None
```

### `host` moved out of the compute-only fence

A consequential side-effect: `host` USED to be in the kind=compute-
only field list (ADR-0002). The operator's generic shape (TG 9667)
makes `host` a general-purpose "where does this task live/run"
handle — any row can carry it, not just compute rows. So `host`
moved out of the compute-only fence and into the generic operator-
field block validated above.

The compute-only fence still covers `job_id` / `command` /
`started_at` / `finished_at` because their semantic ("the compute
job's identifier / invocation / runtime bookends") doesn't fit a
non-compute task.

Backward-compat: old data carrying `host` only on `kind=compute`
rows keeps loading. New data can put `host` on any row.

### `blocker` enum rename: `dep` → `dependency`, plus explicit `none`

Operator co-design (TG 9667): the canonical spelling is `dependency`
(more readable), not the legacy `dep` from ADR-0004's first cut. The
validator accepts BOTH during a deprecation window (`VALID_BLOCKERS`
includes both); the dataclass normalizes on read (`_BLOCKER_ALIASES`).
Writers that round-trip through the dataclass produce canonical
`dependency`; legacy writers producing `dep` keep working until they
migrate.

Also added: `blocker: "none"` — the explicit "we looked, no specific
blocker named" value. Distinct from field-absent (= "haven't named
the variant yet"); useful for the Resolve flow in the BLOCKING YOU
panel (ADR-0006) where the operator wants to record an explicit
"no blocker" state without flipping `status` off `blocked`.

## Consequences

**Positive:**

- One source of schema truth. A new field is one addition to the
  dataclass + the validator (in `_model.py`) + the FE type (in
  `types/board.ts`) — the latter two derive from the dataclass; no
  manual sync drift.
- Forward-compat by default: `from_dict` ignores unknown keys, so a
  future YAML with a new field doesn't crash an older loader.
- Backward-compat by default: missing keys fill with defaults +
  legacy spellings normalize. Existing 270-task stores keep loading.
- Round-trip safe: `to_dict-from_dict-validate` is a no-op for valid
  rows; required fields always emit; defaults omit.
- Sets the convention for any future schema work: update the
  dataclass FIRST; everything else follows.

**Negative:**

- Two API surfaces during the migration window — `Task` (attribute
  access) and the legacy dict-style (`task["status"]`). Handlers +
  `_store.py` + MCP layer still use dict-style today; the migration
  is incremental (per "no big-bang"). Documented as a follow-up.
- The dataclass carries every field — including the legacy `scope` /
  `assignee` / `_log_meta` — even when they're absent on most rows.
  Slight memory overhead per task (~6 None slots). Acceptable;
  `slots=True` keeps the per-instance footprint small.

## Follow-ups (deferred from this PR per the scope-tightening to
unblock board-full-ui-v3)

1. **Handler migration**: `_django/handlers/graph.py`, `_store.py`,
   `_mcp_server.py` migrate from dict-style to `Task` attribute-
   style access. `from_dict` + `to_dict` make this safe at any
   per-handler granularity.
2. **`depends_on` / `blocks` reference-integrity validator**: today
   the graph builder silently drops edges to unknown ids. Move the
   check up to `_validate_tasks` and fail-loud per the
   ADR-0002/0004 pattern.
3. **Cycle detection** on `depends_on` (Tarjan SCC). Hard-error at
   load with the cycle's id chain.
4. **7 → 4 status enum migration** (per the operator-co-designed
   simpler enum `working / waiting / done / blocked`). Defer until
   the operator signs off on the one-shot rewrite; today the FE
   maps the 7-value enum to the 4-color set in the renderer.
5. **`create_board(...)` factory** wiring the PR #55 ports
   skeleton (`TaskSyncPort` / `NotificationPort` / `LivenessPort` /
   `IdentityACLPort`) into the Django app construction. The board
   today goes through `load_tasks` / `save_tasks` directly; the
   factory + adapter injection lands as the live-data PR (step e).

Each follow-up is intended as a focused single-PR landing on top of
this dataclass. The dataclass is the foundation; the follow-ups are
mechanically derived from it.

## Notes

- Surfaced 2026-06-07 by operator TG 9667 (the field shape) + lead
  a2a `6d9b6073` (the "Task dataclass = single schema source"
  framing) + `a62db48c` (the migration table + the dependency rename
  + the additive-fields endorsement).
- This ADR is layered on top of ADR-0002 (`kind` enum) + ADR-0003
  (`kind: "decision"`) + ADR-0004 (`blocker` enum). All three of
  those validators are now consumed by the dataclass's field set;
  the dataclass IS the union.
- ADR-0006 (full board UI spec) has a "Region 2 — Task → card
  render contract" table that maps each dataclass field to its UI
  card element. Together this ADR + ADR-0006 give the complete
  schema → UI pipeline.
- The 270-task store at `~/.scitex/cards/tasks.yaml` carries the old
  7-value status enum. The new dataclass tolerates both during the
  deprecation window — `from_dict` doesn't normalize statuses
  today; the migration is a separate ADR + one-shot rewrite.
- ADR-0007 cross-link: per-task `adr.md` for the quality-hygiene
  task at `tasks/proj-scitex-cards-quality-hygiene/adr.md` (to be
  created when the implementation starts) carries the task-scoped
  application of this convention.
