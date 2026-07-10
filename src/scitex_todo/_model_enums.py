#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Closed enum sets for the task model — split out of ``_model.py``.

Extracted verbatim from ``_model.py`` (the 512-line-cap split, see
``GITIGNORED/REFACTORING.md`` while in progress / the git history of that
file once complete). ``_model.py`` re-exports every name here so existing
``from ._model import VALID_STATUSES`` etc. keep resolving unchanged.
"""

from __future__ import annotations

# Valid task statuses. ``goal`` marks a north-star objective (rendered gold);
# the rest are ordinary execution states.
VALID_STATUSES: tuple[str, ...] = (  # hook-bypass: line-limit
    "goal",
    "pending",
    "in_progress",
    "blocked",
    "done",
    "deferred",
    "failed",
    # ``cancelled`` = GitHub "closed as not planned": a TERMINAL/closed
    # state distinct from ``done`` (completed successfully) and ``failed``
    # (attempted, did not succeed). A cancelled card is CLOSED — it drops
    # out of every open/actionable/stale/backlog view exactly like ``done``
    # (see TERMINAL_STATUSES in _throughput.py, the is_overdue closed-set
    # below, and _LIVENESS_NONRUNNABLE in handlers/graph.py). It does NOT
    # satisfy a dependency: a cancelled upstream leaves dependents blocked,
    # so RESOLVED_STATUSES in _runnable.py stays {"done", "goal"}.
    "cancelled",
)

# Valid task kinds — north-star pillars #1 (compute state) + #4 (operator
# pain "where am I the blocker"). A row with ``kind: compute`` represents
# an external compute job whose status is updated by an automated writer
# (see tasks/proj-scitex-todo-compute-state-deps/README.md). A row with
# ``kind: decision`` represents an operator/agent decision that other tasks
# can ``depends_on`` — when the decision-node's status flips to ``done``
# (the decision is made) the dependents auto-unblock via the existing dep-
# graph wire (no new machinery; the per-task adr.md is its body, 1:1).
# Other tasks use ``kind: task`` (the default, can be omitted). Extensible
# to ``"ci"`` etc. when task #15 wires GH-Actions rows.
#
# Closed validated set — fail-loud on unknown values per ADR-0002
# (a2a `2c7a431d`) and ADR-0003 (this PR; extending to "decision").
VALID_KINDS: tuple[str, ...] = (
    "task",
    "compute",
    "decision",
    # ``status`` — a non-actionable status-tracking card (e.g. the q-*
    # quality-CI status rows, one per fleet package). Carries one-liner
    # status notes (audit-debt counts, green flags) rather than a real
    # ToDo body. Per board card ``scitex-todo-relocate-q-status-tracking``
    # + lead a2a ``60a1a93d`` (operator direction): proceeding with
    # option (b) — keep the rows on the board but mark them with this
    # axis so the board's filter UI (separate frontend PR) can hide them
    # from the actionable default lens. ORTHOGONAL to ``blocker`` /
    # ``status`` (the row-status enum); the validator does NOT cross-imply
    # any compute-field constraints — ``kind: status`` is just a flag.
    "status",
)


# Valid `blocker` values — operator TG 9522 + 9524, lead a2a
# `4691b114` / `c839c59b` / `2bd37bd2` / `554435df`. The operator's exact
# pain: "I cannot tell what is waiting on ME." A blocked task can be stuck
# on different things; each gets a different signal on the board.
#
# Operator's enumeration (verbatim, TG 9524):
#   compute            (計算リソース)      — waiting on a kind=compute row to finish
#   dep                (依存)              — waiting on another task (explicit form of the implicit
#                                            dep-edge case; useful when the dep is the *concept*
#                                            even if no edge id is known yet)
#   operator-decision  (ユーザー判断)      — waiting on the operator to decide; this is the LOUD
#                                            variant the operator opens the UI to find. Usually
#                                            paired with kind=decision rows but the enums are
#                                            ORTHOGONAL (a kind=task can also be blocker=
#                                            operator-decision if it's waiting on a decision that
#                                            hasn't been promoted to its own kind=decision node
#                                            yet).
#   agent-wait         (他エージェント待ち) — waiting on a specific agent action (e.g. "lead to
#                                            write the ADR-0007 entry"). Distinct from `dep`
#                                            because the blocker is a *human/agent action*, not
#                                            a graph-edge dep.
#
# Closed validated set per ADR-0004 (this PR) — same fail-loud pattern as
# VALID_KINDS / VALID_STATUSES: an unknown value raises with the bad value
# and the valid set in the error message. Extensible by editing this tuple
# — closed-in-the-typo sense, open-in-the-variant sense.
#
# Allowed ONLY when `status == "blocked"`: setting a `blocker` on a non-
# blocked row is a config error (the row isn't blocked, so naming a blocker
# is meaningless). Validator raises with "set status: blocked or remove the
# blocker field" — same shape as the compute-fields-only-on-kind=compute
# rule from ADR-0002.
VALID_BLOCKERS: tuple[str, ...] = (
    "compute",
    # ``"dependency"`` is the canonical spelling per operator co-design
    # (TG 9667, lead a2a `6d9b6073`). ``"dep"`` is the legacy alias from
    # ADR-0004's first cut; the validator accepts BOTH during a
    # deprecation window and normalizes on write (`_normalize_blocker`).
    # Once existing tasks.yaml stores are swept, ``"dep"`` drops out.
    "dependency",
    "dep",
    "operator-decision",
    "agent-wait",
    # ``"none"`` is the explicit "no specific blocker named" value
    # (vs the soft-degrade case where the field is absent on a blocked
    # row). Lets the operator set blocker:none in a Resolve flow to
    # mean "I looked, no blocker" — distinct from "we haven't named
    # one yet." Operator co-design TG 9667.
    "none",
)


# Canonical → legacy alias normalization for the blocker enum.
# Used by Task.from_dict to flip incoming ``"dep"`` → ``"dependency"``
# on read, so the in-memory dataclass always carries the canonical
# spelling. The validator still accepts both spellings (deprecation
# window); only the dataclass normalizes.
_BLOCKER_ALIASES: dict[str, str] = {
    "dep": "dependency",
}


# EOF
