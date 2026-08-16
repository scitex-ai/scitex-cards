#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Owner SSOT — the SINGLE resolver for "who owns this card".

One tiny, dependency-free module so EVERY layer that means "the card's
owner" resolves it the SAME way: the comment relay, the notify dispatcher /
``_push`` targeting, the board card-render owner display, the board
grouping / lanes, and the timeline grouping.

Why a standalone module (not a method on ``_model.Task`` or a helper buried
in ``_store``): both the pure store (``_store.py``) and the Django/handlers
layer need it, and both ``_model.py`` and ``_store.py`` are already over the
512-line cap — a focused single-responsibility module is importable from
either side with NO circular-import risk and NO further bloat of the giant
modules.

Operator mandate (2026-06-26, constitution rule 2 "fail fast and fail loud,
NO silent fallbacks"): a comment on a card that had an ``assignee`` but no
``agent`` reached NOBODY because the relay/notify targeted the raw ``agent``
field ONLY and silently no-op'd when empty; the board render + grouping read
the raw ``agent`` too, so an assignee-only card showed a BLANK owner and fell
into a retired fallback lane. The fix is one canonical owner rule used
everywhere, with NO silent fallback.
"""

from __future__ import annotations

from typing import Any


def card_owner(card: Any) -> str | None:
    """Resolve a card's OWNER — the canonical ``agent or assignee`` rule.

    Returns the owning user's name (stripped, non-empty), or ``None`` when
    the card has NEITHER an ``agent`` NOR an ``assignee`` — an owner-less
    card. Callers MUST treat ``None`` as a loud signal (the board surfaces it
    in an "unassigned" lane; the comment relay returns an ``error:no-owner``
    result instead of silently no-op'ing) rather than papering over it with a
    fallback identity.

    Mirrors the rule the C4 notify dispatcher already encodes
    (``_notify._resolver.card_role_members`` owner role = ``agent`` falling
    back to ``assignee``): ``agent`` is the operator-co-designed owner field;
    ``assignee`` is the legacy owner field that
    :func:`scitex_cards._store.add_task` / ``reassign_task`` keep in lock-step
    with ``agent`` so every reader agrees on the owner.

    Tolerant of any mapping (a task dict, a ``Task.to_dict()`` round-trip); a
    non-mapping yields ``None``.

    NOTE (identity canonicalisation): this returns the RAW owner string on
    purpose — it is the DISPLAY / grouping key rendered verbatim across the
    board (card render, lanes, timeline). Canonicalising here would silently
    rewrite every displayed owner and is deliberately NOT done. The identity
    collapse happens downstream at the RESOLUTION seams instead
    (:func:`scitex_cards._users.resolve_user` and the notify recipient
    resolver both route through
    :func:`scitex_cards._users.canonical_identity`), so drifted owners still
    resolve to one user without changing what the board shows. A future,
    deliberate display-canonicalisation pass (once the fleet is fully
    registered) could route this through ``canonical_identity`` too — left as
    a CARD to keep this change small and low-risk.
    """
    if not isinstance(card, dict):
        return None
    return (card.get("agent") or card.get("assignee") or "").strip() or None


__all__ = ["card_owner"]

# EOF
