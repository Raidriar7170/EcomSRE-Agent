from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError

from ecomsre.phase1.contracts import (
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    RecommendedNextAction,
    Severity,
)
from ecomsre.phase2.contracts import SpecialistRole
from ecomsre.phase5a.contracts import (
    DiagnosisDecisionV2,
    DiagnosisResultV2,
    MechanismCandidateV2,
    ObservationsStatusV2,
    SpecialistFindingV2,
    UnifiedMechanismV2,
)
from ecomsre.phase5a.semantics import (
    classify_evidence_candidate,
    evidence_contradicts_candidate,
    evidence_supports_candidate,
    is_anomalous_service_signal,
)
from ecomsre.phase5a.specialists import build_specialist_finding


RUN_ID = "a" * 32
NOW = datetime(2026, 8, 4, tzinfo=UTC)


def evidence(
    *,
    source: EvidenceSource,
    service: str,
    observation_type: str,
    attributes: dict[str, object],
    index: int = 0,
) -> Evidence:
    return Evidence(
        schema_version="phase1.evidence.v1",
        evidence_ref=(
            f"evidence://{RUN_ID}/{source.value.lower()}/{index:04d}"
        ),
        run_id=RUN_ID,
        source=source,
        observation_type=observation_type,
        attributes=tuple(
            EvidenceAttribute(
                name=name,
                value=cast(str | int | float | bool | None, value),
            )
            for name, value in sorted(attributes.items())
        ),
        raw_artifact_ref=f"{source.value.lower()}.json#{index}",
        raw_artifact_sha256="b" * 64,
        limitations=("Bounded replay observation.",),
        summary="Bounded replay observation.",
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=1),
        service=service,
    )


def incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id="incident-v2",
        alert_source_service="ad",
        summary="Ad requests are failing in the bounded replay window.",
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=5),
        affected_sli="ad request success",
        severity=Severity.SEV2,
    )


def candidate() -> MechanismCandidateV2:
    return MechanismCandidateV2(
        schema_version="phase5a.mechanism-candidate.v2",
        candidate_id="candidate-ad-request",
        run_id=RUN_ID,
        root_service="ad",
        fault_mechanism=UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE,
        claim="The ad request handler failed during the bounded window.",
        supporting_evidence=(f"evidence://{RUN_ID}/metrics/0000",),
        contradicting_evidence=(),
        missing_evidence=("A second evidence source is required.",),
        confidence=0.6,
    )


def test_candidate_rejects_overlap_and_cross_run_references() -> None:
    with pytest.raises(ValidationError, match="both support and contradict"):
        candidate().model_copy(
            update={
                "contradicting_evidence": candidate().supporting_evidence,
            }
        ).model_validate(candidate().model_copy(
            update={
                "contradicting_evidence": candidate().supporting_evidence,
            }
        ).model_dump())

    with pytest.raises(ValidationError, match="outside the current run"):
        MechanismCandidateV2(
            **{
                **candidate().model_dump(),
                "supporting_evidence": (
                    f"evidence://{'c' * 32}/metrics/0000",
                ),
            }
        )


def test_finding_requires_typed_missing_evidence_for_empty_source() -> None:
    finding = SpecialistFindingV2(
        schema_version="phase5a.specialist-finding.v2",
        finding_id="finding-logs-empty",
        run_id=RUN_ID,
        incident_id="incident-v2",
        plan_id="plan-v2",
        node_id="logs-v2",
        source=EvidenceSource.LOGS,
        specialist_role=SpecialistRole.LOGS_AGENT,
        observations_status=ObservationsStatusV2.EMPTY,
        candidates=(),
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("LOGS returned no observations.",),
        confidence=0.0,
        finding_rationale="The successful LOGS query returned no observations.",
    )
    assert finding.observations_status is ObservationsStatusV2.EMPTY

    with pytest.raises(ValidationError, match="missing evidence"):
        SpecialistFindingV2(
            **{
                **finding.model_dump(),
                "missing_evidence": (),
            }
        )


