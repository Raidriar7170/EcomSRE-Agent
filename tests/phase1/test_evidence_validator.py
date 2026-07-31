from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.phase1.contracts import (
    AgentRunReport,
    BudgetLimits,
    BudgetSnapshot,
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    FaultMechanism,
    FinalAction,
    Incident,
    InvestigationRequest,
    LogsAction,
    MetricsAction,
    ModelCallRecord,
    ModelConfiguration,
    ModelFunctionName,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RCADecision,
    RCAResult,
    ReadOnlyToolName,
    RecommendedNextAction,
    RemainingBudgets,
    Severity,
    ToolCallRecord,
    TranscriptEntry,
)
from ecomsre.phase1.evidence import (
    EvidenceDraft,
    EvidenceStore,
    EvidenceStoreError,
    EvidenceStoreErrorCode,
)
from ecomsre.phase1.semantics import (
    classify_evidence_mechanism,
    evidence_supports_mechanism,
    is_anomalous_metric_evidence,
)
from ecomsre.phase1.validator import (
    EvidenceValidationError,
    EvidenceValidationReason,
    validate_agent_report,
    validate_rca_result,
)

UTC_START = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
UTC_END = UTC_START + timedelta(minutes=5)
RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
SHA256 = "0" * 64


def incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id="inc-001",
        summary="Checkout latency exceeds the SLO.",
        started_at=UTC_START,
        ended_at=UTC_END,
        affected_sli="checkout p95 latency",
        severity=Severity.SEV2,
        alert_source_service="decoy-alert-source",
    )


def add_evidence(
    store: EvidenceStore,
    source: EvidenceSource,
    *,
    service: str = "checkoutservice",
    mechanism: str = "request_processing_failure",
    raw_index: int = 0,
) -> Evidence:
    if mechanism == "request_processing_failure":
        observation_type = {
            EvidenceSource.METRICS: "request_handler_failure_rate",
            EvidenceSource.LOGS: "request_handler_failure_log",
            EvidenceSource.TRACES: "request_handler_failure_span",
            EvidenceSource.CHANGES: "deployment",
        }[source]
        attributes: dict[str, object] = (
            {
                "anomaly": True,
                "component_role": "request_handler",
                "outcome": "failure",
                "sample_count": 12,
            }
            if source is not EvidenceSource.CHANGES
            else {
                "release_scope": "request_path",
                "risk_signal": "request_handler_regression",
                "sample_count": 12,
            }
        )
    elif mechanism == "runtime_configuration_failure":
        observation_type = {
            EvidenceSource.METRICS: "request_error_rate",
            EvidenceSource.LOGS: "configuration_error_log",
            EvidenceSource.TRACES: "configuration_error_span",
            EvidenceSource.CHANGES: "configuration_transition",
        }[source]
        attributes_by_source: dict[
            EvidenceSource,
            dict[str, object],
        ] = {
            EvidenceSource.METRICS: {
                "anomaly": True,
                "error_rate": 0.42,
            },
            EvidenceSource.LOGS: {
                "diagnostic_kind": "configuration_parse_failure",
                "sample_count": 12,
            },
            EvidenceSource.TRACES: {
                "diagnostic_kind": "configuration_parse_failure",
                "sample_count": 12,
            },
            EvidenceSource.CHANGES: {
                "change_kind": "configuration",
                "transition": "valid_to_invalid",
            },
        }
        attributes = attributes_by_source[source]
    elif mechanism == "cache_backend_timeout":
        observation_type = {
            EvidenceSource.METRICS: "cache_timeout_rate",
            EvidenceSource.LOGS: "cache_timeout_log",
            EvidenceSource.TRACES: "cache_client_timeout_span",
            EvidenceSource.CHANGES: "deployment",
        }[source]
        attributes = {
            "anomaly": True,
            "dependency_role": "cache",
            "outcome": "timeout",
            "sample_count": 12,
        }
    else:
        observation_type = "opaque_unrecognized_signal"
        attributes = {"sample_count": 12}
    return store.add(
        source=source,
        observation_type=observation_type,
        attributes=attributes,
        raw_artifact_ref=f"{source.value.lower()}.json#{raw_index}",
        raw_artifact_sha256=SHA256,
        limitations=("fixture-backed replay only",),
        summary=f"{source.value} supports the bounded observation.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service=service,
    )


def add_native_evidence(
    store: EvidenceStore,
    source: EvidenceSource,
    *,
    service: str = "checkoutservice",
    observation_type: str,
    attributes: dict[str, object],
    raw_index: int = 0,
) -> Evidence:
    return store.add(
        source=source,
        observation_type=observation_type,
        attributes=attributes,
        raw_artifact_ref=f"{source.value.lower()}.json#{raw_index}",
        raw_artifact_sha256=SHA256,
        limitations=("fixture-backed replay only",),
        summary=f"{source.value} contains native bounded observations.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service=service,
    )


