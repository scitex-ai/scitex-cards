# ADR-0002: Make `kind` a closed `Literal[…]` enum that raises on unknown values

## Status

Accepted (2026-06-06)

## Context

The compute-state-deps work (north-star pillar #1 — dependencies extend
DOWN TO COMPUTE STATE, not just CI) adds a new discriminator field on
every `tasks.yaml` row:

- `kind: "task"` (default, may be absent) — an ordinary, human-updated
  task row.
- `kind: "compute"` — a row representing an external compute job
  (Spartan slurm submission, SIF rebuild, …) whose status is updated by
  an automated writer (out-of-scope for this ADR; see task #15).

The discriminator gates two downstream behaviours:

1. **UI affordance** — the FE renders a ⚙ glyph on the node label and a
   compute-metadata KV table in the `NodeDetailPanel` ONLY when
   `kind === "compute"`. Rows with the wrong kind get the wrong UI.
2. **Writer ownership** — the future Spartan/CI watcher uses `kind ==
   "compute"` to find rows it's allowed to update. A row with the wrong
   kind would either get falsely auto-updated (writer thinks it owns
   it) or silently ignored (writer doesn't find it).

The choice in front of us was: closed validated set
(`Literal["task", "compute"]`, raise on anything else) vs. free-string
with a warning. Free-string is cheaper to extend (no validator change
when `"ci"` is added later) but makes a typo like `kind: comput`
silently create an unrecognized kind — defeating the discriminator's
whole purpose.

This sits inside the broader **fail-loud principle** the operator + lead
explicitly named on 2026-06-06:

- Operator TG 9517 (translated): "promise the shape" — and ENFORCE it.
  scitex-cards should be a high-quality package; the defined schema must
  be structurally enforceable, not just documented.
- Lead a2a `2c7a431d` (compute-state-deps design nod): "kind: use a
  CLOSED, VALIDATED set (Literal['task','compute']) that RAISES on an
  unknown value — fail-loud per today's principle (free-string would
  let a 'comput' typo silently create an unrecognized kind)."
- The same principle drives scitex-io's recent `fail-loud` pass and the
  fleet's STX-NM / PA-306 no-silent-degradation rules. scitex-cards
  schema validation joins the same family.

## Decision

`kind` is a **closed validated enum** defined in
`src/scitex_cards/_model.py`:

```python
VALID_KINDS: tuple[str, ...] = ("task", "compute")
```

`_validate_tasks` raises `TaskValidationError` on any `kind` value that
is neither absent nor in `VALID_KINDS`, with an error message that names
the bad value AND the valid set so the writer can fix it:

> task 'x' has invalid kind 'comput'; must be one of ('task', 'compute')
> or absent (defaults to 'task')

Absence of the `kind` field is equivalent to `kind: "task"` — the
default, ordinary-task path. The validator only raises on a
**present-but-unknown** value.

**Placement / design principles:**

1. **Closed enum, not free-string.** Typos must fail loudly at YAML
   write/load time, not silently at "the watcher mysteriously didn't
   pick up my row" time. Discriminator-driven branches (FE affordance,
   writer ownership) must be able to trust the data shape — no
   defensive `if kind not in known: treat-as-task` code anywhere
   downstream.

