from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from pydantic import ValidationError

import ecomsre.phase1.agent as agent_module
from ecomsre.backends.live_protocol import (
    BackendObservation,
    BackendStatus,
    ChangesObservationBatch,
    LogsObservationBatch,
    MetricsObservationBatch,
    TracesObservationBatch,
)
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase1.agent import SingleAgent
from ecomsre.phase1.contracts import (
    AgentRunReport,
    AgentTerminalReason,
    BudgetLimits,
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    InvestigationRequest,
    MetricsAction,
    ModelConfiguration,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RCADecision,
    Severity,
    StableErrorCode,
)
from ecomsre.tools.base import ToolContext, ToolStatus

START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = START + timedelta(minutes=5)
RUN_ID = "d" * 32
SHA256 = "a" * 64


def incident(*, hint: str | None = None) -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id="incident-001",
        alert_source_service=hint,
        summary="Request success rate is below the objective.",
        started_at=START,
        ended_at=END,
        affected_sli="request success rate",
        severity=Severity.SEV2,
    )


def request(
    *,
    hint: str | None = None,
    limits: BudgetLimits | None = None,
) -> InvestigationRequest:
    return InvestigationRequest(
        schema_version="phase1.investigation-request.v1",
        request_id="investigation-001",
        run_id=RUN_ID,
        agent_id="single-agent",
        task_id="root-cause-analysis",
        incident=incident(hint=hint),
        budgets=limits
        or BudgetLimits(
            max_model_calls=8,
            max_tool_calls=8,
            max_total_tokens=12_000,
        ),
    )


