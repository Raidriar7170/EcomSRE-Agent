from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2 import contracts as dta_contracts
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionParameter,
    ActionProposal,
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    Precondition,
    ResolvedEvidence,
    RiskLevel,
    RunbookId,
    RunbookParameterSpec,
    RunbookParameterType,
    RunbookPartialFailurePolicy,
    RunbookSpec,
    RunbookStepId,
    RunbookStepSpec,
    ScenarioSpec,
    Terminal,
    build_action_proposal,
    build_resolved_diagnosis_evidence_view,
    semantic_sha256,
    validate_action_proposal_binding,
)
from ecomsre.dta_v2.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.registry import load_runbook_registry


RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_ROOT = REPO_ROOT / "config" / "dta-v2" / "runbooks"


def completed_diagnosis() -> DtaDiagnosis:
    return DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service="payment",
        root_entity_ref="service:payment",
        fault_domain=FaultDomain.CONFIGURATION,
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        confidence=0.91,
        supporting_evidence_refs=(
            f"evidence://{RUN_ID}/metrics/0001",
            f"evidence://{RUN_ID}/traces/0002",
        ),
        contradicting_evidence_refs=(),
        evidence_source_types=(EvidenceSource.METRICS, EvidenceSource.TRACES),
        uncertainties=(),
        summary="Payment configuration drift explains the bounded failure window.",
    )


def resolved_view(diagnosis: DtaDiagnosis):
    refs = tuple(
        sorted(
            diagnosis.supporting_evidence_refs
            + diagnosis.contradicting_evidence_refs
        )
    )
    return build_resolved_diagnosis_evidence_view(
        run_id=diagnosis.run_id,
        evidence=tuple(
            ResolvedEvidence(
                evidence_ref=reference,
                source=EvidenceSource(reference.split("/")[3].upper()),
                artifact_sha256=hashlib.sha256(reference.encode()).hexdigest(),
            )
            for reference in refs
        ),
    )


def action_fixture(runbook_id: RunbookId):
    fixture = {
        RunbookId.ROLLBACK_CONFIGURATION: (
            "payment",
            FaultDomain.CONFIGURATION,
            FaultMechanism.CONFIGURATION_ERROR,
            (EvidenceSource.METRICS, EvidenceSource.TRACES),
            (),
        ),
        RunbookId.RESTART_SERVICE: (
            "recommendation",
            FaultDomain.SERVICE_RUNTIME,
            FaultMechanism.SERVICE_UNAVAILABLE,
            (EvidenceSource.METRICS, EvidenceSource.RUNTIME),
            (ActionParameter(name="wait_for_health_seconds", value=30),),
        ),
        RunbookId.MITIGATE_MEMORY_LEAK: (
            "email",
            FaultDomain.LOCAL_RESOURCE,
            FaultMechanism.MEMORY_LEAK,
            (
                EvidenceSource.METRICS,
                EvidenceSource.RUNTIME,
                EvidenceSource.RESOURCES,
            ),
            (ActionParameter(name="wait_for_health_seconds", value=30),),
        ),
    }[runbook_id]
    service, domain, mechanism, sources, parameters = fixture
    references = tuple(
        f"evidence://{RUN_ID}/{source.value.lower()}/{index:04d}"
        for index, source in enumerate(sources, start=1)
    )
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service=service,
        root_entity_ref=f"service:{service}",
        fault_domain=domain,
        mechanism=mechanism,
        confidence=0.9,
        supporting_evidence_refs=references,
        contradicting_evidence_refs=(),
        evidence_source_types=sources,
        uncertainties=(),
        summary="Resolved evidence supports the bounded diagnosis.",
    )
    evidence = resolved_view(diagnosis)
    registry = load_runbook_registry(RUNBOOK_ROOT)
    candidate_set = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
    )
    return diagnosis, evidence, registry, candidate_set, parameters


@pytest.mark.parametrize(
    ("runbook_id", "missing_source"),
    [
        (RunbookId.ROLLBACK_CONFIGURATION, EvidenceSource.TRACES),
        (RunbookId.RESTART_SERVICE, EvidenceSource.RUNTIME),
        (RunbookId.MITIGATE_MEMORY_LEAK, EvidenceSource.RESOURCES),
    ],
)
def test_action_proposal_requires_all_runbook_evidence_sources(
    runbook_id: RunbookId,
    missing_source: EvidenceSource,
) -> None:
    diagnosis, evidence, registry, candidate_set, parameters = action_fixture(
        runbook_id
    )
    proposal_refs = tuple(
        reference
        for reference in diagnosis.supporting_evidence_refs
        if f"/{missing_source.value.lower()}/" not in reference
    )

    with pytest.raises(ValueError, match="required evidence sources"):
        build_action_proposal(
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=evidence,
            disposition=ActionDisposition.EXECUTE_RUNBOOK,
            runbook_id=runbook_id,
            target_service=diagnosis.root_service,
            parameters=parameters,
            supporting_evidence_refs=proposal_refs,
            rationale="An execute proposal must cover every required source.",
        )


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
def test_action_proposal_accepts_complete_runbook_evidence_coverage(
    runbook_id: RunbookId,
) -> None:
    diagnosis, evidence, registry, candidate_set, parameters = action_fixture(
        runbook_id
    )

    proposal = build_action_proposal(
        candidate_set=candidate_set,
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
        disposition=ActionDisposition.EXECUTE_RUNBOOK,
        runbook_id=runbook_id,
        target_service=diagnosis.root_service,
        parameters=parameters,
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        rationale="The proposal covers every trusted Runbook evidence source.",
    )

    assert proposal.runbook_id is runbook_id


