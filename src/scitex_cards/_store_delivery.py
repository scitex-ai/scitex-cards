"""Revision-bound Cards transitions and an opt-in delivery read port.

This module does not enroll cards or accept submitted evidence. Infrastructure
must supply a reviewed, authenticated observer before enrolled completion can
be enabled. Caller actor strings remain audit labels, never authorization.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from ._delivery_policy import (
    ABSENT,
    DeliveryConfig,
    DeliveryObserver,
    DeliveryPolicyError,
    assert_namespace_preserved,
    completion,
    delivery_namespace,
    enrolled_delivery,
    prepare_transition,
    _same,
    _time,
)
from ._store_errors import RevisionConflictError
from ._task import TaskValidationError, _is_tombstoned

FRESHNESS_ENV = "SCITEX_CARDS_DELIVERY_LIVE_MAX_AGE_SECONDS"
_OPERATIONS = frozenset(
    {"update", "complete", "resolve", "reopen", "upsert", "undo", "restore", "delete"}
)
_CLOCKS = ("completed_at", "completed_by", "closed_at", "closed_by")


@dataclass(slots=True)
class _PreparedWrite:
    """Private process state, never a field in a card or a serialized token."""

    target: str
    before: dict
    after: dict
    revision: int
    connection: object = None
    consumed: bool = False

    def __reduce_ex__(self, protocol):
        raise TypeError("prepared-write-not-serializable")


_WRITE_CONTEXT: ContextVar[_PreparedWrite | None] = ContextVar(
    "cards_prepared_write", default=None
)


class DeliveryGuardError(TaskValidationError):
    """A delivery transition was refused before any write or event."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configuration() -> DeliveryConfig | None:
    """Read explicit server configuration; there is no implicit age budget."""
    value = os.environ.get(FRESHNESS_ENV)
    if value is None:
        return None
    try:
        age = int(value)
    except (TypeError, ValueError):
        return None
    if age <= 0:
        return None
    return DeliveryConfig(max_live_age_seconds=age)


def _authenticated_observer() -> DeliveryObserver | None:
    """Neutral integration seam, intentionally unavailable in this patch.

    No plugin, route, submitted hash, actor name or boolean is promoted to an
    authenticated observation. Adapter activation requires independent review.
    """
    return None


def _target(store, verb: str) -> str:
    from ._store_arg import refuse_ineffective_store
    from ._store_target import resolve_store_target

    refuse_ineffective_store(store, verb=verb)
    return resolve_store_target(None)


def _read(target: str, task_id: str, *, allow_deleted: bool = False):
    from ._store import _task_not_found
    from ._store_single_card import read_card_or_raise

    card, revision = read_card_or_raise(target, task_id)
    if card is None or (_is_tombstoned(card) and not allow_deleted):
        raise _task_not_found(task_id)
    if type(revision) is not int or revision < 0:
        raise DeliveryGuardError("authoritative-card-revision-unavailable")
    return card, revision


def _decision(card: dict, revision: int, now: str):
    return completion(
        card,
        now=now,
        config=_configuration(),
        observer=_authenticated_observer(),
        revision=revision,
    )


def prepare_new_card(card: dict, *, operation: str = "add") -> dict:
    """Generic creation cannot inject enrollment or purported proof."""
    if operation not in {"add", "upsert", "restore"}:
        raise DeliveryGuardError("unsupported-card-creation-operation")
    try:
        assert_namespace_preserved({}, card)
    except DeliveryPolicyError as exc:
        raise DeliveryGuardError(str(exc)) from exc
    return deepcopy(card)


def _clock(card, key):
    meta = card.get("_log_meta")
    return meta.get(key, ABSENT) if isinstance(meta, dict) else ABSENT


def _preserve_clocks(before, after, *, completion=False, reopening=False):
    for key in _CLOCKS:
        old, new = _clock(before, key), _clock(after, key)
        if _same(old, new) or (completion and key in {"completed_at", "completed_by"}):
            continue
        if reopening and old is not ABSENT and new is ABSENT:
            continue
        raise DeliveryGuardError("delivery-completion-clock-reserved")


def _require_completion(before, after, revision, clock):
    if after.get("status") != "done" or not enrolled_delivery(after):
        return
    evaluation = deepcopy(after)
    # Proposed owner, blocker and dependencies never authenticate their own exit.
    for key in ("agent", "status", "blocker", "depends_on"):
        if key in before:
            evaluation[key] = deepcopy(before[key])
        else:
            evaluation.pop(key, None)
    verdict = _decision(evaluation, revision, clock)
    if not verdict.allowed:
        raise DeliveryGuardError(f"delivery:{verdict.stage}:{verdict.reason}")