def test_diagnosis_result_enforces_decision_semantics() -> None:
    result = DiagnosisResultV2(
        schema_version="phase5a.diagnosis-result.v2",
        run_id=RUN_ID,
        decision=DiagnosisDecisionV2.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="ad request success",
        supporting_evidence=(f"evidence://{RUN_ID}/metrics/0000",),
        contradicting_evidence=(),
        missing_evidence=("A second evidence source is required.",),
        confidence=0.4,
        decision_rationale="Additional evidence is required to identify one cause.",
        recommended_next_action=(
            RecommendedNextAction.COLLECT_ADDITIONAL_READ_ONLY_TELEMETRY_EVIDENCE
        ),
    )
    assert result.root_service is None
    assert result.fault_mechanism is None

    with pytest.raises(ValidationError, match="cannot claim"):
        DiagnosisResultV2(
            **{
                **result.model_dump(),
                "root_service": "ad",
                "fault_mechanism": (
                    UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE
                ),
            }
        )


def test_unified_semantics_classify_phase1_and_phase4_mechanisms() -> None:
    request_metric = evidence(
        source=EvidenceSource.METRICS,
        service="ad",
        observation_type="request_handler_failure_rate",
        attributes={
            "anomaly": True,
            "component_role": "request_handler",
            "outcome": "failure",
        },
    )
    domain_log = evidence(
        source=EvidenceSource.LOGS,
        service="ranking",
        observation_type="model_feature_schema_mismatch_log",
        attributes={
            "compatibility": "mismatch",
            "component_role": "feature_adapter",
        },
        index=1,
    )

    assert classify_evidence_candidate(request_metric) == (
        "ad",
        UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE,
    )
    assert classify_evidence_candidate(domain_log) == (
        "ranking",
        UnifiedMechanismV2.MODEL_FEATURE_SCHEMA_MISMATCH,
    )
    assert is_anomalous_service_signal(request_metric)
    assert evidence_supports_candidate(
        request_metric,
        root_service="ad",
        fault_mechanism=UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE,
    )


def test_declared_mechanism_conflict_fails_closed() -> None:
    conflicting = evidence(
        source=EvidenceSource.TRACES,
        service="ad",
        observation_type="request_handler_failure_span",
        attributes={
            "component_role": "request_handler",
            "outcome": "failure",
            "fault_mechanism": "cache_backend_timeout",
        },
    )
    assert classify_evidence_candidate(conflicting) is None
    assert not evidence_supports_candidate(
        conflicting,
        root_service="ad",
        fault_mechanism=UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE,
    )


def test_normal_sli_and_healthy_cache_contradict_candidate() -> None:
    normal = evidence(
        source=EvidenceSource.METRICS,
        service="ad",
        observation_type="normal_request_success_rate",
        attributes={"anomaly": False, "sli_status": "normal"},
    )
    healthy_cache = evidence(
        source=EvidenceSource.TRACES,
        service="recommendation",
        observation_type="cache_client_status_span",
        attributes={"dependency_role": "cache", "status": "healthy"},
        index=1,
    )
    assert evidence_contradicts_candidate(
        normal,
        root_service="ad",
        fault_mechanism=UnifiedMechanismV2.REQUEST_PROCESSING_FAILURE,
    )
    assert evidence_contradicts_candidate(
        healthy_cache,
        root_service="recommendation",
        fault_mechanism=UnifiedMechanismV2.CACHE_BACKEND_TIMEOUT,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (ObservationsStatusV2.EMPTY, ObservationsStatusV2.EMPTY),
        (
            ObservationsStatusV2.SOURCE_UNAVAILABLE,
            ObservationsStatusV2.SOURCE_UNAVAILABLE,
        ),
    ),
)
def test_specialist_policy_preserves_empty_and_unavailable_status(
    status: ObservationsStatusV2,
    expected: ObservationsStatusV2,
) -> None:
    finding = build_specialist_finding(
        run_id=RUN_ID,
        incident=incident(),
        plan_id="plan-v2",
        node_id="logs-v2",
        source=EvidenceSource.LOGS,
        specialist_role=SpecialistRole.LOGS_AGENT,
        observations_status=status,
        evidence=(),
    )
    assert finding.observations_status is expected
    assert finding.missing_evidence
    assert finding.candidates == ()