def observation(
    *,
    service: str,
    mechanism: str,
    anomaly: bool = True,
    source: EvidenceSource = EvidenceSource.METRICS,
) -> BackendObservation:
    if mechanism == "request_processing_failure":
        observation_type = {
            EvidenceSource.METRICS: "request_handler_failure_rate",
            EvidenceSource.LOGS: "request_handler_failure_log",
            EvidenceSource.TRACES: "request_handler_failure_span",
            EvidenceSource.CHANGES: "deployment",
        }[source]
        attributes: dict[str, str | int | float | bool] = (
            {
                "anomaly": anomaly,
                "component_role": "request_handler",
                "outcome": "failure",
            }
            if source is not EvidenceSource.CHANGES
            else {
                "release_scope": "request_path",
                "risk_signal": "request_handler_regression",
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
            dict[str, str | int | float | bool],
        ] = {
            EvidenceSource.METRICS: {"anomaly": anomaly, "error_rate": 0.42},
            EvidenceSource.LOGS: {
                "diagnostic_kind": "configuration_parse_failure",
            },
            EvidenceSource.TRACES: {
                "diagnostic_kind": "configuration_parse_failure",
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
            "anomaly": anomaly,
            "dependency_role": "cache",
            "outcome": "timeout",
        }
    else:
        observation_type = "normal_request_rate" if not anomaly else "opaque_signal"
        attributes = {"anomaly": anomaly}
    return BackendObservation(
        service=service,
        started_at=START,
        ended_at=END,
        observation_type=observation_type,
        attributes=(
            tuple(
                EvidenceAttribute(name=name, value=value)
                for name, value in sorted(attributes.items())
            )
        ),
        limitations=("fixture-backed replay only",),
    )


def anomalous_observation_without_mechanism(
    *,
    service: str,
) -> BackendObservation:
    return BackendObservation(
        service=service,
        started_at=START,
        ended_at=END,
        observation_type="incident_signal",
        attributes=(EvidenceAttribute(name="anomaly", value=True),),
        limitations=("fixture-backed replay only",),
    )


def evidence_native_observation(
    *,
    service: str,
    observation_type: str,
    attributes: dict[str, str | int | float | bool],
) -> BackendObservation:
    return BackendObservation(
        service=service,
        started_at=START,
        ended_at=END,
        observation_type=observation_type,
        attributes=tuple(
            EvidenceAttribute(name=name, value=value)
            for name, value in sorted(attributes.items())
        ),
        limitations=("fixture-backed replay only",),
    )


def empty_or_observed_batch(
    source: EvidenceSource,
    observations: tuple[BackendObservation, ...],
    *,
    status: BackendStatus = BackendStatus.AVAILABLE,
) -> object:
    batch_type = {
        EvidenceSource.METRICS: MetricsObservationBatch,
        EvidenceSource.LOGS: LogsObservationBatch,
        EvidenceSource.TRACES: TracesObservationBatch,
        EvidenceSource.CHANGES: ChangesObservationBatch,
    }[source]
    return batch_type(
        status=status,
        observations=observations,
        raw_artifact_indices=tuple(range(len(observations))),
        raw_artifact_filename=f"{source.value.lower()}.json",
        raw_artifact_sha256=SHA256,
    )


class MemoryBackend:
    def __init__(
        self,
        *,
        metrics: tuple[BackendObservation, ...],
        traces: tuple[BackendObservation, ...] = (),
        logs: tuple[BackendObservation, ...] = (),
        changes: tuple[BackendObservation, ...] = (),
        logs_status: BackendStatus = BackendStatus.AVAILABLE,
    ) -> None:
        self.batches = {
            "metrics": empty_or_observed_batch(EvidenceSource.METRICS, metrics),
            "traces": empty_or_observed_batch(EvidenceSource.TRACES, traces),
            "logs": empty_or_observed_batch(
                EvidenceSource.LOGS,
                logs,
                status=logs_status,
            ),
            "changes": empty_or_observed_batch(EvidenceSource.CHANGES, changes),
        }
        self.calls: list[tuple[str, str | None, float]] = []

    def _result(
        self,
        source: Literal["metrics", "logs", "traces", "changes"],
        query: object,
        timeout_seconds: float,
    ) -> object:
        service = getattr(query, "service")
        self.calls.append((source, service, timeout_seconds))
        batch = self.batches[source]
        if service is None or batch.status is not BackendStatus.AVAILABLE:
            return batch
        selected = tuple(
            (index, item)
            for index, item in zip(
                batch.raw_artifact_indices,
                batch.observations,
                strict=True,
            )
            if item.service == service
        )
        return type(batch)(
            status=batch.status,
            observations=tuple(item for _, item in selected),
            raw_artifact_indices=tuple(index for index, _ in selected),
            raw_artifact_filename=batch.raw_artifact_filename,
            raw_artifact_sha256=batch.raw_artifact_sha256,
        )

    def query_metrics(self, query: object, *, timeout_seconds: float) -> object:
        return self._result("metrics", query, timeout_seconds)

    def search_logs(self, query: object, *, timeout_seconds: float) -> object:
        return self._result("logs", query, timeout_seconds)

    def search_traces(self, query: object, *, timeout_seconds: float) -> object:
        return self._result("traces", query, timeout_seconds)

    def list_changes(self, query: object, *, timeout_seconds: float) -> object:
        return self._result("changes", query, timeout_seconds)


def run(
    backend: MemoryBackend,
    *,
    hint: str | None = None,
    limits: BudgetLimits | None = None,
    gateway: object | None = None,
) -> object:
    selected_gateway = gateway or ScriptedModelGateway()
    agent = SingleAgent(
        gateway=selected_gateway,
        backend=backend,
        model_configuration=ModelConfiguration(
            model_name="scripted-replay-v1",
            temperature=0.0,
            model_timeout_seconds=1.0,
        ),
        tool_timeout_seconds=0.5,
    )
    return agent.run(request(hint=hint, limits=limits))


def action_sequence(report: object) -> tuple[str, ...]:
    return tuple(record.action.action_type for record in report.tool_call_records)


def test_metrics_and_traces_can_confirm_request_processing_failure() -> None:
    backend = MemoryBackend(
        metrics=(
            observation(
                service="ad",
                mechanism="request_processing_failure",
            ),
        ),
        traces=(
            observation(
                service="ad",
                mechanism="request_processing_failure",
                source=EvidenceSource.TRACES,
            ),
        ),
    )

    report = run(backend)

    assert report.terminal_status == "COMPLETED"
    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "ad"
    assert report.final_rca.fault_mechanism == "request_processing_failure"
    assert action_sequence(report) == ("metrics", "traces", "changes")
    assert {ref.split("/")[3] for ref in report.final_rca.supporting_evidence} == {
        "metrics",
        "traces",
    }
    for record in (*report.model_call_records, *report.tool_call_records):
        assert record.run_id == RUN_ID
        assert record.agent_id == "single-agent"
        assert record.incident_id == "incident-001"
        assert record.task_id == "root-cause-analysis"
        assert record.monotonic_duration_seconds >= 0


def test_native_request_handler_signals_infer_request_processing_failure() -> None:
    backend = MemoryBackend(
        metrics=(
            evidence_native_observation(
                service="ad",
                observation_type="request_handler_failure_rate",
                attributes={
                    "anomaly": True,
                    "component_role": "request_handler",
                    "error_rate": 0.37,
                    "outcome": "failure",
                },
            ),
        ),
        traces=(
            evidence_native_observation(
                service="ad",
                observation_type="request_handler_failure_span",
                attributes={
                    "component_role": "request_handler",
                    "error_count": 14,
                    "outcome": "failure",
                },
            ),
        ),
    )

    report = run(backend)

    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "ad"
    assert report.final_rca.fault_mechanism == "request_processing_failure"
    assert action_sequence(report) == ("metrics", "traces", "changes")


def test_native_configuration_diagnostics_require_matching_change() -> None:
    backend = MemoryBackend(
        metrics=(
            evidence_native_observation(
                service="ad",
                observation_type="request_error_rate",
                attributes={"anomaly": True, "error_rate": 0.42},
            ),
        ),
        traces=(
            evidence_native_observation(
                service="ad",
                observation_type="configuration_error_span",
                attributes={
                    "diagnostic_kind": "configuration_parse_failure",
                    "error_count": 18,
                },
            ),
        ),
        logs=(
            evidence_native_observation(
                service="ad",
                observation_type="configuration_error_log",
                attributes={
                    "diagnostic_kind": "configuration_parse_failure",
                    "sample_count": 18,
                },
            ),
        ),
        changes=(
            evidence_native_observation(
                service="ad",
                observation_type="configuration_transition",
                attributes={
                    "change_kind": "configuration",
                    "transition": "valid_to_invalid",
                },
            ),
        ),
    )

    report = run(backend)

    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "ad"
    assert report.final_rca.fault_mechanism == "runtime_configuration_failure"
    assert action_sequence(report) == (
        "metrics",
        "traces",
        "logs",
        "changes",
    )


def test_native_cache_client_timeout_signals_infer_cache_backend_timeout() -> None:
    backend = MemoryBackend(
        metrics=(
            evidence_native_observation(
                service="recommendation",
                observation_type="cache_timeout_rate",
                attributes={
                    "anomaly": True,
                    "dependency_role": "cache",
                    "outcome": "timeout",
                    "timeout_rate": 0.29,
                },
            ),
        ),
        traces=(
            evidence_native_observation(
                service="recommendation",
                observation_type="cache_client_timeout_span",
                attributes={
                    "dependency_role": "cache",
                    "outcome": "timeout",
                    "timeout_count": 11,
                },
            ),
        ),
    )

    report = run(backend)

    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "recommendation"
    assert report.final_rca.fault_mechanism == "cache_backend_timeout"
    assert action_sequence(report) == ("metrics", "traces")


def test_native_frontend_request_path_change_competes_but_cannot_support_ad() -> None:
    backend = MemoryBackend(
        metrics=(
            evidence_native_observation(
                service="ad",
                observation_type="request_handler_failure_rate",
                attributes={
                    "anomaly": True,
                    "component_role": "request_handler",
                    "error_rate": 0.33,
                    "outcome": "failure",
                },
            ),
        ),
        traces=(
            evidence_native_observation(
                service="ad",
                observation_type="request_handler_failure_span",
                attributes={
                    "component_role": "request_handler",
                    "error_count": 12,
                    "outcome": "failure",
                },
            ),
        ),
        changes=(
            evidence_native_observation(
                service="frontend",
                observation_type="deployment",
                attributes={
                    "release_scope": "request_path",
                    "risk_signal": "request_handler_regression",
                    "status": "completed",
                },
            ),
        ),
    )

    report = run(backend, hint="frontend")

    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "ad"
    assert action_sequence(report) == ("metrics", "traces", "changes")
    decoy = next(
        item
        for item in report.evidence_index
        if item.source is EvidenceSource.CHANGES and item.service == "frontend"
    )
    assert decoy.evidence_ref in report.tool_call_records[-1].evidence_refs
    assert decoy.evidence_ref not in report.final_rca.supporting_evidence


def test_runtime_configuration_failure_without_changes_cannot_confirm() -> None:
    backend = MemoryBackend(
        metrics=(
            observation(
                service="ad",
                mechanism="runtime_configuration_failure",
            ),
        ),
        traces=(
            observation(
                service="ad",
                mechanism="runtime_configuration_failure",
                source=EvidenceSource.TRACES,
            ),
        ),
        logs=(
            observation(
                service="ad",
                mechanism="runtime_configuration_failure",
                source=EvidenceSource.LOGS,
            ),
        ),
    )

    report = run(backend)

    assert report.final_rca.decision is RCADecision.NEED_MORE_EVIDENCE
    assert report.final_rca.root_service == "ad"
    assert report.final_rca.fault_mechanism == ("runtime_configuration_failure")
    assert action_sequence(report) == (
        "metrics",
        "traces",
        "logs",
        "changes",
    )
    assert "Changes" in report.final_rca.missing_evidence[0]


def test_wrong_hint_is_ignored_and_frontend_decoy_is_stored_not_supported() -> None:
    backend = MemoryBackend(
        metrics=(
            observation(
                service="ad",
                mechanism="request_processing_failure",
            ),
        ),
        traces=(
            observation(
                service="ad",
                mechanism="request_processing_failure",
                source=EvidenceSource.TRACES,
            ),
        ),
        changes=(
            observation(
                service="frontend",
                mechanism="deployment_change",
                source=EvidenceSource.CHANGES,
            ),
        ),
    )

    report = run(backend, hint="frontend")

    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "ad"
    assert backend.calls[-1][0:2] == ("changes", None)
    decoy = next(item for item in report.evidence_index if item.service == "frontend")
    assert decoy.source is EvidenceSource.CHANGES
    assert decoy.evidence_ref not in report.final_rca.supporting_evidence
    assert all(
        record.request.incident.alert_source_service == "frontend"
        for record in report.model_call_records
    )


def test_policy_is_dynamic_and_skips_unnecessary_tools() -> None:
    backend = MemoryBackend(
        metrics=(
            observation(
                service="recommendation",
                mechanism="cache_backend_timeout",
            ),
        ),
        traces=(
            observation(
                service="recommendation",
                mechanism="cache_backend_timeout",
                source=EvidenceSource.TRACES,
            ),
        ),
    )

    report = run(backend)

    assert report.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert report.final_rca.root_service == "recommendation"
    assert action_sequence(report) == ("metrics", "traces")


def test_normal_metrics_lead_to_changes_check_and_abstention() -> None:
    backend = MemoryBackend(
        metrics=(
            observation(
                service="ad",
                mechanism="none",
                anomaly=False,
            ),
        ),
        changes=(
            observation(
                service="ad",
                mechanism="runtime_configuration_failure",
                source=EvidenceSource.CHANGES,
            ),
        ),
    )

    report = run(backend)

    assert action_sequence(report) == ("metrics", "changes")
    assert report.final_rca.decision is RCADecision.ABSTAIN
    assert report.final_rca.root_service is None
    assert report.final_rca.fault_mechanism is None


def test_anomaly_without_mechanism_is_not_misclassified_as_normal() -> None:
    item = anomalous_observation_without_mechanism(service="ad")
    backend = MemoryBackend(
        metrics=(item,),
        traces=(item,),
        logs=(item,),
        changes=(item,),
    )

    report = run(backend)

    assert action_sequence(report) == (
        "metrics",
        "traces",
        "logs",
        "changes",
    )
    assert report.final_rca.decision is RCADecision.NEED_MORE_EVIDENCE
    anomaly_refs = {
        evidence.evidence_ref
        for evidence in report.evidence_index
        if any(
            attribute.name == "anomaly" and attribute.value is True
            for attribute in evidence.attributes
        )
    }
    assert anomaly_refs
    assert anomaly_refs.isdisjoint(report.final_rca.contradicting_evidence)


@pytest.mark.parametrize(
    ("limits", "expected_model_calls", "expected_tool_calls"),
    (
        (
            BudgetLimits(
                max_model_calls=1,
                max_tool_calls=8,
                max_total_tokens=12_000,
            ),
            1,
            1,
        ),
        (
            BudgetLimits(
                max_model_calls=8,
                max_tool_calls=0,
                max_total_tokens=12_000,
            ),
            1,
            0,
        ),
        (
            BudgetLimits(
                max_model_calls=8,
                max_tool_calls=8,
                max_total_tokens=1,
            ),
            1,
            0,
        ),
    ),
)
def test_model_tool_and_token_budget_termination_is_stable(
    limits: BudgetLimits,
    expected_model_calls: int,
    expected_tool_calls: int,
) -> None:
    backend = MemoryBackend(
        metrics=(
            observation(
                service="ad",
                mechanism="request_processing_failure",
            ),
        )
    )

    report = run(backend, limits=limits)

    assert report.terminal_status == "TERMINATED"
    assert report.terminal_error_code is StableErrorCode.BUDGET_EXHAUSTED
    assert report.final_rca is None
    assert report.budget_snapshot.model_calls == expected_model_calls
    assert report.budget_snapshot.tool_calls == expected_tool_calls


class RaisingGateway:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise self.error


@pytest.mark.parametrize(
    ("error", "terminal_error_code"),
    (
        (TimeoutError("timed out"), StableErrorCode.TIMEOUT.value),
        (RuntimeError("provider failed"), "MODEL_PROTOCOL_VIOLATION"),
    ),
)
def test_model_timeout_and_error_have_stable_terminal_reports(
    error: Exception,
    terminal_error_code: str,
) -> None:
    report = run(
        MemoryBackend(metrics=()),
        gateway=RaisingGateway(error),
    )

    assert report.terminal_status == "TERMINATED"
    assert report.terminal_error_code.value == terminal_error_code
    assert report.final_rca is None
    assert len(report.model_call_records) == 1
    assert report.model_call_records[0].response is None


class TimeoutBackend(MemoryBackend):
    def query_metrics(self, query: object, *, timeout_seconds: float) -> object:
        raise TimeoutError("backend timed out")


def test_backend_timeout_is_a_typed_tool_error_and_fails_closed() -> None:
    report = run(TimeoutBackend(metrics=()))

    assert report.final_rca.decision is RCADecision.NEED_MORE_EVIDENCE
    assert report.tool_call_records[0].status == "ERROR"
    assert report.tool_call_records[0].error_code is StableErrorCode.TIMEOUT
    assert report.tool_call_records[0].evidence_refs == ()


class RecordingGateway:
    def __init__(
        self,
        *,
        total_tokens: int = 2,
        outside_window: bool = False,
    ) -> None:
        self.total_tokens = total_tokens
        self.outside_window = outside_window
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        now = datetime.now(UTC)
        return ModelResponse(
            schema_version="phase1.model-response.v1",
            request_id=request.request_id,
            response_id=f"response-{self.calls:04d}",
            run_id=request.run_id,
            agent_id=request.agent_id,
            incident_id=request.incident_id,
            task_id=request.task_id,
            provider_name="recording",
            model_name=request.model_name,
            action=MetricsAction(
                action_type="metrics",
                started_at=(
                    START - timedelta(seconds=1) if self.outside_window else START
                ),
                ended_at=END,
                service=None,
            ),
            usage=ModelUsage(
                input_tokens=self.total_tokens,
                output_tokens=0,
                total_tokens=self.total_tokens,
            ),
            started_at=now,
            ended_at=now,
            monotonic_duration_seconds=0.0,
            error_code=None,
        )


class MaliciousTimingGateway(RecordingGateway):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = super().complete(request)
        if self.mode == "past":
            past = response.started_at - timedelta(days=1)
            return response.model_copy(update={"started_at": past, "ended_at": past})
        if self.mode == "future":
            future = response.started_at + timedelta(days=1)
            return response.model_copy(
                update={"started_at": future, "ended_at": future}
            )
        return response.model_copy(update={"monotonic_duration_seconds": 10_000.0})


@pytest.mark.parametrize("mode", ("past", "future", "huge_monotonic"))
def test_malicious_provider_timing_terminates_with_sanitized_record(
    mode: str,
) -> None:
    report = run(
        MemoryBackend(metrics=()),
        gateway=MaliciousTimingGateway(mode),
        limits=BudgetLimits(
            max_model_calls=1,
            max_tool_calls=1,
            max_total_tokens=100,
        ),
    )

    assert report.terminal_status == "TERMINATED"
    assert report.terminal_reason == "MODEL_RESPONSE_INVALID"
    assert report.terminal_error_code is StableErrorCode.MODEL_PROTOCOL_VIOLATION
    assert report.final_rca is None
    record = report.model_call_records[-1]
    assert record.status == "ERROR"
    assert record.error_code is StableErrorCode.MODEL_PROTOCOL_VIOLATION
    assert record.charged_tokens == 0
    assert record.response is not None
    assert record.response.provider_name == "recording"
    assert record.response.model_name == record.request.model_name
    assert record.response.usage.total_tokens == 2
    assert (
        record.started_at
        <= record.response.started_at
        <= record.response.ended_at
        <= record.ended_at
    )
    assert (
        record.response.monotonic_duration_seconds <= record.monotonic_duration_seconds
    )


def test_zero_token_budget_preflight_does_not_call_provider() -> None:
    gateway = RecordingGateway()
    report = run(
        MemoryBackend(metrics=()),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=8,
            max_tool_calls=8,
            max_total_tokens=0,
        ),
    )

    assert gateway.calls == 0
    assert report.model_call_records == ()
    assert report.final_rca is None
    assert report.budget_snapshot.model_calls == 0
    assert report.budget_snapshot.total_tokens == 0
    assert report.terminal_error_code is StableErrorCode.BUDGET_EXHAUSTED


def test_oversized_response_is_retained_but_not_charged() -> None:
    gateway = RecordingGateway(total_tokens=2)
    report = run(
        MemoryBackend(metrics=()),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=1,
            max_tool_calls=8,
            max_total_tokens=1,
        ),
    )

    assert gateway.calls == 1
    assert report.final_rca is None
    assert report.terminal_reason == "TOKEN_BUDGET_EXHAUSTED"
    assert report.budget_snapshot.model_calls == 1
    assert report.budget_snapshot.total_tokens == 0
    record = report.model_call_records[0]
    assert record.model_call_consumed is True
    assert record.charged_tokens == 0
    assert record.response is not None
    assert record.response.usage.total_tokens == 2
    assert record.error_code is StableErrorCode.BUDGET_EXHAUSTED


