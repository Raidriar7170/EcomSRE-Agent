from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from typing import Any

import pytest

from ecomsre.dta_v2.v22.diagnosis import (
    CandidateActionV22,
    DiagnosisTerminalV22,
    FaultDomainV22,
    HypothesisDefinitionV22,
    MechanismV22,
    RawSemanticDiagnosisProposalV22,
    TrustedCandidateRegistryV22,
    admit_diagnosis_v22,
    filter_candidates_v22,
)
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    EvidencePredicateV22,
    FullEvidenceMemoryV22,
    LogSalientPayloadV22,
    MetricSalientPayloadV22,
    PredicateThresholdsV22,
    RuntimeObservationDetailV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
    TraceSalientPayloadV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.memory_benchmark import benchmark_fixed_trajectory_v22
from ecomsre.dta_v2.v22.predicates import (
    EvidenceSupportPolicyV22,
    PredicateExtractorV22,
    PredicateKindV22,
    build_default_evidence_support_policy_v22,
    evaluate_no_incident_v22,
    evaluate_support_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    RecentChangeRecordV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RolloutStateV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


NOW = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)


def _outcome(
    *,
    action_id: str,
    source: EvidenceSourceV22,
    records: tuple[ReadRecordV22, ...],
) -> ReadOutcomeV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action_id,
        "source": source,
        "request_sha256": semantic_sha256_v22({"action_id": action_id}),
        "status": (
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        "records": records,
        "truncated": False,
    }
    draft = ReadOutcomeV22.model_construct(
        action_id=action_id,
        source=source,
        request_sha256=payload["request_sha256"],
        status=payload["status"],
        records=records,
        truncated=False,
        schema_version="dta-v22.read-outcome.v1",
        outcome_sha256="0" * 64,
    )
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def _metric(
    service: str,
    kind: MetricKindV22,
    *,
    value: float | None,
    sample_count: int,
) -> MetricFactV22:
    units = {
        MetricKindV22.ERROR_RATE: MetricUnitV22.RATIO,
        MetricKindV22.LATENCY_P95_MS: MetricUnitV22.MILLISECONDS,
        MetricKindV22.REQUEST_SUPPORT: MetricUnitV22.COUNT,
    }
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service=service,
        metric_kind=kind,
        support_status=(
            MetricSupportStatusV22.SUPPORTED
            if sample_count
            else MetricSupportStatusV22.UNSUPPORTED
        ),
        sample_count=sample_count,
        value=value,
        unit=units[kind],
        window_started_at=NOW - timedelta(seconds=300),
        window_ended_at=NOW,
    )


def _baseline() -> BaselineProfileV22:
    return BaselineProfileV22.build(
        metric_stats=(
            ("checkout", MetricKindV22.ERROR_RATE, 0.01, 0.005),
            ("checkout", MetricKindV22.LATENCY_P95_MS, 100.0, 10.0),
            ("checkout", MetricKindV22.REQUEST_SUPPORT, 1000.0, 100.0),
            ("payment", MetricKindV22.ERROR_RATE, 0.01, 0.005),
            ("payment", MetricKindV22.LATENCY_P95_MS, 100.0, 10.0),
            ("payment", MetricKindV22.REQUEST_SUPPORT, 1000.0, 100.0),
        ),
        trace_stats=(("payment", "Charge", 20.0),),
        resource_stats=(("payment", 20.0, 0.0),),
    )


def _runtime_details(
    outcomes: tuple[ReadOutcomeV22, ...],
) -> tuple[RuntimeObservationDetailV22, ...]:
    details: list[RuntimeObservationDetailV22] = []
    for outcome in outcomes:
        for index, record in enumerate(outcome.records):
            if isinstance(record, RuntimeRecordV22):
                details.append(
                    RuntimeObservationDetailV22.build(
                        outcome=outcome,
                        record_index=index,
                        endpoint=f"http://{record.service}:8080/healthz",
                        exit_code=(
                            None
                            if record.state is RuntimeStateV22.RUNNING
                            else 137
                        ),
                    )
                )
    return tuple(details)


