"""Frozen semantic safety and closure rules for Product v0.2.3.3."""

from __future__ import annotations

from typing import Any, Mapping

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.formal_live_v0233 import (
    FormalActionJournalV0233,
    FormalClosureProofV0233,
    FormalObservedStateCountsV0233,
    FormalSafetyObservationV0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalSourceSelectionV0233,
)


def evaluate_formal_safety_v0233(
    *,
    starting_counts: FormalObservedStateCountsV0233,
    source_action_totals: Mapping[str, int | bool],
    ending_counts: FormalObservedStateCountsV0233 | None,
    ending_action_totals: Mapping[str, int | bool] | None,
    action_journal: FormalActionJournalV0233,
    unavailable_new_incident_count: int | None = None,
    unavailable_new_diagnosis_count: int | None = None,
) -> FormalSafetyObservationV0233:
    """Evaluate the fail-closed action and state-delta safety contract."""

    if ending_counts is None or ending_action_totals is None:
        return FormalSafetyObservationV0233.build(
            observation_status="UNAVAILABLE",
            action_journal=action_journal.model_dump(mode="json"),
            starting_counts=starting_counts.model_dump(mode="json"),
            ending_counts=None,
            new_incident_count=unavailable_new_incident_count,
            new_diagnosis_count=unavailable_new_diagnosis_count,
            provider_calls=None,
            agent_writes=None,
            runbook_executions=None,
            fault_attempts=None,
            knowledge_loop_executions=None,
            observed_action_authority=None,
            safe=False,
        )

    provider_calls = int(ending_action_totals["provider_calls"]) - int(
        source_action_totals["provider_calls"]
    )
    agent_writes = int(ending_action_totals["agent_writes"]) - int(
        source_action_totals["agent_writes"]
    )
    runbook_executions = int(ending_action_totals["runbook_executions"]) - int(
        source_action_totals["runbook_executions"]
    )
    action_authority_none = bool(source_action_totals["action_authority_none"])
    action_authority_none = action_authority_none and bool(
        ending_action_totals["action_authority_none"]
    )
    new_incident_count = ending_counts.incident_count - starting_counts.incident_count
    new_diagnosis_count = (
        ending_counts.diagnosis_job_count - starting_counts.diagnosis_job_count
    )
    safe = (
        action_journal.observation_status == "COMPLETE"
        and ending_counts.baseline_count == starting_counts.baseline_count
        and ending_counts.active_baseline_count == starting_counts.active_baseline_count
        and ending_counts.baseline_job_count == starting_counts.baseline_job_count
        and ending_counts.verify_job_count == starting_counts.verify_job_count
        and ending_counts.fault_family_count == starting_counts.fault_family_count
        and ending_counts.knowledge_artifact_count
        == starting_counts.knowledge_artifact_count
        and ending_counts.pending_job_count == 0
        and ending_counts.running_job_count == 0
        and provider_calls == 0
        and agent_writes == 0
        and runbook_executions == 0
        and action_journal.fault_attempts == 0
        and action_journal.knowledge_loop_executions == 0
        and action_authority_none
    )
    return FormalSafetyObservationV0233.build(
        observation_status="OBSERVED",
        action_journal=action_journal.model_dump(mode="json"),
        starting_counts=starting_counts.model_dump(mode="json"),
        ending_counts=ending_counts.model_dump(mode="json"),
        new_incident_count=new_incident_count,
        new_diagnosis_count=new_diagnosis_count,
        provider_calls=provider_calls,
        agent_writes=agent_writes,
        runbook_executions=runbook_executions,
        fault_attempts=action_journal.fault_attempts,
        knowledge_loop_executions=action_journal.knowledge_loop_executions,
        observed_action_authority="NONE" if action_authority_none else None,
        safe=safe,
    )