def confirmed_rca(
    metrics: Evidence,
    logs: Evidence,
    **overrides: object,
) -> RCAResult:
    payload: dict[str, object] = {
        "schema_version": "phase1.rca-result.v1",
        "decision": RCADecision.RCA_CONFIRMED,
        "root_service": "checkoutservice",
        "fault_mechanism": "request_processing_failure",
        "causal_chain": ("checkoutservice requests exceed the SLO",),
        "affected_sli": "checkout p95 latency",
        "supporting_evidence": (
            metrics.evidence_ref,
            logs.evidence_ref,
        ),
        "contradicting_evidence": (),
        "missing_evidence": (),
        "confidence": 0.8,
        "decision_rationale": (
            "Independent metrics and logs confirm the bounded root cause."
        ),
        "recommended_next_action": "Review the bounded replay evidence.",
    }
    payload.update(overrides)
    return RCAResult.model_validate(payload)


def tool_record(item: Evidence) -> ToolCallRecord:
    action: MetricsAction | LogsAction
    if item.source is EvidenceSource.METRICS:
        action = MetricsAction(
            action_type="metrics",
            started_at=UTC_START,
            ended_at=UTC_END,
            service=item.service,
        )
    elif item.source is EvidenceSource.LOGS:
        action = LogsAction(
            action_type="logs",
            started_at=UTC_START,
            ended_at=UTC_END,
            service=item.service,
        )
    else:
        raise AssertionError("test helper supports metrics and logs only")
    tool_name = (
        ReadOnlyToolName.QUERY_METRICS
        if item.source is EvidenceSource.METRICS
        else ReadOnlyToolName.SEARCH_LOGS
    )
    return ToolCallRecord(
        schema_version="phase1.tool-call-record.v1",
        call_id=f"call-{item.source.value.lower()}",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id=incident().incident_id,
        task_id="root-cause-analysis",
        tool_name=tool_name,
        action=action,
        evidence=(item,),
        evidence_refs=(item.evidence_ref,),
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=0.25,
        budget_consumed=True,
        dispatched=True,
        evidence_quarantined=False,
        usable=True,
        status="OK",
        error_code=None,
    )


def valid_report(
    store: EvidenceStore,
    result: RCAResult,
) -> AgentRunReport:
    evidence_index = store.snapshot()
    limits = BudgetLimits(
        max_model_calls=3,
        max_tool_calls=2,
        max_total_tokens=100,
    )
    tool_records = tuple(
        tool_record(item).model_copy(update={"call_id": f"tool-call-{index:04d}"})
        for index, item in enumerate(evidence_index, start=1)
    )
    model_records: list[ModelCallRecord] = []
    for model_index in range(1, len(tool_records) + 2):
        prefix = tool_records[: model_index - 1]
        model_request = ModelRequest(
            schema_version="phase1.model-request.v1",
            request_id=f"model-request-{model_index:04d}",
            run_id=RUN_ID,
            agent_id="single-agent",
            incident_id=incident().incident_id,
            task_id="root-cause-analysis",
            model_name="replay-model",
            incident=incident(),
            transcript=tuple(
                TranscriptEntry(
                    sequence=sequence,
                    action=record.action,
                    tool_name=record.tool_name,
                    status=record.status,
                    error_code=record.error_code,
                    evidence_refs=record.evidence_refs,
                )
                for sequence, record in enumerate(prefix, start=1)
            ),
            evidence=tuple(item for record in prefix for item in record.evidence),
            remaining_budgets=RemainingBudgets(
                model_calls=limits.max_model_calls - model_index,
                tool_calls=limits.max_tool_calls - len(prefix),
                total_tokens=100 - (10 * (model_index - 1)),
            ),
            allowed_actions=tuple(ModelFunctionName),
            temperature=0,
            timeout_seconds=30,
        )
        action = (
            tool_records[model_index - 1].action
            if model_index <= len(tool_records)
            else FinalAction(action_type="final", result=result)
        )
        model_response = ModelResponse(
            schema_version="phase1.model-response.v1",
            request_id=model_request.request_id,
            response_id=f"model-response-{model_index:04d}",
            run_id=RUN_ID,
            agent_id="single-agent",
            incident_id=incident().incident_id,
            task_id="root-cause-analysis",
            provider_name="scripted",
            model_name="replay-model",
            action=action,
            usage=ModelUsage(
                input_tokens=8,
                output_tokens=2,
                total_tokens=10,
            ),
            started_at=UTC_START,
            ended_at=UTC_END,
            monotonic_duration_seconds=0.25,
            error_code=None,
        )
        model_records.append(
            ModelCallRecord(
                schema_version="phase1.model-call-record.v1",
                call_id=f"model-call-{model_index:04d}",
                run_id=RUN_ID,
                agent_id="single-agent",
                incident_id=incident().incident_id,
                task_id="root-cause-analysis",
                request=model_request,
                response=model_response,
                started_at=UTC_START,
                ended_at=UTC_END,
                monotonic_duration_seconds=0.25,
                model_call_consumed=True,
                charged_tokens=10,
                status="OK",
                error_code=None,
            )
        )
    return AgentRunReport(
        schema_version="phase1.agent-run-report.v1",
        run_id=RUN_ID,
        request=InvestigationRequest(
            schema_version="phase1.investigation-request.v1",
            request_id="request-001",
            run_id=RUN_ID,
            agent_id="single-agent",
            task_id="root-cause-analysis",
            incident=incident(),
            budgets=limits,
        ),
        model_configuration=ModelConfiguration(
            model_name="replay-model",
            temperature=0,
            model_timeout_seconds=30,
        ),
        final_rca=result,
        model_call_records=tuple(model_records),
        tool_call_records=tool_records,
        evidence_index=evidence_index,
        budget_limits=limits,
        budget_snapshot=BudgetSnapshot(
            model_calls=len(model_records),
            tool_calls=len(tool_records),
            total_tokens=10 * len(model_records),
            limits=limits,
        ),
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=1.0,
        terminal_status="COMPLETED",
        terminal_reason="FINAL_RCA_ACCEPTED",
        terminal_error_code=None,
        schema_valid=True,
        evidence_references_valid=True,
    )