def _memory(
    outcomes: tuple[ReadOutcomeV22, ...],
    *,
    top_k: int,
) -> tuple[SalientEvidenceMemoryV22, FullEvidenceMemoryV22]:
    return build_memory_views_v22(
        outcomes=outcomes,
        runtime_details=_runtime_details(outcomes),
        baseline=_baseline(),
        observed_at=NOW,
        top_k=top_k,
    )


def _predicate(
    *,
    kind: PredicateKindV22,
    source: EvidenceSourceV22,
    service: str = "payment",
    parent_service: str | None = None,
    evidence_ref: str,
) -> EvidencePredicateV22:
    identity_payload = {
        "kind": kind.value,
        "source": source.value,
        "service": service,
        "parent_service": parent_service,
        "evidence_refs": (evidence_ref,),
    }
    identity = semantic_sha256_v22(identity_payload)
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.evidence-predicate.v1",
        "predicate_id": (
            f"p:{kind.value.casefold().replace('_', '-')}:"
            f"{service}:{identity[:12]}"
        ),
        "predicate_kind": kind,
        "source": source,
        "service": service,
        "parent_service": parent_service,
        "evidence_refs": (evidence_ref,),
    }
    draft = EvidencePredicateV22.model_construct(
        **payload,
        predicate_sha256="0" * 64,
    )
    return EvidencePredicateV22.model_validate(
        {
            **payload,
            "predicate_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"predicate_sha256"})
            ),
        }
    )


def _incident_outcomes() -> tuple[ReadOutcomeV22, ...]:
    return (
        _outcome(
            action_id="a:metrics:payment:core",
            source=EvidenceSourceV22.METRICS,
            records=(
                _metric(
                    "payment",
                    MetricKindV22.ERROR_RATE,
                    value=None,
                    sample_count=0,
                ),
                _metric(
                    "payment",
                    MetricKindV22.LATENCY_P95_MS,
                    value=260.0,
                    sample_count=20,
                ),
                _metric(
                    "payment",
                    MetricKindV22.REQUEST_SUPPORT,
                    value=800.0,
                    sample_count=20,
                ),
            ),
        ),
        _outcome(
            action_id="a:logs:payment",
            source=EvidenceSourceV22.LOGS,
            records=(
                LogRecordV22(
                    schema_version="dta-v22.log-record.v1",
                    observed_at=NOW,
                    service="payment",
                    severity="ERROR",
                    message="invalid config revision 482 downstream=checkout",
                ),
            ),
        ),
        _outcome(
            action_id="a:traces:payment",
            source=EvidenceSourceV22.TRACES,
            records=(
                TraceSpanV22(
                    schema_version="dta-v22.trace-span.v1",
                    observed_at=NOW,
                    service_path=("checkout", "payment"),
                    service="payment",
                    parent_service="checkout",
                    operation="Charge",
                    status=SpanStatusV22.ERROR,
                    duration_ms=80.0,
                    first_error_location=True,
                ),
            ),
        ),
        _outcome(
            action_id="a:runtime:payment",
            source=EvidenceSourceV22.RUNTIME,
            records=(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service="payment",
                    state=RuntimeStateV22.RUNNING,
                    healthy=True,
                    restart_count=0,
                ),
            ),
        ),
        _outcome(
            action_id="a:resources:payment",
            source=EvidenceSourceV22.RESOURCES,
            records=(
                ResourceUsageRecordV22(
                    schema_version="dta-v22.resource-usage-record.v1",
                    service="payment",
                    sampling_window_seconds=10,
                    samples=tuple(
                        ResourceSampleV22(
                            offset_ms=offset,
                            cpu_percent=value,
                            memory_bytes=100 + index * 20,
                        )
                        for index, (offset, value) in enumerate(
                            ((0, 10.0), (2500, 20.0), (5000, 40.0), (7500, 90.0), (10000, 95.0))
                        )
                    ),
                    memory_slope_bytes_per_second=8.0,
                ),
            ),
        ),
        _outcome(
            action_id="a:changes:payment",
            source=EvidenceSourceV22.CHANGES,
            records=(
                RecentChangeRecordV22(
                    schema_version="dta-v22.recent-change-record.v1",
                    opaque_change_id="chg_0123456789abcdef",
                    service="payment",
                    observed_at=NOW - timedelta(seconds=120),
                    category=ChangeCategoryV22.CONFIGURATION,
                    rollout_state=RolloutStateV22.COMPLETED,
                    revision_digest="2" * 64,
                ),
            ),
        ),
    )


