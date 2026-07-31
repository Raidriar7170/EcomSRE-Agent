from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from ecomsre.phase1.budgets import RunBudget
from ecomsre.phase1.contracts import (
    BudgetLimits,
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    Severity,
    StableErrorCode,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.tools.base import ToolContext

UTC_START = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
UTC_END = UTC_START + timedelta(minutes=5)
SHA256 = "a" * 64
RUN_ID = "b" * 32


def tools_api() -> SimpleNamespace:
    from ecomsre.backends.live_protocol import (
        BackendObservation,
        BackendStatus,
        ChangesObservationBatch,
        LogsObservationBatch,
        MetricsObservationBatch,
        TracesObservationBatch,
    )
    from ecomsre.tools.base import ToolContext, ToolStatus

    return SimpleNamespace(
        BackendObservation=BackendObservation,
        BackendStatus=BackendStatus,
        ChangesObservationBatch=ChangesObservationBatch,
        LogsObservationBatch=LogsObservationBatch,
        MetricsObservationBatch=MetricsObservationBatch,
        ToolContext=ToolContext,
        ToolStatus=ToolStatus,
        TracesObservationBatch=TracesObservationBatch,
    )


def incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id="inc-001",
        summary="Checkout latency exceeds the SLO.",
        started_at=UTC_START,
        ended_at=UTC_END,
        affected_sli="checkout p95 latency",
        severity=Severity.SEV2,
        alert_source_service="frontend",
    )


def observation(
    api: SimpleNamespace,
    *,
    service: str = "checkoutservice",
    observation_type: str = "incident_signal",
) -> object:
    return api.BackendObservation(
        service=service,
        started_at=UTC_START,
        ended_at=UTC_END,
        observation_type=observation_type,
        attributes=(EvidenceAttribute(name="fault_mechanism", value="timeout"),),
        limitations=("fixture-backed replay only",),
    )


SOURCE_CONFIG = (
    (
        "metrics",
        "query_metrics",
        "MetricsQuery",
        "MetricsObservationBatch",
        EvidenceSource.METRICS,
    ),
    (
        "logs",
        "search_logs",
        "LogsQuery",
        "LogsObservationBatch",
        EvidenceSource.LOGS,
    ),
    (
        "traces",
        "search_traces",
        "TracesQuery",
        "TracesObservationBatch",
        EvidenceSource.TRACES,
    ),
    (
        "changes",
        "list_changes",
        "ChangesQuery",
        "ChangesObservationBatch",
        EvidenceSource.CHANGES,
    ),
)


class FakeBackend:
    def __init__(self, results: dict[str, object]) -> None:
        self.results = results
        self.calls: list[tuple[str, object, float]] = []

    def query_metrics(self, query: object, *, timeout_seconds: float) -> object:
        self.calls.append(("query_metrics", query, timeout_seconds))
        return self.results["query_metrics"]

    def search_logs(self, query: object, *, timeout_seconds: float) -> object:
        self.calls.append(("search_logs", query, timeout_seconds))
        return self.results["search_logs"]

    def search_traces(self, query: object, *, timeout_seconds: float) -> object:
        self.calls.append(("search_traces", query, timeout_seconds))
        return self.results["search_traces"]

    def list_changes(self, query: object, *, timeout_seconds: float) -> object:
        self.calls.append(("list_changes", query, timeout_seconds))
        return self.results["list_changes"]


def batch_for(
    api: SimpleNamespace,
    batch_name: str,
    source: EvidenceSource,
    *,
    status: object | None = None,
    observations: tuple[object, ...] | None = None,
) -> object:
    batch_type = getattr(api, batch_name)
    batch_observations = (
        (observation(api),)
        if observations is None
        else observations
    )
    return batch_type(
        status=status or api.BackendStatus.AVAILABLE,
        observations=batch_observations,
        raw_artifact_indices=tuple(range(len(batch_observations))),
        raw_artifact_filename=f"{source.value.lower()}.json",
        raw_artifact_sha256=SHA256,
    )


def context(
    api: SimpleNamespace,
    backend: Any,
    *,
    max_tool_calls: int = 4,
) -> ToolContext:
    return api.ToolContext(
        incident=incident(),
        evidence_store=EvidenceStore(RUN_ID),
        budget=RunBudget(
            BudgetLimits(
                max_model_calls=0,
                max_tool_calls=max_tool_calls,
                max_total_tokens=0,
            )
        ),
        backend=backend,
        timeout_seconds=0.75,
    )