def report_with_model_call(
    store: EvidenceStore,
    result: RCAResult,
    **request_overrides: object,
) -> AgentRunReport:
    report = valid_report(store, result)
    existing_record = report.model_call_records[-1]
    request_payload: dict[str, object] = existing_record.request.model_dump()
    request_payload.update(request_overrides)
    request = ModelRequest.model_validate(request_payload)
    assert existing_record.response is not None
    response = existing_record.response.model_copy(
        update={
            "request_id": request.request_id,
            "model_name": request.model_name,
            "action": FinalAction(action_type="final", result=result),
        }
    )
    record = existing_record.model_copy(
        update={"request": request, "response": response}
    )
    return report.model_copy(
        update={
            "model_call_records": (
                *report.model_call_records[:-1],
                record,
            ),
        }
    )


def test_store_allocates_per_source_refs_in_deterministic_insertion_order() -> None:
    store = EvidenceStore(RUN_ID)

    first_metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    second_metrics = add_evidence(
        store,
        EvidenceSource.METRICS,
        raw_index=1,
    )

    assert first_metrics.evidence_ref == (f"evidence://{RUN_ID}/metrics/0001")
    assert logs.evidence_ref == f"evidence://{RUN_ID}/logs/0001"
    assert second_metrics.evidence_ref == (f"evidence://{RUN_ID}/metrics/0002")
    assert store.snapshot() == (first_metrics, logs, second_metrics)
    assert isinstance(store.snapshot(), tuple)


def test_store_canonicalizes_mapping_without_retaining_mutable_input() -> None:
    attributes: dict[str, object] = {
        "zeta": 2,
        "fault_mechanism": "request_processing_failure",
    }
    store = EvidenceStore(RUN_ID)

    item = store.add(
        source=EvidenceSource.METRICS,
        observation_type="incident_signal",
        attributes=attributes,
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256=SHA256,
        limitations=(),
        summary="Bounded summary.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service="checkoutservice",
    )
    attributes["fault_mechanism"] = "mutated"

    assert item.attributes == (
        EvidenceAttribute(
            name="fault_mechanism",
            value="request_processing_failure",
        ),
        EvidenceAttribute(name="zeta", value=2),
    )
    with pytest.raises(ValidationError) as frozen_error:
        item.attributes[0].value = "mutated"  # type: ignore[misc]
    assert frozen_error.value.errors()[0]["type"] == "frozen_instance"


def test_store_add_batch_rejects_invalid_second_draft_atomically() -> None:
    from ecomsre.phase1.evidence import EvidenceDraft

    store = EvidenceStore(RUN_ID)
    valid = EvidenceDraft(
        source=EvidenceSource.METRICS,
        observation_type="incident_signal",
        attributes=(
            EvidenceAttribute(
                name="fault_mechanism",
                value="request_processing_failure",
            ),
        ),
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256=SHA256,
        limitations=(),
        summary="First valid draft.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service="checkoutservice",
    )
    invalid = EvidenceDraft.model_construct(
        source=EvidenceSource.METRICS,
        observation_type="incident_signal",
        attributes=(),
        raw_artifact_ref="metrics.json#1",
        raw_artifact_sha256="not-a-sha256",
        limitations=(),
        summary="Second invalid draft.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service="checkoutservice",
    )

    with pytest.raises(EvidenceStoreError) as raised:
        store.add_batch((valid, invalid))

    assert raised.value.code is EvidenceStoreErrorCode.INVALID_INPUT
    assert store.snapshot() == ()
    first = store.add_batch((valid,))
    assert first[0].evidence_ref.endswith("/metrics/0001")


def test_store_add_batch_sequence_exhaustion_preserves_all_state() -> None:
    from ecomsre.phase1.evidence import EvidenceDraft

    store = EvidenceStore(RUN_ID)
    store._source_counters[EvidenceSource.METRICS] = 9998
    draft = EvidenceDraft(
        source=EvidenceSource.METRICS,
        observation_type="incident_signal",
        attributes=(),
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256=SHA256,
        limitations=(),
        summary="Bounded draft.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service="checkoutservice",
    )
    counters_before = dict(store._source_counters)
    snapshot_before = store.snapshot()

    with pytest.raises(EvidenceStoreError) as raised:
        store.add_batch(
            (
                draft,
                draft.model_copy(update={"raw_artifact_ref": "metrics.json#1"}),
            )
        )

    assert raised.value.code is EvidenceStoreErrorCode.SEQUENCE_EXHAUSTED
    assert store._source_counters == counters_before
    assert store.snapshot() == snapshot_before


