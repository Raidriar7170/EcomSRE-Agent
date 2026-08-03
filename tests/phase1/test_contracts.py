from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

import ecomsre.phase1.contracts as contracts_module
from ecomsre.phase1.contracts import (
    Action,
    AgentRunReport,
    BudgetLimits,
    BudgetSnapshot,
    ChangesAction,
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    FinalAction,
    Hypothesis,
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
    RemainingBudgets,
    StableErrorCode,
    ToolCallRecord,
    TranscriptEntry,
    TracesAction,
)

UTC_START = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
UTC_END = UTC_START + timedelta(minutes=5)
RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
METRICS_REF = f"evidence://{RUN_ID}/metrics/0001"
LOGS_REF = f"evidence://{RUN_ID}/logs/0002"
TRACES_REF = f"evidence://{RUN_ID}/traces/0003"
RECOMMENDED_ACTION_VALUES = (
    "Review the bounded replay evidence.",
    "Review the available read-only observations.",
    "Inspect the read-only evidence index.",
    "Collect additional read-only telemetry evidence.",
    "Compare the available read-only observations.",
    "Preserve the current replay evidence.",
    "Retain the read-only observations.",
    "Request additional read-only evidence from the service owner.",
    "Examine the evidence gaps.",
    "Validate additional observations against the incident window.",
    "Correlate the available read-only observations.",
    "Continue monitoring the affected SLI.",
    "Monitor the affected SLI.",
    "Document the evidence gap.",
    "Await additional observations.",
    "Ask the service owner to review the evidence.",
)


def incident_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase1.incident.v1",
        "incident_id": "inc-001",
        "summary": "Checkout latency exceeds the SLO.",
        "started_at": UTC_START,
        "ended_at": UTC_END,
        "affected_sli": "checkout p95 latency",
        "severity": "SEV2",
    }
    payload.update(overrides)
    return payload


def rca_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase1.rca-result.v1",
        "decision": RCADecision.RCA_CONFIRMED,
        "root_service": "checkoutservice",
        "fault_mechanism": "request_processing_failure",
        "causal_chain": (
            "checkoutservice waits on paymentservice",
            "request latency exceeds the SLO",
        ),
        "affected_sli": "checkout p95 latency",
        "supporting_evidence": (METRICS_REF, LOGS_REF),
        "contradicting_evidence": (),
        "missing_evidence": (),
        "confidence": 0.82,
        "decision_rationale": (
            "The incident is confirmed by independent metrics and log observations."
        ),
        "recommended_next_action": RECOMMENDED_ACTION_VALUES[0],
    }
    payload.update(overrides)
    return payload


def evidence(
    evidence_ref: str = METRICS_REF,
    source: EvidenceSource = EvidenceSource.METRICS,
) -> Evidence:
    source_name = source.value.lower()
    return Evidence(
        schema_version="phase1.evidence.v1",
        evidence_ref=evidence_ref,
        run_id=evidence_ref.split("/")[2],
        source=source,
        observation_type="latency_observation",
        attributes=(
            EvidenceAttribute(
                name="fault_mechanism",
                value="request_processing_failure",
            ),
        ),
        raw_artifact_ref=f"{source_name}.json#0",
        raw_artifact_sha256="0" * 64,
        limitations=(),
        summary="Observed checkout latency increase.",
        started_at=UTC_START,
        ended_at=UTC_END,
        service="checkoutservice",
    )


def model_request() -> ModelRequest:
    return ModelRequest(
        schema_version="phase1.model-request.v1",
        request_id="model-request-001",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id="inc-001",
        task_id="root-cause-analysis",
        model_name="replay-model",
        incident=Incident.model_validate(incident_payload()),
        transcript=(),
        evidence=(),
        remaining_budgets=RemainingBudgets(
            model_calls=7,
            tool_calls=8,
            total_tokens=12_000,
        ),
        allowed_actions=tuple(ModelFunctionName),
        temperature=0,
        timeout_seconds=30.0,
    )


