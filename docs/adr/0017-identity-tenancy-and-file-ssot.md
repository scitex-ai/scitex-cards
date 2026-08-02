# ADR-0017 — Identity, tenancy, and file SSOT

- **Status**: proposed
- **Date**: 2026-08-02
- **Depends on**: ADR-0009 (roles), ADR-0010 (cards.db as SSOT), the operator's
  append-only ruling
- **Reserves**: nothing yet in `src/` — this is a decision record, not a
  migration. §6 lists what has to be true before any of it is built.

## Context

scitex-cards has to work in three shapes at once, and the operator asked for all
three deliberately:

1. **Standalone** — one person, one machine, SQLite, no server.
2. **Behind a proxy** — the same board reachable from a phone, through a
   Cloudflare Tunnel with Cloudflare Access in front. Never Tailscale.
3. **Hub plugin / group SaaS** — several people, one hosted deployment,
   PostgreSQL, groups the hub can sell.

The operator settled the shape of the answer before this document existed, and
these are the constraints the design serves rather than revisits:

- Authentication offers a **choice of SSH key or password**, the way `ssh` does.
- Permissions are **granted by an admin, like GitHub**.
- **A solo user is a group of one, with one admin member.** One model, not two.
- `ywatanabe` the group and `ywatanabe` the user may share a name.
- **The leaf has to be solid on its own**; the hub then merely connects to it.
- **Per-app and per-project scoping is probably a key**, not an afterthought.
- The identity primitives belong in **scitex-app / scitex-ui**, because this is
  not a cards-only problem.
- Files have **one SSOT**.

## Decision

### D0 — The authority boundary is the store handle. A tenant is a store, not a row.

Every CLI, MCP and library open goes through `_db.connect`, and the version
floor there is a no-op for an unstamped client (`_min_client_version.py`). So
whoever holds a store handle holds total authority over everything that handle
reaches. Any tenancy expressed as a *column* inside that same database is a
convention that well-behaved clients follow — which is precisely what a SaaS
cannot be sold on.

Three properties of the existing code make the column approach not merely weaker
but actively unsafe, and each was read in this repository rather than assumed:

- **`_assert_no_shrink`'s ground truth is an unfiltered live read.**
  `_store_backend._current_stored_ids` is documented as "deliberately a live
  query, not a value threaded through from an earlier read, so the check is
  correct even when `doc` was built from a stale snapshot (the exact shape of the
  2170->18 collapse)". Row-level security filters exactly that read. Invisible
  rows are indistinguishable from absent rows, so the guard that exists *because
  of* a board wipe would go quiet.
- **`task_roles` is a projection, not a grant store.** `_db_mirror.py:119` runs
  `DELETE FROM task_roles WHERE task_id = ?` and repopulates from caller-supplied
  card JSON on every card write. A grant recorded there has a lifetime of one
  write by any client version.
- **There is no append-only trigger on `tasks`.** `_pg_triggers.PG_TRIGGER_NAMES`
  holds nine names; eight are `dm_*` or `schema_meta_*`, and the ninth is
  `tasks_bump_revision`. The append-only property the operator ruled on is
  enforced on DM rows, not on cards.

Consequences, which fall out rather than needing separate argument:

- No `tenant_id` column on `tasks`. No RLS. No session GUC. No DB-role split.
  No `SCHEMA_VERSION` bump for tenancy, and therefore no forced DDL pass across
  ~90 v9 clients.
- Reads are never filtered, so the wipe guard keeps working.
- SQLite and PostgreSQL get the **same** enforcement primitive, so shapes 1 and 3
  do not fork at the security boundary. This is the operator's "the leaf has to
  be solid" requirement, stated as a mechanism.
- A multi-tenant deployment hands out **zero** store handles. Members get an HTTP
  session against a mediator that resolves `(subject, tenant) -> store path`,
  which `_workspace.resolve_workspace_store` already does, allowlist-validated
  and fail-closed.

### D1 — One membership model, from one person to a SaaS

A **group** owns a store. A **member** is a `(subject, group, role)` triple.
`role` is `admin` or `member`; admins grant and revoke.

A solo user is a group of one whose sole member is an admin — the operator's
unification, taken literally, so there is no "personal mode" branch anywhere.
Group names and user names live in **separate namespaces**, so `ywatanabe` the
group and `ywatanabe` the user coexist without qualification.

Granularity is the store, exactly as GitHub grants at the repository. There is no
per-card ACL in v1, and that is a decision rather than an omission: the only two
substrates available for one — `task_roles`, and the caller-settable
`created_by` / `assignee` fields of unauthenticated MCP verbs — are as
caller-controlled as `scope`, which the same reasoning already rejects. Within a
store, everyone who can connect can do everything. That is what the code enforces
today and what "standalone" means anyway.

### D2 — A project is a store

Per-app and per-project scoping is not another axis. It is D0 read forwards: if
the boundary is the store, then giving a project its own store gives it its own
boundary, its own membership, and its own backup — with no new mechanism.

### D3 — Two credential types, one principal, and no third option

`scitex_app.identity` exports a `Principal` value object and a provider protocol.
Two providers ship:

- **SSH key** — challenge signed by a key from the group's authorized set. This
  is what agents and CLIs use.
- **Password** — argon2 verifier. This is what a browser on a phone uses.

Both produce the same `Principal`. Nothing downstream learns which was used, so
adding a third (an OIDC provider for hub SSO) touches one module.

