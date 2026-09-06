# ADR-0001 — scitex-cards as the fleet's universal task-driven layer

**Status.** Strawman — operator review pending.

**Date.** 2026-06-02

**Author.** proj-scitex-cards (agent), driven by the lead under operator's
overnight directive.

**Supersedes / extends.** `GITIGNORED/ARCHITECTURE.md` (Phase-0 9-requirement
map, 2026-05-27) and `GITIGNORED/DESIGN/sync-substrate.md` (Phase-2 cross-host
sync design). This ADR widens those internal documents into a public,
broadcast-ready architecture statement.

---

## 1 — Context

The operator's directive (verbatim intent, 2026-06-01 overnight):

> A task-driven UI usable at EVERY level: inside Orochi, inside scitex-hub,
> sac-agent-to-sac-agent, a single agent, a single human, or a team.
> Layer separation: Orochi = chat (Slack-like, good at talking, weak at
> task-centric). scitex-cards = task-centric. Orochi's "todo tab" will
> CONSUME scitex-cards rather than reimplement tasks.

The package is already built on `scitex-{ui,app}`, already ships a Django
module, already lays out the YAML store under `~/.scitex/cards/tasks.yaml`,
already has a drill-down React Flow board with drag-reorder, drag-connect,
markdown drawer, table view, search, undo, keyboard shortcuts, per-scope
progress export, and a stable wire format. Phase-1 MVP (PR #14, scope-aware
shared card store: `_store.py` Python API + `_cli/_write.py` mutation verbs +
`_mcp_server.py` MCP tool surface + `fcntl.flock` write mutex + `scope` /
`assignee` / `_log_meta` schema fields) is staged but **not yet merged to
develop**. This ADR assumes Phase 1 lands as the floor and designs the
layers above it.

**The payoff the operator wants:**

1. **Agent migration becomes easy** — an agent's work-state lives in the
   the card store, not in its head or on its host. A migrated agent reads its claimed
   tasks and resumes.
2. **Operator's own memos live in the card store** — so he won't forget anything.
3. **The whole fleet faces the same direction** — one shared board, one
   set of priorities, every participant sees "their direction" via
   scope/assignee filters but everyone shares one source of truth.

---

## 2 — The multi-level model

The same data store and the same verb set are used at every level. What
changes per level is **which adapter** the participant uses, not what they
can do.

```
                       ┌──────────────────────────────────┐
                       │   ~/.scitex/cards/tasks.yaml      │
                       │  (canonical YAML — single SOT)   │
                       └──────────────────────────────────┘
                                       ▲
                                       │  load_tasks / save_tasks
                                       │  (fcntl.flock around RMW)
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
   ┌──────────┴──────────┐  ┌──────────┴──────────┐  ┌──────────┴──────────┐
   │  _store.py          │  │  _django handlers   │  │  _mcp_server.py     │
   │  Python API         │  │  HTTP /graph etc.   │  │  FastMCP tools      │
   │  (add/update/done/  │  │  (drag-reorder,     │  │  (scitex_cards_*)    │
   │  list/summary)      │  │  /priority, drawer) │  │                     │
   └────────┬────────────┘  └──────────┬──────────┘  └──────────┬──────────┘
            │                          │                        │
   ┌────────┴────────┐    ┌────────────┴───────────┐    ┌───────┴────────┐
   │  scitex-cards    │    │  scitex-cards board     │    │  Claude /      │
   │  CLI verbs      │    │  (React Flow)          │    │  agent harness │
   │  (add/done/...) │    │  ↳ standalone @8051    │    │  via MCP       │
   │                 │    │  ↳ embedded in hub     │    │                │
   └─────────────────┘    │  ↳ embedded in Orochi  │    └────────────────┘
                          └────────────────────────┘
```

The six participant levels:

| Level                    | How they reach the store            | Default scope           |
| ------------------------ | ----------------------------------- | ----------------------- |
| Single human (operator)  | CLI + web board on localhost        | unfiltered              |
| Single agent (standalone)| Python API (`import scitex_cards`)   | `agent:<self>`          |
| sac peer ↔ sac peer      | MCP tools + `$SCITEX_CARDS_SCOPE` env| `agent:<self>`          |
| Team (operator + agents) | Shared YAML + scope filters         | mixed                   |
| scitex-hub app           | `_django` module mounted at `/cards/`| as configured           |
| Orochi todo tab          | Same `_django` module embedded     | as configured           |