@pytest.mark.parametrize(
    "nested_bypass",
    ("hidden_storage", "model_construct_scalar"),
)
def test_store_add_batch_deep_revalidates_each_draft_before_copy_on_write(
    nested_bypass: str,
) -> None:
    store = EvidenceStore(RUN_ID)
    existing = add_evidence(store, EvidenceSource.LOGS)
    valid_attribute = EvidenceAttribute(
        name="fault_mechanism",
        value="request_processing_failure",
    )
    invalid_attribute = (
        valid_attribute.model_copy(update={"evaluator_truth": "paymentservice"})
        if nested_bypass == "hidden_storage"
        else EvidenceAttribute.model_construct(
            name="fault_mechanism",
            value={"not": "a JSON scalar"},
        )
    )
    valid = EvidenceDraft(
        source=EvidenceSource.METRICS,
        observation_type="incident_signal",
        attributes=(valid_attribute,),
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256=SHA256,
        limitations=(),
        summary="First valid draft.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service="checkoutservice",
    )
    invalid = valid.model_copy(
        update={
            "attributes": (invalid_attribute,),
            "raw_artifact_ref": "metrics.json#1",
            "summary": "Second invalid draft.",
        }
    )
    counters_object = store._source_counters
    items_object = store._items
    by_ref_object = store._by_ref
    counters_before = dict(counters_object)
    items_before = tuple(items_object)
    by_ref_before = dict(by_ref_object)

    with pytest.raises(EvidenceStoreError) as raised:
        store.add_batch((valid, invalid))

    assert raised.value.code is EvidenceStoreErrorCode.INVALID_INPUT
    assert store._source_counters is counters_object
    assert store._items is items_object
    assert store._by_ref is by_ref_object
    assert store._source_counters == counters_before
    assert store.snapshot() == items_before == (existing,)
    assert store._by_ref == by_ref_before


def test_store_rejects_noncanonical_iterable_attribute_order() -> None:
    store = EvidenceStore(RUN_ID)

    with pytest.raises(ValueError, match="sorted|canonical"):
        store.add(
            source=EvidenceSource.METRICS,
            observation_type="incident_signal",
            attributes=(
                EvidenceAttribute(name="zeta", value=2),
                EvidenceAttribute(
                    name="fault_mechanism",
                    value="request_processing_failure",
                ),
            ),
            raw_artifact_ref="metrics.json#0",
            raw_artifact_sha256=SHA256,
            limitations=(),
            summary="Bounded summary.",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )

    assert store.snapshot() == ()


@pytest.mark.parametrize(
    ("reference", "code"),
    (
        ("not-an-evidence-ref", EvidenceStoreErrorCode.MALFORMED_REF),
        (
            f"evidence://{OTHER_RUN_ID}/metrics/0001",
            EvidenceStoreErrorCode.CROSS_RUN_REF,
        ),
        (
            f"evidence://{RUN_ID}/metrics/9999",
            EvidenceStoreErrorCode.UNKNOWN_REF,
        ),
    ),
)
def test_store_resolve_fails_closed_with_stable_codes(
    reference: str,
    code: EvidenceStoreErrorCode,
) -> None:
    store = EvidenceStore(RUN_ID)

    with pytest.raises(EvidenceStoreError) as raised:
        store.resolve(reference)

    assert raised.value.code is code
    assert str(raised.value)


@pytest.mark.parametrize(
    "raw_artifact_ref",
    (
        "../metrics.json#0",
        "/metrics.json#0",
        "metrics.json#../0",
        "https://example.test/metrics.json#0",
        "evaluator/metrics.json#0",
        "logs.json#0",
    ),
)
def test_store_rejects_traversal_url_evaluator_and_source_mismatch(
    raw_artifact_ref: str,
) -> None:
    store = EvidenceStore(RUN_ID)

    with pytest.raises(ValueError, match="raw_artifact"):
        store.add(
            source=EvidenceSource.METRICS,
            observation_type="incident_signal",
            attributes={},
            raw_artifact_ref=raw_artifact_ref,
            raw_artifact_sha256=SHA256,
            limitations=(),
            summary="Bounded summary.",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )

    assert store.snapshot() == ()


def test_store_rejects_duplicate_attribute_names() -> None:
    store = EvidenceStore(RUN_ID)

    with pytest.raises(ValueError, match="duplicate"):
        store.add(
            source=EvidenceSource.METRICS,
            observation_type="incident_signal",
            attributes=(
                EvidenceAttribute(name="duplicate", value=1),
                EvidenceAttribute(name="duplicate", value=2),
            ),
            raw_artifact_ref="metrics.json#0",
            raw_artifact_sha256=SHA256,
            limitations=(),
            summary="Bounded summary.",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )


