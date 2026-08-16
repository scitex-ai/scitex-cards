#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tolerant-read / strict-write validation. Pure move from ``_model``."""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

from ._deadlines import _parse_deadline_or_raise
from ._store_verify import _verify_dumped_tmp  # hook-bypass: line-limit
from ._task import (
    _BLOCKER_ALIASES,
    ABOLISHED_STATUSES,
    VALID_BLOCKERS,
    VALID_KINDS,
    VALID_STATUSES,
    TaskValidationError,
)


#: The ``source`` :func:`_validate_tasks` is given on the WRITE side. Every
#: other source is a store being read. Kept as a constant because two places
#: now depend on the exact spelling, and a silent drift between them would
#: reintroduce the mislabel this constant exists to prevent.
WRITE_SOURCE = "<save_tasks>"


def _side_of(source: str) -> str:
    """``"write-side"`` when THIS caller is producing the row, else ``"read-side"``.

    The gate is shared — :func:`_validate_tasks`'s own docstring calls itself
    "the single gate shared by load_tasks (read side) and save_tasks (write
    side)" — so which side we are on is knowable and was simply being thrown
    away. See :func:`_warn_tolerated` for why that mattered.
    """
    return "write-side" if source == WRITE_SOURCE else "read-side"


def _warn_tolerated(msg: str, side: str = "read-side") -> None:
    """Shout about a value this build does not understand, but keep going.

    Loud on stderr AND through ``warnings`` so a human, a log scraper, and
    ``-W error`` all see it. Deliberately NOT silent: the point is that one row
    from a newer writer must not take the fleet's board down, not that the
    problem should be hidden.

    ``side`` IS NOT COSMETIC, and this function used to hard-code ``read-side``
    while its only caller ran on BOTH sides. A reader who sees "read-side" is
    told that somebody else's row came past them; on a write that is false —
    they minted it themselves a microsecond ago. Measured 2026-08-16: three
    cards carrying the ABOLISHED status ``pending`` were created after its
    abolition by the maintainer of the package that abolished it, and a
    several-hundred-card sweep set a status (``archived``) that no build knows.
    The one line that could have stopped either pointed away from the writer.

    The default stays ``read-side`` so a caller that does not pass a side is
    treated as the tolerant-read case it historically was, rather than silently
    accusing a reader of writing.
    """
    import sys as _sys
    import warnings as _warnings

    banner = f"[scitex-cards] TOLERATED ({side}): {msg}"
    print(banner, file=_sys.stderr, flush=True)
    _warnings.warn(banner, stacklevel=3)