@pytest.mark.parametrize(
    ("module_name", "tool_name", "query_name", "batch_name", "source"),
    SOURCE_CONFIG,
)
def test_each_tool_returns_only_typed_allocated_evidence_refs(
    module_name: str,
    tool_name: str,
    query_name: str,
    batch_name: str,
    source: EvidenceSource,
) -> None:
    api = tools_api()
    module = importlib.import_module(f"ecomsre.tools.{module_name}")
    batch = batch_for(
        api,
        batch_name,
        source,
        observations=(
            observation(
                api,
                service="frontend",
                observation_type="alert_signal",
            ),
            observation(
                api,
                observation_type=f"{module_name}_signal",
            ),
        ),
    )
    backend = FakeBackend({tool_name: batch})
    tool_context = context(api, backend)
    query = getattr(module, query_name)(
        schema_version=f"phase1.{module_name}-query.v1",
        started_at=UTC_START,
        ended_at=UTC_END,
    )

    result = getattr(module, tool_name)(tool_context, query)

    assert result.tool_name == tool_name
    assert result.status is api.ToolStatus.OK
    assert result.error_code is None
    assert result.evidence_refs == (
        f"evidence://{RUN_ID}/{module_name}/0001",
        f"evidence://{RUN_ID}/{module_name}/0002",
    )
    stored = tool_context.evidence_store.snapshot()
    assert len(stored) == 2
    assert tuple(item.source for item in stored) == (source, source)
    assert tuple(item.service for item in stored) == (
        "frontend",
        "checkoutservice",
    )
    assert tuple(item.raw_artifact_ref for item in stored) == (
        f"{module_name}.json#0",
        f"{module_name}.json#1",
    )
    assert tuple(item.raw_artifact_sha256 for item in stored) == (
        SHA256,
        SHA256,
    )
    assert type(stored[0]) is type(stored[1])
    assert stored[0].evidence_ref.endswith(f"/{module_name}/0001")
    assert backend.calls == [(tool_name, query, 0.75)]
    assert getattr(backend.calls[0][1], "service") is None


