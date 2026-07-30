#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet-dashboard adapters — registry-reader pattern (Phase 0 harness).

Phase 1 shipped the CI-status pills strip: each watched repo's GitHub CI
state read live from ``gh``. The same harness hosts the timing panel.

Design principles (HARD):

- fail-loud — adapters RAISE :class:`FleetAdapterError` on missing,
  malformed, or unreachable data. No silent fallback to defaults.
- registry-sourced — read authoritative state (GitHub CI),
  never duplicate it locally.
- NO hardcoded proper nouns — the watched-repo list is config-driven
  (``~/.scitex/cards/dashboard.json`` or env
  ``SCITEX_TODO_FLEET_CI_REPOS=foo,bar``).

Public API (this ``__init__``):

- :class:`FleetAdapterError`
- :func:`fleet_config_load`   — read the dashboard config (no raise on
  absence; raises on malformed YAML)
- :func:`fetch_repo_ci_status` — fetch ONE repo's CI summary via ``gh`` REST
- :func:`fetch_many_ci_status` — fetch MANY repos in ONE ``gh`` GraphQL call
  (the rate-safe ecosystem-scale path the pills use)
- :func:`fleet_ci_status_view` — Django view for ``/fleet/ci-status``
"""

from ._config import fleet_config_load
from ._errors import FleetAdapterError
from .ci_status_view import fleet_ci_status_view
from .gh_ci import fetch_many_ci_status, fetch_repo_ci_status
from .timing import compute_timing, task_durations
from .timing_view import fleet_timing_view

__all__ = [
    "FleetAdapterError",
    "compute_timing",
    "fetch_many_ci_status",
    "fetch_repo_ci_status",
    "fleet_ci_status_view",
    "fleet_config_load",
    "fleet_timing_view",
    "task_durations",
]

# EOF
