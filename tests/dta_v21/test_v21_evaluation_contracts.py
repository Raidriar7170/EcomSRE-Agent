from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json

import pytest

from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    MetricRecord,
    MetricUnit,
    ToolName,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationArmV21,
    EvaluationPredictionV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
    GeneralizationSliceV21,
    PublicCaseBindingV21,
    PublicEvaluationManifestV21,
    ReplayObservationFixtureV21,
    ScenarioFamilyV21,
    build_evaluation_score_v21,
)


START = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)
END = START + timedelta(seconds=30)


def _digest(model_type, payload: Mapping[str, object], field: str) -> str:
    draft = model_type.model_construct(**payload, **{field: "0" * 64})
    return semantic_sha256(draft.model_dump(mode="json", exclude={field}))


def _fixture(
    service_scope: tuple[str, ...] = ("payment",),
) -> ReplayObservationFixtureV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.replay-observation-fixture.v1",
        "tool": ToolName.QUERY_METRICS,
        "service_scope": service_scope,
        "records": (
            MetricRecord(
                service="payment",
                metric_kind=MetricKind.ERROR_RATE,
                value=0.5,
                unit=MetricUnit.RATIO,
                sample_count=20,
            ),
        ),
        "truncated": False,
        "error_code": None,
    }
    return ReplayObservationFixtureV21.model_validate(
        {
            **payload,
            "fixture_sha256": _digest(
                ReplayObservationFixtureV21, payload, "fixture_sha256"
            ),
        }
    )


def _case(case_id: str = "dta21-case-001") -> AgentVisibleReplayCaseV21:
    fixture = _fixture()
    payload: dict[str, object] = {
        "schema_version": "dta-v21.agent-visible-replay-case.v1",
        "case_id": case_id,
        "scenario_id": "dta21-dev-001",
        "captured_started_at": START,
        "captured_ended_at": END,
        "observations": (fixture,),
        "full_context_tools": (ToolName.QUERY_METRICS,),
    }
    return AgentVisibleReplayCaseV21.model_validate(
        {
            **payload,
            "case_sha256": _digest(AgentVisibleReplayCaseV21, payload, "case_sha256"),
        }
    )


def _truth(case_id: str = "dta21-case-001") -> EvaluatorCaseTruthV21:
    payload = {
        "schema_version": "dta-v21.evaluator-case-truth.v1",
        "case_id": case_id,
        "split": EvaluationSplitV21.DEVELOPMENT,
        "scenario_family": ScenarioFamilyV21.PAYMENT_CONFIGURATION,
        "generalization_slice": GeneralizationSliceV21.SEEN_SERVICE_SEEN_MECHANISM,
        "meaningful_observation_differences": ("fault_strength",),
        "expected_terminal": TerminalV21.COMPLETED,
        "expected_root_service": "payment",
        "expected_fault_domain": FaultDomainV21.CONFIGURATION,
        "expected_mechanism": FaultMechanismV21.CONFIGURATION_ERROR,
        "expected_disposition": ActionDispositionV21.EXECUTE_RUNBOOK,
        "expected_runbook": RunbookIdV21.ROLLBACK_CONFIGURATION,
        "expected_evidence_sources": (EvidenceSourceV21.METRICS,),
    }
    return EvaluatorCaseTruthV21.model_validate(
        {
            **payload,
            "truth_sha256": _digest(EvaluatorCaseTruthV21, payload, "truth_sha256"),
        }
    )


def test_case_is_truth_free_hash_bound_and_rejects_answer_semantics() -> None:
    case = _case()

    assert case.case_sha256
    assert "expected_" not in case.model_dump_json()
    bad_payload = {
        **case.model_dump(mode="python", exclude={"case_sha256"}),
        "observations": (_fixture(("expected-root",)),),
    }
    with pytest.raises(ValueError, match="truth-isolation"):
        AgentVisibleReplayCaseV21.model_validate(
            {
                **bad_payload,
                "case_sha256": _digest(
                    AgentVisibleReplayCaseV21, bad_payload, "case_sha256"
                ),
            }
        )


def test_scorer_binds_protocol_semantics_selection_and_costs() -> None:
    truth = _truth()
    prediction = EvaluationPredictionV21(
        schema_version="dta-v21.evaluation-prediction.v1",
        case_id=truth.case_id,
        arm=EvaluationArmV21.EVIDENCE_GUIDED_PLANNER,
        protocol_accepted=True,
        terminal=TerminalV21.COMPLETED,
        root_service="payment",
        fault_domain=FaultDomainV21.CONFIGURATION,
        mechanism=FaultMechanismV21.CONFIGURATION_ERROR,
        disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
        runbook_id=RunbookIdV21.ROLLBACK_CONFIGURATION,
        cited_evidence_sources=(EvidenceSourceV21.METRICS,),
        evidence_refs_valid=True,
        requested_evidence_sources=(EvidenceSourceV21.METRICS,),
        requested_targets=("payment",),
        duplicate_normalized_calls=0,
        read_tool_dispatches=1,
        context_materialization_reads=0,
        provider_turns=2,
        input_tokens=100,
        output_tokens=20,
        latency_ms=50,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
    )

    score = build_evaluation_score_v21(prediction=prediction, truth=truth)

    assert score.protocol_acceptance
    assert score.root_exact_match
    assert score.fault_domain_accuracy
    assert score.mechanism_accuracy
    assert score.evidence_validity
    assert score.runbook_top1_accuracy
    assert score.action_precision
    assert score.tool_source_selection_accuracy
    assert score.tool_target_selection_accuracy
    assert score.total_tokens == 120


def test_public_manifest_requires_exact_12_plus_8_and_contains_no_answers() -> None:
    development = tuple(
        PublicCaseBindingV21(
            case_id=f"dta21-case-{index:03d}",
            case_sha256=semantic_sha256({"case": index}),
            truth_sha256=semantic_sha256({"truth": index}),
            split_sha256=semantic_sha256("DEVELOPMENT"),
        )
        for index in range(1, 13)
    )
    held_out = tuple(
        PublicCaseBindingV21(
            case_id=f"dta21-case-{index:03d}",
            case_sha256=semantic_sha256({"case": index}),
            truth_sha256=semantic_sha256({"truth": index}),
            split_sha256=semantic_sha256("HELD_OUT"),
        )
        for index in range(13, 21)
    )
    payload = {
        "schema_version": "dta-v21.public-evaluation-manifest.v1",
        "case_schema_version": "dta-v21.agent-visible-replay-case.v1",
        "truth_schema_version": "dta-v21.evaluator-case-truth.v1",
        "development_cases": development,
        "held_out_cases": held_out,
    }
    manifest = PublicEvaluationManifestV21.model_validate(
        {
            **payload,
            "manifest_sha256": _digest(
                PublicEvaluationManifestV21, payload, "manifest_sha256"
            ),
        }
    )

    serialized = json.dumps(manifest.model_dump(mode="json")).casefold()
    for forbidden in (
        "expected_root",
        "expected_mechanism",
        "expected_runbook",
        "scenario_family",
        "fault_variant",
    ):
        assert forbidden not in serialized

    with pytest.raises(ValueError, match="at least 12|twelve development"):
        PublicEvaluationManifestV21.model_validate(
            {
                **manifest.model_dump(mode="python"),
                "development_cases": development[:-1],
            }
        )
