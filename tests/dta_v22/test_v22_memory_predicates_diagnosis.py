from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from typing import Any

import pytest

from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    ObservationStatus,
    ReadToolObservation,
    RuntimeRecord,
    RuntimeState as RuntimeStateV2,
    ToolCounters,
    build_fake_read_authority,
    build_inspect_service_runtime_request,
    build_read_tool_observation,
)
from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.diagnosis import (
    CandidateActionV22,
    CandidateSetV22,
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
    MemoryReadOutcomeV22,
    MetricSalientPayloadV22,
    PredicateThresholdsV22,
    RuntimeObservationV22,
    RuntimeReadOutcomeV22,
    RuntimeSalientPayloadV22,
    SalientFactV22,
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
from ecomsre.dta_v2.v22.replay import (
    QuerySpecificReplayBackendV22,
    ReadOutcomeV22,
    ReplayCaptureV22,
)


NOW = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
RUN_ID = "1" * 32


def _runtime_source_observation(
    records: tuple[RuntimeRecordV22, ...],
) -> ReadToolObservation:
    source_records = tuple(
        RuntimeRecord(
            logical_service=record.service,
            owned_container_present=record.state is not RuntimeStateV22.ABSENT,
            state=RuntimeStateV2(record.state.value),
            health=(HealthState.HEALTHY if record.healthy else HealthState.UNHEALTHY),
            restart_count=record.restart_count,
            exit_code=(0 if record.state is RuntimeStateV22.RUNNING else 137),
            endpoint_probe_performed=record.state is RuntimeStateV22.RUNNING,
            endpoint_state=(
                EndpointState.READY
                if record.healthy
                else (
                    EndpointState.NOT_READY
                    if record.state is RuntimeStateV22.RUNNING
                    else EndpointState.NOT_APPLICABLE
                )
            ),
        )
        for record in records
    )
    request = build_inspect_service_runtime_request(
        run_id=RUN_ID,
        services=tuple(item.service for item in records),
        max_results=len(records),
    )
    return build_read_tool_observation(
        request=request,
        authority=build_fake_read_authority(),
        duplicate_of_request_sha256=None,
        status=ObservationStatus.SUCCESS,
        error_code=None,
        results=source_records,
        truncated=False,
        observed_at_start=NOW,
        observed_at_end=NOW,
        monotonic_latency_ms=0,
        counters=ToolCounters(
            dispatch_ordinal=1,
            backend_call_count=1,
            success_count=1,
            failure_count=0,
        ),
    )


def _outcome(
    *,
    action_id: str,
    source: EvidenceSourceV22,
    records: tuple[ReadRecordV22, ...],
) -> MemoryReadOutcomeV22:
    if source is EvidenceSourceV22.RUNTIME:
        runtime_records = tuple(
            record for record in records if isinstance(record, RuntimeRecordV22)
        )
        if len(runtime_records) != len(records) or not runtime_records:
            raise TypeError("runtime helper received a non-runtime record")
        services = tuple(sorted(item.service for item in runtime_records))
        topology = StaticTopologyV22.build(services=services, edges=())
        catalog = build_action_catalog_v22(
            candidate_services=services,
            topology=topology,
            capability_registry=build_default_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=20.0,
        )
        action = next(
            item
            for item in catalog.registry_actions
            if item.source is EvidenceSourceV22.RUNTIME
            and item.target_services == services
        )
        if action.action_id != action_id:
            raise ValueError("runtime test action ID is not canonical")
        source_payload: dict[str, Any] = {
            "schema_version": "dta-v22.read-outcome.v1",
            "action_id": action_id,
            "source": EvidenceSourceV22.RUNTIME,
            "request_sha256": action.request_sha256,
            "status": (
                ReadSourceStatusV22.SUCCESS_NONEMPTY
                if runtime_records
                else ReadSourceStatusV22.SUCCESS_EMPTY
            ),
            "records": runtime_records,
            "truncated": False,
        }
        source_draft = ReadOutcomeV22.model_construct(
            **source_payload, outcome_sha256="0" * 64
        )
        source_outcome = ReadOutcomeV22.model_validate(
            {
                **source_payload,
                "outcome_sha256": semantic_sha256_v22(
                    source_draft.model_dump(
                        mode="json", exclude={"outcome_sha256"}
                    )
                ),
            }
        )
        return RuntimeReadOutcomeV22.from_pr_b(
            action=action,
            source_outcome=source_outcome,
            source_observation=_runtime_source_observation(runtime_records),
        )
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


def _memory(
    outcomes: tuple[MemoryReadOutcomeV22, ...],
    *,
    top_k: int,
) -> tuple[SalientEvidenceMemoryV22, FullEvidenceMemoryV22]:
    return build_memory_views_v22(
        outcomes=outcomes,
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


def _incident_outcomes() -> tuple[MemoryReadOutcomeV22, ...]:
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


def _no_incident_outcomes(
    *, include_payment_metrics: bool = True
) -> tuple[MemoryReadOutcomeV22, ...]:
    outcomes: list[MemoryReadOutcomeV22] = []
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
    assert runtime.endpoint is EndpointState.READY
    assert runtime.exit_code == 0
    runtime_outcome = next(
        item
        for item in full.full_observations
        if isinstance(item, RuntimeReadOutcomeV22)
    )
    assert runtime_outcome.records[0].endpoint == runtime.endpoint
    assert runtime_outcome.records[0].exit_code == runtime.exit_code


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
    summaries = {
        item.outcome_sha256: item for item in salient.observation_summaries
    }
    facts = {item.fact_id: item for item in salient.salient_facts}
    for outcome in outcomes:
        entry = entries[outcome.outcome_sha256]
        retained_refs = {
            ref
            for fact_id in summaries[outcome.outcome_sha256].retained_fact_ids
            for ref in facts[fact_id].evidence_refs
        }
        assert entry.original_record_count == len(outcome.records)
        assert entry.omitted_record_count == (
            entry.original_record_count - len(retained_refs)
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


def test_pr_b_runtime_backend_is_adapted_through_canonical_authority() -> None:
    template = next(
        item
        for item in _incident_outcomes()
        if isinstance(item, RuntimeReadOutcomeV22)
    )
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=NOW,
        metrics=(),
        logs=(),
        traces=(),
        runtime=template.source_outcome.records,
        resources=(),
        changes=(),
        source_failures=(),
    )
    source_outcome = QuerySpecificReplayBackendV22(capture).execute(template.action)
    enriched = RuntimeReadOutcomeV22.from_pr_b(
        action=template.action,
        source_outcome=source_outcome,
        source_observation=template.source_observation,
    )
    assert enriched.source_outcome == source_outcome
    assert enriched.request_sha256 == template.action.request_sha256
    assert enriched.records[0].endpoint is EndpointState.READY
    assert enriched.records[0].exit_code == 0
    salient, full = _memory((enriched,), top_k=1)
    assert full.full_observations == (enriched,)
    assert salient.salient_facts[0].evidence_refs == (
        salient.evidence_refs[0].evidence_ref,
    )

    wrong_request_draft = source_outcome.model_copy(
        update={"request_sha256": "f" * 64}
    )
    wrong_request = ReadOutcomeV22.model_validate(
        wrong_request_draft.model_copy(
            update={
                "outcome_sha256": semantic_sha256_v22(
                    wrong_request_draft.model_dump(
                        mode="json",
                        exclude={"outcome_sha256"},
                    )
                )
            }
        ).model_dump(mode="python")
    )
    with pytest.raises(ValueError, match="canonical PR-B authority"):
        RuntimeReadOutcomeV22.from_pr_b(
            action=template.action,
            source_outcome=wrong_request,
            source_observation=template.source_observation,
        )


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://user:secret@payment:8080/healthz",
        "file:///" + "Users/alice/private/customer.csv",
        "http://checkout:8080/healthz",
        "http://payment:8080/" + "users/alice/private/customer.csv",
        "https://payment:8080/healthz",
    ),
)
def test_runtime_endpoint_rejects_urls_credentials_and_private_paths(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError):
        RuntimeObservationV22(
            schema_version="dta-v22.runtime-observation.v1",
            service="payment",
            state=RuntimeStateV22.RUNNING,
            healthy=True,
            endpoint=endpoint,
            restart_count=0,
            exit_code=None,
        )


