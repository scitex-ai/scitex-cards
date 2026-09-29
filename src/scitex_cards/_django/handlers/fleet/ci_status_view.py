#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view: ``GET /fleet/ci-status``.

Returns a JSON document the front-end polls every 30s to render the
CI-status pills strip in the board toolbar.

Response shape (200)::

    {
      "repos": [
        {
          "slug": "owner/name", "branch": "main",
          "head_sha": "abc1234", "overall": "success",
          "checks": [...]
        },
        # per-repo error sub-document:
        {"slug": "owner/missing", "error": "<adapter message>"}
      ],
      "config": {"repos": ["owner/name", "owner/missing"]}
    }

If the config itself can't be loaded (malformed YAML), respond 500 with
``{"error": "<message>"}`` so the front-end can render a single visible
"adapter error" state rather than a silently-empty strip.

The harness contract: ONE bad repo does NOT break the page (caught
per-repo); CONFIG failure DOES (the whole strip is unconfigurable).
"""

from __future__ import annotations

import scitex_logging as slogging

from django.http import JsonResponse

from ._config import fleet_config_load
from ._errors import FleetAdapterError
from .gh_ci import fetch_many_ci_status

logger = slogging.getLogger(__name__)


def fleet_ci_status_view(request):  # noqa: ARG001 — request unused (GET only)
    """Serve ``/fleet/ci-status``. See module docstring for the contract."""
    try:
        config = fleet_config_load()
    except FleetAdapterError as exc:
        logger.warning("[fleet/ci-status] config load failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)

    repos: list[str] = config["fleet"]["ci_status"]["repos"]
    # ONE bulk GraphQL call for the whole watch-list (was N×2 REST calls —
    # which blew GitHub's rate limit at ecosystem scale). Per-repo failures
    # come back as ``{"slug","error"}`` entries INSIDE the batch; a WHOLE-
    # batch failure (gh missing / auth / network) raises and we degrade to
    # one visible error row per repo — never a blank strip.
    try:
        per_repo: list[dict] = fetch_many_ci_status(repos)
    except FleetAdapterError as exc:
        logger.warning("[fleet/ci-status] bulk fetch failed: %s", exc)
        per_repo = [{"slug": slug, "error": str(exc)} for slug in repos]

    return JsonResponse(
        {
            "repos": per_repo,
            "config": {"repos": repos},
        }
    )


__all__ = ["fleet_ci_status_view"]

# EOF