def test_candidate_and_evidence_view_names_freeze_diagnosis_scope() -> None:
    assert "resolved_evidence_sha256" in dta_contracts.CandidateSet.model_fields
    assert "state_snapshot_sha256" not in dta_contracts.CandidateSet.model_fields
    assert hasattr(dta_contracts, "ResolvedDiagnosisEvidenceView")
    assert not hasattr(dta_contracts, "ResolvedEvidenceView")


def test_multistep_runbook_freezes_partial_failure_policy() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    email = registry.require(RunbookId.MITIGATE_MEMORY_LEAK)

    assert email.partial_failure_policy is not None
    assert email.partial_failure_policy.terminal == "PARTIALLY_APPLIED"
    assert email.partial_failure_policy.disposition == "ESCALATE_HUMAN"
    assert email.partial_failure_policy.preserve_completed_steps is True
    assert email.partial_failure_policy.completed_step_compensation_allowed is False
    assert email.partial_failure_policy.additional_forward_write_allowed is False
    assert email.partial_failure_policy.step_receipt_required is True


def test_diagnosis_contract_is_namespaced_and_ground_truth_free() -> None:
    diagnosis = completed_diagnosis()

    assert diagnosis.schema_version == "dta-v2.diagnosis.v1"
    assert diagnosis.mechanism is FaultMechanism.CONFIGURATION_ERROR
    assert "expected_root_service" not in DtaDiagnosis.model_fields

    with pytest.raises(ValidationError, match="extra"):
        DtaDiagnosis.model_validate(
            {
                **diagnosis.model_dump(),
                "expected_root_service": "payment",
            }
        )


def test_diagnosis_rejects_cross_run_overlap_and_source_mismatch() -> None:
    diagnosis = completed_diagnosis()

    with pytest.raises(ValidationError, match="outside the current run"):
        DtaDiagnosis.model_validate(
            {
                **diagnosis.model_dump(),
                "supporting_evidence_refs": (
                    f"evidence://{OTHER_RUN_ID}/metrics/0001",
                ),
                "evidence_source_types": (EvidenceSource.METRICS,),
            }
        )

    with pytest.raises(ValidationError, match="both support and contradict"):
        DtaDiagnosis.model_validate(
            {
                **diagnosis.model_dump(),
                "contradicting_evidence_refs": diagnosis.supporting_evidence_refs,
            }
        )

    with pytest.raises(ValidationError, match="source accounting"):
        DtaDiagnosis.model_validate(
            {
                **diagnosis.model_dump(),
                "evidence_source_types": (EvidenceSource.METRICS,),
            }
        )


def test_noncompleted_diagnosis_cannot_claim_root_or_mechanism() -> None:
    with pytest.raises(ValidationError, match="cannot claim"):
        DtaDiagnosis(
            schema_version="dta-v2.diagnosis.v1",
            run_id=RUN_ID,
            terminal=Terminal.NEED_MORE_EVIDENCE,
            root_service="payment",
            root_entity_ref="service:payment",
            fault_domain=FaultDomain.CONFIGURATION,
            mechanism=FaultMechanism.CONFIGURATION_ERROR,
            confidence=0.4,
            supporting_evidence_refs=(
                f"evidence://{RUN_ID}/metrics/0001",
            ),
            contradicting_evidence_refs=(),
            evidence_source_types=(EvidenceSource.METRICS,),
            uncertainties=("A second source is required.",),
            summary="Additional bounded evidence is required.",
        )


def test_completed_diagnosis_binds_root_entity_and_canonical_evidence() -> None:
    diagnosis = completed_diagnosis()

    with pytest.raises(ValidationError, match="root entity"):
        DtaDiagnosis.model_validate(
            {
                **diagnosis.model_dump(),
                "root_entity_ref": "service:email",
            }
        )

    with pytest.raises(ValidationError, match="canonically ordered"):
        DtaDiagnosis.model_validate(
            {
                **diagnosis.model_dump(),
                "supporting_evidence_refs": tuple(
                    reversed(diagnosis.supporting_evidence_refs)
                ),
            }
        )