def _no_incident_outcomes(*, include_payment_metrics: bool = True) -> tuple[ReadOutcomeV22, ...]:
    outcomes: list[ReadOutcomeV22] = []
    for service in ("checkout", "payment"):
        outcomes.append(
            _outcome(
                action_id=f"a:runtime:{service}",
                source=EvidenceSourceV22.RUNTIME,
                records=(
                    RuntimeRecordV22(
                        schema_version="dta-v22.runtime-record.v1",
                        service=service,
                        state=RuntimeStateV22.RUNNING,
                        healthy=True,
                        restart_count=0,
                    ),
                ),
            )
        )
        if service == "payment" and not include_payment_metrics:
            continue
        outcomes.append(
            _outcome(
                action_id=f"a:metrics:{service}:core",
                source=EvidenceSourceV22.METRICS,
                records=(
                    _metric(service, MetricKindV22.ERROR_RATE, value=0.01, sample_count=20),
                    _metric(service, MetricKindV22.LATENCY_P95_MS, value=100.0, sample_count=20),
                    _metric(service, MetricKindV22.REQUEST_SUPPORT, value=1000.0, sample_count=20),
                ),
            )
        )
    return tuple(outcomes)


def test_salient_memory_preserves_log_template_trace_path_and_unsupported_metric() -> None:
    outcomes = _incident_outcomes()
    salient, full = _memory(outcomes, top_k=32)

    log = next(
        fact.payload
        for fact in salient.salient_facts
        if isinstance(fact.payload, LogSalientPayloadV22)
    )
    assert "invalid config revision <num>" in log.normalized_template
    assert log.downstream_service == "checkout"

    trace = next(
        fact.payload
        for fact in salient.salient_facts
        if isinstance(fact.payload, TraceSalientPayloadV22)
    )
    assert trace.operation == "Charge"
    assert trace.service_path == ("checkout", "payment")
    assert trace.parent_service == "checkout"
    assert trace.first_error_location is True

    unsupported_fact = next(
        fact
        for fact in salient.salient_facts
        if isinstance(fact.payload, MetricSalientPayloadV22)
        and fact.payload.metric_kind is MetricKindV22.ERROR_RATE
    )
    assert isinstance(unsupported_fact.payload, MetricSalientPayloadV22)
    unsupported = unsupported_fact.payload
    assert unsupported.value is None
    assert unsupported.sample_count == 0
    assert unsupported_fact.signal_strength is SignalStrengthV22.NONE
    assert isinstance(full, FullEvidenceMemoryV22)
    assert not hasattr(full.minimal_index[0], "salient_fact_ids")
    runtime = next(
        fact.payload
        for fact in salient.salient_facts
        if isinstance(fact.payload, RuntimeSalientPayloadV22)
    )
    assert runtime.endpoint == "http://payment:8080/healthz"
    assert runtime.exit_code is None
    assert full.runtime_details == _runtime_details(outcomes)