def model_response(
    *,
    error_code: StableErrorCode | None = None,
) -> ModelResponse:
    return ModelResponse(
        schema_version="phase1.model-response.v1",
        request_id="model-request-001",
        response_id="model-response-001",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id="inc-001",
        task_id="root-cause-analysis",
        provider_name="scripted",
        model_name="replay-model",
        action=MetricsAction(
            action_type="metrics",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
        usage=ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=0.25,
        error_code=error_code,
    )


def model_call_payload(
    response: ModelResponse | None,
    *,
    status: str,
    error_code: StableErrorCode | None,
) -> dict[str, object]:
    return {
        "schema_version": "phase1.model-call-record.v1",
        "call_id": "model-call-001",
        "run_id": RUN_ID,
        "agent_id": "single-agent",
        "incident_id": "inc-001",
        "task_id": "root-cause-analysis",
        "request": model_request(),
        "response": response,
        "started_at": UTC_START,
        "ended_at": UTC_END,
        "monotonic_duration_seconds": 0.25,
        "model_call_consumed": True,
        "charged_tokens": (
            response.usage.total_tokens
            if response is not None and status == "OK"
            else 0
        ),
        "status": status,
        "error_code": error_code,
    }


def tool_record_with(
    evidence_items: tuple[Evidence, ...],
) -> ToolCallRecord:
    source = evidence_items[0].source
    action_by_source: dict[
        EvidenceSource,
        MetricsAction | LogsAction | TracesAction | ChangesAction,
    ] = {
        EvidenceSource.METRICS: MetricsAction(
            action_type="metrics",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
        EvidenceSource.LOGS: LogsAction(
            action_type="logs",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
        EvidenceSource.TRACES: TracesAction(
            action_type="traces",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
        EvidenceSource.CHANGES: ChangesAction(
            action_type="changes",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
    }
    action = action_by_source[source]
    tool_name = {
        EvidenceSource.METRICS: ReadOnlyToolName.QUERY_METRICS,
        EvidenceSource.LOGS: ReadOnlyToolName.SEARCH_LOGS,
        EvidenceSource.TRACES: ReadOnlyToolName.SEARCH_TRACES,
        EvidenceSource.CHANGES: ReadOnlyToolName.LIST_CHANGES,
    }[source]
    return ToolCallRecord(
        schema_version="phase1.tool-call-record.v1",
        call_id=f"tool-call-{source.value.lower()}",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id="inc-001",
        task_id="root-cause-analysis",
        tool_name=tool_name,
        action=action,
        evidence=evidence_items,
        evidence_refs=tuple(item.evidence_ref for item in evidence_items),
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


def tool_record_payload(
    action: MetricsAction | LogsAction | TracesAction | ChangesAction,
    evidence_items: tuple[Evidence, ...],
    *,
    call_id: str = "tool-call-test",
    status: str = "OK",
    error_code: StableErrorCode | None = None,
) -> dict[str, object]:
    tool_name = {
        "metrics": ReadOnlyToolName.QUERY_METRICS,
        "logs": ReadOnlyToolName.SEARCH_LOGS,
        "traces": ReadOnlyToolName.SEARCH_TRACES,
        "changes": ReadOnlyToolName.LIST_CHANGES,
    }[action.action_type]
    return {
        "schema_version": "phase1.tool-call-record.v1",
        "call_id": call_id,
        "run_id": RUN_ID,
        "agent_id": "single-agent",
        "incident_id": "inc-001",
        "task_id": "root-cause-analysis",
        "tool_name": tool_name,
        "action": action,
        "evidence": evidence_items,
        "evidence_refs": tuple(
            item.evidence_ref for item in evidence_items
        ),
        "started_at": UTC_START,
        "ended_at": UTC_END,
        "monotonic_duration_seconds": 0.25,
        "budget_consumed": True,
        "dispatched": True,
        "evidence_quarantined": False,
        "usable": status == "OK",
        "status": status,
        "error_code": error_code,
    }


def completed_model_record(result: RCAResult) -> ModelCallRecord:
    request = model_request().model_copy(
        update={"request_id": "model-request-0001"}
    )
    response = ModelResponse(
        schema_version="phase1.model-response.v1",
        request_id=request.request_id,
        response_id="model-response-0001",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id="inc-001",
        task_id="root-cause-analysis",
        provider_name="scripted",
        model_name="replay-model",
        action=FinalAction(action_type="final", result=result),
        usage=ModelUsage(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=0.25,
        error_code=None,
    )
    return ModelCallRecord(
        schema_version="phase1.model-call-record.v1",
        call_id="model-call-0001",
        run_id=RUN_ID,
        agent_id="single-agent",
        incident_id="inc-001",
        task_id="root-cause-analysis",
        request=request,
        response=response,
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=0.25,
        model_call_consumed=True,
        charged_tokens=120,
        status="OK",
        error_code=None,
    )


def completed_model_records(
    result: RCAResult,
    tool_records: tuple[ToolCallRecord, ...],
) -> tuple[ModelCallRecord, ...]:
    base = completed_model_record(result)
    records: list[ModelCallRecord] = []
    for model_index in range(1, len(tool_records) + 2):
        prefix = tool_records[: model_index - 1]
        request = base.request.model_copy(
            update={
                "request_id": f"model-request-{model_index:04d}",
                "transcript": tuple(
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
                "evidence": tuple(
                    item for record in prefix for item in record.evidence
                ),
                "remaining_budgets": RemainingBudgets(
                    model_calls=8 - model_index,
                    tool_calls=8 - len(prefix),
                    total_tokens=12_000 - (120 * (model_index - 1)),
                ),
            }
        )
        assert base.response is not None
        action = (
            tool_records[model_index - 1].action
            if model_index <= len(tool_records)
            else FinalAction(action_type="final", result=result)
        )
        response = base.response.model_copy(
            update={
                "request_id": request.request_id,
                "response_id": f"model-response-{model_index:04d}",
                "action": action,
            }
        )
        records.append(
            base.model_copy(
                update={
                    "call_id": f"model-call-{model_index:04d}",
                    "request": request,
                    "response": response,
                }
            )
        )
    return tuple(records)


def valid_report_payload(**overrides: object) -> dict[str, object]:
    metrics = evidence()
    logs = evidence(LOGS_REF, EvidenceSource.LOGS)
    limits = BudgetLimits(
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=12_000,
    )
    final_rca = RCAResult.model_validate(rca_payload())
    metrics_record = tool_record_with((metrics,)).model_copy(
        update={"call_id": "tool-call-0001"}
    )
    logs_record = tool_record_with((logs,)).model_copy(
        update={"call_id": "tool-call-0002"}
    )
    tool_records = (metrics_record, logs_record)
    model_records = completed_model_records(final_rca, tool_records)
    payload: dict[str, object] = {
        "schema_version": "phase1.agent-run-report.v1",
        "run_id": RUN_ID,
        "request": InvestigationRequest(
            schema_version="phase1.investigation-request.v1",
            request_id="request-001",
            run_id=RUN_ID,
            agent_id="single-agent",
            task_id="root-cause-analysis",
            incident=Incident.model_validate(incident_payload()),
            budgets=limits,
        ),
        "model_configuration": ModelConfiguration(
            model_name="replay-model",
            temperature=0,
            model_timeout_seconds=30.0,
        ),
        "final_rca": final_rca,
        "model_call_records": model_records,
        "tool_call_records": tool_records,
        "evidence_index": (metrics, logs),
        "budget_limits": limits,
        "budget_snapshot": BudgetSnapshot(
            model_calls=len(model_records),
            tool_calls=2,
            total_tokens=120 * len(model_records),
            limits=limits,
        ),
        "started_at": UTC_START,
        "ended_at": UTC_END,
        "monotonic_duration_seconds": 2.5,
        "terminal_status": "COMPLETED",
        "terminal_reason": "FINAL_RCA_ACCEPTED",
        "terminal_error_code": None,
        "schema_valid": True,
        "evidence_references_valid": True,
    }
    payload.update(overrides)
    if "tool_call_records" in overrides:
        overridden_records = payload["tool_call_records"]
        assert isinstance(overridden_records, tuple)
        payload["tool_call_records"] = tuple(
            record.model_copy(
                update={"call_id": f"tool-call-{index:04d}"}
            )
            for index, record in enumerate(overridden_records, start=1)
        )
    if "model_call_records" not in overrides and (
        "tool_call_records" in overrides or "final_rca" in overrides
    ):
        effective_final = payload["final_rca"]
        linked_final = (
            effective_final
            if isinstance(effective_final, RCAResult)
            else final_rca
        )
        effective_tools = payload["tool_call_records"]
        assert isinstance(effective_tools, tuple)
        payload["model_call_records"] = completed_model_records(
            linked_final,
            effective_tools,
        )
    if "budget_snapshot" not in overrides:
        effective_models = payload["model_call_records"]
        effective_tools = payload["tool_call_records"]
        assert isinstance(effective_models, tuple)
        assert isinstance(effective_tools, tuple)
        payload["budget_snapshot"] = BudgetSnapshot(
            model_calls=len(effective_models),
            tool_calls=sum(record.budget_consumed for record in effective_tools),
            total_tokens=sum(record.charged_tokens for record in effective_models),
            limits=limits,
        )
    return payload


def rebind_report_limits(
    payload: dict[str, object],
    limits: BudgetLimits,
) -> dict[str, object]:
    request = payload["request"]
    snapshot = payload["budget_snapshot"]
    model_records = payload["model_call_records"]
    tool_records = payload["tool_call_records"]
    assert isinstance(request, InvestigationRequest)
    assert isinstance(snapshot, BudgetSnapshot)
    assert isinstance(model_records, tuple)
    assert isinstance(tool_records, tuple)
    rebound_records: list[ModelCallRecord] = []
    preceding_tokens = 0
    for call_index, record in enumerate(model_records, start=1):
        assert isinstance(record, ModelCallRecord)
        prefix_length = len(record.request.transcript)
        consumed_prefix_tools = sum(
            item.budget_consumed
            for item in tool_records[:prefix_length]
        )
        remaining = RemainingBudgets(
            model_calls=limits.max_model_calls - call_index,
            tool_calls=limits.max_tool_calls - consumed_prefix_tools,
            total_tokens=limits.max_total_tokens - preceding_tokens,
        )
        rebound_records.append(
            record.model_copy(
                update={
                    "request": record.request.model_copy(
                        update={"remaining_budgets": remaining}
                    )
                }
            )
        )
        preceding_tokens += record.charged_tokens
    return {
        **payload,
        "request": request.model_copy(update={"budgets": limits}),
        "model_call_records": tuple(rebound_records),
        "budget_limits": limits,
        "budget_snapshot": snapshot.model_copy(update={"limits": limits}),
    }


def test_incident_hint_is_optional_and_non_authoritative() -> None:
    without_hint = Incident.model_validate(incident_payload())
    wrong_hint = Incident.model_validate(
        incident_payload(alert_source_service="unrelated-service")
    )

    assert without_hint.alert_source_service is None
    assert wrong_hint.alert_source_service == "unrelated-service"
    assert "root_cause" not in Incident.model_fields
    assert "alert_source_evidence" not in Incident.model_fields


@pytest.mark.parametrize(
    "extra_field",
    (
        {"root_cause": "paymentservice"},
        {"alert_source_evidence": METRICS_REF},
        {"evaluator_root_service": "paymentservice"},
    ),
)
def test_incident_forbids_evaluator_truth_and_hint_evidence_conversion(
    extra_field: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate(incident_payload(**extra_field))


def test_contracts_are_frozen_and_forbid_extra_fields() -> None:
    incident = Incident.model_validate(incident_payload())

    with pytest.raises(ValidationError):
        Incident.model_validate(incident_payload(unexpected=True))
    with pytest.raises(ValidationError):
        incident.summary = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad_time",
    (
        datetime(2026, 7, 31, 1, 0),  # noqa: DTZ001 - intentional naive input
        datetime(2026, 7, 31, 9, 0, tzinfo=timezone(timedelta(hours=8))),
    ),
)
def test_incident_requires_time_aware_utc_timestamps(bad_time: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        Incident.model_validate(incident_payload(started_at=bad_time))


def test_intervals_are_closed_and_ordered() -> None:
    point_incident = Incident.model_validate(
        incident_payload(started_at=UTC_START, ended_at=UTC_START)
    )
    assert point_incident.started_at == point_incident.ended_at

    with pytest.raises(ValidationError, match="precedes"):
        Incident.model_validate(
            incident_payload(started_at=UTC_END, ended_at=UTC_START)
        )


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("incident_id", 123),
        ("alert_source_service", 123),
    ),
)
def test_before_validators_return_validation_error_for_non_string_scalars(
    field_name: str,
    bad_value: object,
) -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate(incident_payload(**{field_name: bad_value}))


def test_optional_claim_before_validator_rejects_integer_with_validation_error() -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(root_service=123))


def test_tuple_entries_reject_non_strings_with_validation_error() -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(causal_chain=(object(),)))
    payload = model_request().model_dump()
    payload["transcript"] = (123,)
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("incident_id", "i" * 129),
        ("alert_source_service", "s" * 257),
        ("summary", "x" * 2001),
        ("affected_sli", "sli" * 86),
    ),
)
def test_incident_text_fields_have_conservative_max_lengths(
    field_name: str,
    overflow: str,
) -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate(incident_payload(**{field_name: overflow}))


@pytest.mark.parametrize(
    ("field_name", "maximum"),
    (
        ("incident_id", 128),
        ("alert_source_service", 128),
        ("summary", 1000),
        ("affected_sli", 128),
    ),
)
def test_incident_approved_text_boundaries_are_exact(
    field_name: str,
    maximum: int,
) -> None:
    exact = Incident.model_validate(
        incident_payload(**{field_name: "x" * maximum})
    )
    assert len(getattr(exact, field_name)) == maximum

    with pytest.raises(ValidationError):
        Incident.model_validate(
            incident_payload(**{field_name: "x" * (maximum + 1)})
        )


def test_stable_error_code_contract_is_complete() -> None:
    assert {code.value for code in StableErrorCode} == {
        "INVALID_QUERY",
        "OUTSIDE_INCIDENT_WINDOW",
        "TIMEOUT",
        "BUDGET_EXHAUSTED",
        "BACKEND_UNAVAILABLE",
        "MALFORMED_REPLAY_ARTIFACT",
        "INTERNAL_CONTRACT_VIOLATION",
        "MODEL_PROTOCOL_VIOLATION",
        "MODEL_NOT_CONFIGURED",
    }


@pytest.mark.parametrize(
    "bad_ref",
    (
        f"evidence://{'A' * 32}/metrics/0001",
        f"evidence://{'a' * 31}/metrics/0001",
        f"evidence://{'a' * 32}/metric/0001",
        f"evidence://{'a' * 32}/logs/001",
        f"x-evidence://{'a' * 32}/logs/0001",
        f"evidence://{'a' * 32}/logs/0001/trailing",
    ),
)
def test_evidence_reference_grammar_is_exact(bad_ref: str) -> None:
    with pytest.raises(ValidationError):
        Evidence(
            schema_version="phase1.evidence.v1",
            evidence_ref=bad_ref,
            run_id=RUN_ID,
            source=EvidenceSource.LOGS,
            observation_type="log_observation",
            attributes=(),
            raw_artifact_ref="logs.json#0",
            raw_artifact_sha256="0" * 64,
            limitations=(),
            summary="Bad reference.",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )


def test_evidence_reference_source_must_match_typed_source() -> None:
    with pytest.raises(ValidationError, match="source"):
        evidence(METRICS_REF, EvidenceSource.LOGS)


def test_evidence_contract_binds_run_source_and_raw_artifact_capability() -> None:
    item = evidence()

    assert item.run_id == RUN_ID
    assert item.observation_type == "latency_observation"
    assert item.raw_artifact_ref == "metrics.json#0"
    assert item.raw_artifact_sha256 == "0" * 64
    assert item.attributes == (
        EvidenceAttribute(
            name="fault_mechanism",
            value="request_processing_failure",
        ),
    )

    with pytest.raises(ValidationError, match="run"):
        Evidence.model_validate(
            {
                **item.model_dump(),
                "run_id": OTHER_RUN_ID,
            }
        )


@pytest.mark.parametrize("service", (None, "", "   "))
def test_evidence_requires_a_named_nonempty_service(
    service: str | None,
) -> None:
    payload = evidence().model_dump()
    payload["service"] = service

    with pytest.raises(ValidationError, match="service"):
        Evidence.model_validate(payload)


def test_evidence_attributes_are_strict_unique_and_deeply_immutable() -> None:
    item = evidence()

    with pytest.raises(ValidationError) as frozen_error:
        item.attributes[0].value = "mutated"  # type: ignore[misc]
    assert frozen_error.value.errors()[0]["type"] == "frozen_instance"
    with pytest.raises(TypeError):
        item.attributes[0] = EvidenceAttribute(  # type: ignore[index]
            name="fault_mechanism",
            value="mutated",
        )
    with pytest.raises(ValidationError, match="duplicate"):
        Evidence.model_validate(
            {
                **item.model_dump(),
                "attributes": (
                    {"name": "fault_mechanism", "value": "dependency timeout"},
                    {"name": "fault_mechanism", "value": "another timeout"},
                ),
            }
        )
    with pytest.raises(ValidationError):
        EvidenceAttribute(name="sample", value=["mutable"])  # type: ignore[arg-type]


def test_evidence_rejects_noncanonical_attribute_order() -> None:
    item = evidence()

    with pytest.raises(ValidationError, match="sorted|canonical"):
        Evidence.model_validate(
            {
                **item.model_dump(),
                "attributes": (
                    {"name": "zeta", "value": 2},
                    {
                        "name": "fault_mechanism",
                        "value": "dependency timeout",
                    },
                ),
            }
        )


@pytest.mark.parametrize(
    "bad_raw_ref",
    (
        "../metrics.json#0",
        "/metrics.json#0",
        "metrics.json#../0",
        "https://example.test/metrics.json#0",
        "evaluator/metrics.json#0",
        "logs.json#0",
    ),
)
def test_evidence_raw_artifact_ref_is_source_bound_and_non_traversable(
    bad_raw_ref: str,
) -> None:
    with pytest.raises(ValidationError, match="raw_artifact"):
        Evidence.model_validate(
            {
                **evidence().model_dump(),
                "raw_artifact_ref": bad_raw_ref,
            }
        )


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("summary", "e" * 4001),
        ("service", "s" * 257),
        ("observation_type", "o" * 129),
        ("limitations", ("bounded",) * 33),
    ),
)
def test_evidence_text_fields_have_conservative_max_lengths(
    field_name: str,
    overflow: str,
) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            {
                **evidence().model_dump(),
                field_name: overflow,
            }
        )


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("hypothesis_id", "h" * 129),
        ("statement", "s" * 2001),
    ),
)
def test_hypothesis_text_fields_have_conservative_max_lengths(
    field_name: str,
    overflow: str,
) -> None:
    payload: dict[str, object] = {
        "schema_version": "phase1.hypothesis.v1",
        "hypothesis_id": "hyp-001",
        "statement": "A bounded hypothesis.",
        "supporting_evidence": (METRICS_REF,),
        "contradicting_evidence": (),
        "confidence": 0.4,
    }
    payload[field_name] = overflow

    with pytest.raises(ValidationError):
        Hypothesis.model_validate(payload)


