---
description: |
  [TOPIC] Diagnose DM persistence or exchange-status refusal caused by the
  canonical status-ledger route, role privileges or provisioning visibility.
  [WHEN] A DM fails before persistence, or authenticated status polling is 503.
  [DETAILS] Scoped credentials, typed SQLSTATE diagnosis and reviewed ACL recipes.
tags: [scitex-cards-dm-ledger-credentials]
---

# DM ledger credentials and refusal diagnosis

A DM and its notification use the ordinary `store=` data target. It may be an
explicit owned schema and does not select or authenticate the shared ledger.
The exchange ledger resolves separately through `scitex_dev.store.resolve_target`
for the builtin `status_exchanges` plugin. Only the separate internal trusted
`ledger_store=` capability overlays user/password on that canonical target.
All other parsed connection parameters must match, including host, port, database, schema/options and TLS
settings. The authenticated server session role and database are checked before
opening the Store. A mismatch refuses; there is no alternate database or file.

Hub supplies the capability after its authentication/audience guards, through
`request.scitex_store`; the DM view passes it separately as `ledger_store` as
well as the DM data store. A browser query/body cannot select this credential.
The generic RPC server keeps its ordinary boot-pinned data store separate from
the canonical ledger; RPC bodies reject `ledger_store`, and HubBackend refuses
to forward such credentials. CLI/library calls keep canonical ledger routing
and credentials unless trusted in-process host code explicitly supplies the
internal capability. No new CLI credential flag is introduced. Preserve
the existing sender visibility check on exchange GETs. Do not widen the global
`SCITEX_STORE_DSN` reader to make a private DM operation work.

## Distinguish the failure

- SQLSTATE `42501` is insufficient privilege, not a bad password. Its typed
  status is HTTP 403 with a database-privilege observation.
- SQLSTATE class `28` identifies an authentication/authorization connection
  failure; Cards reports the dependency authentication refusal separately.
- `StoreProvisionError` without a more specific inner SQLSTATE means the schema
  is missing **or unavailable to this role**. Empty `information_schema`
  results do not prove physical absence: role privileges affect visibility.
- Follow the bounded cause/context chain, even when traceback context was
  suppressed. Do not infer authentication failure from the substring `auth`
  in an instruction such as “authorized migration identity”.

Inspect configured endpoints/roles and PostgreSQL catalog permissions without
printing DSNs, passwords or message bodies. A successful connection is not a
write-capability check. Compare physical catalog objects/indexes with the
installed ledger declaration. `scitex-cards health` alone does not establish
ledger admission. `Store()` is not a guaranteed read-only probe: other targets
may require schema creation, so do not use it as an unreviewed live preflight.

## Existing-schema service-role capability

The reviewed deployment uses the private server role `scitex_cards_dm`. For
DM exchange creation/read, the existing four ledger tables need:

| Table | Required privileges |
| --- | --- |
| `status_exchanges_rows` | SELECT, INSERT, UPDATE |
| `status_exchanges_oplog` | SELECT, INSERT |
| `status_exchanges_cursor` | SELECT |
| `status_exchanges_identity` | SELECT |

Rows use an upsert; the oplog appends; cursor/identity metadata is read. The
owned PostgreSQL regression exercises the actual DM + notification + ledger
202 and idempotent replay with this set. No ledger sequence, DELETE, TRUNCATE,
schema CREATE, ownership or unrelated-table grant is needed. Notification
sequence USAGE is a separate existing Cards capability, not a ledger grant.

This ledger intentionally uses global MULTI_WRITER application actors and has
no per-user database RLS in the reviewed deployment. Granting this **server-only**
credential access trusts the backend to write global exchange rows; it does not
create SQL-level per-user isolation. Keep browser requests authorized and
sender-filtered, the credential private, and public/global readers unchanged.

The [grant](scripts/dm-ledger-grant.sql) and
[rollback](scripts/dm-ledger-revoke.sql) are bounded, transactional **operator
review recipes**, not automatic migrations. The grant checks the incident's
all-absent prestate, canonical database and current table ownership/policy.
Record fresh prestate and the applied script hash before using the authorized
migration identity. A later ACL change needs a new review; rollback applies
only to this recorded change. Missing schema requires separate provisioning,
not broader application-role rights. Never put a password in these scripts.

Source: [`_exchange_target.py`](../../_dm/_exchange_target.py),
[`_exchange_errors.py`](../../_dm/_exchange_errors.py),
[`exchange.py`](../../_dm/exchange.py), and the Django
[`dm.py`](../../_django/handlers/dm.py) caller. HTTP 202 proves persistence,
not visibility, recipient acknowledgement or model completion.
