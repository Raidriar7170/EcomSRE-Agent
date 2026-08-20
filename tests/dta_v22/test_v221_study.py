from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v22.controller_contracts import (
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.evidence_acquisition_campaign_v221 import (
    EvidenceAcquisitionStudyArtifactV221,
    FINAL_STUDY_COMBINATIONS_V221,
    GatedDevelopmentArtifactV221,
    PracticalTruthSetV221,
    balanced_combination_order_v221,
    run_evidence_acquisition_campaign_v221,
    evaluate_gated_development_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_scorer_v221 import (
    compute_control_cost_metrics_v221,
    summarize_study_interpretation_v221,
    score_evidence_acquisition_runs_v221,
)
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import (
    StudyCombinationV221,
    TerminalExplorationPolicyV221,
)
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseSetV22,
    load_practical_case_set_v22,
)
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalAdaptiveReadEventV221,
    PracticalCaseRunV221,
    PracticalRunStatusV22,
)
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
)
from ecomsre.dta_v2.v22.simple_provider import ProviderTurnOutcomeV22


ROOT = Path(__file__).resolve().parents[2]


def _event(source: EvidenceSourceV22) -> PracticalAdaptiveReadEventV221:
    return PracticalAdaptiveReadEventV221(
        schema_version="dta-v22.1.practical-adaptive-read-event.v1",
        ordinal=1,
        action_id=f"a:{source.value.casefold()}:payment",
        source=source,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
    )


def _run(
    *,
    case_id: str,
    combination: StudyCombinationV221,
    status: PracticalRunStatusV22 = PracticalRunStatusV22.VALID_TERMINAL,
    terminal: str | None = "DIAGNOSED",
    root: str | None = "payment",
    mechanism: str | None = "CONFIGURATION_ERROR",
    redirects: int = 0,
    repeated: int = 0,
    redirect_response: ControllerDecisionKindV22 | None = None,
    read_source: EvidenceSourceV22 | None = None,
) -> PracticalCaseRunV221:
    events = () if read_source is None else (_event(read_source),)
    logical_attempts = 1 + redirects
    return PracticalCaseRunV221(
        schema_version="dta-v22.1.practical-case-run.v1",
        case_id=case_id,
        arm=combination.arm,
        case_bytes_sha256="1" * 64,
        normalized_case_sha256="2" * 64,
        status=status,
        terminal=terminal,
        root_service=root,
        mechanism=mechanism,
        supporting_evidence_refs=("e:a:metrics:payment:core:0:123456789abc",)
        if terminal == "DIAGNOSED"
        else (),
        evidence_ref_valid=terminal == "DIAGNOSED",
        semantic_clause_valid=terminal == "DIAGNOSED",
        adaptive_reads=len(events),
        adaptive_read_events=events,
        duplicate_read_attempts=0,
        provider_turns=max(0, logical_attempts - redirects),
        provider_calls=logical_attempts,
        logical_decision_attempts=logical_attempts,
        first_pass_protocol_successes=logical_attempts,
        post_repair_protocol_successes=logical_attempts,
        semantic_repairs=0,
        policy_redirects=redirects,
        premature_abstention_proposals=redirects + repeated,
        repeated_premature_abstentions=repeated,
        redirect_response_kind=redirect_response,
        terminal_exploration_policy=combination.policy,
        input_tokens=100 * logical_attempts,
        output_tokens=10 * logical_attempts,
        total_tokens=110 * logical_attempts,
        latency_ms=float(logical_attempts),
        transport_retry_count=0,
        uncaught_exceptions=0,
        safe_error_code=(
            "PREMATURE_ABSTENTION_REPEATED" if repeated else None
        ),
        agent_writes=0,
        planner_ledger_visible=combination.arm is ControllerArmV22.PLANNER_LITE,
    )


def test_balanced_final_schedule_is_interleaved_without_position_bias() -> None:
    orders = tuple(
        balanced_combination_order_v221(index, FINAL_STUDY_COMBINATIONS_V221)
        for index in range(12)
    )

    assert all(set(order) == set(FINAL_STUDY_COMBINATIONS_V221) for order in orders)
    for position in range(4):
        counts = {
            combination: sum(order[position] is combination for order in orders)
            for combination in FINAL_STUDY_COMBINATIONS_V221
        }
        assert set(counts.values()) == {3}


def test_study_artifact_schemas_freeze_execution_counts() -> None:
    assert "EXACTLY_ONE_FULL_STUDY_EXECUTION" in str(
        EvidenceAcquisitionStudyArtifactV221.model_fields["single_execution_rule"]
    )
    assert GatedDevelopmentArtifactV221.model_fields["development_iteration"].is_required()