def test_token_terminal_cannot_spoof_model_budget_priority() -> None:
    report = run(
        MemoryBackend(metrics=()),
        gateway=RecordingGateway(total_tokens=2),
        limits=BudgetLimits(
            max_model_calls=1,
            max_tool_calls=1,
            max_total_tokens=2,
        ),
    )

    assert report.terminal_reason == "MODEL_CALL_BUDGET_EXHAUSTED"
    spoofed = report.model_copy(
        update={"terminal_reason": AgentTerminalReason.TOKEN_BUDGET_EXHAUSTED}
    )
    with pytest.raises(ValidationError, match="token|model|budget"):
        AgentRunReport.model_validate(spoofed.model_dump(mode="python"))


def test_rejected_tool_action_records_dispatch_and_budget_truth() -> None:
    gateway = RecordingGateway(outside_window=True)
    report = run(
        MemoryBackend(metrics=()),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=1,
            max_tool_calls=1,
            max_total_tokens=100,
        ),
    )

    record = report.tool_call_records[0]
    assert record.error_code is StableErrorCode.OUTSIDE_INCIDENT_WINDOW
    assert record.budget_consumed is False
    assert record.dispatched is False
    assert report.budget_snapshot.tool_calls == 0


def test_absent_result_ref_mismatch_appends_terminal_quarantine_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_dispatch = agent_module._dispatch_tool

    def mismatching_dispatch(
        context: object,
        action: object,
    ) -> object:
        result = original_dispatch(context, action)
        return result.model_copy(
            update={"evidence_refs": (f"evidence://{RUN_ID}/metrics/9999",)}
        )

    monkeypatch.setattr(
        agent_module,
        "_dispatch_tool",
        mismatching_dispatch,
    )
    gateway = RecordingGateway()
    report = run(
        MemoryBackend(metrics=()),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=3,
            max_tool_calls=3,
            max_total_tokens=100,
        ),
    )

    assert gateway.calls == 1
    assert report.terminal_status == "TERMINATED"
    assert report.terminal_reason == "TOOL_EVIDENCE_ALLOCATION_INVALID"
    assert report.terminal_error_code is StableErrorCode.INTERNAL_CONTRACT_VIOLATION
    record = report.tool_call_records[-1]
    assert record.status == "ERROR"
    assert record.error_code is StableErrorCode.INTERNAL_CONTRACT_VIOLATION
    assert record.evidence == ()
    assert record.evidence_refs == ()
    assert record.evidence_quarantined is True
    assert record.usable is False
    assert record.budget_consumed is True
    assert record.dispatched is True
    assert report.budget_snapshot.tool_calls == 1


