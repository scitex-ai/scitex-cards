# Changelog

## [Unreleased]

## [0.43.0] - 2026-08-17

### Cut to end a live incident, and the incident is the reason to install it

**Every client older than this release runs the full schema DDL on every
connection against a store that has been migrated to rung 12, and a resulting
`pg_proc` deadlock LOSES THE WRITE.**

Measured 2026-08-17 on the live store, with the package's own connection:

    client SCHEMA_VERSION    11        (0.42.0)
    shape.observed           11
    agreement                STAMP_IS_HIGH      <- AGREES? False
    triggers present         10, MISSING: []
    schema_already_current   False              <- DOES NOT SKIP

`schema_already_current` refuses the fast path whenever the stamp and the
physical shape disagree — correctly, since that is the state the migration
chain exists to repair. Rung 12 raised the stamp to 12 while every installed
client's ladder tops out at 11, so the disagreement went from rare to
UNIVERSAL and the skip stopped firing for the whole fleet. Each open then
issues `CREATE OR REPLACE FUNCTION` ten times, taking `ShareRowExclusiveLock`
on `pg_proc`; the 2026-08-01 curve is 4 concurrent opens -> at least 1
deadlock, 12 -> 11 of 12 failed.

**And there is no retry.** `DeadlockDetected` appears five times in the package
and all five are comments; there is no `except` clause for it and no call site
retries. `_store_tx` states the intent: "a serialization failure, which every
call site would then have to retry." So a deadlocked write raises to the caller
and is lost unless a human or an agent happens to notice — two were observed on
2026-08-17 and both were recovered only because someone was watching.

Installing this release restores `AGREES` for the client and ends its
participation in the storm. A rung bump is therefore a fleet-wide availability
event, not a schema change, and that is now written into the rung's own module.

### Added

- **Schema rung v11 -> v12: the SYNCED tables get their sync columns** (#882).
  `tasks` and `task_comments` now carry `origin_node`, `row_uuid`, `revision`,
  `updated_at` and `deleted_at` FROM CREATION, per the operator's rule that any
  syncable table carries them from the start and that a blind
  `ON CONFLICT DO UPDATE` is prohibited. Purely additive — `ALTER TABLE ADD
  COLUMN` only, no trigger and no function — so the columns EXIST and are not
  yet populated; population is deliberately separate, because `origin_node` is
  SUBJECT (which machine the row is about) and never PROVENANCE (which node
  relayed it), and those coincide only until the first relay.
- **`update_task` accepts `expected_revision`, an opt-in compare-and-set**
  (#880). A caller that opts in and loses leaves the row untouched.

### Fixed

- **A failed board load says why instead of painting a blank canvas** (#883).
  The board was rendering the server's complete store-resolution diagnosis as
  one unreadable red line, which reads as "nothing displayed". It now leads
  with the server's own first sentence and keeps the full text behind a
  disclosure. A correct diagnosis rendered as a wall is indistinguishable from
  silence. Also: a zero-card payload gets a NAMED state that distinguishes "the
  store is empty" from "cards exist and none are scoped to you" — the server
  already separates those and the page was discarding the distinction.
- **The BACKLOG nudge said "untouched" while ageing by `deferred_at`** (#884).
  Two different predicates in one sentence, so a card deferred a month ago and
  worked an hour ago was reported as untouched for over a day. The clock is
  deliberately unchanged: `last_activity` measures whether anyone LOOKED,
  `deferred_at` measures how long it has WAITED, and the sweep is about
  waiting. A touch is not a start.
- **Tolerated-value warnings reach the writer who caused them** (#881), and
  name the side they fired on (#878).
- **`update_task` and `add_task` declare the row they touched** (#872).
- **A snapshot whose DM export disagrees with the live sidecar is refused**
  (#585).

### Changed

- **The store plugin declares the card document and registers the column
  rules** (ADR-0018 D1, #877, building on #835). `TASK_FIELDS` declares exactly
  the card document (JSON / LAST_WRITER_WINS) and `id` (IDENTITY / IMMUTABLE);
  the ~29 typed columns that duplicated the document moved to a promotion
  register that keeps every rationale, so "promote one at a time, with a stated
  reason" has something to promote FROM.
- `_db_mirror` and `_store_mutate` split at the 512-line ceiling (#879).
- In-wheel docs are built at release instead of pushed to a protected branch
  (#876).

## [0.42.0] - 2026-08-16

### Fixed — two alarms that could not fire, and one that fired on the wrong clock

- **The backlog nudge measured the last touch, not entry into the backlog.**
  `detect_pending_backlog` passed no `clock` and silently inherited
  `_age_hours`, which reads `last_activity`. So commenting on a rotting
  deferred card reset its own backlog alarm for a day — the exact hazard
  `_store_clocks` names in the module that WRITES `deferred_at`: "key any of
  them on `last_activity` and the sweep that reads it becomes SILENCEABLE BY
  TYPING."

  The ruling already existed forty lines above: `detect_blocked_external`
  passes `clock=_blocked_age_hours` and its docstring spells out the same
  reasoning. The backlog sweep sat below that paragraph and kept the touch
  clock, while `deferred_at` was written on every entry and read only by the
  CLI triage surface — the right fact in the wrong lane.

  Adds `_deferred_age_hours`. An unstamped card is aged by
  `min(created_at, last_activity)` — the OLDEST evidence, which is not a
  fallback: a fallback lets a touch make a card look FRESHER, a minimum can
  only make it look OLDER, so typing still cannot silence the alarm while
  genuinely old cards stop being dropped.

  Measured across 1854 deferred cards before landing: 1193 nudged under the
  old clock, 1354 under the new, **and zero cards lost coverage**. Monotonic
  by construction — entry into the backlog cannot postdate the last touch.

- **A card leaving `done` kept its completion stamp.**
  `clear_completion_stamp` had exactly one production caller (`reopen_task`)
  while its own docstring said "call this from ANY transition that takes a
  card OUT of `done`". Every other exit went through `update_task`, which
  never called it. Since the throughput surfaces aggregate solely on
  `completed_at` and never read `status`, a stamped-open card counted as
  delivered work forever WHILE ALSO nagging its owner as backlog.

### Changed — the digest states the predicate it counted

- **`BACKLOG: N …` now names its clock and its owner field.** One question —
  "how many backlog cards does this owner have" — produced four different true
  answers on one database: 62 from the sweep, 103 from `last_activity > 24h`,
  163 from `deferred_at > 24h`, and 583 from a reader on a stale replica. None
  of them disagreed; they were four predicates wearing one sentence.

  The field name is READ from the clock (`BACKLOG_AGE_FIELD`) rather than
  written as prose, and a test pins the printed name to the key the clock
  looks up, so the message cannot outlive the behaviour it describes.

### Fixed — gates and guidance

- The quality gate no longer passes `--no-version-check`; measured
  byte-identical output with and without it, so the flag silenced "is the rule
  corpus current?" and bought nothing.
- The PS-140 cross-package gate covered 3 of 7 cases and skipped what it
  exists to catch.
- The cardsync compare-and-set advice named the one door that is locked.
- Ten boot-read skill files told every agent the store is "a SQLite database"
  while `resolve_store` reports `backend: postgresql`. They now name no engine
  and point at `resolve-store`, because naming an engine in prose is a guess
  about someone else's deployment.

## [0.41.0] - 2026-08-16

### Removed — BREAKING

- **The pre-rename compatibility surface is gone.** The import shim, the stub
  distribution, the second console script, the deprecated env-var prefix and
  the legacy skills directory are all deleted. Operator directive, verbatim:
  the old name「なんて使いません」and the migration is to be hard rather than
  incremental. Anything still importing the retired module, invoking the
  retired console script, or exporting the retired env prefix now FAILS rather
  than silently resolving.

  Consumers must import `scitex_cards`, invoke `scitex-cards`, and export
  `SCITEX_CARDS_*`. There is no transition window; that was the point.

### Fixed — defects the removal exposed

A mechanical rename leaves no mechanical trace: it produces code that reads
correctly and means less. Across roughly 1300 replacements, 37 were wrong and
exactly ONE announced itself as a test failure. These were found by asking what
each string used to DISTINGUISH.

- **`mcp install --apply` deleted the entry it had just written.** The retire
  step's `LEGACY_CLI_NAME` collapsed onto the current name, so `del
  servers[LEGACY_CLI_NAME]` removed the live server. A config-destroying bug,
  invisible to a search for the old name because the old name was what had gone.
- **Plugins and delivery channels were discovered twice.** Two
  `LEGACY_ENTRY_POINT_GROUP` constants collapsed onto their current
  counterparts, so each scan ran over the same group twice.
- **The git→card hooks had silently stopped recording.** `.githooks/_lib.sh`
  invoked a console script deleted in the same sweep; the hooks fail soft by
  design, so nothing said so.
- **`.gitignore` whitelisted the pre-rename runtime directory** while the live
  one is `.scitex/cards/`, so `.scitex/*` swallowed it whole — git does not
  descend into an excluded directory, so no `!` rule underneath could apply.
- **A version fallback that could not fall back**, in `__init__.py` and
  `_min_client_version.py`: both name lists collapsed to one string repeated,
  so the second lookup could only re-raise the first's error. In the floor
  check that resolves to "too old" against any minimum.
- **The reachability fixture stopped reproducing the outage it pins.** It was
  built from two names a rename merged into one, so `ok is False` became `True`.
- **A leak guard silently narrowed.** `tests/conftest.py`'s real-store
  candidate list collapsed to two duplicated paths.
- **The JobSpec gate added in #858 inverted.** It held the retired name in a
  constant; the sweep rewrote it, so it began asserting that no JobSpec
  contains the CURRENT name. Rewritten from a blocklist to an allowlist — every
  `scitex-<pkg>` token in a load-bearing field must name THIS package — which
  is rename-proof, also catches a typo'd sibling package, and now carries the
  positive control the blocklist lacked.

### Changed

- **The skill bundle fits its budget.** `SKILL.md` is an index again (318 → 113
  lines); it had inlined a whole leaf that the audit separately flagged as
  unreferenced. Oversized leaves split into `31_fleet-ports-sync-and-citation`,
  `43_consuming-agent-schema-and-crud`, `44_consuming-agent-coordination`,
  `45_blocker-taxonomy` and `46_task-harvest-cadence-and-routing`, all linked.
- **A lossy pattern removed from the docs agents read.** The consuming-agent
  guide said there was no `comment` verb and told agents to hand-roll the
  append through `update_task(comments=[...])` — which drops any comment
  another agent added between the read and the write. The verb has existed
  since #144.


## [0.40.0] - 2026-08-15

### Added

- **`scitex-cards dev list-undelivered` — measure the rail instead of asking
  peers.** After a restart, `a2a_inbox` is an in-memory buffer: it returns empty
  whether or not anything was sent, so it cannot distinguish "nothing arrived"
  from "I lost my copy". Answering "did I miss anything?" from it previously led
  to asking two other agents to resend messages the durable rail already knew
  were delivered. This verb queries `channel_events` for undelivered inbound and
  outbound rows and NAMES them — id, peer, timestamp, first line — so a lost
  message can be resent without asking anyone what they said.
  - A **positive control runs first**: if the rail is unreadable or empty, the
    answer is `CANNOT_TELL`, which is NOT a pass. This matters because the
    per-agent `state.db` shard *has* the `channel_events` table and *is* empty,
    so a query against it succeeds and reads exactly like an all-clear. The verb
    therefore reads the top-level rail and deliberately ignores
    `$SCITEX_AGENT_CONTAINER_STATE_DB`, which points at that empty shard.
  - `$SCITEX_CARDS_RAIL_DB` overrides the location; when set it is the *only*
    candidate, because a silent fallback would be indistinguishable from the
    override working.
  - Exit codes: 0 clean, 1 undelivered found, 2 cannot tell.

- **Every comment now carries a globally-unique id.** Comment elements
  previously carried no `id`, which blocks declaring `comments[]` under
  `MergeRule.APPEND` for multi-host replication: `_element_id` raises on an
  id-less element, and the only id the schema offered was an autoincrement
  primary key — which upstream names as *worse* than no id, since two hosts both
  mint `id=8` and replay silently drops one. Ids are now minted at creation from
  all nine append sites (the `_store_*` modules, the CLI loop, and the
  `reopen`/`resolve`/`stale` handlers).
  - **This is half of two.** Existing threads are not backfilled; 1,137 of 9,506
    live comment elements still lack an id. `comments[]` cannot be declared
    APPEND until that backfill lands.

- **Phone view of your own cards** — `/me` and `/me/cards`.

### Changed

- **`/graph` no longer ships the `mermaid` key — 8.10 MB of a 21.11 MB payload,
  38.4%.** The server was serializing the whole board a second time as a
  flowchart string that no live surface read: `board_v3` builds its own mermaid
  source client-side from `STATE.graph.edges` so it can respect the visible
  filter set, and the server's copy was the unfiltered diagram nobody drew. The
  `/legacy` view and the `render-graph` CLI call `build_mermaid` directly and are
  unaffected. Measured before and after against the live store: 21,109,219 B ->
  13,009,914 B.
  - The board refetches the whole payload on nearly every 5-second poll, so this
    is per-poll rather than per-page-load.

- **The board is a resident service, and its absence is loud.** On 2026-08-14
  the operator opened the board and got a bare `ERR_CONNECTION_REFUSED`:
  nothing was listening on `:8051` on any host, and the board had been serving
  nowhere for hours. Every other instrument was green — the card store was
  resident, the GUI agent was alive with a fresh heartbeat — because the only
  thing that had ever started the board was a human running a startup script by
  hand. A process nobody is responsible for starting has no failure mode, only
  an absence, and an absence is invisible until someone goes looking. That same
  night the board had been declared the fleet's primary channel.
  - `scitex-cards board install-service` writes a systemd **user** unit
    (`scitex-cards-gui.service`) and prints the `systemctl --user` commands.
    Operator-gated exactly like `notifyd install-unit`: it never runs systemctl
    itself. The gate is on INSTALL, once per host — not on every boot. It sits
    on `board`, this package's own noun, and NOT on `gui` — `gui` is the
    ecosystem-standard four-verb group (`open`/`serve`/`status`/`stop`) shared
    with figrecipe / scitex-writer / scitex-scholar so one startup script
    drives every SciTeX GUI, and a shared convention each package extends
    privately stops being shared. `tests/test_cli_gui.py` pins that group at
    exactly four verbs and caught the first attempt.
  - Check `loginctl show-user $USER -p Linger` before believing any of this on
    a headless host: without lingering a user unit starts only at interactive
    login, so the board would sit enabled and dead through every reboot — the
    same silence, reached by a different road. `scitex-compute-04` has
    `Linger=yes`.
  - `Restart=always`, not the notify daemon's `on-failure`: the board's
    ABSENCE is the fault however it went away, so a clean exit must still come
    back. `ExecStart` carries `--force`, because `gui serve` refuses to start
    against a live pidfile and one leftover would otherwise keep the unit down
    forever — reproducing the outage with extra steps.
  - Bound to `127.0.0.1` on EVERY host. The operator ruled out one
    VPN-reachable board: 「一つの場所を見ると単一障害点になったり、vpn が切れる
    と見れなくなったりしてしまいます」. What travels between hosts is the DATA,
    over the per-host `:55432` Postgres and its existing sync.
  - No `Environment=` line: verified under `env -i` that the store resolves
    from `~/.scitex/cards/config.json` alone, so the unit cannot crash-loop on
    the unconfigured-store guard — and the store keeps exactly one identity.
  - New `gui_resident` health check, three-valued and DELIVERY-severity. It
    reads a declaration (is a unit installed) before a liveness (is anything
    listening): declared-and-silent is a FAILURE naming the restart command,
    serving-without-a-unit PASSES but reports that the board will not survive a
    reboot, and neither-declared-nor-serving is UNKNOWN — named in the summary,
    never a silent pass, because a check that failed on every container in the
    fleet would be switched off within a day. It probes the port the installed
    unit declares, so a custom-port host is not told a confident story about
    `:8051`. See `docs/ops/resident-board.md`.

### Changed

- **systemd unit rendering is shared, not copied.** The absolute-`ExecStart`
  resolution — which raises rather than write a unit guaranteed to die at
  `203/EXEC` — lived only in the notify daemon's installer. It now lives in
  `scitex_cards._systemd_unit` as a `UnitSpec` + installer that both the daemon
  and the board use. Every public name in `_delivery/_systemd.py` is unchanged
  and its 35 tests pass untouched; this is a de-duplication, not a re-design.
  A second unit copying that routine would have inherited whichever version its
  author happened to read.

## [0.39.0] - 2026-08-14

**The notification rail finally reaches the database everyone else is on, and
a board you already started stops being a wall.**

### Added

- **`--force` on `gui serve` and `board start`** — stop a board that is already
  running, then serve. The operator asked for it after `scitex-cards gui serve
  --force` answered "No such option '--force'": 「stop するのめんどくさいので。
  あとはなければ通すように。stop ではないので。」 Both halves are load-bearing.
  With an incumbent, `--force` stops it (through the same resolve-by-pidfile,
  then cmdline-verified-port path `stop` uses — never by port alone) and serves.
  With NO incumbent it simply serves: `--force` is a takeover, not a stop verb,
  so an absent board is the ordinary case and not an error. Without `--force`
  the existing refusal is unchanged, and now names the flag as the remedy.
  `--force --dry-run` prints which pid it WOULD stop and kills nothing; the
  unconfigured-store guard still runs first, ahead of both the kill and the
  bind. A stop the kernel REFUSES raises instead of binding a port that is
  still held, naming the pid and the command to identify its owner. (#838)

### Changed

- **Notifications are stored where the cards are.** The inbox rail resolved its
  own target — `runtime_dir(store)/cards.db`, a SQLite file *per container* —
  while every card write went to PostgreSQL. Two agents on two hosts therefore
  enqueued into two different files that nothing ever reconciled: measured on
  2026-08-14 the laptop's copy was 5.1 MB, compute-04's 147 KB, and the
  PostgreSQL `notifications` table held 0 rows, with 41 of the operator's
  notifications unread in a single container. A notification enqueued by one
  agent could not reach anyone else, by construction. `inbox_target(store)` is
  now `resolve_store_target(store)` and every rail call site follows the
  configured store; `inbox_db_path` survives only for the migration tooling,
  which by definition must still read the old file. No schema change was
  needed — `notifications` already carried `recipient_id` and a `seq` ordering
  column. Verified end to end against the live store: enqueue → 1 row in
  PostgreSQL → the same id read back through `poll_inbox`, the first row that
  table has ever held. **If you are upgrading from ≤0.38.0, notifications still
  sitting in a per-container `cards.db` are not migrated by installing this
  release** — the `no_stranded_backlog` health check added in 0.38.0 is what
  tells you whether you have any, and `_inbox_migrate` is what moves them.
  (#779)
- **The SIGTERM → poll → SIGKILL sequence moved out of `board stop`'s command
  body** into `stop_board_process` in `_cli/_board_proc.py`, so the three doors
  onto the board lifecycle escalate identically instead of hand-rolling a copy
  each. It answers with a validated `StopOutcome` dataclass whose `stopped` is
  three-valued: `SIGKILL_SENT` reports `None`, because SIGKILL is sent and the
  exit is never re-checked, and calling that "stopped" would report an
  observation the code does not make. `board stop`'s messages, exit codes,
  dry-run text and pidfile handling are unchanged, and are now pinned by exact
  string assertions rather than substrings. (#838)

## [0.38.0] - 2026-08-14

**A cutover that moved the rail and left the backlog.**

### Fixed

- **149 undelivered notifications were stranded by the SQLite → PostgreSQL
  cutover**, and nothing noticed for three days. The rail moved on 08-11; the
  backlog did not — 0 of the 149 unseen rows existed in PostgreSQL. 130 were
  addressed to the operator, and 134 of 149 were DMs to people rather than card
  churn. Among them: an answer the operator had asked for, written 35 seconds
  after he asked, and another agent's retraction of a false outage report. He
  concluded this agent was dead; it was not, its reply was in a file nothing
  read any more. All 149 recovered through the package's own `enqueue` path,
  pre-image archived, verified per recipient.

### Added

- **`no_stranded_backlog` health check** — DELIVERY severity, three-valued.
  Detects notifications left in a backend the rail no longer reads. Validated
  on the real incident (red at 149, green after remediation) rather than on a
  fixture. An unreadable legacy file reports `unknown`, never `ok`: collapsing
  "I could not look" into "nothing is stranded" is exactly how the original
  defect stayed invisible. (#836)
- **`register_user` accepts a caller-supplied deterministic id**, so two hosts
  minting the same user independently converge instead of forking. (#834)

### Note on this file

**0.37.0 and 0.37.1 shipped with no CHANGELOG entries.** Both releases exist as
tags and on PyPI; only this file is missing them. Recording the gap rather than
resuming as if the history were continuous — a changelog that silently skips
two versions is worse than one that says which two.

## [0.36.0] - 2026-08-11

**Four things that reported success while doing something else.**

Every fix here was found by a peer measuring rather than by a test failing, and
each one had a correct-looking implementation. That is the theme: none of these
were wrong code. They were right answers to questions nobody had asked.

### Fixed

- **Five verbs changed a card without aging it.** `complete_task`,
  `resolve_task`, `reopen_task`, `restore_task` and `set_edge` mutated a card
  without advancing `last_activity` — the field every last-writer-wins
  reconciler orders by. Reported by scitex-dev after two cards proved
  unorderable across three hosts; an AST audit of all 16 card-persisting
  functions found five, not one. The failure does not lose a CARD, it loses a
  COMPLETION, in the direction that looks like ordinary reconciliation.
  `restore_task` was the worst: `delete_task` stamps when it tombstones, so a
  restore replaying the pre-delete snapshot wrote a row strictly OLDER than the
  tombstone it reverses — an Undo a second host would undo. (#795)

- **Deleting a blocked card failed outright.** `delete_task` flipped `status` to
  `cancelled` without clearing `blocker`, which `_validate_tasks` refuses — and
  because validation covers the whole document, one such card blocked every
  other write in the same save. The same rule `complete_task` learned on
  2026-08-01, never applied to the other closing verb. (#795)

- **`unconfirmed` described the fetched page, not the inbox.** It also keyed on
  `seen`, which the channel drain advances when it pushes. Two independent
  causes, and fixing either alone leaves the field useless: the default page is
  unseen-only, so after the drain it is EMPTY and the field was empty with it —
  by construction, whatever column it read. A consumer had to query the rail
  directly to find a notification it had just acted on. (#797)

- **`ack_notifications` told a first delivery "you already did this".**
  Classification was read off the cursor advance, which the drain had already
  performed, so `_inbox.ack` honestly reported flipping nothing and every first
  ack of a pushed record returned `already_confirmed`. Measured across two
  agents: twenty acks, twenty `already_confirmed`, zero `confirmed`. (#799)

### Changed

- **Foreign keys are declared `DEFERRABLE INITIALLY DEFERRED`.** Under directed
  replay a foreign key is an ORDERING constraint: a child arriving before its
  parent must be checked at COMMIT, not at statement. `NOT DEFERRABLE` was never
  a decision — it is what an inline `REFERENCES` gives you when nobody thinks
  about ordering. This fixes stores created after it; existing stores need a
  migration rung, tracked separately. Raised by scitex-db, who declined to run
  their own ALTER because two reconcilers with different target shapes oscillate
  forever with both logs reporting success. (#796)

- **`ack_notifications` response semantics.** `confirmed` and
  `already_confirmed` now mean what their names say, keyed on the confirmation
  stamp rather than on the cursor. A caller that treated `already_confirmed` as
  "nothing to do" will now correctly see `confirmed` on a first delivery.

### Added

- `is_confirmed` / `unconfirmed_ids` in `_inbox_receipt` — the single definition
  of "has the recipient confirmed this?", consumed by the three surfaces that
  must agree. The rule was already written down, correctly and in full, at
  `_inbox_confirm.py:218-224`, and it protected exactly the line it was attached
  to. A comment cannot travel; a predicate can. (#797)

## [0.35.1] - 2026-08-10

**A refused compare-and-set must destroy nothing.**

Found by reading the *caller* of the code 0.35.0 shipped, before any caller
could reach it.

### Fixed

- **`_write_card` compared the revision too late** (#792). It DROPS a card's
  comments, roles and outbound edges before upserting — load-bearing, because
  comments key on a sequence and re-inserting without clearing duplicates every
  one of them on every write.

  That drop sat in FRONT of the revision guard added in 0.35.0. A losing
  compare-and-set would therefore have:

  1. deleted the card's comments, roles and outbound edges
  2. hit the guard and skipped the upsert
  3. reported `revision_skipped=1` — *"I changed nothing"*

  while the winner's comments were already gone. A lock that destroys the data
  it protects and then reports success at protecting it is worse than no lock,
  because the caller has no reason to look.

  The revision is now read and compared BEFORE anything is dropped. The `WHERE`
  clause inside `_insert_tasks` remains the real guard against the read-to-write
  race; the pre-check only ensures the destructive half never runs for a write
  that was always going to be refused.

**This was latent in 0.35.0, never live.** No caller passed `expected_revision`
through `_write_card`, so no published version could reach the destroying path.
It would have become real the moment the row-level `update_task` did — which is
the next change queued. Fixed before that, not after.

### Why 0.35.0's tests did not catch it

They asserted a losing write leaves the card's **title** intact. The title lives
on the `tasks` row, which the guard genuinely protected. The comments live in a
**child table cleared before it**. Testing the row you are thinking about rather
than the blast radius of the operation is how this class of defect ships.

Six new tests pin the blast radius, including one asserting comments are NOT
duplicated on an accepted write — which pins why the drop exists at all, so it
is not "simplified" away later.

## [0.35.0] - 2026-08-10

**The revision lock is finally asserted.**

`tasks.revision` has existed since schema v6 and been auto-incremented by v7's
`tasks_bump_revision` trigger. **No writer ever compared it.** So a card write
that raced another writer simply won, and the losing side was discarded with
nothing raised anywhere.

Verified independently in two corpora before a line was written — the installed
0.33.0 wheel and the repo at develop: `revision` appeared only in
`_db_migrations.py`, `_schema_shape.py`, `_schema_current.py` and
`_pg_triggers.py`, with no `WHERE ... revision = ?` in any write path.
scitex-dev measured the same, plus a live histogram over 3,722 rows confirming
the column is correct and moving — it simply was not read.

Not theoretical: scitex-dev lost an edit to a concurrent writer and **reported
it done**, because nothing told them otherwise.

### Added

- **`_insert_tasks(..., expected_revision=N)`** — compare-and-set on the
  row-level write path (#790).

  **A lost race is REPORTED, not raised** — `counts["revision_skipped"]` and
  `counts["revision_found"]`. A reconciler counts lost races as ordinary
  outcomes; an exception would make routine concurrency indistinguishable from
  a fault and force catch-and-continue around the happy path. (scitex-dev's
  correction; the first cut raised, and they were right that it was wrong.)

  **Misuse still raises** — a batch, or `replace=False`. Those are capability
  gaps, and a capability gap silently tallied as a lost race is precisely the
  miscount this split prevents.

  **`revision_found` is three-valued**: the revision now in the row, or `None`
  when the row is gone. "Someone wrote past me" and "the card was deleted"
  need different responses.

  **Opt-in by construction, and that is load-bearing.** `_migrate_v6_to_v7`
  records that REJECT semantics for this lock were *ruled unusable* — an UPDATE
  from a writer ignorant of `revision` would ABORT, so fleet writes would fail
  until every container was current. With `expected_revision` unset, no clause
  is emitted and the SQL is identical to before. Only a caller that opts in can
  fail; a test pins that.

  One hole closed along the way: `ON CONFLICT DO UPDATE ... WHERE` only fires
  when a conflicting row exists, so a compare-and-set against a **deleted** card
  would have silently re-created it. A pre-read reports row-absent as a skip;
  the `WHERE` still provides the atomicity.

### Known limitation

`update_task` is **whole-document read-modify-write**, so it does not yet accept
`expected_revision` — putting it there would guard the caller's card while
overwriting every other card in the same document, which is worse than the
last-write-wins it replaces because it would carry the appearance of safety.
The public verb arrives when `update_task` becomes row-level; tracked as
`cards-update-task-is-whole-document-rmw-blocks-row-level-compare-and-set-20260810`.

Scope note: `update_task` holds `_store_lock` across its read-modify-write, so
within a single host writes ARE serialised. The exposure this release addresses
is **cross-host**, where no shared lock exists — which is the multi-host mode
now being built.

## [0.34.0] - 2026-08-10

**The notification rail can finally cross a host, and a store stops lying about
which store it is.**

The operator asked twice — 2026-07-30 and again 2026-08-09 (「通知は cards.db????
…ポスグレを使っているはずなのになぜまだ sqlite を使っているのか」) — why
notifications were still SQLite when the card store had moved to PostgreSQL. The
honest answer is that the mission card claimed the migration was COMPLETE while
only half of it was. Measured on the live rail the day of this release:

```
/home/agent/.scitex/cards/runtime/cards.db   table `inbox`
  rows                324      unseen  133
  recipient  operator 123      unseen  123   <- not one ever consumed
  recipient  every agent whose consumer runs on this host: unseen 0
```

The split is binary with no middle case: delivery works exactly when a consumer
is co-located with the file, and fails completely when it is not. Nothing errored
anywhere — every write succeeded, every read answered honestly, and the only
symptom available to anyone was a human saying nothing arrives.

### Added

- **A shared PostgreSQL inbox, so a notification can cross hosts** (#780). The
  rail's backend is selected through the same seam as the card store rather than
  being hardcoded to a local file. This is the mechanism the 123 stranded
  messages needed; carrying the existing rows and giving the operator's inbox a
  consumer are the remaining halves, tracked separately.

- **A store identity a COPY cannot carry** (#784). `StoreInstance` reads
  PostgreSQL's `system_identifier`, which is per-cluster and does not survive a
  file copy, so a store restored from a snapshot is no longer mistaken for the
  original. The verdict is three-valued by construction — MATCHES / DIFFERS /
  CANNOT_TELL, with a validator that refuses to let two UNKNOWNs compare equal —
  because collapsing "I could not tell" into either pole is the bug this type
  exists to prevent.

### Fixed

- **`summarize_tasks` named a store it had not read** (#775). It reported
  `/home/agent/.scitex/cards/tasks.yaml` — a path that does not exist on disk —
  while serving 3709 cards out of PostgreSQL. Reported independently twice:
  by scitex-logging on 2026-08-04, and by scitex-storage on 2026-08-10 while
  reconnecting after an outage and asking the exact question this verb exists to
  answer ("am I on the real store, or a local shadow?"). The label said local
  YAML shadow; the data was canonical PostgreSQL. During an outage that is the
  moment someone starts repairing a store that was never broken. The field now
  reports the backend that actually served the read, and a test pins it to
  `resolve_store()` so the next backend change cannot silently reopen it.

### Changed

- **The quality gate stops failing every pull request on inherited debt** (#788).
  `PS-108` / `PS-108b` (12 prefix clusters; 133 flat `.py` files against a
  threshold of 15) are structural debt no current PR introduced, and they were
  red on all 18 open PRs. PR #785 *reduces* the count to 125 — exactly the remedy
  the rule prescribes — and was failed for doing so, because `--new-only` keys
  findings by a rendered line that contains the tally. A gate every PR fails is
  as useless as one that cannot fail.

  Both rules are skipped **per-rule, in `.scitex/dev/config.yaml`, each with a
  written reason and an explicit deletion condition** tied to the tracking card —
  never a blanket flag. Verified scoped rather than assumed: a strict local run
  on the change still reports 233 findings across 12 other rules and still exits
  1. Those rules are earning their keep — they are what caught #780's leaked test
  connection and #785's missing test mirror. The upstream keying defect is
  reported to scitex-dev.

## [0.33.0] - 2026-08-10

**A workspace identity is SEGMENTS, and provisioning is its own verb.**

scitex-hub had been blocked on these two since 2026-07-30. Both are additive
with zero callers in `src/`, so nothing changes for any existing consumer — a
single-segment caller sees exactly the previous behaviour, which the unchanged
existing test suite is the evidence for.

### Added

- **`resolve_workspace_store(*segments)` takes a structured identity.** hub's
  tenancy is two-dimensional — a tenant is `(owner, project)`, and the owner is
  itself two namespaces — and flattening that into one separator-joined slug
  COLLIDES. hub measured it before building against it:

  ```
  owner "alice-my" + project "project"    ->  alice-my-project
  owner "alice"    + project "my-project" ->  alice-my-project
  ```

  Two tenants, one identity, one store. *Any* separator has this property as
  long as it is legal inside either component, so the encoding is unsatisfiable
  rather than merely bad — and under ADR-0017 (*a tenant is a STORE, not a row;
  the authority boundary IS the handle*) an identity collision **is** a
  cross-tenant read, reached through the sanctioned primitive rather than around
  it, which is worse because it looks compliant.

  Segments now join as **path components**, so there is no separator to be
  ambiguous about and nothing to escape. Each segment keeps the full allowlist,
  so traversal stays impossible per segment. Uppercase is **refused, not
  folded** — folding would map `Alice` and `alice` to one store, the second
  collision arriving through the fix for the first.

- **`provision_workspace_store(*segments)` — the sanctioned creation path, and
  the only one.** `resolve` deliberately refuses to create, because a resolver
  that creates on miss turns a typo into a new empty tenant and the caller
  cannot tell that from a workspace that genuinely existed. But refusing with no
  creation path anywhere leaves every new tenant at a fail-closed raise, which
  is the pressure that eventually softens the resolver.

  It creates the **database**, not merely the directory. An earlier draft made
  only the parent directory while `resolve` tests for the file, so provision
  returned success and the very next resolve raised `StoreNotProvisionedError` —
  precisely the first-contact failure hub identified. Its own first test run
  caught it. A provision that does not satisfy the resolver is a rename of the
  problem.

## [0.32.4] - 2026-08-10

**Six fixes had been merged and were sitting in no release. This is that
release, and the reason it was late is worth recording.**

`git tag --contains faae0e03` returned empty. The board 500 that scitex-hub
reported on scitex.ai had been *fixed* since the afternoon and shipped to
nobody; the `set_edge` fix that was blocking scitex-db's migration was in the
same position. Both agents were waiting on work that was already done. The
release was held by one test, and that test was a stopwatch.

### Fixed

- **A database this board cannot reach is an outage, not "nothing here"**
  (#772). An unreachable database and a store that was never provisioned were
  the same answer, so scitex.ai's board reported an empty task list where it
  should have reported that it could not read its store. The two are now
  distinguished by type; only the second is a 404.
- **`set_edge(action="remove")` can scrub an edge whose target is gone**
  (#773). Removal validated that the target still existed, so the one verb for
  cleaning up a dangling edge refused precisely when the edge was dangling.
- **`gui serve` refuses an unconfigured store instead of inventing one**
  (#774). With no DSN configured the board did not fail — it silently read a
  local SQLite file that had stopped being written on 2026-08-02 and served it
  as current. A silent fallback to a stale store is the failure mode ADR-0016
  exists to forbid.
- **The board's DM thread list reads the database, not `threads.json`**
  (#776), **and so does the thread pane** (#777), **and so does the agent-side
  `dm_list`** (#778). `threads.json` is a PER-HOST file: the operator's board
  showed only threads from agents on the same machine, so five agents on
  scitex-compute-04 were invisible while all 4150 messages sat in the shared
  store the entire time. Three separate readers had to be moved because each
  looked complete on its own.

### Security

- **A caller may not name the store on a board it can reach** (#782).
  `read_store` fell back to the caller-controlled `?store=` query parameter
  unconditionally. On the card path that fallback is inert — `load_tasks`
  discards the resolved store and reads the one canonical database — but on
  the **DM** path it is live: the value becomes a `cards.db` path and is
  opened. So the one surface that honours the parameter was the one surface
  with no guard, while the surface everyone reasons about was safe only by a
  different defect. The query channel is now bounded by
  `settings.PUBLIC_HOST`, already "the ONE switch that says 'this board is
  reachable from the internet'": admitted when empty (loopback board, test
  suite), refused when set, and refused when **absent** — because absence is
  the signature of running inside a host application's settings, and "cannot
  tell" must not read as "not exposed".

  This is not a lenient read policy beside a strict write one, which
  `_store_canonical_read` forbids by name. Reads converge on the write rule
  wherever it matters and keep the legacy seam only where the deployment has
  provably one tenant and one caller.

### Changed

- **The store-write verify asserts its mechanism rather than a stopwatch**
  (#781). The test proving the event-scan verify is cheaper than the
  `safe_load` construct-reparse it replaced compared two wall-clock
  measurements, and failed CI at 0.3626 s vs 0.3274 s — a 35 ms margin on a
  shared runner, with 5919 other tests passing. Speed was never the property;
  *not constructing the document's object graph* is, and the speed is its
  consequence. It now asserts that directly, with a document whose bytes are
  well-formed YAML but whose tag no `SafeLoader` can build: parsing reaches
  stream-end, constructing raises, so a verify that accepts it demonstrably
  constructed nothing. Mutation-tested — reverting the implementation to a
  constructing `safe_load` makes it fail, which the timing assertion would
  have caught only on a quiet machine.

## [0.32.3] - 2026-08-06

**An `agent:<id>` scope names an OWNER, not a lens — and the instruction that
said otherwise shipped in every agent's system prompt.**

`list_tasks` compared scope by exact string, while this package's own MCP
instructions told every agent to *"call list_tasks with `scope='agent:<id>'`
**to see only your slice**"*. That phrasing does not suggest a query; it asserts
an equivalence, with tool authority, on first contact. It was false. A card a
**peer** filed against you under `fleet`, `ecosystem`, or no scope at all — which
is what most filings do — was excluded from "your slice".

Measured on the canonical store: **441 open cards** owned by an agent were
invisible to that agent's own scoped query, across **39 owners**, **398** of them
for the single reason that nobody set a scope when filing. The `lead` agent had
12 hidden and **0 visible** — an empty board while it held work.

Reported independently by **scitex-agent-container** (69), **scitex-ui** (3, all
blocked on an operator decision) and **scitex-app** (4). None were looking for
it; two found it only after hearing about the first, which makes the discovery
mechanism gossip rather than tooling. The failure is silent by construction — a
filter returning fewer rows is indistinguishable from a board holding fewer
cards.

`_in_scope` now reads `agent:<id>` as an owner: a card assigned to `<id>`, or
carrying it in `agent`, is that agent's work whatever lens someone else filed it
under. `fleet`, `ecosystem` and project scopes are genuine views and still match
exactly.

Validated against the canonical store by importing the real predicate rather
than reimplementing it: **461** open cards newly reach their owner, **0** become
visible to a non-owner, **0** previously-visible cards are lost, **0** change to
lens membership. The three zeros are what separate this from the broader
proposal — surface every unscoped card to everyone — which would have made the
first two non-zero by design and buried each agent under other people's work.

Both halves changed together, in that order. The instruction now says the scope
names *you* and points at `list_tasks(assignee=…)` as the direct question the
two should agree on — a sentence that is only true because the filter changed
first. Changing the wording alone would have moved the failure into the
tool-result size cap, which two of the reporters had already hit that same
session; an agent that hits the cap narrows its query, which is this bug again.

**The instructions no longer name a storage backend or a default store path,**
and a test now refuses any that do.

Found while verifying the fix above by *reading the rendered string* rather than
the diff. The same instructions carried a second false claim, untouched by the
scope work: *"The canonical store is the SQLite database at `$SCITEX_CARDS_DB`
(default `~/.scitex/cards/cards.db`) — that path is the SOLE store identity."*
After the PostgreSQL cutover both halves were false at once, and the named path
is the **abandoned** pre-migration file — still on disk, still holding thousands
of real cards.

That sentence is what misled this package's own maintainer earlier the same day:
the first round of the figures above was measured against that file, and reached
three docstrings, a pull-request body and a card comment before a positive
control caught it — looking up a card created in the same session, which came
back NOT FOUND, proving the reader wrong rather than the data. The stale file
answered plausibly and reproduced a reporter's own count exactly, which is
precisely what stopped the checking. A store that answers plausibly is the
dangerous kind of wrong.

The sentence had rotted twice (YAML → SQLite → PostgreSQL) because it
**restates** what `resolve_store` already answers correctly, and nothing
asserted it. It now names only the question and the verb that answers it, and
`test__mcp_instructions_names_no_backend.py` fails the build on any backend name
or default path in either branch of the renderer — while separately requiring
that `resolve_store` stay named, so the guard cannot be satisfied by deleting
the sentence and leaving an agent no way to learn which store it is on.

That test earned its place immediately: the first replacement sentence said "a
SQLite path or a PostgreSQL URL, depending on the deployment" — naming both
backends inside the sentence that says not to — and the guard caught it before
it was committed.

The identical claim still ships in `scitex-cards --help` and in `_db.py`. Both
are carded rather than fixed here: `_cli/_main.py` is already 520 lines against
a 512-line cap, so the repo's line-limit hook refuses any edit to it until it is
split, and a module refactor does not belong in a release.

## [0.32.2] - 2026-08-06

**A board READ honours the trusted store attribute, and one module now owns
the decision.**

The write path stopped trusting `?store=` on 2026-07-28, after scitex-hub found
in design review that a request parameter was choosing which file got written.
The read path kept trusting it alone for nine more days — in the function
immediately above it — because the same two `request.GET.get("store")` lines
had been hand-copied into `views.py` and `handlers/dm.py`, and only one was
ever revisited.

That asymmetry was not merely untidy: it forced a neighbouring package into a
worse design. Since reads consulted the query *only*, scitex-hub's tenancy
middleware could not simply set `request.scitex_store` — it had to keep
**overwriting** `request.GET["store"]`, which, as its own comment says, put a
security-critical value "in the exact namespace the attacker controls", making
their injected store and a hostile one "byte-identical, indistinguishable by
construction" downstream. They were right, and the indistinguishability was
ours: the two values *are* distinguishable where one arrives as an attribute
and the other as a query parameter.

`_django/_request_store.py` now decides for the whole layer. `write_store`
accepts only the trusted attribute; `read_store` **prefers** it and falls back
to the query. The preference is the fix — once the attribute wins, a
caller-supplied `?store=` is inert wherever a tenancy middleware runs, defended
by construction rather than by a neighbour remembering to overwrite it.

The **value** does not change, only the channel. hub sets the attribute to a
`Path` while it injected the query as `str(store)`, so the attribute is
normalised to `str` and a test pins that both channels resolve identically — a
silent type change riding along with a security fix is how the next incident
starts.

The query fallback **stays**, deliberately: the standalone loopback board and
the Django suite both select a store through it, and removing it before hub
deletes its injection would drop tenancy for a release window and fall the
board back to one ambient store for every tenant. Alias first, then remove.

A build-failing AST guard refuses any `store` key taken off `request.GET` **or**
`request.POST` outside the owning module. `POST` is included though no handler
reads one — the cheapest moment to refuse a channel is before it exists. It
matches on syntax rather than on the word "store", because every docstring here
contains that word and a text check would match its own prose; and it carries a
positive control, because a matcher that has silently stopped matching and a
tree that is genuinely clean produce the identical empty result.

## [0.32.1] - 2026-08-03

**A merge must not overrule a deliberate blocker.**

`reconcile-merged-prs` treated a merged PR as evidence that the CARD was
finished. A merge is evidence about a **pull request**; reading it as evidence
about the card holds only where the card's scope is strictly its diff. Where
the card also carries verification or rollout, it closed live work — and closed
it with the confident shape, `done`, rather than surfacing a question.

Measured by scitex-hub: at 19:08Z they set a card to `blocked=dependency` whose
note opened "STATUS blocked=dependency, NOT done". At 19:30Z the reconciler set
it to `done`. Twenty-two minutes, with the note explaining why a merge is not
completion sitting unchanged in the card body. It was not merely early — the
card's closing condition was an authenticated request from the operator's phone
succeeding, and production was five commits behind including that PR, so the
route did not exist.

`blocked` leaves `OPEN_STATUSES`. Auto-closing an `in_progress` card is a
defensible heuristic; overruling a blocker is not. A blocker is the record that
someone ALREADY considered the question and decided the work cannot complete —
encoding "do not assume" is the entire job of the status. The reasoning was
already in that file, written for `deferred`, and had simply never been
extended to the status it applies to more strongly.

The quiet part, and why this is worth a patch release rather than waiting: a
closed card leaves the board, so no sweep nudges it again and the mistake is
invisible to anyone hunting for it. It also fires unattended, so it was the one
defect of its family still producing wrong states overnight.

**This release exists because the fix was merged and not running.** The
reconciler executes from the installed distribution, so #764 changed nothing
until it shipped — and it auto-closed the same card a second time at 19:45,
after the fix had merged. Merged is not deployed, demonstrated on the very
change that says so.

## [0.32.0] - 2026-08-03

**The runtime install is bare, so there is nothing left to pick wrong.**

MINOR rather than patch because the install SHAPE changes. `django`,
`scitex-app`, `scitex-ui` and `fastmcp` move from the `web` and `mcp` extras
into core, joining `psycopg`. `pip install scitex-cards` — no extras — now
produces a complete client.

That is the fix, not a consequence of it. Every hand-pickable subset was a
chance to pick the wrong one, and the 2026-08-01 fleet outage was exactly
that: `scitex-cards[mcp]` resolved cleanly and produced a client that could
not open the canonical store, while the error blamed the database. Removing
the choice removes the failure, which is the only kind of fix that survives
someone with a plausible local reason for the partial set.

The board and the MCP server are not optional capabilities in any sense a
user would recognise: the board is how the operator reads the store, and the
MCP server is how every agent writes to it.

`web`, `mcp`, `postgres` and `currency` remain as redundant aliases so the
pins outside this repo keep resolving — and they RESTATE their requirements
rather than being emptied, because an empty extra is worse than a missing
one. A missing extra warns; an empty one resolves silently and installs
nothing, so whoever was told to run it stays broken and believes they already
tried the fix (PS-214). Their removal is sequenced behind the three
`scitex-agent-container` build sites that name them.

`currency` is deliberately NOT promoted. It fails the test the others passed:
`check_currency()` is a no-op when scitex-dev is absent, so its absence names
itself, and promoting it would make a development toolchain a hard dependency
of every runtime install.

**A label must not fail the command it captions.** `scitex-cards list-tasks`
and `summary` crashed against the canonical PostgreSQL store — not on the
read, which had already returned 301 cards, but on the header line naming
where they came from, which called a resolver typed to return a filesystem
path. The refusal was correct; the call site was not. Naming a store is not
the same operation as opening one. `store_label()` renders the target with
credentials and query string stripped, and never through `Path`.

## [0.31.8] - 2026-08-02

**A login page, because the browser will not show what the header says.**

0.31.7 made the 401 name its own source — realm and body both — and `curl`
prints both. Chrome prints neither: its Basic dialog shows only "Sign in" and
the origin, the realm having been removed years ago because an
attacker-controlled realm is a phishing surface.

So the operator met a bare, unlabelled password box on his own board and could
not get in. The header was correct and invisible to the only person using it,
and it was verified with the tool that displays the realm rather than the
browser that discards it.

A browser now gets a PAGE, where the instructions can simply be on it: what the
password is, the command that reads it, that there is no username today and why,
and the warning that a prompt which cannot say where its answer lives has the
shape of a phishing prompt. Anything that is not a browser still gets the Basic
401 unchanged — two audiences with different renderers, so the mechanism follows
the audience rather than the reverse.

Status is 200 rather than 401 deliberately: a 401 carrying HTML makes the browser
open its native dialog ON TOP of the page, hiding the explanation behind the very
prompt it replaces. The session cookie is signed, HttpOnly and SameSite=Lax.

Verified end to end in a real browser — navigate, type, land on the board — not
only at the protocol level.

The username field is REMOVED and that is a stopgap, not the design. This board
has one shared password today and genuinely discards the username, so a field
that is ignored is a lie. It returns wired to per-user credentials, because
several people on one card need per-person attribution.

Also in this release: validation warnings name the store the rows actually came
from (#756), a client behind the store no longer re-runs its DDL on every
connection (#755), and psycopg is a hard dependency rather than an extra (#754).

## [0.31.7] - 2026-08-02

**The password prompt says where its answer lives.**

0.31.6 let the board demand a password before binding a public hostname. It did
not say where that password came from. The realm read `SciTeX Cards` and the
body read *"This board is password protected."* — restating the fact the user
could already see, while withholding the only thing they needed.

The operator met the consequence on their own machine: a credential dialog on
loopback, for a password they had not set, with no path from the dialog to the
secret — *"no idea what password this is, and I don't know the username either."*

**This is a security defect, not a cosmetic one.** An anonymous credential
prompt is indistinguishable from a phishing one, and a user who cannot tell them
apart is being trained to type secrets into whichever dialog appears. So the fix
is not a friendlier message, it is a **refusable** one: the challenge now names
its source, so a reader can check whether that source is theirs and decline when
it is not.

Both halves carry it — the realm the browser prints inside its dialog, and the
body it renders when the dialog is cancelled. The body additionally states the
two things the dialog cannot: that the username is **discarded entirely**
(`is_authorised` splits on the first colon and compares only the password), and
what to run to read the value. And it tells a reader who did not set the
password not to answer.

The realm is kept under 80 characters because browsers truncate long ones — a
truncated realm would silently drop the hint this change exists to deliver — and
free of quote and backslash, since a realm is an HTTP quoted-string and neither
escapes portably. Tests pin those transport facts, and pin what the message must
*say* rather than how it says it.

**Not fixed by exempting loopback**, which was the tempting shortcut and would
have been wrong twice: it weakens the gate, and `cloudflared` forwards to
`127.0.0.1`, so tunnel traffic also arrives from loopback — the exemption would
have opened the public path it was meant to leave alone.

Interim. The durable fix is credential locations a user can find unaided —
`~/.scitex/cards/authorized_keys` and `~/.scitex/cards/auth.yaml`, sshd-shaped,
password hashed, the plaintext environment variable retired.

## [0.31.6] - 2026-08-02

**A public hostname can no longer be bound by a board that cannot authenticate
its callers, and the notification rail stops opening its own database.**

`SCITEX_CARDS_PUBLIC_HOST` used to add a hostname to `ALLOWED_HOSTS` while
asserting only that `DJANGO_SECRET_KEY` was set. That check is real but measures
a **different property**: it makes session and CSRF signatures unforgeable and
says nothing about who may send a request. A board behind an enforcing Cloudflare
Access policy and a board behind nothing produced byte-identical settings — two
states with one representation, the unsafe one rendering as the safe one exactly
when it mattered. Its LAN twin `SCITEX_CARDS_ALLOWED_HOSTS` had refused without a
password since it was written; the path that reaches the internet had not.

A security-shaped check on that branch made it worse rather than better, because
it reads to the next maintainer as "this path is guarded" and ends the question.

**The board now always authenticates its own callers — a key or a password, the
way `sshd` chooses, never neither.** A proxy in front (Cloudflare Access, a hub's
own login) is a *second layer*, never the boundary. An interim revision also
accepted a written claim that something in front authenticated; that was the only
path to an origin with no login, and this process cannot observe whether such a
proxy is enforcing, so it is gone. A test pins the gate's signature at exactly
`(public_host, password)` so the escape cannot return quietly. Consequences: a
misconfigured Access policy stops being a breach, and standalone stays honest
because it is the same code path with no proxy at all.

**The notification rail no longer hand-rolls `sqlite3.connect`.** It was the only
part of the package opening its own database, and therefore the only part that
could not be handed a PostgreSQL target — where the failure is not a clean error:
a DSN reaching `Path(...)` does not raise, it yields a plausible relative path,
and `mkdir` + `sqlite3.connect` then *manufacture* a SQLite file named after the
DSN that accepts writes while the real server sits untouched. It now opens
through `_db.connect`, which dispatches on the target before any path handling.
No rows move: same file, same contents, measured at 56 emitted statements
byte-for-byte identical either side. The S0 PRAGMAs come along, notably
`busy_timeout=300000`.

Supporting that move: a per-backend shape seam so every rail query reads its
table, recipient column and ordering from one place (`rowid` → `seq` is a
replacement, not a rename, and a pure rename would produce SQL valid on both
engines that silently loses delivery order); the null-safe comparison resolved
per connection, because no literal spelling parses on both SQLite 3.37 and
PostgreSQL; schema **v9** giving `notifications` a server-assigned arrival-order
column; row-carry verified by id rather than by count; and a PostgreSQL CI leg so
the canonical backend has regression coverage and its absence is loud.

Also: the off-site backup survives a PostgreSQL store target (it had been dead
31 hours behind a `resolve_db_path(...).parent` that raises on a DSN), and the
channel's own diagnostics are readable in production via an opt-in file sink.

## [0.31.5] - 2026-08-02

**Schema v8, and an instrument for the delivery lag that had the bug it was
built to find.**

`notifications` gains `msg_id`, `pushed_at` and `confirmed_at` — the three
columns the SQLite sidecar gained and the store's own table never did. The table
already existed on the fresh-create path with the right shape and index, and was
vestigial (0 rows on the live store), so the notification rail can move *into*
the store rather than into a parallel table. The columns live in one list used
by both the fresh-create script and the migration, and a test asserts both paths
produce identical shape — a fresh store disagreeing with a migrated one is this
repo's own recorded v4 failure, and it stayed invisible because the stamp was
right.

The **`queued`** lamp on the DM gauge now computes. It rendered *"not observable
yet (the notification carries no message id)"* while 205 of 1517 DM
notifications carried one — the plumbing had landed and the reader was never
updated, its docstring still citing the obsolete limitation. Computed from the
exact `dm_messages.id → inbox.msg_id` join, and three-valued: `None` means the
inbox could not be *read*, never "not queued". Collapsing those would render a
dropped notification as delivered, which is the failure the gauge exists to
detect.

**Channel tick timing.** DMs reach an agent 13–25 s after they are written,
against a 5 s interval. Nine candidates were eliminated by direct measurement —
the wrong daemon, the mtime drain gate, the burst cap, PostgreSQL write latency,
the drain work, an overridden interval, MCP transport backpressure (an *idle*
session measured slower), SQLite write-lock contention, and PostgreSQL
advisory-lock contention. Every component measured fast and the composite stayed
slow, which is the shape outside observation cannot resolve. The loop now
records `drain_s`, `gap_s` and `unexplained_s = gap − prev_drain − interval` —
time spent neither working nor sleeping.

The first version of that instrument subtracted the *current* tick's drain
rather than the previous one. Invisible when drain times are equal; when they
vary the residual absorbs the difference and still reads as an unowned wait.
Measured, a slow tick followed by a fast one reported **0.302 s** of fiction
where the truth was **0.001 s** — the same magnitude as the lag being hunted.
Fixed before any reading was believed.

The invariant it checks is the **sign**, not the identity: `gap == drain +
interval + unexplained` is tautological and would be a gate that cannot fail. A
loop cannot return before its own sleep, so a residual negative beyond clock
jitter means a term is mismeasured. Reported at WARNING, never asserted — a bare
assert in a long-lived delivery loop kills the task and stops the delivery it
measures.

Also documents the twelve SQLite→PostgreSQL hazards measured during the store
migration, nine of which produced no error at all.

## [0.31.4] - 2026-08-02

**The doctor names the engine on both rails, and fails when they differ.**

`check_single_write_target` reported the literal string "SQLite"
*unconditionally*. True when written; a lie from the day a store could be a
PostgreSQL server. Measured on the live store: it printed `exactly one write
target: SQLite` while every card write went to PostgreSQL. The one line that
looks like it answers "which engine am I on" answered it wrongly, confidently,
on every PostgreSQL deployment. It now resolves the engine instead of asserting
it.

Nothing reported the *notification* rail's engine at all. The inbox is a SQLite
sidecar located from the store **path**, so pointing the store at a server does
not move it — cards go to PostgreSQL and notifications stay on SQLite. That
split is what let a DM commit to the store on 2026-08-01 while no notification
was ever created, with every card-side check green.

`check_backend_mode` reports both rails and **fails** when they disagree. A
check that merely printed the two modes would report the split as normal, and
normal is the wrong word for a state in which a green card-side doctor says
nothing about whether notifications are delivered.

It deliberately offers **no toggle** to disable the SQLite rail, and the hint
says so: in postgres mode the sidecar is the only inbox implementation that
exists, so a switch would let the split be *configured* rather than *fixed* — a
fallback wearing a switch. The doctor goes green when the inbox moves into the
store, not when someone sets a variable.

It also names **which tier chose the store target** (explicit argument,
`SCITEX_CARDS_DB`, `config.json`, or the built-in default). "I edited the config
and nothing changed" is the most confusing way this resolution fails, because
every tier is individually working — the environment simply outranks the file.
Determined by comparison rather than by re-implementing the precedence, so it
cannot drift out of step with `resolve_store_target` and start naming the wrong
source.

**An explicit server store no longer collapses into a phantom local store.**

`resolve_tasks_path` has two branches and they disagreed. The ambient branch
already asked `is_postgres_url` and returned the local root. The explicit branch
fell straight through to `Path(explicit)`, which does not reject a DSN — it
coerces it into a *relative* path:

```
Path("postgresql://scitex_cards@127.0.0.1:5432/scitex_cards")
  -> PosixPath("postgresql:/scitex_cards@127.0.0.1:5432/scitex_cards")
```

Everything derived from it then resolved against the writer's current
directory, so `runtime_dir` yielded `postgresql:/…/runtime` and `inbox_db_path`
put `cards.db` inside it.

The failure was a silent **success**, which is why it survived: measured
2026-08-02, `enqueue(store=<DSN>)` returned a notification id and created a
phantom store under the caller's CWD. Nothing raised, so the fail-soft caller
logged nothing, and the notification was unreachable because nobody polls a
directory named after a DSN.

An explicit DSN now resolves to the same local root the ambient branch already
used, rather than raising. Every caller of this function wants a local
directory — pidfiles, the delivery ledger, reminder state, the inbox sidecar —
and wants one just as much when the cards live on a server. Raising would break
the board, which legitimately threads its store through to the inbox rail.

## [0.31.3] - 2026-08-02

**The SQLite inbox used SQL that old SQLite cannot parse, so no notification
was ever delivered on the host.**

`_inbox_sqlite.enqueue` spelled its null-safe comparisons
`IS NOT DISTINCT FROM` — standard SQL, and exactly what SQLite's `IS` means.
SQLite only accepts that spelling from **3.39** (2022-06). The host runs
**3.37.2**, so every enqueue raised `near "DISTINCT": syntax error`.

`_threads_mirror.dispatch_to_inbox` is deliberately fail-soft — the message is
already committed, so a failed enqueue should cost a push, not a message. That
turned a hard SQL error into silence: DMs landed in `dm_messages`, no
notification row was ever written, and the board reported success. Measured on
the live store — an operator DM sat in the store and never reached the agent's
session.

It stayed hidden because the failure is **environment-dependent**. Containers
run SQLite 3.45.1 and parse the standard spelling happily, so agent-to-agent
DMs delivered normally while board-originated ones vanished. CI ran a new
SQLite too, so a behavioural test was green no matter which spelling the source
used — it pinned the SQLite version, not the SQL.

Fixed by using `IS ?`, null-safe in every SQLite that ships this module and
needing no version floor. The PostgreSQL side (`_pg_triggers`) keeps the
standard spelling, which is correct there.

**This reverses a deliberate decision from 0.31.2**, and the reasoning behind
that decision was sound apart from one premise. It chose the standard spelling
so the module's SQL would survive a later move to PostgreSQL, and pinned
SQLite >= 3.39 as a floor. The floor was false where it mattered — production
measured 3.37.2 — and it was never ours to enforce, since the package controls
neither the CI images nor the host's system python. A requirement the package
cannot enforce is a hope, not a floor. The premise does not hold either:
`_inbox_sqlite` resolves `inbox_db_path(store)` and opens a **file**, so it can
never be handed a PostgreSQL connection. The PostgreSQL rail will be its own
backend module, exactly as the YAML and SQLite backends are separate today.

What that decision got right is kept: rewriting the comparison to `=` parses on
both engines and then silently stops deduplicating, because `actor = NULL` is
never true. That trap is still pinned by a positive-control test.

The regression test reads the statements the module actually hands to
`execute()` via AST and fails on the non-portable spelling regardless of the
local SQLite version. It deliberately does not scan the file for a substring:
the module now discusses `IS NOT DISTINCT FROM` by name, and a substring scan
would match that prose and fail forever.

## [0.31.2] - 2026-08-01

**Completing a blocked card clears its gate.** (#723)

`complete_task` set `status=done` and left `blocker` in place, producing a
document `_validate_tasks` refuses. A done card still naming an unresolved gate
is incoherent — either the gate was cleared, or the card is not done — and
`resolve_task` has always cleared it for that reason. The two closing verbs
disagreed, and this one could not write back at all.

Measured on the live `*/15` reconcile cron:

```
TaskValidationError: task 'ci-runner-gitconfig-lock-collision'
has blocker 'operator-decision' but status is 'done'
```

That card was legitimately blocked on an operator decision and its pull request
merged anyway — real data, not corruption. Because validation covers the **whole
document**, that single card stopped the sweep from closing *any* card.

This is the third defect stacked in one code path, after the store target
(0.31.0) and the actor identity (0.31.1), each hidden by the one below it.

### `reassign_task` moves beside `reassign_all`

Carried in the same change because the one-line fix was blocked:
`_store_lifecycle.py` was already 45 lines over the 512-line limit, and its own
`delete_task` carried the comment *"verb-module split still queued"*.

Single-card and all-cards reassignment are one responsibility — changing a
card's owner — and were split across two modules, with the one named for the job
holding half of it. Ownership leaves *lifecycle*, which is about a card's state.

| file | before | after |
| --- | --- | --- |
| `_store_lifecycle.py` | 539 | 421 |
| `_store_reassign.py` | 170 | 312 |

`_store_lifecycle` re-exports `reassign_task` and keeps it in `__all__`, so every
existing import path resolves unchanged — verified that the lifecycle, reassign
and `_store` paths all return the same object.

## [0.31.1] - 2026-08-01

**An unattended reconcile names itself instead of failing.** (#720)

The `*/15` cron entry runs with no `SCITEX_CARDS_AGENT_ID`, so every close raised
`creator unresolved` and the job closed nothing. It surfaced only once 0.31.0
fixed the store-target failure that had been masking it — the job had been dying
at store-open, so it never reached `complete_task`.

`reconcile-merged-prs` closes a card because a **pull request merged**, not
because a person decided anything. Borrowing whichever agent happened to export
a variable is less truthful than naming the reconciler, and requiring one means
an unattended run cannot work at all. Precedence is widened only at the end:

```
explicit by=  →  $SCITEX_CARDS_AGENT_ID  →  SYSTEM_ACTOR
```

The two cases that already worked are untouched; the third previously raised.

This does **not** weaken `_store._resolve_creator_or_raise`, which still refuses
to invent an author for an ordinary caller — the reconciler may default because
it *knows who it is*. It also does not extend 0.31.0's store-target config tier
to identity: a store is a host-level fact every client shares, whereas identity
is per-actor, and one config naming an agent would make every cron job on the
host impersonate it.

## [0.31.0] - 2026-08-01

**The store target can now come from config, not only from an environment
variable.** This closes the single gap behind every store-target failure of the
PostgreSQL cutover.

### An env var is a rule every caller must remember (#717)

Until now the only way to point a client at a non-default store was
`$SCITEX_CARDS_DB`, exported at every invocation site. Anything that did not
export it fell through to a hardcoded local SQLite filename. That one gap
produced, in different clothes each time:

- **8 host-side writers** (4 systemd units, 3 cron entries, 1 hourly timer)
  carrying no store env and silently writing the **old** store while the fleet
  was believed migrated
- **87 agent specs** each needing the variable pasted in individually
- a crontab fix **reverted** by the hourly `copy_crontab` sync, dropping 3 cron
  jobs back to the old default — then failing outright with `StoreRetired`
- any **new** client on the host defaulting to a store that no longer accepts
  writes

`_config.py` already implemented a layered, fail-soft `config.json` across user
and project scope, read at **call** time so a running daemon picks up an edit
without a restart. It carried only `reminders:`. This adds a `store:` section
and a new resolution tier:

```
explicit → $SCITEX_CARDS_DB → deprecated env → config store.target → default
```

**Below** the environment, so per-agent and per-test overrides still win and
nothing that worked before changes. **Above** the hardcoded default — the tier
that was silently wrong.

Both resolvers get it, because `resolve_store_target`'s docstring promises it
mirrors `resolve_db_path` exactly. In `resolve_db_path` the configured value
goes through `_as_path`, so a DSN written into config produces the same loud
refusal an env DSN does rather than being coerced into a mangled relative path
that would create a second, empty store. A new tier must not open a new way in.

The password is not in the config and must never be: the DSN carries none and
libpq reads `$PGPASSFILE` itself.

Measured on the live host with `$SCITEX_CARDS_DB` unset and the same config
present — `0.30.3` resolved to the **retired** SQLite store, this release
resolves to the PostgreSQL DSN.

## [0.30.3] - 2026-08-01

**The schema is now asserted once per store, not once per open.** Required
before a fleet of ~90 agents can share one PostgreSQL store: at that width the
old behaviour is not a slow path, it is a broken one.

### Concurrent opens were deadlocking on the system catalogue (#714)

`init_schema` ran its full DDL on **every connection**. On SQLite that was very
nearly free. Against a shared PostgreSQL server it is DDL against the system
catalogues, and `CREATE OR REPLACE FUNCTION` rewrites the `pg_proc` row every
time — it is *not* a no-op when the definition already matches.

Measured on the live store with the entire fleet **stopped** and only four host
daemons connected — `pg_proc.xmin` sampled every 10s while idle:

```
t+10  all 9 trigger functions  5069 -> 5075
t+20  all 9 trigger functions  5075 -> 5082
t+30  all 9 trigger functions  5082 -> 5087
```

and concurrency did not survive it:

```
 4 simultaneous open_db  ->  at least 1 deadlock
12 simultaneous open_db  ->  11 of 12 FAILED, DeadlockDetected on pg_proc
```

Two clients replacing the same function at once contend on the catalogue and
PostgreSQL resolves it by killing one. `init_schema`'s own comment already noted
that "~90 containers call init_schema on every connection".

The new gate in `_schema_current` skips the DDL when the store already has the
shape this client would assert. **Conservative by construction:** every
uncertain answer falls through to the full DDL, so the worst case is exactly the
previous behaviour. The version must match, the physical rungs and the stamp
must agree, and an unreadable catalogue is not a current schema.

**The guard triggers are verified, not assumed.** They are the retirement
enforcement *and* the proof-of-currency mechanism, so a client that skipped the
DDL without confirming their presence could leave a store unguarded while
believing it had guarded it. Presence is read from the catalogue on every open;
only the *write* is skipped.

| check | before | after |
| --- | --- | --- |
| fresh store gets all 9 guards | — | all 9 present |
| functions rewritten per `open_db` | 9 of 9 | 0 of 9 |
| 12 simultaneous `open_db` | 11 failed | 0 failed |
| 24 simultaneous `open_db` | — | 0 failed |

The fresh-store row is the positive control: it proves the gate does not skip
*creation*, which is the dangerous direction.

## [0.30.2] - 2026-08-01

**The board could not read a PostgreSQL store at all, and every DM write died
before it began.** Both defects were found by exercising the real path after the
fleet cutover, and both were invisible to every cheaper check.

### The board answered 500 to every data request (#712)

Two calls on `get_board`'s read path coerced the store target to a filesystem
`Path`, and `resolve_db_path` **refuses** a DSN rather than coercing it:
`_django/services.py` for the `/rev` mtime, and `_store_write.store_generation`'s
file-existence gate.

The refusal is correct and load-bearing. Coercing a DSN would have created an
empty SQLite store at a mangled path and served 0 cards **while reporting
healthy** — the exact failure `services.py` already carries a post-mortem for.
The guard worked; the server branch behind it was missing.

`store_generation` now gates on the file only when the target *is* a file — a
server's existence is established by connecting, which `load_doc` does and which
raises when it cannot. `"absent"` stays reserved for a genuinely missing local
file, because it disables the optimistic-concurrency guard.

The `/rev` stamp moves to `_django/_revision.store_change_stamp`. On a file store
it is the database mtime; on a server it is a content fingerprint derived from
the generation hash already computed on that path. `/rev`'s `mtime` is typed
float but consumed only as an equality key, and was already documented as
REPORTED, not trusted. The server value sits far outside epoch range so anything
treating it as a time is obviously wrong rather than subtly skewed.

### Every DM write died on `BEGIN IMMEDIATE` (#712)

`syntax error at or near "IMMEDIATE"` — SQLite-only spelling, reported by
scitex-db with a live reproduction. It failed before writing anything, so no data
was harmed, but DM is the operator's channel to the fleet.

A gap in the 0.30.0 port: the statements *inside* the transaction were made
portable and the statement that *opens* it was not.

**A plain `BEGIN` would have been worse than the syntax error.** `IMMEDIATE` is
not decoration — SQLite takes the write lock at BEGIN so two appenders serialise,
and the DM append reads `max(seq)` then inserts `seq + 1`. PostgreSQL defaults to
READ COMMITTED, under which both appenders read the same `max(seq)` and both
insert. That parses, runs, passes a smoke test, and silently reintroduces the
exact race `IMMEDIATE` exists to prevent. SERIALIZABLE detects it but by aborting
one side, which every call site would have to retry.

New `_store_tx.begin_write_transaction` issues `BEGIN IMMEDIATE` on SQLite and
`BEGIN` plus `pg_advisory_xact_lock` on PostgreSQL — blocking, not aborting, and
released on commit or rollback alike. It replaces all 7 executable sites
(`_dm_write` ×4, `_dm_migrate` ×2, `_store_uuid` ×1). The lock is store-wide,
matching SQLite where the write lock covers the whole file: this is a
compatibility seam, not a concurrency rewrite.

### Verified against the live server, not a fixture

`get_board` returns 2980 cards with `empty_store` false and the discriminators
land both ways; `begin_write_transaction` opens a write transaction; a real
`append_pair` landed a message at seq 2.

## [0.30.1] - 2026-08-01

**DM writes work on PostgreSQL, and retirement now stops them.** 0.30.0 carries
both defects: DMs could not be written to a server store at all, and a retired
store accepted them.

### DM writes died on a server store while cards worked (#710)

`resolve_dm_db` fell through to `resolve_db_path` for its ambient tier, and that
**raises** on a DSN. With `$SCITEX_CARDS_DB` pointing at PostgreSQL:

```
READ  list_tasks   2971 cards            ok
WRITE dm funnel    StoreTargetIsNotAPath
```

Card reads and card writes were unaffected — only DMs, which agents send
constantly. The two tiers above stay paths deliberately: an explicit `db` or
`store` names a file, and deriving the DM database from `store.parent` is what
stops a test with a tmp store writing its DMs into the live fleet database. Only
the **ambient** tier can be a server.

Found by booting the rebuilt image the way an agent does and attempting a write.
Every cheaper check passed on the broken build — right version, driver present,
`backend: postgresql`, correct uuid, 2971 cards readable.

### Retirement now stops DM writes (#708)

Required before any store is retired.

Card writes reached the guard only *incidentally* — they are read-modify-write,
so they pass through the canonical read, which checks. DM writes had their own
path and checked nothing. Measured during the PostgreSQL cutover:

```
14:16  a card write   REFUSED   (correct)
14:26  a DM write     LANDED    in the retired store
```

So retirement was a fence for one path and a signpost for the other, and
"stragglers fail loudly" held only for readers.

The refusal lives in `_dm_write_rows._open`, the DM **write funnel** — all five
mutating verbs open through it and nothing else does. Deliberately **not** in
`open_db`: the canonical read and the export/snapshot paths open through that
too, and a retired store must stay **readable**, because recovering from a
retirement means reading the store you retired.

Reuses the existing `_refuse_if_retired_on` rather than adding a second
definition of "is this store retired" — per that helper's own docstring,
duplicating it per caller is how the two answers drift.

Two of the new tests assert the store stays **readable** after retirement,
specifically so a later "fix" that moves the guard into `open_db` goes red.
Negative control, run against unfixed 0.30.0 source: the retired store opens for
a DM write and the test fails (`- refused / + opened`).

## [0.30.0] - 2026-08-01

**The client can now WRITE PostgreSQL.** 0.29.0 could read one; every write
still died, because the write side had never been ported. SQLite remains the
DEFAULT and PostgreSQL stays OPT-IN via `$SCITEX_CARDS_DB`.

### The read path stopped taking the query side down (#704)

Pointing `$SCITEX_CARDS_DB` at PostgreSQL made the whole query side raise
*before opening a connection* — `list-tasks` died in path resolution while the
write and canonical-read paths were already DSN-aware. One resolver conflated
two things, and a server store has no directory:

- store **identity** — `$SCITEX_CARDS_DB`; a path OR a server URL
- local state **dir** — pidfiles, delivery ledger, reminder state, the
  users/groups sidecar; always a real directory, whatever the backend

Card data never needed that path: `load_doc` opens the store with no argument
and interpolates the path into an error string only.

Two diagnostics that lied are fixed with it. `resolve-store` — the verb whose
whole job is *which store am I on?* — crashed on the answer; it now reports the
target uncoerced plus a `backend` field, with `exists` three-valued (`None` on
a server, because `False` reads as "your store is missing"). And `store_uuid_at`
was path-only, so `Path("postgresql://…").exists()` was `False` and it returned
`None` **without raising** — a PostgreSQL store reported "no identity",
indistinguishable from having none, which silently disarmed `expected_uuid`.

### The write path is ported (#705)

Every store-path statement now uses ONE form both engines understand, rather
than a dialect-translation layer:

```
INSERT OR IGNORE   ->  INSERT ... ON CONFLICT DO NOTHING
INSERT OR REPLACE  ->  INSERT ... ON CONFLICT(<key>) DO UPDATE SET ...
```

**This also fixes the v7 optimistic-lock counter, which had never once fired.**
`INSERT OR REPLACE` is DELETE+INSERT, so `AFTER UPDATE ON tasks` was never
reached and `revision` read `0` on all 2957 live rows. Measured after: a card
update advances the counter (`1 -> 2`), and the live distribution now carries
non-zero revisions.

Two hazards were measured before converting, not after:

- `REPLACE` resets columns the statement does not name; `DO UPDATE` preserves
  them. Every `REPLACE` site names **every** column of its table, so the forms
  are equivalent here.
- The append-only guards are `BEFORE DELETE … RAISE(ABORT)`, so conversion
  could have silently *removed* a refusal. The intersection is **empty**: every
  delete-guarded table is written with the non-deleting `IGNORE` form. That is
  a design property, not a coincidence — which is why all four `_dm_write`
  conversions are `DO NOTHING`, preserving "skip, never overwrite".

Also: positional `r[0]` under psycopg's `dict_row` raised `KeyError: 0` inside
the shrink guard, and the ambient-store guard coerced the DSN while evaluating
an *argument*, so it raised before the guard it feeds ever ran.

`_dm_write.py` was 515 lines against a 512 cap; row primitives moved to
`_dm_write_rows.py` and every name is re-exported, so the public import surface
does not move.

## [0.29.0] - 2026-07-31

**The store layer now REACHES PostgreSQL.** 0.28.0 made a PostgreSQL store
buildable; this release makes the package actually read one. Measured against
the live server: `2962` cards and `6171` comments at `schema_version 7`, not the
`0` a broken path returns. SQLite remains the DEFAULT and PostgreSQL is OPT-IN
via `$SCITEX_CARDS_DB`; the SQLite path is byte-identical to 0.28.0.

**The blocker was one line, and it explains why the seam sat unused.**
`open_db` — the one-call entry point the canonical read path uses — resolved
through `resolve_db_path`, which is typed `-> Path` and refuses a DSN. So no
call site could reach PostgreSQL however well `connect()` dispatched, which is
exactly what `_store_target.py`'s docstring meant by "NOTHING in the package
imports them". It now resolves the TARGET (#693).

**A PostgreSQL DSN is refused, never coerced (#692).** `Path("postgresql://h/db")`
collapses to the RELATIVE path `postgresql:/h/db`, which manufactures an empty
SQLite file and then serves 0 cards while reporting `exists: True`. Two stores,
both looking healthy, is a failure this package has scar tissue from.

**Schema init is portable (#693).** `PRAGMA` is SQLite-only and PostgreSQL
rejects it outright, so the version stamp and the column probe are now
dialect-aware. On PostgreSQL the trigger-protected `schema_meta` row IS the
stamp — the direction `stamp_schema_version` already argued for, since a PRAGMA
structurally cannot carry a trigger.

**Canonical read reaches PostgreSQL with its guards intact (#694).** Existence,
ownership and retirement are re-expressed for a server rather than a file,
because each asks a question whose MEANING differs by backend: existence is a
catalogue question, not a `stat()` call. Every failure raises — this is
read-modify-write, and returning an empty document here is written back as the
whole store.

**The v7 revision lock was INERT and now fires (#695).** `INSERT OR REPLACE` is
`DELETE` + `INSERT`, so it never fired the `AFTER UPDATE` trigger that bumps
`revision`. The lock was installed, present, and doing nothing. `ON CONFLICT DO
UPDATE` makes it real, parses on both engines, and drops the `ON DELETE CASCADE`
work this codebase measured at 42x. BREAKING: an upsert through the mirror now
bumps `revision` where it previously did not.

**The PostgreSQL cross-check is snapshot-consistent (#696).** PostgreSQL's
default isolation is READ COMMITTED, under which every statement takes a fresh
snapshot EVEN INSIDE A TRANSACTION — so holding one connection was never enough.
The export and its verifying `COUNT(*)` now run in `REPEATABLE READ`. Without
it the guard compares two different database states and reports success.

**Three defects in this release were found only by running the real server, and
two of those only by the NEGATIVE control.** A static scan of the obvious
init-path modules missed the `PRAGMA` inside `init_schema` itself. An existence
guard placed after `open_db()` could never fire, because `init_schema` CREATES
the tables — pointed at an empty database it built the whole schema and returned
0 tasks instead of refusing. A guard defeated by the act of opening is not a
guard, and the positive control passed in every one of those cases.

## [0.28.0] - 2026-07-31

**A PostgreSQL store can now be built and reached — not just described.** 0.27.0
made the backend seam importable; this release makes it usable. `_db.connect()`
accepts a PostgreSQL target, the schema script creates a working store on
PostgreSQL including every guard, and the export path no longer speaks SQLite.
Nothing writes to PostgreSQL yet: the canonical store is still SQLite, and the
cutover switches are deliberately not in this release.

**A theme, and it is the reason for the test style below: on this port, the
dangerous failures pass at DDL time and fail at runtime.** `AUTOINCREMENT` has
no portable spelling, and the obvious substitute — a plain `INTEGER PRIMARY KEY`
— *parses on both engines* and only fails when you INSERT, because PostgreSQL
does not auto-assign it the way SQLite's rowid alias does. So the tests here
insert a row and read the generated id back; asserting `CREATE TABLE` succeeded
would have certified the broken choice.

### Added
- `_db.connect()` dispatches a PostgreSQL URL or a libpq keyword/value conninfo
  to the backend seam. The dispatch is the **first** statement in the function:
  `Path(dsn)` on a conninfo does not raise, it manufactures a SQLite file named
  after the DSN that accepts writes while the real server sits untouched. That
  file was created and observed during development, so the test asserts **no
  file appears** (#685).
- `_pg_triggers` — PostgreSQL equivalents of all nine guard triggers, and
  `execute_ddl` now **substitutes** them when it meets a SQLite `CREATE TRIGGER`.
  An unrecognised trigger name **raises**. Skipping what a backend cannot run is
  the tempting move and it is silently wrong: the tables come up, the store
  passes every smoke test, and an append-only table quietly accepts `DELETE`
  because its guard was never installed (#687).
- A DDL dialect step translating `AUTOINCREMENT` at execution time, so the
  schema constants stay written in the dialect the production store speaks
  today (#686).
- `_db_schema_sql` — the core schema DDL extracted from `_db`, joining
  `_db_dm_schema` / `_store_retirement` / `_schema_shape` so every piece of DDL
  now lives in a module named for what it creates (#685).

### Fixed
- The min-client-version gate no longer assumes SQLite. It recognised a missing
  `schema_meta` by catching `sqlite3.OperationalError`; PostgreSQL raises
  `UndefinedTable`, so opening a **brand-new** PostgreSQL store raised out of a
  function whose contract is "no floor stamped, this is a no-op". It also read
  `row[0]` positionally, which `dict_row` refuses (#684).
- `_schema_probe` built its result set positionally too, so `has_table` raised
  `KeyError: 0` on exactly the by-name connection PostgreSQL requires (#684).
- The canonical export ordered four result sets by `rowid`, which PostgreSQL
  does not have — this would have failed **at cutover**, inside the code that
  writes the YAML the whole fleet reads. Now ordered by creation timestamp with
  the primary key as tie-breaker; the tie-breaker is load-bearing, because these
  timestamps have one-second resolution and same-second rows would otherwise
  order arbitrarily, making the export differ run to run (#688).
- `StoreConnection` gained `executemany` / `executescript` / `commit` /
  `rollback` and opt-in by-name rows, which is what the write path needed
  before anything could adopt the seam (#682).

### Notes
- The nine PostgreSQL guards are **generated** from a running server via
  `pg_get_triggerdef` / `pg_get_functiondef`, not hand-written. A retyped
  plpgsql guard that reads correctly and permits what it forbids is precisely
  the failure this avoids.
- Guard tests attempt the forbidden operation rather than counting triggers, and
  include a positive control (a legal `7 -> 8` upgrade must still succeed) so a
  guard cannot pass by refusing everything.

## [0.27.0] - 2026-07-31

**The backend seam becomes reachable.** Until this release `_backend_connect`
and `_store_url` were implemented, tested, and imported by nothing — every read
and write called `sqlite3` directly, so a PostgreSQL store could receive no
tables and, more importantly, **no guards**. A store with no retirement guard
reports itself current and authoritative, which is the failure that took this
board from 2170 rows to 18.

A recurring lesson runs through the SQL fixes below: **both looked like they
needed a dialect branch and neither did.** `GREATEST` is PostgreSQL-only and
two-argument `MAX` is SQLite-only, but the standard-SQL spelling works on both.
Try standard SQL against both engines before adding a translation layer — every
branch is a place the two backends can drift.

### Added
- **A DDL runner that works on both backends (#675).**
  `sqlite3.Connection.executescript` is pysqlite-only and was how *every* schema
  object here got installed, all nine triggers included. The difficulty is one
  character: a trigger body is `BEGIN <stmt>; <stmt>; END`, so its semicolons are
  internal and a naive `split(';')` severs it — and the first fragment can still
  parse as a complete `CREATE TRIGGER`, installing a **truncated guard** that
  every by-name presence probe then reports as present. `split_sql_script`
  tracks nesting instead. `execute_ddl` returns a **count**, because
  `executescript` returned a cursor nobody read and "installed nothing" looked
  exactly like "installed nine triggers".

- **`resolve_store_target` — the store target without assuming it is a path
  (#674).** `resolve_db_path` is typed `-> Path`, so a URL cannot be
  represented; it was coerced instead:
  `postgresql://user@host:5432/db` → `Path('postgresql:/user@host:5432/db')`, a
  **relative** path, silently, one slash lost. The caller then creates an empty
  SQLite file at that name and reports a healthy empty board.

### Fixed
- **Guards are read from the right catalogue (#676, #678).** Four sites asked
  `sqlite_master` which guards a store carries — a table PostgreSQL does not
  have. The quiet failure is the dangerous one: the query returns nothing, the
  store looks unguarded, and it is reported healthy and current. A store that
  can prove nothing must not answer yes. The PostgreSQL query excludes
  `tgisinternal`, since every FK constraint installs internal triggers and
  counting them would report a guard-free store as richly guarded.

- **Every DDL install routes through the runner (#677).** Verified by building
  the same database twice, one process per branch: 44 objects vs 44 objects,
  identical `sqlite_master`, `user_version` 7, 9 triggers. The transaction
  boundary was the risk rather than the SQL — `executescript` issues an implicit
  COMMIT before running — so only building both databases establishes the result
  is the same.

- **NULL-safe comparison both engines accept (#679).** The inbox dedups on four
  nullable columns. SQLite spells it `x IS ?`; PostgreSQL rejects that outright.
  The tempting fix, `=`, **parses on both and silently stops deduplicating** —
  `actor = NULL` is UNKNOWN, never true — producing a notification storm and
  quietly killing the "at most one pending digest per recipient" invariant.
  Measured against a NULL column: `IS ?` → 1 row, `IS NOT DISTINCT FROM ?` → 1
  row, `= ?` → **0 rows**. That last line is now a test.

- **Scalar max both engines accept (#680).** `MAX(a, b)` is scalar on SQLite and
  an **aggregate only** on PostgreSQL — `function max(integer, integer) does not
  exist`, measured live. Spelt as `CASE`, which is standard SQL.


## [0.26.1] - 2026-07-31

### Fixed
- **The migration decision now floors on the physical shape, not the PRAGMA
  (#671).** A PRAGMA cannot carry a trigger, so 0.26.0's engine-level floor
  protects `schema_meta` and *structurally cannot* protect `user_version` — and
  `init_schema` reads exactly that PRAGMA to decide whether it is migrating.

  Observed on the live store, from its own stamps:

  ```
  schema_migrated_at   02:45:01Z -> 02:45:47Z -> 03:00:03Z -> 03:00:47Z
  schema_migrated_from 5, then 6, then 5
  schema_migrated_by   0.25.0
  ```

  while v6's `tasks.revision` and v7's `tasks_bump_revision` were physically
  present throughout. `record_migration()` returns early when `prior == new`, so
  that advancing timestamp is not noise — it is proof the PRAGMA kept reading
  back as 5. A current client was re-migrating a store that had never been
  behind, every ~45 seconds, rewriting the migration record each time. The one
  field that could say when the store was last really migrated was being
  destroyed by the loop it exists to document.

  Applied schema is additive, so the physical shape cannot go backwards — it is
  the one floor a stale stamp cannot lower. `_prior_version` now takes
  `max(user_version, observed_version(conn).observed)`. This is what
  `_schema_shape` was built for in 0.26.0 and was not yet wired into the
  decision it exists to inform.

  **This does not stop the writer**, which remains unidentified. It stops
  current clients from being misled by it. Fresh databases are unaffected:
  `observed` is None with no rung present, preserving the CREATE-not-migrate
  branch.


## [0.26.0] - 2026-07-31

**Three safeguards that existed in code and did not hold in practice.** A
version floor only the clients carrying it obeyed; an enforcement probe that
could pass without testing anything; a working PostgreSQL backend whose driver
the package never asked for. In each case the artifact was real and the
protection was not.

### Fixed
- **The schema version stamp is floored in the ENGINE, not in the client
  (#667).** Measured on the live fleet store, with nothing of this agent's
  writing to it and no tests running:

  ```
  t=02:45:25  schema_version='5'
  t=02:45:50  schema_version='7'
  t=02:46:15  schema_version='5'
  ```

  and, settled minutes later: `tasks.revision` column present, the
  `tasks_bump_revision` trigger present — both installed by the v5→v6 and
  v6→v7 migrations — while the stamp read `5`. **The store WAS v7 and SAID
  v5**, which is the unsafe direction: a reader gating on the stamp concludes
  `tasks.revision` is absent while it is physically there, and writes
  accordingly.

  0.25.0 already took `max(prior, SCHEMA_VERSION)` when stamping. That fix was
  real, and it was the "remember to apply it" kind — it binds only the clients
  that have it, and any client still executing a bare
  `PRAGMA user_version={SCHEMA_VERSION}` overwrote the store regardless. The
  floor now lives in a SQLite trigger on `schema_meta`, so it applies to every
  writer whether or not that writer knows it exists.

  It ASSIGNS rather than REJECTS, deliberately: `RAISE(ABORT)` would fail every
  old writer's connection outright on a mixed-version fleet. A refused
  downgrade is recorded (`schema_version_downgrades_refused` plus the timestamp
  and the attempted transition), so the condition is counted instead of merely
  prevented — on the live store that counter is now in the thousands, all
  `7 → 5`, and the store has held at 7 throughout.

  Adds `_schema_shape.py`, which reads the store's PHYSICAL shape (which
  tables, columns and triggers actually exist) and reports agreement with the
  stamp as a three-valued answer rather than trusting either alone.

### Added
- **A vacuous enforcement probe is now unconstructible (#666).** The same trap
  caught two agents independently, hours apart, both of whom knew the
  principle:

  - a guard tested with `DELETE ... WHERE id = 'nonexistent-id'` was reported
    NOT ENFORCED, against a guard that demonstrably refuses — a `BEFORE DELETE`
    trigger fires PER ROW, so deleting zero rows succeeds;
  - a cutover pre-check specified as "flip `store_status` retired → current,
    expect ABORT" would, on a store with no `store_status` row, match nothing,
    succeed, and halt the cutover by declaring a working guard dead.

  Same shape both times: **a statement that touches nothing cannot be refused,
  and "was not refused" reads identically to "is not guarded."**

  `probe_enforcement()` counts the rows the forbidden statement would touch
  BEFORE attempting it and raises `VacuousProbe` on zero, naming the remedy
  (manufacture the precondition inside a transaction, probe, roll back).
  `EnforcementVerdict` re-checks the same rule in `__post_init__`, so a vacuous
  verdict cannot be smuggled past by constructing one directly.
  `expect_refusal_containing` is REQUIRED and must be non-empty, because a
  read-only connection, a typo and a lock all raise, and a probe that catches
  any exception reads all three as proof of enforcement.

- **The `postgres` extra, so the PostgreSQL path is reachable by install
  (#668).** `_backend_connect` reads a PostgreSQL store today: the same query
  string, written with SQLite's `?` placeholders and never rewritten by the
  caller, returned the same row count through both backends against PostgreSQL
  18.4, because `to_paramstyle` translates in transit. 39 tests cover it and it
  was independently reproduced.

  It worked because psycopg *happened* to be installed where it was tried.
  Measured in a normal environment: `psycopg available: False`. The capability
  was real, tested, and unreachable by anyone installing the package normally —
  including its author. **A capability with no declared dependency is not
  shipped, it is merely written.**

  psycopg is declared in `postgres`, in `all` so CI exercises the backend
  rather than merely compiling it, and in `dev` so a fresh
  `pip install -e .[dev]` runs the backend tests instead of erroring on their
  unguarded import. Guarding those imports with `importorskip` was rejected: a
  skipped test for a capability about to be cut over to reads green while
  measuring nothing.


## [0.25.0] - 2026-07-30

The PostgreSQL migration became possible, and a byte that could have made it
impossible was stopped at the door. Nothing here switches the store — this is
the capability plus the guard, not the cutover.

### A byte no backend can store (#663)

A NUL is legal in SQLite TEXT and illegal in PostgreSQL TEXT, so a body SQLite
accepted silently made the whole store unmigratable. Two rows in `messages`
blocked the preflight; within ~2 minutes of clearing them a third arrived in
`dm_messages`, written by an agent actively trying not to write one, in a
message ANNOUNCING the fix. Prose about the byte is how the byte spreads.

`dm_messages` is append-only and immutable except its tombstone columns, so that
third row cannot be corrected in place. Write time is not the convenient place
to catch this, it is the only place.

Sanitised rather than rejected — rejecting would discard a legitimate 4 KB
technical message whose only sin was quoting the byte it was about. The
`record_json` already holds the body with the byte JSON-escaped, byte-identical
to the column, so the original survives in the row. The marker is U+2400, not a
backslash escape: one live body already contained `\x00` as prose, so a naive
un-escape produced three NULs where the original had one. A marker a human can
type by accident cannot be distinguished from content.

The constant is `chr(0)`, never a literal. The first draft of the guard put a
real NUL on line 53 and git classified the module as binary — precisely the
defect the rows that started this were discussing. A test pins the source as
plain text.

### Reading either backend (#663)

140 `execute()` sites write SQLite's `?` paramstyle. Porting each is 140 chances
to miss one, so the translation is bound to the CONNECTION: code keeps writing
`?` and forgetting is not expressible. A `?` inside a string literal is NOT a
placeholder — card titles and message bodies contain them constantly, and a
naive replace corrupts those literals silently, producing wrong data rather than
an error.

Read-only. The 52 upserts, 32 PRAGMA sites and 10 `BEGIN IMMEDIATE` blocks are
not ported and no claim is made about writes. Verified against a real PostgreSQL
18.4 holding a verified copy of the live store — the same query string returning
the same answer from both backends, with the caller unaware which replied.

### Store retirement, one-way and engine-enforced (#663)

After a verified copy there are TWO stores with the same identity. Identity
cannot say which is authoritative; only a statement of which is CURRENT can, and
the cutover is the act of moving that statement into the OLD store so a
straggler fails loudly instead of serving yesterday's board.

Enforced by triggers, not client code: `schema_version` was measured oscillating
7 → 5 → 6 within an hour because an older client stamps its own version
unconditionally. A rule only the current client honours is not a rule.

The guard has three states, and the third is deferred behind a REQUIRED
`unguarded_store` keyword with no default. Wiring it as an immediate refusal
would have blacked out every board on release day: the guards install via
`init_schema` on a WRITE open, readers open `mode=ro`, and a read-only
connection cannot create a trigger. The retirement branch itself is NOT
deferred — a retired store is refused in either era.

### Static assets carry the release (#663)

The operator reported right-click no longer opening the DM context menu. It was
unreproducible — and being unreproducible was the diagnosis, since a fresh
browser cannot hold a stale file. A hard reload fixed it. Applied at the storage
backend rather than at the 61 `{% static %}` call sites, so it cannot be
forgotten and new templates inherit it.


## [0.24.0] - 2026-07-30

The DM thread is readable on a phone, and the board can be reached from one
without being open to the network. Both requested repeatedly by the operator;
0.23.0's attempt at the first one was reverted within minutes of shipping.

### The DM render window (#656)

0.23.0 used `content-visibility: auto` with a `contain-intrinsic-size` estimate.
Typing got fast and the thread became unreadable: an off-screen message
contributed an ESTIMATED height, scrolling replaced estimates with measurements,
and total scroll height grew underneath the reader, so the bottom receded.
Reverted in #655. The operator's words were 「一番下にたどり着けないの地獄すぎる」.

Two things about that failure, recorded because they were avoidable:

* The measurement needed to size the estimate (883 B/msg, so 250px+, not the
  64px guessed) was already in my own notes on the card.
* Guessing too small is the UNRECOVERABLE direction — it strands the reader at a
  bottom that keeps moving. There was no reason to guess at all.

This renders the newest 60 messages and builds no nodes for the rest, so a
message either has real nodes or contributes no height; the 0.23.0 failure mode
is unavailable rather than mitigated. Scrolling near the top reveals 60 more,
which the operator preferred to a button.

The load-bearing part is the correction, not the windowing. Prepending older
messages pushes the view down, so `scrollTop` is adjusted by the height the pane
actually gained, measured from the real pane after the repaint:

    newTop = topBefore + (heightAfter - heightBefore)

`createScrollUpLoader` keeps that arithmetic and the repaint in ONE unit. In
0.23.0 they lived in separate files and nothing exercised the pair, so the
combination shipped untested while both halves looked correct. The regression
test drives the loader against a container whose `scrollHeight` grows with its
node count: 60 messages at 250px, reader 100px from the top, grow to 120,
`scrollTop` must become 15100. A test asserting "fewer nodes rendered" would have
passed on the broken version, which is why that is not the test.

* `chat_window.js` — window policy plus the loader, pure, 15 tests
* `chat_avatar.js` — extracted en route, previously untested
* The DM-only header colour overrides are gone. An earlier fix matched the header
  TEXT across board and DM and left DM feeding `--accent-2`, so the heading still
  differed between pages along an axis the first fix did not touch. Removed
  rather than set to a matching value, which would agree today and drift later.

### A password on the board (#657)

The board had no authentication of any kind — no `django.contrib.auth`, no
sessions, no login. That was survivable only on 127.0.0.1, where the operating
system is the access control. Reaching it from a phone means binding the LAN,
which removes the only gate it had.

`SCITEX_CARDS_ALLOWED_HOSTS` now REQUIRES `SCITEX_CARDS_PASSWORD` and raises
without it, so exposure-without-auth is unreachable instead of discouraged. The
knob's previous comment said "the standalone board has no auth, so only open it
on a trusted network" — advice, which is what you write when the code will still
let you do the unsafe thing.

* `_board_exposure.py` — the rule, importing nothing, because settings.py calls
  it during Django's settings import
* `_board_auth.py` — HTTP Basic, constant-time comparison, and
  `MiddlewareNotUsed` so the loopback default pays nothing
* Whitespace-only passwords are refused; a stray space in a shell export would
  otherwise silently open the board
* Nothing is exempt, including static files: an exemption list is a second place
  for the gate's shape to be wrong

Verified by mutation rather than by a green run: replacing the guard with
`if False:` turns the refusal test red with its own message.

LIMITS, stated because a password invites more trust than this one earns. Basic
over `http://` is base64 — encoding, not encryption — so anyone who can watch the
network sees it; this is for a trusted LAN. It is therefore deliberately NOT
treated as sufficient for public exposure: `SCITEX_CARDS_PUBLIC_HOST` still
requires the tunnel's own authenticator in front, because a tunnel carries no
auth of its own.

### Store internals

* `tasks.revision` is now incremented by a DB trigger (schema v7, #654), so the
  counter moves on every UPDATE regardless of the writing client's version. This
  makes `revision` trustworthy; it is NOT yet a lock, because no caller passes a
  `WHERE ... AND revision = ?` predicate. The lost-update hole is still open.

## [0.23.0] - 2026-07-30

Two DM-page fixes, both reported by the operator and both cases where they
located the problem better than my code reading did.

### Typing lag in the DM composer (#651)

Distinct from 0.22.0's board search-box debounce, which was a real defect in the
wrong place: the operator's lag was on `/dm`. They then ran the controlled
experiment — typing lags in a heavy thread, is comfortable in a light one — which
located it in thread weight rather than in any handler.

    dm:operator::scitex-cards   222 msgs   86,859 B   lags
    dm:operator::scitex-hpc      51 msgs   18,761 B   comfortable

The pane keeps every message in the DOM (`chat_diff.js` appends incrementally,
so re-rendering was never the cost — the tree size is). Each keystroke pays
style+layout across it, and `chat_compose.js` auto-sizes the textarea by reading
`scrollHeight`, forcing that layout synchronously.

* `content-visibility: auto` on `.msg` — the browser skips layout and paint for
  off-screen messages. No cap, no "load older" affordance, and no change to the
  diff logic, so it cannot alter which messages appear.
* `contain-intrinsic-size: auto 64px` — the `auto` keyword makes the browser
  remember each bubble's real height after first render, so the scrollbar
  settles. Bubbles differ ~6x in this thread, so a fixed estimate would leave it
  visibly wrong.

No per-keystroke timing is claimed: the build container has no browser, so the
mechanism is established from source and the magnitude is not measured.

### The header text changed between Board and DM (#651)

Operator: they sent the board's header element and asked for it verbatim on DM,
then 「header が変わると変です」. The `<h1>` was swapping "SciTeX Cards" for
"Direct messages", which reads as the page breaking rather than as navigation.

Both pages now pass the same `page_title`: the band names the PRODUCT, the
switcher names the PAGE. This supersedes a 2026-07-29 decision (DM wording over
"chat") that had been implemented partly as this heading and pinned by a test —
the DM wording survives in the switcher item, its tooltip and the browser tab,
each already covered by its own test. A new test pins that the switcher still
says DM, since it is now the only visible thing distinguishing the two pages.


## [0.22.0] - 2026-07-30

Two alarm/latency fixes, both reported from outside and both cases where the
obvious measurement would have confirmed the wrong thing.

### The blocked-check was silenceable by TYPING (#646)

`detect_blocked_external` asks "has your blocker cleared?" but measured
`last_activity` — "when was this touched". A comment moves `last_activity` and
clears nothing, so annotating a stuck card hid it for another 24 h. Reported by
grant across three consecutive sweeps: of five cards that dropped off, three had
genuinely been reclassified and TWO had merely been commented on. That inverted
the incentive — recording evidence on a stuck card is the behaviour we want, and
doing it hid the card — so they had stopped commenting on seven genuine waits to
keep them visible.

Not a narrowed set: every card was inspected and answered. The clock measured a
DIFFERENT QUANTITY than the question asked, which is why no filter fix would
have touched it.

* `blocked_at`, stamped by `_stamp_blocked_at` when the `(status, blocker)` PAIR
  moves. A different blocker restarts the clock (a genuinely new wait); a comment
  does not.
* `_blocked_age_hours` falls back to `created_at` and NEVER to `last_activity` —
  the 394 already-blocked cards carry no stamp, so a `last_activity` fallback
  would have silenced the very cards that motivated the fix. `created_at` makes
  an unstamped card read as maximally stale, so the alarm errs toward FIRING: a
  spurious re-check costs a glance, a suppressed one costs a card.
* `_detect_owned_untouched` takes a `clock`, defaulting to `_age_hours`, so
  stale-active is unchanged — there a comment legitimately IS acting.

Rollout measured with the real function against the live 394 blocked rows rather
than a reimplemented predicate: 46 messages where 42 already went out daily, so
4 owners newly nudged and 0 losing coverage. No backfill: there is no event log
to reconstruct a stamp from (`task_comments.kind` is NULL on 5908/5967 rows).

`_stale_active.py` split into `_stale_active_thresholds` (how long is too long)
and `_stale_active_clocks` (which quantity each sweep measures). Both clocks now
sit side by side, because picking the wrong one is the defect. Public API
unchanged — all 23 `__all__` names still resolve.

### The caret lagged the keyboard on the board (#647)

Distinct from 0.21.0's payload work, and not fixable by it: that cut LOAD cost
(21.0 MB → 11.9 MB), this is INTERACTION cost. `board_v3.html` called `render()`
synchronously from `oninput`, which is dispatched BEFORE the browser paints — so
a full-layout rebuild over 2885 cards sat on the critical path of displaying the
character just typed. Hence the CARET lagging rather than results lagging the
caret; only one of those complaints points at the input handler.

New pure `board_v3/searchDebounce.js` (timers only, no DOM/STATE) providing a
trailing-edge 120 ms debouncer, unit-tested under `node --test` like its 14
siblings. A missing module degrades to the OLD synchronous `render()` — correct
but slow — never to a board that stops responding to the filter.

`renderSearchSuggest` is deliberately NOT debounced: it populates the state
`onSearchKeyDown` reads to route Enter / Tab / ArrowDown, so deferring it would
trade a working keyboard contract for a smaller saving.

No per-keystroke timing is claimed — there is no browser in the build container,
so the mechanism is established from source and the magnitude is not measured.

## [0.21.0] - 2026-07-30
comments[] is GONE from the /graph payload. Step 3 of 3, and the step the
other two existed to make safe.

MEASURED ON THE LIVE STORE at 2,881 cards, not inherited from the estimate
this had carried since 2026-07-17:

    comments[]        9,015,360 B   REMOVED
    comment_scalars     707,195 B   kept (0.19.0)
    rescore_history      15,343 B   kept (0.20.0)
    NET REMOVED       8,292,822 B   = 7.9 MiB

The replacements cost 8.0% of what they replace. The field had grown past
the 8.5 MB figure quoted earlier the same evening, which is why it was
re-measured rather than reused.

Why it mattered: the board polls /rev every 5000 ms and refetches the whole
response on any change, while the store is written every ~4 s (15.5
row-deltas/min of ordinary fleet traffic). So it refetched this on very
nearly every tick, and `skipIfUnchanged` only skips the REDRAW - the
download had already happened.

WHY THIS IS ONE LINE AND NOT A FLAG DAY. A previous attempt removed the
field and added its replacements in a single branch; that broke every
consumer at once and sat unmergeable for twelve days. This time the
replacements AND the per-consumer fallbacks shipped first, in 0.19.0 and
0.20.0, so a consumer missed here degrades to its fallback instead of
breaking. Do NOT remove those fallbacks in the same release as this.

NEW GUARD, because nothing pinned the field's ABSENCE. Re-adding it would
have been silent AND would have looked fine: every consumer still has a
comments[] fallback, so the board would keep working while the payload
quietly tripled. The guard asserts the thread is gone, the three
replacements are present, and - as a PROPERTY rather than a key name - that
no comment prose survives anywhere in the serialized node, so
reintroducing the thread under a different name also fails.

The full thread is still served by GET /chat/<card_id>.

## [0.20.0] - 2026-07-30
Cut so five merged PRs actually reach a running process. 0.19.0 was live on
the operator's board and none of tonight's work was in it — the same
merged-is-not-deployed gap 0.19.0 itself was cut to close, one turn later.

DM READ RECEIPTS FIRE AT ALL (#639). The operator reported a DM arriving
with the sent/queued/read lamps dead. Measured on the live store: every
operator -> agent message had ZERO receipts while every agent -> operator
message had one, and across the whole `dm_receipts` table only THREE
readers had ever written a row. A receipt was only written by
`dm_list(ack=True)`, which an agent receiving DM bodies over the channel
push never calls — so "the agent has not read this" was indistinguishable
from "the agent is dead", the one distinction the feature exists to make.

`_inbox.enqueue` now carries `msg_id`, which is the fix
`_dm_receipt_state.py` had already specified in prose, and receipts are
written at CONFIRM. Not at push: a returning `send()` proves only that the
stdout writer took the bytes, and treating that as delivery is what
destroyed weeks of operator DMs on 2026-07-29.

Two bugs fell out of that change:
- `msg_id` becomes the dedupe key. The old key was
  `(event_type, card_id, ts, actor)`; DM timestamps are second-resolution,
  so it was many-to-one BY CONSTRUCTION, and two distinct durable messages
  were measured collapsing onto one notification — the second never
  delivered.
- A FRESH STORE COULD NOT INITIALISE ITS INBOX. The legacy-YAML reader
  promised "malformed -> {}" but did not catch malformed-because-BINARY, so
  it raised on a SQLite store. Existing stores escape only because their
  migration flag predates the cutover. A NEW HOST IS THE FRESH-STORE CASE,
  so this sat directly on the multi-host path.

/graph PAYLOAD: EVERY CONSUMER MIGRATED (#637, #638, #640). comments[] is
8.5 MB of a 19.8 MB payload, and the board refetches it on nearly every
5 s poll because the store is written every ~4 s. Nothing in the board
requires it any more: the Matrix reads a new `rescore_history` field
(9,307 B — 0.11% of what it replaces), the detail panel reads the summary
scalars, the timeline footer reads `last_comment`, and the detail thread
fetches `GET /chat/<id>` on open. Every one keeps a fallback, so the field
can be deleted without a flag day — which is the point of doing it in six
pieces rather than one. The deletion itself is NOT in this release.

ADR-0016 amended (#636): the operator revisited the 2026-07-20
single-backend ruling, so storage plurality is permitted. Records the shape
it is permitted in, and scitex-db's correction that the board wipes were
caused by reconciling PEER stores where absence reads as deletion — not by
two backends existing.

## [0.19.0] - 2026-07-30
Cut because 0.18.0 had been published while develop kept accumulating — 59
commits, including four merged PRs that were reported as done and were not
live anywhere. The installed 0.18.0 still carried the cross-tenant store
fallback that #628 removed, and I cited #628 to scitex-hub as a reason to
open writes under their public mount. Merged is not deployed; this release
is the difference.

### Added

- **DM bodies render as markdown** (#629) — headings, lists, tables, fenced
  and inline code, blockquotes, bold/italic and links, built as DOM nodes
  rather than an HTML string. `chat_markdown.js` never produces markup for
  a parser to trust, so a hostile body cannot inject an element; only
  `https:`, `http:` and `mailto:` become anchors. The rendered path drops
  `white-space: pre-wrap`, since blocks already express the line breaks the
  plain path needed it for.

- **Every MCP `initialize` handshake is recorded to a sink that SURVIVES a
  restart.** `scitex-cards mcp start` answers its first `initialize` 7-14
  seconds after spawn — measured 2026-07-29 over five real starts: 6.67 / 7.04 /
  7.19 / 8.49 / 9.76 s. The variance is as dangerous as the mean: against a
  client with a fixed handshake timeout it is a coin flip, not a constant.
  Clients that give up mark the server "not connected", and the peer agent
  scitex-agent-container has repeatedly lost its card slice that way. It could
  not produce the client-side evidence either, because its stderr sink is
  TRUNCATED ON BOOT — a disconnect that precedes or causes a restart destroys
  its own trace, prospectively as well as retroactively.

  `scitex_cards._mcp_handshake_log` wraps the stdio transport inside
  `_mcp_channel._serve` and appends four facts per run to
  `<store_dir>/runtime/mcp-handshake.jsonl`: `server_start` (carrying
  `startup_s`, the gap between the process being exec'd and the serve loop being
  ready), `initialize_received`, `initialize_answered` (carrying `handshake_s`,
  the delta) and `server_exit`. The sink is opened `O_APPEND | O_CREAT` and
  NEVER `O_TRUNC`; past `MAX_BYTES` it rotates by RENAME. A log cleared on start
  is structurally incapable of retaining evidence about anything that causes a
  start, which is the whole reason the outage went undiagnosed.

  **It records the handshake that is never answered.** `initialize_received` is
  written the moment the session takes the request off the transport — before
  the server has any chance to answer — so a process killed mid-handshake leaves
  an orphan line behind, and that orphan IS the diagnosis. A sink that only held
  COMPLETED handshakes would be silent on precisely the failure it exists to
  catch. The observation sits at the TRANSPORT rather than in the message loop
  because `ServerSession` answers `initialize` inside the SDK and never yields
  it to the loop.

  **What it proved on its first run.** Across five real starts the handshake
  itself took 2.3-5.1 MILLISECONDS while `startup_s` was 4.36-8.80 s. The
  "7-14 second handshake" is not a handshake at all — it is import cost sitting
  in front of one, which is what
  `cli-startup-costs-5s-before-any-work-20260719` has to move. No optimisation
  is attempted here; this change is the measuring.

  Fails open — an unwritable sink disables the recorder, never the server, since
  diagnosing an availability problem must not create one. Measured cost:
  0.75-2.0 ms of one-time setup (published by the recorder itself as `setup_ms`,
  so its overhead appears in its own output), ~0.2 ms per recorded event, and
  under 1 microsecond per transport message. `$SCITEX_CARDS_MCP_HANDSHAKE_LOG`
  relocates the sink, or disables it with `off` — disabled hands the original
  streams straight back, so the transport is not wrapped at all.

- **The Stop hook is now a SECOND DELIVERY RAIL** — it delivers the agent's
  pending notifications itself, then requires the ack. Delivery had exactly ONE
  rail: the MCP channel push. An agent spec whitelisted `server:scitex-cards`
  while `.mcp.json` registered the server as `scitex-cards` (renamed during the
  migration), so Claude Code SILENTLY DISCARDED every push — `send()` returned
  normally, the drain acked on that success, and roughly three weeks of operator
  DMs were gone. Measured on the affected agent: **228 inbox rows, ZERO unseen**.
  sac found the same hazard armed on ~96 spec entries fleet-wide. Fixing that one
  spec is not a fix for the class: a single rail with nothing independent
  checking it fails again, silently, because *the transport returned* is not
  *the recipient received*.

  `scitex-cards stop-hook` now reads the store directly at turn end and puts the
  message text in the `reason` it hands back. No push involved, so a channel
  registration mistake cannot silence it.

  **The order of operations is the safety property.** PULL (a pure read, cursor
  untouched) → PRESENT (the reason IS the delivery) → only then REQUIRE the ack.
  A hook that merely blocked on unacked messages would have DEADLOCKED every
  agent on the morning of the outage: nothing had been shown, so nothing COULD
  have been acked. That is enforced structurally rather than by care — the new
  `scitex_cards._inbox_present.present()` returns `(text, presented_ids)` where
  `presented_ids` is exactly the ids whose content is in `text`, and the hook
  demands acks for those and no others. Overflow is counted out loud, left
  unconfirmed, and redelivered next turn.

  **Bounded**, because a hook that can refuse forever is a new outage: an
  unreadable store, an unresolvable agent id or any rail exception ALLOWS the
  stop and explains itself on stderr (each rail fails open independently); the
  same message stops being demanded after 3 unacked presentations in a session
  and the record is left unseen in the store; when the retry counter cannot be
  persisted the harness's own `stop_hook_active` becomes the bound; a reason
  that would be empty never blocks. Full table in
  `_skills/scitex-cards/23_stop-hook-second-delivery-rail.md`.

  Rides on `_inbox_confirm.confirm_notifications` — no second ack path. The hook
  reads across BOTH inbox keys (raw name and resolved `u_*` id) via
  `recipient_keys`, closing the same silent-miss shape as the outage itself:
  `_may_stop` read only the raw name.

- **`scitex-cards inbox ack --agent <a> <ids...>`** — the standalone surface onto
  the one existing ack verb. The Stop hook demands an ack, so an agent that
  installed scitex-cards and nothing else must be able to give one; without this
  the hook would block where the actor cannot remediate. No new ack path: it
  calls `confirm_notifications` like every other surface.

### Changed

- **`import scitex_cards` costs ~137 ms, down from ~425 ms** (#630) —
  `importlib.metadata` was imported at module scope purely to compute
  `__version__`, and reading package metadata drags in `email.message`,
  `email.utils` and `zipfile` behind it: 223 ms of the 425 ms total. That
  block sat three lines above the comment explaining that the PEP 562
  machinery exists to keep cold start under the audit §10 budget of 500 ms.
  `__version__` now resolves through the `__getattr__` that was already
  there. Measured interleaved on one interpreter, 5 rounds: before
  325–763 ms, after 108–168 ms — the distributions do not overlap.

  The public surface is unchanged: `scitex_cards.__version__` still answers,
  still prefers the `scitex-cards` dist, still falls back to `scitex-cards`
  for un-cutover editable installs, and `dir()` still lists it.
  `from scitex_cards import __version__` is covered separately because it
  takes a different path than attribute access.

- **`health()` check records are now THREE-VALUED.** A check's `ok` may be
  `True`, `False` or `null` (UNKNOWN — the check could not measure). "nothing
  is wrong" and "I cannot tell" are different answers and no longer collapse
  into a pass. `report["ok"]` counts only real failures, so an unknown does not
  redden a run, but every unknown is NAMED in `summary` and rendered `[????]`
  by `scitex-cards health`. The record keeps exactly its four standard fields.

### Fixed

- **Consecutive messages send again** (#627) — the double-send guard set a
  `sending` flag and released only the button, so one message per page load
  got through and the operator was reloading with Ctrl+Shift+R every time.
  Both halves of the guard now clear in the handler that runs on every path,
  including failure, and a failed send restores the optimistically-cleared
  text instead of eating it. Extracted to `chat_send.js` because `chat.js`
  had passed its size cap and could not be edited at all.

- **The board no longer falls back to the host store** (#628) — `dm.py`
  resolved the store as `getattr(request, STORE_REQUEST_ATTR, None) or
  _store_of(request)`. Under a multi-tenant mount, a request that arrived
  without the injected attribute silently read and wrote the HOST store —
  one user's writes landing in another's board. It now honours the request
  attribute only, and fails rather than guessing.

- **A fail-open test that never failed.** `test__stop_hook.py` arranged its
  "detector failure" as `SCITEX_CARDS_DB=/nonexistent/scitex-cards/none.db`,
  which was MEASURED (2026-07-29) not to raise at all — it reads as an EMPTY
  BOARD. Both fail-open tests passed for the wrong reason and proved nothing.
  The arrangement now uses a path that cannot be created
  (`/proc/1/.../cards.db`), which does raise.

- **A notification the client discards is now VISIBLE instead of gone.** The
  channel drain ack'd a record the instant `await send(params)` returned. That
  proves only that our own stdout writer took the bytes: a
  `notifications/claude/channel` push is a JSON-RPC NOTIFICATION, which by spec
  has no reply, and Claude Code silently DISCARDS a push from a server missing
  from its launch-line allowlist. So the drain was storing "the transport call
  returned" as "the recipient received it". Measured 2026-07-29: one agent's
  spec allowlisted `server:scitex-cards` while `.mcp.json` registers the server
  as `scitex-cards` (renamed during the migration) — 228 rows enqueued for that
  agent, ZERO unseen, weeks of operator DMs destroyed, every check green.

  The drain now writes a RECEIPT: `record_push` advances the cursor and stamps
  `pushed_at` in one atomic write, leaving `confirmed_at` for the recipient's
  own `ack_notifications`. Each record is still pushed exactly once (no
  redelivery); a receipt write that fails moves neither the stamp nor the
  cursor, so that record retries on the next tick, bounded as before by
  `MAX_PUSH_PER_DRAIN` (50 per tick).

  New health check `delivery_confirmed` reports notifications pushed and never
  confirmed past a 15-minute grace window, and its hint names both possible
  causes — the agent's `channels:` list not naming the MCP server that is
  actually registered, or a consumer that never confirms — plus how to tell
  them apart and that a restart is required.

## [0.18.0] - 2026-07-29

MINOR, not a patch. This cut ADDS two published URLs (`/board` and `/dm`) and a
new notification verb (`ack_notifications`), and DEPRECATES the ack-on-read
shape of `poll_notifications`. New surface plus a deprecation is a minor bump,
and calling it a patch would misreport what consumers are being handed.

The store-identity UUID fix associated with this window is NOT in this release:
it shipped in **0.17.13**, which is tagged and on PyPI. What is genuinely new
since that tag is #615, #616 and #617 — nothing else.

Two of those three reached `develop` with NO changelog entry at all. #615 and
#616 merged without touching this file, so the section below described only
#617, and a reader of the release notes would never have met either
user-visible change. Both are written up below for the first time, from their
commit messages. (Nothing was moved out of an older version's section this
time; the `0.17.10 / 0.17.11 — entries filed late` heading further down is a
PRIOR repair, already retired and documented, and is left untouched.)

### Added

- **`/board` and `/dm` are real routes** (#616). Both URLs 404'd. Not because
  they collided with the `dm/*` JSON API — `path()` matches exact strings, so
  `dm`, `dm/threads` and `dm/thread/<peer>` cannot shadow one another — but
  because the catch-all `<path:endpoint>` at the bottom of the urlconf
  swallowed both names into `api_dispatch`. The pages are now registered
  BEFORE the catch-all. `/` and `/chat` (and `/chat/`) are KEPT: this is an
  addition, not a rename, because a published URL is a migration and the
  operator has both bookmarked. `views._include_root` knows the new aliases,
  so the pages stay mount-aware under the hub sub-path, and its strip is
  anchored to a whole path segment — a naive `endswith()` would have rewritten
  an `/apps/scoreboard/` mount to `/apps/score`.

  The guard `test_no_dm_page_route_was_invented` is SUPERSEDED, not deleted.
  It was written against a label change and was right for that; the operator
  then asked for `/board` and `/dm` directly. Deleting a guard because it went
  red is how a rule gets lost, so the concern underneath it is restated: the
  replacement asserts the half that still matters — `/chat` and `/dm` resolve
  to one and the SAME view.

### Changed

- **One header, shared by both pages** (#616). The board rendered the page
  switcher on the left, the DM page on the right via `margin-left:auto`. The
  board bar was 8px/14px padding with a 48px floor, the DM header 10px/14px
  with none — two heights, two baselines. Both pages now render one partial,
  `_page_header.html`, with geometry in `page-header.css` and the switcher
  LEFT on both. Each page's own header rule was stripped of those metrics
  rather than left as a shadow copy, and keeps only its palette, fed in
  through two `--stx-cards-header-*` variables. The tests pin that the two
  pages AGREE, rather than pinning two positions that can drift apart.

### Fixed

- **The DESKTOP agent sidebar was blanked by the drawer logic** (#615).
  `chat.js` passes `#agents` as the drawer panel, but above the 720px
  breakpoint `#agents` is not a drawer — it is the permanently visible agent
  sidebar. `render()` set `panel.inert` and an inline `visibility:hidden`
  unconditionally from the open flag, and "closed" is the state at mount, so
  merely loading the module blanked the sidebar on desktop. An inline style
  beats the stylesheet, so no CSS could win it back. The operator saw
  "No agent selected." beside an EMPTY sidebar while `/dm/threads` returned 15
  agents and the tab title counted unread correctly. Reported twice. The API
  was healthy throughout, which is exactly why checking the API instead of the
  page missed it.

  `render()` now asks which mode the page is in instead of assuming. The
  COMPUTED display of `#menu-btn` already answers the question, so no
  breakpoint number is duplicated in JS to drift from the CSS. On desktop BOTH
  properties are cleared and the stylesheet decides: `inert` stops the keyboard
  and assistive tech, `visibility` stops the pointer, neither implies the
  other, and clearing only `visibility` leaves a sidebar that looks correct and
  cannot be reached by keyboard. Drawer mode is unchanged.

  This fix had already run live as a patch applied straight to the installed
  file on the operator box, where the next `pip install` erased it an hour
  after it worked. Shipping it in a release is what makes it survive.

  The older `test__chat_drawer_is_inert_when_closed.py` scans source TEXT, and
  every one of its assertions stayed green for the entire life of this defect:
  the lines it looks for were present and correct, the condition around them
  was missing, and a scan cannot see a missing condition. The new
  `test_chat_drawer.py` RUNS the shipped module under node and reads back the
  properties it actually wrote.

- **DM delivery is now LOSSLESS: handover is no longer confirmation.**
  `poll_notifications` marked notifications SEEN at the moment it handed them
  over, so a consumer that read with `ack=True` and then failed to deliver had
  PERMANENTLY DESTROYED the message — it left the unseen set and no retry could
  find it. We turned a transient delivery failure into permanent loss, and we
  made that the easiest call in the API to write. Measured on the live store
  2026-07-29: five operator DMs enqueued correctly, four marked SEEN, the agent
  saw none of them; the operator asked twice, eleven minutes apart, because
  nothing came back.

  New verb `ack_notifications(agent, ids)` (MCP tool, backend verb, and
  `POST /v1/rpc/ack_notifications`) is now the ONLY thing that advances the
  cursor, and it advances it per id. Reading never does. Anything left
  unconfirmed is redelivered on the next poll, so a consumer that dies between
  read and confirm loses nothing. Confirming twice is a no-op, never an error.
  `poll_notifications` additionally reports `unconfirmed` (the ids still
  awaiting confirmation) and `confirm_with`, so the safe loop is the obvious
  one to write.

### Deprecated

- `poll_notifications(ack=True)` — the ack-on-read shape above. Behaviour is
  DELIBERATELY UNCHANGED (sac reads this path today and a surprise change to
  their read path would be its own outage); it now announces itself on three
  surfaces instead: a `DeprecationWarning`, one WARNING log line per process,
  and an `ack_on_read_deprecated` field in the returned payload so the
  consuming agent reads it too. Use `ack=False` + `ack_notifications`.

## [0.17.13] - 2026-07-29

**0.17.12 was cut but never published, and the moving version string hid it.**
There is no `v0.17.12` tag, no GitHub release, nothing on PyPI, and `main` still
sits on the 0.17.11 merge: the newest thing anyone can install is 0.17.11. So
everything 0.17.12 claimed — the board that reads the database, and the HTTP 500
it was cut to end — reaches an installed package for the FIRST TIME here.
Bumping a number on a branch delivers nothing. Merged is not deployed, and
neither is bumped.

Cut now because SIX AGENTS ARE WAITING ON IT. sac cannot scrub the six container
overlays that our own error message dirtied until the fix that stops the SEVENTH
is on PyPI and content-verified. That fix is the currency-gate entry below. It
has moved up out of the 0.17.12 section, where it had been filed by mistake:
#609 merged AFTER that cut and has never been in any released version.

### Fixed

- **Store identity is a UUID, not a path — the ROOT FIX for the board's HTTP
  500.** Ownership of a database was decided by comparing PATHS, and one
  bind-mounted `cards.db` has three names here:
  `/home/agent/.scitex/cards/cards.db` (container only — the host cannot
  `stat` it), `/home/ywatanabe/.scitex/cards/cards.db` (both), and
  `/home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db` (the realpath). One
  inode, three spellings.

  `stamp_store_provenance` rewrote the stamp with the WRITER'S OWN spelling on
  every write (`ON CONFLICT DO UPDATE SET value=excluded.value`), so the name
  FLIPPED each time the other namespace wrote a card. Whenever it landed on the
  container-only name the host could not resolve it, `_same_file` fell through
  to a realpath STRING compare that can never match across that boundary, the
  ownership guard said "different store", and the board answered `GET /tasks`
  with HTTP 500. It was repaired three times on 2026-07-28 and broken three
  times again by nothing more than writing the next card from the other side.

  A path cannot be identity when two namespaces both write. So:

  - NEW `scitex_cards._store_uuid`. `identity_verdict(db_uuid, expected)` is a
    PURE function of two optional strings — no path, no connection, no
    environment — so no mount namespace can change the answer. Identities are
    minted as bare lowercase uuid4 and NEVER derived from a path, hostname or
    timestamp (a deterministic hash would reintroduce exactly the
    view-dependence being removed).
  - `_dual_write._db_mirrors_this_store` is uuid-first. On `ACCEPT`/`REFUSE`
    THE PATH IS NOT CONSULTED AT ALL. The legacy path compare survives only on
    `ADOPT` — no identity AND no expectation, which is every database today.
  - An unstamped database stays ADOPTABLE, so nothing existing breaks. A
    database with no identity facing a caller that NAMES one is REFUSED:
    adopting there would MINT the expected uuid into a database that never
    earned it, making a misresolution permanent, self-certifying identity.
  - The realpath STRING fallback in `_same_file` is REMOVED. It fired precisely
    when a path could not be `stat`-ed — i.e. across a namespace boundary,
    where it was least entitled to an opinion — and answered with the more
    destructive of the two options. That case now says CANNOT TELL honestly
    instead of claiming "stamped for a DIFFERENT store".
  - The path stamp is no longer rewritten when it already names the SAME FILE.
    It is diagnostic now, not authoritative, and rewriting it on every write was
    the flip mechanism itself.
  - IDENTITY AND RESOLUTION STAY TWO SEPARATE RULES. Nothing here bypasses
    `_paths.refuse_ambient_store_creation`; merging them would silently turn
    "no expectation is not evidence of a foreign store" into "use whatever you
    were pointed at".

- **The currency gate was MANUFACTURING the fault it detects.** Reported by the
  sac agent with a live reproduction inside their own container (scitex-ui,
  2026-07-29). The chain:

  1. the base image ships scitex-cards N; PyPI moves to N+1
  2. our gate REFUSES to run the CLI and prints `run: pip install -U scitex-cards`
  3. the agent does exactly that — inside a container that installs into the
     AGENT'S OVERLAY, not into the read-only base
  4. overlay N+1 alongside base N = TWO dist-info directories = ambiguous
     metadata
  5. which is precisely the integrity failure this gate exists to detect

  The remedy was the disease's vector, and sac was about to scrub six agents'
  overlays that our own error message could re-dirty the moment any of them
  fell back to the CLI.

  **The gate now BLOCKS WHERE THE ACTOR CAN REMEDIATE AND WARNS WHERE THEY
  CANNOT**, and that sentence is in the code as the rule rather than as a
  comment on one branch. On a BARE HOST an in-place upgrade genuinely repairs,
  so refusing is correct and that path is untouched: it still RAISES, and its
  message still carries scitex-dev's upgrade command verbatim, because there
  that command IS the repair. IN AN OVERLAY the agent cannot repair anything —
  the package comes from a read-only base they do not control, the only real fix
  is an operator REBAKE — so the gate logs a warning and RETURNS. Blocking there
  left an agent with no working rail AND a harmful instruction. A gate that
  cannot be satisfied is a trap, not a gate.

  **No in-place install command survives anywhere in the overlay output, from
  any source.** This is the correction of 0.17.11, which appended a do-NOT block
  AFTER scitex-dev's verbatim message and assumed that was enough — it is not,
  and assuming it was is the mistake being fixed. An agent scanning for an
  actionable command takes the FIRST one, and the first one harms. The verbatim
  passthrough is now SCRUBBED: `scrub_install_commands()` removes the command
  and keeps the facts, so the reader still learns which version is installed and
  which is current (exactly what the operator needs in the rebake request) with
  nothing runnable left in the text. The remover is narrow and its detector is
  broad; when they disagree the quote is WITHHELD rather than printed, because
  losing an upstream message costs a reader context while printing that command
  costs them the container.

  The overlay message states plainly that this container's package comes from a
  READ-ONLY BASE IMAGE, that installing here creates a DUPLICATE INSTALL which
  BREAKS METADATA RESOLUTION, and that the repair is an operator REBAKE OF THE
  BASE IMAGE. It names the escape hatch outright —
  `SCITEX_DEV_CURRENCY_SEVERITY=silent`, read off the installed scitex-dev
  (`staleness._ENV_SEVERITY`) rather than invented — so nobody has to guess it,
  and says why to prefer it over `SCITEX_DEV_NO_CURRENCY_GATE=1` (which prints a
  bypass banner on stdout and corrupts `--json` output).

  The barrier is MECHANICAL, not a docstring warning: a rule that must be
  remembered is forgotten exactly when it matters, which is what 0.17.11 proved.
  `test_the_overlay_text_carries_no_in_place_install_command_from_any_source`
  scans the ENTIRE emitted text — passthrough included — against an
  independently authored pattern list that is deliberately NOT imported from the
  implementation, and a positive control proves that list can actually see a
  command. Mutation-checked in two steps (probe confirmed present in the module
  the interpreter actually loaded via `inspect.getsource`, then the test run):
  making the scrubber a passthrough reddens that barrier; making the overlay
  branch raise reddens `test_the_overlay_case_does_not_raise`; making the
  bare-host branch warn reddens
  `test_a_bare_host_install_still_fails_the_currency_gate`.

- **A notifyd tick that could not read the store printed the same line as a
  healthy idle one.** Measured on the host: the daemon failed its store read on
  1196 consecutive ticks over roughly a day, and the only line anyone reads
  said `notifyd tick 1196: sent=0 failed=0 skipped=0 failed_terminal=0
  (0 recorded)` — character-for-character what a daemon with nothing to do
  prints. The exception WAS logged ("notifyd reminder sweep raised; continuing
  to delivery") but never COUNTED, so the summary stayed clean. The operator's
  DMs, including the answer an agent was blocked on, went undelivered; they
  asked "are you making progress?" twice, eleven minutes apart, because nothing
  came back. Their inbox held 196 notifications, all marked seen, none
  delivered.

  The defect fixed here is the COUNTER, not that particular outage (the store
  bug is its own change). The next delivery outage will have a different cause
  and must not be silent:

  - **A swallowed exception is a FAILED tick.** Every guard inside the tick —
    the reminder sweep, the liveness sweep, the heartbeat stamp, each
    recipient's inbox read, the clock, and the tick body itself — now RETURNS
    what it swallowed instead of discarding it. Guarding the loop against a bad
    sweep was always right; letting the guard also hide the failure is what made
    a day of silence look like a day of quiet. `sent=0` WITH a fault is a
    failure, and there is no longer a way to log one without the summary showing
    it.
  - **`pending` is THREE-VALUED.** "nothing pending" is `pending=0`; "could not
    determine what is pending" is `pending=unknown`. Any recipient whose inbox
    cannot be read poisons the whole count rather than contributing zero to it —
    a partial count presented as a total is a lie with a number on it.
  - **Consecutive failures ESCALATE**, getting louder rather than quieter: INFO
    while healthy, WARNING on a failing tick, ERROR once the streak reaches the
    threshold, carrying the count, how long it has been failing, and the
    underlying reason. The streak is persisted, so a systemd bounce does not
    reset the alarm to zero.
  - **A healthy idle tick stays at INFO and stays terse.** Making an idle daemon
    noisy is how alarms get ignored, which would reproduce this same outage by a
    different route.
  - **`scitex-cards health` gained a `delivery_liveness` check** reading
    `<store_dir>/runtime/notifyd-liveness.json` (last successful delivery, last
    ok tick, consecutive failures, reason). `notifyd_alive` only answers "is the
    process ticking" — it was GREEN throughout the outage, because the loop was
    spinning perfectly while doing nothing. The new check is three-valued too:
    no record reads `unknown`, never "healthy" and never "failing", because
    inventing a verdict from a measurement nobody took is the same class of lie.

  Shape: `TickReport` (`_delivery/_tick.py`) is a frozen dataclass with a
  validator that refuses an unexplained `pending=None` and a fault count that
  disagrees with the failure bookkeeping — malformed answers fail where they are
  built. `DeliveryLiveness` (`_delivery/_liveness.py`) folds one tick's outcome
  into the persisted streak. `deliver_pending` additionally returns `pending`
  and `faults`. Terminal-comm-miss reporting moved to `_delivery/_terminal.py`
  and the store checks to `_health_store.py` (both re-exported unchanged) to
  stay under the file-size budget.

- **The path stamp was claimed, then re-claimed, by whoever wrote last.**
  `stamp_store_provenance` described itself as idempotent — "a re-stamp with the
  same store is a no-op" — which is true only for a store with ONE name. This
  one has three for a single inode, so the no-op was a FLIP, and the repair lost
  a race to the next write that every container write wins. Measured live on
  2026-07-28/29: stamping a host-visible name took `GET /tasks` from 500 to 200
  serving 2,684 cards, and the next container-side card write put it back to 500.
  The stamp now answers WHICH STORE THIS IS, not WHO WROTE LAST — an unstamped
  database is claimed by the first write, a stamp already naming the same file is
  left alone however this writer spells it, and a genuinely different file is
  still refused upstream by the ownership guard. Sameness is asked of the SAME
  `_same_file` predicate that guard uses, so the stamper cannot disagree with the
  guard that reads its stamp. This shipped as a MITIGATION and says so in its own
  docstring: path identity cannot be made correct across namespaces, only stable.
  The uuid above is the real repair; this is what stopped the bleeding while it
  was being built.

### Added

- `scitex-cards store adopt-uuid [--uuid X]` — binds the resolved database to
  an identity, once, deliberately. Writes ONE `schema_meta` row and prints it;
  does not touch `store_path`, does not touch any card row, does not change
  what any resolver resolves. Idempotent.
- `resolve_store()` reports `store_uuid` and `expected_uuid`, and the `health`
  doctor's `store_identity` check NAMES the identity in its detail — on the
  passing branches too, so a registry can be populated from a healthy board.

  Design: `docs/design/store-identity-is-a-uuid.md`. The 16
  `xfail(strict=True)` spec tests written before the implementation now pass
  with their markers deleted and not one assertion edited.

- **The chat page's agent list has a fuzzy filter, and the matcher is
  scitex-ui's.** Operator standing request, repeated: 「普通にあいまい検索でフィ
  ルタはいつも入れてください；scitex-ui にもなければいけない話です」. The board
  already honoured it — its six filter `<select>`s are wrapped by scitex-ui's
  Combobox — but the chat page's agent list did not, and it is the list that
  grows without bound: every agent the fleet has ever registered, one flat
  column, findable only by scrolling. The filter consumes
  `STX.Combobox.fuzzyMatch`, the static scitex-ui exports beside the Combobox
  class for consumers that want a list narrowed rather than a `<select>`
  replaced. Writing a second subsequence matcher here would have meant two
  different search behaviours in one app, so a test asserts the module CALLS
  base's rather than reimplementing it; the substring fallback for an old
  scitex-ui is deliberately dumber, and a test pins that too, so a page running
  degraded is visibly rather than silently degraded.

  Two structural details are load-bearing. `renderAgents` clears its container
  every 5s, so the input lives OUTSIDE the rebuilt element (`#agent-list` exists
  to be the wiped part) — inside it, the operator's query would vanish four
  seconds after they typed it, which no screenshot taken right after typing can
  show. And because rows are hidden after render, that same rebuild un-hides
  everything, so a MutationObserver re-applies the filter; without it the filter
  quietly stops working and keeps looking correct until you glance away.
  Verified under jsdom against the rendered page, not asserted: typing `dvhlp`
  (a subsequence of `dev-helper`, a substring of nothing) leaves one row, `wtg`
  leaves both `worker-telegrammer-*`, a non-matching query says which word
  emptied the list rather than showing a blank column, and a forced repaint
  leaves the filtered set unchanged.

- **A guard that everything we load from scitex-ui actually arrives.** Both
  pages consume base through `if (window.STX && window.STX.Combobox) {…}`, which
  is the right shape — a missing component must not take the page down — and
  which also means a missing asset produces no error, no warning and no visual
  cue: the page keeps working in its degraded branch indefinitely. The new test
  resolves every `{% static 'scitex_ui/…' %}` path our templates reference
  through the real finders, and then executes the bundle we feature-detect to
  confirm it still attaches `window.STX.Combobox`. The second half is not
  hypothetical: on 2026-07-29 scitex-ui regenerated that bundle with
  `esbuild --format=esm`, producing valid JavaScript at the same path that
  passed every exists() check and set NO global — which would have pinned every
  consumer to its fallback permanently, with nothing failing anywhere.

- **The Python rail now tells you when the CLI rail is dead.** Measured by
  agent `grant` inside their own container: `scitex-cards --version` answered
  `0.17.7` while `scitex-cards list-tasks` REFUSED with "0.17.7 is behind latest
  0.17.9". Their card rail had been dead for HOURS with no way to know it. They
  reach the operator through the PYTHON path (`LocalBackend.dm_send()`), which
  does not pass the CLI/MCP currency gate — so DMs kept arriving normally and
  nothing ever prompted them to suspect cards was broken. One rail dead, one
  rail alive, and they were watching the live one.

  The fix is deliberately NOT "add the same gate to Python": that would take
  the LAST WORKING RAIL from an agent whose CLI is already refusing, which is
  strictly worse than the bug it fixes. `check_currency()` is unchanged and
  still ERRORS at the CLI and MCP entry points. What Python gets instead is a
  non-raising sibling: `currency_verdict()` answers in a fixed
  `CurrencyVerdict(state, detail, checked)` shape whose `state` is
  THREE-valued — `"current"` / `"stale"` / `"unknown"` — because scitex-dev is
  an optional dependency and ABSENT TOOLING IS NOT EVIDENCE OF CURRENCY.
  `warn_if_stale_once()` wraps it and logs ONE warning per process that names
  the sibling rail explicitly — "this Python call SUCCEEDED, but the CLI/MCP
  rail for this same package is currently REFUSING" — quotes scitex-dev's
  message verbatim, and prescribes a BASE REBAKE. The warning names BOTH
  console scripts, `scitex-cards list-tasks` **and** the still-installed legacy
  alias `scitex-cards list-tasks`, because the latter is what actually refused
  in the incident and is still what much of the fleet types; a reader must
  recognise the command they are running.

  A FAILING CURRENCY CHECK CANNOT TAKE THE PYTHON RAIL DOWN — and the guard
  states its limit rather than claiming a false absolute. It swallows every
  `Exception` **and `SystemExit`**, degrading all of it to `"unknown"`.
  `SystemExit` is deliberate and was a real hole: it derives from
  `BaseException`, not `Exception`, so a `sys.exit()` anywhere on the currency
  path used to propagate straight out of `dm_send` — measured, with the store
  never touched and the DM never sent, on the one rail this feature exists to
  keep alive. scitex-dev is optional and independently versioned and its API is
  deliberately not pinned, so "present but changed" is exactly the case covered;
  a library calling `sys.exit()` inside a diagnostic helper is a LIBRARY BUG and
  absorbing it is correct. The guard is NOT `BaseException`, and a test pins
  that: `KeyboardInterrupt` (and `GeneratorExit`, `asyncio.CancelledError`) must
  still propagate, because Ctrl-C is the operator's INTENT and swallowing it
  would trade one usability bug for another. Swallow library misbehaviour,
  propagate "stop now".

  The remedy is a base rebake and never an in-place `pip` upgrade, and a test
  pins that: inside an apptainer overlay an in-place upgrade leaves a whiteout
  masking exactly ONE dist-info name; on the next base rebake that whiteout
  covers a name that no longer exists, the new base copy is masked by nothing,
  TWO dist-info directories appear, and the rail is dead AT BOOT. (Measured:
  two agents, same version, same base, both healthy, OPPOSITE restart-safety,
  differing only in WHEN they upgraded.)

  Wired into the backend seam's messaging verbs — `LocalBackend.dm_send`
  (the confirmed entry point from the incident), `dm_list` and
  `poll_notifications` — and deliberately NOT into `_cli/_main.py` or
  `_cli/_mcp.py`, which already call `check_currency()`.

## [0.17.12] - 2026-07-29

Cut to DELIVER the board fix, not because the code needed a version. The
operator's board has been unusable for over a day — first serving a clean,
zero-card page while 2654 cards sat in the canonical database, then a bare
HTTP 500. The host runs a NON-EDITABLE wheel, so the fix reaches them the
moment it reaches PyPI and not one minute before. Merged is not deployed.

### Fixed

- **The board decided whether the store existed by looking for a file the store
  does not use.** `get_board` gated the CARD read on the existence of the
  `tasks.yaml` SIDECAR beside the database:

  ```python
  store_exists = resolved.exists()
  tasks = _load_global_tasks(resolved) if store_exists else []
  ```

  `resolve_tasks_path`'s own docstring says that path is "the non-task YAML
  CONTAINER path — NOT the store identity"; card data lives in the database.
  Under SQLite nothing creates that sidecar, so the gate was permanently shut
  and the board took the literal `else []`. The card read was never ATTEMPTED,
  which is why no guard anywhere had an opinion — the fail-loud reader in
  `_read_canonical_db_or_raise` was never reached. Worse, the same branch set
  `empty_store=True`, which tells the frontend to render a clean zero-card board
  instead of an error banner. That is the visual signature of a WIPE, shown for
  a store holding 2654 cards.

  `get_board` now reads the cards unconditionally: no `else []`, no empty
  fallback, no second read target. Every unreadable-store shape — absent
  database, foreign identity stamp, an export disagreeing with its own
  `COUNT(*)` — RAISES. `empty_store` is DERIVED FROM the read: true iff the
  store was read and held no cards. Emptiness must be read, never inferred. The
  sidecar now gates only what actually lives in it (`groups:`), through a named
  `_load_sidecar_groups`, so the two reads cannot be conflated again by moving
  one line. `_kick_board_refresh` carried the identical gate and could cache an
  empty board that its deliberate except-keeps-previous branch would then serve
  silently and indefinitely on `/graph` and `/timeline`; it is removed.

  This is the same defect as the deleted `_store_read_sqlite` accelerator
  (2026-07-21), whose post-mortem sits forty lines above the bug: a guard
  comparing against a YAML file that stopped existing at the cutover, silently
  degrading to an empty board. Fixing one instance of a pattern is not fixing
  the pattern.

- **The outage had no failing test because a fixture manufactured the missing
  file.** The `_django` conftest carried an autouse fixture that CREATED the
  `tasks.yaml` sidecar before every test in the package — precisely the file
  production does not have. Every test in the package therefore ran in a world
  where the gate was open, so the entire suite was green against a store shape
  that has not existed since the SQLite cutover. The fixture is deleted. The one
  test that genuinely needs a marker file — `test__board_stale_while_revalidate`,
  which exercises the stat half of the cache key — now creates it in its own
  fixture. The file is that test's subject, so that test owns it, and no other
  test is handed a precondition production never provides.

- **An unreadable store answered with an error page the board cannot parse.**
  `api_dispatch` swallowed `FileNotFoundError` into a fixed 400 "No task store
  found.", and every other load failure escaped the function entirely as an HTML
  error page — which the board's `fetch` cannot read. That is why the operator
  saw a bare HTTP 500 with no cause stated anywhere. It now answers an
  unreadable store with a JSON 500 carrying the store's own reason.

- **`/rev` reported the mtime of a file that does not exist, so an open board
  stopped refreshing.** The reported store mtime was the SIDECAR's; under SQLite
  that file is absent, so on any real deployment mtime was permanently `0.0`.
  The board's AutoRefresh keys on `f"{mtime}:{count}"`, so with mtime frozen the
  operator's open pane only refreshed when the card COUNT changed — a status
  flip, a reorder, a reassignment or an edited title never reached the screen.
  The store is the database, so the reported mtime is now the database's. WAL
  can move it without a card change, which costs exactly one extra `/graph`
  fetch: the frontend's `skipIfUnchanged` compares the fresh payload against the
  last rendered one and returns before re-rendering. A spurious refresh is
  invisible. A refresh that never happens is not.

- **A retired environment variable was believed as policy during triage.**
  `_env_compat.warn_retired_vars` now logs one ERROR per retired variable a live
  config still exports. The board unit carries `SCITEX_CARDS_READ_BACKEND`,
  which has zero references in `src`. It changed nothing, but it appeared to
  state the read policy, so it was trusted while the board was down and sent
  readers looking in the wrong place. The answer is never to make such a flag
  work again — a flag that can be flipped is a second target that merely happens
  to be switched the right way today.

- **The MCP and CLI surfaces introduced themselves as `scitex-cards`.** The
  package was renamed to `scitex-cards`, but what agents and humans actually
  READ still said the old name: the `.mcp.json` key the install snippet emits
  (which is the namespace agents see their tools under, `mcp__<key>__add_task`),
  every `{prog}` in help text on installs without scitex-dev, the `mcp doctor`
  and `health` payloads, and the shipped skills. Two of these were not merely
  stale but WRONG: `pip install 'scitex-cards[mcp]'` pointed at the superseded
  dist (the `[mcp]` extra is declared by `scitex-cards`), and the skills taught
  the `mcp__scitex-cards__*` tool namespace that no longer exists.

  Renaming the emitted `.mcp.json` key would, on its own, have left configs
  holding BOTH keys pointing at the same server — every tool loaded twice, both
  copies writing one store. So `mcp install --apply` now RETIRES our stale
  `scitex-cards` entry as part of writing the new one. Only our entry, matched on
  console-script basename plus the `mcp` verb: an unrelated server that merely
  shares the old key is left as found.

  What the package PUBLISHES is unchanged, because that is a migration and not
  a rename: the `scitex-cards` console script, the `SCITEX_CARDS_*` environment
  variables, the `scitex_cards.*` legacy entry-point groups, the
  `scitex-cards-notifyd.service` unit and the `scitex-cards.dashboard` job names
  all still work. Breaking any of them would have stopped the operator's running
  units — one of which serves the board, and another of which is the live
  systemd dashboard that execs the legacy console script.

### Added

- **The unread count now shows in the browser tab** — `(3) DM — SciTeX Cards
  v0.17.12`. Operator, 2026-07-29: 「新着がある場合、ページタイトルに新着
  メッセージ数（未読メッセージ数）を出してください。多少点滅などエフェクトが
  あっても良いかもです。」 They are migrating off Telegram onto this page, and a
  backgrounded tab looked identical whether or not an agent had written — the
  one thing Telegram did for them that this page did not.

  The count is NOT a new number. `/dm/threads` already returns per-peer
  `unread` and the drawer already paints it as a badge beside each peer; the
  title is handed that same array from that same 10s poll and sums it, so the
  tab and the badges cannot disagree. `chat_title.js` has no fetch of its own,
  and a test asserts it never grows one — a second reader of "how many unread?"
  is a second answer waiting for `mark_read` to land between them.

  The blink is BOUNDED and it is SILENT under `prefers-reduced-motion: reduce`.
  A title that flashes until you look is hostile and unreadable in the tab
  strip, so the alternation runs a fixed four half-steps at 700ms and then
  settles on the count permanently; it also fires only when the count RISES,
  because re-announcing a standing unread every poll is the forever-blink by
  another route. Under reduced motion the count still appears — suppressing the
  motion must not suppress the information.

  `chat.js` was at its 512-line budget, so attachment RENDERING moved into
  `chat_attach.js` — which already owned the upload that produces the url —
  rather than the budget being exceeded. The url shape is one decision; the
  code that writes it and the code that reads it now sit in one file. (The
  peer-list rendering was the other extraction candidate and was deliberately
  left alone: PR #604 is rewriting `renderAgents` in place, and moving a
  function out from under an open PR is a merge conflict chosen for style.)

### Changed

- **The DM surface is called "DM" everywhere the operator reads it.** Operator,
  2026-07-29: 「あと、"chat" となってますが、"DM" でそろえると良いと思います。」
  The switcher, the heading and the browser tab now all say DM (the longer form
  reads "Direct messages", their own preferred wording); the tab title was the
  last holdout, still rendering `Chat — SciTeX Cards v…` in the screenshot they
  sent. Tooltip and the switcher's `aria-label` moved with the label, because a
  screen reader announcing "Board or Chat" over a control labelled DM is the
  same inconsistency one layer down.

  **The route is still `/chat` and deliberately stays there.** Renaming a
  published URL is a MIGRATION, not a rename: the operator has it bookmarked,
  agents reference it, and both spellings are pinned by tests. The same
  reasoning leaves the JS module filenames (`chat_*.js`), the CSS classes and
  the template filenames alone — none of them is a string the operator reads.
  Tests hold both halves at once: visible text says DM, the URL still resolves
  to `chat_page`.

## [0.17.11] - 2026-07-28

### Added

- **Agents can attach files to a DM.** `dm_send_document(to, file_path,
  caption)` — mirrors claude-code-telegrammer's `send_document`
  argument-for-argument, so an agent that can send the operator a file over
  Telegram makes the structurally identical call here. Until now there was NO
  such API at any version: an agent asked which one to use and the honest
  answer was "none exists", so a PDF arrived as prose describing a PDF. That
  blocked the operator's migration off Telegram, because deliverables could not
  reach them at all. Bytes are COPIED into the existing attachment store (the
  source path is never recorded and never served from), reusing the same
  storage and URL scheme as operator-side uploads so one renderer serves both.
  The verb is deliberately absent from the HTTP backend surface — a path-taking
  verb there would be an arbitrary-file read.

### Fixed

- **A closed mobile drawer was still in the tab order.** It is hidden with
  `transform: translateX(-105%)`, and a transform moves PIXELS — it does not
  remove an element from the tab order or the accessibility tree. At phone
  width with the drawer shut, Tab put focus into the invisible agent list with
  no visible focus ring, and the next Enter opened a thread the operator could
  not see: the page appeared to jump on its own. Now `inert` (keyboard and
  assistive tech) AND `visibility: hidden` (pointer) — neither implies the
  other, so both are set and both are asserted separately.
- **The drawer and its scrim could desync and strand the operator.** Two bare
  `classList.toggle("open")` calls; `toggle()` flips whatever is there, so any
  path clearing one without the other diverged them — and `close()` is called
  from the thread-open handler. Once diverged, one tap put them in opposite
  states, the bad half being a scrim with no drawer: greyed screen, nothing to
  dismiss it, menu button behind it, force-reload the only exit. One boolean
  now owns the state; a test rejects bare toggles so the pattern cannot return.
  Escape closes it, and `aria-expanded` is maintained.

Both drawer defects were found by scitex-ui while harvesting the component, and
both were invisible to a screenshot, which is why they survived review.

## [0.17.10] - 2026-07-28

Cut to DELIVER the chat work, not because the code needed a version. The
operator is migrating off Telegram onto the cards chat TODAY and every fix
below was invisible to them while it sat on `develop`: they run an installed
package, not a checkout. Merged is not deployed.

Everything under the retired heading below was cut here or in 0.17.11.

### Fixed

- **The right-click menu rendered as unreadable grey.** Consuming scitex-ui
  0.12.0 exposed a latent defect: its context-menu stylesheet reads
  `--text-secondary` from `theme.css` but `--bg-secondary` from
  `primitives/colors.css`, and this page linked only the former. One token
  followed the theme, the other could never — dark grey text on a permanently
  dark fill. The items were LIVE and merely looked disabled, which is worse
  than broken because nobody files a bug against something that looks
  deliberate. Fixed by activating base's dark theme (`<html data-theme="dark">`)
  and taking every colour from scitex-ui: the page's local names are now
  aliases onto base tokens and ZERO hex literals remain in its own CSS. A test
  enforces that, because a written rule about not hardcoding colours is exactly
  what gets forgotten. (Operator directive: 「最適 ui を常に使ってください」.)

## [0.17.10 / 0.17.11] — entries filed late

The 0.17.10 cut left a live `## [Unreleased]` heading HERE, below its own
section, and later PRs appended to it — a search for "Unreleased" finds this one
as readily as the one at the top of the file. #611's notifyd entry landed here,
which would have shipped a day-long delivery outage documented below the 0.17.9
heading where no reader of the release notes would ever meet it. Those entries
have been moved up to 0.17.13. What remains below belongs to 0.17.10 and
0.17.11, both already published. The heading is retired so nothing lands here
again.

### Changed

- **Agents can attach a file to a DM** (`dm_send_document`). `dm_send` took
  `to` and `body` and nothing else, so there was no API for sending a file at
  all — an agent asked which one to use for a PDF and the honest answer was
  "none exists". The PDF arrived as prose describing a PDF. With the operator's
  conversation now largely moved onto cards DMs, that made real deliverables
  undeliverable: three SOHO application documents and a loan contract in a
  single day, all summarised instead of sent. The receiving half already
  worked, so this is the missing sending half and nothing more.
  `dm_send_document(to, file_path, caption)` mirrors
  claude-code-telegrammer's `send_document` argument-for-argument, so an agent
  that can hand the operator a file over Telegram makes the same call here.
  The bytes are **copied** into the existing attachment store and get the
  existing `attachments/<YYYY-MM>/<uuid>/<name>` url, so the chat pane's
  current renderer serves agent-sent and operator-uploaded files identically —
  no second storage layout, and therefore no second renderer. Storage layout,
  the `MAX_UPLOAD_BYTES` ceiling and the root-containment check move into
  `scitex_cards._attachments` as the single source for both entry points.
  The original path is never recorded and never served from (a file the agent
  later deletes still reaches the operator), and the verb is deliberately kept
  out of `BACKEND_VERBS` — `_server.py` dispatches that tuple over HTTP, where
  a path-taking verb would be an arbitrary-file read.

- **DMs live in `cards.db` (schema v5)**. Direct messages were the one piece of
  fleet data the canonical store's protections did not cover: they sat in a
  `threads.json` sidecar, so WAL, store-identity stamping, tombstones, the
  no-shrink guard, export and snapshot all applied to cards and to nothing the
  operator actually talks through. Appending one message rewrote the entire
  document — the same whole-document read-modify-write shape behind the
  2026-07 board wipes. Four append-only tables (`dm_threads`,
  `dm_thread_member_events`, `dm_messages`, `dm_receipts`) plus SQLite triggers
  that make `DELETE` and post-hoc edits unreachable at the ENGINE, not merely
  guarded in Python. `append_message` now writes the database FIRST and raises
  on failure; the sidecar is mirrored best-effort and kept complete as the
  rollback state. Backfill (`scitex-cards dm backfill`, **dry-run by default**),
  the A/B gate (`dm verify`) and an append-only cross-host union
  (`dm export` / `dm merge`) ship with it. Rehearsed on a copy of the live
  store: 165 threads / 2352 messages carried with the sidecar byte-identical
  afterwards, a re-run inserting 0, and `verify` clean.

  Two things the schema deliberately does NOT copy from the superseded
  `messages` table. There is no `recipient` column — recipients are derived
  from thread membership, which is the schema-level reason group DM was
  impossible and now is not. And read state is a per-reader receipts table
  rather than a boolean, because a scalar cannot say "Bob read it, Carol did
  not" — which also leaves `dm_messages` immutable, making a cross-host merge a
  pure union with no arbitration. The old `messages` table is left in place
  untouched forever: dropping a table holding real rows is a count decrease,
  the exact bug class this change exists to avoid.

### Fixed

- **The declared scitex-ui floor was two minor versions under what the code
  needs.** `pyproject.toml` asked for `>=0.7.1`; `chat.html` has documented
  `>=0.11.1` since #581 and said "the upgrade ships alongside this". It never
  did. 0.11.1 is where `.stx-app-context-menu__item` gets `font-family:
  inherit`, and the items are `<button>`s — buttons do not inherit the page font
  and base ships no global button reset, so a resolver honouring 0.7.1 gets a
  right-click menu rendered in the UA button font. `context-menu.css` EXISTS at
  0.7.1 without that rule, which is why no file-level check could have caught
  it: presence is not currency, and a floor is the only thing that expresses the
  difference. Measured against the scitex-ui tags rather than inferred.

  Worth recording what this was NOT: the board's Combobox was investigated on
  the belief it had been inert behind this same too-low floor. It had not been.
  The Combobox bundle first shipped in scitex-ui **0.6.0**, below even the old
  0.7.1 declaration, and a jsdom run of the rendered board confirms all six
  filter `<select>`s are hidden and replaced by live comboboxes with working
  subsequence matching. Nothing about the board's fuzzy filtering was broken —
  the floor bug is real and adjacent, not the same bug.

- **`threads_path()` no longer writes a file** (design part 2 §7.3). A PATH
  QUERY materialised the sidecar from a legacy `threads.yaml` as a side effect
  of being asked where the sidecar would be — a landmine that would re-create
  the retired file behind the migration's back, and the last YAML reader on
  this path. `attachments_root()` stops locating the attachments directory
  through that function and resolves from the store instead (same directory, no
  attachment moves). That decoupling is preserved where the layout now lives,
  `scitex_cards._attachments.attachments_root()`.

- **Board | Chat switcher on both pages** (#586). `/chat/` was reachable only by
  typing the URL — operator, 2026-07-28: 「今だと chat が隠し URL みたいに
  なってしまっているので、ホームに Board | Chat のスイッチャーを付けて欲しい
  です。」 With the migration off Telegram onto this chat under way, an
  undiscoverable chat page is a migration blocker, not a polish item. One
  partial (`templates/scitex_cards/_page_switcher.html`) renders on the board
  home and on the DM page, so the two cannot drift; every href is built from
  the view's `api_base` include root, the same mount-aware mechanism the
  board's `API_BASE` const and the chat page's `<body data-api-base>` already
  use — a hardcoded `/chat` is the bug class of #556 / #557 and is now linted
  against. The chat header's old one-way "← board" link is replaced by the
  switcher (the reverse trip was the missing half). Styling reuses the
  segmented-control shape the board's Layout axis already uses; it reads the
  host page's palette through six `--sw-*` variables and gets phone-sized tap
  targets under the board's own 768px breakpoint.

## [0.17.9] - 2026-07-28

Everything below already existed on `develop` and on the host — but under the
version string `0.17.8`, which was **already published**. Agents run their own
`/opt/venv-sac` inside a container image, so they saw `0.17.8` and got the OLD
code. sac measured it: same version string, `_may_stop.py` still on the bare
count, the new renderer absent. This release exists so the fixes can actually
reach a container, and it is the correction of a mistake — installing changed
code onto a host without bumping the version manufactured two different
codebases sharing one number, which is the exact "a version string is not
evidence of the code that runs" trap this project has hit repeatedly.

### Fixed

- **The unread-inbox signal names its sender** (#582). `may_stop` collapsed the
  inbox to `"N unread notification(s)"`, discarding the `actor` and `body` the
  records already carried. A bare count is unactionable by construction — it
  cannot distinguish the operator asking a direct question from a card-status
  echo, so deferring it is rational. On 2026-07-28 the operator asked an agent
  for its top-5 tasks twice, and told it they were migrating off Telegram, and
  all three arrived as the number `4` and went unread for two hours. This
  blocks the Telegram→cards migration rather than merely annoying. Note the
  consumer forwards this `reason` verbatim by design, so the wording here is
  the entire user-visible signal.

### Added

- **Chat attachments** (#580). `POST /dm/upload` (25 MB cap, store resolved
  server-side), files under `attachments/<YYYY-MM>/<uuid>/<name>` so identical
  filenames cannot collide, and a serve route that validates every path
  component *and* re-checks the resolved path is still inside the attachments
  root. Composer gains a paperclip button, clipboard paste and drag-drop;
  images render inline, other files as a download chip.
- **Right-click menu on messages** (#581), consuming scitex-ui's
  `.stx-app-context-menu` rather than shipping a private one — zero private
  menu CSS, asserted by test. Requires scitex-ui >= 0.11.1 (0.11.1 adds
  `font-family: inherit`, and the items are `<button>`). Reply prefills a
  quote; Copy copies the text.

## [0.17.8] - 2026-07-28

Card creation was broken fleet-wide, and the check that should have caught it
was the reason nobody saw it. Both are fixed here.

### Fixed

- **Card CREATE guards the resolved database, not a synthetic YAML label**
  (#574). `add_task` passed the ambient-creation guard a display label
  (`<db_dir>/tasks.yaml`) rather than the store's real location, and the guard
  answers "would this write MANUFACTURE a board?" with a literal
  `path.exists()`. The YAML tier was deleted (#512), so that label can never
  exist and the guard refused unconditionally: **every `add` failed for any
  agent whose environment lacked `$SCITEX_CARDS_DB`**, while reads and updates
  on the same store succeeded. The error even advised running `init-store`,
  which did not help, because the file it created was not the file being
  tested. The guard now receives `resolve_db_path(store)` — the same location
  `save_tasks` writes and `init-store` creates — so CREATE agrees with
  read/update. `_resolved_store` is unchanged, so the read surface is
  untouched. Reported and reproduced by scitex-ui on 0.17.7.

- **`health` measures store writability instead of asserting it** (#575).
  `_verify_db_store` opens the database `mode=ro`, learns nothing about
  writing, and then reported the store "readable, writable" — that word was a
  hardcoded literal, so it could never be false. This is why the create-path
  outage above stayed invisible: `add` refused every card while `health` called
  the same store writable. Writability is now measured with `os.access`,
  matching the sibling file-store branch that already did so. The store's
  **directory** is checked too, because SQLite creates `-wal` / `-journal`
  siblings — a writable file in a read-only directory still fails every write.
  Both failures name the offending path and say what to do.

### Notes

- The pre-existing decoy-board regression test previously passed for the wrong
  reason (it refused because the synthetic label never exists, not because the
  database was absent). It now passes for the right one.

## [0.17.7] - 2026-07-24

Delivery that admits when it is not working, and a chat page that is readable.

### Added

- **`channel_reaches_session` health check** (#566). The client surfaces a
  `notifications/claude/channel` push only from a server named on its launch
  line (`--dangerously-load-development-channels server:<name>`), matched
  against the key that server is registered under in the MCP config. A name the
  client does not know is discarded on arrival — and because a channel
  notification is fire-and-forget, the drain marks the record `seen` whether or
  not the push was accepted, so a mismatch does not delay delivery, it destroys
  it. Measured 2026-07-24: the package rename re-registered this server under
  its new name while agent launch lines still allowlisted the pre-rename one,
  and the fleet had been deaf to the board ever since.
  A self-test notification was consumed and marked seen within six seconds and
  never reached any session. `channel_capable` and `channel_drain` were green
  throughout; neither asks whether the far end accepts what we send. The check
  fails loudly, names both the registered and the allowlisted names, and prints
  the exact flag to add plus the fact that a restart is required. It identifies
  our server by program token, never by a substring of the command line — a
  substring match claims sac's `sac mcp channel --name scitex-cards` entry as
  ours and reports the channel healthy.

### Fixed

- **The chat thread renders in a readable centre column** (#567). Messages
  spanned the full pane (measured at 1820px), putting a bubble at ~1420px. The
  thread is now capped at 860px and centred, the compose box is capped and
  centred to match, and the input starts at three rows instead of one.
- **`/chat/` serves the DM page instead of 404ing** (#568). The page was
  registered only as `chat`; `/chat/` matched neither that nor
  `chat/<str:card_id>` (a str converter will not match an empty segment), fell
  through to the catch-all and answered `{"error": "Unknown endpoint: chat/"}`.
  Now dual-registered exactly as `legacy/` and `board-v3/` already were.
- **Chat timestamps render on the viewer's clock** (#569). `shortTs`
  string-sliced the ISO stamp and printed UTC digits under a local label, so a
  message stored at `20:39Z` displayed as "20:39" to a reader whose clock said
  05:39 the next morning. Now parsed and rendered at the viewer's own offset, so
  the date rolls correctly; a stamp carrying no zone is pinned to UTC before
  parsing (the store writes UTC), and an unparseable value is shown verbatim
  rather than as a confidently wrong time.

## [0.17.6] - 2026-07-24

Overdue-alarm correctness and store-export integrity, plus the hub-mount
integration follow-ups to #556.

### Fixed

- **`overdue=True` honours the time-of-day in datetime deadlines** (#563).
  `is_overdue` flattened every deadline to a bare date before comparing, so a
  deadline carrying a time (`2026-07-23T09:00`) was not overdue until its whole
  day had passed — and that filter is the only thing that surfaces an overdue
  card, so a timed deadline was a silent no-op alarm. A timed deadline is now
  overdue the moment its timestamp passes (aware-normalised, so naive-vs-aware
  never raises); a date-only deadline keeps its whole-day semantics; a recurring
  deadline stays never-overdue; the board date-pill is unchanged. A stored
  deadline the parser cannot read now logs loudly instead of silently reading
  as "not overdue".
- **Store export + verify-count come from ONE snapshot** (#562), killing a
  TOCTOU that could report a false `INCOMPLETE`.
- **The chat page is mount-aware.** `chat.js` fetched root-absolute `/dm/*`
  paths, so the DM page's data calls escaped a sub-path mount (the hub's
  `/apps/cards/`) exactly like the board's did before #556. `chat_page` now
  derives the include root from `request.path`, `chat.html` always renders it
  on `<body data-api-base>` (plus the "← board" header link now targets the
  include root, not the site root), and `chat.js` reads the marker — throwing
  loudly when it is absent, never silently guessing a root mount. Regression
  lint extended to `static/scitex_cards/chat/*.js`.
- **Honest empty state — an absent store renders 0 cards, not an error
  banner** (adapted from unpushed `9db9146b` to the SQLite-era `get_board`).
  A fresh workspace resolves to a store-identity path that does not exist
  yet; `load_groups` on that absent file was the one leftover raise that
  turned the new tenant's board into a 400 "No task store found." (and
  `/timeline` into a 500). Reads now return 200 with 0 rows and an
  `empty_store: true` flag on the `/graph`, `/tasks` and `/timeline`
  payloads; loud paths (unknown endpoint 404, handler exceptions 500, the
  FileNotFoundError → 400 backstop for mid-load raises) are unchanged.

### Added

- **Load-failure UI states on board_v3.** A failed `/graph` now reads the
  response body before giving up: the hub's signed-out 401
  (`{"error": "signed-out", "login_url"}`) renders a sign-in panel, the hub
  tenancy middleware's no-active-project 404 (`{"error", "hint"}`) renders a
  "No active project" panel linking the hint, `empty_store: true` renders the
  normal zero-card board, and anything unrecognized keeps the loud red error
  — now carrying the server's `error` field and the HTTP status.

### Changed

- `handlers/graph.py` line-limit split: the fleet-liveness builder
  (`_build_fleet` + helpers) moved verbatim to `handlers/graph_fleet.py`
  (its mirrored test file already existed under that name); `handlers.graph`
  re-exports, so dotted references keep resolving.

## [0.17.5] - 2026-07-21

One write target, one read path, loud failure everywhere else. Closes the
silent-wrong-board class found in production on 2026-07-21.

### Removed

- **The dual-write mirror is deleted as a feature** (#545). A stale provenance
  stamp plus the mirror env flag had routed a session's writes into a side file
  while every call reported success. SQLite is the only write target; a write
  that cannot reach the canonical DB raises. A sentinel test fails if the
  toggle is ever reintroduced.
- **The S2 read accelerator is deleted** (#547). On containers with the
  deprecated read-backend env set, it refused the unstamped DB and fell through
  to an empty bundled-example board while claiming reads were correct. The DB
  is canonical unconditionally; the backend env knobs are gone; an
  unresolvable store raises.
- **The dead legacy-sidecar import module is deleted** (#546), and CLI headers
  now name the real store instead of a legacy path.

- **The bundled-example resolver is deleted** (#554). No code path can resolve
  a packaged fixture as the store; an unresolvable store raises. (An
  env-lost agent resolving the example was the vector of the fourth wipe.)

### Added

- **`min_client_version` floor** (#548). The store can carry a minimum client
  version; an older client errors at DB-open — reads and writes both — with
  the exact upgrade command. Set only via the deliberate
  `db set-min-client-version` verb, which refuses a floor above its own
  version. Complemented by scitex-dev's currency gate.
- **The append-only invariant** (#552). A written card never physically
  disappears: the write chokepoint refuses any net row decrease,
  `delete_task` tombstones instead of deleting, and no flag opts out.
  (Operator ruling after the third wipe.)
- **The CURRENCY gate** (#550). Invoking the CLI or starting the MCP server
  on a stale or payload-broken install errors with the exact upgrade command
  (via `scitex_dev.staleness.ensure_current`, `scitex-dev>=0.34.0` as an
  optional extra; a no-op when scitex-dev is absent). Imports stay
  side-effect-free — the gate runs at invocation, never at import.
- **Forced test isolation** (#551). The test suite pins every
  store-influencing variable (including `SCITEX_DIR`, the leak that wiped the
  board) to a tmp store, and an end-of-session assert fails the run loudly if
  the real board's mtime changed. Full-suite runs mechanically cannot touch a
  live board.
- **Snapshot staleness guard** (#544). The hourly snapshot refuses to commit
  an export that does not reflect the DB's live state (count + newest
  last_activity), closing the false-green backup found the same day. Sits
  beside the existing shrink-refusal guard.

## [0.17.4] - 2026-07-21

The YAML-to-SQLite cutover release. SQLite is the store; YAML is gone from the
task path.

### Changed

- **`$SCITEX_CARDS_DB` is the sole store identity** (#540). The store is no
  longer identified by a resolved `tasks.yaml` path, ending the class of
  read-only / data-loss recurrences in which a YAML-path resolver re-stamped
  the database to a foreign store and locked writers out.
- **YAML sidecars migrated to JSON** (#541). The inbox, threads, notify,
  recipients, and delivery-ledger sidecars now persist as JSON via one-time
  migrate-and-drop; `db export` emits JSON. Functional YAML is eliminated from
  the task path. A documented residual (the high-risk users-registry heartbeat
  path, line-cap-blocked modules, and genuinely external formats such as
  agent-container `spec.yaml` and skill frontmatter) is deferred to 0.17.5.

## [0.17.3] - 2026-07-20

The store-safety release. Five fixes, each closing a path by which the fleet's
one board could be destroyed or the fleet could fail to boot.

### Fixed

- **`db import` could wipe the live board, and could not restore it** (#531).
  The importer resolved its destination from the ambient environment, so an
  import against any store could rebuild the one globally-resolved database.
  Store identity is now compared by inode, dissolving the class where two
  different path spellings of the SAME file (the store directory under its
  current name and under its pre-rename one) each re-stamped the other and
  locked writers out.
- **A write no longer manufactures a board nobody asked for** (#533). A write
  to a store that did not exist silently created it, which is how a packaged
  fixture came to be read as the board. The write now refuses.
- **The hourly snapshot no longer declares a YAML import** (#534). The cadence
  job declared `db snapshot --refresh`, which rebuilds the database *from* a
  YAML document — a data-loss engine on a timer once the database is the store.
  Removed at the declaration, with a test that fails if any scheduled job
  declares `--refresh` or `--from-yaml`. `--push` (the off-site backup) is
  kept.
- **Reconcile inserts and updates; it never deletes** (#536). A document that
  merely *lacked* a card destroyed it — the mechanism that removed the same 16
  cards twice in one day, because a writer holding a document read before those
  cards existed wrote it back and the diff called them "removed". The delete is
  removed from the reconcile path, not guarded: absence from a document is not
  evidence of deletion, it is evidence of a stale read. The explicit delete
  verb is unaffected.
- **The bundled skills directory is named for this package** (#532).
  `_skills/scitex-cards` → `_skills/scitex-cards`, with an in-repo compat symlink
  so the fleet's staging links do not dangle mid-migration (the failure that
  made every agent unstartable on 2026-07-16).

## [0.17.1] - 2026-07-19

### Fixed

- **The shadow DB mirrors ONE store — both write doors guarded** (#509).
  `mirror_after_save` and `write_doc_to_db` each resolved their destination
  from the ambient environment while taking the document from the caller, so a
  write to ANY store rebuilt the one globally-resolved database. A pytest
  fixture twice replaced the live board this way (2,136 cards -> 21; then
  2,138 -> 1 through the second, unguarded door). Ownership is now checked
  against the DB's own provenance stamp; the mirror declines, the canonical
  path raises.
- **A failed canonical READ no longer becomes a write of nothing** (#510).
  `export_doc(None)[0] or {}` promoted any failed read into an authoritative
  empty board, which read-modify-write then wrote over everything (2,138 cards
  -> 3, from one `comment_task`). A missing database now raises, and the
  export is cross-checked against `SELECT COUNT(*)` because the exporter
  answers a nonexistent DB with a well-formed empty document.
- **Malformed `SCITEX_CARDS_*` values are refused, not mirrored** (#508).
  An unexpanded `${...}` placeholder overwrote a working `SCITEX_CARDS_*`
  value, corrupting card authorship and silently relocating the store.
- Concurrency test's subprocess bound raised 30s -> 300s: it is a deadlock
  detector, not a latency assertion, and was failing on loaded CI runners.

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.17.0] - 2026-07-18 — an agent with work on the board cannot stop

### Added
- **`scitex-cards stop-hook` — the Claude Code Stop hook, emitted directly.**
  While an agent's board holds runnable work, that agent is EXECUTING;
  idle-with-work-pending is not a state the system passes through and
  repairs, it is a state the design makes unreachable. Prints
  `{"decision": "block", "reason": …}` to refuse a stop, feeding `reason`
  back as the agent's NEXT INSTRUCTION — a refusal that does not say what
  to do next just leaves the agent stopped-but-refused, which is still
  idle. Prints `{}` to allow.

  Three properties that are contract, not detail:
  - **Exit code is ALWAYS 0.** The decision lives in the JSON, never in
    `rc`. Anything gating on the exit status reads every verdict as fine.
  - **FAIL-OPEN by construction.** Unreadable store, unresolvable agent
    id, malformed card → allow the stop, with the reason on stderr. An
    agent wedged by *our* bug is worse than one that stopped early: the
    first is invisible and self-inflicted, the second is caught by the
    failure-net sweep. So this REDUCES silent stops; it does not make
    them impossible.
  - **The reason is capped** at 5 named items with a COUNTED `+N more`
    remainder. An instruction listing forty cards is not an instruction,
    and a silently truncated one is a lie about the board.

  Cards owns both ends of the format — what work exists AND what a useful
  next instruction reads like — so the runtime's remaining job is
  registration in `.claude/settings.json` and nothing else. An earlier
  design had the runtime parsing `may-stop`'s stdout, which made our
  output a public API it depended on. (#498)

  **It refuses for as long as the work is there.** There is no
  second-attempt exemption and no "we already asked" escape: while
  runnable cards remain, the stop is refused. The escape hatch is the
  behaviour we want anyway — RECONCILE the card. Close it, or mark it
  blocked with a NAMED gate, and `may_stop` stops counting it, so an
  honestly-reconciled board lets you stop while an untouched one does
  not. Runaway is bounded by the runtime's own consecutive-block floor,
  so the hook does not need to duplicate that.

### Fixed
- **The board could miss a write entirely — read-your-own-writes was
  silently broken.** The cache compared `stat().st_mtime`, a float of
  SECONDS; on a filesystem with 1-second timestamp granularity a write
  and the stat following it report the same value, so a STRICT read
  answered from the pre-write cache. The chat POST depends on this
  guarantee — it writes a message and reads it straight back. It only
  misfires when the machine is FAST enough to do both inside one granule,
  which is why it surfaced as a "flaky test" rather than a bug report.

  Invalidation now keys on `(mtime_ns, size, inode)` per source.
  `(mtime_ns, size)` alone is NOT enough: `st_mtime_ns` is
  nanosecond-TYPED, not nanosecond-ACCURATE, so a same-length edit (a
  priority `1` → `2`) still collides; the inode moves on every atomic
  `os.replace`. `BoardState.mtime` is unchanged and still reported — it
  is part of the `/rev` contract the frontend polls.

  The general rule, worth stating precisely because the loose version
  breaks working code: **never use `st_mtime` as an EQUALITY key.**
  Sorting by it and doing age arithmetic with it are unaffected. (#499)

## [0.16.2] - 2026-07-18 — upgrading no longer deletes your CLI

### Fixed
- **The scitex-cards stub now declares both console scripts** (`scitex-cards`
  and `scitex-cards`), healing the upgrade kill: old scitex-cards wheels
  (0.13.x–0.15.x) own both binaries in their RECORD, so `pip install -U
  scitex-cards` deleted BOTH during the old wheel's uninstall — and pip
  processes dependencies first, so a same-transaction scitex-cards
  reinstall could not save them. The stub installs LAST and recreates
  them. Venv-matrix verified: the 0.13.5 upgrade path, a fresh stub
  install, and a later scitex-cards force-reinstall all end with both
  CLIs present and running. Recovery for already-broken environments:
  `pip install --force-reinstall --no-deps scitex-cards`.
- **Hub token resolution fails loud on an explicitly-set but unreadable
  `SCITEX_CARDS_HUB_TOKEN_FILE`** instead of silently falling through to
  `~/.scitex/cards/hub.token` — the fall-through authenticated against
  the wrong hub whenever the default path held a foreign token (measured
  on CI the day a pilot host was provisioned). (#489, re-noted here
  because 0.16.1's changelog predated it.)

## [0.16.1] - 2026-07-18 — the GUI chat stops re-reading megabytes per click

### Changed
- **`/dm/threads` 10.7 s → 6 ms warm; thread open 2.7 s → 5 ms** (measured on
  the live GUI). Two read caches on the proven `(mtime_ns, size)` guard:
  the validated `users:` section (previously a full parse of the multi-MB
  store per request, for one registered row) and the `list_threads`
  summaries (previously an unread-count rescan of every record per call).
  Any store write rolls the key, so no reader — including the GUI unread
  badge across a `mark_read` — can be served stale content. (#485)
- **`_users/_store.py` split by data-flow direction** (`_store_read` /
  `_store_write` / thin re-exporting `_store`) per the line-budget
  protocol; every existing import keeps working. Write paths keep the
  UNCACHED section read under the store lock. (#485)

## [0.16.0] - 2026-07-17 — the backup rail runs itself, off-site, and the decoupling is a CI invariant

### Added
- **`db snapshot --push`** — pushes the snapshot repo to its remote after
  committing (`-u origin HEAD`, so the first push to a freshly-wired remote
  works). No remote = reported local-only (exit 0). A FAILED push exits 1:
  the rail's job is the off-site copy, and a local-only commit reported as
  success would be a lie. The off-site remote is the operator-chosen private
  repo `ywatanabe1989/scitex-cards-cards` (first snapshot verified by
  reading the remote back).
- **`scitex-cards.snapshot` cron JobSpec** (hourly, minute 7) running
  `db snapshot --refresh --push`: rebuild `cards.db` from the canonical
  yaml (import IS the freshness step pre-cutover), export to YAML text,
  git-commit, push off-site. Provisioned by `scitex-dev ecosystem up`.
- **`db snapshot --refresh`** — the rebuild-then-export halves as one
  systemd-safe argv.
- **The S7 decoupling gate** (`tests/test_decoupling_gate.py`) — CI-enforced:
  an AST scan proves no module imports `scitex_agent_container` /
  `claude_code_telegrammer` / `sac` (lazy in-function imports included), and
  a runtime CRUD probe proves none loads laterally. The operator's
  independently-usable rule is now an invariant, not a fact about today.

## [0.15.0] - 2026-07-17 — cards.db is the store's future; the yaml export is its backup rail

### Added
- **`db export`** — DB → YAML text, exact by construction: every record is
  reconstructed from its verbatim JSON payload (`tasks.card_json` — v2;
  `users`/`notifications`/`messages.record_json` — new schema v3), never from
  typed columns; unknown keys and per-record key order survive; a payload-less
  row is REFUSED loudly (re-import first), never exported stripped. Mutable
  flags (`seen`/`read`/`last_seen`) overlay from live columns.
- **`db snapshot`** — exports into a self-contained git repo and commits: the
  ADR-0010 backup/audit rail (git tracks an EXPORT, never live data).
- **`db rehearse`** — the cutover equivalence gate as one command: freeze
  (copy) the store + threads sidecar, import into a throwaway DB, export,
  deep-compare every section. READ-ONLY on the live store; exit 0 iff all
  sections equal; a failing run keeps its workdir as evidence. Proven EQUAL
  on the live 1,713-card store.
- **Schema v4** — `inbox_recipients` records the `inboxes:` map keys, so a
  drained (empty) inbox survives the round-trip instead of vanishing with its
  zero rows.
- **The scitex-cards deprecation stub** (`stub/scitex-cards/`) — a
  metadata-only dist depending on `scitex-cards>=0.14.0`, published from the
  same tag, so old `scitex-cards` pins keep resolving. Version sync with the
  main package is test-enforced.
- **ADR-0010** — `~/.scitex/cards/cards.db` as the single source of truth,
  the yaml-snapshot-export backup rail, and the fleet-cutover sequencing that
  gates the canonicality flip.

### Changed
- **The canonical DB path is `~/.scitex/cards/cards.db`** —
  `resolve_db_path`: explicit arg → `$SCITEX_CARDS_DB` → `$SCITEX_CARDS_DB`
  (deprecated, warned) → `local_state.user_path("cards", "cards.db")`. The
  pre-rename shadow `~/.scitex/cards/cards.db` is never moved or trusted;
  `cards.db` is rebuilt by the importer at cutover.

### Fixed
- **`health` no longer cries wolf on env-less shells** — the two card-state
  checks (`terminal_state_honest`, `no_falsely_blocked`) fed a bare `None`
  straight to `load_tasks` and reported "cannot read the task store
  (TypeError…)" (7/9 UNHEALTHY on healthy installs); both now resolve through
  the standard precedence chain like every other check.

## [0.14.0] - 2026-07-16 — the package is scitex-cards now (scitex-cards stays as a shim)

### Changed
- **Package identity: `scitex-cards` → `scitex-cards`** (operator directive
  2026-07-15/16; stage 1 of the migration, card
  `scitex-cards-s1-package-identity-rename-20260716`).
  - PyPI/dist name `scitex-cards`; import package `scitex_cards` → `scitex_cards`
    (380 files, `scitex-dev rename-symbols`, CHANGELOG history left untouched).
  - Both console scripts ship and resolve to the same CLI: `scitex-cards`
    (canonical) and `scitex-cards` (legacy, kept for the un-cutover fleet).
  - MCP server identity is `scitex-cards`.
  - Entry points under `scitex_dev.*` register the new key only (those groups
    are iterated, so a legacy twin key would list the package twice — and
    `scitex_dev.jobs` would double-schedule every job).

### Added
- **`scitex_cards` import shim** — `import scitex_cards` (and any
  `scitex_cards.<submodule>`) resolves to the very same module objects as
  `scitex_cards` via a meta-path finder: one import, one module state, never a
  duplicated copy. Emits a `DeprecationWarning`; ships for one transition
  window only.
- **Environment dual-read** (`scitex_cards._env_compat`, operator-requested) —
  every `SCITEX_CARDS_<X>` env var is mirrored onto `SCITEX_CARDS_<X>` at
  import, so shells already exporting the new names work today while the
  un-cutover fleet's old names keep working with one deprecation warning per
  process. When both are set, the new name wins, loudly.
- **Legacy entry-point groups honoured** — hook plugins registered under
  `scitex_cards.hooks` and delivery channels under
  `scitex_cards.delivery_channels` stay discoverable alongside the new
  `scitex_cards.*` groups until producers re-release.

### Changed (repo)
- GitHub repository renamed **scitex-ai/scitex-cards → scitex-ai/scitex-cards**
  (operator-approved); old URLs auto-redirect. `[project.urls]` and README
  badges follow. RTD project rename is still pending (docs URL unchanged).

### Unchanged (deliberately — later stages)
- The store path (`~/.scitex/cards/tasks.yaml`): the store engine flips to
  sqlite-as-truth and the file moves in the migration's later stages.

## [0.9.9] - 2026-07-13 — fix: a flag that outran its deploy cost 135 seconds per card write

### Fixed
- **`SCITEX_CARDS_DUAL_WRITE` now refuses to turn on where the code cannot honour it.**

  MEASURED on a live 1,449-card store, in the configuration that was actually running:

  | | |
  |---|---|
  | scitex-cards **0.9.4**, dual-write **ON** | `add_task()` = **135.2 s** |
  | scitex-cards **0.9.4**, dual-write **OFF** | `delete_task()` = **3.8 s** |

  **35×. One flag.**

  The flag was switched on because the incremental mirror had shipped — and it *had*, on PyPI.
  But containers do not run PyPI; they run a wheel **baked into an image**, and that image was
  still 0.9.4. So the flag did not enable the incremental mirror. **It enabled the full rebuild
  that the incremental mirror had replaced** — an O(n) rewrite of every row of every table on
  every card write, which grows with the board (8.69 s at 1,370 cards; 135 s at 1,449).

  `merged != released != installed != RUNNING.`

  **The fix lives in the code, because a precondition that lives only in a conversation is not a
  precondition — it is a hope.** `enabled()` now requires the env var *and* proof that this
  process can actually do an incremental mirror.

  **The probe asks for the SYMBOL, never a version string.** It imports
  `_db_mirror.mirror_doc_incremental`. A version string is metadata, and metadata lies — a stale
  wheel, an orphaned `.dist-info`, an image baked months ago all report a version that outlived
  the code beside it. The only honest question is *"is the function here?"*, so the probe answers
  it by importing it.

  Fails **safe** (writes proceed at full speed; only the mirror is skipped, and `db import`
  rebuilds it), **loud** (ERROR, with the measured cost and the recovery path — including that a
  container restart alone will *not* update a baked wheel), and **once** per process, because the
  same message on every write is noise that teaches the reader to skip the channel.

## [0.9.8] - 2026-07-13 — cli: `scitex-cards` is a real command now

### Added
- **A `scitex-cards` console script.** The product is called SciTeX Cards, the board says so,
  and the operator typed `scitex-cards gui serve` and got `command not found`. The alias had
  been written and reviewed and was sitting in an unmerged branch — which is the same as not
  existing. It ships now.

  `scitex-cards` and `scitex-cards` are the SAME entry point: every verb, identical behaviour.
  This is the console script only — the package, the module, the MCP tool prefix and the store
  path are untouched (the full rename is a separate, coordinated effort).

## [0.9.7] - 2026-07-13 — board: it is SciTeX Cards now; two views gone, the Graph pre-rendered, a Details column added

Operator-driven overhaul of the board. Everything below was verified in a real browser against
the live 1,390-card store, not inferred from the diff.

### Removed
- **Column and Table views are gone**, along with the controls that only served them (Sort,
  Group-by, bulk-select, hide-project). Column was the DEFAULT, so the default moves to
  **Timeline**. Anyone whose browser still remembers `column` or `table` is migrated to
  Timeline on load — **not** dropped on a blank board. (This board has shipped a blank board
  twice this month; the migration is deliberate and tested.)
- **The header is quiet again**: no Reload button, no Hide-project control, no oversized
  "Blocking me" readout (the legend already says it), no `new/24h` counter in the bar.
- **The status ring around each agent icon is gone.** The icons stay — the operator likes them.
  The *ring* nobody could read: "I could not tell what the ring around
  the agent icons represents" (operator, translated).
  It encoded status around a glyph that encodes identity, with no legend entry to decode it.
  Displayed is not the same as read.
- **Wall: one icon per agent tile**, not one per card. 50 islands, 50 icons — down from 161.

### Added
- **A Details column on the right**, built on scitex-ui's real `.stx-shell-sidebar` primitive
  rather than a bespoke layout, so the board collapses and responds like the rest of the
  ecosystem. It holds the filters, the `new/24h` counter, and:
- **A Stats panel that matches the Legend** — the same statuses, in the same order, in the same
  colours, sharing one source of truth in code. Two lists that must agree and are maintained
  separately will drift; these cannot.
- **A d/w/m timescale**, which drives (and is driven by) the Timeline's *existing* window rather
  than inventing a second, subtly-different notion of "week".
- **Timeline hover feedback**: the hovered lane highlights and only that lane's dots grow.

### Fixed
- **Search autocomplete has been silently dead.** `searchQuery.js` and `searchSuggest.js` both
  declared a top-level `const _api`; in a classic `<script>` that is one shared global binding,
  so the second file threw `Identifier '_api' has already been declared` at parse time and never
  ran. No error surfaced to the user — the dropdown simply never opened.
- **The Graph no longer freezes the page.** It is rendered off-DOM ahead of time and cached, so
  switching to it shows a finished diagram (measured: on screen within 300 ms, 1238 px inside a
  1300 px canvas — fitted, not tiny). Rendering into a `display:none` container would have
  produced a zero-width, unscaled SVG; it deliberately does not.

### Changed
- **The product is called SciTeX Cards.** Display strings only — package, CLI, MCP tool prefix
  and store path are untouched (a coordinated rename is a separate effort).

## [0.9.6] - 2026-07-13 — fix: health called a LIVE daemon dead, and the one recovery path it offered could not start

### Fixed
- **`health` reported the notify daemon DEAD while it was running and ticking.** The check read
  the pid from the pidfile and probed it with `os.kill(pid, 0)`. But notifyd runs on the HOST
  while agents run in CONTAINERS — same bind-mounted store, **different PID namespace**. The
  host's pid does not exist in the container's `/proc`, so the probe raised
  `ProcessLookupError` and the check reported "stale pidfile: pid N is not running",
  confidently, and permanently.

  **A pid is only meaningful inside the namespace that issued it.** The check was drawing a
  conclusion from a number it had no standing to interpret. Liveness across that boundary is
  now judged by **freshness, not identity**: notifyd re-stamps its pidfile every tick with
  `pid_ns` / `boot_id` / `host` / `interval` / `heartbeat`; the check probes the pid only when
  the pidfile came from *this* PID namespace, and otherwise judges by heartbeat age (3× the
  recorded interval, 60s floor). An undeterminable state now degrades to a truthful non-verdict
  instead of a false failure.

  (Hostname would NOT have worked as the discriminator — Apptainer shares the UTS namespace, so
  the container's hostname is *identical* to the host's. Only the PID namespace distinguishes
  them.)

  **Fail-loud is preserved deliberately**: a *local* daemon whose pid is gone still reports DEAD
  even with a fresh heartbeat. Freshness must not paper over a corpse we can actually see.

- **The systemd unit template could not start.** `scitex-cards notifyd install-unit` emitted
  `ExecStart=scitex-cards notifyd` — a bare command. systemd does not use your login PATH, and
  the console script lives in a venv, so the unit died with `status=203/EXEC`. The one durable
  recovery path the tool offered was itself broken. `ExecStart` is now resolved to an absolute
  path at generation time, and generation **raises** rather than writing a unit that is
  guaranteed not to start.

### CI
- **A parked workflow was manufacturing a red X on every push.** It was disabled with `on: {}`,
  which GitHub does not read as "disabled" — it treats a workflow with no valid trigger as a
  *broken file*, and created a zero-job run on every push to every branch, failing each in 0s.
  A check that is always red is not a signal; it teaches everyone that red means "that's just
  the broken one". Parked properly with `workflow_dispatch:`.

## [0.9.5] - 2026-07-13 — perf: a card write no longer drags the whole board through SQLite

Two fixes to the dual-write mirror. Together they take the mirror from **more than half of a
card write** down to **under 2% of it**.

### Performance
- **The mirror now writes only the cards that actually changed.** It used to `DELETE` and
  re-insert every row of every table on every single write — 1,370 cards and 3,073 comments
  rebuilt because you edited one card. That rebuild was **8.69 s of a 16.31 s card write**,
  and it *grew with the board* (1.24 s in the morning, 8.69 s by the evening). Worse, it ran
  **inside the store lock**, so it doubled the critical section — and therefore the convoy —
  for every other writer. It now diffs by card hash: **8.69 s → 0.199 s**. (#401)

- **The full rebuild that remains was 86% one word of SQL.** `INSERT OR REPLACE INTO tasks`
  cost **4,592 µs/row** against **110 µs/row** for a plain `INSERT` — a **42x** difference,
  and 6.3 s of the rebuild's 7.3 s. `tasks` is a *parent* of `task_comments` / `task_edges` /
  `task_roles` (`ON DELETE CASCADE`), so under `PRAGMA foreign_keys=ON` every REPLACE runs
  SQLite's full cascade/FK-check machinery — to resolve a collision that **cannot happen**,
  because the rebuild has just deleted every row in the same transaction.

  It was never foreign keys: `task_comments` already used a plain `INSERT`, and FK
  enforcement costs it *nothing* (150 vs 149 µs/row, FK on vs off). It is REPLACE **on a
  parent row** that is expensive. The rebuild — now the `db import` / post-failure
  re-bootstrap path — drops from **7,299 ms → 1,415 ms**, verified byte-for-byte: every row
  of all seven tables hashes identically before and after. (#402)

### Fixed
- **A duplicate card id is no longer swallowed in silence.** `INSERT OR REPLACE` absorbed it
  — and still appended *both* copies' comments. The mirror now keeps the same winner
  (last-wins) and logs the data bug loudly. Two cards cannot share an id.

## [0.8.6] - 2026-07-12 — fix: the WIP gate refused to let an agent record a P0; deadlines documented honestly

### Fixed
- **The board REFUSED to let an agent record a P0 incident.** The operator escalated a
  fleet-wide config/state-loss hazard; scitex-hub went to card it and the WIP gate said:

      WIP gate refuses add: scitex-hub already has 40 open tasks (>= 2 × limit 20).
      Close existing tasks before adding more.

  They had to bury the incident as a comment on an unrelated card — the worst outcome for
  the one class of card that most needs to be findable. A WIP cap is a **throughput-shaping**
  device and it was sitting on the **emergency-recording** path. "Your board is untidy" must
  never mean "you may not record that production is on fire."
  Worse, the old message created a **perverse incentive**: under outage pressure the cheapest
  way past the gate is to *close cards you have not finished*. A cap that pressures agents to
  falsify card state during an emergency is worse than no cap.
- **`priority <= 1` (P0/P1) is now never gated.** No flag to remember mid-outage — filing an
  incident simply works. Keyed on priority, deliberately *not* on a new `kind` enum value:
  adding an enum value would brick every agent still on a pre-0.8.0 reader, which is exactly
  the 2026-07-10 fleet outage this project already lived through.
- **The bypass is STAMPED, not silent.** A card admitted over the cap carries a
  `kind: wip-override` audit comment (the agent's WIP count and the limit), written inside the
  same locked insert. Abuse is self-reporting — and it makes priority inflation *measurable*
  rather than invisible. A silent bypass would be its own silent-absence bug.
- **The refusal message now names the emergency path** and says explicitly: *do not close
  cards you have not finished to get past this gate.*

### Changed
- **Deadlines are documented honestly.** A deadline drives no notification — nothing in the
  delivery surface (`_reminders`, `_stale_active`, `_stale_active_nudge`, `_delivery/*`) reads
  it. And a **recurring** deadline is never even *overdue*: the repeater always rolls the next
  occurrence into the future, so `is_overdue()` can never fire for one. A recurring deadline
  therefore reaches **neither** rail. This is now stated in all seven places deadlines appear —
  including the org export, which emits `DEADLINE:` lines into org-agenda (a real reminder
  engine) and so invited precisely the wrong inference. Pinned by a behavioural test: the
  sweeps produce byte-identical output with and without a deadline, plus a structural guard
  that fails if any delivery module ever starts reading one.
- If you want to be nudged: keep the card open and owned. The stale-active and backlog sweeps
  nudge on **real neglect**, which for an ongoing responsibility is the better signal anyway.

## [0.8.5] - 2026-07-12 — fix: `status=""` was a SILENT DELETE; clearing an enum field now deletes the key

### Fixed
- **`status=""` silently deleted the status, minting a card with no lane.** The MCP layer
  mapped `"" -> None` for *every* field, so an empty status removed the key behind the
  store's back. A status-less card has no lane on the board and drops out of every
  status-filtered view — it does not error, it simply **vanishes**. Enum fields now pass
  through verbatim and the store owns the rule.
- **Clearing a blocker with `""` was the one documented way that could not work.** The MCP
  docstring promises *"pass an empty string to CLEAR a string field"*, but on the store
  primitive `blocker=""` wrote the literal empty string and the validator then rejected the
  save:

      TaskValidationError: invalid blocker ''; must be one of
      ('compute','dependency','dep','operator-decision','agent-wait','none') or absent

  Worse, it failed at SAVE time — after the caller had built a mutation it believed valid —
  so in a bulk script it aborted the **whole batch**. Now `""` on an enum field means
  DELETE-THE-KEY, consumed in the update path before the lock is taken, so a doomed
  mutation never acquires it and `""` never reaches the validator as a value.
- The CLI could not clear `kind` at all (strict `click.Choice` rejected `''` at parse
  time), so the documented contract had no CLI form. Added, mirroring the blocker flag.

### Decisions
- `blocker` — **clearable** (`""` or whitespace-only deletes the key).
- `kind` — **clearable**; an absent `kind` already means `task`, so clearing is meaningful.
- `status` — **NOT clearable, refused loudly.** A card's status is its *decision*, not an
  optional label — the same reasoning that abolished `pending`. `status=""` now raises,
  naming the reason and the valid set, rather than being silently swallowed.

### Notes
- The validator is untouched: `blocker="banana"` still raises. The guard refusing
  `status: done` while a blocker is still set is untouched and pinned by a regression test —
  a done-but-blocked row is incoherent and that guard is correct.

## [0.8.4] - 2026-07-11 — fix: the MCP instructions taught a DEAD identity; agents saw 3% of their own cards

### Fixed
- **The board was telling every agent to look in an empty drawer.** The MCP server
  instructions — read by every agent at session start — hard-coded a dead example:

      "Use list_tasks with a `scope` arg (e.g. 'agent:proj-scitex-cards')"

  There is no `proj-scitex-cards`. Measured against the live store, that taught scope held
  **2** cards while the real one (`agent:scitex-cards`) held **63**. So an agent that
  *followed the shipped instructions* saw ~3% of its own work and reasonably concluded the
  board had nothing for it. Nothing errored; the query simply returned almost nothing.
  This is a mechanical explanation for the standing complaint that "the fleet ignores the
  board" — the board was not being ignored, it was lying about where to look.
- The instructions now interpolate each agent's **resolved** identity
  (`$SCITEX_CARDS_AGENT_ID`). When the identity **cannot** be resolved they say so and tell
  the agent how to discover its scope, rather than falling back to a hard-coded example. A
  silently-wrong example is worse than an honest absence — that was the entire bug.
- The same dead prefix was fixed everywhere it was taught, not just the one line: CLI
  `--help` examples, docstrings, the shipped skills, the README and the fleet cheatsheet.
- **`sync-github` was still MINTING cards under the dead `proj-scitex-dev` owner.** Fixing
  the instructions while a write path kept re-creating the problem would have left the hole
  open: every card it imported landed under an owner that does not exist, so the real owner
  never saw it.

### Notes
- A regression test asserts zero dead-identity examples across the entire MCP surface
  (instructions + every registered tool description).
- Data half (applied to the live store, outside this release): 37 cards were stranded under
  dead `proj-*` scopes — 34 of them scitex-writer's, three still `blocked`, i.e. live work
  its owner could not see. Migrated after a dry-run and a backup; verified 0 dead scopes and
  0 dead owners remain. scitex-writer's visible slice went 21 → 55 cards, and its owner
  confirmed all five newly-visible blocked cards are real.

## [0.8.3] - 2026-07-11 — fix: the liveness nudges reached NOBODY; they now ride the inbox rail

### Fixed
- **The fleet-liveness sweep delivered to nobody.** v0.8.2 gave the sweep a scheduled
  caller — and it then ran every 30 minutes reaching zero agents. Verified against the
  live daemon:

      liveness sweep: ERR  scitex-cards    32 pending  wire=http  reason=transport-error
      liveness sweep: ERR  scitex-types    2 pending  wire=http  reason=no-turn-url-configured
      liveness sweep: ERR  scitex-writer   3 pending  wire=http  reason=no-turn-url-configured
      liveness sweep: # 0 pending-backlog push(es) sent

  Root cause: two delivery rails exist and the nudge used the wrong one. The digest
  (which works) enqueues into each recipient's pull-inbox; the nudge instead pushed over
  the HTTP turn-url rail, which is not provisioned for nearly any agent. Nudges now
  enqueue on the same inbox rail as the digest, using the same helpers and record shape,
  so agents' existing drain path picks them up with no change on their side.
- **A sweep that reaches nobody now SCREAMS.** When every attempted owner fails, the log
  emits `!! ALERT <kind>: 0 of N attempted nudge(s) delivered — this sweep reached
  NOBODY`. The old quiet `0 sent` is exactly what let a completely dead sweep ship and
  look healthy. An all-suppressed sweep does not cry wolf.
- Summary line now reports `detected / delivered (inbox) / suppressed / failed`.

### Changed
- `_push` (HTTP turn-url) is kept only as an opt-in secondary echo
  (`SCITEX_CARDS_NUDGE_PUSH=1`). It never counts toward delivery, never arms suppression,
  and there is no silent fallback between rails in either direction.

### Notes
- Preserved from 0.8.2: deliver-on-change suppression (fingerprint = the set of stale card
  ids) with a 24h floor; only a DELIVERED nudge arms suppression; fail-soft per owner;
  suppressed owners still logged; `(unassigned)` surfaced but not delivered.
- Verified END-TO-END against the live fleet, not just in tests: after the fix, a
  `stale-active` nudge was delivered into a running agent's session through the inbox
  rail. That is the first time the fleet-liveness check has ever reached anyone.

## [0.8.2] - 2026-07-11 — fix: the fleet-liveness sweep actually runs, and nudges deliver on CHANGE

### Fixed
- **Nobody was checking whether agents were still working.** `sweep_and_nudge()`
  detects owners whose `in_progress` cards have gone untouched past a threshold
  and nudges that owner — but its only caller was the interactive `stats` CLI.
  It was pulled out of notifyd's loop when the store-lock convoy was fixed and
  never given another home, so in practice an idle agent was never nudged.
  notifyd now schedules the sweep on its own low cadence: outside the 60 s
  delivery path, detect-and-enqueue only, holding no store lock across it (a
  lock-holding sweep in that loop is what caused the convoy), and fail-soft, so
  a raising sweep can never kill delivery.
- **The sweep could not safely be scheduled as it stood.** `_deliver_per_owner()`
  pushed unconditionally — no fingerprint, no dedupe. With 30 owners currently
  stale, cronning it would have sent ~30 identical nudges every hour forever:
  the same desensitizing spam removed from the digest in 0.8.1. Nudges now
  deliver on CHANGE. Per `(owner, kind)` state persists `{fingerprint,
  delivered_at}`; the fingerprint is the *set* of stale card ids — order
  independent, and deliberately excluding wall-clock age, which would change
  every sweep and defeat suppression entirely.

### Added
- `SCITEX_CARDS_NUDGE_FLOOR_HOURS` (default `24.0`) — an unchanged nudge is
  re-sent anyway once the floor elapses, so a genuinely stuck agent is still
  nudged daily. Mirrors the existing `SCITEX_CARDS_DIGEST_FLOOR_HOURS`.

### Notes
- Only a **delivered** nudge arms the suppression; a failed push does not, so a
  broken delivery wire cannot silently mute an agent forever.
- Suppressed owners are still logged, so `stats` shows who was skipped and why.
  Silent suppression is how a sweep loses its readers' trust.

## [0.8.1] - 2026-07-11 — fix: the digest wakes an owner on CHANGE, not every sweep; `update --help` renders on click >= 8.2

### Fixed
- **Digest re-fired every sweep with an identical list.** Observed live: 30
  identical "Assigned-card digest #N" wake-ups in ~3 h — same cards, only the
  counter moving — and every agent got them. A signal that repeats unchanged
  every five minutes teaches its reader to ignore it, and the digest is the
  one signal that must stay un-ignorable. The owner's wake-up is now skipped
  while the card set AND each card's status are unchanged since the last
  DELIVERED digest, with a 24 h floor (`SCITEX_CARDS_DIGEST_FLOOR_HOURS`) so a
  genuinely stuck owner is still nudged daily rather than never. A status flip
  alone (`in_progress` → `blocked`) re-notifies even when the id set is equal.
  The digest TICK still advances on the cadence: operator escalation fires
  after N ticks, so suppressing ticks would have silently disarmed
  high-priority escalation (the existing escalation test caught exactly that on
  the first cut). Only the owner-facing enqueue is conditional; escalation and
  creator-escalation are untouched, and a regression test pins it.
- **`scitex-cards update --help` crashed on click >= 8.2.** The custom
  `--blocker` param type's `get_metavar()` predated the `ctx` keyword click now
  passes, so the help screen died with a `TypeError` and the update syntax was
  undiscoverable (found by neurovista in production while working around a
  dropped MCP session).

## [0.8.0] - 2026-07-11 — feat: abolish `pending`; WIP gate counts work-in-flight only; deferred consumption pipeline; tolerant enum handling; board-UI batch

The fleet-incident release (2026-07-10 night): four operator-directed fixes
that each bit multiple agents in production, plus the board-UI review batch.

### Changed — status model
- `pending` is ABOLISHED. It is out of `VALID_STATUSES`; every default (CLI
  `--status`, MCP `add_task`, board create handler, `Task` dataclass) is now
  `deferred` — a new card carries a real decision. The CLI Choice and the
  board handlers reject `pending` at the boundary (HTTP 400 / usage error).
- `deferred` is NOT terminal (operator ruling, translated: "deferred is
  not an end state"). It is
  open backlog: it shows in active views, counts as open, and CAN be overdue
  when it carries a missed deadline. `close` writes `cancelled` (the real
  "closed as not planned" state) instead of overloading `deferred`.
- Tolerant enum handling on the SHARED store: an unknown status or a
  blocker-less `blocked` row WARNS loudly (naming the card and the likely
  version skew) instead of raising — on both read and write. One newer
  writer's row can no longer take every older reader's board down (the
  2026-07-10 fleet outage) or make every other agent's write fail.
  Structural corruption (missing id/title, duplicate id) still raises.

### Fixed — WIP gate counted backlog, not WIP
- The add gate excluded only `{done, goal}`, so `deferred`/`failed`/
  `cancelled` consumed budget forever; after the pending→deferred migration
  agents were refused at "88 open tasks" and could not even record incidents.
  Now `WIP_STATUSES = {in_progress}`; the gate fires only when the incoming
  card is itself `in_progress`. RECORDING (blocked/deferred/goal) is never
  gated. `OPEN_EXCLUDED_STATUSES` unifies the open predicate that previously
  existed in two drifted hand-copies.

### Added — deferred consumption pipeline (deferred is debt)
- `_backlog_triage`: recency-weighted pick-for-action sampling
  (Efraimidis–Spirakis, without replacement — fresh cards dominate; the
  backlog must not eat the agent), age-based expiry past 30 days (default
  outcome cancellation; the owner rescues what they still want), the
  `deferred_at` age clock (stamped once on entry, never reset by a re-defer)
  and the `last_triaged_at` re-draw cooldown.
- `scitex-cards triage [--mine|--agent X] [--json]` — the read-only payload a
  short-lived twin consumes under its parent's identity; mutation stays with
  the existing verbs.
- The 24 h backlog nudge, `runnable`, and `next` now target `deferred`
  (they still targeted the abolished `pending`, which no card carries — 379
  deferred cards were ageing in total silence).

### Added — store concurrency (lost-write incident)
- `edit_tasks(path)`: one locked read-modify-write cycle; writes nothing on
  exception. The sanctioned bulk-edit primitive.
- `save_tasks(..., expected_generation=store_generation(path))`: optimistic
  concurrency — a write based on a stale read raises `StaleStoreError`
  instead of silently erasing a concurrent writer's rows.
- `comment_task` stamps `last_activity` — a comment IS activity (cards under
  active discussion no longer read as abandoned).

### Added — board UI (operator live-review batch)
- Sticky-note Wall view with per-assignee islands and a derived next-up
  stack; brand-colored agent avatars; one-shot status-transition glow
  (compositor-only, with an SVG `drop-shadow` twin); uniform right-click on
  every view; cursor-offset hover tooltips replacing native `<title>` (which
  renders under the pointer); Timeline leftmost; Stale view removed; search
  input at filterbar scale; gzip on `/graph` (4.98 MB → 1.60 MB).

## [0.7.50] - 2026-07-09 — feat: inbox reads/writes default to SQLite (retires the per-poll whole-store parse)

Fleet load incident: every agent's `scitex-cards mcp start` digest-poll (every
5 s) `safe_load`ed the entire ~9 MB task store just to read ONE recipient's
inbox — across ~21 agents the fleet's biggest CPU sink (host load ~27). This
moves the inbox read/write path onto SQLite so a poll is an indexed
`(recipient, seen)` lookup, never a whole-store parse.

- New `_inbox_sqlite` backend (stdlib `sqlite3`, WAL) at the constitution's
  runtime-DB path `<store_dir>/runtime/cards.db`. `enqueue` / `poll_inbox` /
  `ack` mirror the YAML contract exactly (dedup on `(event_type, card_id, ts,
  actor)`, `supersede`, `unseen_only`, `mark_seen`).
- SQLite is now the DEFAULT. `SCITEX_CARDS_INBOX_BACKEND=yaml` is an explicit
  break-glass only; an unknown/unset value uses SQLite. No silent fallback — a
  SQLite error fails loud.
- Lazy one-time auto-migration: first access copies the YAML `inboxes:` records
  into the DB (guarded by a `migrated_from_yaml` meta flag), so no unseen
  notification is lost regardless of restart timing; steady state never reads
  YAML. Idempotent + reversible (the YAML section is never deleted).
- CLI: `scitex-cards inbox migrate-to-sqlite` / `inbox info`.

Phase 1 of the YAML→SQLite migration (inboxes only; cards/users/ledger stay on
YAML for now — Phase 2 covers cards). Complements the S0 shadow store (#349).

## [0.7.49] - 2026-07-08 — feat: S0 shadow SQLite DB + YAML bootstrap (YAML still canonical)

STAGE S0 of the YAML→SQLite migration (design-confirmed by scitex-dev,
RFC #348). Purely ADDITIVE: an authority-local SHADOW SQLite database is
created and bootstrapped FROM the current YAML store. The YAML (`tasks.yaml` +
the `threads.yaml` sidecar) STAYS the CANONICAL source of truth — no CRUD verb,
MCP tool, or `load_doc`/`_save_doc_unlocked` path reads or writes the DB in S0.
The shadow DB is incapable of harming the YAML by construction (a separate
file, never linked into any write path). S1 (dual-write) comes next.

- New `_db.py` adapter — stdlib `sqlite3` only (no scitex-db). `resolve_db_path`
  follows explicit arg → `$SCITEX_CARDS_DB` → `local_state.user_path("cards",
  "cards.db")`, DELEGATING the user tier to the ecosystem resolver (never a
  re-rolled project/user precedence — the class of bug behind the 2026-07-06
  stale-store incident). On connect: WAL, `synchronous=NORMAL`,
  `busy_timeout=300000`, `foreign_keys=ON`; schema stamped `user_version=1`.
- Schema: `tasks` (scalar Task fields as columns; `deadlines`/`_log_meta` as
  JSON TEXT; `group`→`grp`), `task_comments`, `task_edges`, `task_roles`,
  `users` + `user_names`, `notifications` (index `(recipient_id, seen)`),
  `messages` (folds the threads sidecar), `schema_meta`, plus the RFC's indexes.
- New `_db_bootstrap.py` — `import_from_yaml` reads the current YAML via the
  existing load path and rebuilds every table in one transaction. Idempotent
  (re-run = same state); opens the YAML READ-ONLY and never writes it back.
- New `db` CLI noun group: `db path`, `db verify`, `db import --from-yaml`.
- `repo` promoted to a first-class optional `Task` field + `tasks.repo` column
  (confirmed latent bug — used by add_task/list_tasks but absent from the
  dataclass; the ONE additive existing-code change allowed in S0).
- Adds `scitex_config` (foundational ecosystem lib) as a runtime dependency.

## [0.7.48] - 2026-07-08 — fix: guard the `print-stats` rollup, not just the push

The 0.7.47 single-instance guard (#346) did NOT stop the CPU stacking it was
meant to prevent. Verified live: two `*/10` notify runs still ran concurrently
at ~46% and ~30% CPU, and NO "prior run still holds the lock, skipping" log
fired. Root cause was **call-site placement**: in `_cli/_stats.py` the EXPENSIVE
work — the per-agent rollup that parses the ~9 MB `tasks.yaml` and aggregates
all ~930 cards — was computed ABOVE the flock guard (it was shared with the
plain-read `click.echo(out)` path). The `single_instance(...)` lock wrapped only
the push at the END. So two overlapping `--notify` ticks BOTH ran the costly
rollup concurrently (the observed CPU); the lock merely serialized the cheap
final push, giving zero CPU relief, and since neither tick blocked on the
other's rollup the "skipping" line never printed.

- **Lock BEFORE the rollup, in notify mode only.** In side-effecting/cron mode
  (`(notify or nudge_quiet) and by == "agent"`) `print-stats` now acquires the
  single-instance flock FIRST; if the lock is already held it logs the skip line
  and returns (exit 0) WITHOUT parsing the store at all. Only when the lock is
  confirmed acquired does the ENTIRE expensive path (store parse + rollup +
  notify/push) run — inside the lock. The plain read-only path (no `--notify`)
  computes its OWN rollup UNGUARDED and echoes the table, exactly as before, so
  interactive reads are never blocked or skipped. The rollup is factored into a
  `_rollup(...)` helper called from both branches; nothing expensive runs before
  the lock is confirmed in notify mode.
- **`_singleflight.single_instance` / `notify_lock_path` unchanged.** They were
  correct — only the CALL SITE was wrong. The lockfile still resolves to the
  same `<store>/runtime/print-stats-notify.lock` across invocations via
  `_paths.runtime_dir`, so two cron runs contend on one lock.
- **Regression test asserts ZERO store-loads when the lock is held.** The 0.7.47
  test only checked the push was skipped — which is why it missed the bug (the
  push is skipped either way). `test__print_stats_single_instance.py` now spies
  the real `_stats.load_tasks` (a call-counter wrapping the real parse, no mock)
  and asserts it is called ZERO times while the lock is held, that it DOES run
  when the lock is free, and that the unguarded plain-read parses even while the
  lock is held.
- **File split.** To keep both under the size budget, the `sync-github` verb
  moved to `_cli/_sync_github.py`; `_cli/_stats.py` keeps `print-stats` and still
  registers both. No behavior change to `sync-github`.

Incident: incident-cards-wake-watcher-interval2-spiral-20260708.

## [0.7.47] - 2026-07-08 — fix: single-instance flock on `print-stats --notify` (third store-size daemon)

The managed notify cron runs `scitex-cards print-stats --by agent --notify
--nudge-quiet` every 10 minutes. `print-stats --by agent` re-derives per-agent
rollups from all ~930 cards in the ~9 MB `tasks.yaml`; when a single run exceeds
the 10-min period it OVERLAPS the next cron tick, so runs STACK (observed: 2
concurrent at ~63% CPU each, heading toward the same saturation as the
wake-watcher spiral). This is the cron/one-shot analogue of the wake-watcher
death-spiral PR #344 fixed and the MCP inbox-drain spin #345 fixed — same
store-size root. The durable cure is archival (separate card); this is the
stacking guard.

- **Single-instance flock (`_singleflight.py`).** A new small, reusable module
  mirrors the wake-watcher's process-level lock (#344): a NON-BLOCKING
  `flock(LOCK_EX | LOCK_NB)` on `<store>/runtime/print-stats-notify.lock`
  (resolved via `_paths.runtime_dir`, the same resolver the delivery ledger /
  pidfiles use). Exposed as a `single_instance(...)` context manager +
  `notify_lock_path(...)` helper so it is unit-testable.
- **Guard the notify path only.** `print-stats` takes the lock ONLY when
  `--notify` / `--nudge-quiet` is set (the cron/side-effect path). When the
  lock is already HELD (a prior run still going) the run LOGS a clear line and
  EXITS 0 — a skipped nudge tick is fine; the next tick runs. The lock releases
  on exit and automatically on process death, so a crashed run never wedges it.
- **Plain reads stay unguarded.** An interactive `print-stats` (no `--notify`)
  neither takes nor is blocked by the lock — it prints the table read-only and
  runs freely.

## [0.7.46] - 2026-07-08 — fix: mtime-gate the channel inbox drain (read-side twin of #344)

Each agent's `scitex-cards mcp start` runs a channel poll loop that called
`drain_once` every 5s **unconditionally**. Every drain calls `recipient_keys` +
`_inbox.poll_inbox`, both of which `safe_load` the ENTIRE shared store — the
inbox lives in an `inboxes:` section of the SAME ~9 MB / ~930-card `tasks.yaml`
as the cards. A ~9 MB parse every 5s per agent × ~7 channel servers on a host =
~350% sustained CPU (a major contributor to the load-25 baseline and the
conditions behind the recent wake-watcher saturation incident). This is the
READ/poll analogue of the every-tick reload spiral PR #344 fixed on the WRITE
side.

The cure mirrors #344's `WatcherState.mtime` short-circuit:

- **mtime gate (`_channel_drain_state.py`).** The inbox is only ever mutated
  through a store WRITE, so a new notification cannot appear without the store
  file's mtime advancing. Before any parse, a drain tick `os.stat`s the store
  and compares its mtime to the last drained tick; when UNCHANGED it SKIPS the
  whole drain (no `recipient_keys`, no `poll_inbox`, no parse) — an idle inbox
  now costs one `stat()` per 5s instead of a full re-parse. The store path is
  resolved the same way `_inbox.poll_inbox` resolves it
  (`resolve_tasks_path(store)` for `None`, else the explicit path) so the gate
  stats the EXACT file that would be parsed.
- **Fail-safe + first-tick.** The first tick always drains (seeds the mtime);
  an unresolvable / unstatable store path fails SAFE = drain, so correctness
  never regresses.
- **ack-write interaction.** A drain that pushes+acks WRITES the store (flipping
  records `seen`), bumping the mtime — the next tick drains once more, finds
  nothing new, records the post-ack mtime, and the tick after that skips. Net:
  exactly one extra parse after real activity, then a truly quiescent inbox
  idles at one `stat()` per tick.
- **Behavior preserved.** When the mtime DID change the drain is unchanged —
  recipient-key fan-out, unseen read, ack-after-push, `MAX_PUSH_PER_DRAIN` burst
  cap, and fail-soft all intact.

`_mcp_channel.py` was already over the 512-line file cap; the agent-identity
resolution (`resolve_agent_id` / `resolve_agent_id_optional`) was extracted to a
new `_channel_identity.py` (re-exported from `_mcp_channel` — no import breaks)
to land the change under budget.

## [0.7.45] - 2026-07-08 — fix: prevent the wake-watcher digest death-spiral

`scitex-cards.wake-watcher` (`watch --push --interval 2`, systemd
`Restart=on-failure`) death-spiraled on ywata-note-win 2026-07-08: the 2s
interval re-parsed the ~9 MB / ~930-card store faster than a tick finished on a
slow host, so the watch daemon ran at sustained high CPU while the separate
10-min `print-stats --by agent --notify` cron piled up unfinished digests on the
already-saturated box. Load hit 43 on 16 cores; sac-listen OOM-died and several
agents/builds died before the host was recovered
(incident-cards-wake-watcher-interval2-spiral-20260708).

Four durable, structural fixes to `_wake_watcher.py` / `_jobs_provider.py` /
`_cli/_loop.py`:

- **Interval floor + bump.** The wake-watcher JobSpec now uses `--interval 30`
  (was 2), the `watch` CLI default is 30s, and `clamp_interval()` enforces a
  **hard 10s floor** — any sub-floor value is clamped up with a loud WARNING
  naming the incident, so a stray `--interval 2` can never foot-gun the fleet
  again.
- **Single-instance lock.** `run_watcher_forever` takes a non-blocking `flock`
  on a runtime-dir lockfile; a second `watch` process sees the lock held and
  refuses to start, making two concurrent watchers (overlapping full-store
  re-parses) structurally impossible. The loop is strictly sequential, so a
  slow tick delays the next one — it can never launch an overlapping one.
- **Change-gated push + single parse.** After seeding, a tick whose store mtime
  is unchanged short-circuits before any parse (one `stat()`, no diff, no push)
  — a quiet board does no work per interval. When work is needed the store is
  parsed **once** per tick via `load_doc` (task list + `agents:` registry from
  the same `safe_load`), replacing the old double full-parse.
- **Self-throttle.** Push concurrency stays 1 and a slow tick degrades by
  delaying, never by stacking a second digest.

## [0.7.44] - 2026-07-08 — perf: cheapen the post-dump store-write verify without weakening the corruption guard (Fix B2)

The crash-safe store write (`_save_doc_unlocked`) reparsed the just-dumped tmp
with a FULL `safe_load` construct-reparse before promoting it — the 2026-06-13
corruption guard (lead a2a `d5809cd3`, the incident where the canonical file
ended mid-string). That full construct built ~159k Python objects on the live
9.2 MB / ~930-card store just to prove the bytes were parseable, and every
write paid it; bursts convoyed on the flock.

- New `src/scitex_cards/_store_verify.py` `_verify_dumped_tmp(tmp_path, dumped)`
  keeps the SAME guarantee (the promoted bytes must be FULLY reparseable) but
  drops the object construction. It does two cheap checks:
  1. **Byte-length check** — `os.stat(tmp).st_size == len(dumped.encode())`,
     catching a short / partial / disk-full write.
  2. **Event-scan reparse** — streams the tmp through the libyaml C parser
     (`yaml.parse(..., Loader=CSafeLoader)`) consuming events until a
     `StreamEndEvent` is observed. The C parser raises `yaml.YAMLError` on
     truncation / unterminated-scalar / malformed docs WITHOUT constructing the
     document objects; reaching StreamEnd proves the whole byte stream is
     well-formed end-to-end.
- `_save_doc_unlocked` now dumps to a STRING once (so the length check has the
  intended bytes and we never dump twice), writes it, fsyncs, then calls
  `_verify_dumped_tmp` before `os.replace`. Same crash-safe
  dump→tmp→fsync→verify→replace flow.
- The old reparsed-task-COUNT match is DROPPED: reaching `StreamEndEvent` proves
  the entire stream parsed, so a truncation that silently drops tasks aborts the
  parse before promotion — the event-scan supersedes the count check. Flagged
  in-code + here for scitex-dev review.
- Measured on a synthetic realistic-shape store: the event-scan verify is
  ~2.4x faster than the full `safe_load` construct-reparse it replaces
  (e.g. ~3.1 s → ~1.3 s on a ~900-card 1 MB doc; the saving scales with store
  size). New `tests/scitex_cards/test__store_verify.py` (10 tests) pins the
  corruption-safety non-negotiables; `test__store_doc_preservation.py` +
  `test__model.py` regression-green.

## [0.7.43] - 2026-07-08 — fix: collapse the notifyd digest replay-storm (supersede-on-enqueue)

A digest is a full point-in-time snapshot, but notifyd enqueued a fresh one
every tick without superseding prior unseen digests. A recipient whose channel
was down piled up dozens of stale digests that all replayed on reconnect (seen
live: one agent had 53 unseen `reminder` digests spanning 3 days).

- `_inbox.enqueue` gains a `supersede: bool = False` keyword. When `True`, every
  EXISTING unseen record matching both `event_type` AND `card_id` is removed
  before the new record is appended — at most ONE pending digest per recipient
  survives. Seen records (history) and the plain `(type,card,ts,actor)` dedup
  path are untouched.
- The reminder engine wires `supersede=True` ONLY at the cumulative owner-digest
  enqueue (`EVENT_DIGEST` / `(digest)`). Per-card events (escalation,
  creator_escalation) stay distinct and do NOT supersede.
- New maintenance verb `scitex-cards notifyd collapse-digests [--json]`
  (`_inbox_maint.collapse_digests`): one safe locked pass that collapses each
  recipient's unseen digest backlog to the single newest digest (older ones
  marked seen, nothing deleted) — clears the already-accumulated fleet backlog.
- Refactor: extracted the fail-soft dispatch helpers `_safe_resolve` /
  `_safe_enqueue` into `_reminder_enqueue.py` to keep `_reminders.py` within
  budget. Public API unchanged.

## [0.7.42] - 2026-07-08 — fix: tolerate a STALE deprecated env var when the current one is valid

Fleet agents still carry a stale ambient `SCITEX_CARDS_AGENT` (the pre-0.7.30
name) baked in by an old sac injector. Until now scitex-cards fail-louded on the
mere PRESENCE of that old var — even when the current `SCITEX_CARDS_AGENT_ID` was
set and correct. In the unified MCP server that fail-loud was swallowed by
`resolve_agent_id_optional` → returned `None` → the digest poll loop never
started, so agents on 0.7.32 with a correct `AGENT_ID` connected (tools worked)
but never received channel notifications.

### Changed

- `resolve_agent_id` (`_mcp_channel.py`) now makes the CURRENT var WIN: when
  `arg` / `$SCITEX_CARDS_AGENT_ID` yields a valid id it is returned even if the
  stale `$SCITEX_CARDS_AGENT` is also exported — a loud warning is logged and the
  stale var is ignored (no raise). The fail-loud on the old name fires ONLY when
  the current var is absent/invalid (a genuine reliance on the renamed-away
  var). Placeholder / unresolved errors are unchanged. `resolve_agent_id_optional`
  therefore returns the id (not `None`) when both vars are set, re-enabling the
  poll loop.
- Same tolerance applied to the store var in `_paths.py`: a stale
  `SCITEX_CARDS_TASKS` is warn-and-ignored when `SCITEX_CARDS_TASKS_YAML_SHARED`
  is set, and fails loud only when the current var is absent.

## [0.7.41] - 2026-07-07 — feat: operator↔agent direct-message chat view (/chat)

Minimal slice of the DM board pane (card
fleet-agent-direct-message-board-pane-20260707; scitex-dev DM convention
spec v1): the operator can message a specific agent from the phone via the
board, and agents reply through an MCP verb.

### Added

- **`scitex_cards._threads`** — pure DM thread store. Canonical record
  `{id, thread, from, to, body, ts, read}`; thread id `dm:<a>::<b>` with the
  peers sorted lexicographically (one thread per pair, both directions;
  reserved operator name `operator`). Threads live in a SIDECAR
  `<store_dir>/threads.yaml` next to the resolved `tasks.yaml` with its OWN
  flock, so chat writes never convoy with card writes; the write mirrors the
  crash-safe dump→tmp→fsync→reparse-verify→`os.replace` pattern of
  `_model._save_doc_unlocked`. API: `append_message` / `get_thread` /
  `list_threads` / `mark_read`.
- **dm-dispatch** — `append_message` also enqueues an `event_type="dm"`
  notification into the recipient's EXISTING pull-inbox
  (`_inbox.enqueue`, keyed via `_users.resolve_user` exactly like
  `poll_notifications`), so the ≥0.7.32 unified channel server pushes the
  message into the agent's live session. The `operator` recipient is
  enqueued too (symmetry; the board reads unread state from the sidecar).
  Fail-soft: an enqueue failure never loses the persisted thread record.
- **MCP verbs `dm_send(to, body)` / `dm_list(peer=None, ack=False)`** —
  agent-side reply + read surface (in `_mcp_skills`; `from` resolves via
  `resolve_agent_id_optional` with an actionable error when unset; store IO
  wrapped in `anyio.to_thread.run_sync`).
- **Board `/chat` view** — mobile-first page (new `chat.html` template +
  `static/scitex_cards/chat/chat.js`): collapsible agent list (users registry
  ∪ existing thread peers, unread badges), chronological bubble thread
  (operator right-aligned), compose box; polls `/dm/thread/<peer>` every 5s
  and `/dm/threads` every 10s. JSON endpoints `GET /dm/threads`,
  `GET/POST /dm/thread/<peer>` in `_django/handlers/dm.py` (distinct from
  the per-card `/chat/<card_id>` comment endpoint).

### Deferred (polish later)

- WebSocket push, markdown rendering, group threads, message search,
  CLI `dm` verbs, operator-side inbox drain.
## [0.7.40] - 2026-07-07 — feat: CLI verb-rename pilot (slice 6b) — `list-stale` / `find-card` / `watch-ci`

Pilot migration for the ecosystem CLI-standardization plan (doctrine:
scitex-dev `general/03_interface/02_cli`).

### Changed

- **`stale-list` → `list-stale`**, **`ci-watch` → `watch-ci`** (§1d grammar:
  compounds are kebab-case and VERB-FIRST), and **`resolve-card` →
  `find-card`** (it is a READ — prints ids of cards whose `repo` matches a
  filter — which is the doctrine `find` verb; `resolve` is also a banned
  synonym). The old names remain as HIDDEN warn-phase deprecated aliases:
  they forward all args/options to the canonical command, exit as it does,
  and print `'<old>' is deprecated — use '<new>' (removed in v0.9)` to
  stderr once per shell session. They disappear in v0.9 (three-phase
  ladder, §5).
- **Root `--help` is now categorized** under the fixed §4a headers (Core /
  Data & Sync / Service / Diagnostics / Introspection / Shell; the `Other`
  catch-all is empty), with spec-built help (`CliHelp`) on the root group
  and on `list-tasks` / `add` / `done` / `close` plus the renamed leaves.
- The `scitex-cards.ci-watch` JobSpec keeps its registry NAME (systemd/dedupe
  identity) but its command now invokes the canonical
  `scitex-cards watch-ci --once`.

### Added

- `src/scitex_cards/_cli/_compat.py` — guarded imports of scitex-dev's
  `deprecated_alias` + `help_spec` helpers (present on scitex-dev develop,
  absent from the released 0.21.0; scitex-python#352 precedent) with
  doctrine-contract fallbacks so warn+forward behavior is identical on
  every installed scitex-dev release.

### Refactored

- `_cli/_write.py` (pre-existing over the 512-line cap): the `update` verb
  moved to `_cli/_update.py` — pure move, one-verb-per-file precedent.

## [0.7.39] - 2026-07-07 — chore: channel-notification source label is now a short sender identity

### Changed

- **Default `meta.source` label: `scitex-cards-system` → a short sender identity.** Per the
  fleet naming agreement (operator 2026-07-07, card
  fleet-channel-source-sender-identity-naming-20260707), channel-notification
  source labels are standardized to SHORT sender-identity names — sac / cct /
  that label (`daemon` is reserved for daemon-origin messages). This supersedes the
  short-lived `scitex-cards-system` default introduced in 0.7.32. Label-only
  change: `meta.source` is a free attribution label decoupled from routing
  (replies route via the MCP tool + ids).
- **Deployed config note:** `.mcp.json` entries that pin the old values
  (`--name scitex-cards` / `--name scitex-cards-system`, or
  `SCITEX_CARDS_CHANNEL_SOURCE` set to either) should update to the short label or
  simply drop the override and inherit the new default.

## [0.7.34] - 2026-07-05 — fix: harden the channel push path (size cap + first-connect burst cap)

Hardens the `notifications/claude/channel` push surface against the crash class
behind the 2026-07-02 incident, where 180 solver apptainer containers died on
boot with `JSON message exceeded maximum buffer size of 1048576 bytes` — an
oversized scitex-cards channel push overflowed the Claude Agent SDK's 1 MB stdio
reader.

### Fixed

- **Oversized push body → SDK reader overflow.** `build_channel_params` now caps
  the pushed `content` body at `MAX_CONTENT_BYTES` (256 KiB, a quarter of the
  1 MB reader with generous headroom for `meta` + JSON framing). An oversized
  body is truncated on a UTF-8 char boundary (multibyte-safe) and gets a
  `[truncated — see card <id> on the board]` pointer so the full text stays
  reachable. `meta` values are additionally clamped (belt-and-suspenders).
- **First-connect burst.** `drain_once` now pushes at most `MAX_PUSH_PER_DRAIN`
  (50) records per call, across all recipient keys combined. A large unseen
  backlog can no longer flood the session in one tick — the remainder stays
  unseen and drains on the next ~5 s poll tick, a few dozen at a time. Acks
  still happen only for records actually pushed.

### Added

- New pure, unit-testable `scitex_cards._channel_guard` module holding the size
  constants and `_bounded_content` / `_bounded_meta_value` helpers (keeps
  `_mcp_channel.py` within the module size budget).

### Docs

- Documented the headless lever: with **no** `SCITEX_CARDS_AGENT_ID` the unified
  `scitex-cards mcp start` runs tools-only (no poll loop, zero pushes) — the
  intended mode for solver / headless capsules that must not receive pushes.

## [0.7.33] - 2026-07-05 — feat: package-level `health` doctor (MCP tool + CLI verb)

A broad store / identity / delivery health check, exposed as BOTH the `health`
MCP tool and the `scitex-cards health` CLI verb. Motivated by the 0.7.32
handshake incident: the `channel_drain` check turns that class of "MCP not
connected" failure into a one-command diagnosis.

### Added

- **`scitex_cards._health.health(...)`** — one pure, never-raising function that
  returns the cross-package standard report shape
  `{"package", "ok", "checks":[{name,ok,detail,hint}], "summary"}` (shared
  verbatim with the sac/cct health tools). Every FAILING check carries an
  actionable `hint`; a check that errors internally is reported as `ok=false`
  with the error in its hint rather than raising. Checks: `store_canonical`
  (resolved store is the canonical user/shared path — not a project shadow —
  and is readable, writable, and parses with a top-level `tasks` key),
  `agent_id` (`$SCITEX_CARDS_AGENT_ID` resolves to a real value, not
  blank/`unknown`/an unexpanded `$VAR`), `notifyd_alive` (real pidfile probe of
  the delivery daemon), `channel_drain` (this agent's unseen vs seen inbox
  backlog — flags a large unseen pile that was never drained), and
  `channel_capable` (`scitex_cards._mcp_channel` imports and exposes
  `_serve`/`_run`).
- **`health` MCP tool** — registered on the shared FastMCP instance
  (`scitex_cards._mcp_skills`); returns the JSON report. Distinct from the
  narrow `mcp doctor` (which only checks the fastmcp install).
- **`scitex-cards health [--json]` CLI verb** — human-readable report by default,
  raw JSON with `--json`; exits `0` when all checks pass, else `1` (usable as a
  shell/CI gate).

## [0.7.32] - 2026-07-04 — fix: channel poll loop no longer starves the MCP handshake

Hotfix for a fleet-wide "scitex-cards MCP not connected" regression introduced
by the unified server (0.7.31).

### Fixed

- **Unified `mcp start` failed the MCP `initialize` handshake once an agent had
  an identity set** — every fleet agent showed the `scitex-cards` server as "not
  connected". Root cause: the inbox poll loop's first drain ran SYNCHRONOUS
  blocking store IO (`recipient_keys` + `_inbox.poll_inbox`, which lock and
  parse the whole YAML store) **inline on the asyncio event loop**. That starved
  the `ServerSession` so it never answered `initialize` before the client timed
  out. The stall scaled with inbox size, so it surfaced once an inbox reached
  ~600 entries. `drain_once` now off-loads every blocking store call to a worker
  thread (`anyio.to_thread.run_sync`); only the push itself runs on the loop, so
  the handshake (and tool calls) are never blocked. Both tools AND digest push
  are preserved. Regression tests pin the invariant (drain yields before it
  touches the store) and the end-to-end handshake with an active poll loop.

### Changed

- **Channel render name is now `scitex-cards-system`** (was `scitex-cards`). The
  system-pushed notification source (`meta.source`, env
  `SCITEX_CARDS_CHANNEL_SOURCE`, default) is deliberately distinct from the
  `scitex-cards` agent id so the operator's TUI does not confuse a system digest
  with a message authored by the scitex-cards agent. Deployed `.mcp.json` entries
  that pin `SCITEX_CARDS_CHANNEL_SOURCE=scitex-cards` must update to
  `scitex-cards-system` (or drop the key to take the new default).

## [0.7.31] - 2026-07-03 — one unified scitex-cards MCP server (tools + digest push)

The turn-on release for fleet-wide notifications. Together with the 0.7.30
env-var standardization, this is what the coordinated fleet flip deploys.

### Changed

- **One MCP server instead of two**: `scitex-cards mcp start` now runs a SINGLE
  server that both serves the card tools AND pushes this agent's digest
  (`notifications/claude/channel`). Previously the tools server (`mcp start`)
  and the digest-push server (`mcp channel`) were separate, needing two
  `.mcp.json` entries. Now one `scitex-cards` entry (`args: ["mcp", "start"]`)
  does both — matching the one-server-per-project convention.
  - It reuses FastMCP's underlying low-level server (which has every registered
    tool) and declares the `claude/channel` capability alongside the tools
    capability, so no tool behaviour changes.
  - The agent id is optional: with `SCITEX_CARDS_AGENT_ID` set, the digest is
    pushed; without it, the server serves tools only (a loud warning, never a
    hard failure on the tools surface).
  - `--http` transport remains tools-only (HTTP cannot carry the push).
  - The standalone `scitex-cards mcp channel` verb is retained for
    back-compatibility.

## [0.7.30] - 2026-07-02 — env-var standardization for fleet-wide notification delivery

Enables the per-agent channel-drain server to be wired fleet-wide (each agent
receives its own periodic digests + action-hooked card notifications), the
crucial rail for task-driven fleet coordination. Coordinated with the container
layer: the env-injection + `.mcp.json` wiring flip in lockstep with this release.

### Changed

- **Env var rename**: the agent-identity var `SCITEX_CARDS_AGENT` is renamed to
  `SCITEX_CARDS_AGENT_ID` (encodes that it is an identity). It stamps
  `created_by`/`updated_by`, keys the channel inbox, and drives the `--mine`
  filter.
- **Env var rename**: the task-store override `SCITEX_CARDS_TASKS` is renamed to
  `SCITEX_CARDS_TASKS_YAML_SHARED` (encodes the shared-yaml store).
- **Channel server is fully env-configurable**: `scitex-cards mcp channel` now
  reads `SCITEX_CARDS_CHANNEL_SOURCE` (meta.source, default `scitex-cards`) and
  `SCITEX_CARDS_CHANNEL_INTERVAL` (poll seconds, default `5`), with CLI flags as
  optional overrides. The `.mcp.json` entry needs zero config args — every
  parameter is a `SCITEX_CARDS_`-prefixed env var.

### Fixed

- **Fail-loud on the deprecated env-var names**: if `SCITEX_CARDS_AGENT` or
  `SCITEX_CARDS_TASKS` is still set, resolution raises with an actionable
  "renamed to …; unset the old var" message instead of silently honouring a
  stale export that could pin the wrong identity or store.

## [0.7.29] - 2026-07-02 — standalone user-delivery rail, notify/reminder engine, user registry + identity, and the release-pipeline fix

First successful PyPI publish since 0.7.10 — the release pipeline had been
broken (see Fixed below), so the accumulated work below shipped only now.

### Added

- **Standalone user-delivery rail**: scitex-cards's own notification path —
  channels + a delivery ledger + an always-on `notifyd` daemon (with a systemd
  unit) + a standalone MCP channel-notification server. Users-first, with no
  dependency on scitex-agent-container.
- **Notify / reminder engine**: nag-until-closed reminders with per-owner
  digest cadence, an owner allowlist for phased rollout, operator escalation
  for high-priority stale cards, and liveness-triggered escalation to the card
  creator when an assignee is unreachable.
- **User registry + canonical identity resolver**: collapses owner naming
  drift (host@name aliases) so notifications resolve to the right inbox;
  assignee liveness surfaced at assign time.
- **Model**: `cancelled` status (closed-as-not-planned terminal state).
- **Idle-guard Stop-hook**: blocks going idle while in-progress work is
  abandoned.
- **Fleet payload**: surfaces the waiting-on-operator queue (ids + SSOT count).

### Changed

- **Standalone decoupling from scitex-agent-container**: removed the sac
  listen-daemon HTTP fallback for turn URLs (zero runtime sac coupling) and
  reworded sibling-system names out of standalone-claim docstrings.
- **Store performance**: replaced the ruamel round-trip writer with a fast
  C-backed safe dump; config + reminders sidecar reads use the fast loader.
- **Board runtime state** now lives under `<store>/runtime/`.
- **Board v3 UX**: bigger timeline scatters with marquee select + Ctrl/Cmd+C
  copy + right-click menu; tighter left gutter; timeline edge legend on hover.
- **CI**: pytest-matrix serialized so one PR can't saturate all three runners.

### Fixed

- **Release pipeline**: the `publish` job declared `permissions: id-token: write`
  only, which defaults every other scope (including `contents`) to `none`, so
  `actions/checkout` could not clone the private repo ("Repository not found").
  Added `contents: read`. This had broken every tagged release since 0.7.10.
- **Fail-loud on unresolved actor/author** at task creation (no `getuser`
  fallback); board create-form requires creator/assignee.
- **Multi-select toolbar** no longer stretches to full column height.
- **Reminders**: parked-blocked cards excluded from the per-owner nag digest;
  store resolved before the notifyd reminder sweep.
- **Channel delivery**: drains producer-matching keys (raw name + resolved
  user-id); mutation store threaded into card-event emit so notifications
  actually enqueue.
- **Board**: falls back to the port-found board when the pidfile is stale.

## [0.7.28] - 2026-06-26 — board UX (timeline beeswarm/anti-flash, marker copy, user roles) + CI off paid runners

### Added

- **Timeline marker multi-select + copy + right-click menu** (`timelineSelect.js`):
  click markers to select, right-click for a menu, copy selected cards' contents
  to the clipboard.
- **Card detail user roles**: the detail drawer shows Creator / Assignee /
  Collaborators / Subscribers in user vocabulary; a `created_by` field is now
  captured at task creation (CLI `--created-by` + MCP), back-compatible with
  legacy rows; the `/graph` node payload emits `created_by` / `collaborators` /
  `subscribers`.
- **`help-wait` / `help-clear`** verbs + MCP tools (also in 0.7.27) — the SSOT
  card primitive the agent-waiting escalation hook calls.

### Changed

- **Timeline no longer flashes / jumps to top**: the raster skips its rebuild
  when the `/timeline` payload is unchanged and preserves scroll position; the
  main board likewise skips redraw on unchanged `/graph` and keeps scroll.
- **Comment posting is non-blocking + fail-loud**: the in-request agent relay
  uses a short (2s) timeout instead of 30s and surfaces a loud toast on a
  notify failure (comment is still saved).
- **CI moved off GitHub paid runners**: `cla.yml` + `auto-merge-to-develop.yaml`
  now run on self-hosted Spartan (auto-merge's `gh` calls rewritten as `curl`
  REST since Spartan has no `gh`); `newb-docs-quality` disabled (docker-only,
  pending apptainer). No workflow uses `ubuntu-latest`.

### Fixed

- **Fleet-adapter tests** skip (not fail) when `sac` is absent/non-functional,
  so a broken optional dependency can't red-gate CI (also in 0.7.27).

## [0.7.27] - 2026-06-25 — Timeline beeswarm + `help-wait` verb + sac-decoupled CI

### Added

- **Timeline beeswarm y-packing** (PR #245). In the board_v3 Timeline raster,
  time-overlapping markers in a lane used to render at the same vertical
  center and occlude each other. A deterministic sub-row packer
  (`timelinePack.js::packRows` — greedy interval partitioning, capped at
  `MAX_ROWS`) now fans co-located markers into stacked sub-rows and grows the
  lane to fit, so every task is visible. x/time math and the time-axis are
  unchanged.
- **`scitex-cards help-wait` / `help-clear`** CLI verbs + `help_wait` /
  `help_clear` MCP tools (PR #242). First-class "an agent is waiting on the
  operator" card semantics (`help-<agent>-waiting`, `status=blocked`,
  `blocker=operator-decision`), idempotent atomic upsert / resolve. Lifts the
  card shape out of the dotfiles Notification hook so scitex-cards owns the
  single source of truth; the hook becomes a thin trigger that calls the verb.

### Changed

- **Fleet-adapter tests decoupled from the live `sac` binary** (PR #244). The
  happy-path hosts tests now SKIP (not FAIL) when `sac` is absent or
  non-functional, via a shared probe guard — so a broken/missing optional
  fleet dependency can never red-gate the standalone package's CI. Fail-loud
  adapter-error tests still run (they need no working sac).

## [0.7.25] - 2026-06-15 — `scitex-cards ci-watch` (record-only CI poller)

### Added

- **`scitex-cards ci-watch`** + **`scitex-cards.ci-watch` cron JobSpec**
  (PR #206, lead a2a `b4c10158` / operator decoupled-pollers override
  via dev a2a `96afacc7`). Record-only CI poller — server-side
  `*/5 * * * *` cron that sweeps every repo in
  `dashboard.yaml → fleet.ci_status.repos` (or env override
  `SCITEX_CARDS_FLEET_CI_REPOS=owner/a,owner/b`), diffs against the
  local state cache at `~/.scitex/cards/ci-state.json` (override via
  `SCITEX_CARDS_CI_STATE`), classifies the transition
  (`first-seen` / `newly-green` / `newly-red` / `still-pending` /
  `unchanged`), and logs one stderr line per repo.

  Lane: **the card layer records, SAC delivers** — it writes no a2a sends
  and emits no bus events; SAC has its own independent poller for the
  delivery side. Either side can crash without breaking the other.
  The dedupe key (`head_sha`, `overall`) is content-keyed so SAC's
  poller can run at a different cadence (10 / 15 / 30 min) without
  breaking parity.

  CLI:

      scitex-cards ci-watch --once                # cron mode (one sweep)
      scitex-cards ci-watch --interval 600        # loop with custom cadence
      scitex-cards ci-watch --once --dry-run      # plan + summary, no state write
      SCITEX_CARDS_FLEET_CI_REPOS=owner/a scitex-cards ci-watch --once

  Wired into the ecosystem federation via `_jobs_provider.py`; after
  `scitex-dev ecosystem up`, the `scitex-cards.ci-watch.timer`
  systemd-user unit fires every 5 min. 18 mock-free tests
  (classifier purity, state load/save round-trip + atomic-write, CLI
  dry-run, JobSpec registration).

## [0.7.24] - 2026-06-14 — `scitex-cards mcp install-fleet` (P3a one-liner)

### Added

- **`scitex-cards mcp install-fleet --agents-dir <DIR>`** (PR #204,
  lead a2a `1ab212f3`). One-shot fleet sweep — walks every
  ``<agents-dir>/*/to_home/.mcp.json`` (the agent-container spec
  convention) and idempotently applies the scitex-cards MCP entry to
  each. Sibling MCP server entries preserved; per-agent corrupt JSON
  reported + sweep continues; final summary line carries
  ``agents=N updated=K noop=M errors=E``. Closes the missing-MCP gap
  that ripple-wm hit (had to a2a-relay through me for card add
  because their container's `.mcp.json` was bare). 12 mock-free
  CliRunner tests.

  Sweep one-liner for agent-container:

      scitex-cards mcp install-fleet \\
          --agents-dir ~/.dotfiles/src/.scitex/agent-container/agents \\
          --env-tasks-path /home/agent/.scitex/cards/tasks.yaml -y

  Mirrors the single-file ``install --apply`` semantics (PR #155 +
  #158) — same backup, same idempotency, same env-pin.

## [0.7.23] - 2026-06-14 — Board v3: time-based view (sort + group by time)

### Added

- **Sort by time + Group by time on the v3 board** (PR #201
  cherry-picked via #202; lead a2a `ff1441d7`, operator request for
  "a time-based view", translated). The v3 board at `/` (the
  operator's home view)
  now exposes time-based controls in the existing
  `.stx-cards-filterbar__group--view` group:
  - Sort dropdown extends with `created_at` + `completed_at` options
    (newest first) plus the reworked `last_activity` comparator.
  - New "Group by time" checkbox (`#stx-toggle-group-by-time`) folds
    each project column's cards under collapsible bucket headers:
    TODAY / THIS WEEK / THIS MONTH / OLDER. State persists in
    localStorage (`scitex-cards:group-by-time`,
    `scitex-cards:time-buckets-collapsed`).
  - New `board_v3/08-time-grouping.css` with token-only styling
    (bucket headers, chevrons, collapsed state, body left-rail).
  - 43 mock-free test cases pin the bucket classifier + sort-key
    helper + CSS contract.

  The existing Time View raster (PR #186) on `/legacy/` is
  untouched — this is a complementary control on the v3 board so
  the operator can sort/group by time WITHOUT switching to the
  React-SPA route.

### Notes for ops

PR #201 originally landed on `main` (subagent missed `--base develop`).
#202 cherry-picked the change onto develop and re-fixed the multi-line
Django comment that the cherry-pick re-introduced (regression caught
by `test__no_multiline_django_short_comments.py` from PR #199).

## [0.7.22] - 2026-06-14 — Hotfix: operator-visible Django template comment leak

### Fixed

- **board_v3 template comment leaked as literal text** (PR #199,
  lead a2a `f7a5d37930b9479ca7e53a7e316c132d`). Django's
  ``{# … #}`` syntax is single-line only — newlines between ``{#``
  and ``#}`` are NOT stripped, so the multi-line block at
  ``board_v3.html:200-208`` (introduced in PR #173) rendered as
  visible text on the board UI. Converted to
  ``{% comment %}…{% endcomment %}`` (multi-line safe). New
  regression test (``tests/scitex_cards/_django/test__no_multiline_django_short_comments.py``)
  walks every ``.html`` under ``_django/templates/`` and asserts
  every ``{#`` closes with ``#}`` on the same line — bug class
  pinned. Operator reported live; hotfix-released same hour.

## [0.7.21] - 2026-06-14 — Hook bus: ordering + card-message feedback channel

Two enhancements that close the **operator↔card↔owner+collaborators
feedback ring** Phase 6 was missing. Cross-package coordination via
the existing `scitex_cards.hooks` entry-point bus — no new poller, no
inter-package import.

### Added

- **Handler ordering primitives** (PR #196). Two optional function
  attributes on hooks-bus handlers:
  - `on_event.priority = <int>` (default 100; LOWER runs FIRST).
  - `on_event.critical = True` (default False; if True and the
    handler raises, dispatcher aborts the chain and re-raises so the
    producer's HTTP/CLI wrapper translates to 500 / non-zero exit).
  Sort key is `(priority asc, entry-point-name asc)` — stable.
  Mutation visible by reference (early handlers' mutations land for
  late handlers). Plugin LOAD failures (ImportError on `ep.load()`)
  logged as `"load: <msg>"` in `plugin_errors`; chain continues.
  Each error entry now carries `priority` + `critical` metadata so
  the producer can see the failure context. 11 mock-free tests.
  Designed with dev for the ci-result chain (owner-map priority=10
  critical=True before SAC's delivery at priority=200).
- **`card-message` event kind** (PR #197). Every comment landing on
  a card via `_store.comment_task` fans out a `card-message` event
  on the bus. Payload: `{kind, card_id, body, author, owner,
  collaborators, created_at}`. Owner resolution falls back
  `card.agent → card.assignee → null`. Collaborators is the
  pre-append snapshot of distinct comment authors, deduped,
  EXCLUDING owner AND new author (SAC must not echo). Emit happens
  OUTSIDE the file-lock so slow handlers can't starve writers; bus
  errors are caught + logged so external handler failure (SAC
  unreachable, missing entry-point) never breaks the producer's
  comment-save. 15 mock-free tests.
  Surfaces emit: `/chat/<card_id>` POST, `scitex-cards comment` CLI,
  MCP `comment_task` tool, Python API direct calls.

### Provenance

PR #196 + #197. Lead a2a `0ab1d9fd` (ci-result ordering coordination
with dev) + `1e8e33d0` (card-message feedback channel — Phase 6
extends to active routing). Both follow the same loose-coupling
pattern: the card layer = producer, SAC = consumer, no cross-package import.

## [0.7.20] - 2026-06-14 — 🎯 TRACK 2 dashboard mission COMPLETE (6/6 surfaces)

Closes the operator-mandated fleet-dashboard mission. The board at
:8051 is now the ONE screen the operator watches: tasks (existing)
+ CI status + host geometry + agent mesh + ACL + timing telemetry
+ chat. All six surfaces honor the same architectural principles:
fail-loud / registry-sourced / no hardcoded proper nouns / no mocks.

### Added

- **Phase 6 — Chat surface** (PR #194). Operator↔agent thread view
  over the existing per-card `comments[]` substrate. New
  `_django/handlers/chat.py` with `GET /chat/<card_id>` (returns
  comments + title) and `POST /chat/<card_id>` (validates
  non-empty text, calls `_store.comment_task`, returns the appended
  comment). 404 on unknown card_id; 400 on empty text; 405 on
  PUT/DELETE. New `ChatPanel.tsx` mounts inside the existing
  NodeDetailPanel drawer — bubble layout with author-color hash,
  30s auto-poll for new comments, fail-loud error pill + toast on
  write failure. Author default from `SCITEX_CARDS_AGENT` env. 45
  new mock-free tests (16 backend + 8 JS predicate + 21 CSS/wiring).
  Follow-ups: RW-perm gating, WebSocket push, markdown rendering,
  @-mentions / threading / reactions / attachments.

### Mission complete — 6/6 TRACK-2 surfaces

| # | Surface              | PR    | Adapter source                          |
|---|----------------------|-------|------------------------------------------|
| 1 | CI status pills      | #178  | `gh api repos/.../check-runs`            |
| 2 | Host geometry        | #185  | `sac host list --json`                   |
| 3 | Agent mesh + ACL     | #189  | `sac a2a list --json` + `... grants`     |
| 4 | Timing backend       | #191  | card `_log_meta` timestamps              |
| 5 | Timing chart UI      | #192  | `/fleet/timing`                          |
| 6 | Chat surface         | #194  | per-card `comments[]`                    |

The board reads from authoritative registries; it never duplicates
state. Every adapter raises `FleetAdapterError` on missing data;
the UI surfaces a visible error state instead of silently degrading.

### Provenance

PR #194. Lead a2a `74db4f2d` + `10afa799` (vision); operator's
"one screen, watch the whole fleet, self-improvement" intent
realized end-to-end.

## [0.7.19] - 2026-06-14 — Phase 4 + 5: timing telemetry (backend + chart UI)

5 / 6 TRACK-2 dashboard surfaces shipped. Last remaining: Phase 6
chat. Operator's "record what took how long → self-improvement"
intent now visible end-to-end on the board.

### Added

- **Phase 4 — Timing telemetry backend** (PR #191). New
  `_django/handlers/fleet/timing.py`: pure
  `compute_timing(tasks, *, window_days=30)` derives three durations
  per task (`created_to_started` / `started_to_done` /
  `created_to_done`) from existing card timestamps (no state
  duplication), then aggregates per agent / project / group with
  median + p95 + median-queue. `_django/handlers/fleet/timing_view.py`
  exposes `GET /fleet/timing?window_days=N` (200 OK; 405 on POST;
  500 on store-read failure — fail-loud). `<ungrouped>` sentinel for
  null groups; `n_tasks_missing_timestamps` diagnostic surfaces
  done cards with broken `_log_meta`. 23 mock-free tests (16 pure +
  7 view). Phase 4.b gaps flagged inline: a2a-log scraping for
  per-turn agent durations, histograms / CDF arrays, p50/p75/p99
  knobs.
- **Phase 5 — Timing chart UI** (PR #192). New
  `FleetTimingPanel.tsx`: collapsed `📊 timing` pill in the STATUS
  toolbar group; click to expand. WINDOW (7d/30d/90d) + GROUP-BY
  (Agent/Project/Group) controls + inline SVG bar chart, one row
  per key with median + p95 bars. Sort by p95 desc so the
  bottleneck rides at the top. Tooltip carries `n_tasks_done` +
  `median_queue_s`. Footer carries `n_tasks_in_window` +
  `n_tasks_missing_timestamps`. 60s poll. Fail-loud on adapter
  error. 17 mock-free CSS/helper tests.

### Provenance

PR #191 + #192. Lead a2a `74db4f2d` + `10afa799`. Subagent execution
on both phases; Phase 5 subagent terminated mid-flight + the parent
agent finished the commit/push/PR.

## [0.7.18] - 2026-06-14 — Phase 3: agent mesh + ACL graph

### Added

- **Phase 3 — Agent mesh + ACL graph** (PR #189). New
  `_django/handlers/fleet/sac_mesh.py` adapter reads `sac a2a list
  --json` (peer registry) + `sac a2a grants --json` (comms_grants
  ACL). New `/fleet/mesh` Django endpoint. New `FleetMeshPanel.tsx`
  with an inline-SVG radial graph: nodes = agents, edges = grants,
  allow=`--status-success` green, deny=`--status-error` muted red.
  Mounted in the toolbar STATUS group. 26 new mock-free tests (10
  adapter + 4 view + 12 FE CSS/helper) + 119-test broader fleet
  suite green.
- **Phase 3.b follow-ups captured inline** (will land in a follow-up):
  - `comms_blocks` has no listing CLI yet → deny edges not wired
    (the shape already supports `allow: false`).
  - No heartbeat-freshness threshold in `sac a2a list` → status is
    `online` / `unknown`, never `offline`.
  - `state.db` path not surfaced → `config_path` returns null.

### Provenance

PR #189. Lead a2a `74db4f2d`. 3/6 TRACK-2 dashboard surfaces shipped
(CI / hosts / mesh). Remaining: timing telemetry + chat surface.

## [0.7.17] - 2026-06-14 — Hook-consumer contract + Time View + Phase 2 hosts

Wave 2 of the fleet-dashboard mission. The hook-consumer contract
is the operator-mandated "green static record pipe" — SAC's
push-hook + dev's merge-Action will call scitex-cards's API to
auto-record progress/DONE on the board.

### Added — Hook-consumer (loose-coupling contract)

- **`scitex_cards.hooks` entry-point group** (PR #187, lead a2a
  `6fff33d6` + `fbffb879`, operator-mandated). External producers
  register a plugin callable under this group:
  `def on_event(event: dict) -> None`.
- **Three converging wire surfaces** (producers pick one):
  - **HTTP**: `POST /hooks/push`, `POST /hooks/done`. Idempotent.
    405 on GET, 400 on bad shape / kind-mismatch.
  - **CLI**: `scitex-cards hook push --payload <FILE|->` /
    `scitex-cards hook done --payload <FILE|->`.
  - **Python**: `from scitex_cards._hooks import dispatch_event`.
- **Canonical event payloads**:
  - push: `{kind, repo, branch, commit_sha, author?, message?,
    card_ids?}`
  - done: `{kind, repo, pr_number, pr_url, author?, merged_at?,
    card_ids?}`
- **Built-in handlers run BEFORE plugins**:
  - push → idempotent comment-append (dedupe via full commit_sha
    substring match).
  - done → idempotent `pr_url` stamp + `status=done` flip (noop if
    already done with matching pr_url).
- **Plugin failures are caught + logged** — one bad plugin can NOT
  silently break the board's own record-keeping.
- 29 mock-free tests (validator fail-loud + handler idempotency +
  HTTP contract).

### Added — Dashboard surfaces

- **Time View** (PR #186, operator-direct via lead a2a `d0f7a0e3`).
  Live SVG raster timeline as the 5th LAYOUT toggle. Horizontal
  axis = TIME (1h/6h/24h/7d window); lanes by agent OR group; bars
  fade-out on done; depends_on/blocks edges drawn as connecting
  lines; click-through to the existing NodeDetailPanel. 30s poll.
  17 backend + 15 frontend mock-free tests. Pan/zoom/WebSocket are
  flagged follow-ups for future iterations.
- **Phase 2 — Host geometry** (PR #185, lead a2a `74db4f2d` +
  `10afa799`). `sac host list --json` adapter + `/fleet/hosts`
  endpoint + `FleetHostsPanel.tsx` mounted next to the CI pills.
  Fail-loud on missing `sac` CLI (FleetAdapterError → HTTP 500).
  Phase 2.b cpu/mem/SLURM enrichment landing site marked with
  `FOLLOW-UP(phase-2.b)`. 14 + 47 = 61 tests green.

### Provenance

PR #185 + #186 + #187. Lead a2a `74db4f2d` (vision) + `6fff33d6`
(hook-consumer mandate) + `d0f7a0e3` (Time View). Multiplier-#3
dogfooded on every PR.

## [0.7.16] - 2026-06-14 — TRACK 1 COMPLETE: parallelism-engine dispatch backbone

Completes the **dependency-aware ticket** track the operator/lead
vision (a2a `74db4f2d` + `10afa799`) named as the parallelism
engine. Combined with the v0.7.15 TRACK-2 Phase-1 CI pills, this
release closes Wave 1 of the fleet-dashboard mission.

### Added — TRACK 1 (parallelism-engine backbone)

- **T1.2 — `runnable_tasks()` API + `scitex-cards runnable` CLI**
  (PR #181). Batch runnable view (sister to `next_task`'s single
  pick) respecting `depends_on` + reverse-`blocks` closure +
  optional agent + group filter. Diagnostic counts
  (`candidate_count`, `blocked_by_deps_count`) let the dispatcher
  distinguish "queue empty" from "queue blocked." 22 mock-free
  tests.
- **T1.3 — `blocked_tasks()` inverse view + `scitex-cards blocked`
  CLI** (PR #182). For every NOT-runnable task, name WHY
  (`explicit-blocker` / `manual-block` / `depends-on` /
  `reverse-blocks`) + the chain of upstream ids. `by_reason`
  histogram for observability. 20 mock-free tests.
- **T1.4 — `/runnable` + `/blocked-batch` Django endpoints**
  (PR #183). JSON HTTP twins of the CLI verbs so the dispatcher
  consumes the data over HTTP. POST returns 405; fail-loud on
  load_tasks errors. 12 mock-free RequestFactory tests.

TRACK 1 wave list:
- T1.1 #179 (group field, in v0.7.15)
- T1.2 #181 (runnable API + CLI)
- T1.3 #182 (blocked inverse + CLI)
- T1.4 #183 (HTTP endpoints)

The lead-side dispatcher can now drive parallel work across agents
and groups end-to-end via either CLI or HTTP.

### Provenance

PR #181 + #182 + #183. Lead a2a `74db4f2d`. TRACK 2 (fleet
dashboard) continues in parallel — Phase 2 host geometry queued.

## [0.7.15] - 2026-06-14 — Fleet-dashboard Phase 1 (CI pills) + TRACK-1 `group` field

Operator vision (lead a2a `74db4f2d` + `10afa799`): scitex-cards
becomes the ONE fleet dashboard + dependency-aware ticket backbone.
This is wave 1 of two parallel tracks.

### Added — TRACK 2 (Fleet Dashboard)

- **Phase 1 — CI-status pills + Phase-0 registry-reader harness**
  (PR #178). New `_django/handlers/fleet/` package: `FleetAdapterError`
  (fail-loud on missing data, no silent fallback), `fleet_config_load`
  (reads `~/.scitex/cards/dashboard.yaml` or env
  `SCITEX_CARDS_FLEET_CI_REPOS=owner/name,...`; NO hardcoded slugs),
  `gh_ci.fetch_repo_ci_status` (`gh repo view` for default branch +
  `gh api .../check-runs`). New `/fleet/ci-status` Django endpoint
  with per-repo error trap (200 with `error` field per bad repo, 500
  on malformed config). Front-end `FleetCiPills.tsx` polls every 30s,
  per-repo green/red/amber/grey pill bound to scitex-ui status
  tokens. 33 fleet tests + full 277-task Django suite green. Pattern
  established for Phases 2-6 (hosts / mesh / timing / chart / chat).

### Added — TRACK 1 (Parallelism-engine backbone)

- **T1.1 — `group` field on Task** (PR #179, lead a2a `74db4f2d`).
  Optional `group: str | None` on the Task dataclass. The
  parallelism-engine dispatcher will ask
  `runnable(group=<G>)` so independent (dep-free) tasks within a
  group run concurrently per the operator's model. Free-form
  non-empty string; absent = ungrouped. Validator extends the
  existing scope/assignee non-empty-string loop. New `--group` CLI
  flag on `add` + `update` (empty string clears). Distinct from
  `_groups.py:Group` (project-cluster viewer aggregation). 15
  mock-free tests pin the dataclass shape, validator, Python API,
  and CLI wiring. Follow-up chain: T1.2 (`runnable()` API + CLI),
  T1.3 (`scitex-cards blocked` introspection), T1.4 (`/runnable` +
  `/blocked-batch` endpoints).

### Architectural principles enforced

- **fail-loud / no-silent-fallback** — adapters RAISE on missing
  data; no stubs.
- **registry-sourced** — read from authoritative GitHub via `gh`;
  scitex-cards doesn't duplicate state.
- **NO hardcoded proper nouns** — watched-repo list is fully
  config-driven; no `["scitex-cards","scitex-dev",...]` literals in
  source.

### Provenance

PR #178 + #179. Lead a2a `74db4f2d` + `10afa799` (refined brief
+ Q&A). Phase-1 subagent execution; T1.1 main-thread.

## [0.7.14] - 2026-06-13 — CLI: bare `board` hard-errors (noun-verb enforcement)

### Changed (BREAKING)

- **`scitex-cards board` (no verb) HARD-ERRORS** (PR #176, op TG 13316
  via lead a2a `c36b0d1e`). PR #139 (v0.7.6) had kept it as a
  deprecation-warn-and-forward to `board start`, but that path HID
  the noun-verb violation from audit tools. Bare invocation now exits
  2 + emits a redirect message naming the canonical replacements:

  ```
  ERROR: `scitex-cards board` (no verb) is no longer supported.
  Operator directive TG 13316 — noun-verb CLI convention. Use:
    scitex-cards board start [--port N] [--no-browser]
    scitex-cards board stop
    scitex-cards board restart
    scitex-cards board status
  ```

  In-tree call site migrated: `_jobs_provider.py`'s
  `scitex-cards.dashboard` JobSpec command now reads
  `scitex-cards board start --port 8051`. External call sites (the
  host systemd unit `scitex-cards.dashboard.service` ExecStart + any
  launcher script) need the same migration on the host side. Until
  they do, restarting them will exit 2 + log the redirect — which IS
  the operator's intended forcing function, but coordinate with the
  host-side deploy to avoid disruption.

  14 mock-free CliRunner tests pin the contract (exit code 2,
  redirect message, no forwarding, flags-don't-bypass).

## [0.7.13] - 2026-06-13 — Board UI wave-2: header declutter + Calendar view (4th LAYOUT)

Completes the operator-direct board UI overhaul (lead a2a `d1af161e`
+ `510a58d4`). With the v0.7.12 theme + Table-filter fixes, the
operator's board screenshot complaints (white scrollbar, white
dropdowns, cluttered Table view, cluttered toolbar) are end-to-end
addressed; new Calendar view satisfies op TG 13295.

### Added

- **Toolbar declutter** (PR #173) — the board's overcrowded toolbar
  is reorganized into 3 logical groups + a primary-action zone:
  `view` (LAYOUT toggle / Sort / Group), `search` (Search bar +
  Filters), `status` ("N new" badge / Reload / hide-project), and a
  brand-accent `+Add Task` primary action separated by a divider.
  Responsive wrap at ≤780px. All scitex-ui token-bound (no
  hardcoded colors). Behavior preserved — every original control id
  survives so existing onclick / event handlers / localStorage keys
  keep working. 31 mock-free tests pin the CSS contract + structural
  presence.
- **Calendar view — 4th LAYOUT** (PR #174, op TG 13295) — month grid
  (7×6) with task chips placed by `deadline_next` →
  `deadline` → `last_activity` precedence (pure-function helper
  `taskDateForCalendar` in `calendarDate.ts` for testability). Today
  gets accent ring, past days muted, weekends subtle bg-shift, Today
  pill snaps back to current month, prev/next nav. Chips click-thru
  to the existing NodeDetailPanel drawer. Token-bound; deferrals
  flagged for future PRs (drag-reschedule, week/day view, recurring
  expansion beyond server-provided `deadline_next`, inline edit
  on cell click, full a11y grid contract). 9 mock-free tests pin
  the date-assignment logic + grid generation.

### Provenance

PR #173 + #174 from the operator's design-intent directive +
TG 13295. Subagent-pair execution; both subagents dogfooded
multiplier-#3 (recorded their cards with `--pr-url` post-merge).

## [0.7.12] - 2026-06-13 — Board UI: themed scrollbar+dropdowns + Table-view structural filter

Two operator-direct UI fixes (lead a2a `510a58d4`, op TG screenshot
of the board's white scrollbar + un-themed dropdowns + cluttered
Table view). Header declutter + Calendar view follow in v0.7.13.

### Fixed

- **Themed scrollbar + `<select>`/`<option>` dropdowns** (PR #170) —
  the board's white-in-dark-mode scrollbar and OS-default white
  dropdowns now bind to scitex-ui shell tokens (`var(--col-bg)` /
  `var(--text)` / `var(--border)` / `var(--purple)`). Two layers:
  global `.stx-cards-board, *` fallback in `board.css` + a new
  `board_v3/00-theme-scrollbar-select.css` loaded FIRST in the
  template. 13 CSS-contract tests pin the rule set.

### Added

- **Table view: hide structural cards by default** (PR #171) — the
  `kind=status` quality-axis rows (8 q-*) and `kind=goal` umbrella
  rows (proj-clew / proj-cards / pool-* / ywatanabe-operator-anchor)
  are FILTERED OUT of the Table view by default; a "Show structural
  cards" checkbox in the toolbar flips them back on. Graph + Column
  views are unchanged — they keep showing every card per the
  existing dependency-graph contract. New `tableFilter.ts` helper
  exposes `STRUCTURAL_KINDS` + `isVisibleRow` so the filter is
  pure-function-testable. 5 new TS+Python tests.

### Provenance

PR #170 + #171 from the lead's a2a `d1af161e` (board UI overhaul)
triage. Subagent-pair execution model — one PR each, isolated
worktrees, multiplier-#3 dogfooded (both subagents recorded their
card with `--pr-url` post-merge).

## [0.7.11] - 2026-06-13 — Skill mandate: never hand-edit tasks.yaml

### Added

- **Canonical skill mandate: NEVER hand-edit `tasks.yaml`** (PR #168,
  lead a2a `02c8a4ae`). Folds into the bundled `scitex-cards` skill
  alongside the SSoT MANDATE and the multiplier-#3 PR-merge recording
  mandate. The 2026-06-13 corruption episode traced to a hand-edit
  bypassing the API. Rule: always use the CLI / MCP / Python API; the
  flock + atomic-rename + post-dump-validate path is the only safe
  write. Emergency-repair exception documented (already-broken file
  with backup-first / parse-verify-after / report-to-lead protocol).
  Propagates to every agent's required_skills via `scitex-cards skills
  propagate` (PR #161 mechanism), so every fleet agent reads it on
  boot. 4 mock-free file-content tests pin the load-bearing phrases.

## [0.7.10] - 2026-06-13 — Durable writer safety + CLI: --blocker '' clear

### Fixed

- **Writer: post-dump round-trip validation** (PR #166, lead a2a
  `d5809cd3`) — after the 2026-06-13 corruption episode where
  `~/.scitex/cards/tasks.yaml` was found truncated mid-string at line
  ~2784 and recovered by hand. Audit: the existing writer already had
  pre-write `_validate_tasks`, atomic-rename (tmp + fsync +
  `os.replace`), `fcntl.flock`, and tmp-cleanup-on-error. NEW LAYER:
  before `os.replace`, the writer now REPARSES the just-dumped tmp
  file from disk via ruamel and verifies both (a) it parses cleanly
  and (b) the reparsed task count matches the in-memory count. Either
  failure aborts with a `RuntimeError` and the canonical file is left
  untouched — never promote suspect bytes into the SSoT. 7 mock-free
  subprocess-based tests pin the contract (kill-mid-dump leaves
  canonical byte-identical; failed pre-write doesn't create a
  canonical file).
- **CLI: `--blocker ''`/`'none'` clears the field** (PR #165). Dev
  a2a (via lead `f5a54f85`): the strict `_BLOCKER_CHOICE` rejected
  `""` and `"none"` at parse time so there was no CLI form for
  clearing a card's blocker — `campaign-*` cards needing to flip a
  blocker off couldn't be closed from the CLI. New
  `_BlockerOrClearParamType` on the UPDATE verb honours both
  sentinels; ADD verb keeps the strict closed enum (you can't clear
  on insert). 7 mock-free CliRunner tests.

### Provenance

PR #166 + #165. Lead a2a `d5809cd3` + `f5a54f85`. The writer-safety
fix is the structural fix for SSoT-write hazard; the CLI clear-gap
fix closes the dogfooded blocker that surfaced from dev's reconcile.

## [0.7.9] - 2026-06-13 — Fleet-adoption multiplier #3: PR-merge recording mandate

Closes the **board-recording gap** surfaced by the 2026-06-13 reconciliation
pass (199 PRs merged in 24h vs ~5 board completions — structural, not a
hygiene problem). Adds a LOAD-BEARING mandate to the canonical scitex-cards
skill that propagates to every fleet agent via `skills propagate` (#161).

### Added

- **PR-merge recording mandate** (PR #163) — new `## ⚑ MANDATE — record
  evidence at PR-merge / issue-close time` section in `SKILL.md` + a
  sister leaf `60_pr-merge-recording-mandate.md` with the CLI/API/MCP
  verb table, no-PR alternative, bulk catch-up verb (`sync-github
  --since <date> -y`), anti-pattern list, and provenance. Hard rule:
  `scitex-cards done <card-id> --pr-url <merged-PR-URL>` IMMEDIATELY at
  PR-merge time; bare `done` without `--pr-url` is the recording-gap.
  8 mock-free file-content tests pin the load-bearing phrases so they
  can't drift silently. Lead a2a `0cdca03a` approved as fleet-adoption
  multiplier #3, sister to #160 (TaskCreate-redirect hook) and #161
  (skill propagation manifest).

### Provenance

PR #163 (`feat/skill-pr-url-mandate`). Diagnostic source:
`/work/GITIGNORED/RECONCILE_TRACE.json` — the 2026-06-13 reconciliation
pass.

## [0.7.8] - 2026-06-13 — Fleet-adoption multipliers (PreToolUse hook + skill propagation)

Ships the two **fleet-adoption multipliers** so every other agent in the
fleet uses scitex-cards correctly without per-agent buy-in. Lead a2a
`1b5c3b4d` prioritized both over the UX cards because they move the
operator's single-shared-store doctrine forward across the WHOLE fleet
in one bump.

### Added

- **Bundled PreToolUse hook** (PR #160): a bash script in the skill
  bundle (`_skills/scitex-cards/hooks/pre-tool-use/`) that any agent
  drops into `~/.claude/hooks/pre-tool-use/` and immediately gets
  the redirect. Intercepts Claude Code's built-in `TaskCreate`,
  `TaskUpdate`, `TaskList` — exits non-zero with a clear stderr
  redirect to the equivalent scitex-cards CLI verb. ENFORCES the
  doctrine, not just warns. Opt-out: `CC_ALLOW_CLAUDE_TASKLIST=1`
  for rare legit uses. 8 mock-free subprocess tests.
- **Canonical skill manifest + `scitex-cards skills propagate`**
  (PR #161): `_skills/manifest.yaml` lists which scitex-cards skill
  IDs every fleet agent should require. `scitex-cards skills
  propagate --agents-dir <DIR>` walks a tree of agent-container
  `spec.yaml` files and idempotently appends those IDs to each
  agent's `required_skills` list (ruamel.yaml round-trip preserves
  comments; SciTeX audit-cli §2 `--dry-run` + `-y`). Supports both
  `metadata.labels.skills` (v3) and `spec.required_skills` (older)
  shapes. 16 mock-free CliRunner tests.
- **Runbook leaf §22 — fleet-wide skill propagation**: documents
  the canonical manifest path + the agent-container integration.

### Provenance

PR #160 + #161 — fleet-adoption multipliers off the lead a2a
`1b5c3b4d` triage. Co-located with the existing P3a chain
(PR #155 / #156 / #158 / #159) so a single PyPI bump unlocks the
WHOLE single-shared-store + agent-redirect story for agent-container.

## [0.7.7] - 2026-06-13 — P3a fleet host-store wire-up + board-reconciliation verbs

Cuts the **P3a throughput unlock** (host scitex-cards store reachable from
every containerized agent, write-safety via flock-scoped RMW) into a
pull-able PyPI release so agent-container can bake the wire into
`to_home/.mcp.json`. agent-container a2a `e330b084` confirmed
`/home/agent/.scitex/cards` bind is fleet-wide; dev a2a
`dd971b57` + `932ea837` independently verified the host's 632-task
corpus is visible from their container. Also rolls up the
board-reconciliation verb sweep landed over 2026-06-13.

### Added

- **`scitex-cards mcp install [--apply] --env-tasks-path <abs/path>`**
  (PR #158) — when set, pins `SCITEX_CARDS_TASKS` in the generated
  `.mcp.json` entry's `env` block. Belt-and-suspenders for the
  bind-mount-based host-store resolution; makes the wire-up
  self-documenting in the generated config. Operator P3a, lead a2a
  `a579358e` + `d7789963`. agent-container's one-liner:
  `scitex-cards mcp install --apply --to to_home/.mcp.json --env-tasks-path /home/agent/.scitex/cards/tasks.yaml -y`.
- **`scitex-cards mcp install --apply`** (PR #155) — idempotent
  `.mcp.json` merge; the foundation #158 builds on. P3a fleet
  enablement.
- **`scitex-cards stale-list`** (PR #157) — terminal twin of the
  board's `🧹 Stale` panel + `/stale` HTTP endpoint. Lets agents
  reconcile from the CLI without opening the board.
- **`/stale` + `/archive` board endpoints + `🧹 Stale` layout +
  per-row Archive button** (PR #153 backend + #154 frontend) —
  recurring stale-review surface; 128 / ~218 candidate cards
  flagged for operator review at landing.
- **`scitex-cards close <id> --reason ...`** (PR #151) — close-stale-
  with-reason verb (board-reconciliation gap fix); writes
  `status=deferred` + a `[CLOSED]` activity comment.
- **`scitex-cards comment <id> <text>`** (PR #144) — CLI wrapping
  `_store.comment_task` (the PR #64 replacement).
- **Per-row multi-select + bulk status change on the board**
  (PR #150) — PR(h) Stage 1.
- **`kind=status` axis** (PR #146) — non-actionable quality-tracking
  cards; renders distinct from `kind=task` on the board.
- **Activity-bucket badge** (PR #148) — color cards by
  `last_activity` recency (fresh / warm / stale); pairs with PR #122
  backend decay.
- **Directory-card scanner + plan CLI** (PR #142) — PR-D Stage 1,
  operator-direct.

### Docs

- **Runbook §7.5 — fleet MCP enablement via `mcp install --apply`**
  (PR #156) — the P3a chain end-to-end recipe.
- **Board-reconciliation runbook — canonical verbs for fleet sweep**
  (PR #152) — covers the new close / comment / stale-list verbs.
- **Skill refresh — comment verb + kind=status + SSoT write-here**
  (PR #149) — keeps the bundled agent skill in lock-step with the
  current CLI.
- **Container/host tasks.yaml divergence audit** (PR #143) — the
  audit that became the P3a brief.

### Provenance

PR #158 (`feat/mcp-install-apply-env-tasks-path`), lead a2a
`a579358e` + `d7789963` + `f9c78d48` (the write-safety
follow-up — model: single shared file + flock-scoped RMW). Co-tested
with proj-scitex-dev (container end-to-end) and
proj-scitex-agent-container (fleet bind-mount confirmation,
canonical path lock-in).

## [0.7.6] - 2026-06-13 — board lifecycle verbs (start/stop/restart/status + pidfile)

Operator-direct TG12949/12950/12951 (via lead a2a `b5726672`).
`scitex-cards board` was a bare NOUN that directly LAUNCHED — CLI
noun-verb violation, AND no clean way to restart after a card/source
change (`port already in use` was the trap).

### Added

- **`scitex-cards board <verb>` lifecycle CLI** (PR #139):
  - `board start [--port --tasks --no-browser] [--dry-run] [-y]` —
    foreground launch, writes `~/.scitex/cards/board.pid` (env-
    overridable via `SCITEX_CARDS_BOARD_PIDFILE`).
  - `board stop [--timeout] [--dry-run] [-y]` — SIGTERM the pidfile
    PID; escalate to SIGKILL on timeout.
  - `board restart [--port --tasks --no-browser] [--dry-run] [-y]` —
    stop + start. THIS is the operator's "reload after a source
    change" shape.
  - `board status [--json]` — one-line / JSON read of the pidfile +
    liveness probe.
- SciTeX audit-cli §2 (mutating-verb `--dry-run` + `--yes/-y`) and §4
  (concrete Example blocks) compliance landed in the same PR.

### Changed

- Bare `scitex-cards board` (no verb) stays back-compat: forwards to
  `board start` with a stderr DEPRECATION line. Operator's muscle
  memory survives; the alias will be removed in a future minor bump.

### Provenance

PR #139 (`feat/board-lifecycle-verbs`), lead a2a `b5726672`,
operator-direct TG12949/12950/12951.

## [0.7.5] - 2026-06-13 — per-project lane UNION + board UX rescue + /graph perf

Three operator-visible improvements landed via the overnight
Stage 0-1 chain:

### Added

- **`services.get_board()` UNIONS the global store + every per-project
  lane** (`~/proj/*/.scitex/cards/tasks.yaml`, comma-sep override via
  `SCITEX_CARDS_LANE_GLOBS`). Skill 30's two-tier rollup is finally
  delivered; the operator's hand-curated `nv-lessons` + 31 other
  neurovista cards become visible on the board (lead a2a
  `1ceec0ef` / `40c0a42d`). Collision policy: project-lane wins on
  id, logged at WARNING. Malformed lane is SKIPPED + logged — the
  board renders the rest. (PR #137)
- **Empty-state banner on the board** when active filters narrow the
  result set to 0 cards (operator TG12911 — "filtering by nv-lessons
  does NOT work at all"). The banner offers a one-click "Clear all
  filters" so a 0-match state can't read as a broken filter. (PR #135)
- **mtime-keyed in-process cache on `/graph` payload** — skips the
  full `_build_graph` rebuild (mermaid + nodes + edges + fleet +
  groups) on cache hits, ~50-100 ms saved per /graph request on a
  500-task store. Cache invalidates on any source mtime change
  (PR #136, plays naturally with the new lane-union mtime = MAX).

### Internal

- `BoardState.lane_paths` exposes the successfully-consumed per-project
  lanes so the FE / tests / future indexer can see what was unioned.
- Suite-wide test isolation: `tests/scitex_cards/conftest.py` autouse
  fixture pins `SCITEX_CARDS_LANE_GLOBS=""` by default so existing
  fixture-pure tests don't pick up the test runner's host lanes.

### Provenance

PRs #135, #136, #137. Lead a2a `aa02fb0e` (Stage 2 design ACK) +
`1ceec0ef` / `40c0a42d` (lane-union ACK). YAML SSoT invariant
preserved throughout: read-side union only, no writes.

## [0.7.4] - 2026-06-12 — `_push.deliver` semantics: 30 s timeout + dispatched-on-read-timeout

Third (and likely last) cron-pilot hotfix. The 0.7.3 fix made the
receiver accept the body, but the client gave up too early: SAC's
`/v1/turn` runs the agent turn synchronously (up to ~120 s), and the
5 s client cap aborted before any turn could land in
`session.jsonl`. Probed `/v1/turn` for a fast-ack flag —
`wait=false`/`dispatch_only=true`/`async=true` all reject — so the
pragmatic stopgap (lead a2a `0b59485f`) is to give the client more
time AND treat the client-side read-timeout as "request was already
fully sent, receiver is mid-turn = dispatched success" so one slow
turn can't fail the nudge batch.

### Fixed

- **`DEFAULT_TIMEOUT_S` 5.0 → 30.0**, env-overridable via
  `SCITEX_CARDS_PUSH_TIMEOUT_S`. Reflects the receiver's actual
  budget so short ack-style turns complete cleanly.
- **Read-timeout treated as `DISPATCHED` success**
  (`ok=True, reason="dispatched"`), not `transport-error`. By the
  time the client read-timeout fires, the request body has long
  since been fully transmitted; treating it as success stops one
  slow turn from failing the whole `*/10` nudge batch. Connection-
  refused / DNS / SSL handshake errors still surface as
  `transport-error`.

### Tests

Real localhost `http.server` round-trips (no mocks, STX-NM / PA-306):

- `test_read_timeout_treated_as_dispatched_ok` — handler accepts the
  request body then sleeps past the client timeout; pre-fix this
  returned `reason=transport-error`, post-fix it returns
  `reason=dispatched`.
- `test_default_timeout_env_override` — `SCITEX_CARDS_PUSH_TIMEOUT_S`
  reflected at call-time.
- `test_default_timeout_falls_back_to_constant_when_env_unset` — bare
  case yields `DEFAULT_TIMEOUT_S`.

### Followup (out of scope)

Long-term: sac-listen should grow a real fast-ack endpoint
(e.g. `POST /v1/turn/dispatch` returning 202 + an async session id).
The pragmatic stopgap here can then be reverted.

### Provenance

PR #123 (`fix/push-timeout-env`), lead a2a `0b59485f` (root-fix
directive: not just a bigger timeout but DISPATCHED-success
semantics), proj-scitex-cards overnight mission.

## [0.7.3] - 2026-06-12 — `_push.deliver` payload aliases `text` to `body` (SAC /v1/turn unblocked)

Second hotfix found via the P3a(c) cron pilot. The 0.7.2 fix made the
cron survive its tick, but the POST then failed at the *receiver*:
SAC's `/v1/turn` (and `claude-code-telegrammer`'s TURN_URL) require a
`text=<msg>` field, while `_push.deliver` only sent `body=<msg>`. The
receiver returned `HTTP 400 "missing or empty 'text' field"`, so the
whole nudge chain still produced zero delivered turns.

### Fixed

- **`_push.deliver` now sends BOTH `text` and `body`** in the payload.
  `text` satisfies SAC + the telegrammer; `body` stays for back-compat
  with any pre-existing consumer keying off scitex-cards's historical
  name.

### Tests

Real localhost `http.server` round-trips (no mocks, STX-NM / PA-306):

- `test_post_carries_text_field_aliased_to_body` — the payload
  round-trip pins both fields.
- `test_succeeds_against_text_strict_receiver` — end-to-end against a
  stdlib `HTTPServer` that mimics SAC's 400-on-missing-text
  validation; pre-fix this returned `reason=http-error`, post-fix
  it returns `reason=delivered`.

### Provenance

PR #120 (`fix/push-text-alias`), lead a2a `8afe659e` (SPLIT directive
from the decay PR so the delivery fix ships first), proj-scitex-cards
overnight mission.

## [0.7.2] - 2026-06-12 — coerce naive ISO timestamps to UTC-aware (unblocks `--notify` cron)

Hotfix for the 10-min structural-nudge cron shipped in 0.7.1. The
P3a(c) cron pilot caught a `TypeError: can't subtract offset-naive
and offset-aware datetimes` raised by `_throughput._hours_since` on
the first `tasks.yaml` row whose `last_activity` was serialized
without a timezone suffix (e.g. `"2026-06-08T00:42:30"` vs.
`"2026-06-08T00:42:30Z"`). The cron then died silently every tick
BEFORE any POST fired, so no agent ever received a structural nudge.

### Fixed

- **`_throughput._parse_iso` always returns UTC-aware.** Naive ISO
  strings are coerced to UTC — the canonical assumption for
  `tasks.yaml` timestamps. One offending row no longer kills the
  entire `--notify` / `--nudge-quiet` sweep.

### Tests

- `TestNotifyBody::test_naive_last_activity_does_not_crash` —
  composes a notify body for a task whose `last_activity` lacks a
  timezone suffix.
- `TestParseIso::test_naive_string_coerces_to_utc_aware` — direct
  unit check on the helper.

### Provenance

PR #118 (`fix/parse-iso-utc-coerce`), lead-ACK a2a `cfbade6b` /
`f556b755`, proj-scitex-cards overnight mission.

## [0.7.1] - 2026-06-12 — 10-min structural-nudge cron + `--nudge-quiet` flag

Operator standing direction (lead a2a `19d575415a` + revision
`9e710ab074ef4bf3a615be41793e0c51`, 2026-06-12): the structural
feedback loop must push per-agent nudges every 10 minutes, not on
manual lead intervention. The 10-min threshold is the operator's
"silence + in_progress = escalation" rule from TG12600.

### Added

- **New `--nudge-quiet` flag on `scitex-cards print-stats`.** Per-agent
  sweep: if any open `in_progress` task hasn't been touched in
  `SCITEX_CARDS_NUDGE_QUIET_MIN` (default 10) minutes, push a
  quiet-nudge body via `_push.deliver(kind="quiet-nudge")` — the
  same self-contained HTTP push wire 0.7.0 introduced. Composes the
  full per-agent open list (RUNNABLE first, BLOCKED after) so the
  recipient sees the full picture, not just the stalled row.
- **`scitex-cards.notify` JobSpec** in `_jobs_provider.provide_jobs`.
  `kind="oneshot"` + `schedule="*:0/10"` → systemd runs it every 10
  minutes via the existing `scitex-dev ecosystem up` federation.
  Command: `scitex-cards print-stats --by agent --notify --nudge-quiet`.
  Pairs with the v0.7.0 UI nudge button: the cron is the STRUCTURAL
  feedback path; the button is the manual override.

### Out of scope

- Stdio MCP channel server + board-event poller (operator TG12618
  long-term plan) — tracked as PR (j) in the queue.

## [0.7.0] - 2026-06-12 — Self-contained push channel + nudge button + comment relay

Operator standing direction (lead a2a `f16b0d2a` + `9e710ab0` +
`8e51b1e0` + `ffc6629c80e4462a8401fb7e4ebb7240`, 2026-06-12,
operator TG12608 / TG12611 / TG12617): scitex-cards must NOT depend on
the `sac` CLI for outbound notifications. The package owns its own
push delivery, the contract is HTTP (not Python imports), and silent
fallbacks are forbidden — failures must be loud-but-not-fatal so the
operator can fix the config without breaking the running board.

### Added — `src/scitex_cards/_push.py` (self-contained HTTP push wire)

- `deliver(agent, body, *, kind=..., task_id=..., store_path=...)` —
  resolves the agent's turn URL from `SCITEX_CARDS_AGENT_TURN_URLS`
  (JSON map, canonical) or `SCITEX_CARDS_TURN_URL_<AGENT_SLUG>` (per-
  agent fallback, same shape as claude-code-telegrammer's
  `TURN_URL`). POSTs a JSON envelope (`agent` / `kind` / `body` /
  `task_id` / `store_path` / `ts` / `source: scitex-cards`) and
  returns a structured result with `ok`, `wire`, `reason`,
  `status`. No `sac` dependency.
- `SCITEX_CARDS_PUSH_DRY_RUN=1` short-circuits to stdout; useful in
  test / dev.
- `announce_missing_at_boot(tasks)` lists distinct agents in the
  store that have no turn URL configured; emits a single WARN log
  at board startup. Operator can iterate the config without a board
  restart per agent.

### Added — `POST /nudge` Django endpoint + UI button (PR g)

- New handler `_django/handlers/nudge.py` registered as the `nudge`
  endpoint. Body `{"agent": "<name>"}`. Composes the same per-agent
  body the `stats --notify` cron uses (`build_notify_body`) + an
  appended ACTION ask ("push or BLOCKED within 15 min"), then
  invokes `_push.deliver(agent, body, kind="nudge")`.
- Per-agent in-process cooldown (`COOLDOWN_SECONDS = 5 * 60`)
  matches the operator's spec; cooldown hit → HTTP 429 with the
  remaining seconds.
- UI: per-column `🔔` button (next to the existing `📌 pin` button).
  Click resolves the column's PRIMARY agent (modal agent among
  the column's tasks) and POSTs `/nudge`. Toast surfaces every
  result branch — success / no-turn-url-configured / http-error /
  cooldown-active / no-agent-attribution.

### Changed — Comment-relay hook on `POST /comment` (PR g)

- When a comment's `author != task.agent`, `handle_comment` invokes
  `_push.deliver(target, body, kind="comment-relay", task_id=...)`
  AFTER the write succeeds. Best-effort; relay failure does NOT fail
  the comment write. Relay outcome surfaces in the response so the
  UI can render a toast ("📨 relayed → <agent>" / failure marker).
- Comment-relay body invites the agent to reply via
  `scitex-cards comment <task-id>` (CLI) or `add_comment` / `comment_task`
  (MCP) — both surfaces are already available in v0.5.x.

### Changed — `print-stats --notify` migrated to `_push.deliver`

- `_cli/_stats.py::_push_notify` now calls `_push.deliver(agent,
  body, kind="notify")` instead of `subprocess.run(["sac",
  "agents", "send", ...])`. Same per-agent body as before; the wire
  swap is transparent to callers.

### Changed — Board boot announce (`board_v3_page`)

- Once per process, the board page logs a WARN listing the agents
  in the store with no turn URL configured. Single-shot via
  `_TURN_URL_ANNOUNCED` module flag.

### Tests

- `tests/scitex_cards/test__push.py` — 12 tests against a localhost
  `http.server` capture (no mocks, STX-NM / PA-306). Covers env
  resolution (JSON map + per-agent fallback + malformed JSON +
  missing), HTTP 200 / 4xx / transport-error, dry-run, and
  `announce_missing_at_boot`.

### Out of scope

- Dedicated stdio MCP channel + board-event poller mirroring
  claude-code-telegrammer's `~/proj/claude-code-telegrammer` shape —
  operator TG12618 long-term plan. Tracked as PR (j) in the queue.

## [0.6.0] - 2026-06-12 — `stats` CLI + WIP-validation gate + `sync-github` verb + `--notify` push

Operator standing direction via lead a2a `4b23ebc1` + `7489ac31` +
`6f24a752` + `5263c8d9` + `02b71bd0` + `130cc5ac` + `d99b8de6` +
`5acfbb5d` (2026-06-12): the fleet must measure its own creation vs
completion rate, push the per-agent numbers hourly so receivers
self-correct, hard-throttle add-task at 2× the agent's WIP limit, and
absorb GitHub merges back into the canonical board automatically.

### Added — `scitex-cards print-stats`

- New CLI: `scitex-cards stats [--by agent|project|host] [--since
  YYYY-MM-DD] [--format text|json] [--notify]`.
- Per-group rows: `name / open / stale / created / completed / delta
  / ratio / velocity_per_day`. Source = canonical `tasks.yaml`. The
  `created_at` field anchors the window; `last_activity` anchors the
  `done` projection; `in_progress` rows older than
  `SCITEX_CARDS_STALE_HOURS` (default 24) count as `stale`.
- `--notify` (agent grouping only): for each agent, push a body via
  `sac agents send <agent> <body>` (stdout fallback when `sac`
  unavailable). Body layout: HEADER (counts + ratio) → RUNNABLE
  tasks first, then BLOCKED (depends_on-gate / blocker-reason),
  capped at 10 + `+ N more`, then a RECENT DONE section. `⚠` marks
  stale in_progress so receivers see neglected work at a glance.

### Added — `scitex-cards sync-github`

- New CLI: `scitex-cards sync-github [--since YYYY-MM-DD] [--dry-run]`.
- Permanent version of the lead's 2026-06-12 one-time GitHub→board
  sync. Pulls `ywatanabe1989/*` merged PRs in the window, matches by
  `pr_url` (and creates new `status=done` records for unmatched PRs),
  collapses mechanical CI-speedup PRs (`title contains "ci-speedup"
  | "L1-L5"`) into a single bundle task per day.
- Designed for the scitex-dev cron registry's hourly poll — the lead
  registers the JOB_REGISTRY entry; this PR ships the verb itself.

### Added — WIP-validation gate on the write side

- `_store.add_task` now consults `_throughput.evaluate_wip(tasks,
  agent)` BEFORE the append. The agent's open-task count (`status
  NOT IN {done, goal}`) drives:
  - `>= SCITEX_CARDS_WIP_LIMIT` (default 20) → WARN to stderr.
  - `>= 2 × SCITEX_CARDS_WIP_LIMIT` → `TaskValidationError` HARD
    REFUSE; the message names the agent + the count + the limit.
- Goal-tier umbrellas (`status == "goal"`) are explicitly excluded
  per lead-confirm `5acfbb5d`.
- The gate is CLI/MCP/Python-path only — direct YAML hand-edits
  bypass it by design (operator wants the normal path made fat so
  hand-edits are unnecessary, not policed).

### Added — `_throughput.py` shared aggregator

- New module `src/scitex_cards/_throughput.py` — the single source of
  truth for "open" / "stale" / "completed" / "RUNNABLE" / "BLOCKED"
  semantics across the three new surfaces (stats CLI, WIP gate,
  notify body). The dependency classifier (`classify()`) is
  operator-confirmed defensive: an `depends_on` reference to a task
  id that doesn't exist returns `BLOCKED(→ unknown:<id>)` rather
  than silently treating it as RUNNABLE (lead-confirmed `130cc5ac`).
- 26 unit tests in `tests/scitex_cards/test__throughput.py` covering
  `aggregate` (groupings, status semantics, stale flag, unassigned
  rendering), `classify` (RUNNABLE / BLOCKED / unknown-dep
  defensive / status-blocked precedence), the WIP thresholds
  (warn / refuse / agent-attribution short-circuit), and the
  `--notify` body (RUNNABLE-first sort, truncation, ⚠ on stale,
  recent-done section).

## [0.5.9] - 2026-06-12 — Filterbar reorganization (3-group layout)

Operator UX feedback (lead a2a `b48f7c2c438b464698183d2e95d3bb04`,
2026-06-12): `current UI/UX is terrible` — the filterbar grew to
~108 px tall because every control sat in a single `display: flex;
flex-wrap: wrap` row and the wrap order was chaotic. Reorg into three
explicit groups so the placement is intentional, not flex-wrap-roulette.

### Changed

- **HTML**: wrap filterbar children in `.fb-left` (identity:
  title + version + LIVE chip) / `.fb-center` (search input +
  autocomplete suggest dropdown) / `.fb-right` (Layout segment +
  Sort + Filters popover + Recent count + Group + Add Task +
  blocking-me + project-hide + hidden + Reload).
- **Second row** `.filterbar-chips` — active filter chips
  (`#filt-chips`) + qualifier hint pills (`#filt-qhints`) moved off
  the main row into a slim band shown ONLY when populated (via
  `:has(...:empty)` selector). Default state collapses to a single
  ~48 px row.
- **CSS**: `.filterbar { display: flex; min-height: 48px }` with NO
  top-level wrap. The `.fb-right` group wraps internally on narrow
  viewports so the identity + search row stays intact regardless of
  how many right-side controls are visible.
- **Removed** the `margin-left: auto` hack from `.toggle-block` —
  explicit grouping now controls position; the auto-margin pushed
  this single button to the right edge in the old layout, which was
  the source of the asymmetric wrapping the operator photographed.

## [0.5.8] - 2026-06-12 — Graph view edges fix + fleetstrip removal + search-kbd pill folded into placeholder

Operator-reported regressions on `/`, lead-approved fixes:

### Fixed — Graph layout had no edges

- Lead `c212aa72bb0a4161b4faa8e81d508bc8` / `8af2a4a65fe94c9aa0e5f774598127a0`.
  PR #108's Graph view tried to read `t.depends_on` / `t.blocks` per-node
  and emitted a 400-node, 0-edge layout (41 552 px tall — operator
  element-inspector confirmed). The `/graph` endpoint doesn't expose
  those per-node — it returns them aggregated at top-level
  (`STATE.graph.edges`, 26 entries in the operator's live store).
  Hierarchical `parent` edges (111 of them, per-node) were also
  missing.
- Fix: `_renderGraphView` now consumes `STATE.graph.edges` directly
  for the depends_on / blocks set, and walks `t.parent` for the
  hierarchical edge set. Edges are visually distinguished — solid
  arrows for depends_on / blocks, dashed for parent. Graph is
  filtered to the connected component (nodes touched by ≥1 edge);
  disconnected nodes render in Column / Table layouts only.
- New empty-state: when 0 edges among the visible scope, the canvas
  shows a friendly explanation pointing at the `depends_on` / `blocks`
  / `parent` YAML encoding so the operator can fix the data.

### Removed — empty fleetstrip + standalone kbd-hint pill

- Lead `032e41545fcf4ab4b98d864ec1770249`. The `div#fleetstrip`
  rendered as `Content: none` because the payload never populated
  `STATE.graph.fleet` — operator: "i dont need this". The element +
  the orphan `renderFleetStrip()` helper are removed; fleet-liveness
  lives in the lead's periodic reports.
- The standalone `span.filt-search-kbd` "press / to focus" pill is
  removed. The same hint is now inline in the search input's
  placeholder text — operator: "just write the kbd in the search box".

## [0.5.7] - 2026-06-12 — User lane normalization + tighten left space + age pill + finish BLOCKING-YOU removal

Lead-HOLD-approved follow-up to 0.5.6 (PR #107, rebased on top of the
P0 LAYOUT-axis + Recent-sort merge):

### Changed — User lane normalization

- **The 360 px BLOCKING-YOU right-side aside is now FULLY removed.**
  0.5.6's render-refactor removed the JS that populated `#block-rows`
  but left the `<aside id="right-panel">` HTML in the template — the
  operator saw "loading…" forever in the right sidebar. This PR
  finishes the job: the `<aside>` block + the mobile `#by-fab` toggle
  + the `toggleByDrawer` / `updateByFabBadge` helpers are all
  removed. Operator-decision-blocked tasks live in the synthesized
  `user` lane in `_renderColumnView` (a normal column with normal
  width, drag-reorder, pin, column-context-menu).

### Changed — Left space tightened

- Board overrides the scitex-ui standalone shell so the
  `ws-ai-pane` (console / chat), `ws-worktree-pane` (file tree), and
  `ws-viewer-pane` (file viewer) are `display: none`. The kanban
  doesn't need any of those, and the operator reported "left empty
  space" eating the columns area. The `ws-module-pane` (board
  content) now uses the full viewport width.

### Added — Card age pill

- Each card carries a `⏳ Nd` pill in the header next to
  `last <activity>`. Stale color buckets:
  `today` mint-green "new" / `fresh 1–6d` muted / `aging 7–29d`
  amber / `stale 30–89d` orange / `rotten ≥90d` saturated red.
  Source is `created_at` (preferred) with `last_activity` fallback;
  null when neither parses (back-compat: legacy data shows no pill
  instead of `NaN`).
- CSS in `board_v3/02-card.css` (`.age-pill` + 5 modifiers, same
  shape as the existing `.date-pill` family).

## [0.5.6] - 2026-06-12 — Board v0.5.4 P0: empty-pill fix + LAYOUT axis + Recent sort

Lead-prioritized fix after PR #105 verification miss surfaced two
still-broken symptoms on `/` (the operator's primary board page):

### Fixed — cards rendered as empty pills on every lane

- Diagnosed against the live store at `:8051`: `business` lane's 28
  cards rendered with `offsetHeight = 24 px`; `scitex-dev` 68 cards at
  18 px; `paper-scitex-clew` 15 cards at 42 px. Root cause: `.col-body`
  is `display: flex; flex-direction: column` and `.main { height:
  100%; overflow: hidden }` (added in 22b6a6f to keep the BLOCKING-YOU
  aside from stretching). Inside a bounded flex column container,
  child `.card` items defaulted to `flex-shrink: 1` and compressed
  down to the card-status row when content exceeded container height.
- Fix: `.card { flex-shrink: 0 }` in `board_v3/02-card.css`. Cards
  keep their natural content height; the existing `.col-body
  { overflow-y: auto }` lets excess content scroll inside the column.
  Verified live by injecting the rule via Playwright `add_style_tag`:
  every lane's first card grew from 18-42 px back to 111-150 px.

### Added — LAYOUT axis (Graph | Column | Table) + Recent sort

Lead design ruling (TG 12461, operator-confirmed): the board renders
the SAME data along two orthogonal axes — LAYOUT (Graph | Column |
Table) sits in the filterbar; TIME (Recent) is a SORT mode in the
existing Sort dropdown, applies across all layouts.

- **LAYOUT switcher** — three segmented buttons in the filterbar.
  Persisted in `localStorage["scitex-cards:layout"]`.
  - `📋 Column` — the existing kanban (default).
  - `📑 Table` — flat rows view, sortable, click a row to open the
    detail drawer. Status / Title / Project / Blocker / Priority /
    Last activity columns.
  - `📊 Graph` — depends_on / blocks mermaid graph, lazy-loads
    `mermaid@10` from jsdelivr the first time the operator switches
    to it.
- **Recent sort mode** — `Recent (newest first) 🆕` option added to
  the existing `#f-sort` dropdown. Cards sort by `last_activity →
  created_at` desc; cards with activity in the last 24 h get a gold
  `NEW` badge in `.card-top`. The badge renders across every layout
  when sort = recent. Persisted in `localStorage["scitex-cards:sort"]`.
- **🆕 N new in 24 h pill** — always-visible filterbar indicator
  showing how many of the currently-visible cards moved in the last
  day. Click to set Sort = Recent. Hidden when zero.

CSS lives in a new sibling file `board_v3/06-layout-and-recent.css`
(keeps the per-file CSS under the 512-line hook limit). Linked from
`board_v3.html`'s `{% block extra_css %}`.

## [0.5.4] - 2026-06-12 — Board v0.5.3 display fix (template leak + bundle/template food)

Operator-reported regression after the 0.5.3 release:

- Multi-line `{# … #}` comment block leaked verbatim ("`{# searchQuery.js …
  #}`") into the rendered HTML at the top of every board page.
- Cards in every lane rendered as empty pills (no text) — `board_v3/*.css`
  had been wiped from the static dir.
- View toggle (Graph / Table / Recent) was invisible — the React SPA bundle
  was out of sync with the TypeScript source.

### Fixed

- **Template comment leak** (PR #105). Replaced the two multi-line `{# … #}`
  blocks in `board_v3.html` with `{% comment %} … {% endcomment %}`.
  Django's `{# … #}` is single-line only; multi-line blocks render their
  body as page text. Already pinned by
  `test_standalone_template_does_not_leak_django_comment` in
  `tests/scitex_cards/_django/test_views.py`.
- **Bundle/template food (root cause)** (PR #105). The vite config wrote
  into `../static/scitex_cards` with `emptyOutDir: true`, which wiped the
  SIBLINGS of `assets/` on every rebuild — `favicon.svg`,
  `board_v3/*.css`, and `board_v3/searchQuery.js`/`searchSuggest.js` are
  all tracked-in-git static assets consumed by the live `board_v3.html`
  template. We now scope `outDir` to the `assets/` subdir, so a rebuild
  only ever touches the React SPA bundle and never the board_v3 statics.
- **Bundle rebuild from current TS source** (PR #105). Clean
  `npm install` + `vite build` ran against the post-#104 source so the
  shipped `assets/index.js` / `assets/index.css` matches the TypeScript
  source (the Graph / Table / Recent toggle ships with the bundle).

## [0.5.3] - 2026-06-12 — Board UX wave + self-consuming loop + deadline schema

Captures every PR that landed between 0.5.2 and develop tip (operator
TG 12028 / 12038 / 12081 wave).

### Added — Board UX

- **Search-as-launcher** (PR #86). `#f-search` becomes the primary
  filterbar control; press `/` from anywhere to focus, `Esc` to blur.
  Purple-haloed at rest, brighter on focus, kbd-hint chip advertises
  the affordance.
- **Filter UX collapse + active chips + sort-by** (PR #89). Six
  filter dropdowns hide behind a single `🔧 Filters (N active)`
  popover; active filters render as removable chips; new sort-by
  selector (deadline / priority / status / project / last_activity /
  title).
- **Self-named project-umbrella cards hidden** (PR #87). A card
  whose title matches its column name is suppressed inside that
  column.
- **Move-picker lists ALL projects + Create-new** (PR #88 + #94).
  Right-click → Move picker is a Combobox over every project in the
  store with `+ Create '<query>'`.
- **Combobox primitive in scitex-ui** (scitex-ui #36 + #37, consumed
  via PR #94). Fuzzy-typeahead select layered over the six filterbar
  dropdowns + the move-picker; pure-JS bundle for Django-template
  consumers.
- **Project GROUPS** (PR #91). User-defined clusters of projects;
  new top-level `groups:` key in `tasks.yaml`; `spans_all: true`
  banner above the grid; group-header rows between clusters.

### Added — Deadlines + org-mode bridge

- **`deadline` + `scheduled` fields on `Task`** (PR #92). ISO-8601
  strings; validator rejects empty / unparseable / `deadline <
  scheduled`.
- **Org-mode export adapter** (PR #93). `build_org(tasks) → str`
  emits `DEADLINE:` / `SCHEDULED:` + properties drawer. 17 tests.
- **Multi + recurring deadlines** (PR #97). Org-style repeater suffix
  on the single field (`+1w` / `++2m` catch-up); optional `deadlines:
  list[str]` mutually exclusive with the single field; server emits a
  synthetic `deadline_next` for FE consumption.

### Added — Self-consuming board loop (operator TG 12038)

- **`scitex-cards next` CLI verb** (PR #95). Canonical "what to pick
  up next" predicate. `--mine` reads `SCITEX_CARDS_AGENT`;
  `--auto-claim` atomic-flips to `in_progress` + stamps a starting
  comment in one write.
- **`scitex-cards watch --push` CLI verb** (PR #95). Polls tasks.yaml,
  diffs, POSTs `/v1/turn` to the owning agent's a2a port on
  new / commented / status-changed tasks. Watcher declared as a
  second `kind=service` JobSpec.
- **Agent self-consumption loop sub-skill (32)** + **MANDATE block in
  `SKILL.md`** (PR #90 + #95).

### Fixed

- **P1 + P7 regressions restored** (PR #96). The P10/P11 squash wave
  silently dropped P1 #86 + P7 #87 from develop; PR #96 restores both
  and pins **24 substring signatures** in
  `tests/scitex_cards/test__board_v3_signatures.py` so future squash
  drops fail CI instead.

### Notes for operators

After upgrading: `systemctl --user restart scitex-cards.dashboard`.
The new `scitex-cards.wake-watcher` unit needs
`systemctl --user reset-failed scitex-cards.wake-watcher` followed by
`systemctl --user enable --now scitex-cards.wake-watcher`.

## [0.4.2] - 2026-06-08 — Crash-safe store + version label + Uncategorized column

Patch release in response to the 2026-06-08 autoassign-parallel-run
data-loss incident: roughly 130 operator-added tasks lost when two
concurrent autoassign scripts were SIGTERM'd mid-`save_tasks` dump
and the store was left half-written. This release closes the bug at
the store layer + makes the live release visible on the board.

### Fixed (crash-safety, lead a2a `3b0df14a`)
- **Atomic write in `save_tasks`** — dump now goes to a sibling `.tmp`
  file, fsync, then `os.replace(tmp, tasks.yaml)`. POSIX-atomic; a
  SIGTERM/SIGKILL mid-dump can no longer leave the canonical file
  half-written. The pre-existing `fcntl.flock` on the sidecar lockfile
  is unchanged.
- **Git auto-commit on every save** — lazy-initializes a `.git` inside
  the store directory on first save_tasks call, then commits each
  successful write. Operator gets time-travel: `git -C ~/.scitex/cards
  log` + `git show <sha>:tasks.yaml` to restore any prior state.
  Best-effort: a git failure never blocks the actual save.

### Added (board v3)
- **`scitex-cards vX.Y.Z` page title + header** (operator TG 407). The
  live `__version__` is read off the package import and rendered in
  both the `<title>` tag (browser tab) and the in-page H1. No second
  source of truth to drift on release.
- **"Uncategorized" replaces "Ungrouped"** (operator TG 405). The
  no-project column label aligns with the legacy "Uncategorized pool"
  convention from PR #4 and reads as plain English. Internal grouping
  key + filter dropdown both updated.

### Notes for operators
After upgrading: restart your `scitex-cards board` systemd unit.
`~/.scitex/cards` becomes a git repo on the first board write — the
operator can `git -C ~/.scitex/cards log` immediately, no extra setup.
Any future corruption is recoverable via standard git commands.

## [0.4.1] - 2026-06-08 — Board v3 horizontal layout + column pin + drag-reorder + fleet-liveness

Patch release on top of 0.4.0 to unblock operator UX (TG 370) the
moment they saw 0.4.0 live: project columns stacked vertically with
many projects + no way to reorder / prioritize them.

### Fixed (board v3)
- **Columns now lay out side-by-side with horizontal scroll.** The
  previous CSS grid `repeat(auto-fit, minmax(220px, 1fr))` wrapped
  many-column boards into a 40,000px tall stack (operator's element-
  inspector dump confirmed 39929px height). Switched to a single-row
  flex strip with `overflow-x: auto`; each column is a fixed 280px
  wide. Kanban / Trello / Linear convention.

### Added (board v3)
- **Column drag-to-reorder.** Each column section is `draggable`;
  drop on another column inserts BEFORE that target. Order persists
  in `localStorage` under `scitex-cards:col-order` (per-browser
  preference, no backend change).
- **Column pin (📍 / 📌).** Per-column pin button in the header.
  Pinned columns float to the LEFT of the strip regardless of drag
  order. Persists in `localStorage` under `scitex-cards:col-pinned`.
- **Fleet-liveness dot-strip** (PR #75) — one colored dot per agent
  in the filter bar, gold/green/blue/grey by status, click toggles
  the agent filter. Powered by a new `fleet` summary on `/graph`
  (additive — no schema change).

## [0.4.0] - 2026-06-08 — Board v3 + scitex-ui shell + task-harvest skill

The shared-fleet board matures into a real Django app: the live
**board v3** (kanban + BLOCKING-YOU panel + Resolve → notify wire) is
promoted to the package root and now extends the **scitex-ui shell**
so it picks up the Alt+I element-inspector + shared chrome for free.
The **Task dataclass** becomes the single schema source. The
**task-harvest skill** documents the operator-commissioned backlog
burn-down loop (2-state model, 4-value blocker enum, root-blocker
walk, `scitex-dev cron` registration). Compute-state-deps + decision-
nodes + ports skeleton land for the north-star roadmap.

### Added (board)
- **Board v3** — live Django board (kanban-style columns, status
  filters, BLOCKING-YOU panel, Resolve → a2a notify wire). Promoted to
  root URL (`/`); legacy GraphView demoted to `/legacy/`. (#57, #58.)
- **scitex-ui shell integration** — board v3 extends
  `scitex_ui/standalone_shell.html`, so Alt+I element-inspector +
  shared chrome work the same way on board v3 as on the legacy
  GraphView. Compatibility with scitex-hub register-as-module via
  `scitex_app._django.ScitexAppConfig` preserved. (#69.)
- **CRUD endpoints** on the Django backend (`/create`, `/update`,
  `/delete`, `/comment`, `/edge`, `/restore`, `/priority`, `/resolve`)
  — see `handlers/crud.py`; UI wiring on board v3 ships incrementally.
- **Board v3 Resolve safety** — 2-click confirm + Undo toast + new
  `/reopen` endpoint so an accidental Resolve is recoverable. (#61.)
- **Board v3 comments + priority + hide** — Word-style comment thread
  + per-card priority up/down + hide button. (#62.)
- **ESC closes the detail modal** (operator TG 265). (#59.)
- **Drill-down clarity** — empty-state explainer, Pool label, Back
  button + region labels (Board / Drill / Canvas / Pool) + count
  breakdown (Total·Showing·Nested·Pool). (#50, #51.)
- **Hover affordance** — replace parent-node tilt with a "⊞ Drill in"
  hover-hint pill (operator TG 245). (#53.)

### Added (schema / Task dataclass)
- **Task dataclass = single schema source** (#56). All schema
  validation flows through one dataclass; `_validate_tasks` consumes
  it; the Gitea adapter + the future README-frontmatter SSoT both
  consume the same shape. 9 new operator fields (`task` /
  `last_activity` / `host` / `pr_url` / `issue_url` / `agent` /
  `project` / `goal` / `created_at`) land.
- **D11 stamping** (#67) — `created_at` is auto-stamped on `add_task`;
  `last_activity` is auto-stamped on `update_task`.
- **Field-flag expansion for `add` / `update`** + closed-enum CLI
  validation (#65). Every operator-facing field is now a `--flag` on
  the CLI; closed enums (`status` / `kind` / `blocker`) reject typos
  at write time.
- **Compute-state-deps north-star pillar #1** (#52) — `kind` enum
  (`task` / `compute`) + compute metadata (`job_id` / `host` /
  `command` / `started_at` / `finished_at`) + ⚙ glyph + KV table.
  Compute jobs (Spartan / SIF builds / CI) become first-class graph
  nodes that external watchers can flip done.
- **Decision-nodes + closed BlockerKind enum** (#54) — `kind: decision`
  + ⚖️ glyph + LOUD operator-decision halo + "unblocks N" impact badge
  + 👤 awaiting-you lens. North-star pillar #4.
- **Core / Extension Ports / Fleet Adapters skeleton** (#55) — ADR-0006
  backbone for the open-source / fleet-adapter split.

### Added (skills)
- **`11_adopting-from-a-project`** (#60) — 30-second adoption how-to
  for project agents to write their tasks into the shared board.
- **`40_task-harvest`** (#70, #72) — operator-commissioned backlog
  burn-down protocol: 2-state model (BLOCKED + reason + dependency
  from a 4-value enum vs RUNNABLE), 2-phase sweep cycle (Phase 1
  re-check blockers + walk `task-dependency` chains to their LEAF
  root-blocker; Phase 2 escalate every RUNNABLE task to its owning
  agent via a2a), lead-centric funnel routing, and registration as a
  `scitex-dev cron` JobSpec.

### Fixed
- **`scitex-cards board --tasks PATH`** now actually pins the server's
  store (was previously a no-op for the Django subprocess — only the
  browser URL query was set). (#46.)
- **Audit pipeline unblocked** — TQ002 / TQ007 + PS-202 / PS-204
  violations fixed. (#68.)

### Notes for operators
After upgrading: restart your `scitex-cards board` systemd unit so the
board picks up the scitex-ui-shell extension. Alt+I + element-
inspector work immediately after restart. CRUD UI on board v3 wires
to the existing endpoints incrementally — Resolve + Priority +
Comment + Hide already land in this release; full Create / Update /
Delete UI ships in a follow-up patch.

## [0.3.0] - 2026-06-04 — Phase 1 MVP: shared-fleet card board

The universal-task-layer FLOOR for the agent fleet. Every agent can
read/write the same YAML store across hosts, the board at
http://127.0.0.1:8051 aggregates everyone's tasks for the operator,
and the Python API / CLI / MCP surface follows scitex-dev audit
conventions (Convention A: tool_name == python_api_name).

### Added
- **Per-task `scope` / `assignee` fields** (additive-optional, free-form
  strings). Convention is `agent:<name>` / `project:<name>` / `private`
  but the schema doesn't enforce it (Req 8: be generic).
- **`_log_meta` mapping** — opaque event-stamp dict; `complete_task` writes
  `completed_at` (ISO-8601 UTC, `Z`-suffixed, second resolution) +
  `completed_by`. Phase-2 progress-history substrate.
- **Mutation Python API** (`scitex_cards._store`, re-exported from
  `scitex_cards`): `add_task`, `update_task`, `complete_task`, `list_tasks`,
  `summarize_tasks`, `resolve_store`, `TaskNotFoundError`, `ENV_SCOPE`,
  `ENV_AGENT`. The public top-level surface is narrowed to these six
  task-store functions (plus errors / env constants) to satisfy audit §6
  (Convention A: tool_name == python_api_name). The mermaid / render /
  model / paths helpers remain importable from their submodules
  (`scitex_cards._diagram`, `scitex_cards._diagram`, `scitex_cards._model`,
  `scitex_cards._paths`).
- **CLI write / admin verbs**: `add`, `update`, `done`, `summary`, plus
  `list-tasks` (extended with `--scope` / `--assignee` / `--status`
  filters; backward-compatible default output for existing `list-tasks`
  users), `resolve-store`, `init-store [--shared|--project]`,
  `sync-store [--dry-run|--apply]` (Phase-1 stub). Mutating verbs
  (`add`, `update`, `init-store`, `sync-store`, `mcp start`, `mcp install`)
  accept `--dry-run` + `-y`/`--yes` per audit §2. The pre-audit names
  `list` / `where` / `init` / `sync` were renamed per audit §1 (bare
  transitive verbs at the top level need an object noun).
- **MCP server** (`scitex_cards._mcp_server`) behind the new `[mcp]` extra
  (`fastmcp>=2.0`). Eight tools — six task-store tools follow
  Convention A (tool_name == python_api_name, no prefix): `add_task`,
  `update_task`, `complete_task`, `list_tasks`, `summarize_tasks`,
  `resolve_store`; plus `cards_skills_list` / `cards_skills_get` for
  bundled-skill discovery. `import scitex_cards` works fine without the
  extra installed.
- **`mcp` CLI subgroup** — §3 required four (`start`, `doctor`,
  `list-tools`, `install`). Prefers `scitex_dev._mcp_cli` when present;
  hand-rolled fallback otherwise.
- **`fcntl.flock` mutex** on `save_tasks` (and the new mutators in
  `_store`) holding the full read-modify-write cycle. Phase-1 prereq for
  the Phase-2 cross-host sync substrate (Req 2).

### Documented
- `GITIGNORED/ARCHITECTURE.md` — Phase-0 9-requirement → mechanism map.
- `GITIGNORED/QUESTIONS.md` — open defaults for the operator/lead.
- `GITIGNORED/PROPOSAL_scitex-dev-ecosystem-register.md` — paste-apply
  diff for the lead so `scitex_dev.ECOSYSTEM` includes `scitex-cards`
  (Req 6).

### Test surface
- +47 real tests (no mocks). The two-subprocess concurrent-writer test
  proves the lock serializes interleaved inserts (the failure caught
  while writing it was the source of the `_save_tasks_unlocked` split —
  the lock has to wrap the full read-modify-write, not just the write).

## [0.2.0] - 2026-05-27

### Added
- Web board (read-only React-Flow dependency graph) served by Django:
  `scitex-cards board` (needs the `[web]` extra). Nodes colored by status,
  `depends_on` arrows, `blocks` inhibition edges, clickable cards, and
  nested-graph drill-down via a new `parent` task field.
- Drag-reorder write path: the board's `POST /priority` handler persists a new
  ordering back to the YAML store (preserving comments via ruamel) — the first
  agent↔user GUI write surface. `save_tasks` is now public.
- §1a CLI introspection: `list-python-apis` (with the additive `-v/-vv/-vvv`
  ladder) and `mcp list-tools`, both with `--json`.
- Shell completion: `install-shell-completion` / `print-shell-completion`
  (bash/zsh/fish) using the static cache-file pattern.
- Agent skills: bundled `_skills/scitex-cards/` (installation, quick-start,
  python-api, cli-reference, env-vars) plus a self-contained
  `skills {list, get, install}` CLI group.
- `python -m scitex_cards` entry point; `.env.example`; `examples/` with a
  matching `tests/examples/` smoke test; cross-package integration gate.

### Changed
- **CLI verbs renamed** to noun-verb compounds (audit §1): `render` →
  `render-graph`, `list` → `list-tasks` (now with `--json`). Added top-level
  `--help-recursive` and `--json`.
- `_cli.py` split into a focused `_cli/` package (`_main`, `_introspect`,
  `_completion`, `_skills`).
- README rebuilt to the canonical SciTeX layout (logo, badges, Problem/Solution,
  Architecture diagram, Interfaces, footer); `docs/roadmap.md` refreshed.
- Added GitHub Actions: `tests`, `import-smoke`, `quality`, tag-driven
  `release` (PyPI via OIDC + GitHub Release), and the CLA gate.
- Test suite reorganized to mirror `src/` and to satisfy the test-quality rules
  (one assertion per test, AAA markers).

[0.2.0]: https://github.com/ywatanabe1989/scitex-cards/releases/tag/v0.2.0

## [0.1.0] - 2026-05-22

### Added
- Canonical YAML task store: top-level `tasks:` list with `id` / `title` /
  `status` (required) and optional `repo` / `depends_on` / `blocks` / `note`.
- `load_tasks` — validating loader (`TaskValidationError` on missing id/title,
  duplicate id, or invalid status). Statuses: `goal`, `pending`,
  `in_progress`, `blocked`, `done`, `deferred`, `failed`.
- Mermaid adapter: `build_mermaid` renders `flowchart TB` with `depends_on`
  arrows, `blocks` inhibition edges (`-- blocks --x`), and per-status colors
  (goal = gold `#ffe082`).
- Renderer: `render` (mmdc-first with auto-discovered puppeteer/playwright
  chromium and `--no-sandbox`; `kroki.io` fallback).
- Task-store path resolution following the SciTeX local-state convention:
  explicit path -> `$SCITEX_CARDS_TASKS` -> project `.scitex/cards/tasks.yaml`
  -> user `~/.scitex/cards/tasks.yaml` -> bundled generic example.
- CLI `scitex-cards` (Click, noun-verb): `render`, `list`.
- Bundled generic example task store at `scitex_cards/examples/tasks.yaml`.

[0.1.0]: https://github.com/ywatanabe1989/scitex-cards/releases/tag/v0.1.0