def test_v221_scorer_uses_explicit_attempt_and_process_denominators() -> None:
    combination = StudyCombinationV221.FLAT_GATE
    runs = (
        _run(
            case_id="e01",
            combination=combination,
            redirects=1,
            redirect_response=ControllerDecisionKindV22.READ,
            read_source=EvidenceSourceV22.TRACES,
        ),
        _run(
            case_id="e02",
            combination=combination,
            status=PracticalRunStatusV22.PROTOCOL_FAILED,
            terminal=None,
            root=None,
            mechanism=None,
            redirects=1,
            repeated=1,
            redirect_response=ControllerDecisionKindV22.ABSTAIN,
        ),
    )
    truths = (
        PracticalTruthV22(
            case_id="e01",
            expected_terminal="DIAGNOSED",
            expected_root_service="payment",
            expected_mechanism="CONFIGURATION_ERROR",
            evidence_applicable=True,
        ),
        PracticalTruthV22(
            case_id="e02",
            expected_terminal="DIAGNOSED",
            expected_root_service="payment",
            expected_mechanism="CONFIGURATION_ERROR",
            evidence_applicable=True,
        ),
    )

    report = score_evidence_acquisition_runs_v221(
        combination=combination,
        runs=runs,
        truths=truths,
        bootstrap_insufficient_case_ids=("e01", "e02"),
    )

    assert report.total_runs == 2
    assert report.logical_decision_attempts == 4
    assert report.first_pass_protocol_success == 1.0
    assert report.valid_terminal_rate == 0.5
    assert report.end_to_end_exact_completion_count == 1
    assert report.policy_redirect_rate == 1.0
    assert report.policy_redirect_compliance_rate == 0.5
    assert report.repeated_premature_abstention_rate == 0.5
    assert report.adaptive_read_rate == 0.5
    assert report.read_source_distribution == {"TRACES": 1}
    assert report.successful_read_rate == 1.0
    assert report.diagnosis_after_read_rate == 1.0
    assert report.process.bootstrap_insufficient_cases == 2
    assert report.process.cases_with_at_least_one_adaptive_read == 1
    assert report.process.premature_abstain_proposals == 3
    assert report.process.redirect_to_read_conversions == 1


class _NoIncidentProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete_turn_v221(self, **kwargs: object) -> ProviderTurnOutcomeV22:
        del kwargs
        self.calls += 1
        return ProviderTurnOutcomeV22(
            decision=ControllerDecisionV22(
                decision=ControllerDecisionKindV22.NO_INCIDENT,
                working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
                action_id=NO_ACTION_ID_V22,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            ),
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            semantic_repair_used=False,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            latency_ms=0.0,
        )

    def complete_policy_redirect_turn_v221(
        self, **kwargs: object
    ) -> ProviderTurnOutcomeV22:
        del kwargs
        raise AssertionError("No-Incident must not be policy redirected")

    def complete_repair_turn(self, **kwargs: object) -> ProviderTurnOutcomeV22:
        del kwargs
        raise AssertionError("healthy No-Incident must not need repair")


