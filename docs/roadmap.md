# scitex-todo roadmap

The canonical artifact is the **YAML task store** (top-level `tasks:` list).
Everything else is an adapter that renders, imports, or serves that store.
This keeps the data portable and the surfaces swappable.

```
                         tasks.yaml  (canonical store, single source of truth)
                              |
        +---------------------+---------------------+
        |                     |                     |
   mermaid adapter       Web UI adapter        org adapter
   (YAML -> PNG)         (board + reorder)     (org <-> YAML)
      [done]              [done / partial]        [future]
```

## Vision

`scitex-todo` is the **shared task backend** for SciTeX — one store that:

- is shared across agents (including SAC agents), each with their own scope so
  unnecessary detail stays hidden;
- synchronizes across (remote) hosts;
- centralizes TODOs and visualizes them and their progress;
- acts as the interface between agents and users — users reprioritize and edit
  via a web GUI, agents read/write the same YAML;
- keeps private todos in `~/.scitex/todo/`;
- stays generic (no project-specific assumptions);
- is part of SciTeX, slotting into `scitex-hub` via `scitex-ui` / `scitex-app`.

## Done

- **Canonical store + validator** — `load_tasks` / `save_tasks` validate
  id/title/status (statuses include `goal`); `parent` enables drill-down.
- **Mermaid adapter** — `build_mermaid` (depends_on arrows, `blocks`
  inhibition edges, per-status colors) + `render` (mmdc-first, kroki fallback).
- **Web board (read-only + drag-reorder)** — React-Flow board served by Django
  (`scitex-todo board`); the priority handler persists drag-reorder back to the
  YAML store (the agent↔user GUI interface, first write path).
- **Store identity** — explicit `store` -> `$SCITEX_CARDS_DB` (PostgreSQL on
  55432); unset raises. One axis, one backend: no SQLite tier, no project scope,
  no bundled-example fallback.
- **CLI** — `render-graph`, `list-tasks`, `board`, plus the standard
  introspection / completion / `skills` commands.
- **Agent skills** — `_skills/scitex-todo/` (installable via
  `scitex-todo skills install`).

## Future

- **org adapter** — read/write org-mode TODO trees (`:BLOCKER:` / `ORDERED` /
  org-edna) as an alternate canonical face, round-tripping back to YAML.
- **Multi-agent scopes** — per-agent views over the shared store so each agent
  sees only its lane; SAC-agent integration.
- **Cross-host sync** — keep one logical store consistent across remote hosts.
- **MCP server** — expose `load`/`render`/`list` as agent-callable tools
  (`todo_*`) under the SciTeX interface convention.
- **HTTP API** — optional FastAPI surface for web clients / dashboards.
- **scitex-hub integration** — mount the board into `scitex-hub` via
  `scitex-ui` / `scitex-app`.
- **Read the Docs** — published Sphinx site.

## Intended role in the ecosystem

A fleet orchestrator (orochi) reads/writes the same YAML store a researcher
edits locally; the mermaid adapter and web board give everyone the same
dependency picture without prose.