**Layer separation guarantee.** scitex-cards never imports sac, scitex-hub,
or Orochi. The flow is one-way: those higher layers depend on scitex-cards
or speak to it via its public surfaces. This keeps scitex-cards testable
and shippable on its own and avoids the "everything depends on everything"
trap.

---

## 3 — Storage backend decision: **GitHub-backed YAML** (not the sac listen DB)

The lead asked us to pick between two substrates. Decision: **a private
GitHub repo backing `~/.scitex/cards/`**, synced via `git pull --rebase
--autostash && git push`. The sac `listen` control-plane DB is **not** the
canonical store; it is, optionally, a notification fan-out adapter on top
(Section 6).

### Why GitHub (and not the sac DB)

1. **Doctrine match.** "GitHub is the sync substrate" is the operator's
   stated fleet-wide pattern. `scitex-dev`, `scitex-clew`, and other
   personal-state dirs already sync this way. One mental model, one set of
   credentials, one debug path.

2. **The data is already YAML.** The store is hand-editable, diff-able,
   git-mergeable text. Moving it into a DB would force a translation layer
   that the operator's "structured data IS the asset, programmable and
   extensible" philosophy explicitly rejects. The data must stay readable
   by any tool that can read text.

3. **NAT-friendly.** `git push/pull` through GitHub works through every
   NAT'd laptop, agent container, and offline-with-deferred-sync workflow.
   The sac listener assumes the control plane is reachable from every
   writer at write time — offline laptops can't write.

4. **Free audit trail.** `git log -p ~/.scitex/cards/tasks.yaml` is
   "who changed what when" with zero code. The sac DB would need separate
   audit instrumentation, and we'd lose the ability to roll back to a
   specific snapshot with `git checkout`.

5. **Layer cleanliness.** sac is a meta-runtime (peer messaging, control
   plane). Loading the operator's task graph into sac entangles layers —
   when sac bounces, the cards would go down too. Git-backed YAML
   survives any fleet outage.

6. **Conflict resolution is already designed.** `GITIGNORED/DESIGN/sync-substrate.md`
   specs the per-task LWW merge driven by `_log_meta.completed_at`. The
   seam is locked; Phase-2 just fills in the body.

### What the sac listen DB IS used for

A **notification fan-out adapter** on top of the git-backed store
(Section 6 — Phase 4, optional). When an agent does a local write, it
emits a `scitex-cards:tasks-changed` event on a sac channel; other agents
subscribed to that channel can choose to re-pull and re-render. This buys
"the operator drags a node on the board and three agent containers see
the new priority within a second" without making sac the source of truth.

### Where the GitHub remote lives

`ywatanabe1989-private/scitex-cards-state` (private, operator-owned). The
public scitex-cards *package* repo stays at `ywatanabe1989/scitex-cards` and
contains zero personal task data — only the code, the schema, the example
store, and the adapters.

---

## 3.5 — Cross-cutting (*kushizashi*, the skewer view) is first-class

The operator's framing — a *kushizashi* ("skewered") view that pierces ALL
projects and ALL hosts at once — is the LOAD-BEARING use case for the
universal task layer. Both axes are first-class:

### Two independent axes

