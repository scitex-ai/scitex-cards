# ADR-0009 — Task-driven feedback: four lifecycle careers connected over the hook bus (2026-06-24)

## Status

Accepted (operator design session, 2026-06-24). Builds on the Fleet Feedback
Architecture (operator, 2026-06-14) governing invariant:

> **Every A→B path MUST have a B→A feedback. No fire-and-forget — for
> dispatch, push, deploy, CI. Enforced in code (entry-point pub/sub +
> event sources), not prose.**

Implemented incrementally; the phases (P1–P6) are tracked as a card-DAG on
the board (epic `tcfb-epic-task-driven-feedback`). This ADR is the durable
design record; P1 (this PR) lands the card-side fields + the unblock event.

## Context

A card (= a task — one concept) should reflect "what is actually going on"
in the codebase and the fleet, and every action on it should produce
feedback to the people/agents who care. Today the board is largely a
passive log. We want it to be an **active feedback surface**.

Four things have a lifecycle ("career" — a state machine + a history
trail). The right model is to treat each as its own machine and connect
them **through the card as the hub**, by stable join keys, over the
existing `scitex_cards.hooks` entry-point bus — so each package stays
standalone (no hard cross-imports):

```
CARD career         pending → in_progress → (blocked) → done   [+ deferred/failed/goal]   ← HUB
VersionControl      git: branched→committed→pushed   github: pr_opened→checks→merged
Agent               defined → started → running ⇄ idle → stopped/failed
Dependency DAG      card ⇄ card  (depends_on / blocks edges)
```

Join keys (the card is self-describing):

```
card.assignee  = <agent>    → card ⇄ Agent career
card.branch    = <branch>   → card ⇄ VersionControl (git)
card.pr_url    = <pr>       → card ⇄ VersionControl (github)
agent id       = host@name  → the dedup/join key between card membership and sac runtime
```

What already exists (verified 2026-06-24 by a 4-track code investigation):

* **The bus** — `scitex_cards.hooks` entry-point dispatch; events `push`
  / `done` / `card-message`; built-in handlers (`_handle_push` →
  comment, `_handle_done` → `status:done` + `pr_url`, both idempotent);
  three surfaces (HTTP `/hooks/*`, CLI `scitex-cards hook`, in-process
  `dispatch_event`).
* **Ports & adapters** — `NotificationPort`, `IdentityACLPort`
  (default `OpenACL`), `TaskSyncPort` — the standalone-with-ports shape.
* **Dependency graph** — `depends_on` / `blocks` edges + `set_edge`;
  `runnable_tasks()` checks deps **passively** (no event when a dep
  finishes).
* **Agent delivery** (sac) — `POST /agents/<name>/message:send` →
  `Broker.publish` → SSE fan-out; the card layer's `deliver()` already resolves an
  agent's turn-URL via the sac `/agents` registry.

## Decision

### Roles (creator ≠ owner; peer-to-peer, no central lead)

```
creator       : anyone (human or agent); provenance
assignee      : the ONE responsible agent — peer-to-peer correspondent
collaborators : others involved (humans included)
subscribers   : notify list; default = creator + collaborators; always unsubscribable
```

`collaborators` and `subscribers` become **persistent fields** (today
collaborators are recomputed from comment authors at event-time;
subscribers don't exist). Notifications fan to **subscribers**.

### Connection = record vs drive (conservative)

Cross-career events are one of two kinds — this table is the core contract:

| Event | Kind | Effect on card |
| :-- | :-- | :-- |
| `merge` (GitHub PR merged) | **DRIVE** | `status → done` (already built-in) |
| commit / push | record | append ROUTE comment |
| PR opened / checks | record | append ROUTE comment |
| agent started / stopped | record | append ROUTE comment |
| blocker-card → done | **DRIVE** | dependents → unblocked + **notify** |

Start conservative: **only `merge → done` and `blocker-done → unblock`
drive**; everything else is record-only + notify. Widen later as each
drive earns trust.

### Git ↔ card linkage — SOFT, by branch name

The branch carries the card id (`<type>/<card-id>-<slug>`), matching the
already-enforced topic-branch-in-a-worktree workflow. The link is a
byproduct of *starting work* (creating the branch), recorded both ways
(branch name → card via the hook; `card.branch` → branch as the card's
SSOT). **Soft**: link when an id is present; ad-hoc branches stay
unlinked (no error). Unlinkable git ops record nothing — not every commit
belongs to a card.

### Agent career — standalone, SSOT-per-concern + a port

No shared agent registry, no hard dependency:

* **scitex-agent-container** is SSOT for agent **runtime** (exists /
  running / stopped / liveness) — it spawns them.
* **scitex-cards** is SSOT for board **membership** (who may be
  assignee/collaborator/subscriber — including humans).
* They join on the canonical agent id **`host@name`** and connect via a
  **port** (entry-point provider): sac exposes an agent-directory; the card layer
  enriches its board when a provider exists and works standalone
  otherwise; dedup by `host@name`.

### Active unblock

When a card flips to `done`, find the cards that depend on it whose
dependencies are now ALL satisfied (the existing `runnable_tasks` logic),
and emit an `unblock` event naming the newly-unblocked card ids. A
consumer (sac) notifies each unblocked card's assignee + subscribers —
*"your task T is now unblocked."*

## Consequences

* Two new persistent fields (`collaborators`, `subscribers`) + a new
  `unblock` event kind. Backward compatible (absent → empty / no-op).
* The board becomes an active feedback surface without coupling packages:
  every cross-career event rides the existing bus; consumers are
  entry-point plugins, swappable, standalone-safe.
* Conservative drive avoids surprise auto-transitions (only merge→done,
  blocker-done→unblock); everything else is visible history + a ping.

## Implementation phases (card-DAG `tcfb-*`)

```
P1 card roles (collaborators + subscribers) + active-unblock event   ← this ADR's PR
P2 sac consumes card-message/unblock → notify subscribers   (needs P1)
P3 git→card: branch→card-id parser + post-commit/pre-push hook
P4 github→card: sac CI poller posts `done` on merge   (needs P3 parser)
P5 sac agent-directory port + host@name identity (join key)
P6 ACL gating (fleet adapter + per-card acl)   (needs P5)
```