def test_campaign_loads_truth_only_after_all_four_combinations(tmp_path: Path) -> None:
    development = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/development/cases.json"
    )
    healthy = next(item for item in development.cases if item.case_id == "d07")
    case_path = tmp_path / "cases.json"
    truth_path = tmp_path / "truth.json"
    case_path.write_text(
        PracticalCaseSetV22(
            schema_version="dta-v22.practical-case-set.v1",
            cases=(healthy,),
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    truth_path.write_text(
        PracticalTruthSetV221(
            schema_version="dta-v22.1.practical-truth-set.v1",
            truths=(
                PracticalTruthV22(
                    case_id="d07",
                    expected_terminal="NO_INCIDENT",
                    expected_root_service=None,
                    expected_mechanism=None,
                    evidence_applicable=False,
                ),
            ),
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    provider = _NoIncidentProvider()

    def load_after_runs(path: Path) -> PracticalTruthSetV221:
        assert provider.calls == 4
        return PracticalTruthSetV221.model_validate_json(path.read_bytes())

    result = run_evidence_acquisition_campaign_v221(
        case_set_path=case_path,
        truth_path=truth_path,
        repository_root=ROOT,
        provider=provider,  # type: ignore[arg-type]
        combinations=FINAL_STUDY_COMBINATIONS_V221,
        truth_loader=load_after_runs,
    )

    assert len(result.case_runs) == 4
    assert result.same_case_bytes_all_combinations is True
    assert result.same_normalized_case_all_combinations is True
    assert result.truth_loaded_after_all_combinations is True
    assert result.agent_writes == 0


def test_development_gate_requires_exact_8_by_2_shape_and_one_real_read() -> None:
    runs = tuple(
        _run(
            case_id=f"d{case_number:02d}",
            combination=combination,
            redirects=1 if case_number == 1 else 0,
            redirect_response=(
                ControllerDecisionKindV22.READ if case_number == 1 else None
            ),
            read_source=(EvidenceSourceV22.TRACES if case_number == 1 else None),
        )
        for case_number in range(1, 9)
        for combination in (
            StudyCombinationV221.FLAT_GATE,
            StudyCombinationV221.PLANNER_GATE,
        )
    )

    gate = evaluate_gated_development_v221(runs=runs)

    assert gate.passed is True
    assert gate.arm_runs == 16
    assert gate.cases == 8
    assert gate.uncaught_exceptions == 0
    assert gate.agent_writes == 0
    assert gate.at_least_one_gated_adaptive_read is True


def test_control_costs_and_preregistered_interpretation_are_separate() -> None:
    truths = (
        PracticalTruthV22(
            case_id="e01",
            expected_terminal="DIAGNOSED",
            expected_root_service="payment",
            expected_mechanism="CONFIGURATION_ERROR",
            evidence_applicable=True,
        ),
        PracticalTruthV22(
            case_id="e02",
            expected_terminal="DIAGNOSED",
            expected_root_service="payment",
            expected_mechanism="CONFIGURATION_ERROR",
            evidence_applicable=True,
        ),
        PracticalTruthV22(
            case_id="e09",
            expected_terminal="NO_INCIDENT",
            expected_root_service=None,
            expected_mechanism=None,
            evidence_applicable=False,
        ),
        PracticalTruthV22(
            case_id="e11",
            expected_terminal="ABSTAIN",
            expected_root_service=None,
            expected_mechanism=None,
            evidence_applicable=False,
        ),
    )
    bootstrap_ids = ("e01", "e02")

    def arm_runs(combination: StudyCombinationV221) -> tuple[PracticalCaseRunV221, ...]:
        gate = combination.policy is not TerminalExplorationPolicyV221.LEGACY
        planner_gate = combination is StudyCombinationV221.PLANNER_GATE
        incident_runs = (
            _run(
                case_id="e01",
                combination=combination,
                terminal="DIAGNOSED" if gate else "ABSTAIN",
                root="payment" if gate else None,
                mechanism="CONFIGURATION_ERROR" if gate else "UNKNOWN",
                redirects=1 if gate else 0,
                redirect_response=(ControllerDecisionKindV22.READ if gate else None),
                read_source=EvidenceSourceV22.TRACES if gate else None,
            ),
            _run(
                case_id="e02",
                combination=combination,
                terminal="DIAGNOSED" if planner_gate else "ABSTAIN",
                root="payment" if planner_gate else None,
                mechanism=(
                    "CONFIGURATION_ERROR" if planner_gate else "UNKNOWN"
                ),
                redirects=1 if gate else 0,
                redirect_response=(ControllerDecisionKindV22.READ if gate else None),
                read_source=EvidenceSourceV22.METRICS if gate else None,
            ),
        )
        controls = (
            _run(
                case_id="e09",
                combination=combination,
                terminal="NO_INCIDENT",
                root=None,
                mechanism="NO_INCIDENT",
            ),
            _run(
                case_id="e11",
                combination=combination,
                terminal="ABSTAIN",
                root=None,
                mechanism="UNKNOWN",
            ),
        )
        return incident_runs + controls

    runs_by_combination = {
        combination: arm_runs(combination)
        for combination in FINAL_STUDY_COMBINATIONS_V221
    }
    scores = tuple(
        score_evidence_acquisition_runs_v221(
            combination=combination,
            runs=runs_by_combination[combination],
            truths=truths,
            bootstrap_insufficient_case_ids=bootstrap_ids,
        )
        for combination in FINAL_STUDY_COMBINATIONS_V221
    )
    controls = (
        compute_control_cost_metrics_v221(
            arm=ControllerArmV22.FLAT_CANONICAL,
            legacy_runs=runs_by_combination[StudyCombinationV221.FLAT_LEGACY],
            gate_runs=runs_by_combination[StudyCombinationV221.FLAT_GATE],
            truths=truths,
        ),
        compute_control_cost_metrics_v221(
            arm=ControllerArmV22.PLANNER_LITE,
            legacy_runs=runs_by_combination[StudyCombinationV221.PLANNER_LEGACY],
            gate_runs=runs_by_combination[StudyCombinationV221.PLANNER_GATE],
            truths=truths,
        ),
    )

    interpretation = summarize_study_interpretation_v221(
        scores=scores,
        control_costs=controls,
    )

    assert all(item.unnecessary_read_rate == 0.0 for item in controls)
    assert all(item.no_incident_regression == 0.0 for item in controls)
    assert all(item.abstention_regression == 0.0 for item in controls)
    assert interpretation.policy_terminal == (
        "DTA_V22_1_EVIDENCE_ACQUISITION_EFFECT_OBSERVED"
    )
    assert interpretation.planner_quality_improvement_observed is True
    assert interpretation.planner_specific_interaction_observed is True
