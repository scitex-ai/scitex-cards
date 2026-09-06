# ADR-0006: Full board UI spec — filter bar + per-project columns + BLOCKING YOU panel + GUI→code wiring

## Status

Accepted (2026-06-07)

## Context

The board's UX has been evolving in tight operator-feedback loops over
2026-06-06 / 2026-06-07. By 2026-06-07 a coherent **full spec** has
emerged that the operator is actively co-designing (TG 9522 / 9524 /
9667 / 9671 + lead a2a `8d066f54`). The pieces — filter axes, project
columns, blocker views, color-by-status+liveness, GUI-to-code wiring,
cross-host / cross-agent sharing, ACL, tags — have each been discussed
individually; this ADR is the consolidated **specification** so a
future maintainer (or a re-implementer in scitex-ui as the productize
step lands) has one place to read what the board MUST do.

Prior ADRs in scope:
- ADR-0001 — scitex-cards as the fleet's universal task layer.
- ADR-0002 — `kind` as a closed Literal enum, fail-loud.
- ADR-0003 — `kind: "decision"` for decision-nodes as first-class
  graph nodes.
- ADR-0004 — `blocker` as a closed Literal enum orthogonal to `kind`.
- ADR-0005 — fleet-liveness panel + SSH-fanout watcher.

This ADR layers on top of those, focused on what the OPERATOR SEES
and HOW THE GUI WIRES BACK INTO CODE.

## STRUCTURAL BACKBONE — Core vs Extension Ports vs Fleet Adapters (operator TG 9678, lead a2a `fae53b8e`)

Operator's defining architectural principle, verbatim (TG 9678):

> "scitex-cards はそれだけで独立したパッケージであるべきで、他を知らないが、extension port は持っている"
> (scitex-cards MUST be a standalone package that knows nothing about
> the fleet/sac/scitex-specifics, but exposes extension ports through
> which fleet-specific behaviour plugs in.)

This is the clean-architecture / dependency-inversion principle and
it shapes EVERYTHING in this ADR. Read every section below through
this lens; the next section names every port the design needs.