def test_non_running_runtime_cannot_claim_healthy_or_emit_healthy_predicate() -> None:
    with pytest.raises(ValueError, match="cannot be healthy"):
        RuntimeObservationV22(
            schema_version="dta-v22.runtime-observation.v1",
            service="payment",
            state=RuntimeStateV22.EXITED,
            healthy=True,
            endpoint=EndpointState.NOT_APPLICABLE,
            restart_count=0,
            exit_code=0,
        )

    stopped = RuntimeObservationV22(
        schema_version="dta-v22.runtime-observation.v1",
        service="payment",
        state=RuntimeStateV22.EXITED,
        healthy=False,
        endpoint=EndpointState.NOT_APPLICABLE,
        restart_count=0,
        exit_code=0,
    )
    template = next(
        item
        for item in _incident_outcomes()
        if isinstance(item, RuntimeReadOutcomeV22)
    )
    fact = next(
        item
        for item in _memory((template,), top_k=1)[0].salient_facts
        if isinstance(item.payload, RuntimeSalientPayloadV22)
    )
    stopped_payload = fact.payload.model_copy(
        update={
            "state": stopped.state,
            "healthy": stopped.healthy,
            "exit_code": stopped.exit_code,
        }
    )
    stopped_fact_draft = fact.model_copy(update={"payload": stopped_payload})
    stopped_fact = SalientFactV22.model_validate(
        stopped_fact_draft.model_copy(
            update={
                "fact_sha256": semantic_sha256_v22(
                    stopped_fact_draft.model_dump(
                        mode="json",
                        exclude={"fact_sha256"},
                    )
                )
            }
        ).model_dump(mode="python")
    )
    predicates = PredicateExtractorV22(
        thresholds=PredicateThresholdsV22.frozen()
    ).extract(facts=(stopped_fact,))
    kinds = {item.predicate_kind for item in predicates}
    assert PredicateKindV22.RUNTIME_NOT_RUNNING in kinds
    assert PredicateKindV22.RUNTIME_UNHEALTHY in kinds
    assert PredicateKindV22.RUNTIME_HEALTHY not in kinds


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
    assert result.terminal is DiagnosisTerminalV22.FAILED
    assert result.result_code == "IRRELEVANT_SUPPORTING_REF"

    incomplete = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_refs=support.supporting_evidence_refs[:1],
        contradicting_evidence_refs=(),
    )
    incomplete_result = admit_diagnosis_v22(
        proposal=incomplete,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=policy,
        candidate_services=("checkout", "payment"),
        budget_exhausted=False,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert incomplete_result.terminal is DiagnosisTerminalV22.FAILED
    assert incomplete_result.result_code == "SUPPORTING_REFS_INCOMPLETE"


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
    registry = TrustedCandidateRegistryV22.build()
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
        outcomes=_incident_outcomes(),
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
    outcomes = _incident_outcomes()
    salient, full = _memory(outcomes, top_k=32)

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

    runtime_index = next(
        index
        for index, item in enumerate(full.full_observations)
        if isinstance(item, RuntimeReadOutcomeV22)
    )
    endpoint_memory = full.model_dump(mode="json")
    full_observations = list(endpoint_memory["full_observations"])
    forged_runtime_outcome = dict(full_observations[runtime_index])
    forged_runtime_records = list(forged_runtime_outcome["records"])
    forged_runtime_records[0] = {
        **forged_runtime_records[0],
        "endpoint": EndpointState.NOT_READY.value,
    }
    forged_runtime_outcome["records"] = tuple(forged_runtime_records)
    forged_runtime_outcome["outcome_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in forged_runtime_outcome.items()
            if key != "outcome_sha256"
        }
    )
    full_observations[runtime_index] = forged_runtime_outcome
    endpoint_memory["full_observations"] = tuple(full_observations)
    endpoint_memory["memory_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in endpoint_memory.items()
            if key != "memory_sha256"
        }
    )
    with pytest.raises(ValueError, match="canonical PR-B authority"):
        FullEvidenceMemoryV22.model_validate_json(json.dumps(endpoint_memory))

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

    missing_predicates_draft = salient.model_copy(update={"predicates": ()})
    missing_predicates = missing_predicates_draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                missing_predicates_draft.model_dump(
                    mode="json", exclude={"memory_sha256"}
                )
            )
        }
    )
    with pytest.raises(ValueError, match="authoritative predicate provenance"):
        SalientEvidenceMemoryV22.model_validate(
            missing_predicates.model_dump(mode="python")
        )

    summary_draft = salient.observation_summaries[0].model_copy(
        update={
            "status": ReadSourceStatusV22.FAILURE_TIMEOUT,
            "request_sha256": "f" * 64,
        }
    )
    forged_summary = summary_draft.model_copy(
        update={
            "summary_sha256": semantic_sha256_v22(
                summary_draft.model_dump(mode="json", exclude={"summary_sha256"})
            )
        }
    )
    summary_memory_draft = salient.model_copy(
        update={
            "observation_summaries": (
                forged_summary,
                *salient.observation_summaries[1:],
            )
        }
    )
    summary_memory = summary_memory_draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                summary_memory_draft.model_dump(
                    mode="json", exclude={"memory_sha256"}
                )
            )
        }
    )
    with pytest.raises(ValueError, match="authoritative provenance"):
        SalientEvidenceMemoryV22.model_validate(
            summary_memory.model_dump(mode="python"),
            context={"outcomes": outcomes, "baseline": _baseline(), "top_k": 32},
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


def test_no_incident_coverage_denial_requires_explicit_insufficiency_to_abstain() -> None:
    outcomes = _no_incident_outcomes(include_payment_metrics=False)
    memory, _ = _memory(outcomes, top_k=32)
    hypothesis = HypothesisDefinitionV22.build(
        hypothesis_id="h:no-incident:payment",
        target_service="payment",
        root_service="payment",
        fault_domain=FaultDomainV22.NO_INCIDENT,
        mechanism=MechanismV22.NO_INCIDENT,
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
        candidate_services=("checkout", "payment"),
        budget_exhausted=False,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert without_reason.terminal is DiagnosisTerminalV22.FAILED

    unavailable = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=build_default_evidence_support_policy_v22(),
        candidate_services=("checkout", "payment"),
        budget_exhausted=False,
        evidence_source_unavailable=True,
        conflicting_evidence=False,
    )
    assert unavailable.terminal is DiagnosisTerminalV22.ABSTAIN


def test_trace_top_k_prefers_error_spans_then_slowest_edges() -> None:
    error = TraceSpanV22(
        schema_version="dta-v22.trace-span.v1",
        observed_at=NOW,
        service_path=("checkout", "payment"),
        service="payment",
        parent_service="checkout",
        operation="ErrorOp",
        status=SpanStatusV22.ERROR,
        duration_ms=10.0,
        first_error_location=False,
    )
    slow = error.model_copy(
        update={
            "operation": "SlowOkOp",
            "status": SpanStatusV22.OK,
            "duration_ms": 200.0,
        }
    )
    fast = slow.model_copy(update={"operation": "FastOkOp", "duration_ms": 20.0})
    memory, _ = _memory(
        (
            _outcome(
                action_id="a:traces:priority",
                source=EvidenceSourceV22.TRACES,
                records=(slow, error, fast),
            ),
        ),
        top_k=1,
    )
    retained = memory.salient_facts[0].payload
    assert isinstance(retained, TraceSalientPayloadV22)
    assert retained.operation == "ErrorOp"

    slow_memory, _ = _memory(
        (
            _outcome(
                action_id="a:traces:latency-priority",
                source=EvidenceSourceV22.TRACES,
                records=(fast, slow),
            ),
        ),
        top_k=1,
    )
    slow_retained = slow_memory.salient_facts[0].payload
    assert isinstance(slow_retained, TraceSalientPayloadV22)
    assert slow_retained.operation == "SlowOkOp"


def test_log_templates_redact_sensitive_values_and_aggregate_counts() -> None:
    credential_value = "s" + "k-live-1234567890abcdef"
    absolute_path = "/" + "Users/alice/private/customer.csv"
    message = (
        f"invalid configuration token={credential_value} "
        f"email=alice@example.com path={absolute_path} "
        "url=https://private.example.test/customer?id=42 downstream=payment"
    )
    logs = (
        LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=NOW,
            service="payment",
            severity="ERROR",
            message=message,
        ),
        LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=NOW,
            service="payment",
            severity="ERROR",
            message=message,
        ),
    )
    log_outcomes = (
        _outcome(
            action_id="a:logs:sanitization",
            source=EvidenceSourceV22.LOGS,
            records=logs,
        ),
    )
    memory, _ = _memory(log_outcomes, top_k=1)
    payload = memory.salient_facts[0].payload
    assert isinstance(payload, LogSalientPayloadV22)
    assert payload.count == 2
    for forbidden in (
        "s" + "k-live",
        "alice@example.com",
        "/" + "Users/alice",
        "private.example.test",
    ):
        assert forbidden.casefold() not in payload.normalized_template
    retained_entry = memory.loss_ledger.entries[0]
    assert retained_entry.original_record_count == 2
    assert retained_entry.retained_fact_count == 1
    assert retained_entry.omitted_record_count == 0
    forged_entry = retained_entry.model_copy(update={"omitted_record_count": 1})
    ledger_draft = memory.loss_ledger.model_copy(update={"entries": (forged_entry,)})
    forged_ledger = ledger_draft.model_copy(
        update={
            "ledger_sha256": semantic_sha256_v22(
                ledger_draft.model_dump(mode="json", exclude={"ledger_sha256"})
            )
        }
    )
    forged_memory_draft = memory.model_copy(update={"loss_ledger": forged_ledger})
    forged_memory = forged_memory_draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                forged_memory_draft.model_dump(
                    mode="json", exclude={"memory_sha256"}
                )
            )
        }
    )
    with pytest.raises(ValueError, match="memory loss"):
        SalientEvidenceMemoryV22.model_validate(
            forged_memory.model_dump(mode="python"),
            context={
                "outcomes": log_outcomes,
                "baseline": _baseline(),
                "top_k": 1,
            },
        )

    omitted_memory, _ = _memory(
        (
            _outcome(
                action_id="a:logs:loss-ledger",
                source=EvidenceSourceV22.LOGS,
                records=(
                    *logs,
                    LogRecordV22(
                        schema_version="dta-v22.log-record.v1",
                        observed_at=NOW,
                        service="payment",
                        severity="WARN",
                        message="routine diagnostic heartbeat",
                    ),
                    LogRecordV22(
                        schema_version="dta-v22.log-record.v1",
                        observed_at=NOW,
                        service="payment",
                        severity="WARN",
                        message="routine diagnostic heartbeat",
                    ),
                ),
            ),
        ),
        top_k=1,
    )
    omitted_entry = omitted_memory.loss_ledger.entries[0]
    assert omitted_entry.original_record_count == 4
    assert omitted_entry.retained_fact_count == 1
    assert omitted_entry.omitted_record_count == 2