def _prepare_existing(before, proposed, revision, operation):
    if operation not in _OPERATIONS:
        raise DeliveryGuardError("unsupported-delivery-transition")
    if proposed.get("id") != before.get("id"):
        raise DeliveryGuardError("card-identity-cannot-change")
    if delivery_namespace(before) is not ABSENT:
        _check_tombstone_transition(before, proposed, operation)
        reopening = proposed.get("status") != "done" and (
            operation == "reopen" or before.get("status") == "done"
            or (operation == "restore" and _is_tombstoned(before)
                and not _is_tombstoned(proposed))
        )
        _preserve_clocks(
            before, proposed, reopening=reopening,
            completion=operation in {"complete", "resolve"}
            and proposed.get("status") == "done",
        )
    clock = _now()
    try:
        after = prepare_transition(before, proposed, operation, clock)
    except DeliveryPolicyError as exc:
        raise DeliveryGuardError(str(exc)) from exc
    _require_completion(before, after, revision, clock)
    return after


@contextmanager
def _write_context(target, before, after, revision):
    if (
        not isinstance(target, str) or not target
        or not isinstance(before, dict) or not isinstance(after, dict)
        or not isinstance(before.get("id"), str) or not before["id"]
        or before["id"] != after.get("id")
    ):
        raise DeliveryGuardError("prepared-write-identity-invalid")
    if type(revision) is not int or revision < 0:
        raise DeliveryGuardError("authoritative-card-revision-unavailable")
    if _WRITE_CONTEXT.get() is not None:
        raise DeliveryGuardError("prepared-write-cannot-nest")
    capsule = _PreparedWrite(target, deepcopy(before), deepcopy(after), revision)
    token = _WRITE_CONTEXT.set(capsule)
    try:
        yield deepcopy(after)
    finally:
        _WRITE_CONTEXT.reset(token)


@contextmanager
def _prepared_write(target, before, proposed, revision, *, operation):
    """Mint a handoff only after the kernel's ordinary transition evaluation."""
    after = _prepare_existing(before, proposed, revision, operation)
    with _write_context(target, before, after, revision) as prepared:
        yield prepared


def _assert_comment_append(before, after):
    from ._comment_ids import is_comment_id

    old = before.get("comments", [])
    new = after.get("comments")
    if not isinstance(old, list) or not isinstance(new, list):
        raise DeliveryGuardError("comment-append-invalid")
    if len(new) != len(old) + 1 or not _same(old, new[:-1]):
        raise DeliveryGuardError("comment-append-invalid")
    entry = new[-1]
    if (
        not isinstance(entry, dict) or not is_comment_id(entry.get("id"))
        or set(entry) - {"id", "author", "ts", "text", "kind"}
        or any(not isinstance(entry.get(k), str) or not entry[k].strip()
               for k in ("author", "text"))
        or _time(entry.get("ts")) is None
        or ("kind" in entry and not isinstance(entry["kind"], str))
        or any(isinstance(c, dict) and c.get("id") == entry["id"] for c in old)
        or not _same(after.get("last_activity", ABSENT), entry["ts"])
    ):
        raise DeliveryGuardError("comment-entry-invalid")
    restored = deepcopy(after)
    for key in ("comments", "last_activity"):
        if key in before:
            restored[key] = deepcopy(before[key])
        else:
            restored.pop(key, None)
    if not _same(before, restored):
        raise DeliveryGuardError("comment-append-changed-card")


@contextmanager
def _comment_write(target, before, after, revision):
    """Authorize only one exact stamped append, including on an enrolled DONE."""
    _assert_comment_append(before, after)
    with _write_context(target, before, after, revision) as prepared:
        yield prepared


def _bind_write(target, conn, card, expected_revision):
    """Bind the private handoff to the writer's actual connection once."""
    capsule = _WRITE_CONTEXT.get()
    if capsule is None:
        return
    if type(capsule) is not _PreparedWrite:
        raise DeliveryGuardError("prepared-write-type-invalid")
    if (
        capsule.consumed or capsule.connection is not None
        or target != capsule.target or not _same(card, capsule.after)
        or type(expected_revision) is not int
        or expected_revision != capsule.revision
    ):
        raise DeliveryGuardError("prepared-write-binding-mismatch")
    capsule.connection = conn


def _preflight_generic_row_write(before, card, found_revision, expected_revision):
    """Generic policy only; document preflight cannot consume private authority."""
    if before is None:
        prepare_new_card(card)
        return expected_revision
    protected = delivery_namespace(before) is not ABSENT
    if protected:
        _check_tombstone_transition(before, card, "update")
        if type(found_revision) is not int or found_revision < 0:
            raise DeliveryGuardError("authoritative-card-revision-unavailable")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision != found_revision
        ):
            raise RevisionConflictError(card.get("id"), expected_revision, found_revision)
        _preserve_clocks(before, card)
    try:
        assert_namespace_preserved(before, card)
        prepared = prepare_transition(before, card, "update", _now())
    except DeliveryPolicyError as exc:
        raise DeliveryGuardError(str(exc)) from exc
    if not _same(prepared, card):
        raise DeliveryGuardError("controlled-delivery-transition-required")
    if card.get("id") != before.get("id"):
        raise DeliveryGuardError("card-identity-cannot-change")
    _require_completion(before, card, found_revision, _now())
    return found_revision if protected else expected_revision