def test_all_refs_resolve_and_loss_ledger_is_exact() -> None:
    outcomes = _incident_outcomes()
    salient, _full = _memory(outcomes, top_k=3)
    known_refs = {item.evidence_ref for item in salient.evidence_refs}
    assert len(known_refs) == sum(len(item.records) for item in outcomes)
    assert all(
        set(fact.evidence_refs).issubset(known_refs)
        for fact in salient.salient_facts
    )
    assert all(
        set(predicate.evidence_refs).issubset(known_refs)
        for predicate in salient.predicates
    )
    entries = {item.outcome_sha256: item for item in salient.loss_ledger.entries}
    for outcome in outcomes:
        entry = entries[outcome.outcome_sha256]
        assert entry.original_record_count == len(outcome.records)
        assert entry.omitted_record_count == (
            entry.original_record_count - entry.retained_fact_count
        )
        assert entry.artifact_sha256 == outcome.outcome_sha256
    assert sum(item.retained_fact_count for item in entries.values()) == 3
    assert any(
        isinstance(item.payload, RuntimeSalientPayloadV22)
        for item in salient.salient_facts
    )
    assert {
        item.payload.metric_kind
        for item in salient.salient_facts
        if isinstance(item.payload, MetricSalientPayloadV22)
        and item.payload.support_status is MetricSupportStatusV22.SUPPORTED
    } == {MetricKindV22.LATENCY_P95_MS, MetricKindV22.REQUEST_SUPPORT}
    with pytest.raises(ValueError, match="mandatory salient facts"):
        _memory(outcomes, top_k=2)


def test_predicates_are_deterministic_and_have_no_truth_input() -> None:
    first, _ = _memory(_incident_outcomes(), top_k=32)
    second, _ = _memory(_incident_outcomes(), top_k=32)
    assert first == second
    assert first.predicates == second.predicates
    forbidden = {"truth", "fixture", "expected_mechanism", "case_id"}
    assert forbidden.isdisjoint(inspect.signature(PredicateExtractorV22.extract).parameters)


def test_alternative_support_clause_accepts_and_irrelevant_refs_are_rejected() -> None:
    memory, _ = _memory(_incident_outcomes(), top_k=32)
    policy = build_default_evidence_support_policy_v22()
    support = evaluate_support_v22(
        policy=policy,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        target_service="payment",
        parent_service=None,
        predicates=memory.predicates,
    )
    assert support.accepted is True
    assert support.matched_clause_id in {
        "configuration:change-and-log",
        "configuration:change-and-error-metric",
    }

    hypothesis = HypothesisDefinitionV22.build(
        hypothesis_id="h:configuration:payment",
        target_service="payment",
        root_service="payment",
        fault_domain=FaultDomainV22.CONFIGURATION,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        root_entity_ref="service:payment",
    )
    irrelevant_ref = next(
        fact.evidence_refs[0]
        for fact in memory.salient_facts
        if isinstance(fact.payload, RuntimeSalientPayloadV22)
    )
    proposal = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_refs=(*support.supporting_evidence_refs, irrelevant_ref),
        contradicting_evidence_refs=(),
    )
    result = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=policy,
        candidate_services=("checkout", "payment"),
        budget_exhausted=False,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert result.terminal is DiagnosisTerminalV22.ABSTAIN
    assert result.result_code == "IRRELEVANT_SUPPORTING_REF"