def test_confirmed_rca_requires_complete_causal_claim() -> None:
    for field, empty_value in (
        ("root_service", None),
        ("fault_mechanism", ""),
        ("causal_chain", ()),
        ("affected_sli", None),
    ):
        with pytest.raises(ValidationError):
            RCAResult.model_validate(rca_payload(**{field: empty_value}))


def test_confirmed_rca_requires_two_distinct_sources() -> None:
    with pytest.raises(ValidationError, match="two"):
        RCAResult.model_validate(
            rca_payload(supporting_evidence=(METRICS_REF,))
        )
    with pytest.raises(ValidationError, match="sources"):
        RCAResult.model_validate(
            rca_payload(
                supporting_evidence=(
                    METRICS_REF,
                    f"evidence://{RUN_ID}/metrics/0002",
                )
            )
        )


def test_confirmed_rca_rejects_missing_evidence() -> None:
    with pytest.raises(ValidationError, match="missing_evidence"):
        RCAResult.model_validate(
            rca_payload(missing_evidence=("A trace is still missing.",))
        )


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("root_service", "s" * 257),
        ("fault_mechanism", "f" * 1001),
        ("affected_sli", "sli" * 86),
        ("causal_chain", ("c" * 1001,)),
        ("causal_chain", ("bounded step",) * 33),
        (
            "supporting_evidence",
            (LOGS_REF,)
            + tuple(
                f"evidence://{RUN_ID}/metrics/{index:04d}"
                for index in range(1, 65)
            ),
        ),
        (
            "contradicting_evidence",
            tuple(
                f"evidence://{RUN_ID}/traces/{index:04d}"
                for index in range(1, 66)
            ),
        ),
    ),
)
def test_rca_claims_and_reference_tuples_are_bounded(
    field_name: str,
    overflow: object,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(**{field_name: overflow}))


@pytest.mark.parametrize(
    "missing_evidence",
    (
        ("m" * 1001,),
        ("bounded missing item",) * 33,
    ),
)
def test_need_more_missing_evidence_entries_and_tuple_are_bounded(
    missing_evidence: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(
                decision=RCADecision.NEED_MORE_EVIDENCE,
                missing_evidence=missing_evidence,
                decision_rationale="More evidence is needed to confirm the cause.",
            )
        )


def test_need_more_evidence_requires_explicit_missing_evidence() -> None:
    valid = RCAResult.model_validate(
        rca_payload(
            decision=RCADecision.NEED_MORE_EVIDENCE,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            affected_sli=None,
            supporting_evidence=(METRICS_REF,),
            missing_evidence=("A trace linking both services is required.",),
            confidence=0.35,
            decision_rationale=(
                "More evidence is needed before the incident cause can be confirmed."
            ),
        )
    )
    assert valid.decision is RCADecision.NEED_MORE_EVIDENCE

    with pytest.raises(ValidationError, match="missing_evidence"):
        RCAResult.model_validate(
            rca_payload(
                decision=RCADecision.NEED_MORE_EVIDENCE,
                missing_evidence=(),
                decision_rationale="More evidence is needed to confirm the cause.",
            )
        )


def test_abstain_requires_no_root_claim_and_an_explanatory_rationale() -> None:
    valid = RCAResult.model_validate(
        rca_payload(
            decision=RCADecision.ABSTAIN,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            affected_sli=None,
            supporting_evidence=(),
            confidence=0.0,
            decision_rationale=(
                "No confirmed incident can be established from the available context."
            ),
        )
    )
    assert valid.root_service is None

    with pytest.raises(ValidationError, match="root_service"):
        RCAResult.model_validate(
            rca_payload(
                decision=RCADecision.ABSTAIN,
                fault_mechanism=None,
                causal_chain=(),
                affected_sli=None,
                supporting_evidence=(),
                decision_rationale="No confirmed incident is present.",
            )
        )
    with pytest.raises(ValidationError, match="no confirmed incident"):
        RCAResult.model_validate(
            rca_payload(
                decision=RCADecision.ABSTAIN,
                root_service=None,
                fault_mechanism=None,
                causal_chain=(),
                affected_sli=None,
                supporting_evidence=(),
                decision_rationale="The available context is ambiguous.",
            )
        )


@pytest.mark.parametrize(
    "rationale",
    (
        "",
        " " * 5,
        "x" * 1001,
        f"Use {METRICS_REF} as the deciding fact.",
        "Invoke MetricsAction(started_at=now) to prove the cause.",
        "rm -rf /var/tmp/ecomsre",
        "The incident is confirmed; curl http://localhost.",
    ),
)
def test_rationale_rejects_empty_overlong_reference_tool_and_shell_text(
    rationale: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(decision_rationale=rationale))


def test_rationale_is_trimmed() -> None:
    result = RCAResult.model_validate(
        rca_payload(
            decision_rationale=(
                "  The incident is confirmed by independent metrics and logs.  "
            )
        )
    )
    assert result.decision_rationale == (
        "The incident is confirmed by independent metrics and logs."
    )


@pytest.mark.parametrize(
    "tool_name",
    ("query_metrics", "search_logs", "search_traces", "list_changes"),
)
@pytest.mark.parametrize(
    "field_name",
    ("decision_rationale", "recommended_next_action"),
)
def test_rca_text_rejects_every_bare_typed_tool_name(
    tool_name: str,
    field_name: str,
) -> None:
    value = (
        f"The incident is confirmed from {tool_name} observations."
        if field_name == "decision_rationale"
        else f"Review {tool_name} output with the service owner."
    )

    expected_error = (
        "typed tool" if field_name == "decision_rationale" else None
    )
    with pytest.raises(ValidationError, match=expected_error):
        RCAResult.model_validate(rca_payload(**{field_name: value}))


@pytest.mark.parametrize(
    "field_name",
    ("decision_rationale", "recommended_next_action"),
)
def test_rca_text_rejects_embedded_kubectl_remediation_command(
    field_name: str,
) -> None:
    value = (
        "The incident is confirmed. Run kubectl rollout undo deployment checkout."
        if field_name == "decision_rationale"
        else "Run kubectl rollout undo deployment checkout."
    )

    expected_error = "shell" if field_name == "decision_rationale" else None
    with pytest.raises(ValidationError, match=expected_error):
        RCAResult.model_validate(rca_payload(**{field_name: value}))


@pytest.mark.parametrize(
    "command_text",
    (
        "The timeout is confirmed. Use rm -rf workspace.",
        "Please curl http://localhost.",
        "The timeout is confirmed. Run bash investigate.sh.",
        "The timeout is confirmed. Execute sh replay.sh.",
        "The timeout is confirmed. Use python diagnose.py.",
        "Please node inspect.js.",
        "The timeout is confirmed. Run git reset --hard.",
        "Please make deploy.",
    ),
)
@pytest.mark.parametrize(
    "field_name",
    ("decision_rationale", "recommended_next_action"),
)
def test_rca_text_rejects_embedded_shell_command_tokens(
    command_text: str,
    field_name: str,
) -> None:
    expected_error = "shell" if field_name == "decision_rationale" else None
    with pytest.raises(ValidationError, match=expected_error):
        RCAResult.model_validate(rca_payload(**{field_name: command_text}))


@pytest.mark.parametrize(
    "command_text",
    (
        "Use docker compose down",
        "Please wget https://example.invalid",
        "Please kubectl get pods",
    ),
)
@pytest.mark.parametrize(
    "field_name",
    ("decision_rationale", "recommended_next_action"),
)
def test_rca_text_rejects_review_named_command_false_negatives(
    command_text: str,
    field_name: str,
) -> None:
    expected_error = "shell" if field_name == "decision_rationale" else None
    with pytest.raises(ValidationError, match=expected_error):
        RCAResult.model_validate(rca_payload(**{field_name: command_text}))