def test_failed_tool_persisted_evidence_is_preserved_and_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_dispatch = agent_module._dispatch_tool

    def persisted_error_dispatch(
        context: object,
        action: object,
    ) -> object:
        result = original_dispatch(context, action)
        assert result.status is ToolStatus.OK
        return result.model_copy(
            update={
                "status": ToolStatus.ERROR,
                "evidence_refs": (),
                "error_code": StableErrorCode.TIMEOUT,
            }
        )

    monkeypatch.setattr(
        agent_module,
        "_dispatch_tool",
        persisted_error_dispatch,
    )
    gateway = RecordingGateway()
    report = run(
        MemoryBackend(
            metrics=(
                observation(
                    service="ad",
                    mechanism="request_processing_failure",
                ),
            )
        ),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=3,
            max_tool_calls=3,
            max_total_tokens=100,
        ),
    )

    assert gateway.calls == 1
    assert report.terminal_status == "TERMINATED"
    assert report.terminal_reason == "FAILED_TOOL_PERSISTED_EVIDENCE"
    assert report.terminal_error_code is StableErrorCode.INTERNAL_CONTRACT_VIOLATION
    record = report.tool_call_records[-1]
    assert record.status == "ERROR"
    assert record.error_code is StableErrorCode.INTERNAL_CONTRACT_VIOLATION
    assert len(record.evidence) == 1
    assert record.evidence_refs == tuple(item.evidence_ref for item in record.evidence)
    assert record.evidence_quarantined is True
    assert record.usable is False
    assert report.evidence_index == record.evidence
    assert report.budget_snapshot.tool_calls == 1