def test_candidate_registry_rejects_semantically_rehashed_fabrication() -> None:
    registry = TrustedCandidateRegistryV22.build()
    evil = CandidateActionV22(
        action_candidate_id="candidate:evil:payment",
        target_service="payment",
        fault_domain=FaultDomainV22.CONFIGURATION,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        runbook_id="runbook:evil",
        source_runbook_sha256="e" * 64,
        backend_mode="REPLAY_ONLY",
    )
    draft = registry.model_copy(
        update={
            "candidates": tuple(
                sorted(
                    (*registry.candidates, evil),
                    key=lambda item: item.action_candidate_id,
                )
            )
        }
    )
    forged = draft.model_copy(
        update={
            "registry_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"registry_sha256"})
            )
        }
    )
    with pytest.raises(ValueError, match="trusted v2.1 authority"):
        TrustedCandidateRegistryV22.model_validate(forged.model_dump(mode="python"))

    set_payload: dict[str, Any] = {
        "schema_version": "dta-v22.candidate-set.v1",
        "diagnosis_sha256": "d" * 64,
        "registry_sha256": registry.registry_sha256,
        "candidates": (evil,),
    }
    set_draft = CandidateSetV22.model_construct(
        **set_payload, candidate_set_sha256="0" * 64
    )
    with pytest.raises(ValueError, match="outside trusted authority"):
        CandidateSetV22.model_validate(
            {
                **set_payload,
                "candidate_set_sha256": semantic_sha256_v22(
                    set_draft.model_dump(
                        mode="json", exclude={"candidate_set_sha256"}
                    )
                ),
            }
        )

    canonical = registry.candidates[0]
    wrong_registry_payload: dict[str, Any] = {
        "schema_version": "dta-v22.candidate-set.v1",
        "diagnosis_sha256": "d" * 64,
        "registry_sha256": "f" * 64,
        "candidates": (canonical,),
    }
    wrong_registry_draft = CandidateSetV22.model_construct(
        **wrong_registry_payload,
        candidate_set_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="registry differs from trusted authority"):
        CandidateSetV22.model_validate(
            {
                **wrong_registry_payload,
                "candidate_set_sha256": semantic_sha256_v22(
                    wrong_registry_draft.model_dump(
                        mode="json", exclude={"candidate_set_sha256"}
                    )
                ),
            }
        )


