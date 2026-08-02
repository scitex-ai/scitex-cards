---
description: |
  [TOPIC] Fleet-wide propagation of the scitex-cards skill IDs into every
  agent's spec.yaml so every agent reads the usage skill on boot.
  [WHEN] Read on every agent-container update wave + every time a new
  agent is added to the fleet. This leaf documents WHY the propagation
  exists, WHAT the manifest is, and HOW to run the propagate CLI verb.
  [HOW] `scitex-cards skills manifest` to inspect; `scitex-cards skills
  propagate --agents-dir <dir> --dry-run` to preview; `-y` to apply.
tags:
  [
    scitex-cards-skills-propagation,
    scitex-cards-fleet-rollout,
    scitex-cards-required-skills,
  ]
---

# Fleet-wide skills propagation

scitex-cards is **THE fleet's single source of truth** for durable todos
(operator + lead mandate; see [SKILL.md](./SKILL.md#-mandate--single-source-of-truth-operator--lead-2026-06-12)).
For every agent to honour that mandate it has to **read the scitex-cards
usage skill on boot** — which means the skill ID has to be declared in
each agent's spec.yaml ``required_skills`` list.

This leaf is the **canonical propagation artifact**: one manifest, one
CLI verb, one idempotent fleet-wide sweep.

Provenance: operator directive — board card
``rec-propagate-scitex-cards-skill-into-every-agent-required-skills``.

## The canonical skill manifest

scitex-cards ships a manifest at
``src/scitex_cards/_skills/manifest.yaml`` (resolved at runtime via the
:func:`scitex_cards._cli._skills_propagate.manifest_path` helper). It
lists the canonical skill IDs that should land in every fleet agent's
skill list:

```bash
# Inspect.
scitex-cards skills manifest          # human view
scitex-cards skills manifest --json   # machine view
```

The manifest is the **single source of truth** for the skill IDs — both
the propagate verb (below) AND agent-container's spec-generation script
read from it. Bumping the canonical set is therefore a **one-line
manifest edit** + a propagate sweep; no spec.yaml edits needed by hand.

## The propagate CLI verb

`scitex-cards skills propagate --agents-dir <DIR>` walks ``<DIR>/<agent>/
spec.yaml`` files and idempotently appends the manifest's skill IDs to
each one's skill-list field. Round-trip is via ``ruamel.yaml`` so
existing comments + key ordering survive.

```bash
# Preview first (no writes).
scitex-cards skills propagate \
    --agents-dir ~/.scitex/agent-container/agents --dry-run

# Apply (idempotent — repeated runs are noops).
scitex-cards skills propagate \
    --agents-dir ~/.scitex/agent-container/agents -y
```

Default field is ``metadata.labels.skills`` (v3 spec: CSV string). The
``--field`` flag switches to a YAML-list flavor — handy when the agent
declares the list under a different name:

```bash
scitex-cards skills propagate \
    --agents-dir ~/.scitex/agent-container/agents \
    --field spec.required_skills -y
```

Idempotence is enforced via set semantics: the verb dedup-appends only
the canonical IDs missing from the existing list, and is a true no-op
when every ID is already present.

### SciTeX audit-cli §2 compliance

- ``--dry-run`` previews every planned edit and writes nothing.
- ``-y`` / ``--yes`` skips the interactive confirmation (required when
  ``stdin`` is a TTY and ``--dry-run`` is not set).
- ``--json`` emits per-file outcomes (path, before, after, added,
  changed) for machine consumers (agent-container's CI; operator
  dashboards).

## End-to-end flow

```
                  ┌──────────────────────────────────────────────┐
                  │  scitex-cards (this package)                  │
                  │  _skills/manifest.yaml ← canonical IDs       │
                  └────────────────────┬─────────────────────────┘
                                       │ read
                                       ▼
                  ┌──────────────────────────────────────────────┐
                  │  scitex-cards skills propagate                │
                  │      --agents-dir <agents-root>              │
                  └────────────────────┬─────────────────────────┘
                                       │ ruamel round-trip edits
                                       ▼
                  ┌──────────────────────────────────────────────┐
                  │  <agents-root>/<agent>/spec.yaml             │
                  │  metadata.labels.skills:                     │
                  │    scitex-dev, git, scitex-cards              │
                  │                              ^^^^^^^^^^^^    │
                  │                              propagated      │
                  └────────────────────┬─────────────────────────┘
                                       │ agent-container boot
                                       ▼
                  ┌──────────────────────────────────────────────┐
                  │  Every fleet agent reads scitex-cards SKILL.md│
                  │  on boot → consults shared YAML store        │
                  │  correctly. The SSoT mandate holds.          │
                  └──────────────────────────────────────────────┘
```

## When to re-run

- After every ``pip install -U scitex-cards`` on the fleet host (if a
  new release bumped the manifest).
- After every new agent is added to ``~/.scitex/agent-container/
  agents/`` (the verb is idempotent — running it again is harmless).
- As a recurring agent-container ``cron`` job for self-healing fleets
  (matches the ``mcp install --apply`` pattern from PR #155 / runbook
  §7.5).

## Cross-references

- [SKILL.md](./SKILL.md) — the entry leaf + operator-mandate context.
- [21_fleet-mcp-rollout.md](./21_fleet-mcp-rollout.md) — the sister
  fleet rollout artifact (MCP wire). Propagation here covers the
  spec.yaml side; 21 covers the ``.mcp.json`` side.
- ``src/scitex_cards/_cli/_skills_propagate.py`` — implementation.
- ``src/scitex_cards/_skills/manifest.yaml`` — the manifest file
  itself.
