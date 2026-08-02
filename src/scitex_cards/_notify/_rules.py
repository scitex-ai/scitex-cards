#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notify-config SCHEMA: roles, the global default rules, and NotifyConfig.

The schema half of foundation C3 (see :mod:`scitex_cards._notify` for the
package overview). Holds the closed role set (:data:`VALID_ROLES`), the
built-in SSOT :data:`DEFAULT_NOTIFY_RULES`, the merged-global
:class:`NotifyConfig` dataclass (whose docstring is the authoritative
definition of the per-user and per-card prefs shapes), and the fail-loud
:class:`NotifyConfigError`. No I/O lives here — loading is in
:mod:`._config`, resolving in :mod:`._resolver`.
"""

from __future__ import annotations

import dataclasses

from .._events import EVENT_TYPES, EventType

# --------------------------------------------------------------------------- #
# Roles                                                                       #
# --------------------------------------------------------------------------- #
#: ``owner`` resolves to the card's ``agent`` field, falling back to
#: ``assignee`` when ``agent`` is absent (``agent`` is the operator-co-designed
#: owner field; ``assignee`` is the legacy spelling — see
#: :class:`scitex_cards._model.Task`). ``assignee`` is ALSO its own role so a
#: rule can target the legacy field explicitly. ``collaborators`` /
#: ``subscribers`` map to the same-named list fields on the card.
ROLE_OWNER = "owner"
ROLE_ASSIGNEE = "assignee"
ROLE_COLLABORATORS = "collaborators"
ROLE_SUBSCRIBERS = "subscribers"

#: Closed, validated set of role names usable in a notify rule (global
#: default, ``notify.json``, or a per-card ``events`` override). Fail-loud on
#: any other value — a typo'd role would otherwise silently notify no one.
VALID_ROLES: frozenset[str] = frozenset(
    {ROLE_OWNER, ROLE_ASSIGNEE, ROLE_COLLABORATORS, ROLE_SUBSCRIBERS}
)


# --------------------------------------------------------------------------- #
# Global default rules — the SSOT zero-config baseline                        #
# --------------------------------------------------------------------------- #
#: Built-in ``{event_type: [role, ...]}`` defaults — the SSOT baseline used
#: when no ``notify.json`` sidecar overrides a given event. Sensible signal
#: levels:
#:
#: * ``commented``      → owner + collaborators + subscribers (full thread)
#: * ``completed``      → owner + subscribers (the card is closed) — the
#:   SINGLE canonical "done" notice (see ``merged`` below)
#: * ``merged``         → [] (default-quiet; still per-card opt-in). A
#:   PR-merge that closes a card emits BOTH ``completed`` (via the store's
#:   done flip) AND ``merged`` (via the git-link). If ``merged`` ALSO
#:   defaulted to [owner, subscribers] the C4 dispatcher would DOUBLE-notify
#:   the same people for one event. ``completed`` is the canonical
#:   done-notice; ``merged`` stays quiet by default and a card that wants a
#:   merge ping can opt in via its per-card ``notify`` override.
#: * ``released``       → subscribers (release announcement)
#: * ``deployed``       → subscribers (deploy announcement)
#: * ``reassigned``     → owner (the new owner should learn they own it)
#: * ``reassigned_batch`` → owner (mirrors ``reassigned`` for the bulk verb;
#:   the event carries a SYNTHETIC ``batch:<old>-><new>`` card_id, so owner-role
#:   resolution against a real card is a no-op — actual delivery to the new
#:   owner is the caller's job, per the reassign_all recover-via-caller contract.
#:   The rule exists so the coverage invariant holds and any subscriber tooling
#:   watching renames has a declared surface.)
#: * ``created``        → assignee (whoever it was filed against)
#: * ``status_changed`` → subscribers (state transitions are subscriber-level)
#: * ``rank_changed``   → owner + subscribers (the owner's DISPATCH POSITION
#:   moved — ADR-0011 §8's "the new order is immediately shared with agents"
#:   lands here for the card most affected; subscribers are the watching
#:   parties, e.g. a lane blocked on the rank engine. One event per rescore,
#:   for the rescored card only — neighbours shift silently by design.)
#: * ``committed``      → owner (low-ish signal; the owner tracks commits)
#: * ``pushed``         → owner (same)
#: * ``pulled``         → [] (low-signal; default-quiet — opt-in via watch)
#:
#: Every key is a member of :data:`scitex_cards._events.EVENT_TYPES`; every
#: value is a list of :data:`VALID_ROLES`. The map is COMPLETE over the
#: taxonomy (an event with no recipients maps to ``[]`` explicitly, not by
#: omission) so a reader can see the full policy at a glance.
DEFAULT_NOTIFY_RULES: dict[str, list[str]] = {
    EventType.CREATED: [ROLE_ASSIGNEE],
    EventType.REASSIGNED: [ROLE_OWNER],
    EventType.REASSIGNED_BATCH: [ROLE_OWNER],
    EventType.STATUS_CHANGED: [ROLE_SUBSCRIBERS],
    EventType.COMMENTED: [ROLE_OWNER, ROLE_COLLABORATORS, ROLE_SUBSCRIBERS],
    EventType.COMPLETED: [ROLE_OWNER, ROLE_SUBSCRIBERS],
    EventType.RANK_CHANGED: [ROLE_OWNER, ROLE_SUBSCRIBERS],
    EventType.COMMITTED: [ROLE_OWNER],
    EventType.PUSHED: [ROLE_OWNER],
    # Default-quiet: a PR-merge-close already fires `completed` (the
    # canonical done-notice). Defaulting `merged` to recipients too would
    # double-notify; keep it [] and let a card opt in per-card. See the
    # rationale block above.
    EventType.MERGED: [],
    EventType.RELEASED: [ROLE_SUBSCRIBERS],
    EventType.PULLED: [],
    EventType.DEPLOYED: [ROLE_SUBSCRIBERS],
}

#: Filename of the optional sidecar that overrides / extends the built-in
#: defaults. It lives in the resolved task store's directory (see
#: :func:`scitex_cards._paths.resolve_tasks_path`) — SoC-separate from the
#: task payload (do NOT fold notify rules into the task store).
NOTIFY_SIDECAR_NAME = "notify.json"

#: Recognised keys in a ``User.notify`` prefs dict. See :class:`NotifyConfig`
#: for the semantics. Unknown keys are ignored (forward-compat).
USER_NOTIFY_KEYS: frozenset[str] = frozenset({"mute", "watch"})

#: Recognised keys in a per-card ``notify`` override dict. See
#: :class:`NotifyConfig` for the semantics. Unknown keys are ignored.
CARD_NOTIFY_KEYS: frozenset[str] = frozenset({"events", "add", "mute"})


class NotifyConfigError(ValueError):
    """Raised when a notify config (sidecar or per-card) is malformed.

    Fail-loud per the SciTeX constitution: the message echoes the offending
    value plus the expected shape so the author can fix the config.
    """


# --------------------------------------------------------------------------- #
# NotifyConfig — the merged global layer                                      #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class NotifyConfig:
    """The merged GLOBAL notify layer: ``{event_type: [role, ...]}``.

    This is layer 1 (the global default) AFTER an optional ``notify.json``
    sidecar has been merged onto :data:`DEFAULT_NOTIFY_RULES`. It does NOT
    carry per-user or per-card data — those layers live on ``User.notify``
    and ``card["notify"]`` respectively and are applied by
    :func:`scitex_cards._notify.resolve_recipients` at resolve time.

    Per-user prefs schema (``User.notify``)
    ---------------------------------------
    A mapping with the optional keys in :data:`USER_NOTIFY_KEYS`::

        {
            "mute":  ["committed", "pushed"],   # never notify me for these
            "watch": ["pulled"],                # also notify me for these
                                                # on cards I'm a member of
        }

    * ``mute``  — opt-OUT: drop this user from a recipient set for the listed
      event types, even if a role they hold matched the global default.
    * ``watch`` — opt-IN: add this user to the recipient set for the listed
      event types, but ONLY on cards where they are already a member
      (owner / assignee / collaborator / subscriber). A watch never makes a
      non-member a recipient.

    Per-card overrides schema (``card["notify"]``)
    ----------------------------------------------
    A mapping with the optional keys in :data:`CARD_NOTIFY_KEYS`::

        {
            "events": {"commented": ["subscribers"]},  # override roles here
            "add":    {"merged": ["u_abc", "alice"]},  # force-include users
            "mute":   ["u_xyz", "bob"],                # force-exclude users
        }

    * ``events`` — replace the role list for an event ON THIS CARD (wins over
      the global default for that event).
    * ``add``    — force-include specific user ids / names for an event.
    * ``mute``   — force-exclude specific user ids / names for EVERY event on
      this card (the card's final word — beats ``add`` and ``watch``).

    Attributes
    ----------
    rules : dict[str, list[str]]
        The merged ``{event_type: [role, ...]}`` map.
    """

    rules: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    def roles_for(self, event_type: str) -> list[str]:
        """Return the global role list for ``event_type`` (``[]`` if unset)."""
        return list(self.rules.get(event_type, []))


# Re-exported so siblings can ``from ._rules import EVENT_TYPES`` without a
# second import of the events module (keeps the validation set in one place).
__all__ = [
    "CARD_NOTIFY_KEYS",
    "DEFAULT_NOTIFY_RULES",
    "EVENT_TYPES",
    "NOTIFY_SIDECAR_NAME",
    "NotifyConfig",
    "NotifyConfigError",
    "ROLE_ASSIGNEE",
    "ROLE_COLLABORATORS",
    "ROLE_OWNER",
    "ROLE_SUBSCRIBERS",
    "USER_NOTIFY_KEYS",
    "VALID_ROLES",
]

# EOF
