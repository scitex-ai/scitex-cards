---
description: |
  [TOPIC] Installation
  [DETAILS] pip install scitex-cards; the [web] extra adds the Django board;
  PNG rendering needs mmdc or outbound kroki.io. Verify with
  `python -c "import scitex_cards"`.
tags: [scitex-cards-installation]
---

# Installation

```bash
# Recommended — uv resolver
uv pip install scitex-cards[all]

# Plain pip also works
pip install scitex-cards
```

System requirements: Python ≥ 3.10.

## Extras

| Extra   | Adds                                                        |
|---------|-------------------------------------------------------------|
| `web`   | Django + scitex-app/scitex-ui for the `scitex-cards board`   |
| `docs`  | Sphinx + RTD theme for building the documentation           |
| `dev`   | pytest, pytest-cov, scitex-dev (test + audit toolchain)     |
| `all`   | everything above                                            |

## Rendering backends (for `render-graph`)

PNG output needs **one** of:

- `mmdc` (mermaid-cli) on `PATH`, with a puppeteer/playwright chromium, or
- outbound access to `kroki.io` (the automatic fallback).

Printing the mermaid source (`scitex-cards render-graph --print-mermaid`)
needs neither.

## Verify

```bash
python -c "import scitex_cards; print(scitex_cards.__version__)"
scitex-cards --help
```
