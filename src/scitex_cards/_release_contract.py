#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cards release-stage promotion contract — Internal → Dogfooding → Stable → Public.

Own-ledger #224 ("only public after enough internal dogfooding") and #225
("decide the public-release conditions"), the Cards portions of which live in
the package itself, not only in prose. This module is that decision made
MACHINE-CHECKABLE: a fixed stage order, the explicit readiness criteria that
gate each promotion, and the function that enforces them.

WHY A CONTRACT AND NOT A FLAG
------------------------------
The manifest's historical ``wip`` boolean was the closest thing to a release
state — and it was the whole defect: one bit cannot express "internal but not
yet dogfooded" vs "dogfooding but not yet stable" vs "stable, cleared for
public". A single flag is a gate configured so it cannot fail; the four-stage
ladder is the honest model of what "ready to ship to the public" actually
means, and each rung has named criteria so a promotion is a decision against a
checklist rather than a hope.

THE RULES THE ENFORCEMENT ENCODES
---------------------------------
1. :data:`RELEASE_STAGES` is a strict ORDER. A stage may only be reached from
   the stage immediately before it; you cannot promote internal -> stable, or
   internal -> public, skipping the intermediate rungs.
2. Every promotion is gated on :func:`criteria_met` for the SOURCE stage: the
   readiness criteria listed for that stage must all be satisfied (a dict of
   criterion -> True) before the next stage is admitted.
3. :data:`PUBLIC` is the terminal "cleared for public" stage — and NOTHING in
   this module makes Cards public. The manifest declares the CURRENT stage
   (:func:`current_stage`, read from ``manifest.json``); the contract only
   validates transitions, it never advances the stage. Whether Cards is public
   is an operator decision recorded by editing ``release_stage`` in the
   manifest, and that edit must be defensible against :data:`RELEASE_STAGES`.

Cards-specific task/board LOGIC (graph, timeline, DM, matrix) is UNAFFECTED by
this contract — the contract is a deployment-readiness declaration, consumed by
the hub launcher / release process, not by the board's rendering. That
separation (domain logic vs infrastructure declaration) is the same principle
as ``_user_scope``.

NO EXTERNAL DEPENDENCY: this module imports only the standard library, so the
contract is testable on a lean install and cannot drag a Django or hub import
into a release-gate check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# --- the fixed stage order (a strict ladder, not a set) ----------------------

INTERNAL = "internal"
DOGFOODING = "dogfooding"
STABLE = "stable"
PUBLIC = "public"

#: The promotion order. ``RELEASE_STAGES[i+1]`` may only be reached from
#: ``RELEASE_STAGES[i]``. PUBLIC is the terminal "cleared" stage.
RELEASE_STAGES: tuple[str, ...] = (INTERNAL, DOGFOODING, STABLE, PUBLIC)

#: The manifest field that records Cards' CURRENT stage. Declared by the leaf
#: package (the manifest), read by the hub / release process. The hub does not
#: get to invent Cards' stage — the manifest is the source, mirroring how
#: ``scope`` and ``pip_package`` are read from here.
MANIFEST_STAGE_KEY = "release_stage"

# --- the readiness criteria that gate each promotion -------------------------
#
# Each stage maps to the criteria that must hold to LEAVE it (promote forward).
# These are the "public-release conditions" #225 asks to be decided, expressed
# per-rung so a promotion is a checklist, not a jump. Criteria are NAMES only
# (the contract checks that each is satisfied=True); whether a criterion is in
# fact satisfied is measured by evidence (tests, deployment observation, an
# operator sign-off) — this module deliberately does not re-derive those facts
# from prose, it makes the checklist STRUCTURALLY enforceable.

RELEASE_CRITERIA: Dict[str, Dict[str, str]] = {
    INTERNAL: {
        "internal_visibility": "Cards is hidden from public users (internal/staff-only).",
        "operator_chrome_gated": "Internal/operator terminology + schema/ADR/store-path are not exposed to a public viewer.",
        "no_app_specific_user_model": "Cards uses the common identity; it owns no app-specific auth user model.",
    },
    DOGFOODING: {
        "dogfood_period_met": "Cards has been used internally for the agreed dogfood period, with real fleet traffic.",
        "known_gaps_tracked": "The open gaps (e.g. SPA/wire store_path, route gate) are tracked, not silently dropped.",
    },
    STABLE: {
        "core_workflows_stable": "create/list/comment/resolve/reopen work end-to-end against a real store, covered by tests.",
        "error_handling_typed": "Store failures are typed (store_absent 404, public_summary) and never a bare 500.",
        "persistence_durable": "The canonical store is durable (postgres) and a wrong/empty store is refused, not invented.",
    },
    # PUBLIC is terminal: nothing gates leaving it (there is no stage past it).
    PUBLIC: {},
}


class ReleaseStageError(ValueError):
    """Raised when a promotion violates the stage contract.

    A ValueError subclass on purpose: a bad stage is a DATA problem in the
    manifest, and the same ``except Exception`` that a release tool uses for
    "cannot proceed" should catch it — a promotion that raises something
    obscure would be swallowed or misreported.
    """


