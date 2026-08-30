# ADR-0010 — `~/.scitex/cards/cards.db` becomes the single source of truth

> **SUPERSEDED — 2026-08-15. THE STORE IS POSTGRESQL ON 55432, NOT A LOCAL
> `cards.db`.** The body below is kept verbatim as the record of what was ruled
> on 2026-07-16; it is **not** current architecture. Do not implement from it.
>
> What changed, measured rather than recalled:
> - The live fleet store is `postgresql://scitex_cards@127.0.0.1:55432/scitex_cards`
>   (store_uuid `1d55dd6e-3d2a-4c24-a429-a78835ab988f`), one per host, synchronised
>   across hosts.
> - `_paths.py:152` records that **since 2026-08-13 the zero-config the retired engine default
>   RAISES** instead of naming a database. The tier this ADR established is gone.
> - Operator, 2026-08-15, verbatim: 「and never use the retired engine」/「we migrated to 55432
>   postgres」/「if you find the retired engine, <path/to>.db, it is violation」— and the
>   reasoning he gave for the singularity: 「if you have both the retired engine and postgres,
>   it will introduce mistakes and misunderstanding; remember, more options does
>   not mean better」.
>
> That last sentence is the same principle ADR-0016 already carries in its title
> (*transport plurality yes, storage plurality no*); this ADR is the storage tier
> that principle now excludes.
>
> An ADR marked ACCEPTED is the most authoritative doc form we have, so leaving
> this one unmarked meant the most trusted document in the repo asserted the one
> thing that is now a violation.

**Status:** SUPERSEDED 2026-08-15 (was ACCEPTED, operator-ruled, 2026-07-16)
**Supersedes / amends:** `docs/design/the retired engine-migration.md` (the RFC on branch
`design/the retired engine-migration`) — adopted as the base design; this ADR records the
deltas the operator's rulings introduced and the sequencing they force.

## Context

The RFC designed the yaml→the retired engine migration when the package was still
scitex-cards, target path `~/.scitex/cards/cards.db`, with open questions Q5 (git
audit trail) and Q6 (multi-host). Since then (all 2026-07-16):

1. The package was renamed **scitex-cards** (S1 shipped in v0.14.0: import
   shim, env dual-read, both CLIs; repo renamed to `scitex-ai/scitex-cards`).
2. The operator ruled the **DATABASE is the single source of truth** — not
   yaml-with-a-db-mirror: "unless the database is the single source of
   truth, we will sooner or later hit a wall on speed somewhere"
   (operator, translated; the original lives on the board card).
3. The operator declared the SSOT path: **`~/.scitex/cards/cards.db`**.
   An optional per-card file view (`~/.scitex/cards/cards/<card-id>/…`) is
   allowed but strictly DERIVED — the db always wins.
4. The operator answered RFC-Q5: the git audit/backup rail is a **periodic
   YAML snapshot EXPORTED FROM the db**, itself git-snapshotted. Git tracks an
   export, never live data — which also retires the dotfiles bind-mount hazard
   (the dotfiles working tree IS today's live store; a `git merge` there writes
   live data).
5. The operator approved migration path **(b)**: backup → stand up the db
   infrastructure → verified bulk import; a short dual window only at cutover.

## Decision

1. **Path.** `resolve_db_path`: explicit arg → `$SCITEX_CARDS_DB` →
   `$SCITEX_CARDS_DB` (deprecated, loud warning, one transition window) →
   `local_state.user_path("cards", "cards.db")`. The final tier stays
   DELEGATED to the ecosystem resolver — a project scope remains structurally
   inexpressible (the 2026-07-06 stale-store class stays dead).
2. **The pre-rename shadow db is dead weight.** `~/.scitex/cards/cards.db`
   (stale since 2026-07-13, dual-write off fleet-wide) is never moved, read,
   or trusted; `cards.db` is REBUILT from the live yaml by the idempotent
   importer at cutover, then verified (RFC-R4 A/B equivalence, counts printed
   and spot-checked).
3. **Backup rail is a deliverable, not an option.** `export --yaml` (db →
   canonical yaml text) plus a git-snapshot cadence ships with S4. dotfiles
   keeps tracking the live store — today's ONLY off-site backup — until this
   rail exists and is verified, then their tracking flips to the export.
4. **Canonicality flips only at fleet cutover (S6).** The fleet's SIF bakes
   pre-rename code and every agent writes the yaml through its own MCP server
   today. Flipping the hub to db-canonical while ANY old writer remains forks
   the store. Order: publish scitex-cards (S3) → SIF floor bump → sac env flip
   → dotfiles store-path pin flip (their card
   `dotfiles-env-store-path-flip-to-scitex-cards-20260716`, pinged by us after
   verification) → db becomes canonical → `~/.scitex/cards` becomes a symlink
   to `~/.scitex/cards` (operator-ruled end shape). Exactly one canonical
   store at any moment.
5. **Multi-host (RFC-Q6).** Spartan's island store and its project-shadow fork
   stay on yaml untouched until the hub cutover is verified; merge-vs-federate
   is decided afterwards (card `scitex-cards-spartan-store-fork-reconcile-20260716`).

## Consequences

- The store's speed ceiling moves from O(whole-yaml-per-write) to O(row);
  the Cards Django board reads through the indexed db.
- yaml demotes to (a) the rollback state until cutover is verified, then
  (b) the export/backup format only. `_store_verify`'s reason to exist goes
  away with it (engine-guaranteed integrity; `quick_check` on open stays).
- Every identity/path flip in this migration carries the fleet checklist
  learned from the S1 skill-symlink regression: sweep the FLEET for old-path
  references before landing, regenerate links in the same change, and a boot
  canary (`sac agents start` must succeed) gates "done".
