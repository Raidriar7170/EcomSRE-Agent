from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase1.contracts import (
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    RCADecision,
)
from ecomsre.phase2.contracts import (
    JudgeRequest,
    ModelAllowedActions,
    Phase2Variant,
)
from ecomsre.phase2.evidence_views import build_judge_request
from ecomsre.phase2.workflows import (
    execute_replay_specialists,
    prepare_specialist_execution,
)
from ecomsre.phase4.contracts import (
    DomainFaultMechanism,
    DomainRemediationOutcome,
    DomainWorkflowTrace,
    DomainVariant,
)
from ecomsre.phase4.judge import judge_domain_request
from ecomsre.phase4.workflows import run_domain_replay_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISIBLE_ROOT = PROJECT_ROOT / "config/phase4/replay-cases/agent-visible"
EXPECTED = {
    "search-feature-freshness-lag-complete": (
        RCADecision.RCA_CONFIRMED,
        "feature",
        DomainFaultMechanism.FEATURE_FRESHNESS_LAG,
    ),
    "recommendation-model-feature-schema-mismatch": (
        RCADecision.RCA_CONFIRMED,
        "ranking",
        DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH,
    ),
    "search-ranking-configuration-frontend-decoy": (
        RCADecision.RCA_CONFIRMED,
        "ranking",
        DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE,
    ),
    "recommendation-feature-evidence-insufficient": (
        RCADecision.NEED_MORE_EVIDENCE,
        None,
        None,
    ),
    "ranking-change-with-normal-search-sli": (
        RCADecision.ABSTAIN,
        None,
        None,
    ),
}


@pytest.mark.parametrize("case_id", tuple(EXPECTED))
@pytest.mark.parametrize("variant", tuple(DomainVariant))
def test_domain_workflows_diagnose_all_five_cases(
    case_id: str,
    variant: DomainVariant,
) -> None:
    trace = run_domain_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=load_replay_case(VISIBLE_ROOT, case_id),
        variant=variant,
    )

    expected_decision, expected_root, expected_mechanism = EXPECTED[case_id]
    assert trace.status == "COMPLETED"
    assert trace.final_rca is not None
    assert trace.final_rca.decision is expected_decision
    assert trace.final_rca.root_service == expected_root
    assert trace.final_rca.fault_mechanism is expected_mechanism
    assert trace.admitted_graph is not None
    assert trace.findings
    assert trace.tool_call_records
    assert trace.final_budget_snapshot.active_capacity_slot_ids == ()
    assert trace.final_budget_snapshot.active_specialist_authorization_ids == ()
    assert trace.final_budget_snapshot.active_lease_ids == ()
    assert trace.live_environment is False
    assert trace.phase5_entered is False

    evidence_by_ref = {
        evidence.evidence_ref: evidence
        for record in trace.tool_call_records
        for evidence in record.evidence
    }
    cited = (
        *trace.final_rca.supporting_evidence,
        *trace.final_rca.contradicting_evidence,
    )
    assert all(reference in evidence_by_ref for reference in cited)
    assert all(
        evidence_by_ref[reference].run_id == trace.run_id for reference in cited
    )

    if expected_decision is RCADecision.RCA_CONFIRMED:
        assert trace.remediation_disposition.outcome is (
            DomainRemediationOutcome.NO_SUPPORTED_REMEDIATION
        )
    else:
        assert trace.remediation_disposition.outcome is (
            DomainRemediationOutcome.NO_ACTION
        )
    assert trace.remediation_disposition.remediation_action is None
    assert trace.remediation_disposition.live_mutation is False
    assert trace.remediation_disposition.remediation_backend == "NONE"


@pytest.mark.parametrize("variant", tuple(DomainVariant))
def test_frontend_decoy_never_supports_ranking_rca(variant: DomainVariant) -> None:
    trace = run_domain_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=load_replay_case(
            VISIBLE_ROOT,
            "search-ranking-configuration-frontend-decoy",
        ),
        variant=variant,
    )
    assert trace.final_rca is not None
    supporting = set(trace.final_rca.supporting_evidence)
    for record in trace.tool_call_records:
        for evidence in record.evidence:
            if evidence.service == "frontend":
                assert evidence.evidence_ref not in supporting