### Three layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  FLEET ADAPTERS — implements ports against sac / SSH / a2a / git    │
│   • git-backed TaskSyncAdapter                                       │
│   • SacChannelNotificationAdapter (rides the a2a/channel bus)        │
│   • SacAgentsLivenessAdapter (SSH-fanout of `sac agents list`)       │
│   • SacFleetGroupsACLAdapter (sac fleet groups model — task #2)      │
│  → LIVES OUTSIDE `scitex_cards` (separate package, e.g. `scitex-      │
│    cards-fleet`; or in `scitex-agent-container` / a similar glue pkg) │
└──────────────────────────────────────────────────────────────────────┘
                                ↑ implements
┌──────────────────────────────────────────────────────────────────────┐
│  EXTENSION PORTS — interfaces the core defines                       │
│   • TaskSyncPort      (durable storage / cross-host sync)            │
│   • NotificationPort  (publish + subscribe to task changes)          │
│   • LivenessPort      (fleet agent status for the liveness panel)    │
│   • IdentityACLPort   (who can read / write which task)              │
└──────────────────────────────────────────────────────────────────────┘
                                ↑ used-by
┌──────────────────────────────────────────────────────────────────────┐
│  CORE — `scitex_cards` package, ZERO knowledge of fleet/sac/etc.      │
│   • Task dataclass, store, CRUD                                      │
│   • Board UI rendering (filter bar / columns / cards / drawer / lens)│
│   • Filtering, tags, the BLOCKING YOU predicate                      │
│   • The dep-graph render + drill-down                                │
│   • Resolves IdentityACLPort? before any mutation                    │
│   • Calls NotificationPort.publish on every mutation                 │
│   • Reads LivenessPort.list_agents for the fleet-liveness panel      │
│   • Delegates "where does the durable data live + how does it sync"  │
│     entirely to TaskSyncPort                                         │
└──────────────────────────────────────────────────────────────────────┘
```

The core ships with **default no-op / single-host implementations** of
each port (in-memory pub/sub, file-backed sync, identity ACL that
allows everything) so the package is **independently usable** without
the fleet glue — a single-user installing `pip install scitex-cards`
gets a working local task board. Drop in the fleet adapters and the
same code becomes the fleet's shared SSoT.

### The four extension ports (interface contracts)

Each port is a `typing.Protocol` (duck-typed) in
`scitex_cards._ports` — implementations live in adapter packages and
are registered via constructor injection on `Board()` / `Store()`:

```python
# scitex_cards/_ports.py — CORE; zero fleet knowledge.
from typing import Protocol, Callable
from ._model import Task

class TaskSyncPort(Protocol):
    """Durable sync of the task store. The core writes through this
    port; the adapter decides what 'durable' means.

    Default impl (`_adapters.LocalFileSync`) = atomic ruamel write to
    `~/.scitex/cards/tasks.yaml`. The fleet swaps in a git-backed
    variant: write → git commit → push to GitHub. Cross-host pull is
    a side-loop in the adapter, never in the core.
    """
    def load(self) -> list[Task]: ...
    def save(self, tasks: list[Task]) -> None: ...
    def reload_if_changed(self) -> bool: ...   # returns True if mtime changed

class NotificationPort(Protocol):
    """Publish + subscribe to task changes. The core publishes on
    every mutation; the adapter routes it (in-memory, sac/a2a bus,
    Kafka, anything).

    Default impl (`_adapters.InProcessPubSub`) = a simple in-process
    callback registry. The fleet swaps in a SacChannelAdapter that
    publishes on `scitex-cards:task:<id>` and uses the
    wake-generalize wire to wake idle agent subscribers.
    """
    def publish(self, channel: str, payload: dict) -> None: ...
    def subscribe(self, channel: str, handler: Callable[[dict], None]) -> None: ...

class LivenessPort(Protocol):
    """Surface 'are these agents alive?' state for the fleet-liveness
    panel. The core renders the colored dots / Fleet tab from this
    output; HOW it's gathered is the adapter's problem.

    Default impl (`_adapters.NullLiveness`) returns []. The fleet
    swaps in SacAgentsLivenessAdapter that runs SSH-fanout (ADR-0005,
    ADR-0002 of fleet-liveness task) against peer hosts and emits
    the same shape.
    """
    def list_agents(self) -> list[dict]: ...
    # Each dict has: name, status, heartbeat, current_task, last_activity, …

class IdentityACLPort(Protocol):
    """Answer 'can ACTOR perform ACTION on TASK?' The core consults
    this before any write; the adapter decides authority.

    Default impl (`_adapters.OpenACL`) = everyone-can-do-everything.
    The fleet swaps in SacFleetGroupsACLAdapter that consults
    `sac fleet groups list --json` and the per-task `acl: {read, write}`
    field (when that lands per ADR-0006's ACL section).
    """
    def can_read(self, actor: str, task: Task) -> bool: ...
    def can_write(self, actor: str, task: Task, field: str) -> bool: ...
```

### Wiring (dependency injection at the edges)

The core's entrypoints (`scitex_cards.create_board()`, the Django app
factory) take optional port arguments. Without them, the core wires
the defaults. The fleet's deployment script wires the real adapters:

```python
# Single-user / standalone — uses defaults; works with no fleet code.
from scitex_cards import create_board
app = create_board()    # uses LocalFileSync + InProcessPubSub + NullLiveness + OpenACL

# Fleet deployment — wires the real adapters from a separate package.
from scitex_cards import create_board
from scitex_cards_fleet import (
    GitTaskSyncAdapter,
    SacChannelNotificationAdapter,
    SacAgentsLivenessAdapter,
    SacFleetGroupsACLAdapter,
)
app = create_board(
    sync=GitTaskSyncAdapter(repo="ywatanabe1989/scitex-tasks", branch="main"),
    notify=SacChannelNotificationAdapter(channel_prefix="scitex-cards:task:"),
    liveness=SacAgentsLivenessAdapter(host_list="~/.config/sac/peers.yaml"),
    acl=SacFleetGroupsACLAdapter(group_source="sac fleet groups list --json"),
)
```

### Consequences of the standalone+ports principle

Positive:
- `scitex-cards` is independently usable (operator's "それだけで独立")
  — a researcher who never heard of sac can `pip install scitex-cards`
  and get a working local task board. Standalone-package-value
  preserved.
- The fleet glue (sac / SSH-fanout / a2a / git-sync) lives OUTSIDE
  scitex-cards, in a separate package. scitex-cards's CI doesn't need
  sac installed; sac's CI doesn't pull in scitex-cards's whole UI tree.
  Ecosystem standalone-vs-module rule satisfied.
- The productize-step (HANDOFF.md LONG-ARC EXIT — ship the board as
  a scitex.ai / scitex-hub built-in) becomes trivial: ship the core,
  let external users register their own adapter if they want
  Slack-notify / GitHub-Issues-sync / etc. The fleet's adapters are
  one of MANY possible implementations.
- Testing: the core is tested with the in-memory/null adapters, no
  fleet infra needed. Adapters are tested separately against their
  real backends.

Negative:
- Slightly more upfront work — defining the Protocol interfaces and
  ensuring the core never imports fleet-only modules. Mitigated by
  starting with the defaults (LocalFileSync / InProcessPubSub /
  NullLiveness / OpenACL) and only formalizing each Protocol when
  the second implementation lands (YAGNI for ports themselves).
- The fleet's deployment grows a thin wiring script (the
  `create_board(...)` call above). Acceptable: deployment glue
  belongs at the deployment layer.

Notes:
- This principle SUPERSEDES any prior assumption that scitex-cards
  imports sac / a2a / SSH directly. If you read an earlier section of
  this ADR (or skill 30) that says "the board does SSH-fanout to peer
  hosts," translate to "the core's LivenessPort is asked; the
  adapter the operator's host installs does SSH-fanout." Same
  behaviour, cleaner dependency direction.
- Every other section of this ADR (regions, cards, GUI→code wiring,
  cross-host sync, citation, notification) flows through the ports.
  Read those sections noting which port the behaviour delegates
  through:
    - Region 4 Resolve → notify agent ⇒ NotificationPort.publish
    - Region 3 BLOCKING YOU panel data ⇒ TaskSyncPort.load
    - Cross-host sync ⇒ TaskSyncPort (git-backed adapter)
    - Subscribers / wake-generalize ⇒ NotificationPort
    - Liveness colors / Fleet tab ⇒ LivenessPort
    - Per-row read/write gating ⇒ IdentityACLPort

## Decision

The scitex-cards board renders ONE PAGE with three regions and a
strict GUI-to-code wiring contract.

### Region 1 — Filter bar (top)

A single horizontal strip carrying every filter axis the operator
named, plus the blocking-me toggle:

| Filter axis | Source                           | Effect                                  |
| ----------- | -------------------------------- | --------------------------------------- |
| project     | Task.project                     | column visibility AND in-column filter  |
| host        | Task.host                        | filter (any host)                       |
| status      | Task.status (working/waiting/done/blocked) | filter (any one status)       |
| tag         | Task.tags (list)                 | filter by tag membership                |
| blocker     | Task.blocker (enum + "no blocker") | filter by blocker variant              |
| agent       | Task.agent                       | filter by owning agent                  |
| 🚧 blocking me toggle | computed predicate     | shortcut for status=blocked AND blocker=operator-decision |

All filters AND-compose. Search box (free-text over task / goal /
title / id) is a v1.1 candidate; not in v1.

### Region 2 — Center: 6 project columns

The 6 active streams (Clew paper / NeuroVista paper / SciTeX-Hub /
Ripple-WM paper / scitex-cards / scitex-dogfooding — definitive list
operator TG 9666 + 9671) render as fixed columns. Each column header
shows the project name + a task-count pill. Each task = ONE CARD
inside its project's column.

Card layout (from operator's co-designed Task dataclass, ADR-0007 the
quality-hygiene PR; field-by-field render contract):

```
┌─────────────────────────────────────┐
│ ▌ STATUS-LABEL      last 7s         │ ← color rail (left, 4px) +
│ ▌ 🎯 goal text (italic, muted)      │   status uppercase chip +
│ ▌ TASK TEXT IN BIG LINE             │   last_activity recency
│ ▌ host-pill         [PR-link]       │   🎯 prefix on goal
│ ▌ [tag] [tag] [tag]                 │   tags color-coded
│ ▌ 🚧 blocker: <variant>             │   blocker pill (gold on operator-decision)
└─────────────────────────────────────┘
```

Color logic (left rail + status chip):
- `status: working` → green (drives green halo on liveness in the
  fleet-liveness panel; combined with ADR-0003's recency: stale →
  amber → red).
- `status: waiting` → amber.
- `status: blocked` → red, **pulsing** (1.8s ease-in-out animation).
- `status: done` → grey.

Card click → opens the DETAIL DRAWER (Region 4 below).

### Region 3 — Right: BLOCKING YOU panel

A fixed-width (340px on wide screens, collapses on narrow) right-side
panel filtering to the STRICT predicate `status=="blocked" AND
blocker=="operator-decision"`. Per ADR-0004: do NOT dilute the lens
with transitive dependents.

Each row renders:
- the task text (big),
- project · host · created_at meta line (muted, monospaced),
- ⚖️ unblocks N impact badge (forward-closure count from ADR-0003),
- ⏱ wait-time since the task entered status=blocked (gold accent),
- ACTIONS: [Resolve → notify <agent>] (green primary) + [Open] (secondary).

Empty state: `Nothing waiting on you. ✓` (italic, muted) — the
operator's win condition.

### Region 4 — Card-click detail drawer (modal-ish)

Centered modal overlay (backdrop dimmed). Renders the full Task
dataclass field-by-field as a KV table:

- title (top, large)
- meta line: `<project> · <host> · agent <agent>` (monospaced, muted)
- KV rows: `status`, `blocker`, `last_activity`, `created_at`, `goal`,
  `tags`, `pr_url`, `issue_url`
- Actions: `[Close]` + `[Resolve → notify agent]`

Editing (status, assignee, tags) is in v1.1. v1 displays + Resolve
only — the operator-decision flow is the load-bearing path.

### GUI→CODE wiring (operator's explicit ask, TG 9667 + 9671)

The board is NOT a static dashboard. Operator actions flow into code:

| GUI action                          | Write path                                                                |
| ----------------------------------- | ------------------------------------------------------------------------- |
| Card click                          | (read) load Task by id from the store                                     |
| Edit status / assignee in drawer    | POST `/task/<id>` → `_store.update_task()` → ruamel writes `tasks.yaml`   |
| **"Resolve" on a BLOCKING YOU row** | (1) `status: done`, `blocker: null` in `tasks.yaml`. (2) FIRE an `a2a notify` to `Task.agent` carrying `{task_id, resolution: "operator-resolved", ts}`. (3) Optionally append a `comments[]` entry capturing the resolution. The owning agent picks the a2a up on its next turn and unblocks any dependent work. |
| Filter change                       | client-side only — re-renders from in-memory state                        |
| Tag add/remove on a card            | (v1.1) POST `/task/<id>/tags` → store write                               |

The "Resolve → notify agent" path is the **load-bearing GUI→code
loop** — it closes the "operator is the blocker" cycle bidirectionally
in one click. The board becomes the operator's primary mechanism for
unblocking agents, not just an observation surface.

Backend wire (the data flow direction):
- READ: `sac-status-writer` sidecar (SSH-fanout per ADR-0002) +
  `tasks.yaml` writes from agents → board reads via `/graph`,
  `/agents`, `/rev`, `/agents-rev`. Auto-refresh 5s.
- WRITE: operator GUI POSTs → Django handlers → `_store.py` ruamel
  writes → AutoRefresh delivers to every viewer within 5s. Agents
  receive a2a notifications synchronously on resolve-click.

### Cross-machine + cross-agent sharing (operator TG 9671)

The `tasks.yaml` lives at `~/.scitex/cards/tasks.yaml` on the
operator's host (current SSoT per HANDOFF.md). Cross-host
visibility is the north-star pillar #3 (cross-host sync via
GitHub) — until that lands, the operator's-host board is the
single canonical viewer + writer. The fleet-liveness panel
(ADR-0005) handles the cross-host READ-side (SSH-fanout to each
peer's `sac agents list --json`). The WRITE-side (operator edits
on his host writing to the agent's tasks) flows via the same
shared YAML once cross-host sync lands.

Cross-AGENT sharing is the default today: every agent + the
operator reads and writes the SAME `tasks.yaml`. No per-agent
silos. This is the HANDOFF.md SSoT DATA LAYOUT contract — the
write-here table specifies WHICH file each kind of change goes
to, but every agent sees every file.

### ACL — wire to the sac group model (task #2)

The sac fleet has a shared-`fleet` ACL group (task #2:
`e1-sac-fleet-acl`, queued). When that lands, scitex-cards grafts
onto it:

- READ ACL: every fleet-group member sees every task by default
  (the current state). Future per-task `acl: {read: [<groups>]}`
  field gates per-task visibility when needed.
- WRITE ACL: ditto with `acl: {write: [<groups>]}` for fields
  the operator wants to restrict (e.g. status mutation on a
  decision-node restricted to operator + lead).
- Validator extension: if `acl` is present, type-check the
  groups list against `sac fleet groups list --json`; fail-loud
  on unknown group names.

NOT in scope for v1 (no acl: field validators yet) — but the
schema reserves the field name + documents the future shape.

### Tags

`Task.tags: list[str]` — free-form string labels. The validator
type-checks (each entry non-empty string) but does NOT enforce a
closed enum (per the YAGNI rule in HANDOFF.md). Suggested
conventions baked into the prototype:

- priority: `P0` / `P1` / `P2` (color: gold)
- type: `paper` / `infra` / `release` (color: purple)
- stream-specific tags (free-form): `cohort-a`, `pac-line`,
  `cutover`, etc. (color: green)

Filter axis "tag" in the filter bar narrows to a single tag at
a time in v1; multi-tag conjunction is a v1.1 candidate.

## Consequences

**Positive:**

- The operator's "I want to SEE the fleet state" ask is structurally
  satisfied: one page, big-text, color-by-status, eye-magnet
  BLOCKING YOU panel.
- The Resolve-with-notify-agent path makes the board the primary
  GUI→code lever — clicking a button updates the YAML AND fires an
  a2a, so the operator's decision flows directly into agent action.
- The render contract (Task → card) is fully spelled out;
  re-implementing the board (e.g. as the productize-step's scitex-ui
  module) is a translation, not a redesign.
- ACL + tags + filtering each have a documented insertion point so
  v1.1 / v1.2 work doesn't need new ADRs — just new validators +
  UI surfaces hanging off this spec.
- The same dataclass feeds: validator (`_validate_tasks`), UI render
  (this ADR), Gitea field-map (HANDOFF.md), future README-frontmatter
  (HANDOFF.md). ONE source of schema truth.

**Negative:**

- The full UI is a significant build relative to today's drag-graph
  + table view. Mitigated by incrementalism: the v3 static
  prototype at `/tmp/scitex-cards-prototype/prototype-v3.html` (served
  on `:8052`) is the deliverable for visual review; the live wire-up
  lands across two PRs (quality-hygiene = Task dataclass + write
  endpoints; fleet-liveness = SSH-fanout + `/agents` endpoint).
- 6 fixed columns assume the fleet's stream-count is stable around
  6. If a 7th stream appears (e.g. a new paper line) the columns
  reflow; >10 streams the layout breaks. Acceptable: the fleet's
  stream count is operator-curated, slowly changing.
- The a2a-notify-on-resolve coupling makes the GUI flow load-bearing
  for agent unblocking. If sac's a2a transport is down, the YAML
  write still lands (the agent picks it up via AutoRefresh on its
  side); the a2a is the FAST path, the YAML is the durable path.

## Cross-host sync — GitHub-backed (durable) + SSH-fanout (live, ephemeral) split

Operator + lead a2a `3d7a20e7` extension: scitex-cards must sync task
state across hosts WITHOUT inventing a peer-rsync protocol (ecosystem
rule: "GitHub is the SSoT, no peer rsync"). But a LIVE board needs
faster propagation than a typical git-pull cycle. The split:

| Tier                          | Storage                                              | Sync mechanism                                                                | Latency      |
| ----------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------- | ------------ |
| project tier `<proj>/.scitex/cards/`  | per-project git repo (skill-30 convention)    | **git-backed via GitHub** — commits + push + pull on hosts                    | minutes      |
| global tier `~/.scitex/cards/` (tasks.yaml + tasks/<id>/) | host-local file                | **rebuilt by aggregator sidecar** from project-tier reads (SSH-fanout per ADR-0005) | 5s tick     |
| global tier `~/.scitex/cards/agents.json` | host-local file                            | **rebuilt by sac-status-writer sidecar** (SSH-fanout, ADR-0005)               | 5s tick     |

Design rule:
- **Durable** = git. The project-tier `.scitex/cards/` directory IS
  the SSoT. Each agent commits + pushes its own project-tier
  directory to its project's GitHub repo (or a dedicated
  `<project>-tasks` repo per the operator's preference). Other hosts
  pull. Standard GitHub-as-sync-substrate, no scitex-cards-specific
  transport.
- **Live** = SSH-fanout to the project tiers + aggregator-rebuild of
  the global. The operator's host runs the aggregator; it reads each
  project's `.scitex/cards/tasks.yaml` (via `sac host exec <peer>
  "cat <proj>/.scitex/cards/tasks.yaml"` or a thin sac fleet
  read-helper) every 5s and rebuilds the global. The global is
  EPHEMERAL — durable state lives in git per-project.

Failure mode handling:
- If GitHub is down: the live SSH-fanout aggregator still rebuilds
  the global from each project's local working copy. Operator still
  sees current state; durable sync resumes when GitHub returns.
- If SSH-fanout fails on a peer: the global flags that project's
  tier UNREACHABLE (same as the agent UNREACHABLE rule from ADR-0005)
  — last-known data + as_of stamp surfaces; not silently omitted.
- If both: the operator sees stale data with explicit staleness
  markers on each row.

The split mirrors the agents.json pattern from ADR-0005: durable
truth lives in the agent's own SSoT location (git for tasks, sac
registry for agents); a sidecar aggregates into the global tier for
the board to read; the board never directly polls peers.

## Task referencing / citation — stable IDs + URL scheme

Operator's ask (TG 9675/9676): "Resolve task ABC" must point to ONE
unambiguous task across the fleet. ID scheme + URL:

### ID scheme

**Project-prefixed string ids** (matches the existing skill-30
convention + the dataclass spec). Format:

```
<project>/<local-id>
```

- `<project>` = the project's directory basename (matches `Task.project`).
- `<local-id>` = the agent's chosen string, unique within the project.
- Slash separator is path-safe AND URL-safe.

Examples:
```
paper-scitex-clew/cohort-a-rerun
scitex-hub/decide-prod-cutover-final-go
scitex-cards/proj-scitex-cards-fleet-liveness
```

Validator enforces:
- Both segments non-empty.
- `<project>` matches `Task.project` exactly (so the id is self-describing).
- Cross-project uniqueness: aggregator dedupes by id; if two projects
  emit the same id (shouldn't happen because the project prefix
  guards it), the validator raises with both rows' provenance.

Backward-compat: existing tasks today carry single-segment ids
(`proj-scitex-cards-compute-state-deps`). The validator accepts both
during a deprecation window; the aggregator stamps
`_log_meta.canonical_id = "<project>/<id>"` on read so the URL scheme
works on legacy rows.

### URL scheme

The board's Django serves a per-task page:

```
http://<board-host>:8051/task/<project>/<local-id>
```

Maps to the existing NodeDetailPanel drawer rendered as a standalone
page (same React component, different mount). The page is shareable:
operator pastes `localhost:8051/task/scitex-hub/decide-prod-cutover-
final-go` into chat → recipient hits the same task.

A short-form alias `/t/<project>/<local-id>` is supported for
ergonomics in chat.

Citation in `comments[]` entries uses the same id form (`see
paper-scitex-clew/cohort-a-rerun` in markdown becomes auto-linked to
the URL by the renderer).

## Update → subscriber notification — reuse the a2a/channel push bus

Operator + lead a2a `3d7a20e7`: when a task changes, the relevant
agents + UIs must learn FAST. CRUCIAL: ride the SAME a2a/channel
push bus the fleet is hardening (the empty-beacon fix +
`any-channel-wakes-idle-agent` generalization make this reliable).
Do NOT invent a parallel notification system.

### Publisher side

The Django board's `_store.update_task` (the existing path) gains a
publish step right after the ruamel write:

```python
def update_task(task_id, **changes):
    write_yaml_atomically(...)
    notify_subscribers(task_id, changes)  # NEW
```

`notify_subscribers` posts a typed event onto the sac channel bus
(payload: `{task_id, changes, ts, actor}`). The event channel is
`scitex-cards:task:<project>/<local-id>` so subscribers can match by
glob pattern.

### Subscription rules

| Subscriber       | Subscribes to                                                                                            | Action on receive                                                                                          |
| ---------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| owning agent     | `scitex-cards:task:<own-project>/*` (every task they own)                                                 | the empty-beacon-wake + wake-generalize means an idle agent WAKES on this event and acts on the change.   |
| dependent agent  | `scitex-cards:task:<each-of-its-depends_on-ids>` (every task on its dep chain)                            | wakes + re-evaluates; if a dep just flipped to `done`, re-checks own readiness.                            |
| UI (every viewer)| `scitex-cards:task:*` (all changes; UI filters client-side)                                              | re-fetches `/graph` and re-renders the affected card / panel. Same wire as today's AutoRefresh `/rev` poll, but pushed instead of polled when the bus is available. |
| lead             | `scitex-cards:task:*` (full firehose; lead's own filter on top)                                          | logs into its own `_log_meta`; no auto-action — lead decides.                                              |
| operator         | (none — operator interacts via UI; the UI is the subscriber on their behalf)                            | UI surfaces the change visually.                                                                            |

### Synergy with empty-beacon + wake-generalize

The empty-beacon fix (proj-scitex-agent-container task) ensures the
a2a transport's "no messages → still alive" beacon doesn't crash
the channel subscriber. The wake-generalize (any-channel-wakes-idle-
agent) lets a task-update event on `scitex-cards:task:<id>` wake an
idle agent the same way an a2a-direct message would. **scitex-cards
is one of the loadiest consumers of that hardening work** — every
task update is a potential agent-wake event.

### Fallback to polling

If the a2a/channel bus is down: subscribers fall back to the
existing 5s `/rev` polling pattern (AutoRefresh.tsx, ADR-0005
sidecar pattern). The push bus is the FAST path; the poll is the
durable path. Same shape as the GitHub-vs-SSH-fanout split above.

## Notes

- Operator's primary pains pinned for the design rationale:
  - "返事が来ない＝私にとって死んだのと同じ" (TG 9576) → drives the
    fleet-liveness panel + the color-by-recency logic (ADR-0003 +
    -0005).
  - "what's waiting on ME" (TG 9522) → drives the BLOCKING YOU
    panel + the operator-decision gold-LOUD halo on cards (ADR-0004).
  - "decision is a node" (TG 9524) → drives kind=decision + the
    decision-node ADR per-task (ADR-0003).
  - "文字でいろいろやられてもわからない" (TG 9666) → drives the
    visual-first layout (this ADR).
  - "cross-machine + cross-agent + ACL + tags + filtering" (TG 9671)
    → drives the filter bar + the sharing model + the ACL
    insertion point (this ADR).

- Per-task `adr.md` for this work lives in `tasks/proj-scitex-cards-
  full-board-ui-spec/` (new task to be added at PR open). The
  per-task ADR tracks the implementation rollout; this repo-arch
  ADR is the lasting spec.

- Visual reference: `/tmp/scitex-cards-prototype/prototype-v3.html`
  (served on `:8052`). v1/v2/v3 history kept in the same dir as
  the design evolves: v1 = stream-strip prototype; v2 = card
  renderer from operator schema; v3 = full UI spec (this ADR).

- Implementation across THREE PRs (locked order):
  1. `feat/task-dataclass-and-strict-validators` — the dataclass
     + write endpoints + Resolve→notify wiring (the quality-hygiene
     PR). Builds the data layer this UI consumes.
  2. `feat/board-full-ui-v3` — the FE rewrite delivering the
     filter bar + columns + BLOCKING YOU + drawer + Resolve button.
  3. `feat/fleet-liveness-panel` — the SSH-fanout watcher + the
     per-host header dot-strip / Fleet tab (ADR-0005). Lays
     alongside or after the FE rewrite depending on operator-
     review pace.