def test_action_proposal_binds_candidate_and_rejects_authority_fields() -> None:
    diagnosis = completed_diagnosis()
    evidence = resolved_view(diagnosis)
    registry = load_runbook_registry(RUNBOOK_ROOT)
    candidate_set = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
    )
    proposal = build_action_proposal(
        candidate_set=candidate_set,
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
        disposition=ActionDisposition.EXECUTE_RUNBOOK,
        runbook_id=RunbookId.ROLLBACK_CONFIGURATION,
        target_service="payment",
        parameters=(),
        supporting_evidence_refs=(
            f"evidence://{RUN_ID}/metrics/0001",
            f"evidence://{RUN_ID}/traces/0002",
        ),
        rationale="The bounded candidate matches the resolved diagnosis evidence.",
    )

    assert proposal.proposal_sha256
    assert proposal.runbook_id is RunbookId.ROLLBACK_CONFIGURATION
    assert proposal.schema_version == "dta-v2.action-proposal.v2"
    assert proposal.registry_sha256 == registry.registry_sha256
    assert proposal.resolved_evidence_sha256 == evidence.resolved_evidence_sha256
    assert proposal.runbook_sha256 == semantic_sha256(
        registry.require(RunbookId.ROLLBACK_CONFIGURATION).model_dump(mode="json")
    )

    forged_payload = proposal.model_dump(mode="python")
    forged_payload["candidate_set_sha256"] = "e" * 64
    forged_payload["proposal_sha256"] = semantic_sha256(
        {
            key: value
            for key, value in forged_payload.items()
            if key != "proposal_sha256"
        }
    )
    forged = ActionProposal.model_validate(forged_payload)
    with pytest.raises(ValueError, match="candidate digest"):
        validate_action_proposal_binding(
            proposal=forged,
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=evidence,
        )

    with pytest.raises(ValidationError, match="extra"):
        ActionProposal.model_validate(
            {
                **proposal.model_dump(),
                "risk_level": "LOW",
            }
        )

    with pytest.raises(ValidationError, match="forbidden parameter"):
        ActionParameter(name="shell_command", value="safe")

    with pytest.raises(ValidationError, match="path or URL"):
        ActionParameter(name="service_name", value="/tmp/recommendation")

    with pytest.raises(ValueError, match="outside the candidate set"):
        build_action_proposal(
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=evidence,
            disposition=ActionDisposition.EXECUTE_RUNBOOK,
            runbook_id=RunbookId.MITIGATE_MEMORY_LEAK,
            target_service="email",
            parameters=(
                ActionParameter(name="wait_for_health_seconds", value=30),
            ),
            supporting_evidence_refs=(
                f"evidence://{RUN_ID}/metrics/0001",
            ),
            rationale="A structurally valid but noncandidate action is rejected.",
        )

    trusted = registry.require(RunbookId.ROLLBACK_CONFIGURATION)
    forged_runbook = trusted.model_copy(
        update={
            "executor_id": "OtherBoundedExecutor",
        }
    )
    forged_registry = registry.model_copy(
        update={
            "runbooks": tuple(
                forged_runbook if item.runbook_id is trusted.runbook_id else item
                for item in registry.runbooks
            )
        }
    )
    with pytest.raises(ValueError, match="frozen MVP contract"):
        build_action_proposal(
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=forged_registry,
            diagnosis_evidence=evidence,
            disposition=ActionDisposition.EXECUTE_RUNBOOK,
            runbook_id=RunbookId.ROLLBACK_CONFIGURATION,
            target_service="payment",
            parameters=(),
            supporting_evidence_refs=(
                f"evidence://{RUN_ID}/metrics/0001",
            ),
            rationale="A caller-forged registry must not create action authority.",
        )


def test_nonexecute_proposal_cannot_smuggle_runbook_or_parameters() -> None:
    diagnosis = completed_diagnosis()
    evidence = resolved_view(diagnosis)
    registry = load_runbook_registry(RUNBOOK_ROOT)
    candidate_set = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
    )
    with pytest.raises(ValidationError, match="must not carry"):
        build_action_proposal(
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=evidence,
            disposition=ActionDisposition.ESCALATE_HUMAN,
            runbook_id=RunbookId.RESTART_SERVICE,
            target_service="recommendation",
            parameters=(),
            supporting_evidence_refs=(),
            rationale="No compatible bounded action is admitted.",
        )


