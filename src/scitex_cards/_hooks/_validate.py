#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-loud validation of inbound hook-event payloads.

Split out of the original flat ``_hooks.py`` (C2 refactor). Holds the
accepted-kinds set, the :class:`HookEventError`, and :func:`event_validate`.

C2 adds the canonical ``card-event`` kind (C1's :mod:`scitex_cards._events`
envelope) to the validator: a producer that emits a typed
:class:`scitex_cards._events.Event` via ``emit`` now passes validation
instead of being rejected as an unknown kind. The inner ``type`` is
validated against :data:`scitex_cards._events.EVENT_TYPES` — reused, NOT
re-listed here — and ``card_id`` is required (fail-loud).
"""

from __future__ import annotations

from typing import Any

from .._events import CARD_EVENT_KIND, EVENT_TYPES

#: Accepted event kinds. Producers that emit any other ``kind`` are
#: rejected at the validate step — fail-loud per Phase-0 doctrine.
#:
#:   - ``push``         git push event (SAC's push-hook).
#:   - ``done``         PR merge event (dev's GitHub Action).
#:   - ``card-message`` operator/agent comment on a card. Emitted
#:                      automatically by :func:`scitex_cards._store.comment_task`
#:                      so any comment landing — via the chat panel,
#:                      the ``scitex-cards comment`` CLI verb, or the
#:                      MCP ``comment_task`` tool — fans out through
#:                      the bus. SAC's consumer a2a-delivers to the
#:                      card's owner + collaborators (lead a2a
#:                      ``1e8e33d0``, 2026-06-14).
#:   - ``unblock``      a card that others ``depends_on`` flipped to
#:                      ``done``, so its dependents are now runnable.
#:                      Emitted by :func:`scitex_cards._store.complete_task`
#:                      (the active-unblock DRIVE, ADR-0009). Carries the
#:                      ``unlocker_id`` (the finished card) and ``card_ids``
#:                      (the newly-unblocked dependents). The built-in
#:                      handler records a ``[unblocked]`` ROUTE comment on
#:                      each; SAC's consumer notifies their assignee +
#:                      subscribers ("your task is now unblocked").
#:   - ``card-event``   the C1 canonical typed event envelope
#:                      (:mod:`scitex_cards._events`). The inner ``type``
#:                      is one of :data:`scitex_cards._events.EVENT_TYPES`
#:                      and ``card_id`` is required. There is no built-in
#:                      handler yet (C5) — it flows straight to plugins.
VALID_EVENT_KINDS = frozenset(
    {"push", "done", "card-message", "unblock", CARD_EVENT_KIND}
)


class HookEventError(ValueError):
    """A producer sent a malformed event payload.

    Raised by :func:`event_validate` on shape violations. The HTTP +
    CLI wrappers translate this to a 400 / non-zero exit.
    """


def event_validate(event: Any) -> dict:
    """Fail-loud validation of an inbound event payload.

    Returns the normalized event dict on success (string-coerces the
    ``card_ids`` entries, defaults missing optional fields to None).
    Raises :class:`HookEventError` on any structural violation.
    """
    if not isinstance(event, dict):
        raise HookEventError(f"event must be a JSON object, got {type(event).__name__}")
    kind = event.get("kind")
    if kind not in VALID_EVENT_KINDS:
        raise HookEventError(
            f"unknown event kind {kind!r}; must be one of {sorted(VALID_EVENT_KINDS)}"
        )

    def _require(field: str) -> str:
        val = event.get(field)
        if not isinstance(val, str) or not val:
            raise HookEventError(
                f"{kind} event: {field!r} must be a non-empty string (got {val!r})"
            )
        return val

    out: dict[str, Any] = {"kind": kind}
    if kind == "push":
        out["repo"] = _require("repo")
        out["branch"] = _require("branch")
        out["commit_sha"] = _require("commit_sha")
        out["author"] = event.get("author")
        out["message"] = event.get("message")
        # `trigger` (optional) distinguishes a local `commit` (post-commit
        # hook) from a `push` (pre-push hook) so the built-in handler can
        # emit the matching canonical card-event (`committed` vs `pushed`).
        # Carried through verbatim; absent/unknown means "push" (the
        # historical behaviour). Never required — purely additive.
        out["trigger"] = event.get("trigger")
    elif kind == "done":
        out["repo"] = _require("repo")
        pr_number = event.get("pr_number")
        if not isinstance(pr_number, int):
            raise HookEventError(
                f"done event: 'pr_number' must be an int (got {pr_number!r})"
            )
        out["pr_number"] = pr_number
        out["pr_url"] = _require("pr_url")
        out["author"] = event.get("author")
        out["merged_at"] = event.get("merged_at")
    elif kind == "card-message":
        # The card-id of the card the comment landed on. Required.
        out["card_id"] = _require("card_id")
        # Required body — the comment text itself. Validator pins
        # non-empty so a producer can't fan an empty notification.
        out["body"] = _require("body")
        out["author"] = event.get("author")  # optional but ~always set
        # Owner = the agent the card is assigned to. Nullable when the
        # card has no agent/assignee field — SAC's handler can still
        # fan to collaborators in that case.
        out["owner"] = event.get("owner")
        # Collaborators = everyone else SAC should fan to. Coerce to
        # list[str] of non-empty strings; empty list is valid.
        collaborators = event.get("collaborators") or []
        if not isinstance(collaborators, list):
            raise HookEventError(
                f"card-message event: 'collaborators' must be a list "
                f"(got {type(collaborators).__name__})"
            )
        norm_collab: list[str] = []
        for c in collaborators:
            if not isinstance(c, str) or not c:
                raise HookEventError(
                    f"card-message event: 'collaborators' entry "
                    f"{c!r} is not a non-empty string"
                )
            norm_collab.append(c)
        out["collaborators"] = norm_collab
        # subscribers — optional notify list (ADR-0009). Same shape as
        # collaborators; empty/absent is valid (the consumer falls back
        # to owner + collaborators).
        subscribers = event.get("subscribers") or []
        if not isinstance(subscribers, list):
            raise HookEventError(
                f"card-message event: 'subscribers' must be a list "
                f"(got {type(subscribers).__name__})"
            )
        norm_subs: list[str] = []
        for s in subscribers:
            if not isinstance(s, str) or not s:
                raise HookEventError(
                    f"card-message event: 'subscribers' entry "
                    f"{s!r} is not a non-empty string"
                )
            norm_subs.append(s)
        out["subscribers"] = norm_subs
        out["created_at"] = event.get("created_at")
        # `card-message` does NOT use `card_ids` (singular `card_id`
        # above); return early so the trailing card_ids normalisation
        # block doesn't add an empty list to the payload.
        return out
    elif kind == CARD_EVENT_KIND:
        # Canonical C1 envelope (scitex_cards._events.Event.to_dict()).
        # Reuse EVENT_TYPES — do NOT re-list the closed set here. The
        # inner `type` must be a known canonical event type, and
        # `card_id` is required (fail-loud). There is no built-in
        # card-event handler in `dispatch_event` yet (C5) — a validated
        # card-event flows straight to plugins, so we return the
        # payload mostly verbatim, normalising only the fields the bus
        # contract pins.
        ev_type = event.get("type")
        if ev_type not in EVENT_TYPES:
            raise HookEventError(
                f"card-event: unknown inner type {ev_type!r}; must be one of "
                f"{sorted(EVENT_TYPES)}"
            )
        out["type"] = ev_type
        out["card_id"] = _require("card_id")
        # Pass every other envelope field through untouched so plugins
        # (and the future C5 built-in handler) see the full C1 shape.
        for k, v in event.items():
            if k not in out:
                out[k] = v
        return out
    elif kind == "unblock":
        # The card that just flipped to done (the "unlocker"). Required
        # so consumers can say *who* unblocked the dependents.
        out["unlocker_id"] = _require("unlocker_id")
        out["author"] = event.get("author")
        out["unblocked_at"] = event.get("unblocked_at")
        # `card_ids` here = the newly-unblocked dependents; normalised
        # by the shared block below (NOT returned early).
    # card_ids — optional for push/done/unblock; coerce to list[str] of
    # non-empty strings. Anything else is malformed.
    card_ids = event.get("card_ids") or []
    if not isinstance(card_ids, list):
        raise HookEventError(
            f"{kind} event: 'card_ids' must be a list (got {type(card_ids).__name__})"
        )
    norm_cards: list[str] = []
    for c in card_ids:
        if not isinstance(c, str) or not c:
            raise HookEventError(
                f"{kind} event: 'card_ids' entry {c!r} is not a non-empty string"
            )
        norm_cards.append(c)
    out["card_ids"] = norm_cards
    return out


# EOF