@pytest.mark.parametrize(
    "command_text",
    (
        "Use helm uninstall checkout",
        "Please terraform plan",
        "Use sed -n 1,20p diagnostics.log",
    ),
)
@pytest.mark.parametrize(
    "field_name",
    ("decision_rationale", "recommended_next_action"),
)
def test_rca_text_rejects_final_review_named_shell_commands(
    command_text: str,
    field_name: str,
) -> None:
    expected_error = "shell" if field_name == "decision_rationale" else None
    with pytest.raises(ValidationError, match=expected_error):
        RCAResult.model_validate(rca_payload(**{field_name: command_text}))


@pytest.mark.parametrize(
    "safe_text",
    (
        "The timeout is confirmed. Review the start of the latency window.",
        "The timeout is confirmed by SLO(error_budget) observations.",
    ),
)
def test_rationale_allows_review_named_safe_descriptive_prose(
    safe_text: str,
) -> None:
    result = RCAResult.model_validate(
        rca_payload(decision_rationale=safe_text)
    )
    assert result.decision_rationale == safe_text


@pytest.mark.parametrize(
    "rationale",
    (
        "Restart telemetry preceded the latency regression.",
        "Deploy markers align with the incident window.",
    ),
)
def test_rationale_allows_descriptive_restart_and_deploy_observations(
    rationale: str,
) -> None:
    result = RCAResult.model_validate(
        rca_payload(decision_rationale=rationale)
    )
    assert result.decision_rationale == rationale


@pytest.mark.parametrize(
    "rationale",
    (
        "Restart checkoutservice immediately.",
        "Deploy checkout-v2 now.",
    ),
)
def test_rationale_still_rejects_explicit_remediation_instructions(
    rationale: str,
) -> None:
    with pytest.raises(ValidationError, match="read-only"):
        RCAResult.model_validate(
            rca_payload(decision_rationale=rationale)
        )


def test_confirmed_rationale_rejects_wording_that_does_not_establish_a_cause() -> None:
    with pytest.raises(ValidationError, match="RCA_CONFIRMED"):
        RCAResult.model_validate(
            rca_payload(
                decision_rationale="The observations do not establish a cause."
            )
        )


def test_abstain_accepts_wording_that_does_not_establish_an_incident() -> None:
    result = RCAResult.model_validate(
        rca_payload(
            decision=RCADecision.ABSTAIN,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            affected_sli=None,
            supporting_evidence=(),
            confidence=0.0,
            decision_rationale=(
                "The replay observations do not establish an incident."
            ),
        )
    )

    assert result.decision is RCADecision.ABSTAIN


def test_rca_text_allows_ordinary_observability_and_safe_advisory_prose() -> None:
    result = RCAResult.model_validate(
        rca_payload(
            decision_rationale=(
                "The incident is confirmed by metrics from the Python and Node services."
            ),
            recommended_next_action=RECOMMENDED_ACTION_VALUES[0],
        )
    )

    assert result.recommended_next_action == RECOMMENDED_ACTION_VALUES[0]


def test_recommended_next_action_contract_is_a_closed_string_enum() -> None:
    annotation = RCAResult.model_fields["recommended_next_action"].annotation

    assert isinstance(annotation, type)
    assert issubclass(annotation, str)
    assert issubclass(annotation, Enum)
    assert tuple(member.value for member in annotation) == (
        RECOMMENDED_ACTION_VALUES
    )


@pytest.mark.parametrize("action", RECOMMENDED_ACTION_VALUES)
def test_every_recommended_next_action_catalog_value_serializes_exactly(
    action: str,
) -> None:
    result = RCAResult.model_validate(
        rca_payload(recommended_next_action=action)
    )
    assert result.model_dump(mode="json")["recommended_next_action"] == action


@pytest.mark.parametrize(
    "action",
    (
        "Review the safe-looking evidence.",
        "Review the evidence, then reboot checkoutservice.",
        "Inspect the evidence, then evict checkout-0.",
        "Review the evidence, then upgrade checkoutservice.",
        "Review the evidence, then change replica count.",
        "Review the evidence, then shut down checkoutservice.",
    ),
)
def test_recommended_next_action_rejects_arbitrary_and_mutation_synonyms(
    action: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(recommended_next_action=action)
        )


@pytest.mark.parametrize(
    "action",
    (
        "Review the evidence and restart checkoutservice.",
        "Request a rollback of checkoutservice.",
        "Inspect the evidence, then deploy checkout-v3.",
    ),
)
def test_recommended_next_action_rejects_prefix_bypass_remediation(
    action: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(recommended_next_action=action)
        )


@pytest.mark.parametrize(
    "action",
    (
        "Review the evidence, then modify production configuration.",
        "Inspect the evidence, then write a deployment manifest.",
        "Monitor the SLI, then start the deployment.",
    ),
)
def test_recommended_next_action_rejects_review_named_omitted_verbs(
    action: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(recommended_next_action=action)
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "restart",
        "rollback",
        "roll back",
        "scale",
        "deploy",
        "delete",
        "remove",
        "kill",
        "stop",
        "start",
        "modify",
        "write",
        "patch",
        "remediate",
        "execute",
        "apply",
        "reconfigure",
        "terminate",
        "drain",
        "cordon",
        "uncordon",
        "increase",
        "decrease",
        "enable",
        "disable",
    ),
)
def test_recommended_next_action_rejects_full_named_mutation_set_anywhere(
    mutation: str,
) -> None:
    action = f"Review the evidence, then {mutation} checkoutservice."
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(recommended_next_action=action)
        )


@pytest.mark.parametrize(
    "action",
    (
        "Monitor the affected SLI, then scale checkoutservice.",
        "Document the evidence gap, then disable the feature flag.",
        "Await additional observations, then delete checkout-0.",
        "Ask the service owner to review and restart checkoutservice.",
    ),
)
def test_recommended_next_action_rejects_remediation_after_new_safe_prefixes(
    action: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(recommended_next_action=action)
        )


@pytest.mark.parametrize(
    "action",
    (
        "Increase the replica count",
        "Disable the checkout feature flag",
        "Consider restarting the service",
    ),
)
def test_recommended_next_action_rejects_non_read_only_prefixes(
    action: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(
            rca_payload(recommended_next_action=action)
        )


@pytest.mark.parametrize(
    "action",
    (
        "Restart checkoutservice immediately.",
        "rollback deployment checkout-v2",
        "Invoke LogsAction(started_at=now).",
        "kubectl delete pod checkout-0",
        "Review metrics && stop the service.",
    ),
)
def test_recommended_next_action_rejects_tool_shell_and_mutation_syntax(
    action: str,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(recommended_next_action=action))


@pytest.mark.parametrize("confidence", (math.nan, math.inf, -math.inf, -0.1, 1.1))
def test_confidence_must_be_finite_and_bounded(confidence: float) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(confidence=confidence))


@pytest.mark.parametrize(
    ("action_type", "expected_type"),
    (
        ("metrics", MetricsAction),
        ("logs", LogsAction),
        ("traces", TracesAction),
        ("changes", ChangesAction),
    ),
)
def test_all_four_read_only_action_types_use_the_discriminated_union(
    action_type: str,
    expected_type: type[object],
) -> None:
    action: object = TypeAdapter(Action).validate_python(
        {
            "action_type": action_type,
            "started_at": UTC_START,
            "ended_at": UTC_END,
            "service": None,
        }
    )
    assert isinstance(action, expected_type)


