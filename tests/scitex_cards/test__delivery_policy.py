"""Synthetic delivery invariants; no store, network, attachments or runtime.

The named regression cases correspond to the seventeen private-review probes.
Observer fixtures represent a trusted port, not evidence about a live service.
"""

from copy import deepcopy
from dataclasses import replace

import pytest

from scitex_cards._delivery_policy import (
    STAGES,
    DeliveryConfig,
    DeliveryPolicyError,
    StageEvidence,
    completion,
    enrolled_delivery,
    reconcile_delivery,
)

NOW = "2026-10-03T00:30:00Z"
REVISION = 4
HEAD, BASE, MERGE = "a" * 40, "0" * 40, "b" * 40
CONFIG = DeliveryConfig(max_live_age_seconds=900)


def card():
    target = {
        "repo": "fixture/application", "pr": 1, "head": HEAD, "base": BASE,
        "instance_id": "fixture-localhost", "source_manifest_sha256": "c" * 64,
        "artifact_manifest_sha256": "d" * 64, "served_files_sha256": "e" * 64,
        "acceptance_contract_sha256": "1" * 64,
    }
    return {
        "id": "fixture-card", "agent": "app-owner", "status": "in_progress",
        "repo": "fixture/application",
        "pr_url": "https://github.com/fixture/application/pull/1",
        "_log_meta": {
            "other_event_stamp": "keep", "delivery": {
                "version": 1, "required": True, "generation": 1,
                "started_at": "2026-10-03T00:19:00Z", "target": target,
                "roles": {
                    "implementer": "app-author", "reviewer": "shared-reviewer",
                    "deployer": "shared-deployer",
                },
                "route": {
                    "job": "fixture-job", "host": "fixture-host", "resolved": True,
                },
                "checkpoints": {
                    "caller-claimed": {
                        "by": "shared-reviewer", "receipt_sha256": "f" * 64,
                    },
                },
                "lease": {"owner": "shared-deployer", "fence": 2},
                "completion": {"complete": True},
            },
        },
    }


class Observer:
    """Preconstructed trusted projections, independently bound to one read."""

    def __init__(self, task):
        target = deepcopy(task["_log_meta"]["delivery"]["target"])
        facts = {
            "review": {"verdict": "approved"},
            "ci": {
                "required": ["pytest"],
                "contexts": [{"name": "pytest", "head": HEAD, "conclusion": "success"}],
            },
            "merge": {"state": "merged", "commit": MERGE, "parents": [BASE, HEAD]},
            "checkout": {"commit": MERGE, "clean": True},
            "runtime": {"commit": MERGE, "origin_verified": True},
            "deploy": {"commit": MERGE, "lease_authorized": True},
            "live": {
                "commit": MERGE, "acceptance": "passed",
                "acceptance_contract_sha256": "1" * 64,
            },
            "owner_complete": {"complete": True},
        }
        for stage in ("runtime", "deploy", "live"):
            facts[stage].update(
                artifact_manifest_sha256="d" * 64, served_files_sha256="e" * 64,
            )
        self.evidence = {}
        for index, stage in enumerate(STAGES):
            actor = {
                "review": "shared-reviewer", "owner_complete": "app-owner",
            }.get(stage, "shared-deployer")
            self.evidence[stage] = StageEvidence(
                stage=stage, card_id=task["id"], revision=REVISION, generation=1,
                target=deepcopy(target), canonical_actor=actor,
                observed_at=f"2026-10-03T00:{20 + index:02}:00Z",
                facts=deepcopy(facts[stage]),
            )

    def verify_stage(self, task, stage, *, revision, now):
        return self.evidence.get(stage)


def evaluate(task, observer=None, **options):
    values = {"now": NOW, "config": CONFIG, "revision": REVISION,
              "observer": observer if observer is not None else Observer(task)}
    values.update(options)
    return completion(task, **values)


def capture_error(action):
    try:
        action()
    except Exception as error:
        return error
    return None


def test_fabricated_by_flags_hashes_no_authenticated_adapter():
    # Arrange
    task = card()
    task["_log_meta"]["delivery"]["checkpoints"] = {
        stage: {"by": "shared-deployer", "resolved": True, "lease_authorized": True,
                "receipt_sha256": "f" * 64, "complete": True} for stage in STAGES
    }
    # Act
    result = completion(task, now=NOW, config=CONFIG, revision=REVISION)
    # Assert
    assert (result.allowed, result.reason) == (False, "delivery-observer-unavailable")


def test_freshness_default_instead_of_explicit_config():
    # Arrange
    task = card()
    observer = Observer(task)
    # Act
    result = completion(task, now=NOW, revision=REVISION, observer=observer)
    # Assert
    assert (result.allowed, result.reason) == (False, "delivery-config-unavailable")


def test_caller_inflated_freshness_accepts_stale_proof():
    # Arrange
    task = card()
    task["_log_meta"]["delivery"]["max_live_age_seconds"] = 7200
    # Act
    result = evaluate(task, now="2026-10-03T01:00:00Z")
    # Assert
    assert (result.allowed, result.reason) == (False, "live-proof-stale")


