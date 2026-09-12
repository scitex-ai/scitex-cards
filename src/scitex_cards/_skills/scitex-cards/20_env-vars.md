---
description: |
  [TOPIC] Environment Variables & Local State
  [DETAILS] $SCITEX_STORE_DSN selects the scitex-dev shared PostgreSQL store;
  there is no SQLite fallback. SCITEX_DIR relocates local runtime sidecars.
tags: [scitex-cards-env-vars]
---

# Environment Variables & Local State

| Name                | Default                      | Purpose                                              |
|---------------------|------------------------------|------------------------------------------------------|
| `SCITEX_STORE_DSN`    | scitex-dev host default       | Shared PostgreSQL store DSN (port 55432). A filesystem path is rejected; there is no SQLite fallback. |
| `SCITEX_CARDS_NOTIFY_DSN` | notification service default | Optional separate PostgreSQL LISTEN/NOTIFY transport (port 55433). Never selects the state store. |
| `SCITEX_CARDS_AGENT_ID` | (unset)                   | This agent's identity — stamps every write's `created_by`/`updated_by`, keys the channel inbox, and is the `--mine` filter. Fail-loud when unresolved. (Renamed 2026-07-02 from the now-rejected `SCITEX_CARDS_AGENT`.) **Headless lever:** leave it UNSET and `scitex-cards mcp start` runs TOOLS-ONLY — the inbox poll loop is not started and the session receives ZERO channel pushes. This is the intended mode for solver / headless capsules that must not receive unsolicited pushes. |
| `SCITEX_CARDS_CHANNEL_SOURCE` | `scards` | `mcp channel` `meta.source` (drives the `<- scards` render — the fleet's short sender-identity label, deliberately distinct from the `scitex-cards` agent id). Overridden by `--name`. |
| `SCITEX_CARDS_CHANNEL_INTERVAL` | `5.0`             | `mcp channel` poll interval (seconds) between inbox drains. Overridden by `--interval`. |
| `SCITEX_DIR`        | `~/.scitex`                  | Relocates the user-scope state root (runtime sidecars, per-task prose). It does NOT set the store target — only `$SCITEX_STORE_DSN` does. |

Copy [`.env.example`](../../../../.env.example) to `.env` at your project root
to set these; CLI flags always override env vars.

## Store resolution

1. An explicit `store=` argument wins for that function call.
2. Otherwise scitex-dev resolves `$SCITEX_STORE_DSN`, or its central PostgreSQL
   host default on port 55432.

There is no Cards-specific DB variable, config-file store tier, project-scope
store, or SQLite fallback.

## Local state directories

| Path                                  | Scope         | Purpose                  |
|---------------------------------------|---------------|--------------------------|
| `~/.scitex/cards/cards.db`            | user-global   | the canonical task store |
| `~/.scitex/cards/*.json`              | user-global   | sidecar state (threads, inboxes, notify config, dashboard, reminders) |

See `general/01_ecosystem_04_environment-variables.md` and
`general/01_ecosystem_06_local-state-directories.md`.