def test_declared_dispatch_with_persisted_evidence_but_no_budget_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_dispatch = agent_module._dispatch_tool

    def unaccounted_dispatch(context: object, action: object) -> object:
        assert isinstance(context, ToolContext)
        result = original_dispatch(context, action)
        assert result.status is ToolStatus.OK
        assert result.budget_consumed is True
        assert result.dispatched is True
        context.budget._tool_calls -= 1
        return result

    monkeypatch.setattr(agent_module, "_dispatch_tool", unaccounted_dispatch)
    gateway = RecordingGateway()
    report = run(
        MemoryBackend(
            metrics=(
                observation(
                    service="ad",
                    mechanism="request_processing_failure",
                ),
            )
        ),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=3,
            max_tool_calls=3,
            max_total_tokens=100,
        ),
    )

    assert gateway.calls == 1
    assert report.terminal_reason == "TOOL_EVIDENCE_ALLOCATION_INVALID"
    assert report.terminal_error_code is StableErrorCode.INTERNAL_CONTRACT_VIOLATION
    record = report.tool_call_records[-1]
    assert len(record.evidence) == 1
    assert record.evidence_quarantined is True
    assert record.usable is False
    assert record.budget_consumed is False
    assert record.dispatched is False
    assert report.budget_snapshot.tool_calls == 0


