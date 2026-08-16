#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn-URL resolution for scitex-cards's push delivery.

One cohesive responsibility: *where do I deliver a board event for this
agent?* The actual HTTP wire (``deliver`` + boot announcement) lives in
:mod:`scitex_cards._push`, which re-exports this module's public surface so
``from scitex_cards._push import turn_url_for`` (and the env constants) keep
resolving unchanged.

Resolution precedence (see :func:`turn_url_for`):

  0. scitex-cards's OWN user registry (``users:`` section of the task
     store) — file-local, NO bearer, the reliable PRIMARY source.
  1. ``SCITEX_CARDS_AGENT_TURN_URLS`` JSON map (operator-pinned).
  2. ``SCITEX_CARDS_TURN_URL_<SLUG>`` per-agent env (scitex-cards's own
     per-agent turn-url env contract).
  3. None → caller falls through to fail-loud "no-turn-url-configured".

Step 0 is the durable root fix (card
``cards-push-turn-url-from-user-registry-20260626``): the board OWNS the
registry, so that file-local, bearer-free source is consulted FIRST. It is
NOT a runtime pull — it reads scitex-cards's own ``users:`` rows (no external
import, no HTTP, no bearer); the endpoint fields are populated by explicit
registration and (later) by an external ``agent_registered`` bus consumer, a
separate card.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

ENV_MAP = "SCITEX_CARDS_AGENT_TURN_URLS"
PER_AGENT_PREFIX = "SCITEX_CARDS_TURN_URL_"


def _slug(agent: str) -> str:
    """scitex-cards's per-agent env-slug convention."""
    return agent.upper().replace("-", "_").replace("/", "_")


def _turn_url_from_user_registry(agent: str) -> str | None:
    """Resolve ``agent``'s turn URL from scitex-cards's OWN user registry.

    File-local, NO-bearer PRIMARY source (step 0 of :func:`turn_url_for`):
    resolves ``agent`` to a :class:`scitex_cards._users.User` and returns its
    :func:`scitex_cards._users.user_turn_url` (explicit ``turn_url`` else one
    derived from ``a2a_port`` + the host in ``host_at_name``). NOT a runtime
    pull — reads the ``users:`` store section; no external import, no HTTP, no
    bearer (the endpoint is populated by registration + an external
    ``agent_registered`` bus consumer, a separate card). ``_users`` is
    imported LAZILY to avoid an import cycle. Any failure → ``None`` so the
    caller falls through to the env chain (fail-loud ``None`` preserved).
    """
    try:
        from ._users import resolve_user, user_turn_url

        user = resolve_user(agent)
        return user_turn_url(user) if user is not None else None
    except Exception as exc:  # noqa: BLE001 — registry must never break delivery
        logger.debug(
            "[scitex-cards._turn_url] user-registry lookup for %r failed: %s",
            agent, exc,
        )
        return None


def turn_url_for(agent: str) -> str | None:
    """Resolve the turn URL for ``agent``. Returns None when not configured.

    Lookup order:
      0. scitex-cards's OWN user registry (``users:`` section) — file-local,
         NO bearer, reliable PRIMARY source. See
         :func:`_turn_url_from_user_registry`. (Root fix, card
         ``cards-push-turn-url-from-user-registry-20260626``.)
      1. ``SCITEX_CARDS_AGENT_TURN_URLS`` JSON map entry (operator-pinned).
      2. ``SCITEX_CARDS_TURN_URL_<SLUG>`` per-agent env (scitex-cards's own
         per-agent turn-url env contract).
      3. None — caller falls through to fail-loud "no-turn-url-configured".

    Step 0 is the durable root fix: the board OWNS the registry, so that
    file-local, bearer-free source is consulted FIRST. The env steps stay
    operator-overrides. The final fail-loud ``None`` is unchanged.
    """
    registry_user_url = _turn_url_from_user_registry(agent)
    if registry_user_url:
        return registry_user_url
    raw = os.environ.get(ENV_MAP)
    if raw:
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "[scitex-cards._turn_url] %s is not valid JSON — ignoring: %s",
                ENV_MAP, exc,
            )
            mapping = {}
        if isinstance(mapping, dict):
            url = mapping.get(agent)
            if isinstance(url, str) and url.strip():
                return url.strip()
    per_agent = os.environ.get(PER_AGENT_PREFIX + _slug(agent))
    if per_agent and per_agent.strip():
        return per_agent.strip()
    return None


__all__ = [
    "ENV_MAP",
    "PER_AGENT_PREFIX",
    "turn_url_for",
]

# EOF
