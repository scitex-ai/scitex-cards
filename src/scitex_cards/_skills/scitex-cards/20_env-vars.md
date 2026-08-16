---
description: |
  [TOPIC] Environment Variables & Local State
  [DETAILS] $SCITEX_CARDS_DB pins the SQLite task store; SCITEX_DIR relocates
  the user-scope ~/.scitex root. Both are optional — the store resolves to
  the user-canonical database by default.
tags: [scitex-cards-env-vars]
---

# Environment Variables & Local State

| Name                | Default                      | Purpose                                              |
|---------------------|------------------------------|------------------------------------------------------|
| `SCITEX_CARDS_DB`    | (unset)                      | Absolute path to the SQLite database; wins over the user-canonical default. This is the SOLE store-identity axis — see `scitex_cards._paths`. |
| `SCITEX_CARDS_AGENT_ID` | (unset)                   | This agent's identity — stamps every write's `created_by`/`updated_by`, keys the channel inbox, and is the `--mine` filter. Fail-loud when unresolved. (Renamed 2026-07-02 from the now-rejected `SCITEX_CARDS_AGENT`.) **Headless lever:** leave it UNSET and `scitex-cards mcp start` runs TOOLS-ONLY — the inbox poll loop is not started and the session receives ZERO channel pushes. This is the intended mode for solver / headless capsules that must not receive unsolicited pushes. |
| `SCITEX_CARDS_CHANNEL_SOURCE` | `scards` | `mcp channel` `meta.source` (drives the `<- scards` render — the fleet's short sender-identity label, deliberately distinct from the `scitex-cards` agent id). Overridden by `--name`. |
| `SCITEX_CARDS_CHANNEL_INTERVAL` | `5.0`             | `mcp channel` poll interval (seconds) between inbox drains. Overridden by `--interval`. |
| `SCITEX_DIR`        | `~/.scitex`                  | Relocates the user-scope state root, so the user database becomes `$SCITEX_DIR/cards/cards.db`. |

Copy [`.env.example`](../../../../.env.example) to `.env` at your project root
to set these; CLI flags always override env vars.

## Store resolution order (first existing wins)

1. explicit path passed to the calling function
2. `$SCITEX_CARDS_DB`
3. user scope: `~/.scitex/cards/cards.db` (relocatable via `$SCITEX_DIR`)

There is no project-scope layer for the data store — a process run with
cwd inside any repo resolves the same canonical database (see
`scitex_cards._paths` for the rationale).

## Local state directories

| Path                                  | Scope         | Purpose                  |
|---------------------------------------|---------------|--------------------------|
| `~/.scitex/cards/cards.db`            | user-global   | the canonical task store |
| `~/.scitex/cards/*.json`              | user-global   | sidecar state (threads, inboxes, notify config, dashboard, reminders) |

See `general/01_ecosystem_04_environment-variables.md` and
`general/01_ecosystem_06_local-state-directories.md`.