@pytest.mark.parametrize(
    ("mechanism", "parent_service", "specs", "expected_clause"),
    (
        (
            MechanismV22.CONFIGURATION_ERROR,
            None,
            (
                (PredicateKindV22.CHANGE_RECENT_ROLLOUT, EvidenceSourceV22.CHANGES, "payment", None),
                (PredicateKindV22.METRIC_ERROR_RATE_STRONG, EvidenceSourceV22.METRICS, "payment", None),
            ),
            "configuration:change-and-error-metric",
        ),
        (
            MechanismV22.SERVICE_UNAVAILABLE,
            None,
            ((PredicateKindV22.RUNTIME_NOT_RUNNING, EvidenceSourceV22.RUNTIME, "payment", None),),
            "service-unavailable:not-running",
        ),
        (
            MechanismV22.SERVICE_UNAVAILABLE,
            None,
            (
                (PredicateKindV22.RUNTIME_UNHEALTHY, EvidenceSourceV22.RUNTIME, "payment", None),
                (PredicateKindV22.TRACE_FIRST_ERROR, EvidenceSourceV22.TRACES, "payment", None),
            ),
            "service-unavailable:unhealthy-first-error",
        ),
        (
            MechanismV22.CPU_SATURATION,
            None,
            (
                (PredicateKindV22.RESOURCE_CPU_STRONG, EvidenceSourceV22.RESOURCES, "payment", None),
                (PredicateKindV22.RUNTIME_HEALTHY, EvidenceSourceV22.RUNTIME, "payment", None),
            ),
            "cpu-saturation:resource-and-healthy",
        ),
        (
            MechanismV22.MEMORY_LEAK,
            None,
            (
                (PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG, EvidenceSourceV22.RESOURCES, "payment", None),
                (PredicateKindV22.RUNTIME_RESTART_PRESSURE, EvidenceSourceV22.RUNTIME, "payment", None),
            ),
            "memory-leak:growth-and-restarts",
        ),
        (
            MechanismV22.DEPENDENCY_LATENCY,
            "checkout",
            (
                (PredicateKindV22.TRACE_DEPENDENCY_LATENCY, EvidenceSourceV22.TRACES, "payment", "checkout"),
                (PredicateKindV22.METRIC_LATENCY_STRONG, EvidenceSourceV22.METRICS, "checkout", None),
            ),
            "dependency-latency:trace-and-metric",
        ),
    ),
)
def test_frozen_alternative_clause_families_accept(
    mechanism: MechanismV22,
    parent_service: str | None,
    specs: tuple[tuple[PredicateKindV22, EvidenceSourceV22, str, str | None], ...],
    expected_clause: str,
) -> None:
    predicates = tuple(
        _predicate(
            kind=kind,
            source=source,
            service=service,
            parent_service=predicate_parent,
            evidence_ref=f"e:a:{source.value.casefold()}:{index}:" + f"{index + 1:012x}",
        )
        for index, (kind, source, service, predicate_parent) in enumerate(specs)
    )
    result = evaluate_support_v22(
        policy=build_default_evidence_support_policy_v22(),
        mechanism=mechanism,
        target_service="payment",
        parent_service=parent_service,
        predicates=predicates,
    )
    assert result.accepted is True
    assert result.matched_clause_id == expected_clause


def test_no_incident_requires_broad_runtime_and_metric_coverage() -> None:
    complete, _ = _memory(_no_incident_outcomes(), top_k=32)
    admitted = evaluate_no_incident_v22(
        memory=complete,
        candidate_services=("checkout", "payment"),
    )
    assert admitted.accepted is True

    incomplete, _ = _memory(
        _no_incident_outcomes(include_payment_metrics=False),
        top_k=32,
    )
    denied = evaluate_no_incident_v22(
        memory=incomplete,
        candidate_services=("checkout", "payment"),
    )
    assert denied.accepted is False
    assert "METRIC_COVERAGE_INCOMPLETE" in denied.denial_reasons

    decoy = _outcome(
        action_id="a:changes:checkout",
        source=EvidenceSourceV22.CHANGES,
        records=(
            RecentChangeRecordV22(
                schema_version="dta-v22.recent-change-record.v1",
                opaque_change_id="chg_fedcba9876543210",
                service="checkout",
                observed_at=NOW - timedelta(seconds=30),
                category=ChangeCategoryV22.DEPLOYMENT,
                rollout_state=RolloutStateV22.COMPLETED,
                revision_digest="3" * 64,
            ),
        ),
    )
    with_decoy, _ = _memory((*_no_incident_outcomes(), decoy), top_k=32)
    assert evaluate_no_incident_v22(
        memory=with_decoy,
        candidate_services=("checkout", "payment"),
    ).accepted is True