@pytest.mark.parametrize("declared_status", (ToolStatus.OK, ToolStatus.ERROR))
def test_observed_budget_and_dispatch_override_false_result_declarations(
    monkeypatch: pytest.MonkeyPatch,
    declared_status: ToolStatus,
) -> None:
    original_dispatch = agent_module._dispatch_tool

    def false_declaration(context: object, action: object) -> object:
        result = original_dispatch(context, action)
        update: dict[str, object] = {
            "budget_consumed": False,
            "dispatched": False,
        }
        if declared_status is ToolStatus.ERROR:
            update.update(
                status=ToolStatus.ERROR,
                evidence_refs=(),
                error_code=StableErrorCode.TIMEOUT,
            )
        return result.model_copy(update=update)

    monkeypatch.setattr(agent_module, "_dispatch_tool", false_declaration)
    gateway = RecordingGateway()
    report = run(
        MemoryBackend(metrics=()),
        gateway=gateway,
        limits=BudgetLimits(
            max_model_calls=3,
            max_tool_calls=3,
            max_total_tokens=100,
        ),
    )

    assert gateway.calls == 1
    assert report.terminal_reason == "TOOL_EVIDENCE_ALLOCATION_INVALID"
    record = report.tool_call_records[-1]
    assert record.evidence_quarantined is True
    assert record.usable is False
    assert record.budget_consumed is True
    assert record.dispatched is True
    assert report.budget_snapshot.tool_calls == 1


class ConstructedResponseGateway:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse.model_construct(
            schema_version="phase1.model-response.v1",
            request_id=request.request_id,
            response_id="response-001",
            run_id=request.run_id,
            agent_id=request.agent_id,
            incident_id=request.incident_id,
            task_id=request.task_id,
            provider_name="constructed",
            model_name=request.model_name,
            action={
                "action_type": "metrics",
                "started_at": START,
                "ended_at": END,
                "service": None,
            },
            usage=ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
            started_at=START,
            ended_at=END,
            monotonic_duration_seconds=0.01,
            error_code=None,
            hidden="answer-key",
        )


def test_agent_rejects_model_construct_and_hidden_storage() -> None:
    report = run(
        MemoryBackend(metrics=()),
        gateway=ConstructedResponseGateway(),
    )

    assert report.terminal_status == "TERMINATED"
    assert report.terminal_error_code is StableErrorCode.MODEL_PROTOCOL_VIOLATION
    assert report.final_rca is None