def evaluate_formal_closure_v0233(
    *,
    queue_before_sha256: str | None,
    queue_after_sha256: str | None,
    outer_baseline_before_sha256: str | None,
    outer_baseline_after_sha256: str | None,
    source_before: FreshFormalSourceSelectionV0233,
    source_after: FreshFormalSourceSelectionV0233 | None,
    product_cleanup: Mapping[str, Any],
    demo_cleanup: Any,
    clone_owner_count: int | None,
    clone_baseline_binding_exact: bool,
    frozen_semantic_surface_before_sha256: str | None,
    frozen_semantic_surface_after_sha256: str | None,
    safety_observation: FormalSafetyObservationV0233,
) -> tuple[dict[str, Any], bool]:
    """Evaluate clean closure without mixing it with cleanup orchestration."""

    demo_payload = (
        demo_cleanup.model_dump(mode="json")
        if hasattr(demo_cleanup, "model_dump")
        else None
    )
    clean = (
        queue_before_sha256 is not None
        and queue_before_sha256 == queue_after_sha256
        and outer_baseline_before_sha256 is not None
        and outer_baseline_before_sha256 == outer_baseline_after_sha256
        and source_after == source_before
        and product_cleanup.get("verdict") == "CLEAN"
        and product_cleanup.get("owned_host_processes") == 0
        and product_cleanup.get("non_owned_resources_changed") is False
        and demo_payload is not None
        and demo_payload.get("verdict") == "CLEAN"
        and demo_payload.get("owned_containers") == 0
        and demo_payload.get("owned_networks") == 0
        and demo_payload.get("owned_volumes") == 0
        and demo_payload.get("non_owned_resources_changed") is False
        and clone_owner_count == 0
        and clone_baseline_binding_exact
        and frozen_semantic_surface_before_sha256 is not None
        and frozen_semantic_surface_before_sha256
        == frozen_semantic_surface_after_sha256
        and safety_observation.safe
    )
    if clean:
        assert source_after is not None
        proof = FormalClosureProofV0233.build(
            queue_before_sha256=queue_before_sha256,
            queue_after_sha256=queue_after_sha256,
            outer_baseline_before_sha256=outer_baseline_before_sha256,
            outer_baseline_after_sha256=outer_baseline_after_sha256,
            source_selection_before_sha256=source_before.selection_sha256,
            source_selection_after_sha256=source_after.selection_sha256,
            source_database_before_sha256=source_before.source_database_file_sha256,
            source_database_after_sha256=source_after.source_database_file_sha256,
            product_cleanup="CLEAN",
            demo_cleanup="CLEAN",
            owned_host_processes=0,
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            formal_clone_database_owner_count=0,
            non_owned_resources_changed=False,
            clone_baseline_binding_exact=True,
            frozen_semantic_surface_before_sha256=(
                frozen_semantic_surface_before_sha256
            ),
            frozen_semantic_surface_after_sha256=(
                frozen_semantic_surface_after_sha256
            ),
            safety_observation=safety_observation.model_dump(mode="json"),
        )
        return proof.model_dump(mode="json"), True

    body = {
        "schema_version": "ecomsre.product.formal-closure-observation.v0233",
        "verdict": "BLOCKED",
        "queue_before_sha256": queue_before_sha256,
        "queue_after_sha256": queue_after_sha256,
        "outer_baseline_before_sha256": outer_baseline_before_sha256,
        "outer_baseline_after_sha256": outer_baseline_after_sha256,
        "source_selection_before_sha256": source_before.selection_sha256,
        "source_selection_after_sha256": (
            None if source_after is None else source_after.selection_sha256
        ),
        "source_database_before_sha256": source_before.source_database_file_sha256,
        "source_database_after_sha256": (
            None if source_after is None else source_after.source_database_file_sha256
        ),
        "product_cleanup": dict(product_cleanup),
        "demo_cleanup": demo_payload,
        "formal_clone_database_owner_count": clone_owner_count,
        "clone_baseline_binding_exact": clone_baseline_binding_exact,
        "frozen_semantic_surface_before_sha256": (
            frozen_semantic_surface_before_sha256
        ),
        "frozen_semantic_surface_after_sha256": (
            frozen_semantic_surface_after_sha256
        ),
        "safety_observation": safety_observation.model_dump(mode="json"),
    }
    return {**body, "closure_sha256": semantic_sha256_v22(body)}, False


__all__ = ("evaluate_formal_closure_v0233", "evaluate_formal_safety_v0233")