def test_hypothesis_root_identity_is_exactly_bound_to_target() -> None:
    with pytest.raises(ValueError, match="root service differs from exact target"):
        HypothesisDefinitionV22.build(
            hypothesis_id="h:configuration:payment",
            target_service="payment",
            root_service="checkout",
            fault_domain=FaultDomainV22.CONFIGURATION,
            mechanism=MechanismV22.CONFIGURATION_ERROR,
            root_entity_ref="service:checkout",
        )
    with pytest.raises(ValueError, match="root entity differs from root service"):
        HypothesisDefinitionV22.build(
            hypothesis_id="h:configuration:payment",
            target_service="payment",
            root_service="payment",
            fault_domain=FaultDomainV22.CONFIGURATION,
            mechanism=MechanismV22.CONFIGURATION_ERROR,
            root_entity_ref="opaque:forged",
        )


def test_contradicting_refs_block_diagnosis_without_silent_ignore() -> None:
    memory, _ = _memory(_incident_outcomes(), top_k=32)
    policy = build_default_evidence_support_policy_v22()
    support = evaluate_support_v22(
        policy=policy,
        mechanism=MechanismV22.CONFIGURATION_ERROR,
        target_service="payment",
        parent_service=None,
        predicates=memory.predicates,
    )
    contradictory = next(
        ref.evidence_ref
        for ref in memory.evidence_refs
        if ref.evidence_ref not in set(support.supporting_evidence_refs)
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
        contradicting_evidence_refs=(contradictory,),
    )
    result = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=(hypothesis,),
        memory=memory,
        policy=policy,
        candidate_services=("payment",),
        budget_exhausted=False,
        evidence_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert result.terminal is DiagnosisTerminalV22.FAILED
    assert result.result_code == "CONTRADICTING_EVIDENCE_PRESENT"
