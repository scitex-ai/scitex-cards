"""Pure delivery invariants; evidence and configuration come from trusted ports.

This module performs no I/O. Generic card documents cannot enroll delivery or
edit its reserved namespace. The store owns transitions and supplies evidence
observers separately from the document being evaluated.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple, Protocol


ABSENT = object()
STAGES = (
    "review", "ci", "merge", "checkout", "runtime", "deploy", "live",
    "owner_complete",
)


class Decision(NamedTuple):
    allowed: bool
    stage: str
    reason: str


class DeliveryPolicyError(ValueError):
    """A proposed card write violates the delivery contract."""

    def __init__(self, reason: str, stage: str = "metadata"):
        self.reason = reason
        self.stage = stage
        super().__init__(reason)


@dataclass(frozen=True)
class DeliveryConfig:
    """Freshness policy supplied by the store's configuration port."""

    max_live_age_seconds: int

    def __post_init__(self):
        if type(self.max_live_age_seconds) is not int or self.max_live_age_seconds <= 0:
            raise DeliveryPolicyError("delivery-config-invalid")


@dataclass(frozen=True)
class StageEvidence:
    """Trusted observer projection, bound to the exact authoritative card read.

    An observer must verify canonical identities and obtain facts from their
    authoritative evidence sources. Constructing this from a card checkpoint,
    an unverified receipt digest, or a caller's authentication flags violates
    the port contract. The document itself never supplies this object.
    """

    stage: str
    card_id: str
    revision: object
    generation: int
    target: Mapping
    canonical_actor: str
    observed_at: str
    facts: Mapping


class DeliveryObserver(Protocol):
    def verify_stage(self, card, stage, *, revision, now) -> StageEvidence | None:
        """Read and authenticate stage facts; return None when unproven."""
        ...


def delivery_namespace(card):
    """Return the namespace, distinguishing absence from explicit null/invalid.

    Legacy log metadata need not have a particular shape when no delivery key
    exists. A present key, including an optional or malformed value, is reserved.
    """
    if not isinstance(card, Mapping):
        raise DeliveryPolicyError("card-invalid")
    meta = card.get("_log_meta")
    if isinstance(meta, Mapping) and "delivery" in meta:
        return meta["delivery"]
    return ABSENT


