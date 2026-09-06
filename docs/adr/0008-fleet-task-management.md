# ADR-0008: Adopt `scitex-cards` as the fleet's central task-management substrate

## Status

**Proposed (DRAFT)** — 2026-06-07. Awaiting lead review (a2a
`2bc67d45` + `f36c6121`) on behalf of the operator before
acceptance. **Tightly coupled with `scitex-lead`**: any change to
the decision below requires lead a2a + operator concurrence.

## Context

The operator's standing direction (TG 9494 + 9667, lead a2a
`2bc67d45`/`e6cf6a2b`/`f36c6121`): **agents complete tasks, not
chat.** Today the fleet holds task state in three places — agent
heads, scattered `GITIGNORED/{FUTURE,RUNNING,DONE}/*.md` notes,
and operator memory. The "did anyone tell me?" failure mode burns
operator trust on every reload.

`scitex-cards` ships the floor of a single canonical store:

- Closed-enum `Task` dataclass (`_model.Task`) with fail-loud
  validators (`status`/`kind`/`blocker`) raising
  `TaskValidationError` on unknown values.
- Comment-preserving ruamel writer + atomic rename + a
  read-modify-write lock (`_store._store_lock`).
- Web board at `:8051` (board-v3 server-rendered kanban + legacy
  React-Flow drill-down at `/legacy/`) with AutoRefresh 5s mtime
  poll.
- 270 tasks in the fleet-shared `~/.scitex/cards/tasks.yaml` today
  (267 + the 3 proxy-written for proj-scitex-agent-container,
  2026-06-07).

The board substrate works. The **rollout surface** — how every
agent FILES + UPDATES + COORDINATES on tasks — is what this ADR
locks down so the operator + lead + every worker share one
truth, one wire, zero memory.

### Requirements (operator brief, batched 2026-06-07)

1. **Two-root scope**: `<git-root>/.scitex/cards/` (project-local)
   vs `~/.scitex/cards/` (user-global, fleet-shared) — when to use
   which.
2. **Agent linking**: how an agent attaches to its tasks; how the
   lead filters "show me agent X's open tasks."
3. **Multi-host sync**: GitHub-as-SSoT; no peer rsync.
4. **Daemon/cron**: periodic sync + nudges + CI-completion
   wiring.
5. **SAC dependency**: hard vs optional from agent-container's
   spec.yaml.
6. **Task-creation hook**: Claude Code hook so creation
   auto-lands in `.scitex/cards/`.
7. **Periodic reminders / nudges**: a kick-in-the-pants daemon for stale
   in_progress + assignee digests.
8. **Push-notif inter-agent status**: state changes notify
   dependents via sac a2a push.