2. **Closed in the typo sense, open in the variant sense.** Adding a
   new kind (e.g. `"ci"` when task #15 wires GitHub-Actions rows) is a
   ONE-line update to `VALID_KINDS`; the rest of the schema and FE
   follow mechanically. The enum is closed in that unknown values
   raise, NOT in that new variants are hard to add. (Lead `2c7a431d`:
   "extensible: 'ci' lands when task #15".)

3. **Reuse existing statuses for compute rows.** Compute rows use the
   SAME `VALID_STATUSES` enum as ordinary tasks
   (`pending` = queued, `in_progress` = running, `done` = exit 0,
   `failed` = non-zero/OOM/wallclock, `blocked` = resource constraint,
   `deferred` = paused). No new status vocabulary — one less moving
   part for the writer side to reason about and one less enum to keep
   in sync across renderers. The README's status table documents the
   compute-row interpretation alongside the task-row interpretation.

4. **Compute-metadata fields gated by `kind`.** The new optional fields
   `job_id` / `host` / `command` / `started_at` / `finished_at` are
   only allowed when `kind == "compute"`. Setting any of them on a
   non-compute row is fail-loud (`set kind: compute or remove the
   field`). The writer is responsible for the content; the schema
   only enforces shape (non-empty string when present).

5. **Writer-side deferred to task #15.** This ADR fixes the schema and
   the visual layer. The actual squeue/CI watcher binary (who owns each
   writer, where it runs, the job_id → row mapping mechanics) is a
   separate decision co-designed with the lead in task #15.

## Consequences

**Positive:**

- Typos in `kind` are caught at YAML write/load time. The discriminator
  is now truly load-bearing.
- Downstream branches (FE conditional rendering, future writer code) can
  trust the shape — no `kind not in known` defensive paths.
- The validator pattern (`Literal[…]` + closed tuple + raise-with-context)
  becomes the **seed for the full schema-validator pass** scoped to
  task `proj-scitex-cards-quality-hygiene` (operator TG 9517 / lead a2a
  `28967019` / `2bd37bd2`). The same fail-loud treatment extends
  schema-wide: `depends_on` / `blocks` reference integrity + cycle
  detection + a typed `Task` dataclass as the single schema source.
  One coherent arc landed across two PRs (this seed in PR #52; the
  full pass in `feat/task-dataclass-and-strict-validators`).
- Adding `"ci"` (task #15) is a one-line tuple edit + a test addition.
  No semantic redesign.
- Compute rows REUSING existing statuses means the FE's status
  colouring, the mermaid renderer's class definitions, and the
  progress chips don't need parallel branches for compute vs. task.

**Negative:**

- Any future agent that wrote a free-string `kind` value pre-`#52`
  (none today — the field is new) would fail to load. Mitigation: the
  field is brand new in this PR; no legacy data carries a `kind` value.
- A future schema migration that renames a kind (e.g. `"compute"` →
  `"job"`) requires either (a) accepting both for a deprecation
  window, or (b) a one-shot YAML rewrite. Per ADR immutability rule,
  renaming MUST come with a superseding ADR.
- The "compute fields only when `kind == "compute"`" rule means an
  agent that wants to ANNOTATE an ordinary task with `host` metadata
  (for documentation, say) has to either set `kind: compute` (which
  changes UI + writer semantics) or use the existing `note` /
  `comments[]` fields. Acceptable: the metadata fields have a specific
  contract; "host" on a task = "external watcher will read this"; if
  the writer shouldn't read it, it doesn't belong here.

## Notes

- Surfaced 2026-06-06 by proj-scitex-cards during the lead's design nod
  on the compute-state-deps proposal (a2a `2c7a431d`).
- Reinforced 2026-06-06 by operator TG 9517 (high-quality-package
  ask) and lead a2a `28967019` (full schema-validator audit).
- The locked filename convention for per-task-dir ADRs (operator TG
  9511 / lead a2a `dd1da069`) is `tasks/<id>/adr.md` for task-scoped
  entries; this ADR sits at `<pkg>/docs/adr/NNNN-…md` because the
  decision is repo-architectural per the two-tier placement rule in
  HANDOFF.md. The per-task `tasks/proj-scitex-cards-compute-state-deps/
  adr.md` ADR-0001 carries a one-line cross-link to this file.
- Cross-link: ADR-0001 in this directory (`0001-universal-task-layer.md`)
  defined scitex-cards as the fleet's universal task layer; this ADR is
  the first concrete schema-shape decision inside that layer.
- Cross-link forward: the full schema-validator pass will be ADR-0003
  (or similar) when `feat/task-dataclass-and-strict-validators` lands.