@pytest.mark.parametrize(
    ("action", "source", "evidence_ref"),
    (
        (
            MetricsAction(
                action_type="metrics",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.METRICS,
            METRICS_REF,
        ),
        (
            LogsAction(
                action_type="logs",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.LOGS,
            LOGS_REF,
        ),
        (
            TracesAction(
                action_type="traces",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.TRACES,
            TRACES_REF,
        ),
        (
            ChangesAction(
                action_type="changes",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.CHANGES,
            f"evidence://{RUN_ID}/changes/0004",
        ),
    ),
)
def test_tool_call_action_accepts_only_its_matching_evidence_source(
    action: MetricsAction | LogsAction | TracesAction | ChangesAction,
    source: EvidenceSource,
    evidence_ref: str,
) -> None:
    record = ToolCallRecord.model_validate(
        tool_record_payload(
            action,
            (evidence(evidence_ref, source),),
            call_id=f"tool-call-{source.value.lower()}",
        )
    )
    assert record.evidence[0].source is source


@pytest.mark.parametrize(
    ("action", "wrong_source", "wrong_ref"),
    (
        (
            MetricsAction(
                action_type="metrics",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.LOGS,
            LOGS_REF,
        ),
        (
            LogsAction(
                action_type="logs",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.TRACES,
            TRACES_REF,
        ),
        (
            TracesAction(
                action_type="traces",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.CHANGES,
            f"evidence://{RUN_ID}/changes/0004",
        ),
        (
            ChangesAction(
                action_type="changes",
                started_at=UTC_START,
                ended_at=UTC_END,
            ),
            EvidenceSource.METRICS,
            METRICS_REF,
        ),
    ),
)
def test_tool_call_action_rejects_mismatched_evidence_source(
    action: MetricsAction | LogsAction | TracesAction | ChangesAction,
    wrong_source: EvidenceSource,
    wrong_ref: str,
) -> None:
    with pytest.raises(ValidationError, match="source"):
        ToolCallRecord.model_validate(
            tool_record_payload(
                action,
                (evidence(wrong_ref, wrong_source),),
                call_id="tool-call-mismatch",
            )
        )


def test_failed_tool_call_cannot_contain_evidence() -> None:
    with pytest.raises(ValidationError, match="failed.*Evidence|Evidence.*failed"):
        action = MetricsAction(
                action_type="metrics",
                started_at=UTC_START,
                ended_at=UTC_END,
                service="checkoutservice",
            )
        ToolCallRecord.model_validate(
            tool_record_payload(
                action,
                (evidence(),),
                call_id="tool-call-failed",
                status="ERROR",
                error_code=StableErrorCode.TIMEOUT,
            )
        )


def test_metrics_tool_rejects_current_helper_style_mixed_source_evidence() -> None:
    with pytest.raises(ValidationError, match="source"):
        action = MetricsAction(
                action_type="metrics",
                started_at=UTC_START,
                ended_at=UTC_END,
            )
        ToolCallRecord.model_validate(
            tool_record_payload(
                action,
                (
                    evidence(),
                    evidence(LOGS_REF, EvidenceSource.LOGS),
                ),
                call_id="tool-call-mixed",
            )
        )


def test_final_action_is_part_of_the_action_union() -> None:
    action: object = TypeAdapter(Action).validate_python(
        {
            "action_type": "final",
            "result": rca_payload(),
        }
    )
    assert isinstance(action, FinalAction)


def test_action_window_is_utc_and_closed() -> None:
    point_action = MetricsAction(
        action_type="metrics",
        started_at=UTC_START,
        ended_at=UTC_START,
    )
    assert point_action.started_at == point_action.ended_at

    with pytest.raises(ValidationError):
        MetricsAction(
            action_type="metrics",
            started_at=UTC_END,
            ended_at=UTC_START,
        )


def test_model_call_record_accepts_matching_response_error_authority() -> None:
    response = model_response(error_code=StableErrorCode.TIMEOUT)
    record = ModelCallRecord.model_validate(
        model_call_payload(
            response,
            status="ERROR",
            error_code=StableErrorCode.TIMEOUT,
        )
    )
    assert record.error_code is response.error_code


@pytest.mark.parametrize(
    "record_error",
    (None, StableErrorCode.BACKEND_UNAVAILABLE),
)
def test_model_call_record_rejects_response_error_mismatch(
    record_error: StableErrorCode | None,
) -> None:
    with pytest.raises(ValidationError, match="error"):
        ModelCallRecord.model_validate(
            model_call_payload(
                model_response(error_code=StableErrorCode.TIMEOUT),
                status="ERROR" if record_error is not None else "OK",
                error_code=record_error,
            )
        )


def test_model_call_record_accepts_success_and_requires_absent_response_error() -> None:
    success = ModelCallRecord.model_validate(
        model_call_payload(
            model_response(),
            status="OK",
            error_code=None,
        )
    )
    assert success.error_code is None

    with pytest.raises(ValidationError, match="error"):
        ModelCallRecord.model_validate(
            model_call_payload(
                None,
                status="ERROR",
                error_code=None,
            )
        )


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("request_id", "r" * 129),
        ("model_name", "m" * 257),
        ("agent_id", "a" * 129),
        ("task_id", "t" * 129),
    ),
)
def test_model_request_identity_is_bounded(
    field_name: str,
    overflow: object,
) -> None:
    payload = model_request().model_dump()
    payload[field_name] = overflow
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("request_id", "r" * 129),
        ("response_id", "r" * 129),
        ("provider_name", "p" * 257),
        ("model_name", "m" * 257),
    ),
)
def test_model_response_identity_is_bounded(
    field_name: str,
    overflow: str,
) -> None:
    payload = model_response().model_dump()
    payload[field_name] = overflow
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(payload)


@pytest.mark.parametrize("bad_value", (True, "1"))
def test_accounting_integer_fields_reject_bool_and_string(
    bad_value: object,
) -> None:
    with pytest.raises(ValidationError):
        BudgetLimits.model_validate(
            {
                "max_model_calls": bad_value,
                "max_tool_calls": 2,
                "max_total_tokens": 100,
            }
        )
    with pytest.raises(ValidationError):
        BudgetSnapshot.model_validate(
            {
                "model_calls": bad_value,
                "tool_calls": 0,
                "total_tokens": 0,
                "limits": {
                    "max_model_calls": 2,
                    "max_tool_calls": 2,
                    "max_total_tokens": 100,
                },
            }
        )
    with pytest.raises(ValidationError):
        ModelUsage.model_validate(
            {
                "input_tokens": bad_value,
                "output_tokens": 0,
                "total_tokens": 1,
            }
        )


@pytest.mark.parametrize("bad_value", (True, "0.5"))
def test_confidence_fields_reject_bool_and_string(
    bad_value: object,
) -> None:
    with pytest.raises(ValidationError):
        RCAResult.model_validate(rca_payload(confidence=bad_value))
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(
            {
                "schema_version": "phase1.hypothesis.v1",
                "hypothesis_id": "hyp-001",
                "statement": "A bounded hypothesis.",
                "supporting_evidence": (),
                "contradicting_evidence": (),
                "confidence": bad_value,
            }
        )


@pytest.mark.parametrize("bad_value", (True, "30"))
def test_temperature_timeout_and_duration_reject_bool_and_string(
    bad_value: object,
) -> None:
    with pytest.raises(ValidationError):
        ModelConfiguration(
            model_name="replay-model",
            temperature=bad_value,  # type: ignore[arg-type]
            model_timeout_seconds=30.0,
        )
    with pytest.raises(ValidationError):
        ModelConfiguration(
            model_name="replay-model",
            temperature=0,
            model_timeout_seconds=bad_value,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            {
                **model_request().model_dump(),
                "timeout_seconds": bad_value,
            }
        )
    with pytest.raises(ValidationError):
        AgentRunReport.model_validate(
            valid_report_payload(monotonic_duration_seconds=bad_value)
        )


def test_json_integer_zero_is_an_approved_temperature_value() -> None:
    configuration = ModelConfiguration(
        model_name="replay-model",
        temperature=0,
        model_timeout_seconds=30,
    )
    request = ModelRequest.model_validate(
        {
            **model_request().model_dump(),
            "temperature": 0,
            "timeout_seconds": 30,
        }
    )

    assert configuration.temperature == 0.0
    assert request.temperature == 0.0


@pytest.mark.parametrize("bad_value", (1, "true"))
def test_boolean_flags_reject_integer_and_string(
    bad_value: object,
) -> None:
    with pytest.raises(ValidationError):
        ToolCallRecord.model_validate(
            {
                **tool_record_with((evidence(),)).model_dump(),
                "status": bad_value,
            }
        )
    for field_name in ("schema_valid", "evidence_references_valid"):
        with pytest.raises(ValidationError):
            AgentRunReport.model_validate(
                valid_report_payload(**{field_name: bad_value})
            )


def test_supporting_models_form_a_strict_immutable_report() -> None:
    limits = BudgetLimits(
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=12_000,
    )
    request = InvestigationRequest(
        schema_version="phase1.investigation-request.v1",
        request_id="request-001",
        run_id=RUN_ID,
        agent_id="single-agent",
        task_id="root-cause-analysis",
        incident=Incident.model_validate(incident_payload()),
        budgets=limits,
    )
    hypothesis = Hypothesis(
        schema_version="phase1.hypothesis.v1",
        hypothesis_id="hyp-001",
        statement="checkoutservice is delayed by paymentservice",
        supporting_evidence=(METRICS_REF,),
        contradicting_evidence=(),
        confidence=0.4,
    )
    assert hypothesis.supporting_evidence == (METRICS_REF,)

    typed_model_response = model_response()
    model_record = ModelCallRecord.model_validate(
        model_call_payload(
            typed_model_response,
            status="OK",
            error_code=None,
        )
    )
    metrics_action = MetricsAction(
            action_type="metrics",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )
    tool_record = ToolCallRecord.model_validate(
        tool_record_payload(
            metrics_action,
            (evidence(),),
            call_id="tool-call-001",
        )
    )
    logs_action = LogsAction(
            action_type="logs",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        )
    logs_tool_record = ToolCallRecord.model_validate(
        tool_record_payload(
            logs_action,
            (evidence(LOGS_REF, EvidenceSource.LOGS),),
            call_id="tool-call-logs",
        )
    )
    snapshot = BudgetSnapshot(
        model_calls=3,
        tool_calls=2,
        total_tokens=360,
        limits=limits,
    )
    final_rca = RCAResult.model_validate(rca_payload())
    assert model_record.status == "OK"
    report = AgentRunReport(
        schema_version="phase1.agent-run-report.v1",
        run_id=RUN_ID,
        request=request,
        model_configuration=ModelConfiguration(
            model_name="replay-model",
            temperature=0,
            model_timeout_seconds=30.0,
        ),
        final_rca=final_rca,
        model_call_records=completed_model_records(
            final_rca,
            (
                tool_record.model_copy(
                    update={"call_id": "tool-call-0001"}
                ),
                logs_tool_record.model_copy(
                    update={"call_id": "tool-call-0002"}
                ),
            ),
        ),
        tool_call_records=(
            tool_record.model_copy(
                update={"call_id": "tool-call-0001"}
            ),
            logs_tool_record.model_copy(
                update={"call_id": "tool-call-0002"}
            ),
        ),
        evidence_index=(
            evidence(),
            evidence(LOGS_REF, EvidenceSource.LOGS),
        ),
        budget_limits=limits,
        budget_snapshot=snapshot,
        started_at=UTC_START,
        ended_at=UTC_END,
        monotonic_duration_seconds=2.5,
        terminal_status="COMPLETED",
        terminal_reason="FINAL_RCA_ACCEPTED",
        terminal_error_code=None,
        schema_valid=True,
        evidence_references_valid=True,
    )

    assert report.request.request_id == "request-001"
    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    with pytest.raises(ValidationError) as frozen_error:
        report.final_rca.confidence = 0.1  # type: ignore[misc]
    assert frozen_error.value.errors()[0]["type"] == "frozen_instance"
    with pytest.raises(TypeError):
        report.tool_call_records[0] = tool_record  # type: ignore[index]
    with pytest.raises(ValidationError):
        AgentRunReport.model_validate(
            {**report.model_dump(), "secret": "must-not-be-recorded"}
        )


def test_report_rejects_unindexed_final_evidence_when_marked_valid() -> None:
    limits = BudgetLimits(
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=12_000,
    )
    with pytest.raises(ValidationError, match="evidence"):
        AgentRunReport.model_validate(
            {
                **valid_report_payload(tool_call_records=()),
                "tool_call_records": (),
                "evidence_index": (evidence(),),
                "budget_snapshot": BudgetSnapshot(
                    model_calls=1,
                    tool_calls=0,
                    total_tokens=120,
                    limits=limits,
                ),
            }
        )


def test_report_requires_request_and_report_budget_authority_to_match() -> None:
    payload = valid_report_payload()
    request = payload["request"]
    assert isinstance(request, InvestigationRequest)
    payload["request"] = request.model_copy(
        update={
            "budgets": BudgetLimits(
                max_model_calls=7,
                max_tool_calls=8,
                max_total_tokens=12_000,
            )
        }
    )

    with pytest.raises(ValidationError, match="budget"):
        AgentRunReport.model_validate(payload)


def test_report_rejects_foreign_run_evidence_in_index() -> None:
    foreign = evidence(
        f"evidence://{OTHER_RUN_ID}/metrics/0001",
        EvidenceSource.METRICS,
    )
    payload = valid_report_payload(evidence_references_valid=False)
    payload["evidence_index"] = (
        *payload["evidence_index"],  # type: ignore[misc]
        foreign,
    )

    with pytest.raises(ValidationError, match="run_id|run"):
        AgentRunReport.model_validate(payload)


def test_report_rejects_foreign_run_evidence_produced_by_tool() -> None:
    metrics = evidence()
    logs = evidence(LOGS_REF, EvidenceSource.LOGS)
    foreign = evidence(
        f"evidence://{OTHER_RUN_ID}/traces/0003",
        EvidenceSource.TRACES,
    )

    payload = valid_report_payload(
        tool_call_records=(
            tool_record_with((metrics,)),
            tool_record_with((logs,)),
            tool_record_with((foreign,)),
        ),
        evidence_index=(metrics, logs, foreign),
    )
    limits = payload["budget_limits"]
    assert isinstance(limits, BudgetLimits)
    payload["budget_snapshot"] = BudgetSnapshot(
        model_calls=4,
        tool_calls=3,
        total_tokens=480,
        limits=limits,
    )

    with pytest.raises(ValidationError, match="run_id|run"):
        AgentRunReport.model_validate(payload)


def test_report_rejects_foreign_run_reference_in_final_rca() -> None:
    final_rca = RCAResult.model_validate(
        rca_payload(
            supporting_evidence=(
                f"evidence://{OTHER_RUN_ID}/metrics/0001",
                LOGS_REF,
            )
        )
    )

    with pytest.raises(ValidationError, match="run_id|run"):
        AgentRunReport.model_validate(
            valid_report_payload(
                final_rca=final_rca,
                evidence_references_valid=False,
            )
        )


def test_report_requires_exact_tool_and_index_evidence_object_equality() -> None:
    metrics = evidence()
    logs = evidence(LOGS_REF, EvidenceSource.LOGS)
    changed_metrics = metrics.model_copy(
        update={"summary": "Different content for the same reference."}
    )

    with pytest.raises(ValidationError, match="evidence"):
        AgentRunReport.model_validate(
            valid_report_payload(
                tool_call_records=(
                    tool_record_with((metrics,)),
                    tool_record_with((logs,)),
                ),
                evidence_index=(changed_metrics, logs),
            )
        )


def test_report_rejects_orphan_index_evidence_not_produced_by_tool() -> None:
    metrics = evidence()
    logs = evidence(LOGS_REF, EvidenceSource.LOGS)
    traces = evidence(TRACES_REF, EvidenceSource.TRACES)

    with pytest.raises(ValidationError, match="orphan|evidence"):
        AgentRunReport.model_validate(
            valid_report_payload(
                tool_call_records=(
                    tool_record_with((metrics,)),
                    tool_record_with((logs,)),
                ),
                evidence_index=(metrics, logs, traces),
            )
        )


def test_report_rejects_duplicate_index_and_tool_evidence_refs() -> None:
    metrics = evidence()
    logs = evidence(LOGS_REF, EvidenceSource.LOGS)

    with pytest.raises(ValidationError, match="duplicate"):
        AgentRunReport.model_validate(
            valid_report_payload(evidence_index=(metrics, metrics, logs))
        )
    with pytest.raises(ValidationError, match="duplicate"):
        AgentRunReport.model_validate(
            valid_report_payload(
                tool_call_records=(
                    tool_record_with((metrics, metrics)),
                    tool_record_with((logs,)),
                )
            )
        )


def test_report_rejects_duplicate_ref_across_final_evidence_roles() -> None:
    final_rca = RCAResult.model_validate(
        rca_payload(contradicting_evidence=(METRICS_REF,))
    )

    with pytest.raises(ValidationError, match="duplicate"):
        AgentRunReport.model_validate(
            valid_report_payload(final_rca=final_rca)
        )


@pytest.mark.parametrize(
    ("field_name", "overflow"),
    (
        ("terminal_status", "S" * 65),
        ("terminal_reason", "R" * 513),
    ),
)
def test_report_terminal_strings_are_bounded(
    field_name: str,
    overflow: str,
) -> None:
    with pytest.raises(ValidationError):
        AgentRunReport.model_validate(
            valid_report_payload(**{field_name: overflow})
        )


def test_phase1_agent_config_is_exact() -> None:
    path = Path("config/phase1/agent.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "phase1.agent-config.v1",
        "temperature": 0,
        "max_model_calls": 8,
        "max_tool_calls": 8,
        "max_total_tokens": 12000,
        "model_timeout_seconds": 30.0,
        "tool_timeout_seconds": 5.0,
    }


def test_fault_mechanism_is_a_closed_three_value_enum() -> None:
    mechanism_type = getattr(contracts_module, "FaultMechanism")
    assert issubclass(mechanism_type, Enum)
    assert {item.value for item in mechanism_type} == {
        "runtime_configuration_failure",
        "request_processing_failure",
        "cache_backend_timeout",
    }


@pytest.mark.parametrize(
    "fault_mechanism",
    (
        "dependency timeout",
        "request failure",
        "cache timeout",
        "unknown",
    ),
)
def test_confirmed_rca_rejects_noncanonical_fault_mechanisms(
    fault_mechanism: str,
) -> None:
    with pytest.raises(ValidationError, match="fault_mechanism"):
        RCAResult.model_validate(
            rca_payload(fault_mechanism=fault_mechanism)
        )


EVALUATOR_MARKERS = (
    "expected_decision",
    "expected-root-service",
    "expected_root_service",
    "expected_fault_mechanism",
    "ground truth",
    "ground-truth",
    "ground_truth",
    "ground_truth_path",
    "scenario truth",
    "scenario-truth",
    "evaluator root service",
    "evaluator_root_service",
    "answer key",
    "answer-key",
    "answer_key",
    "evaluator-only",
    "eval/phase1/ground-truth",
    "expected mechanism",
    "expected-mechanism",
    "expected_mechanism",
    "expected/mechanism",
    "scenario label",
    "scenario-label",
    "scenario_label",
    "scenario/label",
    "evaluator path",
    "evaluator-path",
    "evaluator_path",
    "evaluator/path",
)


@pytest.mark.parametrize("marker", EVALUATOR_MARKERS)
def test_incident_rejects_evaluator_markers(marker: str) -> None:
    with pytest.raises(ValidationError, match="evaluator"):
        Incident.model_validate(
            incident_payload(summary=f"Observed field {marker} must be ignored.")
        )


@pytest.mark.parametrize("marker", EVALUATOR_MARKERS)
@pytest.mark.parametrize("location", ("name", "value", "summary"))
def test_evidence_rejects_evaluator_markers(
    marker: str,
    location: str,
) -> None:
    item = evidence()
    if location == "summary":
        payload = item.model_dump()
        payload["summary"] = f"Observed {marker} marker."
    else:
        payload = item.model_dump()
        payload["attributes"] = (
            {
                "name": marker if location == "name" else "observation",
                "value": marker if location == "value" else "bounded",
            },
        )

    with pytest.raises(ValidationError, match="evaluator"):
        Evidence.model_validate(payload)


@pytest.mark.parametrize("marker", EVALUATOR_MARKERS)
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("decision_rationale", "The marker is {marker}."),
        ("recommended_next_action", "Review {marker}."),
        ("causal_chain", ("Observed {marker}.",)),
        ("missing_evidence", ("Missing {marker}.",)),
    ),
)
def test_rca_all_decisions_reject_evaluator_markers(
    marker: str,
    field_name: str,
    field_value: str | tuple[str, ...],
) -> None:
    formatted = (
        tuple(item.format(marker=marker) for item in field_value)
        if isinstance(field_value, tuple)
        else field_value.format(marker=marker)
    )
    payload = rca_payload(**{field_name: formatted})

    with pytest.raises(ValidationError, match="evaluator"):
        RCAResult.model_validate(payload)


def test_marker_filter_avoids_broad_false_positives() -> None:
    item = Incident.model_validate(
        incident_payload(
            summary=(
                "The evaluation is ongoing; the service expectedly recovered."
            )
        )
    )
    assert "expectedly" in item.summary
    assert "evaluation" in item.summary


@pytest.mark.parametrize(
    "safe_summary",
    (
        "The expected decision latency for the ad auction exceeded its SLO.",
        "The evaluation is ongoing.",
    ),
)
def test_marker_filter_allows_ordinary_sre_prose(
    safe_summary: str,
) -> None:
    item = Incident.model_validate(incident_payload(summary=safe_summary))
    assert item.summary == safe_summary


@pytest.mark.parametrize(
    "safe_summary",
    (
        "The expected decision latency exceeded its SLO.",
        "The expected-decision latency exceeded its SLO.",
        "The expected_decision latency exceeded its SLO.",
    ),
)
def test_expected_decision_separator_forms_allow_measurement_prose(
    safe_summary: str,
) -> None:
    item = Incident.model_validate(incident_payload(summary=safe_summary))
    assert item.summary == safe_summary


@pytest.mark.parametrize(
    "unsafe_summary",
    (
        "The expected decision was RCA_CONFIRMED.",
        "The expected decision should be NEED_MORE_EVIDENCE.",
        "The expected decision value is ABSTAIN.",
        "The expected decision for this case is RCA_CONFIRMED.",
    ),
)
def test_expected_decision_disclosure_context_is_rejected(
    unsafe_summary: str,
) -> None:
    with pytest.raises(ValidationError, match="evaluator"):
        Incident.model_validate(incident_payload(summary=unsafe_summary))


@pytest.mark.parametrize(
    "unsafe_summary",
    (
        "expected decision",
        "expected decision: RCA_CONFIRMED",
        "expected decision = RCA_CONFIRMED",
        "expected decision is RCA_CONFIRMED",
        "ground trut\u200bh",
        "expected root serv\u200bice",
        "answer k\u200bey",
        "ｇｒｏｕｎｄ ｔｒｕｔｈ",
    ),
)
def test_marker_filter_normalizes_unicode_and_contextual_values(
    unsafe_summary: str,
) -> None:
    with pytest.raises(ValidationError, match="evaluator"):
        Incident.model_validate(incident_payload(summary=unsafe_summary))


@pytest.mark.parametrize(
    "unsafe_summary",
    (
        "ground trut\u034fh",
        "answer k\u034fey",
        "expected mechani\ufe0fsm",
        "ground tru\u0301th",
    ),
)
def test_marker_filter_removes_default_ignorable_combining_marks_for_scan(
    unsafe_summary: str,
) -> None:
    with pytest.raises(ValidationError, match="evaluator"):
        Incident.model_validate(incident_payload(summary=unsafe_summary))


def test_marker_filter_does_not_mutate_safe_stored_text() -> None:
    summary = "Latency note\u034f remains byte-for-byte intact."
    item = Incident.model_validate(incident_payload(summary=summary))
    assert item.summary == summary


@pytest.mark.parametrize(
    "unsafe_summary",
    (
        "expected-decision latency was 120ms; RCA_CONFIRMED",
        "expected_decision rate: NEED_MORE_EVIDENCE",
        "expected decision duration says ABSTAIN",
    ),
)
def test_expected_decision_measurement_tail_cannot_disclose_enum(
    unsafe_summary: str,
) -> None:
    with pytest.raises(ValidationError, match="evaluator"):
        Incident.model_validate(incident_payload(summary=unsafe_summary))


def test_expected_decision_measurement_without_enum_remains_allowed() -> None:
    summary = "expected-decision latency is 120ms"
    item = Incident.model_validate(incident_payload(summary=summary))
    assert item.summary == summary


@pytest.mark.parametrize(
    "unsafe_key",
    (
        "expected_decision",
        "expected_decision_value",
        "expected-decision-label",
        "expected-decision-latency",
    ),
)
def test_marker_filter_rejects_expected_decision_mapping_keys(
    unsafe_key: str,
) -> None:
    payload = incident_payload()
    payload[unsafe_key] = "RCA_CONFIRMED"
    with pytest.raises(ValidationError, match="evaluator"):
        Incident.model_validate(payload)


@pytest.mark.parametrize(
    "binary",
    (
        b"ordinary incident summary",
        b"expected mechanism",
        bytearray(b"ordinary incident summary"),
        bytearray(b"scenario_label"),
        memoryview(b"ordinary incident summary"),
        memoryview(b"evaluator/path"),
    ),
)
def test_phase1_prevalidation_rejects_binary_agent_visible_values(
    binary: object,
) -> None:
    with pytest.raises(ValidationError, match="binary"):
        Incident.model_validate(incident_payload(summary=binary))


@pytest.mark.parametrize(
    "decision",
    tuple(RCADecision),
)
def test_every_rca_decision_rejects_semantic_evaluator_marker(
    decision: RCADecision,
) -> None:
    payload = rca_payload(decision=decision)
    if decision is RCADecision.NEED_MORE_EVIDENCE:
        payload.update(
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            supporting_evidence=(),
            missing_evidence=("Additional evidence is missing.",),
            decision_rationale="Additional evidence is missing.",
            recommended_next_action=(
                "Collect additional read-only telemetry evidence."
            ),
        )
    elif decision is RCADecision.ABSTAIN:
        payload.update(
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            supporting_evidence=(),
            missing_evidence=(),
            decision_rationale=(
                "The observations do not establish an incident."
            ),
            recommended_next_action="Continue monitoring the affected SLI.",
        )
    payload["decision_rationale"] = "The answer key is untrusted."

    with pytest.raises(ValidationError, match="evaluator"):
        RCAResult.model_validate(payload)


def test_model_request_rejects_nested_semantic_evaluator_marker() -> None:
    payload = model_request().model_dump()
    incident = payload["incident"]
    assert isinstance(incident, dict)
    incident["summary"] = "Scenario-truth must not reach the Agent."

    with pytest.raises(ValidationError, match="evaluator"):
        ModelRequest.model_validate(payload)


def test_call_records_expose_independent_accounting_facts() -> None:
    assert {
        "model_call_consumed",
        "charged_tokens",
    }.issubset(ModelCallRecord.model_fields)
    assert {
        "budget_consumed",
        "dispatched",
        "evidence_quarantined",
        "usable",
    }.issubset(ToolCallRecord.model_fields)


def test_terminal_fields_are_closed_enums_with_explicit_error_code() -> None:
    status_type = getattr(contracts_module, "AgentTerminalStatus")
    reason_type = getattr(contracts_module, "AgentTerminalReason")
    assert {item.value for item in status_type} == {
        "COMPLETED",
        "TERMINATED",
    }
    assert "FINAL_RCA_ACCEPTED" in {item.value for item in reason_type}
    assert "terminal_error_code" in AgentRunReport.model_fields

    with pytest.raises(ValidationError, match="terminal_status"):
        AgentRunReport.model_validate(
            valid_report_payload(terminal_status="PLAUSIBLE_SUCCESS")
        )
    with pytest.raises(ValidationError, match="terminal_reason"):
        AgentRunReport.model_validate(
            valid_report_payload(terminal_reason="plausible fallback")
        )
    with pytest.raises(ValidationError, match="terminal"):
        AgentRunReport.model_validate(
            valid_report_payload(
                final_rca=None,
                terminal_status="TERMINATED",
                terminal_reason="MODEL_CALL_TIMED_OUT",
                terminal_error_code=StableErrorCode.BUDGET_EXHAUSTED,
            )
        )


@pytest.mark.parametrize(
    "terminal_reason",
    (
        "MODEL_CALL_BUDGET_EXHAUSTED",
        "TOOL_CALL_BUDGET_EXHAUSTED",
        "TOKEN_BUDGET_EXHAUSTED",
    ),
)
def test_budget_terminal_rejects_unused_limit_spoof(
    terminal_reason: str,
) -> None:
    payload = valid_report_payload(
        final_rca=None,
        terminal_status="TERMINATED",
        terminal_reason=terminal_reason,
        terminal_error_code=StableErrorCode.BUDGET_EXHAUSTED,
    )

    with pytest.raises(ValidationError, match="budget|limit|exhaust"):
        AgentRunReport.model_validate(payload)


@pytest.mark.parametrize(
    ("terminal_reason", "limits"),
    (
        (
            "MODEL_CALL_BUDGET_EXHAUSTED",
            BudgetLimits(
                max_model_calls=3,
                max_tool_calls=8,
                max_total_tokens=12_000,
            ),
        ),
        (
            "TOOL_CALL_BUDGET_EXHAUSTED",
            BudgetLimits(
                max_model_calls=8,
                max_tool_calls=2,
                max_total_tokens=12_000,
            ),
        ),
        (
            "TOKEN_BUDGET_EXHAUSTED",
            BudgetLimits(
                max_model_calls=8,
                max_tool_calls=8,
                max_total_tokens=360,
            ),
        ),
    ),
)
def test_budget_terminal_rejects_accepted_final_action_relabel(
    terminal_reason: str,
    limits: BudgetLimits,
) -> None:
    payload = rebind_report_limits(valid_report_payload(), limits)
    payload.update(
        final_rca=None,
        terminal_status="TERMINATED",
        terminal_reason=terminal_reason,
        terminal_error_code=StableErrorCode.BUDGET_EXHAUSTED,
    )

    with pytest.raises(ValidationError, match="final|action|outcome|budget"):
        AgentRunReport.model_validate(payload)


def test_token_budget_accepts_preserved_rejected_final_response() -> None:
    limits = BudgetLimits(
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=100,
    )
    payload = rebind_report_limits(
        valid_report_payload(tool_call_records=()),
        limits,
    )
    records = payload["model_call_records"]
    snapshot = payload["budget_snapshot"]
    assert isinstance(records, tuple)
    assert isinstance(snapshot, BudgetSnapshot)
    record = records[0]
    assert isinstance(record, ModelCallRecord)
    assert record.response is not None
    assert isinstance(record.response.action, FinalAction)
    payload.update(
        final_rca=None,
        evidence_index=(),
        model_call_records=(
            record.model_copy(
                update={
                    "charged_tokens": 0,
                    "status": "ERROR",
                    "error_code": StableErrorCode.BUDGET_EXHAUSTED,
                }
            ),
        ),
        budget_snapshot=snapshot.model_copy(update={"total_tokens": 0}),
        terminal_status="TERMINATED",
        terminal_reason="TOKEN_BUDGET_EXHAUSTED",
        terminal_error_code=StableErrorCode.BUDGET_EXHAUSTED,
    )

    report = AgentRunReport.model_validate(payload)
    assert report.final_rca is None
    assert report.model_call_records[-1].response is not None
    assert isinstance(
        report.model_call_records[-1].response.action,
        FinalAction,
    )


def test_model_response_timing_must_be_nested_in_model_call() -> None:
    response = model_response().model_copy(
        update={"started_at": UTC_START - timedelta(seconds=1)}
    )
    payload = model_call_payload(
        response,
        status="OK",
        error_code=None,
    )

    with pytest.raises(ValidationError, match="tim"):
        ModelCallRecord.model_validate(payload)


def test_model_call_response_model_name_must_match_request() -> None:
    response = model_response().model_copy(
        update={"model_name": "provider-alias"}
    )
    payload = model_call_payload(
        response,
        status="OK",
        error_code=None,
    )

    with pytest.raises(ValidationError, match="model_name|model"):
        ModelCallRecord.model_validate(payload)


def test_call_timing_must_be_nested_in_report() -> None:
    payload = valid_report_payload()
    first_record = payload["tool_call_records"][0]
    assert isinstance(first_record, ToolCallRecord)
    escaped = first_record.model_copy(
        update={"started_at": UTC_START - timedelta(seconds=1)}
    )
    payload["tool_call_records"] = (
        escaped,
        payload["tool_call_records"][1],
    )

    with pytest.raises(ValidationError, match="tim"):
        AgentRunReport.model_validate(payload)


def test_model_request_transcript_sequence_is_exact_and_unique() -> None:
    record = tool_record_with((evidence(),))
    base = TranscriptEntry(
        sequence=1,
        action=record.action,
        tool_name=record.tool_name,
        status=record.status,
        error_code=record.error_code,
        evidence_refs=record.evidence_refs,
    )
    for transcript in (
        (base.model_copy(update={"sequence": 2}),),
        (base, base),
        (base, base.model_copy(update={"sequence": 3})),
    ):
        payload = model_request().model_dump()
        payload["transcript"] = transcript
        with pytest.raises(ValidationError, match="sequence"):
            ModelRequest.model_validate(payload)


def _second_model_record(first: ModelCallRecord) -> ModelCallRecord:
    request = first.request.model_copy(
        update={"request_id": "model-request-0002"}
    )
    assert first.response is not None
    response = first.response.model_copy(
        update={
            "request_id": request.request_id,
            "response_id": "model-response-0002",
        }
    )
    return first.model_copy(
        update={
            "call_id": "model-call-0002",
            "request": request,
            "response": response,
        }
    )


@pytest.mark.parametrize(
    "duplicate_field",
    ("call_id", "request_id", "response_id", "alias"),
)
def test_report_rejects_duplicate_or_aliased_model_calls(
    duplicate_field: str,
) -> None:
    payload = valid_report_payload()
    first = payload["model_call_records"][0]
    assert isinstance(first, ModelCallRecord)
    second = _second_model_record(first)
    if duplicate_field == "call_id":
        second = second.model_copy(update={"call_id": first.call_id})
    elif duplicate_field == "request_id":
        assert second.response is not None
        second_request = second.request.model_copy(
            update={"request_id": first.request.request_id}
        )
        second_response = second.response.model_copy(
            update={"request_id": first.request.request_id}
        )
        second = second.model_copy(
            update={
                "request": second_request,
                "response": second_response,
            }
        )
    elif duplicate_field == "response_id":
        assert first.response is not None
        assert second.response is not None
        second = second.model_copy(
            update={
                "response": second.response.model_copy(
                    update={"response_id": first.response.response_id}
                )
            }
        )
    else:
        second = first
    payload["model_call_records"] = (first, second)
    snapshot = payload["budget_snapshot"]
    assert isinstance(snapshot, BudgetSnapshot)
    payload["budget_snapshot"] = snapshot.model_copy(
        update={"model_calls": 2, "total_tokens": 240}
    )

    with pytest.raises(ValidationError, match="call_id|request_id|response_id"):
        AgentRunReport.model_validate(payload)


def test_report_rejects_duplicate_tool_call_id() -> None:
    payload = valid_report_payload()
    first, second = payload["tool_call_records"]
    assert isinstance(first, ToolCallRecord)
    assert isinstance(second, ToolCallRecord)
    payload["tool_call_records"] = (
        first,
        second.model_copy(update={"call_id": first.call_id}),
    )

    with pytest.raises(ValidationError, match="tool call_id"):
        AgentRunReport.model_validate(payload)


@pytest.mark.parametrize(
    ("record_field", "invalid_value"),
    (
        ("call_id", "model-call-0002"),
        ("request_id", "model-request-0002"),
    ),
)
def test_report_requires_exact_sequential_model_local_ids(
    record_field: str,
    invalid_value: str,
) -> None:
    payload = valid_report_payload()
    record = payload["model_call_records"][0]
    assert isinstance(record, ModelCallRecord)
    if record_field == "call_id":
        changed = record.model_copy(update={"call_id": invalid_value})
    else:
        assert record.response is not None
        request = record.request.model_copy(
            update={"request_id": invalid_value}
        )
        response = record.response.model_copy(
            update={"request_id": invalid_value}
        )
        changed = record.model_copy(
            update={"request": request, "response": response}
        )
    payload["model_call_records"] = (changed,)

    with pytest.raises(ValidationError, match=record_field):
        AgentRunReport.model_validate(payload)


def test_report_requires_exact_sequential_tool_local_ids() -> None:
    payload = valid_report_payload()
    first, second = payload["tool_call_records"]
    assert isinstance(first, ToolCallRecord)
    payload["tool_call_records"] = (
        first.model_copy(update={"call_id": "tool-call-0002"}),
        second,
    )

    with pytest.raises(ValidationError, match="tool call_id"):
        AgentRunReport.model_validate(payload)


def test_completed_report_links_exactly_to_last_ok_final_response() -> None:
    payload = valid_report_payload()
    record = payload["model_call_records"][0]
    assert isinstance(record, ModelCallRecord)
    assert record.response is not None

    mismatched_final = payload["final_rca"].model_copy(
        update={"confidence": 0.81}
    )
    with pytest.raises(ValidationError, match="final"):
        AgentRunReport.model_validate(
            {**payload, "final_rca": mismatched_final}
        )

    query_response = record.response.model_copy(
        update={
            "action": MetricsAction(
                action_type="metrics",
                started_at=UTC_START,
                ended_at=UTC_END,
                service=None,
            )
        }
    )
    with pytest.raises(ValidationError, match="FinalAction|final"):
        AgentRunReport.model_validate(
            {
                **payload,
                "model_call_records": (
                    record.model_copy(update={"response": query_response}),
                ),
            }
        )

    error_record = record.model_copy(
        update={
            "charged_tokens": 0,
            "status": "ERROR",
            "error_code": StableErrorCode.MODEL_PROTOCOL_VIOLATION,
        }
    )
    snapshot = payload["budget_snapshot"]
    assert isinstance(snapshot, BudgetSnapshot)
    with pytest.raises(ValidationError, match="last model|final"):
        AgentRunReport.model_validate(
            {
                **payload,
                "model_call_records": (error_record,),
                "budget_snapshot": snapshot.model_copy(
                    update={"total_tokens": 0}
                ),
            }
        )


def test_terminated_final_rca_invalid_preserves_final_action_record() -> None:
    payload = valid_report_payload(
        final_rca=None,
        terminal_status="TERMINATED",
        terminal_reason="FINAL_RCA_INVALID",
        terminal_error_code=StableErrorCode.MODEL_PROTOCOL_VIOLATION,
    )
    report = AgentRunReport.model_validate(payload)
    last = report.model_call_records[-1]
    assert last.status == "OK"
    assert last.response is not None
    assert isinstance(last.response.action, FinalAction)


def test_report_rejects_backdated_model_call_after_tool_quarantine() -> None:
    payload = valid_report_payload()
    first = payload["model_call_records"][0]
    quarantined = payload["tool_call_records"][0]
    assert isinstance(first, ModelCallRecord)
    assert first.response is not None
    assert isinstance(quarantined, ToolCallRecord)

    query_response = first.response.model_copy(
        update={
            "action": MetricsAction(
                action_type="metrics",
                started_at=UTC_START,
                ended_at=UTC_END,
                service="checkoutservice",
            )
        }
    )
    first = first.model_copy(update={"response": query_response})
    second = _second_model_record(first)
    second = second.model_copy(
        update={
            "started_at": UTC_START,
            "ended_at": UTC_START,
            "monotonic_duration_seconds": 0.0,
            "request": second.request.model_copy(
                update={
                    "transcript": (),
                    "evidence": (),
                    "remaining_budgets": RemainingBudgets(
                        model_calls=6,
                        tool_calls=8,
                        total_tokens=11_880,
                    ),
                }
            ),
            "response": second.response.model_copy(
                update={
                    "started_at": UTC_START,
                    "ended_at": UTC_START,
                    "monotonic_duration_seconds": 0.0,
                }
            ),
        }
    )
    quarantine = quarantined.model_copy(
        update={
            "status": "ERROR",
            "error_code": StableErrorCode.INTERNAL_CONTRACT_VIOLATION,
            "evidence_quarantined": True,
            "usable": False,
        }
    )
    limits = payload["budget_limits"]
    assert isinstance(limits, BudgetLimits)
    crafted = {
        **payload,
        "final_rca": None,
        "model_call_records": (first, second),
        "tool_call_records": (quarantine,),
        "evidence_index": quarantine.evidence,
        "budget_snapshot": BudgetSnapshot(
            model_calls=2,
            tool_calls=1,
            total_tokens=240,
            limits=limits,
        ),
        "terminal_status": "TERMINATED",
        "terminal_reason": "TOOL_EVIDENCE_ALLOCATION_INVALID",
        "terminal_error_code": (
            StableErrorCode.INTERNAL_CONTRACT_VIOLATION
        ),
    }

    with pytest.raises(ValidationError, match="transcript|quarantine"):
        AgentRunReport.model_validate(crafted)


def test_report_rejects_tool_records_after_a_final_action() -> None:
    payload = valid_report_payload()
    final_rca = payload["final_rca"]
    limits = payload["budget_limits"]
    assert isinstance(final_rca, RCAResult)
    assert isinstance(limits, BudgetLimits)
    payload["model_call_records"] = (completed_model_record(final_rca),)
    payload["budget_snapshot"] = BudgetSnapshot(
        model_calls=1,
        tool_calls=2,
        total_tokens=120,
        limits=limits,
    )
    with pytest.raises(ValidationError, match="tool|FinalAction|final"):
        AgentRunReport.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    (
        "omit_transcript",
        "alter_transcript",
        "omit_evidence",
        "invent_evidence",
    ),
)
def test_model_request_requires_exact_tool_prefix_projection(
    mutation: str,
) -> None:
    payload = valid_report_payload()
    records = payload["model_call_records"]
    assert isinstance(records, tuple)
    second = records[1]
    assert isinstance(second, ModelCallRecord)
    request = second.request
    if mutation == "omit_transcript":
        changed_request = request.model_copy(update={"transcript": ()})
    elif mutation == "alter_transcript":
        changed_request = request.model_copy(
            update={
                "transcript": (
                    request.transcript[0].model_copy(
                        update={"status": "ERROR"}
                    ),
                )
            }
        )
    elif mutation == "omit_evidence":
        changed_request = request.model_copy(update={"evidence": ()})
    else:
        changed_request = request.model_copy(
            update={
                "evidence": (
                    *request.evidence,
                    evidence(LOGS_REF, EvidenceSource.LOGS),
                )
            }
        )
    payload["model_call_records"] = (
        records[0],
        second.model_copy(update={"request": changed_request}),
        *records[2:],
    )

    with pytest.raises(ValidationError, match="transcript|Evidence|prefix"):
        AgentRunReport.model_validate(payload)


