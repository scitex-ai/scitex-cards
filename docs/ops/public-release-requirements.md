> **Historical evidence notice.** The original 259-line requirements record
> below is preserved verbatim. Its observations, counts, test results, status
> labels and release checklist describe **2026-09-10**, not current deployment
> evidence. The five definitions and their order are retained. Restoring this
> record introduces no new global release gate, activation rule or waiver.
> Current source evidence follows the end of the historical record.

<!-- BEGIN preserved 2026-09-10 record -->

# Public-release requirements for the Cards board

This is the definition the compass §13 (L480-485) asked Cards to own. Until
2026-09-10 the list of requirements lived only in the compass itself — the
audit target — with no artifact in the repo defining what "ready to ship to
the public" means. This document is that definition, so a release decision is
made against a checklist that lives beside the code, not against an external
note.

It is deliberately a GATE, not a promise: every row states what "met" looks
like with the concrete evidence that proves it (or does not), and the release
criterion at the bottom is that **all rows are met**. A row that is met
today is evidenced; a row that is not is named so the next person does not
mistake the silence for compliance.

Scope is the **public** board — the deployment an anonymous or guest user can
reach. The loopback / dev board (DEBUG=true) is the operator surface and is
explicitly out of scope for the "hide internal concepts" rows; those rows
concern what a stranger sees.

---

## The five requirement areas

The compass names five: Persistence, Permissions, Project linkage, Error
handling, Stable basic workflows. Each is split into "what public release
needs" and "what the repo shows today", so the gap is the diff, not the prose.

### 1. Persistence

Public release needs: the board reads and writes a durable store that
survives restarts, is not silently replaced by a fresh empty one, and is
addressed by an identity a wrong replica cannot fake.

Repo shows today (met):
- The canonical store is a **live PostgreSQL** database — `resolve_store()`
  resolves `postgresql://scitex-primary:55432/scitex` (observed 2026-09-10,
  7,122 cards), and `health` reports `single_write_target`: exactly one write
  target via `_store_backend.write_doc_to_db`, no dual-write toggle.
- A store that does not name itself is **refused, not invented**: the
  zero-config resolver raises `StoreTargetNotConfigured` /
  `UnrecognisedStoreTarget` rather than opening a fresh empty file (a
  "wrong board that works is worse than one that will not start" — the
  2026-07-19 destruction is the incident this refusal exists for, see
  `_store_url.py`).
- Fork/replica identity: `store_uuid` is stamped in the store's
  `schema_meta` and `resolve_store` reports it
  (`store_uuid=1d55dd6e-…`). **Known limitation (stays open, tracked):**
  a dump/restore preserves the UUID, so a 300-card fork reports the SAME
  identity — `store_uuid` alone cannot detect a fork
  (card `cards-store-uuid-cannot-detect-a-fork-identity-must-be-the-pair-20260812`,
  P1). This is a documented design defect, not a persistence failure; it does
  not block the gate below but must be named so release does not treat
  identity as proof of "the" store.

### 2. Permissions

Public release needs: shared authentication (not an app-local login), a
real authorization boundary between an operator and a stranger, and no
deployment secret or filesystem path leaked to the untrusted side.

Repo shows today:
- **Authentication is shared, not Cards-local (met).** In the hub the board
  sits behind `apps/workspace/todo_app/middleware.py`: `if user is None or
  not user.is_authenticated:` → page navigations redirect to `/auth/login/`,
  data fetches get a shaped 401 JSON. The cards Django app has **no** `models.py`
  and **no** migrations — there is no app-specific user model, and no
  `AUTH_USER_MODEL` override anywhere in `src/`. (The `_users.User`
  dataclass is a standalone board-*membership* registry — who may be an
  assignee/collaborator/subscriber — with a hard zero-Django-dependency
  constraint; it is not an auth identity.)