def _guard_row_write(conn, before, card, found_revision, expected_revision):
    """Verify authoritative snapshots before any merge, drop, or row write."""
    capsule = _WRITE_CONTEXT.get()
    if capsule is None:
        return _preflight_generic_row_write(
            before, card, found_revision, expected_revision
        )
    if type(capsule) is not _PreparedWrite:
        raise DeliveryGuardError("prepared-write-type-invalid")
    if (
        capsule.consumed or capsule.connection is not conn
        or not _same(card, capsule.after)
        or type(expected_revision) is not int
        or expected_revision != capsule.revision
    ):
        raise DeliveryGuardError("prepared-write-authoritative-mismatch")
    if found_revision != capsule.revision:
        raise RevisionConflictError(card.get("id"), capsule.revision, found_revision)
    if type(found_revision) is not int or not _same(before, capsule.before):
        raise DeliveryGuardError("prepared-write-authoritative-mismatch")
    capsule.consumed = True
    return capsule.revision


def _guard_removal(before):
    if before is not None and delivery_namespace(before) is not ABSENT:
        raise DeliveryGuardError("controlled-delivery-removal-unavailable")


def _check_tombstone_transition(before, after, operation):
    was_deleted, is_deleted = _is_tombstoned(before), _is_tombstoned(after)
    if not was_deleted and is_deleted:
        _guard_removal(before)
    if was_deleted and not is_deleted and operation != "restore":
        raise DeliveryGuardError("controlled-delivery-restore-required")


def _guard_rebuild(before_cards, incoming_cards):
    for before in before_cards:
        _guard_removal(before)
    for card in incoming_cards:
        prepare_new_card(card)


def mutate_card(
    store,
    task_id: str,
    mutate: Callable[[dict], None],
    *,
    operation: str,
    actor: str | None = None,
    expected_revision: int | None = None,
) -> dict:
    """Read, transform, evaluate and CAS one canonical existing row.

    The callback receives a private copy of the authoritative read. A conflict
    propagates without replaying a status action or evidence. Side effects and
    notifications belong to the caller after this function returns. ``actor``
    is accepted for callers' audit plumbing and does not establish authority.
    """
    from ._store_single_card import write_card_or_raise

    if operation not in _OPERATIONS:
        raise DeliveryGuardError("unsupported-delivery-transition")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("mutate_card: 'task_id' is required")
    target = _target(store, "mutate_card")
    before, revision = _read(target, task_id, allow_deleted=operation == "restore")
    if expected_revision is not None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if expected_revision != revision:
            raise RevisionConflictError(task_id, expected_revision, revision)
    if operation == "delete" and delivery_namespace(before) is not ABSENT:
        raise DeliveryGuardError("controlled-delivery-removal-unavailable")
    after = deepcopy(before)
    try:
        callback_result = mutate(after)
    except DeliveryPolicyError as exc:
        raise DeliveryGuardError(str(exc)) from exc
    if callback_result is not None:
        raise TypeError("mutate_card callback must mutate its copy and return None")
    # Evaluate even an enrolled completion noop, but preserve ordinary legacy
    # idempotency: no clock, revision, event or receipt is refreshed on a noop.
    with _prepared_write(target, before, after, revision, operation=operation) as after:
        if after != before:
            write_card_or_raise(target, after, expected_revision=revision)
    return after


def read_delivery(store=None, task_id: str | None = None) -> dict:
    """Read an authoritative row/revision and its current completion verdict.

    This opt-in in-process port honors backend selection. Remote revision
    transport is not wired by this patch; it refuses rather than reading a
    second local store or fabricating a revision from the task payload.
    """
    from ._backend import BackendUnavailableError, get_backend

    backend = get_backend()
    if backend.name != "local":
        raise BackendUnavailableError(
            "delivery revision reads require a reviewed remote read port; "
            "no local fallback"
        )
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("read_delivery: 'task_id' is required")
    target = _target(store, "read_delivery")
    card, revision = _read(target, task_id)
    verdict = _decision(card, revision, _now())
    namespace = delivery_namespace(card)
    prerequisites = []
    if enrolled_delivery(card):
        if _configuration() is None:
            prerequisites.append("explicit-freshness-configuration")
        if _authenticated_observer() is None:
            prerequisites.append("authenticated-delivery-observer")
    return {
        "task": deepcopy(card),
        "revision": revision,
        "delivery": None if namespace is ABSENT else deepcopy(namespace),
        "delivery_present": namespace is not ABSENT,
        "decision": {
            "allowed": verdict.allowed,
            "stage": verdict.stage,
            "reason": verdict.reason,
        },
        "prerequisites": prerequisites,
    }


def read_delivery_stage(store=None, task_id: str | None = None) -> dict:
    """The existing reconciler's read-only next-stage projection."""
    envelope = read_delivery(store, task_id)
    return {
        "task_id": task_id,
        "revision": envelope["revision"],
        **envelope["decision"],
        "prerequisites": envelope["prerequisites"],
    }


__all__ = [
    "DeliveryGuardError",
    "FRESHNESS_ENV",
    "mutate_card",
    "prepare_new_card",
    "read_delivery",
    "read_delivery_stage",
]
