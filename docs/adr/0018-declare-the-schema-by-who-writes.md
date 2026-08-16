# ADR-0018 — Declare the schema by WHO WRITES, and name each column for what it holds

**Status:** accepted, 2026-08-14
**Owner:** scitex-cards
**Card:** `cards-land-the-store-merge-plugin-20260814`
**Depends on:** ADR-0016 (one authoritative store per deployment; the
surviving no-delete-from-absence invariant)
**Supersedes:** the `card_json` entry in `src/scitex_cards/_store_plugin.py:137-145`,
which records the derived-vs-canonical question as *"a schema decision this package
has not made yet"*. It **was** made — in `_db_payload.py` — and this ADR states the
replication consequence that follows from it.

## The one-sentence rule

**Split the replicated schema by WHO WRITES a thing, not by what is convenient to
map.** One writer per field means one JSON document under last-writer-wins; many
writers per field means a real column with a real merge rule.

## Context

scitex-cards has to declare its data to `scitex_dev.store` so cards can replicate
across hosts. The primitive has **no default merge rule** — `_policy.py:7-11` makes
an undeclared field an error at schema construction, on the stated grounds that
*"a default merge rule is the dangerous one"* (`_policy.py:15-20`). So every field
must state a rule, and a wrong rule loses data without raising.

The obvious declaration is the one already drafted: map each typed column of the
`tasks` table to a `FieldPolicy` and let the primitive merge them per field. That
draft exists (`_store_plugin.py:65-124`) and it is where this ADR starts, because
the draft is right about the columns it names and wrong about what it leaves out.

### Why NOT invert to typed-columns-as-truth

`_db_payload.py` already decided this question for the *read* path, and it recorded
the measurement that decided it (`_db_payload.py:5-27`, measured 2026-07-13 on the
live 1,452-card store; the same numbers are mirrored in `_db.py:74-83`):

- **22 distinct card keys are not in the column mapping at all** — `deferred_at`
  (20 cards), `subagent` (8), `blocked_by` (3), `completed_at`, `tasks_path`,
  `canonical_spec`, `next_action`, and a family of ad-hoc `note_*` fields agents
  invent as they work.
- **711 distinct key ORDERS** exist across the cards, so a column-based rebuild
  imposes one order on all of them and anything that serializes a card changes
  shape.

The file states the property this buys, and it is the property this ADR preserves:

> the read is exact BY CONSTRUCTION, not by a mapping someone has to remember to
> update when a new field appears. A field this file has never heard of round-trips
> anyway.
> — `_db_payload.py:26-27`

Inverting to typed-columns-as-truth silently drops every field that has no column.
Confirmed absent from both `_TASK_SCALAR_COLS` (`_db_bootstrap.py:45-74`) and the
`tasks` DDL (`_db_schema_sql.py:61-95`): **`parked`, `urgency`, `importance`,
`rank`, `scored_at`.**

The concrete harm is not hypothetical and not cosmetic. `parked` is the
standing-card exemption (`_backlog_triage.py:73-76`). A card parked on host A
arrives on host B with no `parked` key, so on B it is neither exempt from the
backlog nudge (`_backlog_triage.py:179`, `:248`) nor from expiry — and expiry
*"proposes CANCELLATION by default and cancels on silence"*
(`_backlog_triage.py:169-175`). Replication would auto-cancel the very cards the
park exists to protect, on the host that did not hear about the park.

### Why per-field column merge is not required

The premise that cards must have per-field merge to be replicated was ours, not
upstream's. `_policy.py:78-80`, in `MergeRule`'s own docstring:

> No general CRDT machinery — scitex-cards explicitly does not need field-level
> CRDTs, and shipping them would be complexity nobody asked for.

Upstream and this package's read design already agree. Nothing had to be
negotiated; the constraint was self-imposed.

## Decision

### D1 — The card's own fields travel as ONE declared JSON column under LAST_WRITER_WINS

The verbatim card mapping is declared as a single `FieldKind.JSON` field with
`MergeRule.LAST_WRITER_WINS`. The typed columns stay exactly what
`_db_payload.py:23` calls them — the INDEX — and are not separately declared as
replicated data, because a denormalised copy merged per-field beside the document
it duplicates produces rows that are internally inconsistent while every field
merged "correctly". That hazard is stated correctly in the draft
(`_store_plugin.py:137-145`); this ADR resolves it in the other direction from the
draft's guess — the document is canonical, the columns are derived.

### D2 — The genuinely multi-writer parts get their own columns