def stage_index(stage: str) -> int:
    """Position of ``stage`` in :data:`RELEASE_STAGES`, or -1 if not a stage."""
    try:
        return RELEASE_STAGES.index(stage)
    except ValueError:
        return -1


def is_valid_stage(stage: str) -> bool:
    return stage in RELEASE_STAGES


def criteria_for(stage: str) -> Dict[str, str]:
    """The readiness criteria that gate leaving ``stage`` (empty for terminal)."""
    if not is_valid_stage(stage):
        raise ReleaseStageError(
            f"unknown release stage {stage!r}; valid: {', '.join(RELEASE_STAGES)}"
        )
    return dict(RELEASE_CRITERIA[stage])


def criteria_met(stage: str, satisfaction: Optional[Dict[str, bool]] = None) -> bool:
    """True when every criterion for ``stage`` is satisfied.

    ``satisfaction`` maps criterion-name -> bool (the evidence verdict). A
    criterion with no verdict is NOT met — a missing answer is a failure, not a
    pass, so an un-evidenced rung cannot be promoted. An empty criterion set
    (the terminal PUBLIC stage) is trivially met.
    """
    sat = satisfaction or {}
    return all(bool(sat.get(name)) for name in criteria_for(stage))


def next_stage(stage: str) -> Optional[str]:
    """The stage that comes after ``stage``, or None if ``stage`` is terminal."""
    i = stage_index(stage)
    if i < 0:
        raise ReleaseStageError(
            f"unknown release stage {stage!r}; valid: {', '.join(RELEASE_STAGES)}"
        )
    if i + 1 < len(RELEASE_STAGES):
        return RELEASE_STAGES[i + 1]
    return None


def assert_promotion(
    from_stage: str,
    to_stage: str,
    satisfaction: Optional[Dict[str, bool]] = None,
) -> str:
    """Validate that a promotion ``from_stage -> to_stage`` is allowed.

    Returns ``to_stage`` when the promotion is legal; raises
    :class:`ReleaseStageError` when it is not. The rules:

    * ``to_stage`` must be a known stage and ``from_stage`` a known stage.
    * You may only promote ONE rung forward (internal->dogfooding,
      dogfooding->stable, stable->public) — never skip a rung.
    * The SOURCE stage's readiness criteria must all be satisfied
      (:func:`criteria_met`); an un-evidenced rung cannot be promoted.

    Nothing here ADVANCES Cards' stage — it only decides whether a proposed
    promotion is defensible. Making Cards public is the operator's edit to
    ``manifest.json``'s ``release_stage``, validated against these rules.
    """
    for s in (from_stage, to_stage):
        if not is_valid_stage(s):
            raise ReleaseStageError(
                f"unknown release stage {s!r}; valid: {', '.join(RELEASE_STAGES)}"
            )
    i, j = stage_index(from_stage), stage_index(to_stage)
    if j != i + 1:
        raise ReleaseStageError(
            f"promotion {from_stage!r} -> {to_stage!r} skips a rung; the order is "
            f"{' -> '.join(RELEASE_STAGES)} (promote one stage at a time)"
        )
    if not criteria_met(from_stage, satisfaction):
        missing = [n for n in criteria_for(from_stage) if not (satisfaction or {}).get(n)]
        raise ReleaseStageError(
            f"cannot promote {from_stage!r} -> {to_stage!r}: unsatisfied readiness "
            f"criterion(s) {missing!r} — a promotion is a checklist, not a jump"
        )
    return to_stage


def _manifest_path() -> Path:
    # The app manifest lives in the _django subpackage (the embedded Django app
    # declares itself); _release_contract is package-level but reads that one.
    return Path(__file__).resolve().parent / "_django" / "manifest.json"


def current_stage() -> str:
    """Cards' CURRENT declared stage, read from the package manifest.

    Reads ``manifest.json``'s ``release_stage`` (the leaf-package declaration,
    #48's "specified by the leaf package side"). Falls back to
    :data:`INTERNAL` when the field is absent — the safe direction is to treat
    an undeclared stage as internal, never as public.
    """
    try:
        data = json.loads(_manifest_path().read_text(encoding="utf-8"))
    except Exception:
        return INTERNAL
    stage = data.get(MANIFEST_STAGE_KEY)
    return stage if is_valid_stage(stage) else INTERNAL


def stage_is_public() -> bool:
    """Whether Cards is CURRENTLY declared public (the manifest, not this code).

    This is a READ of the declaration; the contract never writes it. A release
    gate checks this to decide whether Cards may be exposed; it does not flip
    it.
    """
    return current_stage() == PUBLIC


__all__ = [
    "INTERNAL",
    "DOGFOODING",
    "STABLE",
    "PUBLIC",
    "RELEASE_STAGES",
    "RELEASE_CRITERIA",
    "MANIFEST_STAGE_KEY",
    "ReleaseStageError",
    "stage_index",
    "is_valid_stage",
    "criteria_for",
    "criteria_met",
    "next_stage",
    "assert_promotion",
    "current_stage",
    "stage_is_public",
]
