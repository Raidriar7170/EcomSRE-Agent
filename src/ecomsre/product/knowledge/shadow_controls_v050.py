"""Versioned derived controls, never independent episodes or model inputs."""

from copy import deepcopy
import json

from ecomsre.dta_v2.v22.memory import RuntimeReadOutcomeV22, build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.connectors.base import ConnectorQueryResultV1
from ecomsre.product.incidents.anomaly_policy import extract_product_anomalies_v1
from ecomsre.product.incidents.extensions import (
    build_product_extension_runtime_input_v1,
)
from ecomsre.product.knowledge.candidates_v050 import evaluate_candidate
from ecomsre.product.knowledge.contracts import ShadowCaseOutcomeV1

CONTROL_VERSION = "v050-target-and-source-failure-v1"


def failed_outcome(outcome):
    payload = dict(
        schema_version="dta-v22.read-outcome.v1",
        action_id=outcome.action_id,
        source=outcome.source.value,
        request_sha256=outcome.request_sha256,
        status="FAILURE_UNAVAILABLE",
        records=[],
        truncated=False,
    )
    return ReadOutcomeV22.model_validate_json(
        json.dumps(payload | {"outcome_sha256": sha(payload)})
    )


def failed_memory_outcome(outcome):
    failed = failed_outcome(outcome)
    if not isinstance(outcome, RuntimeReadOutcomeV22):
        return failed
    from ecomsre.dta_v2.contracts import semantic_sha256
    from ecomsre.dta_v2.tool_contracts import ReadToolObservation

    observation = outcome.source_observation.model_dump(
        mode="json", exclude={"artifact_sha256"}
    )
    observation.update(
        status="FAILURE",
        error_code="SOURCE_UNAVAILABLE",
        results=[],
        result_count=0,
        truncated=False,
        duplicate_of_request_sha256=None,
    )
    typed = ReadToolObservation.model_validate_json(
        json.dumps(observation | {"artifact_sha256": semantic_sha256(observation)})
    )
    return RuntimeReadOutcomeV22.from_pr_b(
        action=outcome.action, source_outcome=failed, source_observation=typed
    )


def source_failure_inputs(material, snapshots, observations, source):
    """Re-extract memory and anomalies from failed typed reads; never copy predicates."""
    outcomes = tuple(
        failed_memory_outcome(o) if o.source.value == source else o
        for o in material.memory_outcomes
    )
    raw = tuple(
        failed_outcome(o) if o.source.value == source else o
        for o in material.raw_outcomes
    )
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=material.baseline.v22_baseline_profile,
        observed_at=material.incident.diagnosis_observed_at,
        top_k=64,
    )
    changed_snapshots = deepcopy(snapshots)
    for snapshot in changed_snapshots:
        result = snapshot["connector_result"]
        if result["source"] != source:
            continue
        result.update(
            status="FAILURE_UNAVAILABLE",
            records=[],
            covered_services=[],
            truncated=False,
            safe_error_code="DERIVED_SOURCE_FAILURE",
        )
        result["result_sha256"] = sha(
            {k: v for k, v in result.items() if k != "result_sha256"}
        )
        ConnectorQueryResultV1.model_validate_json(json.dumps(result))
        snapshot["read_outcome"] = next(
            o.model_dump(mode="json")
            for o in raw
            if o.action_id == snapshot["action"]["action_id"]
        )
        if "memory_outcome" in snapshot:
            snapshot["memory_outcome"] = next(
                o.model_dump(mode="json")
                for o in outcomes
                if o.action_id == snapshot["action"]["action_id"]
            )
    changed_observations = deepcopy(observations)
    for observation in changed_observations:
        if observation["source"] == source:
            observation.update(
                status="FAILURE_UNAVAILABLE",
                records=[],
                covered_services=[],
                truncated=False,
            )
    anomalies = extract_product_anomalies_v1(
        memory=memory,
        candidate_services=material.incident.candidate_logical_services,
        baseline_known_log_templates=tuple(
            (item.service, item.template)
            for item in material.baseline.normal_log_templates
        ),
        snapshots=tuple(changed_snapshots),
    )
    runtime = build_product_extension_runtime_input_v1(
        case_id=material.incident.incident_id + ":derived-failure:" + source,
        candidate_services=material.incident.candidate_logical_services,
        topology_edges=material.runtime_input.adjacent_services,
        baseline=material.baseline.v22_baseline_profile,
        memory=memory,
        generic_anomalies=anomalies,
        raw_outcomes=raw,
    )
    return runtime, changed_observations


def target_evidence_exclusion(material, snapshots, observations, target):
    """Withhold target-owned query results, keeping actual other-service evidence.

    The evaluated target stays unchanged. Missing target evidence must remain
    UNKNOWN; this tests non-borrowing, not an assertion of target health. A query
    containing the target is removed in full rather than falsifying its binding.
    """

    def target_owned(outcome):
        return any(r.service == target for r in outcome.records) or (
            isinstance(outcome, RuntimeReadOutcomeV22)
            and target in outcome.action.target_services
        )

    outcomes = tuple(o for o in material.memory_outcomes if not target_owned(o))
    raw = tuple(o for o in material.raw_outcomes if not target_owned(o))
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=material.baseline.v22_baseline_profile,
        observed_at=material.incident.diagnosis_observed_at,
        top_k=64,
    )
    kept_snapshots = tuple(
        s
        for s in snapshots
        if target not in s["connector_result"]["requested_services"]
    )
    projected = [
        o
        for o in observations
        if target not in o.get("targets", o["covered_services"])
        and not any(r.get("service") == target for r in o["records"])
    ]
    anomalies = extract_product_anomalies_v1(
        memory=memory,
        candidate_services=material.incident.candidate_logical_services,
        baseline_known_log_templates=tuple(
            (item.service, item.template)
            for item in material.baseline.normal_log_templates
        ),
        snapshots=kept_snapshots,
    )
    runtime = build_product_extension_runtime_input_v1(
        case_id=material.incident.incident_id + ":target-evidence-excluded",
        candidate_services=material.incident.candidate_logical_services,
        topology_edges=material.runtime_input.adjacent_services,
        baseline=material.baseline.v22_baseline_profile,
        memory=memory,
        generic_anomalies=anomalies,
        raw_outcomes=raw,
    )
    return runtime, projected