@pytest.mark.parametrize("variant", tuple(DomainVariant))
def test_domain_workflow_trace_is_byte_deterministic(variant: DomainVariant) -> None:
    case = load_replay_case(VISIBLE_ROOT, "search-feature-freshness-lag-complete")
    first = run_domain_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=case,
        variant=variant,
    )
    second = run_domain_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=case,
        variant=variant,
    )
    assert first.model_dump_json() == second.model_dump_json()


def test_domain_variants_do_not_expand_phase2_variant_enum() -> None:
    assert tuple(item.value for item in Phase2Variant) == (
        "SINGLE_AGENT",
        "FIXED_SPECIALIST_WORKFLOW",
        "DYNAMIC_MULTI_AGENT",
    )


def test_domain_trace_rejects_cross_run_final_evidence_reference() -> None:
    trace = run_domain_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=load_replay_case(
            VISIBLE_ROOT,
            "search-feature-freshness-lag-complete",
        ),
        variant=DomainVariant.FIXED_SPECIALIST_WORKFLOW,
    )
    payload = trace.model_dump(mode="json")
    final = payload["final_rca"]
    assert isinstance(final, dict)
    supporting = final["supporting_evidence"]
    assert isinstance(supporting, list)
    supporting[0] = f"evidence://{'2' * 32}/metrics/0000"

    with pytest.raises(ValidationError, match="cross-run evidence"):
        DomainWorkflowTrace.model_validate(payload)


def test_domain_judge_needs_more_for_conflicting_same_root_mechanism() -> None:
    case = load_replay_case(
        VISIBLE_ROOT,
        "recommendation-model-feature-schema-mismatch",
    )
    boundary = prepare_specialist_execution(
        project_root=PROJECT_ROOT,
        replay_case=case,
        variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        namespace="phase4-domain-conflict-test",
    )
    execute_replay_specialists(boundary)
    assert boundary.graph is not None
    assert boundary.judge_capacity_slot_id is not None
    request = build_judge_request(
        judge_request_id=f"phase4-domain-{boundary.judge_capacity_slot_id}",
        run_id=boundary.run_id,
        incident=case.incident,
        admitted_graph=boundary.graph,
        finding_ids=tuple(
            boundary.finding_id_by_node[node.node_id]
            for node in boundary.graph.initial_plan.nodes
        ),
        finding_store=boundary.finding_store,
        evidence_store=boundary.evidence_store,
        budget_snapshot=boundary.ledger.snapshot(),
        refinement_round=0,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    conflicting = Evidence(
        schema_version="phase1.evidence.v1",
        evidence_ref=f"evidence://{boundary.run_id}/changes/9999",
        run_id=boundary.run_id,
        source=EvidenceSource.CHANGES,
        observation_type="ranking_configuration_transition",
        attributes=(
            EvidenceAttribute(name="change_kind", value="ranking_configuration"),
            EvidenceAttribute(name="transition", value="valid_to_invalid"),
        ),
        raw_artifact_ref="changes.json#9999",
        raw_artifact_sha256="9" * 64,
        limitations=(),
        summary="A conflicting typed mechanism observation exists.",
        started_at=case.incident.started_at,
        ended_at=case.incident.ended_at,
        service="ranking",
    )
    payload = request.model_dump(mode="json")
    findings = payload["findings"]
    assert isinstance(findings, list)
    changes_finding = findings[-1]
    assert isinstance(changes_finding, dict)
    assert changes_finding["source"] == "CHANGES"
    changes_finding["evidence_refs"].append(conflicting.evidence_ref)
    payload["available_evidence_refs"].append(conflicting.evidence_ref)
    payload["resolved_evidence_view"]["evidence"].append(
        conflicting.model_dump(mode="json")
    )
    conflicted_request = JudgeRequest.model_validate(payload)

    result = judge_domain_request(conflicted_request)

    assert result.decision is RCADecision.NEED_MORE_EVIDENCE
    assert result.root_service is None
    assert result.fault_mechanism is None
    assert "conflicting" in result.missing_evidence[0].casefold()