@pytest.mark.parametrize(
    "invalid_case",
    ("source", "mixed_keys", "non_string_key", "invalid_iterable"),
)
def test_store_wraps_malformed_runtime_input_without_mutation(
    invalid_case: str,
) -> None:
    store = EvidenceStore(RUN_ID)
    kwargs: dict[str, object] = {
        "source": EvidenceSource.METRICS,
        "observation_type": "incident_signal",
        "attributes": {"fault_mechanism": "request_processing_failure"},
        "raw_artifact_ref": "metrics.json#0",
        "raw_artifact_sha256": SHA256,
        "limitations": (),
        "summary": "Bounded summary.",
        "started_at": UTC_START,
        "ended_at": UTC_END,
        "service": "checkoutservice",
    }
    if invalid_case == "source":
        kwargs["source"] = "METRICS"
    elif invalid_case == "mixed_keys":
        kwargs["attributes"] = {
            "fault_mechanism": "request_processing_failure",
            2: "invalid",
        }
    elif invalid_case == "non_string_key":
        kwargs["attributes"] = {2: "invalid"}
    else:
        kwargs["attributes"] = (object(),)

    with pytest.raises(EvidenceStoreError) as raised:
        store.add(**kwargs)  # type: ignore[arg-type]

    assert raised.value.code.value == "INVALID_INPUT"
    assert str(raised.value)
    if invalid_case == "source":
        assert "TypeError: source must be an EvidenceSource" in str(raised.value)
    assert store.snapshot() == ()
    first = add_evidence(store, EvidenceSource.METRICS)
    assert first.evidence_ref.endswith("/metrics/0001")


def test_store_sequence_exhaustion_is_typed_and_atomic() -> None:
    store = EvidenceStore(RUN_ID)
    for index in range(9999):
        store.add(
            source=EvidenceSource.METRICS,
            observation_type="incident_signal",
            attributes={"fault_mechanism": "request_processing_failure"},
            raw_artifact_ref=f"metrics.json#{index}",
            raw_artifact_sha256=SHA256,
            limitations=(),
            summary="Bounded summary.",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )
    before = store.snapshot()

    with pytest.raises(EvidenceStoreError) as raised:
        store.add(
            source=EvidenceSource.METRICS,
            observation_type="incident_signal",
            attributes={"fault_mechanism": "request_processing_failure"},
            raw_artifact_ref="metrics.json#9999",
            raw_artifact_sha256=SHA256,
            limitations=(),
            summary="Bounded summary.",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )

    assert raised.value.code is EvidenceStoreErrorCode.SEQUENCE_EXHAUSTED
    assert store.snapshot() == before
    assert before[-1].evidence_ref.endswith("/metrics/9999")


def test_validator_accepts_two_independent_matching_sources_unchanged() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs)

    validated = validate_rca_result(result, store, incident())

    assert validated is result


def test_validator_accepts_native_metric_and_request_handler_trace() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_handler_failure_rate",
        attributes={
            "anomaly": True,
            "component_role": "request_handler",
            "error_rate": 0.37,
            "outcome": "failure",
        },
    )
    traces = add_native_evidence(
        store,
        EvidenceSource.TRACES,
        observation_type="request_handler_failure_span",
        attributes={
            "component_role": "request_handler",
            "error_count": 14,
            "outcome": "failure",
        },
    )
    result = confirmed_rca(metrics, traces)

    assert validate_rca_result(result, store, incident()) is result


@pytest.mark.parametrize("mechanism", tuple(FaultMechanism))
def test_generic_anomalous_metric_does_not_support_any_fault_mechanism(
    mechanism: FaultMechanism,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_error_rate",
        attributes={"anomaly": True, "error_rate": 0.37},
    )

    assert is_anomalous_metric_evidence(metrics) is True
    assert classify_evidence_mechanism(metrics) is None
    assert evidence_supports_mechanism(metrics, mechanism) is False


@pytest.mark.parametrize(
    ("observation_type", "attributes", "expected"),
    (
        (
            "request_handler_failure_rate",
            {
                "anomaly": True,
                "component_role": "request_handler",
                "error_rate": 0.37,
                "outcome": "failure",
            },
            FaultMechanism.REQUEST_PROCESSING_FAILURE,
        ),
        (
            "cache_timeout_rate",
            {
                "anomaly": True,
                "dependency_role": "cache",
                "outcome": "timeout",
                "timeout_rate": 0.29,
            },
            FaultMechanism.CACHE_BACKEND_TIMEOUT,
        ),
    ),
)
def test_native_metric_classifier_requires_closed_mechanism_dimensions(
    observation_type: str,
    attributes: dict[str, object],
    expected: FaultMechanism,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type=observation_type,
        attributes=attributes,
    )

    assert classify_evidence_mechanism(metrics) is expected
    assert evidence_supports_mechanism(metrics, expected) is True


def test_native_mechanism_dimensions_without_anomaly_do_not_support_rca() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_handler_failure_rate",
        attributes={
            "component_role": "request_handler",
            "error_rate": 0.37,
            "outcome": "failure",
        },
    )
    traces = add_native_evidence(
        store,
        EvidenceSource.TRACES,
        observation_type="request_handler_failure_span",
        attributes={
            "component_role": "request_handler",
            "error_count": 14,
            "outcome": "failure",
        },
    )

    assert (
        classify_evidence_mechanism(metrics)
        is FaultMechanism.REQUEST_PROCESSING_FAILURE
    )
    assert is_anomalous_metric_evidence(metrics) is False
    assert (
        evidence_supports_mechanism(
            metrics,
            FaultMechanism.REQUEST_PROCESSING_FAILURE,
        )
        is False
    )
    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(confirmed_rca(metrics, traces), store, incident())
    assert raised.value.code is EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE


