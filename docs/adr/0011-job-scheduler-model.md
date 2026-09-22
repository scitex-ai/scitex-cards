# ADR-0011 — scitex-cards becomes the fleet's job scheduler: a total order, consumed from the head, that cannot stop

**Status:** PROPOSED (operator-directed 2026-07-17; interface open for sac's
refutation; implementation on the 0.17 line)
**Owner:** scitex-cards
**Card:** `scitex-cards-slurm-model-queued-replaces-deferred-20260717`

## Context

The operator's five statements (2026-07-17, translated; the Japanese
originals live on the board card, which is the internal record):

1. "`deferred` is wrong — it should be *queued* with a priority, like
   SLURM. I don't understand what 'stopping' even means here; the
   mechanism that ALLOWS stopping is what must be fixed."
2. "Yes — make it the agent-fleet version of a job management system."
3. "We must build a mechanism in which *waiting* is impossible."
4. "P0/P1 mean nothing — isn't the problem that priority isn't a number?
   It should be 'what position you'll do it at': pieces knocked out one
   at a time from the bottom like a *daruma-otoshi* stack while new ones
   pile on top, ordered by priority. (Urgency × importance, the classic
   two axes — visualizing that together with the user would be good;
   maybe a new cards view.) Above all: I see no reason a card can stop or
   be stopped. cards must nudge loudly and constantly; agents are not
   permitted to stop; close the loopholes."
5. "If there are N cards, there are N ranks."

And two same-day case studies that motivated them, both measured:

- **The purchased silence.** An agent carded a fresh P1, set it `deferred`,
  and stopped. `deferred` is nudged at 24 h (vs 2 h for `in_progress`) and
  asks for no reason — a status choice bought a day of silence on a P1.
- **The invisible delivery gap.** A fleet-critical change sat complete,
  committed, and UNPUSHED for 12+ hours (no PR, no state, no sweep sees it),
  while the party waiting on it was asked for a delivery date. Every signal
  available said "in progress"; the truth was "done and undelivered".

Today's board is a noticeboard: it SHOWS work. Nothing owns
should-be-done → is-being-done. Both failures are that missing owner.

## Decision

### 1. The axiom: N cards ⇒ N ranks

The open board is ONE totally ordered queue per partition. No ties, no
buckets, no P-labels. Every open card has exactly one integer **rank**;
rank 1 is next. `priority` as a free-typed label is DELETED with the same
knife as `deferred` (it survives read-only during migration).

**Rank is computed, never asserted.** Each card carries two judgments a
human (or the submitting agent, subject to operator override) CAN honestly
give — **urgency** and **importance** (1–5 each). Insertion score =
f(urgency, importance) with deterministic tie-breaking by submission time;
**aging** then improves rank monotonically with wait time, so a low score
means LATER, never NEVER. An agent cannot lie about its position because it
does not set it. Fifteen "P1"s among 144 cards was a mood; rank 7 of 144 is
a checkable claim.

### 2. The states — stopping and waiting are unrepresentable

```
queued   — in the line, WILL run (the axiom guarantees it). Not stopped:
           the scheduler has not reached it yet.
running  — dispatched to an agent seat; walltime (TTL) attached; fast clock.
blocked  — waiting on a NAMED edge: blocked_on(WHO, WHAT, DEADLINE) — all
           three MANDATORY, validator-enforced. The escalation clock fires
           at the OWED party, not the waiter.
done / cancelled / failed — terminal. Cancelling — deciding NOT to do —
           is a legitimate exit (operator, 2026-07-17: deciding not to
           do something and cancelling is fine) BUT its reason and the
           transition record are MANDATORY ("no loopholes may be
           created").
goal     — the standing-umbrella card (today's justified `parked` use);
           carries a mandatory reason; its runnable work lives in children.
```

