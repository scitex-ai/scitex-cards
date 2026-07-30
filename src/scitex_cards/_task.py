#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Store constants, error types, and the ``Task`` model.

The BASE module of the ``_model`` split (see GITIGNORED/REFACTORING.md):
it imports nothing else from this package, so ``_deadlines`` / ``_validate``
/ ``_model`` can all import from it without a cycle. Pure move.
"""

from __future__ import annotations

# Valid task statuses. ``goal`` marks a north-star objective (rendered gold);
# the rest are ordinary execution states.
# ``pending`` was ABOLISHED 2026-07-10 by operator directive: "pending という
# タスクがある。存在してはならない状態である。" A card in ``pending`` carried NO
# decision — it was the dataclass default, so 406 of 1100 cards (37%) had silently
# accumulated there and rotted. Every open card must now state its disposition:
# ``in_progress`` (being worked), ``blocked`` (+ a blocker naming the gate),
# or ``deferred`` (can be worked, consciously not now).
ABOLISHED_STATUSES: dict[str, str] = {
    "pending": (
        "status 'pending' was abolished 2026-07-10 — a card must carry a "
        "decision. Choose: 'in_progress' if you are working it, 'blocked' "
        "with a blocker naming the gate, or 'deferred' if it can wait."
    ),
}

VALID_STATUSES: tuple[str, ...] = (  # hook-bypass: line-limit
    "goal",
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
#   dep                (依存)              — waiting on another task (explicit form of the implicit  # noqa: E501
#                                            dep-edge case; useful when the dep is the *concept*  # noqa: E501
#                                            even if no edge id is known yet)
#   operator-decision  (ユーザー判断)      — waiting on the operator to decide; this is the LOUD  # noqa: E501
#                                            variant the operator opens the UI to find. Usually  # noqa: E501
#                                            paired with kind=decision rows but the enums are  # noqa: E501
#                                            ORTHOGONAL (a kind=task can also be blocker=  # noqa: E501
#                                            operator-decision if it's waiting on a decision that  # noqa: E501
#                                            hasn't been promoted to its own kind=decision node  # noqa: E501
#                                            yet).
#   agent-wait         (他エージェント待ち) — waiting on a specific agent action (e.g. "lead to  # noqa: E501
#                                            write the ADR-0007 entry"). Distinct from `dep`  # noqa: E501
#                                            because the blocker is a *human/agent action*, not  # noqa: E501
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
    # Once existing stores are swept, ``"dep"`` drops out.
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


class TaskValidationError(ValueError):
    """Raised when a task store fails structural validation."""


class StaleStoreError(RuntimeError):
    """The store changed between your read and your write — reload and retry.

    Raised by :func:`save_tasks` when the caller passed
    ``expected_generation`` and the on-disk store no longer matches it.
    Writing anyway would silently discard every mutation the other writer
    made since your read (the 2026-07-10 bulk-migration incident: raw
    ``load_tasks → mutate → save_tasks`` scripts ate two concurrent sac
    writes because nothing tied the write to the read it was based on).
    """


class StoreShrinkRefusedError(RuntimeError):
    """A doc-level write would remove rows the store already has. Refused.

    THE THIRD BOARD WIPE (P0, 2026-07-21): the ``tasks`` table went
    2170 -> 18 because a full-suite pytest run's ambient environment
    happened to name the LIVE store, and a stale/replacement document
    missing almost every row was persisted straight over it. The
    snapshot rail already refused to SNAPSHOT a collapsed count
    (2026-07-19, ``_cli/_db.py``'s ``_SHRINK_REFUSAL_RATIO`` guard) —
    this is that guard's sibling on the LIVE WRITE path, which no
    earlier incident had covered.

    Operator ruling — 一度書いたものは消えない, "a written card never
    disappears" — sets the bar at ANY row disappearing, not a ratio: a
    doc-level write is refused the moment it is missing even ONE id the
    store currently has (and that id is not named in the write's
    ``deleted_ids``, the one legitimate single-card removal path — see
    :func:`_is_tombstoned` for why a normal ``delete_task`` no longer
    even uses that path). There is no small-store exemption, because
    there is nothing to be generous about: growing a store from zero
    never removes anything, so the check never fires on it.

    Remedy: if the shrink is genuinely intentional (a deliberate bulk
    archive or migration, never an ordinary card write), re-run the
    call with ``allow_shrink=True``.
    """


#: ``_log_meta`` key marking a row as TOMBSTONED — removed via
#: :func:`scitex_cards._store_lifecycle.delete_task`, never a physical
#: SQL ``DELETE``. See :func:`_is_tombstoned`.
TOMBSTONE_KEY = "deleted_at"


def _is_tombstoned(task: dict) -> bool:
    """True if ``task`` was removed via :func:`delete_task`'s tombstone.

    2026-07-21 P0 (third board wipe) replaced physical delete with an
    in-place mark, per operator ruling 一度書いたものは消えない ("a
    written card never disappears"): the row survives forever, its
    ``status`` flips to ``cancelled``, and ``_log_meta.deleted_at``
    records when. Presence of ``deleted_at`` — not merely
    ``status == "cancelled"`` — is the marker: a card can be
    legitimately cancelled by a human/agent without ever being deleted,
    and conflating the two would hide real cancelled work from the
    board.
    """
    log_meta = task.get("_log_meta")
    return bool(isinstance(log_meta, dict) and log_meta.get(TOMBSTONE_KEY))


def _find_live_task(tasks: list[dict], task_id: str) -> dict | None:
    """First NON-tombstoned task with id == ``task_id``, else ``None``.

    THE shared "does this id exist" lookup for every ordinary write verb
    (complete / resolve / reopen / reassign / rescore / comment / update
    / delete / set_edge / set_collaborator / set_subscriber) operating
    on the raw read-modify-write ``tasks`` list. A tombstoned row is
    retained on disk forever (see :func:`_is_tombstoned`) but must
    behave as ABSENT to all of them — mutating a deleted card would
    silently resurrect it. ``restore_task`` is the ONE verb whose job is
    exactly that resurrection, so it does its own raw (tombstone-
    INCLUSIVE) scan instead of calling this helper.
    """
    for t in tasks:
        if t.get("id") == task_id and not _is_tombstoned(t):
            return t
    return None


# ---------------------------------------------------------------------------
# Task dataclass — SINGLE schema source (ADR-0007, quality-hygiene PR)
# ---------------------------------------------------------------------------
#
# The dataclass IS the canonical schema. It feeds:
#   - the validator (`_validate_tasks`)
#   - the board UI render contract (ADR-0006 — every card field maps to one
#     dataclass attribute)
#   - the Gitea field-map (HANDOFF.md — every dataclass field maps to a
#     Gitea-issue field via label / milestone / assignee / body)
#   - the future README-frontmatter pivot (HANDOFF.md SSoT-layout)
#
# Heuristic pinned in HANDOFF.md: ANY schema evolution touches the dataclass
# FIRST; validator + adapters follow mechanically. Two-sources-of-truth is
# what this dataclass is collapsing.
#
# Back-compat: existing dict-style consumers (handlers/graph.py, _store.py,
# the MCP layer) keep working — `Task.from_dict()` reads any historical
# task shape, `Task.to_dict()` round-trips to a dict the existing writers
# can consume. The migration to attribute-style access is incremental per
# the operator's "no big-bang" rule.


from dataclasses import dataclass, field  # noqa: E402
from dataclasses import fields as _dc_fields  # noqa: E402


@dataclass(slots=True)
class Task:
    """Canonical task shape — the single schema source for scitex-todo.

    Field layout follows the operator's co-design (TG 9667, lead a2a
    `6d9b6073`): the operator's named fields come first
    (`id` / `title` / `task` / `project` / `host` / `created_at` /
    `goal`), then the UI-driving + workflow fields (`status` / `agent` /
    `last_activity` / `blocker` / `pr_url` / `issue_url`), then the
    graph-wiring fields preserved from pre-PR-#52 (`depends_on` / `blocks`
    / `parent` / `priority` / `note` / `comments`), then the kind
    discriminator + compute metadata (ADR-0002 / 0003), then the legacy
    shared-fleet additive fields (`scope` / `assignee` / `_log_meta`).

    Construction: prefer :meth:`Task.from_dict` for loading from a YAML
    row — it handles legacy spellings (e.g. `blocker: "dep"` → canonical
    `"dependency"`), missing fields (filled with the dataclass default),
    and ignores unknown keys defensively (so a forward-compat YAML with
    a future field doesn't crash an older loader).

    Persistence: :meth:`to_dict` round-trips to the dict shape the
    ruamel writer in :func:`save_tasks` expects. Fields with default
    values (None / empty list / empty dict) are OMITTED from the dict
    so the YAML stays compact. Required fields (id, title) are always
    emitted.

    See ADR-0007 in ``docs/adr/`` for the rationale + the migration
    plan from the legacy dict-style API.
    """

    # --- operator's core fields (TG 9667) ----------------------------------
    id: str
    title: str
    # `task` is the operator's "1-line current task" — the BIG text on the
    # board card. Distinct from `title` (which is the short scannable label)
    # and from `note` (which is free-form markdown detail). Optional during
    # the deprecation window so legacy rows that only carry `title` keep
    # loading; the FE falls back `task or title` when rendering the BIG
    # text. Once dogfooded, agents start populating `task` and the FE prefers
    # it.
    task: str | None = None
    project: str | None = None  # directory / repo basename
    # `repo` = the git repository slug the card's work lands in (e.g.
    # ``scitex-todo``). Used by add_task / list_tasks and historically rode
    # ``**extras`` without a dataclass field — a confirmed latent bug: a row
    # carrying ``repo`` survived on disk but never round-tripped through the
    # Task dataclass (from_dict dropped it as an unknown key). Promoted to a
    # first-class OPTIONAL field in the SQLite-migration S0 (RFC #348 Q4);
    # pure-additive, defaults None so to_dict omits it when unset.
    repo: str | None = None
    host: str | None = None  # where the work happens (operator co-design TG 9667)
    created_at: str | None = None  # ISO-8601 UTC; emit at insert
    goal: str | None = None  # WHY (parent-goal text); rendered as 🎯 line on card

    # --- deadline / scheduled (P4, lead approved 2026-06-12) --------------
    # Both ISO-8601 (date "2026-06-15" or datetime "2026-06-15T18:00+09:00").
    # `deadline` = when the task MUST be done; `scheduled` = when work
    # should START. Mirrors org-mode DEADLINE: / SCHEDULED: lines and
    # Gitea's `due_date`. Validator rejects empty strings and rejects
    # `deadline < scheduled` (deadline cannot precede start). FE prefers
    # the field over the existing title-parsed date when both are
    # present; absent field → fall back to title parse (back-compat).
    # See ADR-0007 follow-up + the P4 design a2a.
    #
    # A DEADLINE IS A VIEW, NEVER A NOTIFIER. It drives the `overdue`
    # filter (:func:`is_overdue`), the board date-pill / sort, and the
    # org export — and NOTHING else. No sweep, digest or nudge reads
    # `deadline` / `deadlines`: the delivery surface (`_reminders`,
    # `_stale_active`, `_backlog_triage`, `_delivery/*`) keys ONLY on
    # `last_activity` (falling back to `created_at`). A deadline
    # arriving — recurring or not — fires nothing. And a RECURRING
    # deadline never even goes overdue (the repeater rolls the next
    # occurrence into the future), so it reaches NEITHER rail. To BE
    # nudged, keep the card open and owned: the stale-active sweep
    # nudges the owner of any in_progress/blocked card untouched past
    # its threshold, and the backlog sweep does the same for untouched
    # `deferred` cards.
    # (hook-bypass: line-limit — board_v3.html refactor still queued.)
    deadline: str | None = None
    scheduled: str | None = None
    # P4 PR3 (lead-approved 2026-06-12) — multiple deadlines. When set,
    # `deadline` must be UNSET (mutual exclusion); the loader computes a
    # synthetic `deadline = <min next-occurrence>` so the existing FE
    # date-pill / sort / overdue paths keep working canonically. Each
    # entry follows the same wire shape as `deadline`: ISO-8601 with an
    # optional " +Nu" / " ++Nu" org repeater suffix. Empty list rejected
    # (use the absent form). See `_parse_deadline_or_raise` for the
    # accepted forms.
    deadlines: list[str] | None = None

    # --- lead-added: drives UI color + blocker views (TG 9667) -------------
    status: str = "deferred"  # current canonical = VALID_STATUSES (7-value);
    # the operator's 4-value enum (working/waiting/done/blocked)
    # is mapped IN THE FE renderer for now, not in the
    # schema. See ADR-0007 Consequences for the
    # deferred 7→4 schema migration.
    agent: str | None = None  # owning agent (distinct from `assignee` legacy field)
    # `group` is the logical CLUSTER OF AGENTS this task belongs to —
    # the parallelism-engine dispatcher (TRACK 1, lead a2a `74db4f2d`,
    # 2026-06-14) uses it to ask "what's runnable now in group <G>"
    # so the operator's "independent (dep-free) tasks run concurrently
    # across groups" model works. Free-form non-empty string when
    # present; absent = ungrouped. Distinct concept from `_groups.py`'s
    # project-cluster `Group` dataclass (that's a VIEWER aggregation
    # for the board's column collapser; this is a DISPATCH concept on
    # the task itself).
    group: str | None = None
    last_activity: str | None = (
        None  # ISO-8601 UTC; recency drives green/amber/red coloring
    )
    blocker: str | None = (
        None  # one of VALID_BLOCKERS or absent; only on status=blocked
    )
    pr_url: str | None = None  # optional GH/Gitea PR link
    issue_url: str | None = None  # optional GH/Gitea issue link

    # --- graph wiring (preserved from pre-#52) -----------------------------
    depends_on: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    parent: str | None = None
    priority: int | None = None
    note: str | None = None
    comments: list[dict] = field(default_factory=list)

    # --- roles & notification (P1, ADR-0009) -------------------------------
    # `collaborators` = agents/humans involved beyond the single `agent`
    # (assignee); `subscribers` = the notify list (default = creator +
    # collaborators, always unsubscribable). PERSISTENT fields: previously
    # collaborators were recomputed from comment authors at event-time and
    # subscribers did not exist. Absent / None → empty list (back-compat).
    collaborators: list[str] = field(default_factory=list)
    subscribers: list[str] = field(default_factory=list)
    # `created_by` = the USER (agent or human; user.kind=agent) who created
    # the card, captured at insert by add_task from the same author chain
    # comment authorship resolves ($SCITEX_TODO_AGENT_ID → $USER → "unknown").
    # Back-compat: ABSENT on legacy rows — readers fall back to the earliest
    # comment author, else "—". Optional non-empty string when present.
    # (hook-bypass: line-limit — _model.py split still queued.)
    created_by: str | None = None

    # --- kind discriminator + compute metadata (ADR-0002 / 0003) -----------
    kind: str | None = None  # one of VALID_KINDS or absent (defaults to "task")
    job_id: str | None = None
    command: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    # --- standing / parked cards (raised by scitex-writer 2026-07-13) ------
    # `parked` = WHY this card is deliberately standing, in free text. Its
    # PRESENCE (non-empty) exempts the card from the backlog nudge and from
    # auto-expiry. Its ABSENCE means an untouched deferred card is exactly what
    # the sweep takes it for: abandoned.
    #
    # It is a REASON, never a boolean, and that IS the design. The backlog sweep
    # fires on `deferred` + untouched, a predicate that cannot separate a card
    # nobody got to from a north-star umbrella whose real work lives in its
    # children. For the umbrella the nudge is unanswerable BY CONSTRUCTION —
    # there is nothing to start and no gate to clear, so "untouched" is its
    # steady state. Both escapes available before this field were lies: flip it
    # to `in_progress` (an umbrella is not work in flight), or leave periodic
    # no-op comments purely to bump `last_activity` (gaming the sweep, and it
    # buries the real history under noise).
    #
    # A bare boolean would have been a MUTE BUTTON, and a mute button is how an
    # alarm dies: everything gets muted, and then the sweep stops catching the
    # abandoned cards it exists for. Requiring the reason makes a card PAY for
    # its exemption by saying why, in writing, where the next reader sees it.
    # Whitespace-only is NOT a park (see :func:`_backlog_triage.park_reason`):
    # a park with no stated reason is precisely the abandonment the sweep should
    # still catch.
    #
    # Parking hides a card from the NUDGE, never from the BOARD. `list_tasks`
    # and every view are untouched, so this cannot silently remove work from the
    # view someone relies on.
    parked: str | None = None

    # --- legacy shared-fleet additive fields (Phase-1 SSoT) ---------------
    scope: str | None = None
    assignee: str | None = (
        None  # legacy; `agent` is the operator-co-designed replacement
    )
    _log_meta: dict | None = None  # opaque writer-side event stamps

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        """Construct from a stored task dict.

        - Unknown keys are silently dropped (forward-compat).
        - Missing keys fill with the dataclass default.
        - Legacy blocker spellings (e.g. ``"dep"``) normalize to canonical
          (``"dependency"``) — see ``_BLOCKER_ALIASES``.

        Does NOT raise on schema violations — that's :func:`_validate_tasks`'s
        job. Defensive construction so legacy / forward / partial rows can
        always be read; validation is a separate check.
        """
        valid_names = {f.name for f in _dc_fields(cls)}
        # `comments` default needs the list factory so legacy missing-comments
        # rows construct cleanly (None would break list-of-mapping invariants
        # downstream).
        kwargs: dict[str, object] = {}
        for k, v in d.items():
            if k not in valid_names:
                continue
            if k == "blocker" and isinstance(v, str):
                v = _BLOCKER_ALIASES.get(v, v)
            kwargs[k] = v
        # comments / depends_on / blocks / collaborators / subscribers:
        # replace None with the empty default so downstream code can iterate
        # without isinstance(.., None) checks.
        for list_field in (
            "comments",
            "depends_on",
            "blocks",
            "collaborators",
            "subscribers",
        ):
            if kwargs.get(list_field) is None:
                kwargs.pop(list_field, None)
        return cls(**kwargs)  # type: ignore[arg-type]

    def to_dict(self) -> dict:
        """Round-trip to a plain dict suitable for the ruamel writer.

        Fields with default values (None / empty list / empty dict) are
        OMITTED so the YAML stays compact. The validator-REQUIRED fields
        (`id`, `title`, `status`) always emit — including when `status`
        equals the `"pending"` default — because a row missing `status`
        would fail `_validate_tasks` on the next load. Required fields
        survive the to_dict-then-from_dict round-trip even at defaults.
        """
        result: dict[str, object] = {}
        for f in _dc_fields(self):
            value = getattr(self, f.name)
            # Always-emit: validator-required fields.
            if f.name in ("id", "title", "status"):
                result[f.name] = value
                continue
            # Default-equal values are omitted (keeps YAML compact).
            default = (
                f.default if f.default is not f.default_factory else f.default_factory()
            )  # type: ignore[misc]
            if value == default:
                continue
            # Empty containers: omit so the YAML stays compact.
            if isinstance(value, (list, dict)) and not value:
                continue
            result[f.name] = value
        return result
