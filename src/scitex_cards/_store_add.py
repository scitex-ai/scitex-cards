#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``add_task`` -- the INSERT half of the store's write surface.

Extracted from :mod:`scitex_cards._store_mutate` (PURE MOVE -- no behaviour
change), which re-exports ``add_task`` so ``from ._store_mutate import add_task``
and ``from ._store import add_task`` both keep working.

Split because `_store_mutate` had reached the repo's 512-line ceiling and
``update_task`` could not gain the ``expected_revision`` parameter scitex-dev's
reconciler needs -- five lines of headroom does not fit a signature, a docstring
and a call site. The two verbs were the obvious seam: they share only helpers
that already live in their own modules.

The owner/creator FAIL-LOUD rules and the ambient-store guard travel with the
function they protect, including the measured 2026-07-20 chain that produced
them.
"""

from __future__ import annotations

from pathlib import Path

from ._model import (
    TaskValidationError,
    _save_doc_unlocked,
    _store_lock,
)
from ._paths import refuse_ambient_store_creation as _refuse_ambient_store_creation
from ._store_clocks import _clear_completion_stamp_on_leaving_done
from ._store_enums import resolve_enum_clears as _resolve_enum_clears
from ._store_events import _emit_card_event
from ._store_list import _resolved_store
from ._store_target import resolve_store_target


def add_task(
    store: str | Path | None = None,
    *,
    id: str,
    title: str,
    status: str = "deferred",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    repo: str | None = None,
    created_by: str | None = None,  # hook-bypass: line-limit
    entry_points=None,
    **extras,
) -> dict:
    """Append a new task to ``store`` and persist via :func:`save_tasks`.

    Returns the inserted task mapping (a fresh dict, not the underlying
    YAML node) for convenient round-trip use by callers — the CLI prints
    it, the MCP tools serialize it as the JSON result.

    The ``**extras`` keyword catches operator-co-designed Task dataclass
    fields (``task`` / ``project`` / ``host`` / ``agent`` / ``goal`` /
    ``last_activity`` / ``blocker`` / ``pr_url`` / ``issue_url`` / ``kind``
    + compute metadata ``job_id`` / ``command`` / ``started_at`` /
    ``finished_at``) without an explosion of named parameters. ``None``
    values are dropped; non-``None`` values flow into the new task dict
    and the writer's validator gates closed enums (``status`` / ``kind``
    / ``blocker``) — typos raise ``TaskValidationError`` with the bad
    value and the valid set. Unknown keys are accepted at this layer
    (forward-compat); the validator decides whether they're shape-valid.

    Raises
    ------
    TaskValidationError
        On duplicate id or any other structural fault — `save_tasks`
        re-runs the full validation gate before touching disk.
    """
    from ._store import _read_write_doc, _resolve_creator_or_raise, _utc_now_iso

    # Same ONE rule as `update_task` (the sibling write path): a `""` on a
    # closed-enum field is a clear, so the key is simply NOT written on
    # insert — rather than written as `""` for the validator to reject. A
    # `status=""` is refused loudly (a card cannot be born status-less).
    _enum_in = _resolve_enum_clears({"status": status, **extras}, source="add_task")
    status = _enum_in.pop("status")
    extras = _enum_in
    resolved = _resolved_store(store)
    # A write against a store that does not exist must not INVENT one when
    # nothing named the path — that is how a decoy board accumulates and then
    # gets imported over the real one. See the guard's docstring for the
    # measured 2026-07-20 chain. An explicit `store` is the opt-in.
    #
    # The guard asks ONE question — "would this write MANUFACTURE a board?" —
    # and answers it with `path.exists()`. So it must be handed the store's real
    # LOCATION: the canonical database, which is what `save_tasks` writes and
    # what `init-store` provisions. `_resolved_store` returns a DISPLAY LABEL
    # (`<db_dir>/tasks.yaml`) that the store backend maps to that database —
    # good enough to name a store in a message, never a thing on disk. The YAML
    # tier was deleted (#512), so that label can NEVER exist, and passing it here
    # made the guard refuse unconditionally: every `add` failed for any agent
    # without $SCITEX_CARDS_DB while its own reads and updates succeeded, and the
    # error told you to run `init-store` — which did not help, because the file
    # it created was not the file being tested. Reported and reproduced by
    # scitex-ui on 0.17.7. Guard the database, not the label.
    # resolve_store_target, NOT resolve_db_path: the latter RAISES on a server
    # target, and it raises while evaluating this ARGUMENT — so every write
    # against PostgreSQL died here, before the guard it feeds ever ran. The
    # guard itself is fine with a DSN (it returns early; a server store cannot
    # be manufactured by a write). Handing it the target as written keeps the
    # existing behaviour byte-identical and stops the coercion happening on
    # the way in.
    _refuse_ambient_store_creation(resolve_store_target(store), store)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    # FAIL-LOUD on a missing/blank OWNER (operator mandate 2026-06-26,
    # constitution rule 2 "no silent fallbacks"). The OWNER is `assignee`
    # OR `agent` (lock-step below). A card with neither reached a blank
    # creator/assignee on the board + a fallback lane + an owner-less
    # comment relay that silently no-op'd — so an owner is REQUIRED.
    # `agent` arrives via **extras (operator-co-designed field).  # noqa: E501  # hook-bypass: line-limit
    _agent_in = extras.get("agent")
    _owner_in = assignee or _agent_in or ""
    _owner_in = _owner_in.strip() if isinstance(_owner_in, str) else _owner_in
    if not _owner_in:
        raise TaskValidationError(
            "assignee is required — pass assignee=<user> (or agent=<user>); "
            "creator+assignee are mandatory and an owner-less card is "
            "rejected (no silent fallback; see constitution)."
        )
    # RESOLVE the creator STRICTLY — raises a clear, actionable error when
    # it can't be resolved (blank / "unknown"). Done BEFORE any write so a
    # creatorless card never touches disk. (hook-bypass: line-limit)
    _creator = _resolve_creator_or_raise(created_by)
    new: dict = {"id": id, "title": title, "status": status}
    # D11 partial-fix (ADR-0008): auto-stamp ``created_at`` +
    # ``last_activity`` at insert time. ``created_at`` is the immutable
    # insert stamp; ``last_activity`` starts equal and ticks on every
    # subsequent successful update_task. Callers can override by passing
    # the field explicitly (e.g. importers replaying historical state).
    _stamp = _utc_now_iso()
    new["created_at"] = _stamp
    new["last_activity"] = _stamp
    # A card BORN blocked starts its blocked-check clock now, stated rather than
    # inferred. Without this the row carries no `blocked_at` and
    # `_blocked_age_hours` falls back to `created_at` — which returns the RIGHT
    # answer here, since for a card born blocked those two instants are the same.
    # That is correct-by-luck, and the luck is spent the moment the fallback
    # changes. Surfaced by grant 2026-07-30 while measuring why their blocker
    # change produced no stamp; the fallback is load-bearing enough that a path
    # relying on it silently should not exist.
    if status == "blocked":
        from ._stale.active_clocks import FIELD_BLOCKED_AT

        new[FIELD_BLOCKED_AT] = _stamp
    # `created_by` — the creating USER, STRICTLY resolved above (never a
    # blank/"unknown" placeholder). Drives the board detail ROLES section +
    # ADR-0009's creator auto-subscribe. (hook-bypass: line-limit)
    new["created_by"] = _creator
    if scope is not None:
        new["scope"] = scope
    # Keep `agent` + `assignee` in LOCK-STEP: whichever the caller supplied,
    # BOTH are stamped to the resolved owner so the board/relay/notify never
    # see an owner-less or half-owned card (mirrors `reassign_task`). The
    # `agent` half is set from **extras after this block; force it here so
    # an assignee-only OR agent-only call yields a fully-owned card. The
    # explicit `agent` extra (if any) is overwritten with the same owner.
    new["assignee"] = _owner_in
    extras["agent"] = _owner_in
    if priority is not None:
        new["priority"] = priority
    if parent is not None:
        new["parent"] = parent
    if note is not None:
        new["note"] = note
    if depends_on is not None:
        new["depends_on"] = list(depends_on)
    if blocks is not None:
        new["blocks"] = list(blocks)
    if repo is not None:
        new["repo"] = repo
    # Operator-co-designed surface (TG 9667) + compute metadata
    # (ADR-0002). Forwarded through **extras so callers don't have to
    # match a long explicit parameter list and the writer's validator
    # gates the closed enums.
    for key, value in extras.items():
        if value is None:
            continue
        new[key] = value

    # Lock for the FULL read-modify-write — without this, two concurrent
    # writers each load a stale snapshot and the second `save_tasks` call
    # silently clobbers the first writer's insert. See
    # tests/scitex_cards/test__store.py::test_two_concurrent_writers...
    # COLLECT the tolerated-value warnings this insert raises -- see the note at
    # the result assembly below, and `_tolerated` for what went unseen.
    from ._tolerated import collect as _collect_tolerated

    with _collect_tolerated(id) as _tolerated, _store_lock(resolved):
        # `missing_ok=True` is gone deliberately. It meant "an absent store
        # yields an empty doc", which against a database feeds an empty doc
        # into this read-modify-write and lets the subsequent save delete every
        # card absent from it. A missing database is a configuration error, not
        # an empty board — see `_read_write_doc`.
        doc, tasks = _read_write_doc(resolved)
        # WIP-validation gate (operator standing direction via lead a2a
        # `d99b8de6839d46e586e4ee692f43c1d9` + ``5acfbb5d0db44db8a7fa4f70c399d539``,
        # 2026-06-12). WARN to stderr at the limit, HARD REFUSE at 2x — EXCEPT
        # for the emergency band (``priority <= 1``), which is never gated and
        # is stamped with an audit comment when it lands over the cap. The whole
        # policy — thresholds, exemption, refusal text, audit stamp — lives in
        # ``_store_wip`` so it is readable in one screen; this is the same
        # focused-sibling pattern as ``_store_enums`` / ``_store_verify``.
        # See that module's header for the 2026-07-12 P0 the exemption closes.
        # (hook-bypass: line-limit)
        from ._store_wip import enforce_wip_gate

        enforce_wip_gate(new, tasks, now_iso=_stamp)
        tasks.append(new)
        # DECLARE THE ROW — same reasoning as `update_task` below, and the
        # enumeration is simpler: the only dict mutated here is `new`.
        # `enforce_wip_gate` READS `tasks` to count the agent's open cards but
        # its docstring is explicit that it "Mutates `new` in place (appends
        # the audit comment)", so no other card is written. Without this, a
        # single `add_task` re-asserts the caller's entire snapshot and reverts
        # anything committed between its read and its write.
        _save_doc_unlocked(doc, resolved, tasks=tasks, touched_ids=[new["id"]])
    # C5: emit a canonical `created` card-event AFTER the card is durably
    # persisted + the lock released. Fail-soft (the mutation already
    # succeeded). Actor = the resolved creating user (same chain that
    # `created_by` resolves through). (hook-bypass: line-limit)
    _emit_card_event(
        "card_created",
        id,
        actor=new.get("created_by"),
        store=resolved,
        entry_points=entry_points,
    )
    # Liveness (assignee-liveness feature): the creator just touched the
    # store → stamp its heartbeat; and surface the ASSIGNEE's liveness in
    # the result so the caller learns immediately if it just assigned to a
    # non-running agent. Both fail-soft (never break the durable write).
    from ._liveness import _assignee_liveness, _heartbeat

    _heartbeat(new.get("created_by"), resolved)
    result = dict(new)
    _liveness = _assignee_liveness(new.get("assignee"), resolved)
    if _liveness is not None:
        result["assignee_liveness"] = _liveness
    # Same shape as `assignee_liveness` above, same reason: a fact the caller
    # needs, attached rather than logged past them. THIS verb is where it
    # matters most -- all three `pending` cards created after that status was
    # abolished came through `add_task`, each firing a warning into the server's
    # stderr. Only when non-empty, so an ordinary insert is unchanged.
    if _tolerated:
        result["warnings"] = list(_tolerated)
    return result


# The three lifecycle clocks now live in `_store_clocks`, imported at the top of
# this module and re-exported below. They moved out when a THIRD one was added
# (`_clear_completion_stamp_on_leaving_done`) and this file passed the 512-line
# limit: they are pure, they share one shape, and they are the only pieces of
# this module another module reaches for by name. Existing imports from here
# still resolve — see `__all__`.



__all__ = ["add_task"]

# EOF