def test_opaque_self_labeled_observation_has_no_mechanism() -> None:
    store = EvidenceStore(RUN_ID)
    opaque = add_native_evidence(
        store,
        EvidenceSource.LOGS,
        observation_type="opaque_unrecognized_signal",
        attributes={"fault_mechanism": "request_processing_failure"},
    )

    assert classify_evidence_mechanism(opaque) is None
    assert (
        evidence_supports_mechanism(
            opaque,
            FaultMechanism.REQUEST_PROCESSING_FAILURE,
        )
        is False
    )


def test_validator_rejects_generic_metric_plus_one_mechanism_trace() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_error_rate",
        attributes={"anomaly": True, "error_rate": 0.37},
    )
    traces = add_native_evidence(
        store,
        EvidenceSource.TRACES,
        observation_type="request_handler_failure_span",
        attributes={
            "component_role": "request_handler",
            "error_count": 14,
            "outcome": "failure",
        },
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(confirmed_rca(metrics, traces), store, incident())

    assert raised.value.code is EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE


def test_validator_rejects_generic_metric_and_opaque_self_labeled_log() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_error_rate",
        attributes={"anomaly": True, "error_rate": 0.37},
    )
    logs = add_native_evidence(
        store,
        EvidenceSource.LOGS,
        observation_type="opaque_unrecognized_signal",
        attributes={"fault_mechanism": "request_processing_failure"},
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(confirmed_rca(metrics, logs), store, incident())

    assert raised.value.code is EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE


@pytest.mark.parametrize(
    "invalid_kind",
    (
        "self_report_conflict",
        "metric_self_report_conflict",
        "wrong_service",
        "one_matching_source",
    ),
)
def test_validator_rejects_invalid_native_mechanism_support(
    invalid_kind: str,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_error_rate",
        attributes={
            "anomaly": True,
            "error_rate": 0.37,
            **(
                {"fault_mechanism": "cache_backend_timeout"}
                if invalid_kind == "metric_self_report_conflict"
                else {}
            ),
        },
    )
    if invalid_kind == "self_report_conflict":
        diagnostic = add_native_evidence(
            store,
            EvidenceSource.TRACES,
            observation_type="cache_client_timeout_span",
            attributes={
                "dependency_role": "cache",
                "fault_mechanism": "request_processing_failure",
                "outcome": "timeout",
            },
        )
    elif invalid_kind == "wrong_service":
        diagnostic = add_native_evidence(
            store,
            EvidenceSource.TRACES,
            service="decoyservice",
            observation_type="request_handler_failure_span",
            attributes={
                "component_role": "request_handler",
                "error_count": 14,
                "outcome": "failure",
            },
        )
    elif invalid_kind == "one_matching_source":
        diagnostic = add_native_evidence(
            store,
            EvidenceSource.TRACES,
            observation_type="request_span",
            attributes={"error_count": 14, "outcome": "failure"},
        )
    else:
        diagnostic = add_native_evidence(
            store,
            EvidenceSource.TRACES,
            observation_type="request_handler_failure_span",
            attributes={
                "component_role": "request_handler",
                "error_count": 14,
                "outcome": "failure",
            },
        )
    result = confirmed_rca(metrics, diagnostic)

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert raised.value.code is EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE


def test_native_runtime_configuration_requires_matching_changes() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_native_evidence(
        store,
        EvidenceSource.METRICS,
        observation_type="request_error_rate",
        attributes={"anomaly": True, "error_rate": 0.42},
    )
    traces = add_native_evidence(
        store,
        EvidenceSource.TRACES,
        observation_type="configuration_error_span",
        attributes={
            "diagnostic_kind": "configuration_parse_failure",
            "error_count": 18,
        },
    )
    logs = add_native_evidence(
        store,
        EvidenceSource.LOGS,
        observation_type="configuration_error_log",
        attributes={
            "diagnostic_kind": "configuration_parse_failure",
            "sample_count": 18,
        },
    )
    result = confirmed_rca(
        metrics,
        traces,
        fault_mechanism="runtime_configuration_failure",
        supporting_evidence=(
            metrics.evidence_ref,
            traces.evidence_ref,
            logs.evidence_ref,
        ),
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert (
        raised.value.code is EvidenceValidationReason.REQUIRED_CHANGES_EVIDENCE_MISSING
    )


def test_public_plain_roundtrip_helper_rejects_hidden_storage() -> None:
    from ecomsre.phase1.validator import revalidate_phase1_model

    valid_incident = incident()
    assert revalidate_phase1_model(valid_incident, Incident) == valid_incident
    invalid_incident = valid_incident.model_copy(
        update={"evaluator_truth": "paymentservice"}
    )

    with pytest.raises(EvidenceValidationError) as raised:
        revalidate_phase1_model(invalid_incident, Incident)

    assert raised.value.code is EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("schema_version", "phase1.rca-result.invalid"),
        ("decision", "NOT_A_DECISION"),
        (
            "decision_rationale",
            "Restart checkoutservice immediately.",
        ),
    ),
)
def test_rca_validator_revalidates_model_copy_schema_bypasses(
    field_name: str,
    invalid_value: object,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    invalid = confirmed_rca(metrics, logs).model_copy(
        update={field_name: invalid_value}
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


def test_rca_validator_rejects_model_copy_injected_extra_storage() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    invalid = confirmed_rca(metrics, logs).model_copy(
        update={"evaluator_truth": "adservice"}
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


def test_rca_validator_wraps_non_string_hidden_storage_key() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    invalid = confirmed_rca(metrics, logs).model_copy(
        update={1: "x"}  # type: ignore[dict-item]
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"
    assert "int:1" in str(raised.value)


def test_rca_validator_rejects_nonempty_private_storage() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    invalid = confirmed_rca(metrics, logs).model_copy()
    object.__setattr__(
        invalid,
        "__pydantic_private__",
        {"evaluator_truth": "adservice"},
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"
    assert "private" in str(raised.value)


def test_rca_validator_rejects_unexpected_fields_set_marker() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    invalid = confirmed_rca(metrics, logs).model_copy()
    object.__setattr__(
        invalid,
        "__pydantic_fields_set__",
        {*invalid.__pydantic_fields_set__, "evaluator_truth"},
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"
    assert "fields_set" in str(raised.value)


def test_validator_rejects_support_and_contradiction_overlap() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs).model_copy(
        update={"contradicting_evidence": (metrics.evidence_ref,)}
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert raised.value.code is EvidenceValidationReason.EVIDENCE_ROLE_OVERLAP


@pytest.mark.parametrize(
    ("bad_ref", "reason"),
    (
        (
            f"evidence://{RUN_ID}/traces/0001",
            EvidenceValidationReason.UNKNOWN_EVIDENCE_REF,
        ),
        (
            f"evidence://{OTHER_RUN_ID}/traces/0001",
            EvidenceValidationReason.CROSS_RUN_EVIDENCE_REF,
        ),
        (
            "bad-ref",
            EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
        ),
    ),
)
def test_validator_rejects_unresolved_final_references(
    bad_ref: str,
    reason: EvidenceValidationReason,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs).model_copy(
        update={
            "supporting_evidence": (
                metrics.evidence_ref,
                logs.evidence_ref,
                bad_ref,
            )
        }
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert raised.value.code is reason


def test_validator_rejects_sli_mismatch() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(
        metrics,
        logs,
        affected_sli="payment error rate",
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert raised.value.code is EvidenceValidationReason.SLI_MISMATCH


@pytest.mark.parametrize(
    ("decoy_kind", "expected_reason"),
    (
        ("service", EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE),
        ("mechanism", EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE),
    ),
)
def test_decoy_service_or_mechanism_evidence_cannot_support_rca(
    decoy_kind: str,
    expected_reason: EvidenceValidationReason,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(
        store,
        EvidenceSource.LOGS,
        service="decoyservice" if decoy_kind == "service" else "checkoutservice",
        mechanism=(
            "cpu saturation"
            if decoy_kind == "mechanism"
            else "request_processing_failure"
        ),
    )
    result = confirmed_rca(metrics, logs)

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert raised.value.code is expected_reason


def test_runtime_configuration_failure_requires_matching_changes_evidence() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(
        store,
        EvidenceSource.METRICS,
        mechanism="runtime_configuration_failure",
    )
    traces = add_evidence(
        store,
        EvidenceSource.TRACES,
        mechanism="runtime_configuration_failure",
    )
    logs = add_evidence(
        store,
        EvidenceSource.LOGS,
        mechanism="runtime_configuration_failure",
    )
    result = confirmed_rca(
        metrics,
        traces,
        fault_mechanism="runtime_configuration_failure",
        supporting_evidence=(
            metrics.evidence_ref,
            traces.evidence_ref,
            logs.evidence_ref,
        ),
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_rca_result(result, store, incident())

    assert (
        raised.value.code is EvidenceValidationReason.REQUIRED_CHANGES_EVIDENCE_MISSING
    )

    changes = add_evidence(
        store,
        EvidenceSource.CHANGES,
        mechanism="runtime_configuration_failure",
    )
    valid_result = result.model_copy(
        update={
            "supporting_evidence": (
                metrics.evidence_ref,
                traces.evidence_ref,
                logs.evidence_ref,
                changes.evidence_ref,
            )
        }
    )
    assert validate_rca_result(valid_result, store, incident()) is valid_result


def test_missing_evidence_descriptions_are_never_parsed_as_references() -> None:
    store = EvidenceStore(RUN_ID)
    result = RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.NEED_MORE_EVIDENCE,
        root_service="checkoutservice",
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="checkout p95 latency",
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("evidence://not-a-reference is descriptive text only",),
        confidence=0.2,
        decision_rationale=(
            "More evidence is needed before confirming the incident cause."
        ),
        recommended_next_action=RecommendedNextAction.DOCUMENT_EVIDENCE_GAP,
    )

    assert validate_rca_result(result, store, incident()) is result


def test_report_validator_requires_exact_store_snapshot() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs)
    report = valid_report(store, result)

    assert validate_agent_report(report, store, incident()) is report

    changed_metrics = metrics.model_copy(update={"summary": "Conflicting summary."})
    changed_metrics_record = report.tool_call_records[0].model_copy(
        update={"evidence": (changed_metrics,)}
    )
    changed_model_records = tuple(
        record
        if index == 0
        else record.model_copy(
            update={
                "request": record.request.model_copy(
                    update={
                        "evidence": (
                            changed_metrics,
                            *record.request.evidence[1:],
                        )
                    }
                )
            }
        )
        for index, record in enumerate(report.model_call_records)
    )
    disagreeing = report.model_copy(
        update={
            "model_call_records": changed_model_records,
            "tool_call_records": (
                changed_metrics_record,
                report.tool_call_records[1],
            ),
            "evidence_index": (changed_metrics, logs),
        }
    )
    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(disagreeing, store, incident())

    assert raised.value.code is EvidenceValidationReason.REPORT_EVIDENCE_INDEX_MISMATCH


def test_report_validator_rejects_failed_call_evidence_even_if_bypassed() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs)
    report = valid_report(store, result)
    failed_with_evidence = report.tool_call_records[0].model_copy(
        update={
            "status": "ERROR",
            "error_code": "TIMEOUT",
        }
    )
    adversarial = report.model_copy(
        update={
            "tool_call_records": (
                failed_with_evidence,
                report.tool_call_records[1],
            )
        }
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(adversarial, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


def test_report_validator_revalidates_top_level_schema_bypass() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    report = valid_report(store, confirmed_rca(metrics, logs))
    invalid = report.model_copy(
        update={"schema_version": "phase1.agent-run-report.invalid"}
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


def test_report_validator_rejects_model_copy_injected_extra_storage() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    report = valid_report(store, confirmed_rca(metrics, logs))
    invalid = report.model_copy(update={"secret": "x"})

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


@pytest.mark.parametrize(
    "nested_kind",
    ("request", "tool_record", "evidence", "model_record"),
)
def test_report_validator_rejects_nested_model_copy_extra_storage(
    nested_kind: str,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs)
    report = (
        report_with_model_call(store, result)
        if nested_kind == "model_record"
        else valid_report(store, result)
    )

    if nested_kind == "request":
        invalid = report.model_copy(
            update={"request": report.request.model_copy(update={"secret": "x"})}
        )
    elif nested_kind == "tool_record":
        invalid_tool = report.tool_call_records[0].model_copy(update={"secret": "x"})
        invalid = report.model_copy(
            update={
                "tool_call_records": (
                    invalid_tool,
                    report.tool_call_records[1],
                )
            }
        )
    elif nested_kind == "evidence":
        invalid_metrics = metrics.model_copy(update={"evaluator_truth": "adservice"})
        invalid_tool = report.tool_call_records[0].model_copy(
            update={"evidence": (invalid_metrics,)}
        )
        invalid = report.model_copy(
            update={
                "tool_call_records": (
                    invalid_tool,
                    report.tool_call_records[1],
                ),
                "evidence_index": (invalid_metrics, logs),
            }
        )
    else:
        invalid_model_record = report.model_call_records[0].model_copy(
            update={"secret": "x"}
        )
        invalid = report.model_copy(
            update={"model_call_records": (invalid_model_record,)}
        )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


def test_report_validator_revalidates_constructed_failed_tool_record() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    report = valid_report(store, confirmed_rca(metrics, logs))
    invalid_record = ToolCallRecord.model_construct(
        schema_version="phase1.tool-call-record.v1",
        call_id="failed-without-error",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id=incident().incident_id,
        task_id="root-cause-analysis",
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        action=report.tool_call_records[0].action,
        evidence=(),
        evidence_refs=(),
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=0.25,
        status="ERROR",
        error_code=None,
    )
    invalid = report.model_copy(
        update={
            "tool_call_records": (
                invalid_record,
                report.tool_call_records[1],
            )
        }
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"


@pytest.mark.parametrize(
    ("field_name", "mismatched_value"),
    (
        ("model_name", "different-model"),
        ("temperature", 0.5),
        ("timeout_seconds", 31.0),
    ),
)
def test_report_validator_enforces_model_configuration_authority(
    field_name: str,
    mismatched_value: object,
) -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    result = confirmed_rca(metrics, logs)
    report = report_with_model_call(
        store,
        result,
        **{field_name: mismatched_value},
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(report, store, incident())

    assert raised.value.code.value == "MODEL_CONFIGURATION_MISMATCH"


def test_report_validator_revalidates_model_name_identity() -> None:
    store = EvidenceStore(RUN_ID)
    metrics = add_evidence(store, EvidenceSource.METRICS)
    logs = add_evidence(store, EvidenceSource.LOGS)
    report = valid_report(store, confirmed_rca(metrics, logs))
    last = report.model_call_records[-1]
    assert last.response is not None
    mismatched = last.model_copy(
        update={
            "response": last.response.model_copy(
                update={"model_name": "provider-alias"}
            )
        }
    )
    invalid = report.model_copy(
        update={
            "model_call_records": (
                *report.model_call_records[:-1],
                mismatched,
            )
        }
    )

    with pytest.raises(EvidenceValidationError) as raised:
        validate_agent_report(invalid, store, incident())

    assert raised.value.code.value == "SCHEMA_REVALIDATION_FAILED"