def _same(left, right):
    # Python considers True == 1; that must not permit a metadata type change.
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return left.keys() == right.keys() and all(
            _same(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def assert_namespace_preserved(current, proposed):
    """Refuse any generic addition, removal or change of the entire namespace."""
    old, new = delivery_namespace(current), delivery_namespace(proposed)
    if old is ABSENT and new is ABSENT:
        return
    if old is ABSENT or new is ABSENT or not _same(old, new):
        raise DeliveryPolicyError("delivery-namespace-reserved")


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _positive_int(value):
    return type(value) is int and value > 0


def _marker(card):
    data = delivery_namespace(card)
    if data is ABSENT:
        return data
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        raise DeliveryPolicyError("delivery-metadata-invalid")
    if type(data.get("required")) is not bool:
        raise DeliveryPolicyError("delivery-metadata-invalid")
    return data


def enrolled_delivery(card):
    """Malformed present namespaces must never fall back to legacy completion."""
    try:
        data = _marker(card)
    except DeliveryPolicyError:
        return True
    return data is not ABSENT and data["required"] is True


def _meta_field(card, key):
    meta = card.get("_log_meta")
    return meta.get(key, ABSENT) if isinstance(meta, Mapping) else ABSENT


def _restoring_tombstone(current, proposed, operation):
    # Match the canonical _task._is_tombstoned truthiness predicate. Setting
    # deleted_at to None or an empty string also makes the row live again.
    before, after = current.get("_log_meta"), proposed.get("_log_meta")
    was_deleted = isinstance(before, dict) and bool(before.get("deleted_at"))
    is_deleted = isinstance(after, dict) and bool(after.get("deleted_at"))
    return operation == "restore" and was_deleted and not is_deleted


def _assert_completion_stamps(current, proposed, operation):
    """A generic write must not manufacture a timeline completion claim."""
    if not enrolled_delivery(current):
        return
    if operation in {"complete", "resolve"} and proposed.get("status") == "done":
        # The kernel must authenticate completion before committing this row.
        return
    reopening = proposed.get("status") != "done" and (
        operation == "reopen" or current.get("status") == "done"
        or _restoring_tombstone(current, proposed, operation)
    )
    for key in ("completed_at", "completed_by"):
        old, new = _meta_field(current, key), _meta_field(proposed, key)
        if _same(old, new):
            continue
        if reopening and old is not ABSENT and new is ABSENT:
            continue
        raise DeliveryPolicyError("delivery-completion-stamp-reserved")


def prepare_transition(current, proposed, operation, now):
    """Protect caller metadata before an internal, atomic evidence invalidation.

    Returning a copy lets the caller commit generation, clocks, receipts and
    completion stamps together with the ordinary card fields in one CAS write.
    Legacy and explicit source-only rows retain their existing lifecycle rules.
    """
    assert_namespace_preserved(current, proposed)
    _assert_completion_stamps(current, proposed, operation)
    result = deepcopy(proposed)
    data = _marker(current)
    if data is ABSENT:
        return result
    if data["required"] is False and not _positive_int(data.get("generation")):
        return result
    invalidates = (
        operation == "reopen"
        or (current.get("status") == "done" and proposed.get("status") != "done")
        or _restoring_tombstone(current, proposed, operation)
        or any(
            not _same(current.get(key, ABSENT), proposed.get(key, ABSENT))
            for key in ("repo", "pr_url")
        )
    )
    if not invalidates:
        return result
    if not _positive_int(data.get("generation")):
        raise DeliveryPolicyError("generation-invalid")
    if _time(now) is None:
        raise DeliveryPolicyError("clock-invalid")
    updated = result["_log_meta"]["delivery"]
    updated["generation"] = data["generation"] + 1
    updated["started_at"] = now
    updated["checkpoints"] = {}
    for key in ("lease", "completion", "completed_at", "completed_by"):
        updated.pop(key, None)
    for key in ("completed_at", "completed_by", "closed_at", "closed_by"):
        result["_log_meta"].pop(key, None)
    return result


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hex(value, length):
    return (
        isinstance(value, str)
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


def _stage_facts(stage, facts, *, target, roles, actor, owner, merge_commit):
    """Evaluate verified facts, never the similarly named document claims."""
    if stage == "review":
        if (
            actor != roles["reviewer"]
            or actor == roles["implementer"]
            or facts.get("verdict") != "approved"
        ):
            return "independent-source-review-missing"
    elif stage == "ci":
        required, contexts = facts.get("required"), facts.get("contexts")
        if (
            not isinstance(required, list)
            or not required
            or not all(_text(name) for name in required)
        ):
            return "required-ci-contexts-missing"
        if not isinstance(contexts, list) or not all(
            isinstance(item, Mapping) for item in contexts
        ):
            return "ci-contexts-invalid"
        for name in required:
            matches = [item for item in contexts if item.get("name") == name]
            if len(matches) != 1 or matches[0].get("head") != target["head"]:
                return "ci-head-or-context-unknown"
            if matches[0].get("conclusion") != "success":
                return "required-ci-not-success"
    elif stage == "merge":
        if facts.get("state") != "merged" or not _hex(facts.get("commit"), 40):
            return "actual-merge-missing"
        if facts.get("parents") != [target["base"], target["head"]]:
            return "reviewed-head-not-merge-parent"
    elif stage == "checkout":
        if facts.get("commit") != merge_commit or facts.get("clean") is not True:
            return "checkout-not-exact-clean-merge"
    elif stage in ("runtime", "deploy", "live"):
        if facts.get("commit") != merge_commit:
            return "runtime-merge-mismatch"
        for key in ("artifact_manifest_sha256", "served_files_sha256"):
            if not _hex(target.get(key), 64) or facts.get(key) != target[key]:
                return "serving-artifact-mismatch"
        if actor != roles["deployer"]:
            return "deployer-attribution-missing"
        if stage == "runtime" and facts.get("origin_verified") is not True:
            return "runtime-origin-unknown"
        if stage == "deploy" and facts.get("lease_authorized") is not True:
            return "deployment-lease-unproven"
        if stage == "live":
            contract = target.get("acceptance_contract_sha256")
            if (
                not _hex(contract, 64)
                or facts.get("acceptance_contract_sha256") != contract
            ):
                return "live-scope-contract-unproven"
            if facts.get("acceptance") != "passed":
                return "live-acceptance-missing"
    elif actor != owner or facts.get("complete") is not True:
        return "explicit-owner-completion-missing"
    return None


def completion(
    card, *, now, config=None, observer=None, revision=None,
    store_read_ok=True, dependencies_satisfied=None,
):
    """Evaluate a fresh card using explicitly configured, authenticated ports.

    Checkpoint claims in the card do not establish any stage. The store must
    bind this decision to the same revision in its CAS completion write.
    """
    if store_read_ok is not True:
        return Decision(False, "read", "store-read-unavailable")
    try:
        data = _marker(card)
    except DeliveryPolicyError as exc:
        return Decision(False, exc.stage, exc.reason)
    if data is ABSENT or data["required"] is False:
        return Decision(True, "source-only", "legacy-source-only-completion")
    if card.get("status") == "blocked" or card.get("blocker") not in (None, "none"):
        return Decision(False, "blocker", "unresolved-card-blocker")
    if card.get("depends_on") and dependencies_satisfied is not True:
        return Decision(False, "dependency", "dependency-completion-unproven")
    if type(config) is not DeliveryConfig or not _positive_int(
        config.max_live_age_seconds
    ):
        return Decision(False, "metadata", "delivery-config-unavailable")
    clock, started = _time(now), _time(data.get("started_at"))
    if clock is None or started is None or started > clock:
        return Decision(False, "metadata", "generation-start-invalid")
    generation = data.get("generation")
    target, roles = data.get("target"), data.get("roles")
    if not _positive_int(generation) or not isinstance(target, dict):
        return Decision(False, "metadata", "candidate-missing")
    if not all(_text(target.get(key)) for key in ("repo", "instance_id")):
        return Decision(False, "metadata", "target-missing")
    if not _positive_int(target.get("pr")) or not all(
        _hex(target.get(key), 40) for key in ("head", "base")
    ):
        return Decision(False, "metadata", "candidate-invalid")
    if not _hex(target.get("source_manifest_sha256"), 64):
        return Decision(False, "metadata", "source-manifest-missing")
    if not isinstance(roles, Mapping) or not all(
        _text(roles.get(key)) for key in ("implementer", "reviewer", "deployer")
    ):
        return Decision(False, "route", "roles-missing")
    if not _text(card.get("id")) or not _text(card.get("agent")):
        return Decision(False, "route", "canonical-card-identity-missing")
    if roles["reviewer"] == roles["implementer"]:
        return Decision(False, "review", "self-review-refused")
    if revision is None:
        return Decision(False, "read", "card-revision-unavailable")
    if observer is None or not callable(getattr(observer, "verify_stage", None)):
        return Decision(False, "review", "delivery-observer-unavailable")
    prior, merge_commit = started, None
    for stage in STAGES:
        try:
            evidence = observer.verify_stage(
                deepcopy(card), stage, revision=revision, now=now
            )
        except Exception:
            return Decision(False, stage, "delivery-observer-unavailable")
        if type(evidence) is not StageEvidence:
            return Decision(False, stage, "authenticated-evidence-unavailable")
        if (
            evidence.stage != stage or evidence.card_id != card["id"]
            or not _same(evidence.revision, revision)
            or type(evidence.generation) is not int
            or evidence.generation != generation
            or not isinstance(evidence.target, Mapping)
            or not _same(dict(evidence.target), target)
        ):
            return Decision(False, stage, "evidence-binding-mismatch")
        if not _text(evidence.canonical_actor) or not isinstance(
            evidence.facts, Mapping
        ):
            return Decision(False, stage, "authenticated-evidence-unavailable")
        at = _time(evidence.observed_at)
        earliest = started if stage == "ci" else prior
        if at is None or at < earliest or at > clock:
            return Decision(False, stage, "checkpoint-time-invalid")
        prior = max(prior, at)
        reason = _stage_facts(
            stage, evidence.facts, target=target, roles=roles,
            actor=evidence.canonical_actor, owner=card["agent"],
            merge_commit=merge_commit,
        )
        if reason:
            return Decision(False, stage, reason)
        if stage == "merge":
            merge_commit = evidence.facts["commit"]
        if (
            stage == "live"
            and (clock - at).total_seconds() > config.max_live_age_seconds
        ):
            return Decision(False, stage, "live-proof-stale")
    return Decision(True, "done", "delivery-evidence-complete")


def reconcile_delivery(
    card, *, baseline_action, now, config=None, observer=None,
    revision=None, store_read_ok=True, dependencies_satisfied=None,
):
    """Report enrolled readiness without performing timer-owned completion."""
    check = completion(
        card, now=now, config=config, observer=observer, revision=revision,
        store_read_ok=store_read_ok, dependencies_satisfied=dependencies_satisfied,
    )
    if check.stage == "source-only":
        return Decision(baseline_action == "close", "source-only", baseline_action)
    reason = "owner-completion-ready" if check.allowed else check.reason
    return Decision(False, check.stage, reason)
