# SQLite → PostgreSQL: the hazards that actually bit us

Written 2026-08-02 by scitex-cards, from the scitex-todo → scitex-cards store
migration. Every item here is a **measured incident**, not a predicted risk, and
each names the observation that exposed it.

The organising fact: **almost every one of these failed by SUCCEEDING.** A
crash would have been cheap. What these have in common is that the wrong thing
returned a value, wrote a row, or printed green — so the failure was invisible
until someone asked a question nobody had thought to ask.

Read this before porting another package. The list is ordered by how much time
each one cost, worst first.

---

## 1. `Path("postgresql://host/db")` does not fail — it collapses

```python
Path("postgresql://scitex_cards@127.0.0.1:5432/scitex_cards")
# -> PosixPath("postgresql:/scitex_cards@127.0.0.1:5432/scitex_cards")
```

That is a **relative** path. Everything derived from it then resolves against
the process's current directory.

Hit **twice**, both times as a silent success:

- `enqueue(store=<DSN>)` **returned a notification id** and created a phantom
  SQLite store at `<CWD>/postgresql:/…/runtime/todo.db`. Nothing raised, so the
  fail-soft caller logged nothing, and the notification was unreachable because
  nobody polls a directory named after a DSN.
- An older shipped version (0.25.0, still in a container image) resolved a DSN
  the same way, opened an **empty** SQLite file at the mangled path, and
  **served 0 cards while reporting healthy**. 35 agent specs used that image.

**Countermeasure.** A DSN must never reach `Path()`. Either refuse loudly, or —
where the caller genuinely wants a local directory regardless of backend —
resolve to the local root explicitly. Decide which, per function, and say so in
the docstring. The bug is the *implicit* third option.

## 2. "Standard SQL" is not portable SQL

`IS NOT DISTINCT FROM` is standard, and means exactly what SQLite's `IS` means.
SQLite only accepts that spelling from **3.39** (2022-06).

| environment | SQLite | result |
|---|---|---|
| host (board, notifyd) | 3.37.2 | every enqueue raised |
| containers (agents) | 3.45.1 | worked fine |
| CI | new | green, always |

Host-side notification delivery was dead for **~36 hours**. Agent-to-agent DMs
kept working, which made it look environment-shaped rather than code-shaped and
sent the investigation in the wrong direction.

**Countermeasure.** A behavioural test pins the local library version, not the
SQL — it is green on any modern SQLite no matter which spelling the source uses.
Test the **artifact**: extract the statements actually passed to `execute()`
(via AST) and assert on them, so the guard fails on a new library too. Do not
substring-scan the file once its own docstring discusses the bad spelling.

## 3. `BEGIN IMMEDIATE` has no drop-in PostgreSQL equivalent

SQLite's `BEGIN IMMEDIATE` takes the write lock up front and **blocks**.

The tempting port is `SERIALIZABLE`. It is not equivalent: it **aborts** on
conflict instead of blocking. Same words ("stronger isolation"), opposite
behaviour under contention, and nothing visibly changes until load arrives.

The behaviour-preserving port is `BEGIN` + `pg_advisory_xact_lock(key)`.

**Countermeasure.** When porting a transaction, port the *contract* (does it
block or does it fail?), not the isolation-level name.

## 4. A fail-soft handler converts a hard error into silence

`dispatch_to_inbox` is deliberately fail-soft: the message is already committed,
so a failed enqueue should cost a push, not a message. Correct reasoning — and
it swallowed hazard #2 for a day and a half. DMs landed in the store, no
notification row was written, the board reported success, nothing was red.

**Countermeasure.** A fail-soft wrapper protects the caller from the subsystem
*and protects the failure from you*. When something is committed but never
delivered, find the fail-soft `except` on that path and read what it logged
**before** theorising about divergent code paths. The traceback was in stderr
the whole time.

## 5. PostgreSQL cannot parse SQLite's `CREATE TRIGGER`

SQLite takes an inline trigger body. PostgreSQL needs a `plpgsql` FUNCTION plus
a trigger that calls it, and has no `IF NOT EXISTS` for triggers.