def test_unknown_hypothesis_cannot_become_diagnosed() -> None:
    memory, _ = _memory(_incident_outcomes(), top_k=32)
    unknown = HypothesisDefinitionV22.build(
        hypothesis_id="h:unknown:payment",
        target_service="payment",
        root_service="payment",
        fault_domain=FaultDomainV22.UNKNOWN,
        mechanism=MechanismV22.UNKNOWN,
        root_entity_ref="service:payment",
    )
    proposal = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=unknown.hypothesis_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )
    result = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(unknown,),
        memory=memory,
        policy=build_default_evidence_support_policy_v22(),
        candidate_services=("payment",),
        budget_exhausted=True,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert result.terminal is DiagnosisTerminalV22.ABSTAIN
    assert result.admitted_diagnosis is None


def test_candidate_filter_requires_admitted_clause_trust_and_exact_target() -> None:
    memory, _ = _memory(_incident_outcomes(), top_k=32)
    policy = build_default_evidence_support_policy_v22()
    support = evaluate_support_v22(
        policy=policy,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        target_service="payment",
        parent_service=None,
        predicates=memory.predicates,
    )
    hypothesis = HypothesisDefinitionV22.build(
        hypothesis_id="h:configuration:payment",
        target_service="payment",
        root_service="payment",
        fault_domain=FaultDomainV22.CONFIGURATION,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        root_entity_ref="service:payment",
    )
    proposal = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_refs=support.supporting_evidence_refs,
        contradicting_evidence_refs=(),
    )
    admitted = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=policy,
        candidate_services=("checkout", "payment"),
        budget_exhausted=False,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    registry = TrustedCandidateRegistryV22.build(
        candidates=(
            CandidateActionV22(
                action_candidate_id="candidate:config:payment",
                target_service="payment",
                fault_domain=FaultDomainV22.CONFIGURATION,
                mechanism=MechanismV22.CONFIGURATION_ERROR,
                runbook_id="runbook:restore-config",
                trusted=True,
                backend_mode="REPLAY_ONLY",
            ),
            CandidateActionV22(
                action_candidate_id="candidate:config:checkout",
                target_service="checkout",
                fault_domain=FaultDomainV22.CONFIGURATION,
                mechanism=MechanismV22.CONFIGURATION_ERROR,
                runbook_id="runbook:restore-config",
                trusted=True,
                backend_mode="REPLAY_ONLY",
            ),
        )
    )
    candidate_set = filter_candidates_v22(
        admission=admitted,
        registry=registry,
        memory=memory,
        policy=policy,
    )
    assert tuple(item.action_candidate_id for item in candidate_set.candidates) == (
        "candidate:config:payment",
    )


def test_fixed_trajectory_benchmark_keeps_actions_fixed_and_counts_cost() -> None:
    report = benchmark_fixed_trajectory_v22(
        outcomes=(outcomes := _incident_outcomes()),
        runtime_details=_runtime_details(outcomes),
        baseline=_baseline(),
        observed_at=NOW,
        top_k=4,
    )
    assert report.provider_calls == 0
    assert report.full.action_ids == report.salient.action_ids
    assert report.full.turn_count == len(_incident_outcomes())
    assert report.salient.turn_count == len(_incident_outcomes())
    assert report.full.cumulative_serialized_bytes > 0
    assert report.salient.cumulative_estimated_tokens > 0


def test_frozen_thresholds_and_support_policy_reject_semantic_rehashes() -> None:
    threshold_data = PredicateThresholdsV22.frozen().model_dump(mode="json")
    threshold_data["recent_change_seconds"] = 60
    threshold_data["thresholds_sha256"] = semantic_sha256_v22(
        {key: value for key, value in threshold_data.items() if key != "thresholds_sha256"}
    )
    with pytest.raises(ValueError, match="frozen development values"):
        PredicateThresholdsV22.model_validate(threshold_data)

    policy = build_default_evidence_support_policy_v22()
    policy_draft = policy.model_copy(update={"clauses": policy.clauses[:-1]})
    forged_policy = policy_draft.model_copy(
        update={
            "policy_sha256": semantic_sha256_v22(
                policy_draft.model_dump(mode="json", exclude={"policy_sha256"})
            )
        }
    )
    with pytest.raises(ValueError, match="frozen clauses"):
        EvidenceSupportPolicyV22.model_validate(forged_policy.model_dump(mode="python"))


