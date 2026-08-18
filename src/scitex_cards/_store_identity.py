#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve WHO is writing a card — and refuse every non-answer.

THE ONE RESOLVER. `created_by` on a card, the `by=` on a completion, the
author of a comment: all three land here. Extracted from ``_store`` for the
reason that module already gives for its other guard — "the guard, the
incident history behind each of its checks, and its tests belong together
rather than buried among the mutation helpers".

WHAT COUNTS AS A NON-ANSWER, and why each is refused rather than defaulted:

    ""                          nothing resolved
    "unknown"                   the old lenient chain's sentinel
    "${SCITEX_CARDS_AGENT_ID}"  a substitution that did not happen
    "$SCITEX_CARDS_AGENT_ID"    the same, in the form .mcp.json never expands

A silent default would put a WRONG author on a card, which is worse than a
write that stops and says why: the board's whole purpose is knowing who is
doing what, and a confidently mis-attributed card is unrecoverable in a way
that a refused write is not (constitution rule 2, "fail fast and fail loud,
NO silent fallbacks").

THE PLACEHOLDER INCIDENT (2026-07-19), which the third and fourth rows exist
for. sac injected only the pre-rename env name while a migrated ``.mcp.json``
referenced the new one, so the literal text ``${SCITEX_CARDS_AGENT_ID}``
reached this resolver and was written straight into ``created_by``. Measured
on the live board 2026-08-18, still there:

    tasks total                                 5198
    created_by holding a literal '$'              15   (agent/assignee: 0)
    written after the 2026-07-21 repair claim      0

All 15 fall in 2026-07-18T23:01 .. 2026-07-19T01:20 — the original window, so
the defect has not recurred; those rows were brought back by a restore after
the repair, and nobody re-measured. Their authorship is now unrecoverable:
nothing records who was running.

THE ASYMMETRY THIS CLOSES. The incident card put it exactly: "dm_send FAILS
LOUD, add_task FAILS SILENT". :func:`.._channel_identity.resolve_agent_id` has
rejected a leading ``$`` since that incident; THIS door — the one that writes
``created_by`` — never did. So the loud half was fixed and the silent half
went on writing placeholders. Ask #2 of that card ("NEVER persist an
unexpanded ``${...}``. Validate the resolved identity before write") is what
this module finally implements.

WHY THE PREDICATE IS BROADER THAN THE STORE-TARGET ONE.
:func:`.._store_url.is_unexpanded_variable` matches only ``${`` and ``$(``,
because a bare ``$`` is legal in a POSIX FILENAME and rejecting it would
refuse real paths. An AGENT ID has no such excuse — no agent is named ``$``
anything — so a leading ``$`` in ANY form is refused here, matching the
sibling identity door rather than the store-target one. Two predicates on
purpose; the domain decides, not the symbol.
"""

from __future__ import annotations

import os

from ._model import TaskValidationError

#: Env var name carrying the agent's identity. Used as the default
#: `completed_by` when :func:`complete_task` doesn't get an explicit `by=`.
ENV_AGENT = "SCITEX_CARDS_AGENT_ID"

#: previous name of :data:`ENV_AGENT`. Renamed 2026-07-02. We fail LOUD (never
#: silently honour it) if it is still set, so a stale export can't quietly
#: mis-attribute a write — the operator must migrate to the new name.
ENV_AGENT_DEPRECATED = "SCITEX_CARDS_AGENT"


def _reject_deprecated_agent_env() -> None:
    """Fail loud if the old ``SCITEX_CARDS_AGENT`` var is still set.

    No silent fallback: a leftover export of the old name is a configuration
    error the operator must fix, not something we quietly translate.
    """
    if os.environ.get(ENV_AGENT_DEPRECATED) is not None:
        raise RuntimeError(
            f"{ENV_AGENT_DEPRECATED} was renamed to {ENV_AGENT}; "
            f"unset the old var (it is no longer honoured)."
        )


def _reject_unexpanded_creator(resolved: str) -> None:
    """Refuse an id that is still the SHAPE of an unperformed substitution.

    See the module docstring for the 2026-07-19 incident and for why this is
    deliberately broader than the store-target predicate.
    """
    if not resolved.startswith("$"):
        return
    raise TaskValidationError(
        f"creator is an unexpanded placeholder ({resolved!r}) — the launcher "
        "passed the literal text instead of the value, so this card would "
        "record a substitution that never happened as its author. In "
        '.mcp.json use the brace form "SCITEX_CARDS_AGENT_ID": '
        '"${SCITEX_CARDS_AGENT_ID}" (Claude Code does not expand bare '
        '"$VAR"), or pass created_by=/by= explicitly.'
    )


def _resolve_creator_or_raise(arg: "str | None") -> str:
    """Resolve a card CREATOR — FAIL LOUD when it cannot be resolved.

    Precedence: an explicit ``created_by``/``by=`` arg → ``$SCITEX_CARDS_AGENT_ID``.
    Deliberately does NOT fall back to ``getpass.getuser()`` / ``"unknown"``:
    a card whose creator can't be resolved must not be born. This is the SSOT
    resolver — :func:`_default_agent` (actor / author for completion &
    comments) delegates here so both share the identical fail-loud behaviour.

    Raises
    ------
    RuntimeError
        When the deprecated ``$SCITEX_CARDS_AGENT`` is still exported (renamed
        away — see :func:`_reject_deprecated_agent_env`).
    TaskValidationError
        When the creator resolves to empty, the ``"unknown"`` sentinel, or an
        unexpanded ``$``-placeholder, each with an ACTIONABLE hint.
    """
    _reject_deprecated_agent_env()
    resolved = (arg or os.environ.get(ENV_AGENT) or "").strip()
    _reject_unexpanded_creator(resolved)
    if not resolved or resolved == "unknown":
        raise TaskValidationError(
            "creator unresolved — set SCITEX_CARDS_AGENT_ID=<your-agent> or pass "
            "created_by=/by= (creator+assignee are mandatory; no silent "
            "fallback to a blank/'unknown' creator; see constitution)."
        )
    return resolved


def _default_agent(arg: "str | None") -> str:
    """Resolve an ACTOR/AUTHOR — FAIL LOUD when it cannot be resolved.

    Identical in behaviour to :func:`_resolve_creator_or_raise`; it simply
    delegates, keeping a single source of truth while preserving this public
    name for the completion/comment callers.
    """
    return _resolve_creator_or_raise(arg)


__all__ = [
    "ENV_AGENT",
    "ENV_AGENT_DEPRECATED",
    "_default_agent",
    "_reject_deprecated_agent_env",
    "_reject_unexpanded_creator",
    "_resolve_creator_or_raise",
]

# EOF