The application never reads a header, a cookie, or an environment variable to
learn who is calling. That rule is what makes the hub-plugin and standalone paths
the same code.

**The app always authenticates. A proxy in front is a second layer, never the
boundary.** This was tested against reality the day the ADR was written: the
first version of the public-exposure gate accepted, as a third option, a written
claim that something in front authenticated every request. It was rejected — auth
stops being legible once it is made fine-grained, and key-or-password like `ssh`
is the whole model. The security argument turned out to be the stronger one: that
third option was the *only* path to an origin with no login, and the process
cannot observe whether the thing in front is enforcing. A misconfigured proxy
must not be a breach.

**Two shaping constraints, both making a missing state unrepresentable rather
than detected:**

- **No provider configured is a refusal, never an anonymous `Principal`.** If the
  protocol can return `None` or an "anonymous" subject, a misconfigured
  deployment produces a caller who is not authenticated but *is* represented, and
  every downstream check then evaluates a valid-looking subject. The type is
  non-optional: either you hold a `Principal` or the call raised. (scitex-dev.)
- **Silence is not the permissive case.** Omission must reach the refusal, not the
  permission. A configuration that says nothing about authentication is not a
  configuration that opted out of it.

### D4 — Direct-DB access is attribution, not authentication

`X-Scitex-Agent` is presence-checked and unsigned. The fleet's ~90 agents write
through a shared DSN. This ADR does not pretend otherwise: on the direct-DB path,
the agent id is **attribution for the audit trail**, and the credential is the
DSN. Saying so plainly is the point — a gate documented as authentication that
only records a claim is worse than no gate, because it is cited as one.

The HTTP surface is where verified principals live, and it is where humans and
browsers arrive.

### D5 — Attachment bytes follow the store, not the writer

`attachments_root()` resolves to `<store_dir>/attachments` via
`resolve_tasks_path(store).parent`. For a PostgreSQL DSN, `resolve_tasks_path`
returns the **user root** — so the row lands on the shared server while the bytes
land in whichever HOME happened to write them. That is today's behaviour and it
is a real split-brain, not a hypothetical one.

Files get one SSOT: a `blobs` catalog in the store, addressed by content hash
**scoped to the store**. Scoping is deliberate — a global content address would
buy a cross-tenant existence oracle and cross-tenant erasure in exchange for
saving duplicate bytes. Duplicate bytes are cheap.

The invariant: **a store on a server may not have file-backed blobs.** A design
that permits `backend='fs'` while the catalog lives in shared PostgreSQL has
reproduced today's bug with a catalog row on top of it.

## Consequences

**Good.** No schema bump, so ~90 clients are undisturbed. The wipe guard keeps
its unfiltered read. One membership model covers one person and a paying group.
Adding a credential type is one module. A project boundary costs a store, not a
feature.

**Costs, accepted.** Cross-tenant queries become impossible by construction —
correct for a SaaS, and a genuine loss for fleet-wide reporting, which will need
a separate aggregation path. Duplicate attachment bytes across stores. A mediator
process becomes a required component in shape 3, where today there is none.

**Not solved here.** §7 of the working notes lists the rest; the two that matter
most are recorded as cards rather than buried in prose.

## Open, and blocking

1. **`SCITEX_CARDS_PUBLIC_HOST` opens the board without asserting board auth.**
   `_django/settings.py` calls `assert_exposure_is_authenticated` only on the
   `SCITEX_CARDS_ALLOWED_HOSTS` (LAN) branch, where it raises unless
   `SCITEX_CARDS_PASSWORD` is set. The `PUBLIC_HOST` branch — the Cloudflare
   Tunnel path, which is the one phone access would use — adds the hostname to
   `ALLOWED_HOSTS` and asserts only that `DJANGO_SECRET_KEY` exists.

   The gap was that **nothing verified** whether a proxy was enforcing in front.
   Access being configured is a Cloudflare-side fact this process cannot see, so
   a correct deployment and an open board looked identical from inside.

   **Fixed in #747, per D3**: the board authenticates its own callers, so the
   question no longer arises — Access becomes defence in depth rather than the
   boundary, and a misconfigured policy is no longer a breach. Card:
   `cards-public-host-exposure-has-no-password-assertion-20260802`.

   What remains open is not code: the Cloudflare route and its Access policy
   still have to be created, and the control on that side is that an
   unauthenticated GET must return a challenge rather than a 200. That sits with
   scitex-hub on `hub-cards-phone-access-cloudflare-route-20260802`.

2. **The user registry contradicts itself.** `_users/_store_read.py` reads users
   from `resolve_tasks_path` — a per-host file — while `_db_mirror.py` deletes and
   repopulates `users` / `user_names` in the database. Membership cannot be built
   on a registry that has two disagreeing homes, so this is a prerequisite for
   D1, not a follow-up.

## Provenance

The lens designs (identity/duality, tenancy, ACL, file SSOT) and their adversarial
critiques came from a nine-agent workflow; three of the four critics returned
`fundamentally-broken` against their own lens's proposal, which is what produced
D0 rather than a column.

Every fact in the Decision section above was **re-read in this repository** before
being relied on — `_store_backend._current_stored_ids`, `_db_mirror.py:119`,
`_pg_triggers.PG_TRIGGER_NAMES`, `_attachments.attachments_root`, the
`IdentityACLPort` / `OpenACL` call sites (ports and adapters only; zero
enforcement sites), and the `settings.py` exposure asymmetry. A subagent's
finding is a hypothesis; these are the ones that were checked.