def test_memory_models_reject_rehashed_cross_field_forgeries() -> None:
    salient, full = _memory(_incident_outcomes(), top_k=32)

    forged_index = full.minimal_index[0].model_copy(
        update={"action_id": "a:logs:forged"}
    )
    full_draft = full.model_copy(
        update={"minimal_index": (forged_index, *full.minimal_index[1:])}
    )
    forged_full = full_draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                full_draft.model_dump(mode="json", exclude={"memory_sha256"})
            )
        }
    )
    with pytest.raises(ValueError, match="index does not bind"):
        FullEvidenceMemoryV22.model_validate(forged_full.model_dump(mode="python"))

    detail_draft = full.runtime_details[0].model_copy(update={"service": "checkout"})
    forged_detail = detail_draft.model_copy(
        update={
            "detail_sha256": semantic_sha256_v22(
                detail_draft.model_dump(mode="json", exclude={"detail_sha256"})
            )
        }
    )
    full_with_detail_draft = full.model_copy(
        update={"runtime_details": (forged_detail, *full.runtime_details[1:])}
    )
    full_with_detail = full_with_detail_draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                full_with_detail_draft.model_dump(
                    mode="json", exclude={"memory_sha256"}
                )
            )
        }
    )
    with pytest.raises(ValueError, match="runtime detail binding"):
        FullEvidenceMemoryV22.model_validate(full_with_detail.model_dump(mode="python"))

    first_entry = salient.loss_ledger.entries[0]
    forged_entry = first_entry.model_copy(
        update={
            "original_record_count": first_entry.original_record_count + 1,
            "omitted_record_count": first_entry.omitted_record_count + 1,
        }
    )
    ledger_draft = salient.loss_ledger.model_copy(
        update={"entries": (forged_entry, *salient.loss_ledger.entries[1:])}
    )
    forged_ledger = ledger_draft.model_copy(
        update={
            "ledger_sha256": semantic_sha256_v22(
                ledger_draft.model_dump(mode="json", exclude={"ledger_sha256"})
            )
        }
    )
    salient_draft = salient.model_copy(update={"loss_ledger": forged_ledger})
    forged_salient = salient_draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                salient_draft.model_dump(mode="json", exclude={"memory_sha256"})
            )
        }
    )
    with pytest.raises(ValueError, match="loss entry differs"):
        SalientEvidenceMemoryV22.model_validate(
            forged_salient.model_dump(mode="python")
        )


def test_abstain_requires_an_explicit_insufficiency_condition() -> None:
    no_support_outcomes = tuple(
        outcome
        for outcome in _incident_outcomes()
        if outcome.source not in {EvidenceSourceV22.CHANGES, EvidenceSourceV22.LOGS}
    )
    memory, _ = _memory(no_support_outcomes, top_k=32)
    hypothesis = HypothesisDefinitionV22.build(
        hypothesis_id="h:configuration:payment",
        target_service="payment",
        root_service="payment",
        fault_domain=FaultDomainV22.CONFIGURATION,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        root_entity_ref="service:payment",
    )
    proposal = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )
    without_reason = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=build_default_evidence_support_policy_v22(),
        candidate_services=("payment",),
        budget_exhausted=False,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert without_reason.terminal is DiagnosisTerminalV22.FAILED

    exhausted = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=build_default_evidence_support_policy_v22(),
        candidate_services=("payment",),
        budget_exhausted=True,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert exhausted.terminal is DiagnosisTerminalV22.ABSTAIN
