# ADR-0003: Decisions are first-class graph nodes (kind="decision")

## Status

Accepted (2026-06-07)

## Context

scitex-cards had two row shapes after ADR-0002: ordinary tasks (`kind:
"task"`, the default) and compute jobs (`kind: "compute"`, externally
updated by an automated writer). A third kind of "row" kept showing up
in operational reality: **decisions** — moments where the fleet was
waiting on someone (usually the operator) to choose between options.
Examples that surfaced during the 2026-06-06 design rounds:

- "decide: hub prod-cutover final GO" — operator chose safety-first;
  the final GO is still pending. Multiple hub-side tasks depend on it.
- "decide: clew (a)/(b) inline-DAG" — pending operator pick between
  two design options. clew's DAG task depends on it.

These were initially modeled as an attribute (a `blocker` field on the
task that's stuck). The operator (TG 9524) refined the model:

> "if tasks have dependencies, decisions have dependencies too — a
> decision is itself a node."

This is right. A decision has its OWN lifecycle (proposed → under
deliberation → resolved → recorded), its OWN body (Context / Decision
/ Consequences, which IS an ADR), and OTHER TASKS depend on it. Lead
(a2a `4691b114`) confirmed the framing supersedes the attribute-only
"blocker field" approach.

A taskscoped "blocker" attribute alone reads as "this task is stuck on
something" but loses (a) the shared decision-node multiple dependent
tasks all hang off and (b) the natural place to record the Context /
Decision / Consequences trail when the decision resolves.

## Decision

Extend `VALID_KINDS` to include `"decision"`:

```python
VALID_KINDS: tuple[str, ...] = ("task", "compute", "decision")
```

Same closed-validated-set + fail-loud pattern as ADR-0002 — adding a
new variant is a one-line tuple edit, but unknown values raise.

A `kind: "decision"` row is a first-class graph node with these
semantics:

1. **Lifecycle = the existing status enum, NO new vocabulary.** The
   decision's progression is just the standard
   `pending` (undecided / proposed) →
   `in_progress` (under deliberation) →
   `done` (resolved) — with
   `deferred` (postponed) and `failed` (rejected / abandoned) as the
   off-ramps. The board's existing status colors, blocked-glyph,
   AutoRefresh-on-mtime wire all work unchanged. Reusing the status
   enum is intentional — see Consequences below.

2. **Dependents auto-unblock via the existing dep-graph wire.** Other
   tasks `depends_on` the decision-node's id, exactly as they would
   any other task. When the operator (or whoever decides) flips the
   decision-node's status to `done` and writes the ADR body, the
   board's BlockersSection naturally drops the decision from each
   dependent's "Blockers" list on the next /rev tick. No new
   machinery; same wire as everything else.

3. **The body lives in `tasks/<id>/adr.md`** — Context / Decision /
   Consequences / Notes in the ADR template (operator TG 9511 /
   `dd1da069` locked filename convention). Decision-node ↔ ADR is 1:1.
   The per-task `adr.md` IS where the Decision section's text goes;
   the rest of the ADR template (Status, Context, Consequences, Notes)
   is what makes the resolution durable + auditable.

4. **`kind` and `blocker` are ORTHOGONAL.** A kind=decision row's
   `blocker` is USUALLY `"operator-decision"` (the operator is the
   decider) but can be `"agent-wait"` ("agent X to confirm") or
   `"compute"` ("the model picks"). The validator does NOT cross-
   imply; ADR-0004 has the blocker-side details.

5. **Board affordance**: ⚖️ glyph prefix on kind=decision node labels
   (parallel to ⚙ for compute and ⊞ for parent-drill). A LOUD purple-
   gold halo on `kind=decision AND blocker=operator-decision` rows —
   that's the variant the operator OPENS the UI to find. An "⚖️
   unblocks N" impact-count badge gives the prioritization signal
   (higher N = more leverage, decide that one first; lead a2a
   `554435df`).

6. **"Awaiting operator" lens**: a `👤 awaiting you N` chip in the
   Progress summary, STRICT predicate
   `kind=="decision" AND status=="blocked" AND blocker=="operator-
   decision"`. No transitive dilution. The anti-flood antidote.

## Consequences

**Positive:**

- The operator's pain ("what's waiting on me") is structurally
  surfaced rather than scanned from chat. The LOUD halo + impact
  badge + lens chip read at a glance, no text-skimming.
- Decision-bodies (Context / Decision / Consequences) become a
  durable, search-grep-able artifact in `tasks/<id>/adr.md`. No more
  "what did we decide about X" archaeology.
- ZERO new machinery on the dep-graph side. The decision-node fits
  into every existing pattern: AutoRefresh, BlockersSection,
  drill-down, blocker-vis, status colors. The mental model is
  consistent (everything is a node; some node kinds have specific
  affordances).
- The Gitea-adapter (LONG-ARC EXIT in HANDOFF.md) maps cleanly —
  `kind: "decision"` becomes a `kind/decision` label in Gitea. No
  schema regression.
- Sets a precedent for future kinds — when something needs a graph-
  node-shaped surface (e.g. `kind: "ci"` for CI runs in task #15),
  the playbook is now: add a literal to VALID_KINDS, define
  lifecycle vocabulary (reusing VALID_STATUSES if possible), wire a
  glyph + tooltip + drawer affordance. ADR-0002 was the pattern;
  this ADR proves the pattern composes.

**Negative:**

- One more dimension for operators to internalize. Mitigated by
  reusing the status enum + glyph-not-color signaling: a decision-
  node looks like a task with a ⚖️ prefix; it's not a wholly new
  shape on the board.
- The "decision usually has blocker=operator-decision" relationship
  is convention, not validator-enforced. An agent could create a
  `kind=decision` row with `blocker=compute` legitimately, but
  could also forget to set `blocker` at all — that's a soft-degrade
  to "blocked but no variant named" (the board renders generic 🚧).
  Documented in ADR-0004; not a structural problem (forgotten
  blocker = falls out of the "awaiting operator" lens, which is
  the correct behavior).
- Adding `"ci"` later (task #15) is still a one-line tuple edit, but
  any future schema migration that renames a kind requires a
  superseding ADR per the immutability rule.

## Notes

- Surfaced 2026-06-06 by operator TG 9522 + 9524; refined by lead
  a2a `4691b114` (the "decisions-as-nodes" insight) + `2bd37bd2`
  (orthogonality) + `554435df` (impact-badge for prioritization).
- The per-task `adr.md` for this decision lives in
  `tasks/proj-scitex-cards-blocker-dimension/` (task-scoped ADR-0001
  entry mirrors this repo-architectural one with a cross-link in
  Notes).
- Cross-link forward: ADR-0004 captures the `blocker` enum +
  orthogonality details; ADR-0005 captures the fleet-liveness
  panel (which the lens chip pairs with).
- Two LIVE decision-nodes seeded immediately at PR merge:
  `decide-hub-prod-cutover-final-go` + `decide-clew-a-b-inline-dag`,
  both `kind=decision, status=blocked, blocker=operator-decision`,
  with the hub-cutover + clew-dag tasks repointed to depends_on the
  new ids. Board surfaces the operator's two pending decisions
  within 5s of the YAML write via AutoRefresh.
