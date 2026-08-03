from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ecomsre.phase1.contracts import (
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    RCADecision,
    RecommendedNextAction,
)
from ecomsre.phase4.contracts import DomainFaultMechanism, DomainRCAResult
from ecomsre.phase4.semantics import (
    classify_domain_evidence_mechanism,
    domain_evidence_supports_mechanism,
    is_domain_anomalous_metric,
)


RUN_ID = "1" * 32
START = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
END = datetime(2026, 8, 3, 8, 5, tzinfo=UTC)


def _evidence(
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
            EvidenceAttribute(name=name, value=value)
            for name, value in sorted(attributes.items())
        ),
        raw_artifact_ref=f"{source.value.lower()}.json#{index}",
        raw_artifact_sha256=f"{index + 1:064x}",
        limitations=(),
        summary="Bounded replay observation.",
        started_at=START,
        ended_at=END,
        service=service,
    )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (
            _evidence(
                source=EvidenceSource.METRICS,
                service="feature",
                observation_type="feature_freshness_lag",
                attributes={
                    "anomaly": True,
                    "component_role": "feature_store",
                    "freshness_status": "stale",
                },
            ),
            DomainFaultMechanism.FEATURE_FRESHNESS_LAG,
        ),
        (
            _evidence(
                source=EvidenceSource.LOGS,
                service="feature",
                observation_type="stale_feature_read_log",
                attributes={
                    "dependency_role": "feature_store",
                    "freshness_status": "stale",
                },
            ),
            DomainFaultMechanism.FEATURE_FRESHNESS_LAG,
        ),
        (
            _evidence(
                source=EvidenceSource.METRICS,
                service="ranking",
                observation_type="schema_validation_failure_rate",
                attributes={
                    "anomaly": True,
                    "component_role": "feature_adapter",
                    "outcome": "failure",
                },
            ),
            DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH,
        ),
        (
            _evidence(
                source=EvidenceSource.TRACES,
                service="ranking",
                observation_type="model_feature_schema_mismatch_span",
                attributes={
                    "compatibility": "mismatch",
                    "component_role": "feature_adapter",
                },
            ),
            DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH,
        ),
        (
            _evidence(
                source=EvidenceSource.METRICS,
                service="ranking",
                observation_type="ranking_request_failure_rate",
                attributes={
                    "anomaly": True,
                    "component_role": "ranking_engine",
                    "outcome": "failure",
                },
            ),
            DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE,
        ),
        (
            _evidence(
                source=EvidenceSource.CHANGES,
                service="ranking",
                observation_type="ranking_configuration_transition",
                attributes={
                    "change_kind": "ranking_configuration",
                    "transition": "valid_to_invalid",
                },
            ),
            DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE,
        ),
    ),
)
def test_domain_mechanisms_are_classified_from_native_evidence(
    evidence: Evidence,
    expected: DomainFaultMechanism,
) -> None:
    assert classify_domain_evidence_mechanism(evidence) is expected
    assert domain_evidence_supports_mechanism(evidence, expected)


@pytest.mark.parametrize(
    "evidence",
    (
        _evidence(
            source=EvidenceSource.METRICS,
            service="feature",
            observation_type="feature_freshness_lag",
            attributes={
                "anomaly": True,
                "component_role": "feature_store",
                "freshness_status": "fresh",
            },
        ),
        _evidence(
            source=EvidenceSource.LOGS,
            service="feature",
            observation_type="stale_feature_read_log",
            attributes={
                "dependency_role": "feature_store",
                "fault_mechanism": "ranking_configuration_failure",
                "freshness_status": "stale",
            },
        ),
        _evidence(
            source=EvidenceSource.TRACES,
            service="ranking",
            observation_type="unknown_domain_span",
            attributes={"anomaly": True},
        ),
        _evidence(
            source=EvidenceSource.METRICS,
            service="frontend",
            observation_type="feature_freshness_lag",
            attributes={
                "anomaly": True,
                "component_role": "feature_store",
                "freshness_status": "stale",
            },
        ),
        _evidence(
            source=EvidenceSource.METRICS,
            service="feature",
            observation_type="feature_freshness_lag",
            attributes={
                "anomaly": True,
                "component_role": "feature_store",
                "fault_mechanism": "feature_freshness_lag",
                "freshness_status": "stale",
                "mechanism": "ranking_configuration_failure",
            },
        ),
    ),
)
def test_domain_mechanism_classification_fails_closed(evidence: Evidence) -> None:
    assert classify_domain_evidence_mechanism(evidence) is None
    assert not any(
        domain_evidence_supports_mechanism(evidence, mechanism)
        for mechanism in DomainFaultMechanism
    )