def test_report_binds_every_model_request_to_authoritative_incident() -> None:
    payload = valid_report_payload()
    records = payload["model_call_records"]
    assert isinstance(records, tuple)
    second = records[1]
    assert isinstance(second, ModelCallRecord)
    changed_incident = second.request.incident.model_copy(
        update={"summary": "Different incident state."}
    )
    changed_request = second.request.model_copy(
        update={"incident": changed_incident}
    )
    payload["model_call_records"] = (
        records[0],
        second.model_copy(update={"request": changed_request}),
        *records[2:],
    )

    with pytest.raises(ValidationError, match="incident"):
        AgentRunReport.model_validate(payload)


def test_report_binds_every_model_request_to_exact_remaining_budgets() -> None:
    payload = valid_report_payload()
    records = payload["model_call_records"]
    assert isinstance(records, tuple)
    second = records[1]
    assert isinstance(second, ModelCallRecord)
    changed_request = second.request.model_copy(
        update={
            "remaining_budgets": RemainingBudgets(
                model_calls=999,
                tool_calls=999,
                total_tokens=999_999,
            )
        }
    )
    payload["model_call_records"] = (
        records[0],
        second.model_copy(update={"request": changed_request}),
        *records[2:],
    )

    with pytest.raises(ValidationError, match="remaining|budget"):
        AgentRunReport.model_validate(payload)
