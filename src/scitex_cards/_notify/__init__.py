#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notification config schema + pure recipient resolver (foundation C3).

This is **C3** of the card-event / notification foundation epic: the
NOTIFICATION-CONFIG layer that answers exactly one question — *"which
event types notify whom?"* It is a **PURE schema + resolver**. There is
NO delivery here (delivery — actually sending a Telegram / email / a2a
message — is C4, a separate card). :func:`resolve_recipients` is
deterministic and side-effect-free: it reads users + config and returns
a ``set`` of recipient ids; it never sends anything.

It builds on the merged foundation:

* :mod:`scitex_cards._events` — the canonical :class:`~scitex_cards._events.Event`
  taxonomy (:data:`~scitex_cards._events.EVENT_TYPES`). Recipients are
  resolved per ``event.type``.
* :mod:`scitex_cards._users` — the standalone user registry. All
  name→id resolution goes through :func:`~scitex_cards._users.resolve_user`
  (SSOT for identity); per-user notify prefs live on ``User.notify``.
* :mod:`scitex_cards._model` — the card schema. The role fields are
  ``agent`` (owner), ``assignee``, ``collaborators`` (list),
  ``subscribers`` (list).
* :mod:`scitex_cards._paths` — :func:`~scitex_cards._paths.resolve_tasks_path`
  locates the store; the optional ``notify.json`` sidecar lives next to
  it.

## The three config layers (precedence: GLOBAL ← PER-USER ← PER-CARD)

Most-specific wins. The resolver composes three layers:

1. **GLOBAL defaults** — :data:`DEFAULT_NOTIFY_RULES`, a built-in
   ``{event_type: [role, ...]}`` map (the SSOT default). An optional
   ``notify.json`` sidecar (a sibling of the task store, kept SoC-separate
   from the task payload) can OVERRIDE / extend it. :func:`load_notify_config`
   returns the built-ins merged with the sidecar; with zero config it
   returns the built-ins unchanged.

2. **PER-USER prefs** — read from ``User.notify`` (``{mute, watch}``):
   ``mute`` = never notify this user for these event types (opt-out, even
   if a role matches); ``watch`` = also notify this user for these event
   types on cards where they are ALREADY a member (opt-in; never pulls in a
   non-member). See :class:`NotifyConfig` for the authoritative schema.

3. **PER-CARD overrides** — an optional ``card["notify"]`` field
   (``{events, add, mute}``): ``events`` overrides the role list for an
   event on THIS card; ``add`` force-includes specific users; ``mute``
   force-excludes specific users. The card layer wins.

## Layout

* :mod:`._rules`    — schema: roles, :data:`DEFAULT_NOTIFY_RULES`,
  :class:`NotifyConfig`, :class:`NotifyConfigError`.
* :mod:`._config`   — loading: validation helpers, ``notify.json`` sidecar,
  :func:`load_notify_config`.
* :mod:`._resolver` — :func:`card_role_members` + the deliverable
  :func:`resolve_recipients`.

This module re-exports the public surface so
``from scitex_cards._notify import resolve_recipients`` works.
"""

from __future__ import annotations

from ._config import load_notify_config
from ._resolver import card_role_members, resolve_recipients
from ._rules import (
    CARD_NOTIFY_KEYS,
    DEFAULT_NOTIFY_RULES,
    NOTIFY_SIDECAR_NAME,
    ROLE_ASSIGNEE,
    ROLE_COLLABORATORS,
    ROLE_OWNER,
    ROLE_SUBSCRIBERS,
    USER_NOTIFY_KEYS,
    VALID_ROLES,
    NotifyConfig,
    NotifyConfigError,
)

__all__ = [
    "CARD_NOTIFY_KEYS",
    "DEFAULT_NOTIFY_RULES",
    "NOTIFY_SIDECAR_NAME",
    "NotifyConfig",
    "NotifyConfigError",
    "ROLE_ASSIGNEE",
    "ROLE_COLLABORATORS",
    "ROLE_OWNER",
    "ROLE_SUBSCRIBERS",
    "USER_NOTIFY_KEYS",
    "VALID_ROLES",
    "card_role_members",
    "load_notify_config",
    "resolve_recipients",
]

# EOF
