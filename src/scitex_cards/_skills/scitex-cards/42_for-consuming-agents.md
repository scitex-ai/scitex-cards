---
description: |
  [TOPIC] For consuming agents — quick-onboard "how do I use scitex-cards
  as MY task SSoT?"
  [DETAILS] One-page protocol for any fleet agent: which CLI / MCP /
  Python entry point to use for create / list / update / comment /
  complete, the closed-enum (fail-loud) schema, the title-prefix
  convention, and the lead↔worker coordination wire. Read this first if
  you've just been told "use scitex-cards for your cards."
tags:
  [
    scitex-cards-for-consuming-agents,
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
2. **Fail loud, fail fast.** `scitex-cards` validates the schema on
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
scitex-cards resolve-store
# → prints {resolved: <path>, backend: sqlite, ...}
```

See [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
for scope conventions (project-local vs fleet-shared work) — those
conventions live on as a `scope=`/`project=` field distinction inside
the single database, not as separate files.

---

## Your first task, in 30 seconds (fresh agent quick-start)

```bash
# 1. Confirm scitex-cards is installed + which store it resolves to.
scitex-cards --version
scitex-cards resolve-store                  # prints the resolved DB path

# 2. Add a smoke task to YOUR slice.
scitex-cards add <you>-smoke-$(date +%s) \
    '[P2] smoke: confirm I can write to the store' \
    --scope agent:<you> \
    --assignee <you> \
    --status pending

# 3. List your slice + confirm the row is there.
scitex-cards list-tasks --scope agent:<you> --json | jq '.[].id'

# 4. Mark it done.
scitex-cards done <you>-smoke-<that-stamp> --by <you>

# 5. See yourself on the board.
# Open http://<board-host>:8051/  — your row is visible within 5s.
```

If any step fails: STOP and `reply` to your lead with the exact
failing command + full stderr. Don't retry-loop. ([§ Operating
discipline](#operating-discipline--what-not-to-do))

---

## Long-form prose — `tasks/<id>/README.md` + `adr.md`

Whenever a task has substantive context, seed the per-task dir:

```bash
mkdir -p tasks/scitex-cards-fleet-rollout
$EDITOR tasks/scitex-cards-fleet-rollout/README.md   # what / why / how
$EDITOR tasks/scitex-cards-fleet-rollout/adr.md      # ADR-template decisions
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
  the package owner (`scitex-cards`) to add to the enum.

---

## Sanity-check yourself once you've adopted

```bash
scitex-cards resolve-store                  # confirm the DB path you expect
scitex-cards list-tasks --scope agent:<your-agent-name> --json | jq length
scitex-cards add smoke-$(date +%s) '[P2] smoke from <your-agent-name>'
scitex-cards done smoke-<that-stamp>
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
"how do I file a CARD" is the same answer everywhere.

1. **Pip-install pins the version** — `pip install
   scitex-cards>=<version>` lands the bundled skills under
   `<site-packages>/scitex_cards/_skills/scitex-cards/`.
2. **Agent's spec references the bundled path** under a
   `required_skills:` entry — exact grammar is the SAC container
   glue's domain; the canonical reference shape is:
   `"@scitex_cards:_skills/scitex-cards/40_for-consuming-agents.md"`.
   (See [41_cli-mcp-gap-analysis.md § G](41_cli-mcp-gap-analysis.md#g-propagation-the-path-mechanism)
   for the wiring rationale.)
3. **Container boot resolves the reference** — the skill text loads
   into the agent's context; the agent now knows the protocol.
4. **Operator host: `scitex-cards skills install --claude-symlink`**
   back-fills the symlink under `~/.claude/skills/scitex/` so
   Claude Code on the operator's host sees the same skill.

**Versioning**: the skill is **version-pinned via the package**, NOT
edited live. Editing one skill leaf does NOT propagate to a consuming
agent's spec until the consumer pip-bumps `scitex-cards`. That gives
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