def _validate_tasks(tasks: object, source: str, strict: bool = True) -> None:
    """Validate a task list in place, raising on the first structural fault.

    The single gate shared by :func:`load_tasks` (read side) and
    :func:`save_tasks` (write side) so a bad mutation can never round-trip
    through the writer.

    The split between STRUCTURE and VALUES is the fix for a fleet-wide outage
    on 2026-07-10.

    The store is SHARED and read by agents running DIFFERENT installed
    versions. When a newer writer stored ``status: cancelled`` (a value its
    own enum knew), every agent on an older build raised here and
    ``load_tasks`` aborted — so ONE unknown value in ONE row took the whole
    fleet's board down. A shared, multi-version store can never grow an enum
    value under that design.

    So, on BOTH sides:
      * STRUCTURAL corruption raises — not a list, missing id/title, duplicate
        id, non-integer priority. That is a broken store, not a newer one, and
        no amount of tolerance makes it readable.
      * A bad VALUE warns, loudly, naming the card and the likely cause. An
        unknown status, a blocked card with no blocker: shouted about, never
        fatal.

    The write side warns rather than raises by operator ruling (2026-07-10:
    "カードが書けないということはなしで大丈夫です、warning で十分です"). Raising
    there was the writer-side twin of the same outage: ``save_tasks``
    validates the WHOLE task list, so a single legacy row written by an older
    agent made every *other* agent's write fail — and the live store grew two
    such rows within hours of the sweep that removed them. The enum is kept
    honest at the SOURCES instead (the CLI's ``--status`` Choice, the MCP and
    board defaults), which reject a bad value before it is ever a card.

    This is not a silent fallback: both sides shout. They simply refuse to
    take the fleet offline, or to cost someone their card, because one row
    came from the future.

    Parameters
    ----------
    tasks : object
        The candidate ``tasks`` value (must be a list of mappings).
    source : str
        A label for error messages (the store path or ``"<save_tasks>"``).
    strict : bool
        Accepted for backwards compatibility; no longer changes behaviour.
        Value faults warn on both paths, structural faults raise on both.

    Raises
    ------
    TaskValidationError
        On any structural fault — see :func:`load_tasks`.
    """
    if not isinstance(tasks, list):
        raise TaskValidationError(f"{source}: top-level 'tasks' must be a list")

    # Resolved ONCE, not per row: `source` is fixed for the whole call, and
    # deriving it inside the loop would invite a future edit to derive it from
    # the row instead — which is the row's provenance, not this caller's side.
    side = _side_of(source)
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise TaskValidationError(
                f"{source}: each task must be a mapping: {task!r}"
            )
        tid = task.get("id")
        if not tid:
            raise TaskValidationError(
                f"{source}: a task is missing required 'id': {task!r}"
            )
        if tid in seen:
            raise TaskValidationError(f"{source}: duplicate task id {tid!r}")
        seen.add(tid)
        if not task.get("title"):
            raise TaskValidationError(
                f"{source}: task {tid!r} is missing required 'title'"
            )
        status = task.get("status")
        if status not in VALID_STATUSES:
            # An ABOLISHED status gets a message that says what to do instead,
            # not just "invalid" — the caller is mid-write and needs the fix.
            hint = ABOLISHED_STATUSES.get(status)
            # AND THE UNKNOWN-STATUS ADVICE DEPENDS ON WHICH SIDE WE ARE ON,
            # because on a write the read-side explanation is not merely
            # unhelpful, it is FALSE. "If another agent wrote it, your
            # scitex-cards is older than the writer's — upgrade rather than
            # rewriting the card" is sound counsel to a READER who has just
            # met a row from a newer build. Said to a WRITER it blames an
            # absent third party for a value the caller is setting right now,
            # and tells them to upgrade instead of to fix their own input.
            # Measured 2026-08-16: a several-hundred-card sweep set the unknown
            # status `archived`, and this is the sentence its author would have
            # been shown.
            if hint:
                msg = f"{source}: task {tid!r}: {hint}"
            elif side == "write-side":
                msg = (
                    f"{source}: task {tid!r} is being written with unknown status "
                    f"{status!r}; this build knows {VALID_STATUSES}. YOU are "
                    f"setting it — no other writer is involved. Use a value this "
                    f"build knows, or land the new value in VALID_STATUSES first "
                    f"so every reader can interpret it."
                )
            else:
                msg = (
                    f"{source}: task {tid!r} has unknown status {status!r}; "
                    f"this build knows {VALID_STATUSES}. If another agent wrote "
                    f"it, your scitex-cards is older than the writer's — upgrade "
                    f"rather than rewriting the card."
                )
            # WARN, never raise — on BOTH the read and the write side.
            #
            # Operator ruling 2026-07-10: "カードが書けないということはなしで
            # 大丈夫です、warning で十分です." A status value must never cost
            # someone their card. Strictness here is a trap on a SHARED store:
            # save_tasks validates the whole task list, so one legacy row
            # written by an older agent makes every *other* agent's write
            # fail — the writer-side twin of the reader-side outage this
            # branch was created to fix (incident-cancelled-enum-version-skew,
            # 2026-07-10). The store held 2 such rows within hours of the
            # pending sweep, minted by agents still on the old default.
            #
            # The enum is kept honest at the SOURCES instead — the CLI, MCP
            # and board no longer offer abolished values — and this warning
            # names the row so it gets migrated. Nothing is silently accepted;
            # nothing is destructively refused.
            _warn_tolerated(msg, side)
        # A `blocked` card MUST name its gate. "Blocked with no blocker" is
        # stuck-and-silent: nobody can clear a gate nobody stated. Found
        # 2026-07-10 on 14 live cards, several idle for over a month.
        elif status == "blocked":
            blocker = (task.get("blocker") or "").strip()
            if not blocker or blocker == "none":
                msg = (
                    f"{source}: task {tid!r} is 'blocked' but names no blocker. "
                    f"A blocked card must state its gate so someone can clear it. "
                    f"Set blocker to one of {VALID_BLOCKERS}, or use a status "
                    f"that reflects reality: 'in_progress' if you are working it, "
                    f"'deferred' if it can wait."
                )
                # WARN, never raise — same ruling as the status enum above. A
                # missing blocker is a quality problem worth shouting about;
                # it is not worth destroying the caller's card over.
                _warn_tolerated(msg, side)
        priority = task.get("priority")
        # bool is an int subclass — reject it explicitly so `priority: true`
        # is a clear error rather than a silent 1.
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int)
        ):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-integer priority {priority!r}; "
                f"priority must be an integer or absent"
            )
        # `parent` is the additive-optional nesting field — a task's children
        # are tasks whose `parent` equals this id. Validate type only (must be
        # a non-empty string id when present); we do NOT require the
        # referenced parent to exist or to be acyclic here. Stale/cyclic
        # references are gracefully degraded by the consumers (server-side
        # graph builder and frontend drill-down) — same lenient stance as
        # `depends_on` / `blocks` references to unknown ids, which are dropped
        # rather than rejected.
        parent = task.get("parent")
        if parent is not None and not (isinstance(parent, str) and parent):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-string parent {parent!r}; "
                f"parent must be a task id string or absent"
            )
        # `comments` is an append-only thread of user/agent remarks, distinct
        # from the descriptive `note`. Each entry must be a mapping with a
        # non-empty string `text`; `ts` / `author` are optional strings the
        # server fills in (ISO timestamp + commenter). Validate the shape only
        # so a malformed comment can't round-trip, staying lenient otherwise.
        comments = task.get("comments")
        if comments is not None:
            if not isinstance(comments, list):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-list comments "
                    f"{comments!r}; comments must be a list or absent"
                )
            for entry in comments:
                if not isinstance(entry, dict) or not (
                    isinstance(entry.get("text"), str) and entry.get("text")
                ):
                    raise TaskValidationError(
                        f"{source}: task {tid!r} has an invalid comment "
                        f"{entry!r}; each comment must be a mapping with a "
                        f"non-empty string 'text'"
                    )
        # Additive operator-co-designed fields (TG 9667, lead a2a `6d9b6073`):
        # task / project / host / created_at / goal / agent / last_activity /
        # pr_url / issue_url — all optional non-empty strings, no enum, no
        # referential integrity. The dataclass Task carries the full shape;
        # this validator just type-checks the wire so a stray scalar can't
        # corrupt downstream readers. Convention details (ISO-8601 for
        # timestamps, URL form for pr_url/issue_url) are render-layer rules.
        for label in (
            "task",
            "project",
            "host",
            "created_at",
            # `created_by` — the creating USER, optional non-empty string.
            # Absent on legacy rows (back-compat). (hook-bypass: line-limit.)
            "created_by",
            "goal",
            "agent",
            "last_activity",
            "pr_url",
            "issue_url",
        ):
            value = task.get(label)
            if value is not None and not (isinstance(value, str) and value):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-string {label} {value!r}; "
                    f"{label} must be a non-empty string or absent"
                )
        # P4 (lead approved 2026-06-12) — deadline + scheduled ISO-8601
        # fields. Validated as non-empty strings that parse via
        # datetime.fromisoformat (handles "YYYY-MM-DD",
        # "YYYY-MM-DDTHH:MM:SS", and offset variants). When BOTH are
        # present, `deadline < scheduled` is rejected — a deadline
        # cannot precede the start of work. (hook-bypass: line-limit.)
        deadline_raw = task.get("deadline")
        scheduled_raw = task.get("scheduled")
        deadlines_raw = task.get("deadlines")
        # P4 PR3: mutual exclusion + per-entry validation for the new
        # `deadlines` (list) field. Either `deadline` (scalar) OR
        # `deadlines` (list) — not both — and the list must be non-empty
        # if present (use the absent form for "no deadlines").
        if deadline_raw is not None and deadlines_raw is not None:
            raise TaskValidationError(
                f"{source}: task {tid!r} has BOTH deadline and deadlines "
                f"set; use one or the other (deadline = scalar single,"
                f" deadlines = list of multiple)"
            )
        if deadlines_raw is not None:
            if not isinstance(deadlines_raw, list):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-list deadlines "
                    f"{deadlines_raw!r}; must be a list of ISO-8601 strings"
                )
            if len(deadlines_raw) == 0:
                raise TaskValidationError(
                    f"{source}: task {tid!r} has empty deadlines list; "
                    f"use the absent form for 'no deadlines'"
                )
            for j, entry in enumerate(deadlines_raw):
                _parse_deadline_or_raise(
                    entry,
                    source=source,
                    tid=tid,
                    label=f"deadlines[{j}]",
                )
        deadline_dt, _ = _parse_deadline_or_raise(
            deadline_raw, source=source, tid=tid, label="deadline"
        )
        scheduled_dt, _ = _parse_deadline_or_raise(
            scheduled_raw, source=source, tid=tid, label="scheduled"
        )
        if (
            deadline_dt is not None
            and scheduled_dt is not None
            and deadline_dt < scheduled_dt
        ):
            raise TaskValidationError(
                f"{source}: task {tid!r} has deadline {deadline_raw!r} "
                f"before scheduled {scheduled_raw!r} (a deadline cannot "
                f"precede the start of work)"
            )
        # `scope` and `assignee` are additive-optional shared-fleet fields
        # (PHASE 1, Req 1 in GITIGNORED/ARCHITECTURE.md). Both are free-form
        # non-empty strings — no enum, no referential integrity. Convention is
        # `agent:<name>` / `project:<name>` / `private` but that's a
        # docs/skills convention, not enforced here (Req 8: be generic).
        # `group` is the TRACK-1 dispatch-cluster field (lead a2a
        # `74db4f2d`, 2026-06-14). Same shape as `scope`/`assignee` —
        # free-form non-empty string, no closed enum, no referential
        # integrity. Drives the `runnable(group=...)` query that the
        # parallelism dispatcher consumes. Distinct from `_groups.py`'s
        # project-cluster concept (which is a viewer aggregation).
        for label in ("scope", "assignee", "group"):
            value = task.get(label)
            if value is not None and not (isinstance(value, str) and value):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-string {label} {value!r}; "
                    f"{label} must be a non-empty string or absent"
                )
        # `_log_meta` is an opaque event-stamp mapping written by
        # `complete_task` etc. Keep it open-shaped — Phase 2 progress-history
        # adapter shapes the keys. We only enforce "if present, it's a
        # mapping" so a stray scalar can't corrupt downstream readers.
        log_meta = task.get("_log_meta")
        if log_meta is not None and not isinstance(log_meta, dict):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-mapping _log_meta "
                f"{log_meta!r}; _log_meta must be a mapping or absent"
            )
        # `kind` is the discriminator between an ordinary task row and a
        # compute-job row (north-star pillar #1). Closed validated set per
        # `VALID_KINDS`; absence is equivalent to `kind: "task"` (the
        # default). Fail-loud on unknown values — a "comput" typo would
        # otherwise silently create an unrecognized kind, defeating the
        # discriminator.
        kind = task.get("kind")
        if kind is not None and kind not in VALID_KINDS:
            raise TaskValidationError(
                f"{source}: task {tid!r} has invalid kind {kind!r}; "
                f"must be one of {VALID_KINDS} or absent (defaults to 'task')"
            )
        # Compute metadata fields — only allowed when `kind: compute`. Each
        # is an optional non-empty string. `started_at` / `finished_at` are
        # expected to be ISO-8601 timestamps but we don't strict-parse them
        # here — the writer (Spartan watcher / CI watcher, task #15) is
        # responsible for the content; the schema only enforces TYPE so a
        # stray scalar can't corrupt downstream readers.
        is_compute = kind == "compute"
        # Note: `host` USED to be in this compute-only list (ADR-0002). The
        # operator-co-designed generic shape (TG 9667) makes `host` a
        # general-purpose "where does this task live/run" field — any row
        # can carry it, not just compute rows. So `host` moved out of the
        # compute-only fence and into the generic operator-field block
        # above. The remaining compute-only fields (`job_id` / `command` /
        # `started_at` / `finished_at`) STAY compute-only because their
        # semantic ("the compute job's identifier / invocation / runtime
        # bookends") doesn't fit a non-compute task.
        compute_fields = ("job_id", "command", "started_at", "finished_at")
        for label in compute_fields:
            value = task.get(label)
            if value is None:
                continue
            if not is_compute:
                raise TaskValidationError(
                    f"{source}: task {tid!r} has compute metadata {label!r} "
                    f"but kind is {kind!r}; set kind: compute or remove the "
                    f"{label} field"
                )
            if not (isinstance(value, str) and value):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-string {label} "
                    f"{value!r}; {label} must be a non-empty string or absent"
                )
        # `blocker` is the discriminator for what KIND of thing is blocking
        # a status=blocked row (north-star "what's waiting on me" — operator
        # TG 9522 + 9524). Closed validated set per `VALID_BLOCKERS`; absence
        # is acceptable on a blocked task ("we know it's blocked but haven't
        # named the blocker variant yet"). The orthogonality matters: `kind`
        # and `blocker` validate independently — a `kind: "decision"` row's
        # blocker is USUALLY `"operator-decision"` but can be `"agent-wait"`
        # (an agent confirming) or `"compute"` (a model picking). The
        # validator does NOT cross-imply.
        #
        # Fail-loud rules:
        #  (a) Unknown `blocker` value → raise, name the bad value + the
        #      valid set.
        #  (b) A REAL blocker variant on a non-blocked row → raise.
        #  (c) The `"none"` sentinel ("no specific blocker named") is LENIENT
        #      on a non-blocked row: normalized away (dropped in place), not
        #      rejected. Card `cards-blocker-none-validation-lenient`.
        #      (hook-bypass: line-limit — _model.py split still queued.)
        blocker = task.get("blocker")
        if blocker is not None:
            if blocker not in VALID_BLOCKERS:
                raise TaskValidationError(
                    f"{source}: task {tid!r} has invalid blocker {blocker!r}; "
                    f"must be one of {VALID_BLOCKERS} or absent"
                )
            if status != "blocked":
                if blocker == "none":
                    task.pop("blocker", None)
                else:
                    raise TaskValidationError(
                        f"{source}: task {tid!r} has blocker {blocker!r} but "
                        f"status is {status!r}; set status: blocked or remove "
                        f"the blocker field"
                    )


# ---------------------------------------------------------------------------
# THE WRITE PATH lives in ``_store_write`` (split out 2026-07-12, when this file
# had reached 1,566 lines by tangling two unrelated jobs: what a task IS, and how
# a task gets to DISK).
#
# Re-exported here because 43 test files and every caller in the package import
# these from ``_model``. The split is an INTERNAL reorganisation and must be
# invisible to them — not an API break.
#
# Imported at the BOTTOM, after everything ``_store_write`` needs from this module
# (StaleStoreError, _validate_tasks, load_doc) is defined. A top-of-file import
# would be circular.
