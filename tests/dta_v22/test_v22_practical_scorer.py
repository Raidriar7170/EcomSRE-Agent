from __future__ import annotations

from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalCaseRunV22,
    PracticalRunStatusV22,
)
from ecomsre.dta_v2.v22.practical_scorer import (
    PracticalTruthV22,
    ScoredOutcomeV22,
    score_practical_runs_v22,
)


def _run(
    *,
    case_id: str,
    status: PracticalRunStatusV22,
    terminal: str | None,
    root: str | None,
    mechanism: str | None,
) -> PracticalCaseRunV22:
    return PracticalCaseRunV22(
        schema_version="dta-v22.practical-case-run.v1",
        case_id=case_id,
        arm=ControllerArmV22.FLAT_CANONICAL,
        case_bytes_sha256="1" * 64,
        status=status,
        terminal=terminal,
        root_service=root,
        mechanism=mechanism,
        supporting_evidence_refs=("e:a:metrics:payment:core:0:123456789abc",)
        if root is not None
        else (),
        evidence_ref_valid=status is PracticalRunStatusV22.VALID_TERMINAL,
        semantic_clause_valid=status is PracticalRunStatusV22.VALID_TERMINAL,
        adaptive_reads=1,
        duplicate_read_attempts=0,
        provider_turns=2,
        provider_calls=2,
        first_pass_protocol_successes=2,
        post_repair_protocol_successes=2,
        semantic_repairs=0,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=1.0,
        transport_retry_count=0,
        uncaught_exceptions=0,
        safe_error_code=None,
        agent_writes=0,
        planner_ledger_visible=False,
    )


def test_scorer_uses_explicit_applicability_and_partial_run_taxonomy() -> None:
    truths = (
        PracticalTruthV22(
            case_id="incident",
            expected_terminal="DIAGNOSED",
            expected_root_service="payment",
            expected_mechanism="CONFIGURATION_ERROR",
            evidence_applicable=True,
        ),
        PracticalTruthV22(
            case_id="healthy",
            expected_terminal="NO_INCIDENT",
            expected_root_service=None,
            expected_mechanism=None,
            evidence_applicable=False,
        ),
        PracticalTruthV22(
            case_id="missing",
            expected_terminal="ABSTAIN",
            expected_root_service=None,
            expected_mechanism=None,
            evidence_applicable=False,
        ),
        PracticalTruthV22(
            case_id="transport",
            expected_terminal="DIAGNOSED",
            expected_root_service="email",
            expected_mechanism="MEMORY_LEAK",
            evidence_applicable=True,
        ),
    )
    runs = (
        _run(
            case_id="incident",
            status=PracticalRunStatusV22.VALID_TERMINAL,
            terminal="DIAGNOSED",
            root="payment",
            mechanism="CONFIGURATION_ERROR",
        ),
        _run(
            case_id="healthy",
            status=PracticalRunStatusV22.VALID_TERMINAL,
            terminal="NO_INCIDENT",
            root=None,
            mechanism="NO_INCIDENT",
        ),
        _run(
            case_id="missing",
            status=PracticalRunStatusV22.VALID_TERMINAL,
            terminal="NO_INCIDENT",
            root=None,
            mechanism="NO_INCIDENT",
        ),
        _run(
            case_id="transport",
            status=PracticalRunStatusV22.TRANSPORT_FAILED,
            terminal=None,
            root=None,
            mechanism=None,
        ),
    )

    report = score_practical_runs_v22(runs=runs, truths=truths)

    assert tuple(item.outcome for item in report.scored_runs) == (
        ScoredOutcomeV22.COMPLETED_CORRECT,
        ScoredOutcomeV22.COMPLETED_CORRECT,
        ScoredOutcomeV22.SEMANTICALLY_WRONG,
        ScoredOutcomeV22.TRANSPORT_FAILED,
    )
    assert report.incident_denominator == 2
    assert report.no_incident_denominator == 1
    assert report.abstention_denominator == 1
    assert report.evidence_denominator == 2
    assert report.run_completion_rate == 0.5
    assert report.evidence_ref_validity == 0.5
    assert report.semantic_evidence_clause_validity == 0.5
    assert report.mean_provider_turns == 2.0
    assert report.root_service_accuracy == 0.5
    assert report.no_incident_accuracy == 1.0
    assert report.abstention_accuracy == 0.0
    assert not hasattr(report, "action_success")


def test_incident_abstention_does_not_count_as_valid_cited_evidence() -> None:
    truth = (
        PracticalTruthV22(
            case_id="incident",
            expected_terminal="DIAGNOSED",
            expected_root_service="payment",
            expected_mechanism="CONFIGURATION_ERROR",
            evidence_applicable=True,
        ),
    )
    misleading = _run(
        case_id="incident",
        status=PracticalRunStatusV22.VALID_TERMINAL,
        terminal="ABSTAIN",
        root=None,
        mechanism="UNKNOWN",
    ).model_copy(
        update={
            "supporting_evidence_refs": (),
            "evidence_ref_valid": True,
            "semantic_clause_valid": True,
            "provider_turns": 1,
            "provider_calls": 2,
        }
    )

    report = score_practical_runs_v22(runs=(misleading,), truths=truth)

    assert report.run_completion_rate == 0.0
    assert report.evidence_ref_validity == 0.0
    assert report.semantic_evidence_clause_validity == 0.0
    assert report.mean_provider_turns == 2.0
    assert report.scored_runs[0].evidence_ref_valid is False
    assert report.scored_runs[0].semantic_clause_valid is False