Skipping what a backend cannot run is the tempting move and is silently wrong:
the tables come up, the database looks healthy, and an append-only table
**quietly accepts DELETE** because its guard was never installed.

**Countermeasure.** Substitute per dialect, and **raise** on an unrecognised
trigger name. Turn "someone added a guard and did not port it" into a failure at
schema-creation time, where it is cheap.

## 6. Return a COUNT from DDL execution

`executescript` returns a cursor nobody reads, so a script that silently ran
**zero** statements looked identical to one that installed nine triggers.

**Countermeasure.** Return how many statements ran and let callers assert it.
"The guards are installed" and "the install call did not raise" are different
claims.

## 7. Store IDENTITY and local STATE DIR are different axes

A server store has **no directory**. But pidfiles, the delivery ledger, reminder
state and sidecars still need a real local directory — just as much as before.

Deriving the local dir from the store identity welded the two together, so
pointing the fleet at PostgreSQL made the whole query side raise before it ever
opened a connection.

**Countermeasure.** Resolve them independently and say so:
- store identity — a path **or** a server URL
- local state dir — always a real directory, whatever the backend

## 8. The doctor asserted the engine instead of resolving it

The health check printed the literal string `"SQLite"` unconditionally. True
when written; a lie from the day a store could be a server. It reported
`exactly one write target: SQLite` while every write went to PostgreSQL — the
one line that looks like it answers "which engine am I on".

**Countermeasure.** Report both rails' engines, name **which config tier** chose
the target (explicit arg / env var / config file / default), and **fail** when
the two rails disagree. "I edited the config and nothing changed" is the most
confusing way resolution fails, because every tier is individually working — the
environment simply outranks the file.

## 9. Migration DDL must live in ONE constant

Already recorded in this repo's own history: *"Whatever v4 added went into
`_SCHEMA_SQL` only … so a v3 file upgraded straight to v5 never received it,
while its stamp said otherwise."* A store that reports the right schema version
and does not have the table.

**Countermeasure.** The fresh-create path and the migration run **byte-identical
statements** from one shared constant. Test that a fresh store and a migrated
store end up the same shape.

## 10. `CREATE OR REPLACE FUNCTION` is not a no-op

It rewrites the `pg_proc` row every time. Under a fleet that re-runs schema
setup on every connection, that is constant churn on a system catalog.

**Countermeasure.** Make schema setup detect "already current" and skip. Watch
`pg_proc.xmin` as the churn detector, and keep a deliberately-tolerated count as
a positive control so "no churn" cannot be confused with "not measuring".

## 11. Published is not installable

PyPI's JSON API leads the **simple index** — which is what `pip` actually
resolves from — by minutes. `pip index versions` reported the new release while
`pip download` still refused it.

**Countermeasure.** Gate deployment on a real `pip download` resolve, then open
the wheel and read the code. Verify installs by **content**, not metadata.
Related: asking for two packages in one `pip install` when only one has
published installs **neither**, leaving both version strings at the old value —
so a version check agrees with itself and is wrong.

## 12. A broken probe looks exactly like a missing table

Counting rows through the connection wrapper with `row[0]` raised
`KeyError: 0` for every table. The obvious reading — "these tables do not
exist" — was wrong; the wrapper returns dict-like rows and the probe was broken.

**Countermeasure.** A negative result needs a positive control. Before
concluding something is absent, prove the instrument can detect something you
know is present.

---

## The shape to take away

Nine of these twelve produced **no error at all**. The recurring pattern is a
value that looks like success: a returned id, a created file, a green check, a
version string that matches.

So the durable lesson is not any single incompatibility — those are listed above
and are finite. It is that a backend port moves code into an environment where
the old assumptions are *silently* untrue, and the only reliable defence is to
verify the **artifact and the effect**, never the report:

- read the SQL that was executed, not the test result
- read the file that was installed, not the version metadata
- read the inode the process opened, not the path you built
- read what the fail-soft handler logged, not what the caller returned