def derived_controls(candidate, material, snapshots, observations):
    """Apply the fixed transforms to one declared positive parent, without selection."""
    parent = material.incident.incident_id
    target = candidate.proposal.target
    runtime, projected = target_evidence_exclusion(
        material, snapshots, observations, target
    )
    retained = [
        dict(
            source=o["source"],
            evidence_ref=o["evidence_ref"],
            services=sorted(
                {
                    r["service"]
                    for r in o["records"]
                    if r.get("service") != target
                    and r.get("service") in o["covered_services"]
                }
            ),
        )
        for o in projected
        if o["source"] in candidate.proposal.required_sources
        and o["status"] == "SUCCESS_NONEMPTY"
        and not o["truncated"]
    ]
    retained = [r for r in retained if r["services"]]
    inputs = (
        [
            (
                "TARGET_COUNTERFACTUAL",
                "DERIVED_COUNTERFACTUAL",
                target,
                runtime,
                projected,
                {
                    "withhold_target_owned_queries": target,
                    "evaluated_target_unchanged": True,
                    "retained_other_service_evidence": retained,
                },
            )
        ]
        if retained
        else []
    )
    for source in candidate.proposal.required_sources:
        runtime, failed = source_failure_inputs(
            material, snapshots, observations, source
        )
        inputs.append(
            (
                "SOURCE_FAILURE",
                "DERIVED_SOURCE_FAILURE",
                candidate.proposal.target,
                runtime,
                failed,
                {"failed_source": source},
            )
        )
    outcomes, details = [], []
    if not retained:
        payload = dict(
            schema_version="ecomsre.product.shadow-case-outcome.v1",
            case_id=parent + ":target-control-unavailable",
            incident_id=parent,
            stratum="TARGET_COUNTERFACTUAL",
            origin="NOT_AVAILABLE",
            runtime_input_sha256=None,
            expected_match=None,
            matched=None,
            evaluated_target_services=(),
            supporting_evidence_refs=(),
            available_evidence_refs=(),
            required_sources=candidate.proposal.required_sources,
            source_reachable=None,
            action_authority_violations=0,
            reason_code="NON_TARGET_REQUIRED_SOURCE_CONTROL_NOT_AVAILABLE",
        )
        outcomes.append(
            ShadowCaseOutcomeV1.model_validate(
                payload | {"outcome_sha256": sha(payload)}
            )
        )
        details.append(
            dict(
                case_id=payload["case_id"],
                origin="NOT_AVAILABLE",
                lineage=dict(version=CONTROL_VERSION, parent_incident_id=parent),
                raw_result=dict(status="NOT_AVAILABLE", reason=payload["reason_code"]),
            )
        )
    for stratum, origin, target, runtime, projected, transform in inputs:
        lineage = dict(
            version=CONTROL_VERSION,
            parent_incident_id=parent,
            parent_runtime_input_sha256=material.runtime_input.runtime_input_sha256,
            transform=transform,
            candidate_sha256=candidate.compiled_sha256,
        )
        input_sha = sha(
            dict(
                lineage=lineage,
                runtime=runtime.model_dump(mode="json"),
                observations=projected,
            )
        )
        result = evaluate_candidate(
            candidate,
            target=target,
            memory=runtime.memory,
            anomalies=runtime.generic_anomalies,
            observations=projected,
            incident_end=material.incident.diagnosis_observed_at,
        )
        available = tuple(
            sorted(
                {r.evidence_ref for r in runtime.memory.evidence_refs}
                | {
                    o["evidence_ref"]
                    for o in projected
                    if o["status"] == "SUCCESS_NONEMPTY" and not o["truncated"]
                }
            )
        )
        payload = dict(
            schema_version="ecomsre.product.shadow-case-outcome.v1",
            case_id="derived:" + input_sha,
            incident_id=parent,
            stratum=stratum,
            origin=origin,
            runtime_input_sha256=input_sha,
            expected_match=False,
            matched=result.status == "TRUE",
            evaluated_target_services=(target,),
            supporting_evidence_refs=result.evidence_refs
            if result.status == "TRUE"
            else (),
            available_evidence_refs=available,
            required_sources=candidate.proposal.required_sources,
            source_reachable=result.status != "UNKNOWN",
            action_authority_violations=0,
            reason_code=None,
        )
        outcomes.append(
            ShadowCaseOutcomeV1.model_validate(
                payload | {"outcome_sha256": sha(payload)}
            )
        )
        details.append(
            dict(
                case_id=payload["case_id"],
                lineage=lineage,
                input_sha256=input_sha,
                raw_result=result.model_dump(mode="json"),
                source_states=[
                    dict(
                        source=o["source"],
                        status=o["status"],
                        covered_services=o["covered_services"],
                        record_count=len(o["records"]),
                    )
                    for o in projected
                ],
            )
        )
    return outcomes, details
