# Store identity is a UUID, not a path

Status: IMPLEMENTED (2026-07-29). `scitex_cards._store_uuid` exists, the guard
is uuid-first, and all 16 `xfail(strict=True)` markers are deleted.
Card: `scitex-cards-resolver-never-default-yaml-20260727` (P0).
Contract: accepted verbatim by scitex-dev 2026-07-28, adopted ecosystem-wide.

This document specifies the change. The tests in
`tests/scitex_cards/test__store_uuid_identity_contract.py` and
`tests/scitex_cards/test__store_uuid_guard_integration.py` are the executable
half of it: they were marked `xfail(strict=True)` against an API that did not
exist, so the implementation PR removed the markers rather than writing new
assertions. Anything asserted there and not written here is a bug in this
document.

What landed, against what this document specifies:

| section | landed as |
| --- | --- |
| §3 identity, §5 decision table | `scitex_cards._store_uuid` |
| §7 uuid-first guard | `_dual_write._db_mirrors_this_store` |
| §8 realpath fallback removed | `_dual_write._same_file` |
| §9 migration verb | `scitex-cards store adopt-uuid` |
| §11 exposure | `_store.resolve_store()`, `_health_store._check_store_identity_agrees` |

Two things this document did not anticipate, both recorded in §14.

---

## 1. The defect

Store identity is a PATH. `schema_meta.store_path` records the resolved path of
the store a database belongs to; `_dual_write._same_file` decides whether two
paths name the same store.

scitex-storage's formulation is the whole of it:

> a path is not identity when more than one view or code path can produce it.

Measured on the HOST on 2026-07-28 (the only place it reproduces):

```
stamped store_path : /home/agent/.scitex/cards/cards.db
exists on HOST     : False
host resolves      : /home/ywatanabe/.scitex/cards/cards.db
                     -> /home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db
```

ONE bind-mounted file, two names. `_same_file` compares `st_dev`/`st_ino` when
both paths exist, and falls back to a realpath STRING compare when one does not.
From the host `/home/agent/...` cannot be stat'd, the strings never match, and
the board is refused its own database: `GET /tasks` returns HTTP 500,
"REFUSING TO READ ... stamped for a DIFFERENT store".

`_same_file`'s own docstring records the same false-negative class from
2026-07-20. The inode check was added then; the string fallback it kept is
reachable in exactly the cross-namespace case it was meant to solve.

## 2. Three repairs already tried, and why each is wrong

Recorded because each one looks reasonable until you read what refused it.

1. **Loosen the shared predicate in `_dual_write._db_mirrors_this_store`.**
   Broke `test_a_write_to_a_foreign_store_does_not_clobber_another_stores_mirror`
   CORRECTLY: a write to an unidentifiable store can replace another board's
   rows.

2. **Confine the relaxation to the READ door.** `_store._read_canonical_db_or_raise`
   exists to prevent exactly this. On 2026-07-19 the write door refused a foreign
   store correctly all day while the read door returned its rows, and a packaged
   fixture was read AS THE BOARD for hours. Do not split this into a lenient read
   and a strict write.

3. **Re-stamp the live store to the host path.** The 500 cleared and the board
   returned `{"tasks": []}` -- an EMPTY BOARD, the exact shape that destroyed
   2,138 cards on 2026-07-19 (a failed read promoted to an authoritative empty
   document that a read-modify-write writes back). Reverted within a minute.

**The 500 is the guard working.** Every "fix" above is an attempt to silence a
defence. The defect is one level down: identity is a path.

## 3. The identity

A new `schema_meta` key:

| key | value |
| --- | --- |
| `store_uuid` | a bare lowercase uuid4, `8-4-4-4-12`, e.g. `3f2b8c1e-...` |

Rules, all of them load-bearing:

- **Compared by EXACT STRING EQUALITY.** `db_uuid == expected`. No
  `uuid.UUID()` parse, no case folding, no stripping of `{}` or a `urn:uuid:`
  prefix. A comparison that normalises is a comparison with a second spelling,
  which is the class of bug being removed.
- **Opaque.** Nothing reads meaning out of it. It is not parsed for a version
  nibble, not sorted, not used as a sort key or a filename.