These are the parts where two hosts legitimately write *different* things that must
both survive, so a whole-document LWW would be a real loss rather than a race:

| part | rule | upstream's own words |
|---|---|---|
| `comments[]` | `APPEND` | *"For comments, notifications, messages"* — `_policy.py:98-101` |
| `depends_on` / `blocks` | `UNION` | *"For edges and role assignments"* — `_policy.py:102-103` |
| `collaborators` / `subscribers` | `UNION` | same |

Both collection rules require `FieldKind.JSON` (`_policy.py:214-215`).

**`comments[]` has an unmet prerequisite — see [Open questions](#open-questions) Q2.**
It is a prerequisite, not a reason to pick a different rule.

### D3 — The lifecycle facts leave the `_log_meta` blob

Under LWW, `merge_field` returns the incoming value wholesale when its stamp is
higher (`_merge.py:116-119`). For a JSON field holding the whole card, that means
the whole document. Two lifecycle facts currently live *inside* that document and
must not be decided that way:

- **The tombstone.** `_log_meta.deleted_at` is the sole delete marker
  (`_task.py:183-186`), since the 2026-07-21 P0 replaced physical `DELETE` with an
  in-place mark under the operator's ruling 「一度書いたものは消えない」
  (*"a written card never disappears"*, `_task.py:192-194`).
- **The completion stamp.** `_log_meta.completed_{at,by}` are *"the SOLE input to
  the throughput/timeline aggregates"*, which never consult `status`
  (`_store_lifecycle.py:37-42`).

So: host A deletes a card, host B defers it at a later HLC. LWW takes B's whole
document, `deleted_at` is not in it, and **the card is live again on every host** —
a resurrection with no error and no conflict, because from the merge's point of
view the rule worked.

Therefore:

1. **The tombstone becomes a real BOOL column declared `FieldRole.HIDE_FLAG`.**
   Upstream built this role for exactly this: *"The soft-delete marker. Nothing is
   ever deleted; this is how a row leaves the default view. At most one per
   schema."* (`_policy.py:68-70`). It **requires** a real column — `FieldPolicy`
   refuses `HIDE_FLAG` unless `kind=BOOL` **and** `merge=LAST_WRITER_WINS`
   (`_policy.py:192-206`), the latter because MAX *"would make a hide permanent,
   turning the soft delete into the hard one this store exists to prevent."*
2. **`completed_at` gets its own column.**

The residue of `_log_meta` (`deleted_by`, `completed_by`, and anything else agents
put there) continues to ride in the document under LWW. Promotion is per-fact and
reasoned, never wholesale.

## THE COST — stated here, not buried

**Two agents editing DIFFERENT scalar fields of the SAME card concurrently WILL
clobber each other.** The later HLC wins the entire document, so an edit to `note`
on host A and an edit to `priority` on host B do not merge — one of them is simply
not there afterwards. No error is raised. This is the direct, accepted price of D1.

It is accepted deliberately, and the reasoning is a comparison of failure modes,
not a claim that the cost is small:

- Per-field columns lose ~22 keys **silently and permanently**, on every card,
  forever, including `parked` — where the loss actively causes the board to cancel
  work nobody abandoned.
- Whole-document LWW loses **one concurrent edit**, on the cards that were being
  edited at the same moment from two hosts, and the losing value **remains in the
  append-only oplog** (`_merge.py:11-18`: *"the value it did not pick is still in
  the oplog … 'losing' a merge is not data loss — it is a view"*).

Silence is the failure mode this store cannot afford. Three board wipes are why
ADR-0016 exists.

**The escape hatch, and its limit.** When a specific field turns out to be
genuinely contended, promote *that field* to its own column, with a stated reason,
one at a time — exactly as D3 does for the tombstone and `completed_at`. **Never
invert the whole read path.** The 22 keys do not stop existing because the schema
grew a few columns, and each promotion is a claim about one field that can be
argued on its own evidence.

## Naming — a column name is a published contract

The operator directed this section (Telegram, 2026-08-14):

> 「んー、一度落ち着いて見直して、どんな名前が適しているか後で見た時に初見でわかるか、
> 誰からも明らかか、という目線で進めてください」
>
> *(Roughly: calm down and review it once, and proceed from the perspective of —
> what name is appropriate, would someone understand it at first glance when
> looking at it later, is it obvious to anyone?)*

The evidence that prompted it was his own question minutes earlier, on being shown
this design:

> 「カードジェイソン、という 55432 の中の列、ですか？」
>
> *(Roughly: "card-JSON" — is that a column inside 55432 [the Postgres port]?)*

He had to ask what `card_json` is. Per the constitution §3
(`~/.claude/commands/constitution.md:64`): **"if you must explain a name by
restating it as something else, that something else IS the name. Rename rather than
re-explain — the explanation is the bug report."** And `:70`: **name the INTENT,
not the MECHANISM.**

### N1 — `card_json` names the mechanism. Rename to `canonical_card`.

`card_json` says how the value is *encoded*. It says nothing about the one property
a reader must not get wrong: **this is the truth, and the columns beside it are
derived from it.** Every time the field is introduced it is introduced with that
gloss — `_db_payload.py:23` (*"the typed columns are the INDEX; `card_json` is the
TRUTH"*), `_db.py:75-76` (*"The typed columns are the INDEX; `card_json` is the
PAYLOAD"*). The gloss is the bug report.

**Recommended: `canonical_card`.** It answers the question the name is actually
asked ("which representation wins?") in the name itself, and "canonical" is already
this repo's word for precisely that — `_store_canonical_read.py`, "the canonical
store" throughout `_db.py:5-14`. A reader meeting `tasks.canonical_card` beside
`tasks.status` does not need to be told which one is authoritative.

**Runner-up: `card_record`.** Cleaner English, but it invites a second question —
"how does the card *record* differ from the card *row*?" — which is the gloss
returning by another door.

**A correction to the brief.** This section was proposed to me with the supporting
claim that *"the upstream primitive uses `_record` for its own verbatim-payload
concept, so there is prior art on both sides."* **That is not what `_record` is.**
Upstream's `_record` is the **record key**: a `str` used as the upsert conflict
target (`_store.py:405`) and in `WHERE _record = ?` lookups (`_store.py:421`,
`:430`), sitting alongside the schema's own fields as separate columns
(`_codec.py:77-89`). There is no upstream verbatim-payload column, so there is no
prior art on that side, and this ADR does not claim any. Separately, `_record` and
`_hidden` are both in `RESERVED_COLUMNS` (`_policy.py:138-149`) — a schema that
declares either is rejected — so cards could not adopt those spellings even if the
prior art had been real.

### N2 — `log_meta_json` / `_log_meta` is worse. Rename to `lifecycle_stamps` / `lifecycle`.

`meta` is the emptiest word available: it means "data about data", which describes
every column in the table. Introducing it requires the full gloss — *"the container
for the delete tombstone and the completion stamps"* — and that gloss is the name
it should have had. What it holds is **lifecycle facts, each of them a who/when
stamp**: `deleted_at`, `deleted_by`, `completed_at`, `completed_by`.

- Card key `_log_meta` → **`lifecycle`**
- Column `log_meta_json` → **`lifecycle_stamps`**

### N3 — The columns D3 promotes, named on the same test

- The tombstone BOOL → **`is_deleted`**. A boolean named as a predicate reads
  correctly at every call site, and needs no gloss. Note it sits *beside*
  `deleted_at`, not instead of it: the BOOL is the replicated `HIDE_FLAG` that
  reconciliation reads, the timestamp is when. Both are obvious; neither
  substitutes for the other. `_hidden` is unavailable (reserved, `_policy.py:146`),
  and `is_deleted` is the better name here anyway — this store's word for the
  concept is "tombstone/deleted", not "hidden".
- **`completed_at`** promotes unchanged. It already passes.

### N4 — Names this design touches that already pass

`rescore_history` (`_django/handlers/_comment_digest.py:72`), `comment_count`
(`:137`), `last_comment` (`:138`), `first_comment_ts` (`:143`). Each says what it
holds; none needs a gloss. No change proposed.

**`text_preview` is the house standard, and it was earned.** It was deliberately
renamed from `text` so that posting it back as content would read wrong
(`_comment_digest.py:23-26`):

> THE NAME `text_preview` IS LOAD-BEARING, not decoration. It is a TRUNCATED copy.
> If a caller ever posts it back as the comment body it silently destroys the tail
> of that comment. The field is named so that writing such code reads wrong.

The reasoning is recorded on card `cards-board-graph-payload-slim-20260710`
(comment `c_c16b1bb7d9dc`): *"the preview field is `text_preview`, not `text`. …
`text` reads like the full body."* That is the bar the two renames above are
measured against.

## Migration — these are contracts, so alias first, then remove

Constitution §3 (`constitution.md:69`): **"A published contract (CLI verb,
entry-point group, on-disk key) is a MIGRATION, not a rename — alias first, then
remove."**

Both names are published contracts on a live store holding ~3,000 cards. **No
flag-day rename.** Specifically:

- `card_json` is a physical column (`_db_schema_sql.py:93`); it is exported as
  `CARD_JSON_COL` (`_db_payload.py:47`) and *"checked against `PRAGMA table_info`
  — the artifact, not a stamp"*; and it is a member of `TASK_INSERT_COLS`, which is
  **"PUBLIC ON PURPOSE"** and probed as a *symbol* by the S2 read guard
  (`_db_bootstrap.py:76-88`). A rename that lands before every reader is upgraded
  makes that guard reject the database.
- `_log_meta` is an **on-disk key inside every card document**, not a column. It is
  therefore a data migration over ~3,000 rows plus the YAML export rail — strictly
  harder than adding a column, and dual-read is mandatory rather than advisable.

**Sequence, for each name independently:**

1. **Add** the new column / key. Nothing reads it yet.
2. **Dual-write** both. The old name remains authoritative.
3. **Dual-read**, preferring the new and falling back to the old — the same
   prefer-with-fallback shape the `/graph` payload migration used so that merge
   order never mattered (that programme's ledger is on card
   `cards-board-graph-payload-slim-20260710`).
4. **Backfill** the old rows.
5. **Retire** the old name only once no reachable reader needs it.

The indirection that makes step 1-3 cheap already exists: callers go through
`CARD_JSON_COL` rather than the literal.

**Tooling.** Any rename uses `scitex-dev rename-symbols` — dry-run
(`_cli/_rename.py:89`), collision detection (`:160`), cross-reference updates
(`:55`), invertible by swapping old/new. **Never `sed`/`awk`.** `--exclude`
(`_cli/_rename.py:92`) every data directory, and prove zero data files are in the
plan before running: **a sweep cannot tell a code reference from a live identity
string** — and here that risk is concrete, because `card_json` and `_log_meta`
appear as literal text inside card notes and comments in the store itself,
including on the cards that track this work.

## Relationship to ADR-0016

ADR-0016's surviving invariant, quoted verbatim from its amendment (`:165-167`):

> **No code may delete a row because it is absent from another store.**

Declaring a `HIDE_FLAG` tombstone **satisfies** that invariant rather than
straining it. The invariant exists because reconciliation had no sanctioned way to
*express* deletion, so a sync path inferred it from absence — and that inference
turned a 5-row temporary YAML into a replacement for 2,159 live rows
(ADR-0016:150-163). A declared tombstone gives reconciliation an explicit,
replicated way to say "this row is deleted". Deletion becomes a **value that is
present**, never an **absence that is interpreted**. The invariant then holds
trivially: nothing needs to infer deletion from absence, because deletion has a
column of its own.

This also matches upstream's framing of the role — *"Nothing is ever deleted; this
is how a row leaves the default view"* (`_policy.py:68-69`) — which is the same
rule as the operator's 一度書いたものは消えない (*"a written card never
disappears"*), arrived at independently.

## Open questions

These are open. They are not rhetorical, and none of them is resolved by this ADR.

**Q1 — `revision` conflicts with ADR-0016, and this ADR does not resolve it.**
The draft plugin drops `revision` as *"OWNED BY THE PRIMITIVE … the pre-federation
hand-rolled equivalent [which] must not be merged as data"*
(`_store_plugin.py:146-151`). ADR-0016:215-217 says the opposite in as many words:
preserving `revision` across a store copy is necessary because *"it is user-visible
causal state and belongs in the checksummed column set, not treated as backend
bookkeeping."* Both cannot be true. `revision` is a real column
(`_db_schema_sql.py:94`) and upstream reserves the distinct name `_revision`
(`_policy.py:144`), so this is not a spelling collision — it is a disagreement
about whether the value is domain state or bookkeeping. **Flagged, not decided.**

**Q2 — `comments[]` cannot be declared `APPEND` today; the ids do not exist.**
This ADR was drafted on the premise that comment elements already carry the id
`_element_id` requires. **They do not.** Every comment-append site in the package
writes `{author, ts, text}` plus an optional `kind` and no `id`:
`_store_comment.py:63-68`, `_store_lifecycle.py:259-268`, `:376-385`, `:461`,
`_store_reassign.py:119-125`, `:254`, `_store_rescore.py:196-205`,
`_store_wip.py:161`. There is no comment-id constant anywhere in `src/` — the
package mints `u_`, `n_`, `m_` and `dmr_` ids but never a `c_` one, although
`c_`-prefixed ids do appear on older comments in the live store, so `comments[]` is
a **mixed population**. The only id the schema has is
`task_comments.id INTEGER PRIMARY KEY AUTOINCREMENT` (`_db_schema_sql.py:108`),
which upstream names as the specific worst case:

> An autoincrement primary key is worse than no id at all: two hosts each appending
> a comment both mint `id=8`, so replay treats two DIFFERENT elements as the same
> one and DROPS one of them. That is a lost write presenting as successful
> convergence, and every count still looks correct. Mint ids at creation (a random
> token, not a counter).
> — `_merge.py:214-219`

`_element_id` **raises `StoreError`** on an id-less mapping (`_merge.py:223-235`),
so this fails loudly rather than silently — which is the good case. **APPEND remains
the right rule; minting globally-unique comment ids at creation, and backfilling the
existing threads, is a hard prerequisite before it can be declared.**

**Q3 — The JSON / APPEND / UNION path has zero test coverage upstream.** No test in
`scitex-dev` uses `FieldKind.JSON`, `MergeRule.APPEND` or `MergeRule.UNION`, in
either enum or string form, and there is no `test__merge.py`, `test__policy.py` or
`test__apply.py` under `tests/scitex_dev/store/`. (`FieldRole.HIDE_FLAG` *is*
exercised — `tests/scitex_dev/store/conftest.py:46-51` declares a `hidden` BOOL
field in the shared fixture schema — so D3's role is the one part of this design
with upstream coverage.) The single in-tree consumer of `FieldKind.JSON` is
`scitex_dev/status/_ledger.py:92`. **Cards will be writing the first tests for the
path its entire declaration depends on**, and should plan for that rather than
discover it.

**Q4 — Thirteen tables still have no declared semantics.** This ADR declares
`tasks`. The store has fourteen tables — ten in `_db_schema_sql.py` (`tasks`,
`task_comments`, `task_edges`, `task_roles`, `users`, `user_names`,
`inbox_recipients`, `notifications`, `messages`, `schema_meta`) and four in
`_db_dm_schema.py` (`dm_threads`, `dm_thread_member_events`, `dm_messages`,
`dm_receipts`). The DM tables in particular were moved into the store precisely so
the store's protections would cover them (`_db.py:96-102`). Declaring one table is
not declaring the store.

## Note on method

Every citation in this document was opened and read before it was written, and two
did not survive that check — they are recorded as the corrections in **N1** and
**Q2** rather than quietly adjusted, because the brief for this ADR asked for the
contradiction over the smoothing.

**"The installed version" is not a single fact on this host, so naming a version
without naming a venv is unreliable.** Two are present and they answer differently:

| venv | `scitex_dev` | `StorePlugin` |
|---|---|---|
| `/opt/venv-sac` | 0.48.0 | **absent** — `ImportError` on import |
| the repo's `.venv` | 0.49.2 | **present** |

Upstream quotations above were read in `/opt/venv-sac` (0.48.0) unless the path
says `federation/`, which exists only in 0.49.2 and was read there. `FieldKind`,
`FieldRole`, `MergeRule`, `FieldPolicy` and `Schema` are exported by both.

**`provide()` no longer degrades, and this paragraph previously said it did.** At
the time of drafting, `provide()` caught `ImportError` and returned `[]`; that
branch was removed by `446dbcac` on `feat/declare-card-merge-semantics` while this
ADR was being written. The import is now unguarded on purpose: a raising provider
is caught by `discover_store_plugins`, which logs `"Skipping store plugins from
provider %r: it raised."` **with a traceback** (`federation/_discover.py:113-122`,
`exc_info=True`), serving that function's stated intent — a broken leaf *"must not
stop every other leaf's store from resolving — but it must not pass unnoticed
either"* (`federation/_discover.py:96-99`). Returning `[]` suppressed exactly that
warning, making a dead plugin indistinguishable from a healthy leaf that declares
nothing. It was choosing the silent branch while citing the loud branch's property.

**Line numbers into `_store_plugin.py` index `256899c1`**, the state of
`feat/declare-card-merge-semantics` when this ADR was drafted. That file does not
exist on `develop` at all, and `446dbcac` has since moved the entries this document
cites — `card_json` to `:201-209` and `revision` to `:210-215`. The quoted text is
unchanged; only its position is. Re-resolve by symbol, not by line, once that
branch merges.

If any quotation above ever says something other than what is attributed to it,
this ADR is wrong and should be revised rather than defended.
