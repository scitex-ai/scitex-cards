# ADR-0004: `blocker` is a closed Literal enum on blocked rows, orthogonal to `kind`

## Status

Accepted (2026-06-07)

## Context

The operator's acute pain (TG 9522): "I cannot tell what is waiting
on ME." The board today surfaces a row's status (blocked / in_progress
/ etc.) but doesn't name **what kind** of thing is blocking a blocked
row. That distinction matters because each variant maps to a different
operator action:

- waiting on a **compute** job → check Spartan / wait it out
- waiting on a **dep** task → unblock by acting on the upstream task
- waiting on an **operator decision** → THE OPERATOR's action
- waiting on a specific **agent** action → ping that agent

ADR-0003 promoted `kind: "decision"` to a first-class graph-node shape.
That handles the "decision-as-node" half of the operator's pain.
The other half is the variant-on-blocked-rows: we need a closed enum on
the BLOCKER side too, so the board can color / lens / prioritize by
what's blocking each row.

A free-string `blocker` would let typos ("compoute") silently disappear
from any lens that filters on the value. Same failure mode as ADR-0002
on `kind` — same fix.

## Decision

Add `VALID_BLOCKERS` as a closed validated tuple on the Python side
(mirrored on the FE side as a `BlockerKind` Literal union):

```python
VALID_BLOCKERS: tuple[str, ...] = (
    "compute",            # compute resources — waiting on a kind=compute row
    "dep",                # dependency       — waiting on another task
    "operator-decision",  # user decision    — waiting on the operator (LOUD)
    "agent-wait",         # other-agent wait — waiting on a specific agent action
)
```

Operator's exact enumeration (TG 9524). Closed-in-the-typo sense
(unknown values raise), open-in-the-variant sense (add to the tuple +
write a superseding ADR if the rule changes).

### Placement / design principles

1. **`blocker` is allowed ONLY when `status == "blocked"`.** Naming a
   blocker variant on a non-blocked row is meaningless — the validator
   raises with `set status: blocked or remove the blocker field`.
   Same shape as the "compute fields only on kind=compute" rule from
   ADR-0002. The validator output names the bad field + says how to
   fix it.

2. **`blocker` is OPTIONAL even on blocked rows.** Soft-degrade: a
   `status: blocked` row without a `blocker` reads as "we know it's
   blocked but haven't named the variant yet." The board renders the
   generic 🚧 glyph + the existing BlockersSection chain — no extra
   badge / halo. This is the on-ramp for legacy rows + for agents
   still adopting the new field.

3. **Orthogonal to `kind`.** A kind=decision row USUALLY has
   blocker=operator-decision (the operator is the decider), but the
   validator does NOT cross-imply. Legitimate non-paired combinations:
   - kind=decision + blocker=agent-wait — "agent X is the decider
     here" (e.g. "decide: which release window — wait on lead").
   - kind=decision + blocker=compute — "the model picks" (rare but
     legitimate; e.g. AB-test result decides).
   - kind=task + blocker=operator-decision — a task waiting on an
     operator decision that hasn't been promoted to its own kind=
     decision node yet (soft-onramp; the operator can promote it
     later by adding a new decision-node row + repointing depends_on).
   Keeping the enums independent means the validator is one rule per
   field, no cross-field implication, easy to reason about.

4. **"Awaiting operator" lens is STRICT.** The dashboard predicate is
   `kind == "decision" AND status == "blocked" AND blocker ==
   "operator-decision"` — exact conjunction, not implication. Lead a2a
   `554435df`: "do NOT dilute the lens with transitive dependents;
   re-cluttering it defeats the whole anti-flood point." Transitive
   dependents stay reachable via the existing BlockersSection
   drill-in (they appear when the operator clicks a decision card).

5. **Impact badge is the prioritization signal** (orthogonal to the
   lens — see ADR-0003 Notes). The lens answers "WHICH decisions need
   me"; the impact badge answers "WHICH to do first." Separation of
   concerns keeps each surface single-purpose.

## Consequences

**Positive:**

- Operator's pain ("what's waiting on me") gets a STRUCTURED query
  surface, not a chat-scan. The lens chip is the at-a-glance count;
  the LOUD halo is the at-a-glance highlight; the impact badge is
  the at-a-glance priority signal.
- Closed enum + fail-loud means typos can't dilute the lens. A
  `blocker: oprator-decision` typo (4 chars dropped) lands at YAML-
  write time, not at "the chip count looks wrong, why?" time.
- Extending the enum (e.g. `"upstream-vendor"` for third-party API
  outages) is a one-line tuple edit.
- Fits the convergent quality-hygiene arc (`proj-scitex-cards-quality-
  hygiene` task): the `blocker` Literal validator is a third seed
  (alongside ADR-0002 `kind` and the future ref-integrity + cycle-
  detection passes) that all roll up into the typed `Task` dataclass
  as the single schema source.

**Negative:**

- One more required field for board-clicking-through-options
  affordances. Mitigated by the soft-degrade rule (#2 above); legacy
  rows or in-progress migrations just don't get the per-variant
  badge / halo / lens-count.
- The orthogonality means agents have to think about TWO enums when
  creating a decision-blocked row (kind=decision + blocker=...). The
  ADR-0003 README notes that the typical case is paired (kind=decision
  + blocker=operator-decision); the validator allows the off-cases
  but the canonical pattern is documented.

## Notes

- Surfaced 2026-06-06 by lead a2a `c839c59b` (initial blocker-field
  framing) → refined by `4691b114` (operator's "decisions-as-nodes"
  promotion of the operator-decision variant to its own kind) →
  finalized by `2bd37bd2` (orthogonality) and `554435df` (lens-strict
  + impact-badge separation).
- Operator pain quote pinned (TG 9522 / 9524).
- This ADR's enum + the convention "decisions usually have
  blocker=operator-decision" together let the dashboard answer the
  operator's "where am I the blocker" question with a single SQL-
  level predicate. Documented in the README's "Awaiting operator
  lens" section.
- Two LIVE seeds at PR merge: `decide-hub-prod-cutover-final-go` and
  `decide-clew-a-b-inline-dag`, both `kind=decision, status=blocked,
  blocker=operator-decision` — the lens immediately reads "👤
  awaiting you 2" on the operator's first reload.
- Cross-link forward: ADR-0005 (fleet-liveness panel) adds a per-
  agent "🚧 blocking operator (N)" badge that counts this ADR's
  predicate scoped to each agent's owned tasks — closes the loop on
  the "this agent has N decisions waiting on you" v1.1 follow-up.
