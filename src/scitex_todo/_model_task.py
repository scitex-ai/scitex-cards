#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``Task`` dataclass — split out of ``_model.py``.

Extracted verbatim from ``_model.py`` (the 512-line-cap split, see
``GITIGNORED/REFACTORING.md`` while in progress / the git history of that
file once complete). ``_model.py`` re-exports ``Task`` so existing
``from ._model import Task`` keeps resolving unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as _dc_fields

from ._model_enums import _BLOCKER_ALIASES  # hook-bypass: line-limit

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
    status: str = "pending"  # current canonical = VALID_STATUSES (7-value);
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

    # --- legacy shared-fleet additive fields (Phase-1 SSoT) ---------------
    scope: str | None = None
    assignee: str | None = (
        None  # legacy; `agent` is the operator-co-designed replacement
    )
    _log_meta: dict | None = None  # opaque writer-side event stamps

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        """Construct from a tasks.yaml dict.

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


# EOF