`deferred` is DELETED: it is the state that means "stopped, no reason
given", and that must be inexpressible. `parked` is SUBSUMED: a reasoned
strategic hold is either a `goal` or a `blocked` edge on a named external
condition — both keep the mandatory-reason property that made `parked`
legitimate; nothing keeps the silence.

**Write-time enforcement, not nudges** (the operator's standing
doctrine: uniformity, never-forget, enforce by hooks): the
validator REJECTS a reason-free blocked, a reason-free cancel, a tie in
rank, an anonymous wait, a running card with no seat, a queued card with
no rank. A rule someone must remember is one they will forget — both case
studies broke written rules their authors knew.

**Every transition is a record.** State changes and rank changes append an
immutable audit entry — who, when, from→to, why — to the card. Rank
CURATION is explicitly allowed and expected (the operator reorders at
will); what is forbidden is an untraced change. The audit trail is what
makes "no loopholes" checkable after the fact rather than asserted.

### 3. The scheduler dispatches; agents do not self-select

The head of the queue is DEALT to an eligible seat (partition = agent
group; the ACL mesh exists). An agent's next job arrives; it does not
browse 144 cards for the interesting one. Silence after dispatch escalates
loudly and automatically — "constantly and loudly" is the design, not a
failure mode. Every should-be→is gap gets an observer: dispatched-but-not-
started, running-past-walltime, branch-without-PR, PR-without-review,
merged-without-release. The done-but-undelivered class dies by
observation, the stopped class by construction.

### 4. The decoupling boundary (ADR-0010's hard rule, restated)

scitex-cards DEFINES a port; it never imports sac. Two surfaces:

- **NodeStatePort** (sac → cards): the live agent set, seats/capacity,
  partition membership. Pull (HTTP GET or CLI-JSON) on the scheduler's
  tick; sac's registry/liveness is the adapter behind it. NOT built on
  today's liveness — sac has named its verdict defects as their
  prerequisite to clear.
- **DispatchPort** (cards → agent): "card X is yours, walltime W" delivered
  over the EXISTING self-contained push wire (`_push.deliver` /v1/turn) —
  no new coupling; sac's a2a remains a parallel accelerator, never a
  dependency.

With no adapter present the board degrades to today's pull mode (null
scheduler) and stays fully usable — the S7 CI gate keeps this honest.

### 5. A card is a promise, not an outcome — and the API must say so

Operator diagnosis (2026-07-17, translated): "isn't the problem that the
exit code becomes 0 when you write a card?" Writing a card currently
returns the same green as finishing something, so the caller's loop —
and the caller's sense of completion — reads card-written as done. The
write's exit code stays honest (the write DID succeed; lying about that
would mirror the same defect), but the RESPONSE becomes a dispatch
statement, not a receipt: "queued at rank N of M; owner X; this card is
now OWED, not done." The enforcement then lives where the loophole
actually lives:

### 6. The stop hook — going idle while owing work is refused