def test_legacy_freshness_keyword_is_not_a_configuration_port():
    # Arrange
    task = card()
    # Act
    error = capture_error(lambda: completion(task, now=NOW, max_live_age_seconds=7200))
    # Assert
    assert type(error) is TypeError


def test_stale_proof_default_refused():
    # Arrange
    task = card()
    # Act
    result = evaluate(task, now="2026-10-03T01:00:00Z")
    # Assert
    assert (result.allowed, result.reason) == (False, "live-proof-stale")


def test_legacy_reconcile_store_failure():
    # Arrange
    task = {"status": "in_progress"}
    # Act
    result = reconcile_delivery(
        task, baseline_action="close", now=NOW, store_read_ok=False,
    )
    # Assert
    assert (result.allowed, result.reason) == (False, "store-read-unavailable")


def test_truthy_resolved_refused():
    # Arrange
    task = card()
    task["_log_meta"]["delivery"]["route"]["resolved"] = "yes"
    # Act
    result = completion(task, now=NOW, config=CONFIG, revision=REVISION)
    # Assert
    assert (result.allowed, result.reason) == (False, "delivery-observer-unavailable")


def test_truthy_lease_authorized_refused():
    # Arrange
    task = card()
    observer = Observer(task)
    observer.evidence["deploy"].facts["lease_authorized"] = "yes"
    # Act
    result = evaluate(task, observer)
    # Assert
    assert (result.allowed, result.reason) == (False, "deployment-lease-unproven")


@pytest.mark.parametrize(
    "namespace",
    [None, {}, {"required": False}, {"version": True, "required": False}],
)
def test_malformed_present_namespace_is_enrolled(namespace):
    # Arrange
    task = {"_log_meta": {"delivery": namespace}}
    # Act
    result = (enrolled_delivery(task), completion(task, now=NOW).allowed)
    # Assert
    assert result == (True, False)


@pytest.mark.parametrize(
    "task", [
        {"kind": "incident"}, {"_log_meta": {"completed_at": NOW}},
        {"_log_meta": {"delivery": {"version": 1, "required": False}}},
    ],
)
def test_legacy_and_incident_completion_remains_source_only(task):
    # Arrange
    before = deepcopy(task)
    # Act
    result = (enrolled_delivery(task), completion(task, now=NOW).allowed, task)
    # Assert
    assert result == (False, True, before)


@pytest.mark.parametrize("value", [None, False, 0, -1, "900"])
def test_freshness_configuration_requires_positive_integer(value):
    # Arrange
    candidate_age = value
    # Act
    error = capture_error(lambda: DeliveryConfig(candidate_age))
    # Assert
    assert (type(error), str(error)) == (
        DeliveryPolicyError, "delivery-config-invalid",
    )


def test_complete_authenticated_synthetic_chain_ignores_document_checkpoints():
    # Arrange
    task = card()
    observer = Observer(task)
    task["_log_meta"]["delivery"].pop("checkpoints")
    before = deepcopy(task)
    # Act
    result = evaluate(task, observer)
    # Assert
    assert (result.allowed, task) == (True, before)


@pytest.mark.parametrize(
    "field,value", [
        ("revision", 5), ("generation", 2), ("card_id", "another-card"),
        ("stage", "deploy"), ("target", {"head": HEAD}),
    ],
)
def test_trusted_evidence_requires_exact_binding(field, value):
    # Arrange
    task = card()
    observer = Observer(task)
    observer.evidence["review"] = replace(observer.evidence["review"], **{field: value})
    # Act
    result = evaluate(task, observer)
    # Assert
    assert (result.allowed, result.reason) == (False, "evidence-binding-mismatch")


def test_raw_observer_mapping_is_not_authenticated_evidence():
    # Arrange
    task = card()
    observer = Observer(task)
    observer.evidence["review"] = {
        "by": "shared-reviewer", "verdict": "approved", "authenticated": True,
    }
    # Act
    result = evaluate(task, observer)
    # Assert
    assert result.reason == "authenticated-evidence-unavailable"


def test_caller_by_does_not_override_authenticated_actor():
    # Arrange
    task = card()
    observer = Observer(task)
    observer.evidence["review"] = replace(
        observer.evidence["review"], canonical_actor="app-author",
    )
    # Act
    result = evaluate(task, observer)
    # Assert
    assert result.reason == "independent-source-review-missing"


def test_observer_failure_cannot_allow_completion():
    # Arrange
    class Unavailable:
        def verify_stage(self, *args, **kwargs):
            raise RuntimeError("unavailable")
    task = card()
    # Act
    result = evaluate(task, Unavailable())
    # Assert
    assert result.reason == "delivery-observer-unavailable"


def test_enrolled_reconcile_only_reports_authenticated_owner_readiness():
    # Arrange
    task = card()
    observer = Observer(task)
    # Act
    result = reconcile_delivery(
        task, baseline_action="close", now=NOW, config=CONFIG,
        observer=observer, revision=REVISION,
    )
    # Assert
    assert (result.allowed, result.reason) == (False, "owner-completion-ready")