def test_tool_derives_stable_bounded_summary_from_approved_observation_fields() -> None:
    api = tools_api()
    from ecomsre.phase1.contracts import MAX_EVIDENCE_SUMMARY_LENGTH
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    approved_payload = {
        "service": "checkoutservice",
        "started_at": UTC_START,
        "ended_at": UTC_END,
        "observation_type": "incident_signal",
        "attributes": (
            EvidenceAttribute(name="fault_mechanism", value="timeout"),
        ),
        "limitations": ("fixture-backed replay only",),
    }
    approved_observation = api.BackendObservation.model_validate(
        approved_payload
    )
    assert set(approved_observation.model_dump()) == {
        "service",
        "started_at",
        "ended_at",
        "observation_type",
        "attributes",
        "limitations",
    }
    with pytest.raises(ValidationError):
        api.BackendObservation.model_validate(
            {**approved_payload, "raw_index": 0}
        )
    batch = api.MetricsObservationBatch(
        status=api.BackendStatus.AVAILABLE,
        observations=(approved_observation,),
        raw_artifact_indices=(0,),
        raw_artifact_filename="metrics.json",
        raw_artifact_sha256=SHA256,
    )
    backend = FakeBackend({"query_metrics": batch})
    tool_context = context(api, backend)

    result = query_metrics(
        tool_context,
        MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.OK
    stored = tool_context.evidence_store.snapshot()
    assert stored[0].summary == (
        "metrics observation for checkoutservice: incident_signal."
    )
    assert len(stored[0].summary) <= MAX_EVIDENCE_SUMMARY_LENGTH


@pytest.mark.parametrize(
    ("module_name", "tool_name", "query_name", "_batch_name", "_source"),
    SOURCE_CONFIG,
)
@pytest.mark.parametrize(
    ("extra", "value"),
    (
        ("path", "/tmp/forbidden"),
        ("url", "https://forbidden.invalid"),
        ("write_payload", "forbidden"),
        ("shell_command", "echo forbidden"),
        ("backend_name", "live"),
        ("docker_id", "container-001"),
        ("arbitrary", {"nested": "forbidden"}),
    ),
)
def test_queries_reject_filesystem_network_and_write_fields(
    module_name: str,
    tool_name: str,
    query_name: str,
    _batch_name: str,
    _source: EvidenceSource,
    extra: str,
    value: object,
) -> None:
    module = importlib.import_module(f"ecomsre.tools.{module_name}")
    query_type = getattr(module, query_name)

    with pytest.raises(ValidationError):
        query_type.model_validate(
            {
                "schema_version": f"phase1.{module_name}-query.v1",
                "started_at": UTC_START,
                "ended_at": UTC_END,
                extra: value,
            }
        )
    assert callable(getattr(module, tool_name))


def test_tool_context_revalidates_incident_before_any_tool_activity() -> None:
    api = tools_api()
    from ecomsre.phase1.validator import (
        EvidenceValidationError,
        EvidenceValidationReason,
    )

    invalid_incident = incident().model_copy(
        update={"evaluator_truth": "paymentservice"}
    )
    backend = FakeBackend({})
    store = EvidenceStore(RUN_ID)
    budget = RunBudget(
        BudgetLimits(
            max_model_calls=0,
            max_tool_calls=1,
            max_total_tokens=0,
        )
    )
    budget_before = budget.snapshot()

    with pytest.raises(EvidenceValidationError) as raised:
        api.ToolContext(
            incident=invalid_incident,
            evidence_store=store,
            budget=budget,
            backend=backend,
            timeout_seconds=0.75,
        )

    assert (
        raised.value.code
        is EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED
    )
    assert budget.snapshot() == budget_before
    assert store.snapshot() == ()
    assert backend.calls == []


@pytest.mark.parametrize(
    ("module_name", "tool_name", "query_name", "_batch_name", "_source"),
    SOURCE_CONFIG,
)
@pytest.mark.parametrize("bypass_kind", ("hidden_storage", "model_construct"))
def test_each_tool_revalidates_hidden_query_storage_before_budget_or_dispatch(
    module_name: str,
    tool_name: str,
    query_name: str,
    _batch_name: str,
    _source: EvidenceSource,
    bypass_kind: str,
) -> None:
    api = tools_api()
    module = importlib.import_module(f"ecomsre.tools.{module_name}")
    query_type = getattr(module, query_name)
    if bypass_kind == "hidden_storage":
        query = query_type(
            schema_version=f"phase1.{module_name}-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        )
        query.__dict__["backend_name"] = "hidden-live-backend"
    else:
        query = query_type.model_construct(
            schema_version="phase1.invalid-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
            service=None,
        )
    backend = FakeBackend({})
    tool_context = context(api, backend, max_tool_calls=1)
    budget_before = tool_context.budget.snapshot()

    result = getattr(module, tool_name)(tool_context, query)

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.INVALID_QUERY
    assert result.evidence_refs == ()
    assert tool_context.budget.snapshot() == budget_before
    assert tool_context.evidence_store.snapshot() == ()
    assert backend.calls == []


def test_tool_dispatch_receives_reconstructed_query_not_mutable_caller_alias() -> None:
    api = tools_api()
    from ecomsre.backends.live_protocol import (
        ChangesObservationBatch,
        LogsObservationBatch,
        MetricsObservationBatch,
        ObservabilityBackend,
        TracesObservationBatch,
    )
    from ecomsre.tools.changes import ChangesQuery
    from ecomsre.tools.logs import LogsQuery
    from ecomsre.tools.metrics import MetricsQuery, query_metrics
    from ecomsre.tools.traces import TracesQuery

    caller_query = MetricsQuery(
        schema_version="phase1.metrics-query.v1",
        started_at=UTC_START,
        ended_at=UTC_END,
    )
    expected_batch = batch_for(
        api,
        "MetricsObservationBatch",
        EvidenceSource.METRICS,
    )

    class AliasMutatingProtocolBackend:
        received_query: MetricsQuery | None = None

        def query_metrics(
            self,
            query: MetricsQuery,
            *,
            timeout_seconds: float,
        ) -> MetricsObservationBatch:
            caller_query.__dict__["service"] = "caller-mutated"
            self.received_query = query
            return expected_batch

        def search_logs(
            self,
            query: LogsQuery,
            *,
            timeout_seconds: float,
        ) -> LogsObservationBatch:
            raise AssertionError("unexpected logs dispatch")

        def search_traces(
            self,
            query: TracesQuery,
            *,
            timeout_seconds: float,
        ) -> TracesObservationBatch:
            raise AssertionError("unexpected traces dispatch")

        def list_changes(
            self,
            query: ChangesQuery,
            *,
            timeout_seconds: float,
        ) -> ChangesObservationBatch:
            raise AssertionError("unexpected changes dispatch")

    backend_impl = AliasMutatingProtocolBackend()
    backend: ObservabilityBackend = backend_impl
    tool_context = context(api, backend, max_tool_calls=1)

    result = query_metrics(tool_context, caller_query)

    assert result.status is api.ToolStatus.OK
    assert backend_impl.received_query is not caller_query
    assert backend_impl.received_query is not None
    assert backend_impl.received_query.service is None
    assert caller_query.service == "caller-mutated"


def test_outside_incident_window_fails_before_budget_or_dispatch() -> None:
    api = tools_api()
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    backend = FakeBackend({})
    tool_context = context(api, backend, max_tool_calls=1)
    query = MetricsQuery(
        schema_version="phase1.metrics-query.v1",
        started_at=UTC_START - timedelta(seconds=1),
        ended_at=UTC_END,
    )

    result = query_metrics(tool_context, query)

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.OUTSIDE_INCIDENT_WINDOW
    assert result.evidence_refs == ()
    assert tool_context.budget.remaining_tool_calls == 1
    assert backend.calls == []


def test_budget_exhaustion_is_atomic_and_does_not_dispatch() -> None:
    api = tools_api()
    from ecomsre.tools.logs import LogsQuery, search_logs

    backend = FakeBackend({})
    tool_context = context(api, backend, max_tool_calls=0)
    before = tool_context.budget.snapshot()

    result = search_logs(
        tool_context,
        LogsQuery(
            schema_version="phase1.logs-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.BUDGET_EXHAUSTED
    assert result.evidence_refs == ()
    assert tool_context.budget.snapshot() == before
    assert backend.calls == []


@pytest.mark.parametrize(
    ("backend_status", "expected_error"),
    (
        ("UNAVAILABLE", StableErrorCode.BACKEND_UNAVAILABLE),
        ("TIMEOUT", StableErrorCode.TIMEOUT),
    ),
)
def test_backend_nonavailable_status_returns_error_without_evidence(
    backend_status: str,
    expected_error: StableErrorCode,
) -> None:
    api = tools_api()
    from ecomsre.tools.traces import TracesQuery, search_traces

    batch = batch_for(
        api,
        "TracesObservationBatch",
        EvidenceSource.TRACES,
        status=api.BackendStatus(backend_status),
        observations=(),
    )
    backend = FakeBackend({"search_traces": batch})
    tool_context = context(api, backend)

    result = search_traces(
        tool_context,
        TracesQuery(
            schema_version="phase1.traces-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is expected_error
    assert result.evidence_refs == ()
    assert tool_context.evidence_store.snapshot() == ()
    assert tool_context.budget.remaining_tool_calls == 3


def test_backend_timeout_exception_maps_to_stable_timeout() -> None:
    api = tools_api()
    from ecomsre.tools.changes import ChangesQuery, list_changes

    class TimeoutBackend(FakeBackend):
        def list_changes(
            self,
            query: object,
            *,
            timeout_seconds: float,
        ) -> object:
            self.calls.append(("list_changes", query, timeout_seconds))
            raise TimeoutError("backend timed out")

    backend = TimeoutBackend({})
    tool_context = context(api, backend)
    result = list_changes(
        tool_context,
        ChangesQuery(
            schema_version="phase1.changes-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.TIMEOUT
    assert result.evidence_refs == ()


@pytest.mark.parametrize(
    ("row_count", "expected_status", "expected_error"),
    (
        (64, "OK", None),
        (
            65,
            "ERROR",
            StableErrorCode.MALFORMED_REPLAY_ARTIFACT,
        ),
    ),
)
def test_metrics_tool_enforces_per_call_evidence_ref_boundary_atomically(
    row_count: int,
    expected_status: str,
    expected_error: StableErrorCode | None,
) -> None:
    api = tools_api()
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    observations = tuple(
        observation(
            api,
        )
        for _ in range(row_count)
    )
    batch = batch_for(
        api,
        "MetricsObservationBatch",
        EvidenceSource.METRICS,
        observations=observations,
    )
    backend = FakeBackend({"query_metrics": batch})
    tool_context = context(api, backend)

    result = query_metrics(
        tool_context,
        MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status.value == expected_status
    assert result.error_code is expected_error
    assert tool_context.budget.remaining_tool_calls == 3
    assert len(backend.calls) == 1
    if row_count == 64:
        assert len(result.evidence_refs) == 64
        assert len(tool_context.evidence_store.snapshot()) == 64
        assert result.evidence_refs[-1].endswith("/metrics/0064")
    else:
        assert result.evidence_refs == ()
        assert tool_context.evidence_store.snapshot() == ()


def test_malformed_observation_batch_cannot_partially_persist() -> None:
    api = tools_api()
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    bad_row = api.BackendObservation.model_construct(
        service="checkoutservice",
        started_at=UTC_START,
        ended_at=UTC_END,
        observation_type="x" * 129,
        attributes=(),
        limitations=(),
    )
    batch = api.MetricsObservationBatch.model_construct(
        source=EvidenceSource.METRICS,
        status=api.BackendStatus.AVAILABLE,
        observations=(observation(api), bad_row),
        raw_artifact_indices=(0, 1),
        raw_artifact_filename="metrics.json",
        raw_artifact_sha256=SHA256,
    )
    backend = FakeBackend({"query_metrics": batch})
    tool_context = context(api, backend)

    result = query_metrics(
        tool_context,
        MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.MALFORMED_REPLAY_ARTIFACT
    assert result.evidence_refs == ()
    assert tool_context.evidence_store.snapshot() == ()


@pytest.mark.parametrize("tamper_target", ("batch", "observation"))
def test_hidden_backend_storage_cannot_be_washed_by_tool_revalidation(
    tamper_target: str,
) -> None:
    api = tools_api()
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    row = observation(api)
    batch = batch_for(
        api,
        "MetricsObservationBatch",
        EvidenceSource.METRICS,
        observations=(row,),
    )
    if tamper_target == "batch":
        batch.__dict__["backend_name"] = "hidden-live-backend"
    else:
        row.__dict__["evaluator_truth"] = "paymentservice"
    backend = FakeBackend({"query_metrics": batch})
    tool_context = context(api, backend)

    result = query_metrics(
        tool_context,
        MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.MALFORMED_REPLAY_ARTIFACT
    assert result.evidence_refs == ()
    assert tool_context.budget.remaining_tool_calls == 3
    assert len(backend.calls) == 1
    assert tool_context.evidence_store.snapshot() == ()


def test_wrong_backend_batch_type_is_internal_contract_violation() -> None:
    api = tools_api()
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    wrong_batch = batch_for(
        api,
        "LogsObservationBatch",
        EvidenceSource.LOGS,
    )
    backend = FakeBackend({"query_metrics": wrong_batch})
    tool_context = context(api, backend)

    result = query_metrics(
        tool_context,
        MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
    )

    assert result.status is api.ToolStatus.ERROR
    assert result.error_code is StableErrorCode.INTERNAL_CONTRACT_VIOLATION
    assert result.evidence_refs == ()
    assert tool_context.evidence_store.snapshot() == ()


@pytest.mark.parametrize("timeout", (0, -1, float("inf"), float("nan")))
def test_tool_context_requires_finite_positive_timeout(timeout: float) -> None:
    api = tools_api()
    backend = FakeBackend({})

    with pytest.raises((TypeError, ValueError)):
        api.ToolContext(
            incident=incident(),
            evidence_store=EvidenceStore(RUN_ID),
            budget=RunBudget(
                BudgetLimits(
                    max_model_calls=0,
                    max_tool_calls=1,
                    max_total_tokens=0,
                )
            ),
            backend=backend,
            timeout_seconds=timeout,
        )


def test_tool_result_contract_rejects_inconsistent_status_and_refs() -> None:
    api = tools_api()
    from ecomsre.tools.metrics import MetricsResult

    with pytest.raises(ValidationError):
        MetricsResult(
            schema_version="phase1.metrics-result.v1",
            tool_name="query_metrics",
            status=api.ToolStatus.OK,
            evidence_refs=(),
            budget_consumed=True,
            dispatched=True,
            error_code=StableErrorCode.TIMEOUT,
        )
    with pytest.raises(ValidationError):
        MetricsResult(
            schema_version="phase1.metrics-result.v1",
            tool_name="query_metrics",
            status=api.ToolStatus.ERROR,
            evidence_refs=(f"evidence://{RUN_ID}/metrics/0001",),
            budget_consumed=True,
            dispatched=True,
            error_code=StableErrorCode.TIMEOUT,
        )
