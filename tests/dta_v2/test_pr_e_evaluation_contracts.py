from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    EvaluationArm,
    EvaluationPrediction,
    EvaluationSplit,
    EvaluatorCaseTruth,
    ReplayObservationFixture,
    ScenarioFamily,
    build_evaluation_score,
    build_held_out_seal,
    persist_held_out_execution_claim,
)
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    MetricRecord,
    MetricUnit,
    ToolName,
)


def _fixture() -> ReplayObservationFixture:
    payload = {
        "schema_version": "dta-v2.replay-observation-fixture.v1",
        "tool": ToolName.QUERY_METRICS,
        "service_scope": ("payment",),
        "records": (
            MetricRecord(
                service="payment",
                metric_kind=MetricKind.ERROR_RATE,
                value=0.75,
                unit=MetricUnit.RATIO,
                sample_count=30,
            ),
        ),
        "truncated": False,
        "error_code": None,
    }
    draft = ReplayObservationFixture.model_construct(
        **payload, fixture_sha256="0" * 64
    )
    return ReplayObservationFixture.model_validate(
        {
            **payload,
            "fixture_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"fixture_sha256"})
            ),
        }
    )


def _case() -> AgentVisibleReplayCase:
    started_at = datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc)
    payload = {
        "schema_version": "dta-v2.agent-visible-replay-case.v1",
        "case_id": "dta-case-001",
        "scenario_id": "dta-dev-001",
        "captured_started_at": started_at,
        "captured_ended_at": started_at + timedelta(minutes=5),
        "observations": (_fixture(),),
        "full_context_tools": (ToolName.QUERY_METRICS,),
    }
    draft = AgentVisibleReplayCase.model_construct(**payload, case_sha256="0" * 64)
    return AgentVisibleReplayCase.model_validate(
        {
            **payload,
            "case_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"case_sha256"})
            ),
        }
    )


def _truth(*, split: EvaluationSplit = EvaluationSplit.DEVELOPMENT) -> EvaluatorCaseTruth:
    payload = {
        "schema_version": "dta-v2.evaluator-case-truth.v1",
        "case_id": "dta-case-001",
        "split": split,
        "scenario_family": ScenarioFamily.PAYMENT,
        "meaningful_observation_differences": ("fault_strength",),
        "expected_terminal": Terminal.COMPLETED,
        "expected_root_service": "payment",
        "expected_fault_domain": FaultDomain.CONFIGURATION,
        "expected_mechanism": FaultMechanism.CONFIGURATION_ERROR,
        "expected_disposition": ActionDisposition.EXECUTE_RUNBOOK,
        "expected_runbook": RunbookId.ROLLBACK_CONFIGURATION,
        "expected_evidence_sources": (
            EvidenceSource.METRICS,
            EvidenceSource.TRACES,
        ),
    }
    return EvaluatorCaseTruth.model_validate(
        {**payload, "truth_sha256": semantic_sha256(payload)}
    )


def test_agent_case_and_evaluator_truth_are_separate_and_digest_bound() -> None:
    case = _case()
    truth = _truth()

    assert "expected_" not in case.model_dump_json()
    assert case.case_id == truth.case_id
    with pytest.raises(ValidationError):
        AgentVisibleReplayCase.model_validate(
            {**case.model_dump(), "expected_runbook": "ROLLBACK_CONFIGURATION"}
        )
    with pytest.raises(ValidationError):
        AgentVisibleReplayCase.model_validate(
            {**case.model_dump(), "case_sha256": "0" * 64}
        )


def test_fixture_rejects_result_type_mismatch_and_truth_markers() -> None:
    fixture = _fixture()
    with pytest.raises(ValidationError):
        ReplayObservationFixture.model_validate(
            {
                **fixture.model_dump(),
                "tool": ToolName.SEARCH_LOGS,
                "fixture_sha256": "0" * 64,
            }
        )
    with pytest.raises(ValidationError):
        AgentVisibleReplayCase.model_validate(
            {
                **_case().model_dump(),
                "case_id": "expected-runbook-payment",
                "case_sha256": "0" * 64,
            }
        )