| Axis              | Cross-cutting mechanism                                  |
| ----------------- | -------------------------------------------------------- |
| **Across projects** | One user-level store at `~/.scitex/cards/tasks.yaml` holds tasks from EVERY project. A per-project store at `<project>/.scitex/cards/tasks.yaml` is an **override** for project-internal lists the operator doesn't want in the cross-cut view. Precedence: project store > user store, when both exist (per the SciTeX local-state-directories convention). |
| **Across hosts**    | The user-level store is per-host by physical location (`~/.scitex` lives on each host's local filesystem). A **git-backed sync substrate** linearizes the per-host writes into one canonical history, so reading the store on host B sees host A's edits after a `git pull`. |

The skewer view is what falls out when both axes are unfiltered:
`list --scope ''` (no scope filter) on a fresh-pulled store shows every
task on every project on every host — one skewer, all targets.

### Why this re-justifies the GitHub backend (over sac listen DB)

The cross-host axis is the one that forced the storage decision. Section 3
listed six general reasons; the skewer-view requirement specifically demands
two more guarantees that the sac listen DB cannot give:

7. **The canonical view must be readable when offline.** An operator on
   a flight, an agent container that lost network, a freshly-rebooted
   laptop — they must still be able to read the cross-cut view of
   "what was the world like the last time I synced". With Git, the
   answer is "every clone is a complete read-only replica."  With sac
   listen, "offline" means "blank."

8. **The aggregation operator is `git merge`, not a query.** Adding a
   new host doesn't require schema changes, doesn't require teaching a
   new endpoint, doesn't require an "all hosts" virtual table. It
   requires `git clone` + `git push`. The aggregation is the substrate.

The sac listen DB **would** be a fine push-notification channel for
"host A just pushed a new commit, pull if you want it now", but it is
not the canonical store. Phase-4 fan-out (Section 6) makes that explicit.

### `host:<hostname>` is a first-class scope label

To make the cross-host axis legible in the data, the scope-convention
table (Section 4) elevates `host:<hostname>` to a first-class label
alongside `agent:<name>` and `project:<name>`. Typical uses:

```bash
# A task that only matters on this host (env setup, local config, etc.)
scitex-cards add wsl-ssh-key "regenerate ssh key" --scope host:wsl2-dev

# A cross-project, cross-host operator memo
scitex-cards add memo-call-sarah "Call Sarah re: grant" --scope user:operator

# All my tasks on this host
scitex-cards list-tasks --scope "host:$(hostname)"

# All my tasks across every host — the skewer view
scitex-cards list-tasks --scope ""
```

`host:` is a convention not an enum (Req 8 stance), so a task that
crosses hosts simply doesn't carry the `host:` label.

### Failure modes the design accepts

- **Two hosts edit the same task in the same pull-window.** Phase-2
  resolves via per-task LWW on `_log_meta.completed_at` (or commit
  author-date for non-completion edits). Documented in
  `GITIGNORED/DESIGN/sync-substrate.md`.
- **One host falls behind for days.** It still shows a coherent
  read-only snapshot of its last pull. When it catches up, the LWW
  merge is the same as the same-window case.
- **The state-repo remote is unreachable.** Local writes still succeed
  (writing to disk doesn't depend on the remote). The next `sync`
  pushes the backlog.

What the design does NOT accept:

- **A sub-second consistency guarantee across hosts.** That's the
  Phase-4 notification adapter's job, and it's eventual-consistency
  on top of git, not synchronous.
- **A hard ACL across hosts.** Scope is a display filter; if you give
  every host a copy of the state repo, you give them the full data.
  Hardening this would mean per-row encryption (open question #4 in
  Section 8).

---

## 4 — Schema (Phase-1 floor)

Every field is **additive-optional** so existing YAML keeps loading. This
matches the binding constraint from `GITIGNORED/ARCHITECTURE.md` §Req-8
("be generic"): the schema must not couple to any specific agent, project,
or workflow.

```yaml
tasks:
  - id: my-task                          # REQUIRED, unique
    title: "Implement my-task"           # REQUIRED
    status: pending                      # REQUIRED — one of: goal,
                                         #   pending, in_progress, blocked,
                                         #   done, deferred, failed
    # ── identity / direction ──
    scope: "agent:proj-scitex-cards"      # optional, free-form audience label
    assignee: "agent:proj-scitex-cards"   # optional, free-form actor label
    priority: 3                          # optional, integer (lower = earlier)
    parent: "epic-id"                    # optional, parent task id (nested graph)
    # ── shape ──
    depends_on: [other-task]             # optional
    blocks: [downstream-task]            # optional
    repo: "scitex-cards"                  # optional, free-form
    note: |                              # optional, markdown — shown in drawer
      ## Context
      ...
    # ── automatic event stamps ──
    _log_meta:                           # opaque dict, written by complete_task
      completed_at: "2026-06-02T03:00:00Z"
      completed_by: "agent:proj-scitex-cards"
```

**Scope and assignee conventions** (convention, not enum — generic stance):

- `agent:<name>`  e.g. `agent:proj-scitex-cards`, `agent:lead`, `agent:hub-ops`
- `project:<name>` e.g. `project:scitex-clew`, `project:neurovista`
- `host:<name>`    e.g. `host:wsl2-dev`, `host:mba-arm64`
- `user:<name>`    e.g. `user:operator`
- `private`        operator-only
- any other free-form string — scope is a display filter, not access control

**Filter semantics.** `list_tasks(scope="agent:foo")` returns only tasks
where `scope == "agent:foo"`. `$SCITEX_CARDS_SCOPE` provides the default
when the caller doesn't pass one explicitly; pass `scope=""` to opt out of
the env default. This is **display filtering**, NOT a permission system —
any participant can read any task by removing the filter.

---

## 5 — How each level consumes the layer

### 5.1 Single human (operator)

```
$ scitex-cards board                  # web UI on http://127.0.0.1:8051
$ scitex-cards list-tasks                   # CLI listing
$ scitex-cards add note1 "Don't forget X" --scope private
$ scitex-cards done note1
```

Browser bookmark is the operator's "second brain". Personal memos go in
with `scope: private` so they don't pollute fleet views.

### 5.2 Single agent (one Claude/process, no fleet)

```python
import scitex_cards as cards
# Default scope from $SCITEX_CARDS_SCOPE = "agent:proj-scitex-cards"
mine = cards.list_tasks(status="pending")           # only my pending work
cards.update_task(task_id="my-task", status="in_progress")
# ... work ...
cards.complete_task("my-task")                       # stamps completed_at+by
```

### 5.3 sac agent ↔ sac agent

The sac container injects `SCITEX_CARDS_SCOPE=agent:<name>` and
`SCITEX_CARDS_AGENT=<name>` into the environment (covered by
scitex-agent-container's existing env-injection skill). The agent uses
the MCP tools or the Python API exactly as in 5.2 — sac itself never
touches the schema. Cross-agent claim/handoff is a single
`update_task(assignee="agent:other")` call.

### 5.4 Team (operator + multiple agents on multiple hosts)

Each host has `~/.scitex/cards/` as a git checkout of the private state
repo. `scitex-cards sync-store --apply` runs locally (manual or cron) to push
local edits and pull peers' edits. Conflict-resolve via per-task LWW on
`_log_meta.completed_at` (Phase-2 body — see `sync-substrate.md`).

### 5.5 scitex-hub app

The `_django/` module is already shaped as a scitex-app. The hub PR
(opened on the *scitex-hub* repo, not here) registers the module:
URL prefix `/cards/`, app config `scitex_cards._django.apps.SciTeXCardsConfig`.
Static files are bundled into the wheel. See `GITIGNORED/DESIGN/hub-embed.md`.

### 5.6 Orochi todo tab

Orochi consumes scitex-cards rather than reimplementing tasks. Two
viable embed options, in increasing decoupling:

1. **Iframe** the scitex-cards board served from a known host:port. Cheapest
   to ship; cross-frame click handling is awkward.
2. **Import** the `_django` module into the Orochi app the same way the
   scitex-hub embed works. Better integration; requires Orochi to be on
   the scitex-app convention.
3. **REST consumer**: Orochi makes HTTP calls to `/graph` + the
   mutation handlers (`/priority`, future `/messages`); renders its own
   chrome over the same data. Strongest decoupling; most work.

Recommendation: start with (1) for parity now, plan for (3) once the
chat/message field (Phase 3) is wired so Orochi can show a unified
"chat + linked task" view.

---

## 6 — Phase plan (where we are, what's next)

| Phase | What                                              | Status                  |
| ----- | ------------------------------------------------- | ----------------------- |
| 0     | 9-requirement architecture map                    | ✅ done (`ARCHITECTURE.md`) |
| 1     | scope/assignee schema + Python API + CLI + MCP    | 🟡 PR #14 OPEN, CONFLICTING — needs rebase onto current develop |
| 2     | Cross-host sync body (git pull/push + LWW merge)  | designed in `sync-substrate.md`, not built |
| 3     | Operator↔agent chat (`messages:[]` per task)      | designed in `operator-agent-chat.md`, not built |
| 4     | sac-listen notification fan-out (optional adapter)| this ADR introduces; not built |
| 5     | scitex-hub embed PR (single-file registration)    | designed in `hub-embed.md`, not built |
| 6     | Orochi todo-tab embed (iframe → app → REST)       | this ADR introduces; not built |

**Tonight's unblock.** Phase 1 is the floor everything else stands on. The
universal-task-layer cheatsheet broadcast to the fleet (the lead's
deliverable 2) describes the Phase-1 surface; if Phase 1 doesn't land,
the broadcast describes vaporware. Rebasing + landing PR #14 is therefore
the immediate next action after this ADR ships.

---

## 7 — Migration + operator-memory synergy

The two synergies the operator cares about most are both consequences of
"work-state lives in the store, not in the agent's head":

### 7.1 Agent migration

An agent's claim on a piece of work is a row in `tasks.yaml`:
`assignee: agent:proj-foo`, `status: in_progress`, `note: <last known state>`.
When `proj-foo` is migrated (new host, new container, fresh restart, or
ownership handed to `proj-bar`), the new instance:

1. Reads `$SCITEX_CARDS_SCOPE` from its env (e.g. `agent:proj-foo`).
2. Runs `scitex-cards list-tasks --assignee agent:proj-foo --status in_progress`.
3. Picks up exactly where the old instance left off, with the `note`
   field as the handoff context.

No "memory backup", no "context export", no "what was I doing again?".
The work-state was never in the agent — it was in the store.

### 7.2 Operator memory

Same mechanism, different scope. Operator writes
`scitex-cards add forget-me-not "Tell Sarah about X" --scope private` and
the next time he opens the board, it's there. The store doubles as a
durable inbox that survives laptop reboots, browser-tab closures, and
context-switches between projects. Personal memos are git-backed and
synced across hosts the same way work tasks are, so the operator's MBA
sees them at the same time as his WSL2 dev box.

### 7.3 The fleet facing the same direction

When every agent reads from one store, "priority" means the same number
everywhere. The operator drags a card to the top of the board (web UI,
Phase-1 already shipped), `_django/handlers/priority.py` rewrites the
YAML, the next agent that calls `list_tasks(status="pending")` sees the
new order. The board is the *one rope* every agent rows behind.

---

## 8 — Open questions / risks

1. **Phase-1 PR #14 rebase scope.** Develop has moved ~40 PRs since #14
   was opened (per-scope progress UI, search, keyboard shortcuts, etc.).
   Conflicts are likely concentrated in `_cli/_main.py` (verb wiring) and
   `_model.py` (added schema). Expected effort: 1–2 turns; risk: medium.

2. **sac-listen notification adapter ordering.** Phase-4 emits events
   on local-write — but the canonical state is "after `git push`". If
   listeners react before push, they may pull state that doesn't yet
   exist. Resolution: fire the event *after* `sync --apply` succeeds,
   not after the local write. This makes the notification eventually
   consistent with the canonical store.

3. **Private state-repo bootstrap.** The first time an agent comes up
   on a fresh host, `~/.scitex/cards/.git` doesn't exist yet. Phase-2
   needs a `scitex-cards init-store --shared --from-remote <url>` mode that
   `git clone`s the state repo. Currently the design assumes the dir is
   already a git repo.

4. **Scope vs ACL drift.** As more agents use the store, the operator
   may want true private notes (`scope: private` that other agents
   *cannot* read, not just *don't* by default). The architecture today
   says "scope is display filter, not access control" — if that changes,
   we need a real ACL layer (most likely encryption at rest for
   `scope: private` rows). Flag, don't build.

5. **Schema enrichment pressure.** `tags`, `owner`, `due`, `created_at`,
   `updated_at` are tempting once the fleet uses the store. Stay YAGNI
   — add only when a concrete consumer needs the field. The
   `_log_meta` opaque bag absorbs ad-hoc event stamps without schema
   growth.

---

## 9 — Decision log

- **2026-05-27** — Phase-0 architecture (9-req map) accepted by the lead;
  Phase-1 build started.
- **2026-05-27** — PR #14 (Phase-1 MVP) opened.
- **2026-06-01** — Operator overnight directive: scitex-cards as the
  universal task-driven layer.
- **2026-06-02** — This ADR drafted. Storage backend = GitHub
  (private state repo + `git pull/push` substrate). sac-listen = optional
  Phase-4 notification adapter, not the canonical store.

<!-- EOF -->