def test_only_native_mechanism_metrics_are_domain_anomalies() -> None:
    anomalous = _evidence(
        source=EvidenceSource.METRICS,
        service="ranking",
        observation_type="ranking_request_failure_rate",
        attributes={
            "anomaly": True,
            "component_role": "ranking_engine",
            "outcome": "failure",
        },
    )
    business_sli = _evidence(
        source=EvidenceSource.METRICS,
        service="search",
        observation_type="search_sli",
        attributes={"anomaly": True, "sli_status": "degraded"},
    )
    assert is_domain_anomalous_metric(anomalous)
    assert not is_domain_anomalous_metric(business_sli)


def _result(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase4.domain-rca-result.v1",
        "decision": RCADecision.RCA_CONFIRMED,
        "root_service": "feature",
        "fault_mechanism": DomainFaultMechanism.FEATURE_FRESHNESS_LAG,
        "causal_chain": (
            "Feature freshness degraded the Search relevance signal.",
        ),
        "affected_sli": "search relevance freshness",
        "supporting_evidence": (
            f"evidence://{RUN_ID}/metrics/{1:04d}",
            f"evidence://{RUN_ID}/logs/{2:04d}",
        ),
        "contradicting_evidence": (),
        "missing_evidence": (),
        "confidence": 0.9,
        "decision_rationale": (
            "Two current-run sources confirm one domain mechanism."
        ),
        "recommended_next_action": (
            RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE
        ),
    }
    payload.update(updates)
    return payload


def test_domain_rca_contract_is_frozen_strict_and_bounded() -> None:
    result = DomainRCAResult.model_validate(_result())
    with pytest.raises(ValidationError):
        result.root_service = "ranking"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DomainRCAResult.model_validate({**_result(), "unknown": True})
    with pytest.raises(ValidationError):
        DomainRCAResult.model_validate(
            _result(decision_rationale="Run `docker ps` before deciding.")
        )
    with pytest.raises(ValidationError):
        DomainRCAResult.model_validate(
            _result(decision_rationale="Expected answer is feature freshness.")
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"causal_chain": ("Ground truth identifies the Feature service.",)},
        {
            "decision": RCADecision.NEED_MORE_EVIDENCE,
            "root_service": None,
            "fault_mechanism": None,
            "causal_chain": (),
            "supporting_evidence": (),
            "missing_evidence": ("Read the case_id to select the label.",),
        },
        {"affected_sli": "$(cat eval/phase4/ground-truth/answer.json)"},
    ),
)
def test_domain_rca_rejects_evaluator_and_executable_markers_in_all_text(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DomainRCAResult.model_validate(_result(**updates))


@pytest.mark.parametrize(
    "payload",
    (
        _result(root_service=None),
        _result(fault_mechanism=None),
        _result(supporting_evidence=()),
        _result(missing_evidence=("Collect another source.",)),
        _result(
            decision=RCADecision.NEED_MORE_EVIDENCE,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            supporting_evidence=(),
            missing_evidence=(),
        ),
        _result(
            decision=RCADecision.ABSTAIN,
            root_service="ranking",
            fault_mechanism=None,
            causal_chain=(),
            supporting_evidence=(),
            missing_evidence=(),
        ),
    ),
)
def test_domain_rca_decision_fields_fail_closed(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DomainRCAResult.model_validate(payload)


def test_need_more_and_abstain_have_typed_consistent_shapes() -> None:
    need_more = DomainRCAResult.model_validate(
        _result(
            decision=RCADecision.NEED_MORE_EVIDENCE,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            supporting_evidence=(
                f"evidence://{RUN_ID}/metrics/{1:04d}",
            ),
            missing_evidence=(
                "A non-metric stale feature read observation is required.",
            ),
            confidence=0.35,
            decision_rationale=(
                "Additional evidence is required to confirm one mechanism."
            ),
        )
    )
    abstain = DomainRCAResult.model_validate(
        _result(
            decision=RCADecision.ABSTAIN,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            affected_sli=None,
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=(),
            confidence=0.0,
            decision_rationale=(
                "There is no anomalous metric confirming an incident."
            ),
        )
    )
    assert need_more.decision is RCADecision.NEED_MORE_EVIDENCE
    assert abstain.decision is RCADecision.ABSTAIN