- **Public-vs-operator split exists as a mechanism (met).**
  `_store_errors.py` carries two audiences per failure: `str(exc)` (the full
  diagnosis, including the absolute store path) and `exc.public_summary`
  ("The task store is not available on this server."). `views.py` picks
  between them on `settings.DEBUG`, and `settings.py:86` **forces
  `DEBUG = False` whenever `SCITEX_CARDS_PUBLIC_HOST` is set** (deliberately
  not an env override — "no legitimate reason to serve debug tracebacks on a
  public hostname") and refuses to boot a public board on the dev
  `DJANGO_SECRET_KEY` (settings.py:52). So a publicly-reachable deployment
  already cannot leak the store paragraph to a stranger *on the error path*.
- **Board self-auth for standalone exposure (met).** Setting
  `SCITEX_CARDS_PUBLIC_HOST` without `SCITEX_CARDS_PASSWORD` is a hard
  startup refusal (`assert_public_exposure_is_authenticated`); a public
  board that is not password- or Access-gated will not start.

**Not met (gates release):** the *normal* (non-error) board UI still
renders deployment internals to any authenticated viewer — see row 5 and the
gap list below. The `public_summary` split covers store *errors* only; the
footer and the rendered page do not use it.

### 3. Project linkage

Public release needs: a user's board to be scoped to the user's project,
resolved server-side, so one user cannot read another's cards.

Repo shows today (met):
- `apps/workspace/todo_app/middleware.py: _resolve_workspace_store(request)`
  resolves the requester's active project → workspace base → store path, and
  **containment-validates** the result stays inside the owning workspace base
  (an escape is logged and refused). The store is injected on
  `request.scitex_store` — a request attribute, not a client-controlled
  query param.
- A client-supplied `?store=` is **discarded** with a warning
  (`[todo-mount] discarding client-supplied ?store= from user …`), so
  server-side tenancy cannot be spoofed by the exact parameter an attacker
  controls.
- The hub publishes the deployment's chosen store on mount
  (`config/settings/_optional_apps.py: publish_cards_store_target()`),
  which is what the board actually reads (commit `78073dcb7`, #657:
  "the hub never told the board which store to read, then 500'd about it").
- The tenancy opt-out is wired: `optional_upstream_apps()` sets the
  per-project lane-discovery env to the documented opt-out so host lanes do
  not leak into every hub user's board (commit `927c335a5`, #761).

**Not met (stays open):** the opt-out fix was **latent, not live** — prod and
dev django both run `HOME=/root`, so the fallback lane glob matched zero
files when measured. The control is correct but unexercised; it becomes real
the moment `HOME` changes or a home with project lanes is mounted.

### 4. Error handling

Public release needs: failures to say a lot to the operator (logs) and little
to a stranger (page), never a bare 500, and store-absent to be machine-
distinguishable from an unknown endpoint.

Repo shows today (met):
- Every store failure is a **typed exception**, not a prose blob callers
  string-match: `RevisionConflictError`, `StoreNotProvisionedError`,
  `StoreUnavailableError` (`_store_errors.py`), each with
  `public_summary`.
- Store-absent is a **404 with a typed reason**, not a 403 (which Cloudflare
  Access also answers) and not a 500; `STORE_ABSENT_REASON = "store_absent"`
  lets a client tell "you asked for something that does not exist" from
  "you asked correctly and there is nothing here", without string-matching a
  sentence (`views.py`).
- The board page degrades, never 500s, on a render failure:
  `board_v3_page` wraps `render_to_string` in `try/except`, logs, and serves
  the static graph fallback; `_static_graph_page` surfaces a load error
  *in the page* ("Failed to load task store: …") rather than as an exception
  (`views.py`).
- **Known limitation:** the render-fallback path is itself what hides a
  *different* class of failure — a template that cannot resolve (e.g. the
  `scitex_app` shell gap) is caught by the same `except Exception`, logged to
  stderr only, and answered with a 200 fallback, so the operator sees a
  working-but-wrong board. Pinned by
  `tests/scitex_cards/_django/test__board_shell_migration.py`.

### 5. Stable basic workflows

Public release needs: the core board actions to work end-to-end against a
real store and to be covered by tests that read the database, not a harness
that manufactures the precondition.

Repo shows today (met):
- CRUD is delegated to **locked store verbs** and tested against a real
  seeded database (`tests/…/handlers/test_crud.py`,
  `test__crud_delegates_to_locked_verbs.py`): create/update/delete/archive/
  restore/edge/reopen/resolve, with concurrency survival
  ("survives concurrent write") and stale-board behaviour pinned.
- The board **reads the database unconditionally** — the old
  "create an empty tasks.yaml before each test" marker fixture was deleted
  and its absence is now load-bearing (`tests/scitex_cards/_django/conftest.py`):
  a harness that supplies a missing precondition tests the harness, not the
  system. This was the 2026-07-29 outage shape: the board gated its card read
  on a file production did not have, so /tasks served 0 cards while 2,654 sat
  in the DB, and every test passed because each one recreated the file.
- DM, timeline, runnable/blocked-batch, nudge, rescore/priority are each
  backed by a handler and a test that asserts the response shape against a
  real store.

---

## Gaps that gate a public release (honest, dated 2026-09-10)

These are the rows that are **not** met, with the concrete evidence, so a
release decision cannot paper over them. Gaps 1-2 were the two halves of the
same server-rendered footer; on 2026-09-10 the **server footer** was gated
behind `show_internal_chrome` (= `settings.DEBUG`, which `settings.py:86`
forces off for a public deployment), closing the server-rendered half. What
remains open is named below, honestly.

1. **Internal/operator terminology reaches the rendered page — PARTIALLY
   closed (server footer done; working vocabulary + SPA open).**
   DONE: the server-rendered footer at `board_v3.html` —
   `v3 LIVE · real data · operator schema (ADR-0007) · GUI→code (ADR-0006)`
   plus the `<code id="store-path">` element — is now wrapped in
   `{% if show_internal_chrome %}` and hidden from any public (DEBUG=false)
   viewer; the operator (DEBUG=true) keeps it. Pinned by
   `tests/scitex_cards/_django/test__board_hides_internal_chrome_from_public.py`.
   STILL OPEN (the L478 decision the audit flagged): the board's *working
   vocabulary* — "agent" throughout, "BLOCKING-YOU", the search qualifiers
   (`project:/agent:/status:/kind:/parent:/scope:/id:/priority:/host:`) and
   the blocker filter (`operator-decision` etc.) — is still shown to every
   viewer, and the **React SPA** (a separate surface, `standalone.html` →
   `frontend/src/CardsBoard.tsx`, built bundle `assets/index.js`) renders
   `store_path` in its own header (`<code>{graph.store_path}</code>`).
   Replacing or gating that vocabulary is a product decision (which terms are
   "internal" vs. "user-friendly equivalents") the operator must make; it is
   not something a bounded code change can choose unilaterally.
   (compass §13 L478, §21 L643.)
2. **Schema / ADR / debug-facing information in the normal UI — PARTIALLY
   closed (server footer done; SPA open).**
   DONE: the server footer's ADR references, "operator schema", "v3 LIVE" and
   store path are operator-only now (see gap 1). STILL OPEN: the React SPA
   header still shows `store_path`, and the SPA's export/copy features
   (`clipboard.ts` "file: …", `exportBoard.ts` store line) embed it on demand.
   The server-side `/graph` payload also still carries `store_path` in its JSON
   (pinned as a contract by `test_board_handlers_use_cached_read.py`); a public
   deployment would need that stripped from the wire, not just the chrome.
   (compass §13 L479, §21 L643.)
3. **No internal/dev-only or release-channel gate on the route.**
   `/apps/cards/` is mounted unconditionally when the package is importable
   (hub `config/urls.py:194`, behind only `if _scitex_cards_installed()`);
   the cards manifest declares `"wip": false` and no
   `show_in_launcher` / `visibility` / internal flag. It is absent from the
   hub *launcher* (visibility defaults `private`) but reachable by any
   authenticated hub user, including guest/visitor-pool auto-login users.
   "Keep internal for now" (compass §13 L472) is not enforced at the route.
   (L478/L479 reduce what a public viewer *sees*; they do not stop a public
   viewer from *reaching* the route — that is this gap and the L291 route
   gate.)
4. **Dogfooding is not defined as a release gate.** The board is in active
   internal use (live store serving the fleet), but no artifact defines
   "dogfood" as a required, evidenced precondition of public release
   (compass §13 L473). This document begins that definition; the dogfood
   period + criteria remain to be stated by the operator.

---

## Release criterion

The Cards board is **publicly releasable** when **all** of the following hold:

- [ ] Rows 1-5 above are each **met** (the repo shows the evidence, not a
      promise).
- [x] Gap 1 — server-rendered footer (version/ADR/store-path) is hidden from
      non-operator viewers, keyed on the same `DEBUG` / public-host split
      `_store_errors.py` uses (2026-09-10, `board_v3.html`
      `{% if show_internal_chrome %}`, `settings.py:86`). The remaining L478
      work — the working vocabulary ("agent", "BLOCKING-YOU", search
      qualifiers) and the React SPA's `store_path` header — is an operator
      product decision, tracked in gap 1 "STILL OPEN".
- [x] Gap 2 — server-rendered schema/ADR/debug store-path is operator-only
      (same change as gap 1). The SPA header `store_path` and the `/graph`
      wire payload still carry it; that is tracked in gap 2 "STILL OPEN".
- [ ] Gap 3: an explicit internal-vs-public gate exists on the route (a
      manifest flag or release-channel check that the hub launcher AND the
      URL mount both honour), so "development only" is a state, not an
      aspiration.
- [ ] Gap 4: the operator has stated the dogfood period and acceptance
      criteria, and they have been met with evidence.
- [ ] The `store_uuid`-cannot-detect-a-fork limitation is either fixed or
      explicitly accepted by the operator with the risk named in the
      release note.

Nothing below that line may be "waived" silently. If a row is waived, the
waiver is written here, by whom, and why — the same fail-loud discipline the
rest of the package applies to its schema.

<!-- END preserved 2026-09-10 record -->

---

## Current source evidence — 2026-10-03

This section updates evidence and scope without rewriting the historical
definitions or treating their old checkmarks as present acceptance. The normal
browser scope has already been selected by the application lead: **the Hub
mount and the standalone Board using the shared leaf**. CLI and machine rails
are outside that presentation scope. Routine browser wording needs no further
operator scope decision.

### Merged first stage and its limits

[PR #1056](https://github.com/scitex-ai/scitex-cards/pull/1056) was actually
merged at `af5fbacef1455f13223c1dd82391ae9c2d4f4b74` on
2026-10-03 at 11:32:35 UTC. Its source tree is
`e4730e07c918373accc4ac15947aaf0cef47ff71`, matching reviewed head
`65d751e7f6cedf92abdd4e9e1c53aaa477c94e88`. All six required checks
(import smoke, Python 3.11/3.12/3.13 matrices, Sphinx and audit) and all eleven
material checks succeeded at that exact head. The recorded ordinary merge and
subsequent owned fast-forward-only pull both exited zero; the pulled source
was clean at that merge commit and tree. The earlier proposal's
"CI pending" description is historical.

That first stage changes browser footer and static-fallback presentation.
`_cards_internal_chrome_enabled(request)` delegates to the existing SDK
`element_inspector_enabled(request)`, accepts only the actual Boolean
`True`, and returns `False` if the optional SDK decision is unavailable or
fails. BoardV3 computes this decision once and passes it into its render
fallback. SDK eligibility means inspector **availability**, not the private
overlay's active state. The SDK resolves its existing setting override before
its DEBUG/authenticated-staff policy; raw DEBUG alone is not this eligibility
decision.

Standalone settings map the existing `SCITEX_UI_ELEMENT_INSPECTOR`
environment name only when present, using the existing
`.lower() == "true"` Boolean convention. Absence preserves the SDK's prior
resolution. This presentation eligibility is not authentication, tenancy,
store-write authority or an authenticated delivery observer.

The browser follow-up has since progressed to delivered source. The current
merged source is [PR #1059](https://github.com/scitex-ai/scitex-cards/pull/1059),
commit `dc1a53c587fd7d2fdb5dc90d91304568732fafaf`, tree
`e7e2faa93df8e12998f86080bd3461729e27e89f`. This includes the preceding
[PR #1058](https://github.com/scitex-ai/scitex-cards/pull/1058) milestone at
`88a865d6b6789902a47b66e650c10cbea4fbd78a`, tree
`966d96d9d4ab589c2fd2cc7dec26172389545b3f`: canonical PostgreSQL DM read-ack
isolation and quiet load errors in BoardV3 and the React leaf, with the normal
generated frontend. Ineligible viewers retain a useful generic error lead;
React retains its real Retry action. Diagnosis requires the existing SDK
eligibility decision, transported as literal `true`; missing or invalid
transport is quiet. Machine API error semantics and store fields are retained.
These bounded changes do not establish every working label or connected workflow.

PR #1059 requires a nonblank explicit store choice or `SCITEX_STORE_DSN` before
server startup uses the shared resolver. A shared default is not a server
choice; ordinary non-server resolution is retained. Standalone settings use the
existing `scitex_app.i18n.i18n_settings()` and `with_locale_middleware()` helpers.
The declared App minimum is `>=0.26.1`; the required UI minimum remains
`>=0.11.1`. Hosted CI selected App 0.26.1 and UI 0.23.1. The finite private
controls retain their recorded source/setup scope; they do not qualify complete
HTTP routes, rendered Japanese or the current UI runtime.

PR #1058's [natural postmerge tests](https://github.com/scitex-ai/scitex-cards/actions/runs/37134140170)
succeeded on all three Python jobs at `88a865d6`; each raw summary reports
7,950 passed, 64 skipped and 4 xfailed. PR #1059's matched premerge matrix also
succeeded, with 7,968 passed, 64 skipped and 4 xfailed in each raw summary.
Its [natural postmerge run](https://github.com/scitex-ai/scitex-cards/actions/runs/37137096122)
at `dc1a53c5` is **not green**: Python 3.11 and 3.13 succeeded, Python 3.12 was
cancelled after the job's maximum 30-minute execution time, the test workflow
concluded `cancelled`, and its aggregate failed. All three captured pytest
summaries report 7,968 passed, 64 skipped and 4 xfailed. Those summaries do not
override job cancellation or prove post-job cleanup. Quality, docs, import
smoke and CLA workflows succeeded separately at that exact merged source.

The six normal browser observations remain **UNRUN**: real-fixture BoardV3 entry,
light mobile, dark mobile with reload, Japanese header, Japanese cookie
precedence, and the Japanese/theme React leaf at `/legacy`. Source transfer and
selected private environment readiness are preparation, not a running fixture
or serving result. A fresh supported same-host managed lease is requested;
Infra owns the backend/Unix target and managed scope, while Apps owns the normal
Cards binding, production schema/runtime contract and genuine fixture setup
through clients. No active fixture is established by this document.

The normal standalone command does not install Django authentication or session
middleware. Its browser principal falls back to `operator`; a declared Cards
seed-write actor is separate. The optional board-password cookie provides
single-password access, not an authenticated Django user/session. The Cards
user registry and UI project-provider protocol do not establish owning-host
authentication, project containment or session authority. Normal card events
enqueue PULL inboxes; separate push callers or actual providers require their
own legitimate receiver contract. No authenticated delivery observer or new
SAC, model or harness dependency is introduced by these milestones.

### Evidence matrix, in the original five-area order

| Requirement area | Current source evidence | Evidence still separate |
| --- | --- | --- |
| **1. Persistence** | Current `dc1a53c5` source retains the canonical PostgreSQL Board read/build path. The server-choice refusal prevents startup from silently treating a shared default as its selected store; it grants no write authority. | The old endpoint, 7,122-card count, UUID observation and durability tests are historical. Current restart durability, physical target/schema identity and fork detection need matched evidence. No UUID risk acceptance is recorded here. |
| **2. Permissions** | The package retains the login/staff/tenant mount declarations. The existing SDK eligibility gate covers the footer, fallback, React store chip/Leaf timing and load-error diagnosis; it is not authentication. | Hub enforcement, actual login/staff behavior, tenant isolation and exposure settings require separate evidence. Normal standalone supplies no authenticated Django user/session; its registry and seed actor do not grant one. |
| **3. Project linkage** | The server-side tenant attribute/locator and machine `graph.store_path` field/type remain retained. Optional UI project-provider interfaces consume the owning host's accessibility answer; a locator is not project ownership. | Historical middleware/containment/lane observations are not current qualification. Genuine owning-host project resolution, sessions, containment and absence of cross-user/lane leakage remain connected/deployed acceptance. No two-tenant claim is made. |
| **4. Error handling** | The static fallback retains canonical reads/logs and safe escaped summaries. PR #1058 also gates BoardV3/React load-error diagnosis while preserving useful leads and Retry. The machine API error helper is unchanged. | Isolated renderer/template/compiler and private source controls cover their stated scope. The six normal serving/browser cases are UNRUN; every endpoint and deployed normal/error behavior are not thereby qualified. |
| **5. Stable basic workflows** | Ordinary merges/pulls deliver the bounded source milestones. PR #1058 includes canonical DM read-ack isolation; PR #1059's source gate and shared locale seam are delivered. CI outcomes are recorded above with the postmerge cancellation intact. | Historical workflow/concurrency evidence and bounded DM/private controls do not establish every public workflow. Current connected persistence, authentication, project and basic workflows plus serving/cleanup/observer acceptance remain separate. |

### Source references and release boundaries

The first-stage references describe its `e4730e07` tree. The following paths
anchor current merged `dc1a53c5` / `e7e2faa9` source; they do not prove the serving
incumbent uses this tree:

- `src/scitex_cards/_django/manifest.json`, lines 35–52: retained mount declarations.
- `src/scitex_cards/_django/views.py`, line 91: SDK browser eligibility;
  lines 350–427: BoardV3 context; line 575: safe gated fallback markup.
- `src/scitex_cards/_django/settings.py`, line 51: presence-only inspector
  mapping; lines 138 and 160: shared locale settings and middleware.
- `src/scitex_cards/_store_target.py`, line 251: configured server choice.
- `src/scitex_cards/_django/_user_scope.py`, line 95: request principal;
  `_django/settings.py`, lines 122–160: normal apps/middleware.
- `src/scitex_cards/_django/templates/scitex_cards/board_v3.html`, line 468,
  and `standalone.html`, line 19: server-derived browser visibility transport.
- `src/scitex_cards/_django/static/scitex_cards/board_v3/boardStates.js`,
  line 142, and `frontend/src/CardsBoard.tsx`, line 553: gated error diagnosis.
- `src/scitex_cards/_django/handlers/graph.py`, line 253, and
  `frontend/src/types/board.ts`, line 125: retained machine store field/type.

The historical suggestion to remove `store_path` from the machine wire is
not current delivered behavior or a prerequisite introduced by this
restoration. Browser presentation/export changes must preserve the existing
machine schema unless a separate compatible change is authorized.

This document records no new dogfood duration or criteria, UUID-fork waiver,
permission-policy exception or release activation. Source declarations,
matched CI, merge and pull are evidence of their stated scope; they are not
deployed Hub enforcement, managed fixture/CAS/observer admission or live
public-release readiness. The original 2026-09-10 checklist remains a
historical record, not a silent current waiver or a newly activated global
policy.