Operator mechanism (same message, translated): "scitex-cards provides a
hook; it is caught by the agent's stop hook; stopping is not permitted."
cards EXPOSES the check; the agent harness wires it (sac's half):

```
scitex-cards agent may-stop --agent <id> --json
  -> {"may_stop": bool, "reason": str,
      "blocking_cards": [{id, state, since, why_yours}]}
```

THE ONLY LEGAL STOP: **zero runnable cards exist for this agent** —
nothing running, nothing dispatched, and nothing in the queue this
agent's partition makes it eligible to take. An earlier draft allowed
"all my cards are blocked" as a stop; the operator refuted it on sight
(translated): "when you're waiting on someone, you must advance OTHER
independent cards." One blocked card is not a blocked agent — a SLURM
job waiting on a dependency does not idle the NODE; the node runs
another job. So converting running → blocked(WHO, WHAT, DEADLINE) is
still the legitimate move for stuck work (audited; the escalation clock
transfers to the owed party) — but it leads to the NEXT card, not to
idleness. The may-stop check therefore consults the queue, and a refusal
may carry a dispatch: "you may not stop; card Y is yours." ILLEGAL:
going idle while runnable work exists — the one state to kill. A refusal
always names the card and its age: an unexplained refusal is
unfalsifiable and teaches agents to fight the hook. Decoupling holds: it
is a QUESTION cards answers, never a rule cards enforces remotely — no
hook installed means today's behavior. A nudge is a message an agent may
decline; a stop-hook refusal needs no cooperation. Only the second kind
works, by the evidence of this very night.

### 7. The adversarial catalog — every loophole gets a closer

Operator question (2026-07-17, translated): "Are there other holes? For
instance deliberately sleeping, or running long meaningless computation?"
Yes. The governing principle for all of them: **only artifacts are
evidence of progress — never elapsed time.** Each evasion is closed by
exactly one of two instruments: the write-time validator (makes the state
inexpressible) or an observer (makes the gap visible and escalates).

| Evasion | Closer |
|---|---|
| Fake work: sleep loops, meaningless computation | walltime auto-fails the card; `running` requires artifact-bearing heartbeats (commit / PR event / delta-carrying comment) — clock ticks count for nothing |
| Sleeping before starting | dispatch must be ACKed by a start event within a bound; silence escalates (observer) |
| Fake `blocked`: bogus or circular edges | edge targets must be real entities (card/PR/live agent); cycle detection at write; the OWED party can REJECT the edge, returning the card to `running` |
| Cancel as escape | reason mandatory (validator); per-agent cancel rate is a visible metric the operator reviews |
| Card-splitting as procrastination | children inherit the parent's rank neighborhood — splitting never resets work to the tail; split events audited |
| Axis gaming (inflated urgency/importance) | rank is computed with operator override; axis changes audited; aging rebalances honest low scores |
| Fake `done` (no outcome) | artifact-producing kinds require an outcome reference; the reconcile observers cross-check (the merged-PR reconciler is the existing template) |
| Shedding ownership to dead/fictional agents | assignee must resolve against the live registry (NodeStatePort); dispatch to a dead seat bounces back with escalation |

And the submission/consumption asymmetry, per the operator's refinement:
**submit returns a receipt ("queued at rank N of M — OWED, not done");
the satisfying green is returned only on verified consumption.** The
catalog is expected to grow; every new hole gets filed under one of the
two instruments, never under "we'll remember".

### 8. The two-axis view (urgency × importance) — now a build order

Directed 2026-07-17 (translated): "build a view that displays cards on
the two axes of importance and urgency; humans update by DRAGGING, the
priority is recomputed and shared with the agents." Quadrants, operator-
enumerated: **I** urgent∧important, **II** important∧¬urgent, **III**
urgent∧¬important, **IV** neither. Dragging a card re-scores its axes,
re-ranks the queue, and the new order is immediately what the scheduler
deals from — the matrix is the HUMAN instrument, the queue the
machine's; same data, always synchronized.

**Maximize quadrants I and II** (operator directive): the scoring
function weights IMPORTANCE above URGENCY — f(u,i) = w_i·i + w_u·u with
w_i > w_u — so II outranks III even though III shouts louder; aging lifts
II steadily (important work must not wait forever behind loud trivia);
and the board reports quadrant occupancy over time, making "are we
living in II?" a reviewable metric instead of a hope.

### 9. Two kinds of nudger — mechanical and agentic

The operator's observation (translated): "this risks staying
request-based… we need DECISIVE nudges (hooks, periodic notifications)
AND an agentic nudger." Adopted as architecture:

- **The mechanical rail** (decisive): validators, the may-stop hook,
  walltime, dispatch ACK bounds, the observers — refusals and
  escalations that need no one's cooperation. Already specified above.
- **The agentic nudger** (a ROLE, not a cron): an agent seat whose queue
  IS the escalation stream — it reads observer output (stalled
  dispatches, aging blocked edges, silent owed parties), chases the
  responsible party conversationally, judges excuses, and files what it
  learns back onto the cards. Mechanics catch what is formal; the nudger
  handles what needs judgment. It runs on the same scheduler as everyone
  else — its work is cards, so it cannot itself stop.

### 10. The cycle position — `done` means LIVE, not merged

Operator (2026-07-17, translated): "a red PR must be fixed; you must go
check that CI is even running; a green PR must then be released,
deployed, made live, made live on the other agents too — right? The
cycle is already defined. Isn't a function needed that always records
and tracks WHERE in that cycle a card currently is?"

The diagnosis is the `add_task`-returns-0 defect one layer out: MERGING
ALSO RETURNS 0. The last event an owner observes becomes the event they
treat as completion — and four features in one night were merged and
INERT (never ran / never scheduled / never installed / merged to a
branch where the trigger cannot fire), invisible because the gap between
"merged" and "live" has no name in the model.

Therefore a card carries a **stage** alongside its state — its position
in the already-defined delivery cycle:

```
building → pr-open → ci-green → merged → released → deployed →
live-on-host → live-on-fleet
```

Three rules, all validator/observer-enforced:

1. **`done` requires the terminal stage** for cards whose kind walks the
   chain. "I merged it" cannot close a card whose definition of done is
   "the fleet runs it".
2. **Stages are set by OBSERVATION, never by the owner's assertion.**
   A self-declared stage is a `deferred` in disguise. The emitters are
   the existing content-probe family: PyPI 200s, manifest inspection,
   symbol probes, run history — each transition event arrives through
   the hooks entry-point group (`scitex_cards.hooks`), which external
   observers (sac's probes, CI) already know how to produce into. cards
   defines the event schema; it never imports the emitters.
3. **Stalls between stages are observer territory**: merged-without-
   release, released-without-deploy, deployed-not-live — each gap gets
   the same clock-and-escalation treatment as every other named wait
   (§3, §7). The four inert features would each have read
   "merged, not live — for N days" on the board instead of nothing.

### 11. The remote board rail — one board, every host

Operator (2026-07-17, translated): "like scitex-agent-container, the
channels must treat the HOST as the single source of truth and apply to
agents on EVERY host." Consequence for cards: an agent on a remote host
(spartan, NAS) connects its cards channel OVER THE NETWORK to the one
hub board — never to a local island file. The split-brain class (a
remote agent clobbering a local store; the spec-level contradiction
found today) becomes unrepresentable: there is no local store in the
path at all. This finally answers the store RFC's multi-host question:
the board/API is the sync boundary — file-level sync is dead. Channel
CONFIGURATION is host-SSOT on sac's side (their default-channels
mechanism, mirroring their default-binds pattern); the network CONTRACT
the channel connects to is this package's surface, specified with the
other ports. Sequencing: reachability first, default flip second — a
host whose agents cannot yet reach the hub rail keeps a null default.

## Migration

~150 live `deferred` cards: a one-shot triage tool walks them oldest-last —
each becomes queued (gets axes → rank), blocked (gets a named edge), goal
(gets a reason), or cancelled (gets a reason). The tool refuses to finish
with any card unclassified; the yaml→DB migration (S5) carries the new
fields; schema v5 adds rank/axes/edge columns. P-labels map to initial
axes (P0→(5,5), P1→(4,4), …) as SEEDS for human correction, not truths.

## Consequences

- The WIP gate, digests, stale-active sweeps and backlog triage all
  simplify or die: most existed to compensate for self-selection and
  silent stopping.
- Nudge cadence asymmetry (the 24h/2h loophole) becomes irrelevant:
  queued cards are the scheduler's problem, not the owner's conscience.
- sac builds the NodeStatePort adapter + clears liveness truthfulness
  (theirs, named); cards builds everything else.
- Staged on 0.17+: (a) schema + validator + states behind a flag,
  (b) rank computation + migration tool, (c) dispatcher + ports,
  (d) matrix view. The S6 store cutover ships FIRST or in parallel —
  never entangled in one release.