- **NEVER derived from a path, a hostname, or a timestamp.** This clause must
  live as a COMMENT IN THE CODE beside `mint_store_uuid`, not only here
  (scitex-dev's requirement). A deterministic hash of the path is the
  view-dependence this change removes, reintroduced by someone tidying up.
- **Minted once, never rewritten.** `stamp_store_uuid` is idempotent for the
  same value and REFUSES to overwrite a different one; re-identifying a store is
  a deliberate operator action with its own command, not a write side effect.
- Form is validated at the STAMP boundary (reject anything not matching
  `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`), so the
  corpus stays canonical while the comparison stays dumb. `mint_store_uuid`
  produces a uuid4 specifically; the boundary validator deliberately accepts any
  conforming lowercase `8-4-4-4-12`, so an identity minted by another scitex
  package is never rejected over a version nibble.

## 4. The expectation, and where it may come from

The database carries an identity. The caller may carry an EXPECTATION of which
identity it should find. The expectation is INJECTED, never derived from the
artifact being checked:

- an explicit argument from a caller that knows which store it wants; else
- `$SCITEX_CARDS_STORE_UUID`; else
- absent.

scitex-dev's host registry will carry the expected `store_uuid` per service
alongside its endpoint. That puts the trust anchor in human-controlled config
rather than in the thing being checked, which is the only place it can live.

The expectation is NEVER read out of the database, and never computed from the
path -- either would make the check circular and re-introduce view-dependence.

## 5. The decision table

`identity_verdict(db_uuid, expected) -> ACCEPT | ADOPT | REFUSE`, a PURE
function of two optional strings. It takes no path, no connection, and no
environment: everything the verdict depends on is in its two arguments.

| `db_uuid` | `expected` | verdict | why |
| --- | --- | --- | --- |
| `None` | `None` | `ADOPT` | legacy/fresh database, nothing claims it |
| `None` | `X` | `REFUSE` | a database that never demonstrated it is `X` does not satisfy a statement that it must be `X` (section 5.1) |
| `X` | `None` | `ACCEPT` | absence of an expectation is not evidence of a foreign store (contract rule 4b) |
| `X` | `X` | `ACCEPT` | same store, whatever it is called here |
| `X` | `Y` | `REFUSE` | a declared expectation, contradicted |

`REFUSE` is reached in exactly the two rows where an expectation was DECLARED
and the database did not meet it. Where no expectation was declared the guard
never refuses: a guard that refuses in the cases it cannot judge denies service
to the process that was explicitly pointed at the store, which is what took the
board down. Row 1 therefore stays `ADOPT`, because today that is every database
in existence, including the live board.

### 5.1 Why row 2 REFUSES

Recorded as an open question in the first draft of this document and DECIDED
before merge. The reason is not symmetry with row 5; it is what `ADOPT` actually
does on this row.

Under `ADOPT` the guard does not merely proceed, it MINTS -- it writes the
expected uuid into a database that never demonstrated it deserved that identity.
A misresolution then becomes permanent, SELF-CERTIFYING identity that every later
check agrees with, including the checks built to catch it. Refusing is
recoverable; adopting manufactures the evidence.

Measured corroboration: on 2026-07-28 a board served HTTP 200 with ZERO cards
while the store held 2647. Row 1 (`None`/`None`) must stay `ADOPT` for legacy
databases, so Row 2 = `REFUSE` is the ONLY rule that closes
misresolution-to-an-empty-database.

Note the asymmetry that keeps this consistent with contract rule 4b: 4b is about
the CALLER lacking an expectation (accept -- absence of an expectation is not
evidence of a foreign store). Row 2 is about the DATABASE lacking an identity
while the caller HAS named one, which is a different question and gets a
different answer.

The cost of this row is that a store can no longer be bound by a write once an
expectation is configured, so the migration must go through `adopt-uuid`. That
is not a hazard on its own -- it is a hazard only if the two steps are done in
the wrong order, which constraint 1 of section 9 forbids outright.

## 6. Identity and resolution stay TWO SEPARATE RULES

This is scitex-dev's first required addition and the easiest thing to get
wrong.

- **Identity**: "no expectation" is not evidence of a foreign store (row 3
  above).
- **Resolution**: never auto-create or auto-adopt a store at an ambient
  default (`_paths.refuse_ambient_store_creation`).

Merge them and row 3 silently becomes "use whatever you were pointed at".
scitex-dev hit this on 2026-07-28: their `add_task` resolved to an ambient
default and tried to manufacture a board, and this package's guard refused it
correctly.

Therefore: **the UUID path must NOT bypass `refuse_ambient_store_creation`.**
The two checks run independently and neither is an input to the other. Pinned by
`test_a_matching_identity_does_not_bypass_the_ambient_store_creation_guard`,
which is NOT xfail -- it passes today and must keep passing.

## 7. Where the identity is consulted

`_dual_write._db_mirrors_this_store(db_path, store_path)` becomes uuid-first:

```
1. database does not exist            -> True   (unchanged: nothing to clobber)
2. read schema_meta.store_uuid        (MAY BE ABSENT -- absent is a legal input)
3. identity_verdict(store_uuid, expected_store_uuid())
     ACCEPT -> True.           THE PATH IS NOT CONSULTED AT ALL.
     REFUSE -> False, logged.  THE PATH IS NOT CONSULTED AT ALL.
     ADOPT  -> LEGACY: today's store_path comparison, with the realpath
               string fallback REMOVED (section 8).
```

The verdict is consulted UNCONDITIONALLY, on an absent identity as well as a
present one. It has to be: row 2 is an absent identity, and a guard that only
asked when the answer was already stamped could never reach it. The legacy path
comparison is therefore not the "no uuid" branch -- it is the `ADOPT` branch,
which after section 5.1 means row 1 alone: no identity AND no expectation. That
is exactly where a path compare is still the best available evidence, and
nowhere else.

Step 3 is the whole repair. Once the live database carries a `store_uuid`, the
host and the container reach the same verdict because neither one looks at a
path.

Both doors keep calling the SAME predicate. The read door
(`_store._read_canonical_db_or_raise`) and the write door
(`_store_backend.write_doc_to_db`) are not split, not parameterised, and do not
get a lenient variant. That asymmetry is what 2026-07-19 was made of.

## 8. Removing the realpath string fallback

scitex-dev's framing, endorsed:

> a fallback that triggers only in the case it cannot judge is worse than no
> fallback.

It fires precisely when the stamped path is unstat-able, i.e. exactly when you
are across a boundary -- and it answers a question it cannot answer, with the
most destructive of the two possible answers.

After removal, the legacy path branch (step 4) resolves as:

| stamped `store_path` | verdict |
| --- | --- |
| absent | adoptable -> True |
| both paths stat-able, same inode | True |
| both paths stat-able, different inode | False (genuinely different store) |
| either path not stat-able | CANNOT TELL -> False, with an honest message |

CANNOT TELL refuses. That is NOT a tightening: today the string compare fails in
that same case and the caller is refused anyway. What changes is the message --
"ownership of this database CANNOT BE DETERMINED from a path in this mount
namespace; bind it to an identity" instead of the false claim "stamped for a
DIFFERENT store".

The escape from CANNOT TELL is not a looser comparison. It is binding the store
to an identity, once, deliberately (section 9).

### Interaction with PR #598

PR #598 (`fix/dm-label`, open) makes an unresolvable stamp mean "cannot tell,
so proceed" -- CANNOT TELL -> True. It is explicitly a mitigation, and it is the
only thing in the tree that can bring the board back before this design lands.

If #598 merges first, the implementation PR MUST revert that relaxation as part
of landing the uuid, and only AFTER the live store carries a `store_uuid`.
Reverting it earlier re-opens the outage; leaving it in place afterwards leaves
a permanent "proceed when unsure" branch behind a guard whose entire value is
that it does not do that.

## 9. Migration: binding the live store, once

A new CLI verb, `scitex-cards store adopt-uuid [--uuid X]`, is the RECOMMENDED
path. It mints (or takes) an identity, stamps it into `schema_meta`, prints it,
and does nothing else.

What it must NOT do, and why this is the part worth reading:

- It does NOT touch `store_path`. Re-stamping `store_path` was repair attempt 3
  and it produced an EMPTY BOARD.
- It does NOT touch the `tasks` table, the `messages` table, or any other row.
  Pinned by `test_binding_an_identity_leaves_every_card_row_untouched`.
- It does NOT change what any resolver resolves. `resolve_db_path` reads
  `$SCITEX_CARDS_DB`; nothing in the resolution chain reads `store_uuid`.

The drive-by alternative -- the first write to an unstamped database claims it --
survives only in the row 1 shape: no expectation was declared, so the write mints
a FRESH identity. It may NEVER stamp a CONFIGURED expectation, because that is
exactly the mint row 2 now refuses (section 5.1). An explicit one-time bind is
auditable; a bind that happens as a side effect of whichever process wrote first
is not.

### Sequencing constraints

1. **Stamp the store first. Declare the expectation second. Never ship an
   environment naming an expected store uuid before that store carries one.**

   This is the sequencing that stops row 2 = `REFUSE` from stranding an operator
   mid-migration. Row 2 is reachable only by a database with no identity facing a
   caller that names one, so it is unreachable for any store that was stamped
   BEFORE its expectation was published. An operator is stranded only by
   performing these two steps in the wrong order, never by the rule itself.

   The order below obeys constraint 1: `adopt-uuid` (step 2) precedes the
   registry entry (step 3), and `$SCITEX_CARDS_STORE_UUID` is set in no
   environment until the uuid it names already exists in the database.

Order of operations for the live board:

1. Land the implementation PR (guarded, reviewed, tests green).
2. Run `scitex-cards store adopt-uuid` against the live database. Verify the
   card count before and after (2646 at the time of writing).
3. Record the printed uuid in scitex-dev's host registry next to the board's
   endpoint.
4. Only then revert #598's CANNOT TELL -> True relaxation.

## 10. What this design CANNOT decide

### A byte copy of a store

`cp cards.db elsewhere.db` produces a second file carrying the SAME
`store_uuid`. The file cannot self-distinguish, because everything it could
distinguish itself by is inside the thing being copied. Under this design both
copies are ACCEPTed.

This is unsolvable from the artifact and the design says so rather than
pretending otherwise. It is closed only by an INJECTED expectation that pairs
the uuid with something outside the file -- scitex-dev's registry carrying
`(expected_store_uuid, endpoint)` per service. Even that detects "you are
talking to the wrong endpoint", not "this copy is the stale one".

This package's half of that work is contract point 8: expose the database's
`store_uuid` as a first-class read so the registry can be populated without
archaeology. See section 11.

Pinned honestly by
`test_a_byte_copy_carries_the_same_identity_and_cannot_self_distinguish`, which
asserts the copy is INDISTINGUISHABLE -- so that nobody later "fixes" it by
mixing a path or an inode back into the identity, which would reintroduce
exactly the view-dependence this change removes.

### RESOLVED: an unstamped database when an expectation IS configured

This was the one open question in the first draft of this document. It is
decided: row 2 `REFUSE`s, for the reason set out in section 5.1, and the
sequencing that makes it safe is constraint 1 of section 9. Nothing in this
section is open.

The contract's "a legacy UNSTAMPED database must stay ADOPTABLE" clause is
satisfied by row 1, which is where every legacy database sits -- a legacy
deployment has no expectation to declare, because there is as yet no uuid to
name.

## 11. Exposure (contract point 8)

- `_store.resolve_store()` gains `"store_uuid"` -- the identity read from the
  resolved database, or `None` when unstamped/absent. Machine-readable, so the
  registry can be populated from it directly.
- `_health.health()`'s `store_identity` check NAMES the uuid in its `detail`.
  `_run_check` coerces a check's result to `{name, ok, detail, hint}`, so the
  human-facing surface is the detail string; the machine-readable read is
  `resolve_store`.
- The `scitex-cards resolve-store` and `scitex-cards health` CLI verbs inherit
  both without further work.

## 12. Non-goals for the implementation PR

- No change to `refuse_ambient_store_creation` (section 6).
- No lenient read variant (section 2, repair 2).
- No re-stamping of `store_path` anywhere (section 2, repair 3).
- No write to the live store from any test, ever. Tests use an explicit tmp
  store; the default is the fleet board with 2646 cards.

## 13. Tests, and the one that lied

Every test uses an EXPLICIT tmp store. None depends on ambient filesystem
layout -- paths that must not resolve are spelled
`/proc/self/no-such-mount-namespace/...`, which cannot exist in any environment.

That rule is not decorative. A test written earlier on 2026-07-28 stamped the
real `/home/agent/...` path to exercise the unresolvable branch. Inside the
container that path EXISTS, so the branch never ran and the test passed
VACUOUSLY: green here, red on the host. That is the same environment-coupling
that let the original bug reach the operator.

The stronger form, used by
`test_the_identity_decides_even_when_the_stamped_path_contradicts_it`: stamp a
path that is RESOLVABLE and GENUINELY DIFFERENT, plus a MATCHING uuid. Today
that is refused (the path says different). Under this design it is accepted (the
uuid short-circuits and the path is never read). It cannot pass vacuously under
today's code and it cannot pass vacuously under PR #598 either, because neither
of them can reach ACCEPT by that route.

## 14. What implementation found that this document did not say

### 14.1 One existing test PINNED the realpath fallback

`test__store_identity.py::test_a_store_that_does_not_exist_yet_falls_back_to_path_comparison`
asserted the exact behaviour §8 removes: a stamped path that cannot be `stat`-ed
compared equal to an identically-spelled caller path, so the guard returned
True. §8's truth table says that row is now `CANNOT TELL -> False`, so the test
had to change. It was rewritten (not deleted, not weakened) as
`test_a_store_that_cannot_be_stat_ed_is_CANNOT_TELL_not_a_path_compare`, with
the reasoning in its docstring and the second half — a genuinely different,
also-unstat-able store is refused — kept intact.

Its original premise no longer holds anywhere in production: "in DB-canonical
mode the YAML store is frequently a NAME the database is stamped with rather
than a file on disk". Both doors call `_db_mirrors_this_store(db_path, db_path)`
and a nonexistent `db_path` returns True at the first line, so the only
unstat-able side reachable in production is the STAMPED path — which is the
cross-namespace case the fallback answered wrongly.

### 14.2 The path stamp's unconditional overwrite was the FLIP MECHANISM

The document diagnoses the path as the wrong identity but does not name what
made the stamp move. `_db_freshness.stamp_store_provenance` used
`ON CONFLICT(key) DO UPDATE SET value=excluded.value` — an UNCONDITIONAL
rewrite — so every write replaced the stamp with the writer's own spelling of
the file. That is why the outage recurred three times on 2026-07-28 after three
correct repairs: writing a single card from the other namespace flipped it back.

Now the stamp is left alone when it already names the SAME FILE
(`_dual_write._same_file`, the package's one definition of sameness). The path
stamp is DIAGNOSTIC once identity is a uuid, and rewriting a diagnostic on every
write is pure harm. It is still refreshed when this namespace cannot stat the
stamped path, because a spelling the current reader can resolve is strictly more
useful than one nobody here can.

### 14.3 Two sibling branches landed on the same lines while this was open

Both were taken rather than clobbered, and neither was reverted.

**#610, "the provenance stamp is claimed once, not rewritten per write."** This
branch had made the same change for the same reason. #610's version is better —
it carries the measured inode (`2096/3417791`), the three names, and the RACE
that explains why three correct repairs were each undone: the repair stamps a
host-visible name, the next container-side write re-stamps `/home/agent/...`,
and every container write wins that race. Its version was taken whole. Only its
"THIS IS A MITIGATION, NOT THE FIX ... until that lands" paragraph was updated,
because with this PR the fix has landed and the path stamp is now diagnostic.
Its `tests/scitex_cards/test__stamp_is_claimed_once.py` is untouched and passes:
it builds its two names from a HARD LINK, so both paths are stat-able and the
removal of the realpath fallback cannot reach it.

**#611**, which split `_health.py` into `_health_store.py` for the same
line-cap reason this PR did. Its structure was taken and this PR's separate
`_health_store_identity.py` deleted — one home for the store checks, not two.
The uuid-aware `_check_store_identity_agrees` now lives in `_health_store.py`.

### 14.4 `check_fresh` is uuid-first too

§7 names only `_db_mirrors_this_store`. `_db_freshness.check_fresh` asks the
same ownership question and has no production caller today, but leaving it
path-only would have built the read/write asymmetry §7 forbids the moment
someone wired it up. It now short-circuits on `ACCEPT` exactly as the guard
does, and says CANNOT TELL honestly in the unstat-able case.
