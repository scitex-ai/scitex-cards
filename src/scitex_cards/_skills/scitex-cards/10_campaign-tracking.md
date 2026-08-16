---
description: |
  [TOPIC] Multi-package release/audit campaign tracking — companion tooling
  [DETAILS] Companion helpers that live under `~/.scitex/cards/` (the user
  state dir) and consume the shared task store + a flat campaign-status
  markdown table. Used during the recurring "release wave" and
  "audit sweep" campaigns across the 66-package scitex ecosystem.
  Covers `check_releases.py` (PyPI-vs-pyproject roster) and
  `campaign_report.py` (campaign-status.md → diff-highlighted PDF).
tags: [scitex-cards-campaign-tracking, scitex-cards, release-campaign]
---

# Campaign tracking — companion tooling

`scitex-cards` is the canonical task store. For multi-package campaigns
(release waves, audit sweeps), two companion helpers live under the
user state dir `~/.scitex/cards/` and read the same store plus a
flat board file `GITIGNORED/campaign-status.md` in the orchestrating
repo (typically `scitex-lead`).

These helpers are intentionally NOT bundled into the `scitex-cards`
package — they depend on the local fleet layout (campaign repos under
`~/proj/scitex-*`, board markdown layout). Documenting them here so
the scitex-cards user knows the campaign-side of the workflow.

## `~/.scitex/cards/check_releases.py`

Scans `~/proj/*/pyproject.toml` against PyPI and classifies each
package:

| status | meaning |
|---|---|
| `RELEASED`    | pyproject version == latest PyPI version |
| `STALE`       | pyproject version < latest PyPI version (local behind PyPI) |
| `BEHIND`      | pyproject version > latest PyPI version (unreleased commits — needs publish) |
| `UNPUBLISHED` | package is not on PyPI yet |
| `DYNAMIC`     | pyproject uses a dynamic version backend; classification deferred |

```bash
~/.scitex/cards/check_releases.py            # human-readable table
~/.scitex/cards/check_releases.py --json     # JSON for piping
```

Pair with the scitex-dev release flow (see
`scitex_dev/_skills/general/05_development_03_release-automation.md`):
filter `BEHIND` rows → run the develop→main→tag flow on each.

## `~/.scitex/cards/campaign_report.py`

Renders `GITIGNORED/campaign-status.md` (the orchestrator's board
markdown, hand-curated rows × columns of package × phase) into a
timestamped PDF. Diffs against the most-recent snapshot and
yellow-highlights any cell that changed since last run, so the
operator can see at-a-glance "what moved" without re-reading the
whole table.

```bash
~/.scitex/cards/campaign_report.py
# → ~/.scitex/cards/reports/campaign-status-YYYYMMDDTHHMMSS.pdf
```

The snapshot tracking lives next to the PDF outputs; deleting the
snapshot dir resets the diff baseline.

## When to use which

| Surface | Tracks | Update cadence |
|---|---|---|
| the task store (this package, see [03_python-api.md](03_python-api.md)) | per-task state machine: NEW → IN-PROGRESS → DONE, plus depends_on/blocks edges | per-task |
| `campaign-status.md` (board markdown) | per-package × per-phase tabular state (de-mock / release / smoke / e2e / …) | per-campaign-tick |
| `check_releases.py` output | per-package PyPI-vs-pyproject delta | on demand |

The first is structured (graph). The second is a flat board (matrix).
The third is a live external probe. Each answers a different question
during a campaign.
