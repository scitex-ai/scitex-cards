# The card store, and the multi-host question — an overview for a first-time reader

**Written for external audit, 2026-07-30.** No prior knowledge of this project
is assumed. Every number below states how it was obtained so you can re-take it
yourself; where something is *not* verified, that is said in place rather than
left to inference.

Terminology, once: a **card** is a task record. The **store** is the single
SQLite database holding all of them. The **board** is the web UI over it. The
**fleet** is roughly 90 automated agents that read and write the store
concurrently on one laptop.

---

## 1. What exists today

One SQLite file. That is the whole storage design.

```
~/.scitex/cards/cards.db      45.5 MB, 2,856 card rows      (measured 2026-07-30)
```

Its location is configuration, not code: `$SCITEX_CARDS_DB` names the database,
and that path **is** the store's identity. Absent that variable it defaults
under `$HOME`. There is no second store, and no way to select a different
backend — see §4, because that is a deliberate decision with a violent history.

### How a write happens

This is the single most important fact for an auditor to hold, because most of
the problems below descend from it.

> `src/scitex_cards/_store_canonical_read.py:31` — *"Read the whole store from
> SQLite for a read-modify-write. FAILS LOUD."*

Changing one field on one card **reads all 2,856 cards, modifies one, and
writes all 2,856 back.** Measured cost of that cycle on the current store:
**median 368 ms** (5 trials, min 338, max 387 — includes fsync; measured by the
sac maintainer on this host).

### How the board reads

The board polls one endpoint, `/graph`, which serialises every card including
every comment and note.

| endpoint | wall time | bytes |
|---|---|---|
| page shells (`/`, `/chat/`) | 12–96 ms | — |
| `/dm/threads` | 7 ms | 27,975 |
| `/timeline` | 0.68 s | 78,974 |
| **`/graph`** | **1.0–2.6 s** | **19,838,856** |

Of that 19.8 MB, **comments are 8,450,544 bytes — 43%** (measured across 2,856
cards, 1,765 of which have at least one comment).

Method for the table: `curl -w '%{time_total} %{size_download}'` against
`127.0.0.1:8051`, repeated. Method for the 43%: sum `len(json.dumps(...))` over
the `comments` field of every row's `card_json`.

### Compression is on, and it is not free

GZip middleware wraps every response.

```
plain   1.18 / 0.67 / 0.96 s      19,838,856 B
gzip    2.56 / 2.45 / 2.81 s       6,830,833 B on the wire
```

Interleaved, 3 rounds, alternating so machine load lands on both sides. Two
conclusions an auditor should check independently:

1. **Compression is 2.9×, not 10×.** `settings.py` still claims "roughly 10x";
   that estimate predates the payload tripling and now over-credits gzip.
2. **Compressing costs ~1.7 s of server CPU per request**, consistently, with no
   overlap between the distributions. On a single-worker server that CPU also
   blocks concurrent requests.

Do **not** read (2) as "disable gzip". Over the internet, 6.8 MB versus 19.8 MB
matters enormously; on loopback the 1.7 s is pure loss. The tradeoff is
client-dependent, which is an argument for making the payload small rather than
for adding a switch.

---

## 2. The three problems, stated separately

They are usually conflated. They are not the same problem and they do not have
the same fix.

### 2.1 Concurrent writes can lose cards wholesale

Because every write rewrites the whole document, two writers racing means the
later one's *entire view* replaces everything — including cards the other added
in between. Not a lost field: **lost cards**.

This is not hypothetical. See §4.

### 2.2 The board is slow because it ships everything every time

§1's 19.8 MB. Same root cause as 2.1 in a different direction: all of it, every
time.

### 2.3 There is no way for another machine to reach the store

Only processes on the owning machine can open the file. Consequences observed
2026-07-30:

- The public board at `scitex.ai/apps/cards/` renders, authenticates, and is
  **empty**, returning HTTP 500. Its container mounts `/app/.scitex` as an
  *empty named Docker volume* — there was never a database at that path.
- A containerised agent looks for peer configuration under its own `$HOME`
  (`/home/agent/...`) while the real configuration is under the operator's
  (`/home/ywatanabe/...`), so peer lookups fail.

Note the second is **not** a general "containers can't see the store": measured
by device+inode, `/home/agent/.scitex/cards` **is** bind-mounted and shared,
while `/home/agent/.scitex/agent-container` is not. The mount is partial. I
initially reported these two symptoms as one root cause and that was wrong.