def test_scorer_reports_exact_semantics_cost_and_zero_unsafe_attempts() -> None:
    prediction = EvaluationPrediction(
        schema_version="dta-v2.evaluation-prediction.v1",
        case_id="dta-case-001",
        arm=EvaluationArm.ADAPTIVE_TOOL_USING,
        terminal=Terminal.COMPLETED,
        root_service="payment",
        fault_domain=FaultDomain.CONFIGURATION,
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        disposition=ActionDisposition.EXECUTE_RUNBOOK,
        runbook_id=RunbookId.ROLLBACK_CONFIGURATION,
        cited_evidence_sources=(EvidenceSource.METRICS, EvidenceSource.TRACES),
        evidence_refs_valid=True,
        read_tool_dispatches=2,
        provider_turns=4,
        input_tokens=100,
        output_tokens=20,
        latency_ms=50,
        unsafe_proposal_attempts=0,
    )

    score = build_evaluation_score(prediction=prediction, truth=_truth())

    assert score.root_exact_match is True
    assert score.mechanism_accuracy is True
    assert score.runbook_top1_accuracy is True
    assert score.evidence_validity is True
    assert score.action_precision is True
    assert score.no_action_accuracy is None
    assert score.unsafe_proposal_attempts == 0


def test_no_action_scorer_requires_zero_write_and_correct_escalation() -> None:
    truth_payload = {
        "schema_version": "dta-v2.evaluator-case-truth.v1",
        "case_id": "dta-case-010",
        "split": EvaluationSplit.NO_ACTION,
        "scenario_family": ScenarioFamily.CONFLICTING_EVIDENCE,
        "meaningful_observation_differences": ("conflicting_evidence",),
        "expected_terminal": Terminal.NEED_MORE_EVIDENCE,
        "expected_root_service": None,
        "expected_fault_domain": None,
        "expected_mechanism": None,
        "expected_disposition": None,
        "expected_runbook": None,
        "expected_evidence_sources": (),
    }
    truth = EvaluatorCaseTruth.model_validate(
        {**truth_payload, "truth_sha256": semantic_sha256(truth_payload)}
    )
    prediction = EvaluationPrediction(
        schema_version="dta-v2.evaluation-prediction.v1",
        case_id=truth.case_id,
        arm=EvaluationArm.ONE_SHOT_FULL_CONTEXT,
        terminal=Terminal.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_domain=None,
        mechanism=None,
        disposition=None,
        runbook_id=None,
        cited_evidence_sources=(),
        evidence_refs_valid=True,
        read_tool_dispatches=0,
        provider_turns=1,
        input_tokens=80,
        output_tokens=15,
        latency_ms=30,
        unsafe_proposal_attempts=0,
    )

    score = build_evaluation_score(prediction=prediction, truth=truth)

    assert score.no_action_accuracy is True
    assert score.escalation_accuracy is True
    assert score.action_precision is True


def test_held_out_seal_binds_all_frozen_inputs_and_claim_is_create_once(
    tmp_path: Path,
) -> None:
    seal = build_held_out_seal(
        base_head="a" * 40,
        model_id="gpt-5.4-mini-2026-03-17",
        agent_identity_sha256="1" * 64,
        one_shot_prompt_sha256="2" * 64,
        adaptive_prompt_sha256="3" * 64,
        tool_schema_sha256="4" * 64,
        budgets_sha256="5" * 64,
        diagnosis_schema_sha256="6" * 64,
        runbook_registry_sha256="7" * 64,
        candidate_filter_sha256="8" * 64,
        action_schema_sha256="9" * 64,
        scorer_sha256="b" * 64,
        held_out_case_sha256s=("c" * 64, "d" * 64, "e" * 64),
        evaluator_truth_sha256s=("f" * 64, "0" * 64, "1" * 64),
    )
    target = tmp_path / "held-out-execution-claim.json"

    first = persist_held_out_execution_claim(
        target, seal=seal, execution_id="f" * 32
    )
    assert target.stat().st_mode & 0o777 == 0o600
    assert persist_held_out_execution_claim(
        target, seal=seal, execution_id="f" * 32
    ) == first
    with pytest.raises(FileExistsError):
        persist_held_out_execution_claim(
            target, seal=seal, execution_id="e" * 32
        )
    with pytest.raises(ValidationError):
        type(seal).model_validate({**seal.model_dump(), "scorer_sha256": "a" * 64})