9. **CI/automation integration**: GH-Actions completion →
   `status: done/failed` (existing task #15).
10. **Host attribution**: which host (+ which agent/user) owns
    or last-touched a task.
11. **Timestamps + history**: `created_at` + per-task update /
    change history (audit trail).
12. **Comment threads**: per-task back-and-forth, not just a
    single `--note`.
13. **Task-event notifications**: comments / status flips / new
    dependencies notify the assignee via sac a2a push.

## Decision

`scitex-cards` is **THE** fleet task-management substrate. Every
agent (workers + the lead) reads and writes through its CLI /
MCP / Python API surface; direct YAML edits are forbidden.
Adoption is gated by a per-agent `required_skills` reference to
the bundled `40_for-consuming-agents.md` skill leaf (PR #63).

Concrete decisions per requirement, with **EXISTS** / **GAP**
markers (the audit the lead asked for):

### D1. Two-root scope — EXISTS

Use the package's existing precedence chain:

  1. `$SCITEX_CARDS_TASKS` (explicit override)
  2. `<git-root>/.scitex/cards/tasks.yaml` (PROJECT-LOCAL)
  3. `~/.scitex/cards/tasks.yaml` (USER-GLOBAL, fleet-shared)
  4. bundled examples (read-only fallback)

Plain-language rule (taught in `40_for-consuming-agents.md`): _"Is
this task only about MY repo?"_ → project-local. _"Cross-project
or fleet-coordination?"_ → user-global. When in doubt, write
project-local — the aggregator rolls up.

### D2. Agent linking — EXISTS (`assignee`); migration to `agent` deferred

**Primary linking field = `assignee`** (str, free-form, convention
`proj-<repo>` for workers, `scitex-lead` for the lead).
**Empirical evidence** (lead dogfood 2026-06-07): `scitex-cards
list-tasks --assignee proj-X` filters correctly today.

Forward-compat: the `Task` dataclass also has an `agent` field
(operator-co-designed in TG 9667) as the long-term replacement.
Migration is **deferred** until adoption asks for it: CLI gains
`--agent` as alias, accepts either, prefers `agent` on write once
universal. This ADR locks `assignee` as the decision for NOW.

The 273-row backlog at `assignee=None` is historical drift, not a
schema decision; opportunistic back-fill when an agent touches a
row.

### D3. Multi-host sync — GAP (Phase-1 stub today)

GitHub-as-SSoT, no peer rsync.

**Spec** (replaces the `sync-store` Phase-1 stub):

- Dedicated `scitex-cards-store` GitHub repo holds the canonical
  `~/.scitex/cards/tasks.yaml` + `tasks/<id>/` subtree. NOT
  vendored inside `scitex-cards` source repo (data ≠ code).
- Wire: `scitex-cards sync-store --apply` ≡ `git -C ~/.scitex/cards
  pull --rebase --autostash && git push`.
- **Conflict handling**: ruamel writer is comment-preserving +
  line-stable so per-row edits diff cleanly. Merge conflicts on
  the same row fall back to **last-write-wins at the field
  grain** via `_log_meta.last_writer` + `_log_meta.last_written_at`
  (stamps already in dataclass; just used as tie-breaker).
  Manual fallback: editor with conflict marker.
- **Atomicity**: ruamel rename-pattern atomic write + in-process
  `_store_lock`; GitHub push is the cross-host serializer.

### D4. Daemon / cron — GAP (units don't exist)

**systemd-user** (not cron). Two units, separate concerns:

1. `scitex-cards-sync.{service,timer}` — every 60s, runs
   `scitex-cards sync-store --apply`. `Restart=on-failure`,
   structured logs via `journalctl --user`.
2. `scitex-cards-nudge.{service,timer}` — every 6h (default), runs
   `scitex-cards nudge` (new verb, see D7).

Register unit specs in the operator's `scitex-dev cron registry`
(lead's domain; ADR records the pattern, scitex-dev owns the
inventory). CI-completion handler (D9) lives in the Django web
process, not a separate unit.

### D5. SAC dependency — HARD dep recommended

Recommend `scitex-agent-container` HARD-deps `scitex-cards` (not
optional). Three reasons:

1. **Operator's fail-loud rule** — optional means agents silently
   skip the SSoT path. Hard dep makes "no scitex-cards installed"
   a build-time error.
2. **Light footprint** — core deps = `ruamel.yaml` + `click`;
   `[mcp]` extra = `fastmcp` (already universal via venv-sac);
   `[web]` extra = operator-side only. ~2 MB to the image.
3. **Skill auto-load depends on it** — the `@path` propagation
   (PR #63 § Propagation) reads from `<site-packages>/
   scitex_cards/_skills/scitex-cards/`. No install = no skill load
   = consuming agents fall back to memory.

Needs agent-container's buy-in (lead is looping; the 2026-06-07
proxy-write exercise is dogfood evidence of the friction).

### D6. Task-creation hook — GAP (build later)

Wire as `~/.claude/hooks/pre-tool-use/` that intercepts Claude
Code's built-in `TaskCreate` tool + MIRRORS each call into
`scitex-cards add` (id/title/status + `assignee=$SCITEX_CARDS_AGENT`).
Why mirror not replace: agents already know `TaskCreate`; the
hook is free auto-persistence.

Risk: hook bypass when agents silence `TaskCreate`. Acceptable
v1; v2 closes by ALSO accepting an MCP path
(`scitex-cards mcp start` → agents use `add_task` tool directly).
**Either** path lands a row; neither is "agents type chat about
tasks."

### D7. Periodic reminders / nudges — GAP (new CLI verb)

New CLI verb `scitex-cards nudge` (idempotent; side-effect = post
to sac channel). The systemd-user timer (D4) runs it on cadence:

- Each `status: in_progress` row with `last_activity` older than
  threshold (default 24h) → `a2a notify <assignee> "[NUDGE]
  <task-id> stale {N}h — bump or resolve."`
- Each assignee with N open `pending`+`in_progress` rows → daily
  digest (single message).
- Per-row opt-out via a `_nudge: false` field; per-assignee
  default in user config.

Nudges land in the row's `comments[]` so the activity log keeps
receipts.

### D8. Push-notif inter-agent status — DESIGNED, IMPL GAP

Event wire designed in `30_two-tier-conventions-and-write-protocol.md`
(§ "Update → subscriber notification") + ADR-0006:

- Channel name: `scitex-cards:task:<project>/<local-id>`
- Payload: `{task_id, changes, ts, actor}`
- Subscribers: owning agent (its own `*`), dependent agents (each
  of its `depends_on` ids), board UI (firehose), lead (firehose).

What's missing: the IMPLEMENTATION. `NotificationPort` (ADR-0006)
needs an impl in the `scitex-cards-fleet` glue package (NOT in the
core — operator's standalone-vs-fleet split). The glue translates
`_store.update_task` writes into sac publishes. The CORE stays
oblivious.

Coordinates with agent-container's `empty-beacon-fix` arc —
scitex-cards will be one of the loadiest consumers of the same
wire.

### D9. CI/automation integration — GAP (existing task #15)

Existing HANDOFF.md task #15. Shape: GH-Actions webhook → Django
view `_django/handlers/ci_completion.py` flips `status:
done/failed` based on the workflow conclusion + posts a
`comments[]` entry with the run URL. Idempotent.

Per-row opt-in via a `ci.workflow: <name>` field (new). Workflow
name maps to a Task id via the `ci.task_id:` mapping in the
workflow file's env block.

### D10. Host attribution — EXISTS (dataclass) / GAP (CLI surface)

The `Task` dataclass already carries:

- `host: str | None` — where the work happens.
- `_log_meta: dict | None` — opaque writer-side event stamps
  (where the proposed `last_writer` / `last_writer_host` /
  `last_written_at` fields land).

What's missing: CLI flags + MCP fields + the writer stamping
behaviour. Convention:

- `_log_meta.last_writer` = the value of `$SCITEX_CARDS_AGENT`
  (the writing agent's name).
- `_log_meta.last_writer_host` = `socket.gethostname()` at
  write time.
- `_log_meta.last_written_at` = ISO-8601 UTC `now()` at write
  time.
- `_log_meta.created_by` = `$SCITEX_CARDS_AGENT` at `add_task`.
- `_log_meta.created_by_host` = `socket.gethostname()` at
  `add_task`.

Stamps fall back to `$USER` → `"unknown"` if `$SCITEX_CARDS_AGENT`
is unset.

Operator-facing readout: every row carries a "who/where/when"
strip in the detail drawer.

### D11. Timestamps + history — EXISTS partially / GAP for change-log

Already in dataclass: `created_at`, `last_activity`,
`started_at`/`finished_at` (compute-kind),
`_log_meta.completed_at`/`completed_by` (set by `complete_task`).
PARTIAL GAP: nothing stamps `created_at` on `add_task` today
(the field exists; the writer doesn't fill it).

**FILL THE PARTIAL GAP** in the D10 PR — `_store.add_task` stamps
`created_at = ISO-8601 UTC now()` if not provided; same for
`last_activity` on `_store.update_task` (every mutation pushes the
recency-coloring).

**SCHEMA GAP — per-field change history**:

The operator wants an audit trail of EVERY field change. Today
`comments[]` is the activity log (free-text). True per-field
history (`{ts, author, field, old, new}` rows) is **not** in the
dataclass.

**Decision**: add a `history: list[dict] | None` field to `Task`,
shape `[{ts, author, host, field, old, new}, …]`. Writer
(`_store.update_task`) appends one entry per changed field at
write time. Defaults to `None` (omitted on the wire when empty)
so legacy rows keep loading.

Trade-off: store growth. For a row with 50 lifetime edits,
`history` adds ~5 KB. Acceptable for the audit-trail benefit; if
proven heavy in dogfood, future PR adds `history_truncate(N)`
that keeps the last N + a count.

Validator: each entry validates `{ts, author, field, old, new}`
all present + non-empty strings (`old`/`new` may be `null` for
create / delete-field).

### D12. Comment threads — EXISTS (flat) / GAP (nested reply-to)

`comments: list[{ts, author, text}]` EXISTS — append-only,
chronological. Functions as a flat thread today.

**True nested threading** (reply-to-a-comment) is **not** in the
dataclass. Adding it: each comment dict gains optional
`in_reply_to: str | None` pointing at another comment's `ts` (the
existing string is unique enough at second granularity; `ts` →
ULID/UUID can come later if collisions matter).

**Decision**: add `in_reply_to: str | None` to the comment shape
(optional, validator accepts absent ≡ "top-level comment"). FE
renders threading via a tree fold. NO change to the existing
flat reads — legacy code keeps working.

CLI gap closure: new verb `scitex-cards comment <task-id> <text>
[--in-reply-to <ts>]`. Highest-priority gap closure per PR #63
(the lead's review starts with this PR).

### D13. Task-event notifications — GAP (impl-side of D8)

Concrete events the assignee gets notified about:

- New `comments[]` entry with `author != assignee`.
- `status` flips (any) where the row has a non-self `assignee`.
- New `depends_on` entry where the row is the upstream.
- Resolve / reopen events (already partially wired via the
  Resolve button + `/reopen` HTTP).

Wire: same channel as D8 (`scitex-cards:task:<project>/<id>`).
Payload includes `event_kind ∈ {comment, status_change,
dep_added, resolve, reopen}` so subscribers can filter.

NotificationPort impl lives in `scitex-cards-fleet` (D8); the
event-kind discrimination is in the CORE writer (close to where
the change is detected) so the port stays a thin transport.

## Consequences

### Positive

- **Single canonical store** (per the operator's "no memory"
  rule). Lead + operator + every agent read one truth, one wire.
- **Fail-loud schema** — closed enums + validator raise at write
  time. The class of "silent dropout / typo passed validation"
  bugs is closed for the listed enums.
- **Skill-driven rollout** — `@path required_skills:` means
  agents auto-load the protocol on boot; no per-agent training.
- **Audit trail** (D10 + D11) — every row carries who/where/when
  + per-field change history. Operator's "did anyone tell me?"
  failure mode is replaced by `comments[]` + `history`.
- **Operator UX moat** — push-notif (D8/D13) means an agent's
  status flip surfaces to the operator within seconds via the
  board + via direct a2a to dependents. Live, online, shared,
  machine-readable (north-star pillar #4).

### Negative / trade-offs

- **Hard dep on `scitex-cards`** in every container (D5) ≈ ~2 MB
  per image. Operator-accepted.
- **`history: list[dict]`** (D11) grows the per-row payload. ~5
  KB for a heavily-edited row; truncation path reserved.
- **`scitex-cards-store` GitHub repo** (D3) is a new asset to
  back up + monitor. Operator's git → GitHub policy already
  covers it; no new infra.
- **NotificationPort impl** (D8/D13) lives in a SEPARATE glue
  package (`scitex-cards-fleet`), not the core. Preserves the
  operator's standalone-vs-fleet split (TG 9678, ADR-0006) but
  means the wire requires a deployment step (install the glue +
  configure the port).
- **systemd-user units** (D4) — assume systemd-user is
  available. On hosts without it (rare in this fleet but
  possible), fall back to cron + nohup; document in the
  per-host adoption note.

### Migration / phased rollout (the lead drives ordering)

Numbered against the PR slicing in `41_cli-mcp-gap-analysis.md §
F`, with the new D-numbered decisions folded in:

  1. PR #63 (THIS PR) — skill + gap audit + ADR-0008 (this
     file). Doc-only design checkpoint.
  2. PR — CLI `comment` verb + MCP `comment_task` + Python
     `add_comment` (D12). Highest-value gap closure; load-bearing
     for fleet-skill adoption pattern B/C.
  3. PR — `add` / `update` field-flag expansion + closed-enum CLI
     validation + writer stamping (D10, D11 partial-gap fill).
  4. PR — `list-tasks` filter expansion (`--project --host
     --blocker --kind --blocking-me`); `--assignee` already works
     per D2.
  5. PR — `sync-store --apply` wire + systemd-user `sync.timer`
     unit spec (D3, D4 sync half).
  6. PR — CI-completion handler (D9, existing task #15).
  7. PR — `scitex-cards nudge` verb + nudge unit (D7, D4 nudge
     half).
  8. PR — `history: list[dict]` schema + validator + writer
     append-on-update (D11 schema GAP).
  9. PR — Claude Code `TaskCreate` mirror hook (D6).
 10. PR — `NotificationPort` impl in `scitex-cards-fleet` glue
     package (D8, D13). NOT in `scitex-cards` core.
 11. Agent-container HARD-deps `scitex-cards>=<that-version>` (D5,
     their PR not mine).
 12. Lead propagates the skill via `required_skills:` into every
     agent's `spec.yaml` (operator's discretion on when).

Gates are sequential; later items assume earlier surface
exists. Lead can re-order or pause at any step.

## Notes

- **Provenance**: lead a2a `2bc67d45` (2026-06-07) + `f36c6121`
  (2026-06-07) on behalf of operator standing direction TG
  9494 + 9667 + 2026-06-07 batch.
- **Empirical inputs**: lead's 2026-06-07 dogfood (D2 assignee
  confirmation); proj-scitex-agent-container's 2026-06-07
  proxy-write request (D5 friction evidence — 3 tasks
  `ci-develop-pytest-matrix-jun5` /
  `proj-scitex-agent-fleet-rebuild-verify-pr334` /
  `proj-scitex-openai-compat-design` landed via the Python API
  bridge while the CLI surface catches up).
- **Tightly coupled with `scitex-lead`**: any change to D1–D13
  requires lead a2a + operator concurrence.
- **Supersedes**: nothing. Builds on ADR-0001 (universal task
  layer), ADR-0002/-0003/-0004 (closed enums), ADR-0005 (fleet
  liveness), ADR-0006 (board UI spec + extension ports),
  ADR-0007 (Task dataclass = single schema source). This ADR
  ties them together into the FLEET adoption shape.
- **References**: PR #63 (skill leaf `40_for-consuming-agents.md`
  + gap audit `41_cli-mcp-gap-analysis.md`); HANDOFF.md NORTH
  STAR + SSoT DATA LAYOUT + Operating policy (home-canonical
  store).