---

## 3. Why the obvious fixes are wrong

An auditor will reach for these; here is why each is already ruled out, with the
reason rather than an assertion.

| Candidate | Verdict | Why |
|---|---|---|
| Share the SQLite file over a network filesystem | **Unsafe** | The store runs in WAL mode (`_db.py:299` sets `PRAGMA journal_mode=WAL`; documented at `_db.py:20`). SQLite's own documentation states WAL requires shared memory and does not work over a network filesystem, and advisory locking is unreliable on NFS/SMB. Corruption is silent. **See the caveat below — this constraint is nowhere stated in this codebase.** |
| Replicate the file between hosts | **Contradicts the package** | `db export` is documented in-tree as *"a backup, never a source"*. Bidirectional merge on an append-only store with tombstones is a genuine correctness problem, not a detail. |
| Last-write-wins by timestamp | **Loses cards** | Only safe with row-level writes. Under §1's whole-document write it means the newest writer's whole view wins, which is the §4 incident mechanism. |
| Just flip a config default to a second backend | **Explicitly rejected** | §4. |

**Caveat an auditor should weigh, because it undercuts the first row's
provenance.** The whole multi-host design rests on "the file cannot be shared
over a network filesystem", and **that constraint is not documented anywhere in
this source tree.** A word-boundary search for `NFS` and `SMB` across
`src/scitex_cards/` returns zero hits. The existing design document
(`remote-hub-backend.md`) cites `_db.py:85-119` for it; that range does not
contain such a statement — it discusses a `threads.json` sidecar. I repeated
that citation in an earlier draft of this document before checking it, which is
the same failure the document reports elsewhere.

The technical claim is still correct — it is upstream SQLite behaviour, and WAL
mode is genuinely enabled here — but it is load-bearing, unwritten, and
therefore one refactor away from being forgotten. Writing it down next to
`journal_mode=WAL` would be a cheap, high-value fix.

---

## 4. The history that constrains the design

An auditor cannot evaluate the constraints without this. Three board wipes,
2026-07-19 to 07-21:

- A **5-row temporary YAML file replaced 2,159 live rows.** Reconcile means
  "make identical", and identical includes deleting rows absent from whatever
  document is treated as the source.
- A separate sequence put **2,138 cards** at risk the same way.

Every one of those incidents required a **second store to be
authoritative-ish**. The operator's ruling on 2026-07-20 was therefore to delete
backend selection outright rather than default it off:

> 「例外を用意しないでください。甘くせずにハードに切り替えてください。曖昧に
> すると バグが残ります。他のエージェントも迷ってしまいます。唯一の方法だけ
> ソースコードに含めてください。」
>
> *Provide no exceptions. Switch hard. Ambiguity leaves bugs. Other agents get
> confused. Carry exactly one way in the source code.*

`src/scitex_cards/_store_backend.py` opens with **"SQLite IS the store. There is
no other backend and no way to select one."** Its reasoning against merely
changing a default is worth quoting for audit, because it is the crux of §5:

> A default is an opinion about the common case; it leaves the other world
> supported, reachable, and reviewed by nobody. […] the fleet's agents each
> resolve their own environment — so "which world am I in?" would be a live
> question with a different answer per process.

Two guards were added as a result, and both are load-bearing:

- **Refuse to read a missing database** rather than treat it as empty — because
  an empty read gets written back as the whole store
  (`_store_canonical_read.py:68`). This is the guard currently making the public
  board return 500 instead of erasing the board. It is working correctly; the
  500 is the bug *reporting itself*. The same module removed a `missing_ok`
  parameter deliberately, and calls that removal "the safety property rather
  than a tidy-up".
- **Refuse a write that shrinks the row set** — `_assert_no_shrink`
  (`_store_backend.py:69`), whose stated invariant is *"a written card never
  disappears. Not 'not too many' — NONE"* (`:78-79`, attributed to the operator
  ruling after the 2026-07-21 third wipe). No ratio and no threshold.

---

## 5. The multi-host options

### 5.1 What was proposed and withdrawn during this session

Recorded because the process matters to an audit, not to pad the document.

- *Per-host stores, unioned on read.* Withdrawn: the operator's design is one
  master, and merging reintroduces §3's merge problem.
- *A hand-written HTTP store server.* Withdrawn: a proven database already is
  one.