def test_runbook_contract_bounds_steps_and_keeps_risk_runtime_owned() -> None:
    spec = RunbookSpec(
        schema_version="dta-v2.runbook-spec.v1",
        runbook_id=RunbookId.MITIGATE_MEMORY_LEAK,
        version="v1",
        supported_fault_domains=(FaultDomain.LOCAL_RESOURCE,),
        supported_mechanisms=(FaultMechanism.MEMORY_LEAK,),
        target_services=("email",),
        risk_level=RiskLevel.MEDIUM,
        required_evidence_sources=(
            EvidenceSource.METRICS,
            EvidenceSource.RUNTIME,
            EvidenceSource.RESOURCES,
        ),
        parameters=(),
        preconditions=(
            Precondition.LOCAL_DOCKER_ONLY,
            Precondition.OWNED_SERVICE,
            Precondition.LEAK_FLAG_ACTIVE,
        ),
        forward_steps=(
            RunbookStepSpec(
                step_id=RunbookStepId.DISABLE_LEAK_FLAG,
                parameter_names=(),
            ),
            RunbookStepSpec(
                step_id=RunbookStepId.RESTART_OWNED_SERVICE,
                parameter_names=(),
            ),
        ),
        executor_id="MemoryLeakMitigationExecutor",
        verifier_id="MemoryLeakRecoveryVerifier",
        maximum_forward_steps=2,
        failure_policy="ESCALATE_HUMAN",
        partial_failure_policy=RunbookPartialFailurePolicy(
            terminal="PARTIALLY_APPLIED",
            disposition="ESCALATE_HUMAN",
            preserve_completed_steps=True,
            completed_step_compensation_allowed=False,
            additional_forward_write_allowed=False,
            step_receipt_required=True,
        ),
    )

    assert spec.maximum_forward_steps == 2

    with pytest.raises(ValidationError, match="partial failure policy"):
        RunbookSpec.model_validate(
            {
                **spec.model_dump(),
                "partial_failure_policy": None,
            }
        )

    with pytest.raises(ValidationError, match="less than or equal to 2"):
        RunbookSpec.model_validate(
            {
                **spec.model_dump(),
                "maximum_forward_steps": 3,
            }
        )

    with pytest.raises(ValidationError, match="forbidden parameter"):
        RunbookParameterSpec(
            name="executor",
            parameter_type=RunbookParameterType.STRING,
            required=True,
        )


def test_proposal_binding_enforces_trusted_parameter_bounds() -> None:
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service="recommendation",
        root_entity_ref="service:recommendation",
        fault_domain=FaultDomain.SERVICE_RUNTIME,
        mechanism=FaultMechanism.SERVICE_UNAVAILABLE,
        confidence=0.9,
        supporting_evidence_refs=(
            f"evidence://{RUN_ID}/metrics/0001",
            f"evidence://{RUN_ID}/runtime/0002",
        ),
        contradicting_evidence_refs=(),
        evidence_source_types=(EvidenceSource.METRICS, EvidenceSource.RUNTIME),
        uncertainties=(),
        summary="Resolved evidence shows the service is unavailable.",
    )
    evidence = resolved_view(diagnosis)
    registry = load_runbook_registry(RUNBOOK_ROOT)
    candidate_set = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
    )

    with pytest.raises(ValueError, match="exceeds its maximum"):
        build_action_proposal(
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=evidence,
            disposition=ActionDisposition.EXECUTE_RUNBOOK,
            runbook_id=RunbookId.RESTART_SERVICE,
            target_service="recommendation",
            parameters=(
                ActionParameter(name="wait_for_health_seconds", value=121),
            ),
            supporting_evidence_refs=(
                f"evidence://{RUN_ID}/metrics/0001",
                f"evidence://{RUN_ID}/runtime/0002",
            ),
            rationale="The trusted candidate requires a bounded wait parameter.",
        )


def test_agent_visible_scenario_has_exact_four_call_budget() -> None:
    scenario = ScenarioSpec(
        schema_version="dta-v2.scenario.v1",
        scenario_id="dta-dev-001",
        alert_summary="Checkout failures increased during the bounded window.",
        candidate_services=("checkout", "payment"),
        allowed_read_tools=(
            "query_metrics",
            "search_logs",
            "query_trace_neighborhood",
            "inspect_service_runtime",
            "inspect_resource_usage",
        ),
        maximum_read_tool_dispatches=4,
        maximum_repeated_identical_calls=0,
    )

    assert scenario.maximum_read_tool_dispatches == 4

    with pytest.raises(ValidationError, match="extra"):
        ScenarioSpec.model_validate(
            {
                **scenario.model_dump(),
                "expected_runbook": "ROLLBACK_CONFIGURATION",
            }
        )

    with pytest.raises(ValidationError, match="evaluator marker"):
        ScenarioSpec.model_validate(
            {
                **scenario.model_dump(),
                "candidate_services": ("checkout", "expected_root_service"),
            }
        )