- *SQLite by default, PostgreSQL for shared deployments.* **This is the "two
  worlds" §4 deleted.** I proposed it without having read
  `_store_backend.py`'s docstring, inferring a "backend seam" from a filename.
  That was my error and it is the single most important process failure in this
  document.
- *A "one-way door" migration* — traverse once, after which the new backend is
  the only one for that deployment. Open question: the operator's ruling says
  **ソースコード** (source code). If it constrains the *source*, a
  per-deployment choice is insufficient, because both implementations still
  ship. That reading has not been settled.

### 5.2 What the repository already decided

`docs/design/remote-hub-backend.md` — *"one cards.db, every host"* — already
specifies the shape: **one process owns the file and fronts it with an
authenticated HTTP RPC service; remote agents keep their identical local tool
surface and only the storage verbs beneath swap to an HTTP client.** It reaches
§3's conclusion by the same route (network-FS sharing ruled out by
`_db.py:85-119`).

It is deliberate about where the seam goes — the **locked verb** level, not the
row level — and explicit about which existing endpoints must *not* be reused
(eight board handlers that bypass the lock, have no auth, and honour a
client-supplied store parameter).

**This document existed before the options in §5.1 were proposed.** Two agents
independently re-derived it. For an auditor the finding is not the architecture,
which is sound — it is that the project's own design record was not consulted
first, twice.

### 5.3 What is not settled

| Question | Status |
|---|---|
| Does the 07-20 ruling constrain the source or only the runtime? | **Open.** Decides whether a migration-based approach is admissible at all. |
| Which machine owns the store? | **Open, but measured.** Loopback service call ≈40 ms median vs the 368 ms write it replaces — ~9× cheaper. Container→NAS connect measured 4.8–11.2 s, so "master on the NAS" and "master on the laptop" are very different proposals and only the second has supporting numbers. |
| Ordering | **Settled.** Row-level writes come first: no new dependency, they fix 2.1 on its own terms, and they are revertible where a storage migration is not. Serialising through one service does **not** by itself fix 2.1 if that service still does whole-document read-modify-write internally — a restart mid-sequence, or two instances, reintroduces it. |

---

## 6. Verified, versus not

The distinction most worth auditing.

**Measured, reproducible from the methods above:** store size and row count;
the endpoint timing table; comments at 43% of the payload; gzip's 2.9× ratio
and its ~1.7 s cost; whole-document write at ~368 ms; loopback RPC ≈40 ms;
container→NAS connect at seconds; the partial bind-mount by device+inode; the
empty named volume in the public deployment.

**Asserted from source, not executed:** the incident narratives in §4 are read
from in-tree docstrings and card history, not reproduced. An auditor wanting
independent confirmation should read `_store_backend.py`, `_store.py`'s guards,
and the 2026-07-19/21 card record.

**Not verified at all:** that any proposed multi-host design performs acceptably
for ~90 concurrent agents. No load test exists. The 40 ms figure is a
single-request measurement of a *different* service (the fleet's existing host
daemon, including its auth work) and is an existence proof that loopback is
cheap, not a capacity result.

**Known-wrong claims made and corrected during this session**, listed so an
auditor can calibrate the rest: the payload transfer size was overstated 3×
(6.8 MB, not 19.8 MB, on the wire); the two container symptoms in §2.3 were
reported as one root cause; a "backend seam" was claimed to exist when the
source says the opposite; a tool was reported as faulty when the fault was a
mis-typed flag.

---

## 7. Where to look in the code

| Concern | Path |
|---|---|
| Store resolution, path is identity | `src/scitex_cards/_paths.py` |
| Whole-document read-modify-write, and the fail-loud reader | `src/scitex_cards/_store_canonical_read.py` (:31 the read, :68 the missing-db refusal) |
| Shrink refusal — "a written card never disappears" | `src/scitex_cards/_store_backend.py:69` (invariant at :78-79) |
| Why there is exactly one backend | `src/scitex_cards/_store_backend.py` (module docstring) |
| WAL is enabled (the constraint behind the design) | `src/scitex_cards/_db.py:299`, documented at `:20`. The *consequence* for network filesystems is **not** written down anywhere in this tree — see §3. |
| The board payload | `src/scitex_cards/_django/handlers/graph.py` |
| Comment summary scalars (payload reduction, in progress) | `src/scitex_cards/_django/handlers/_comment_digest.py` |
| Multi-host RPC design | `docs/design/remote-hub-backend.md` |
| Store as single source of truth | `docs/adr/0010-cards-db-single-source-of-truth.md` |
