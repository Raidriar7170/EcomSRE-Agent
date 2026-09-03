"""Product-only corroboration; frozen thresholds and memory-leak clauses remain."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.tool_contracts import build_fake_read_authority
from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.memory import BaselineProfileV22, build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.known_admission import build_known_admission_state_v23
from ecomsre.dta_v2.v23.ontology_view import build_active_ontology_view_v23
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.incidents.anomaly_policy import extract_product_anomalies_v1
from ecomsre.product.incidents.diagnosis_bridge import _effective_admissions_v024
from ecomsre.product.incidents.read_backend import _build_outcome, _runtime_memory


NOW = datetime(2026, 9, 3, tzinfo=UTC)
SERVICES = ("checkout", "payment")
BASELINE = BaselineProfileV22.build(
    metric_stats=tuple(
        (service, MetricKindV22.ERROR_RATE, 0.0, 0.0) for service in SERVICES
    ),
    trace_stats=(),
    resource_stats=tuple((service, 10.0, 0.0) for service in SERVICES),
)


def resource(service="checkout", start=10_000_000):
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=index * 2500,
                cpu_percent=5.0,
                memory_bytes=start + index * 5_000_000,
            )
            for index in range(5)
        ),
        memory_slope_bytes_per_second=2_000_000.0,
    )


def outcome(action_id, source, records):
    payload = dict(
        schema_version="dta-v22.read-outcome.v1",
        action_id=action_id,
        source=source,
        request_sha256=semantic_sha256_v22({"action_id": action_id}),
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        records=records,
        truncated=False,
    )
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def snapshot(read, offset=0):
    window = ConnectorWindowV1(
        started_at=NOW + timedelta(seconds=offset),
        ended_at=NOW + timedelta(seconds=offset + 10),
    )
    services = tuple(sorted({r.service for r in read.records}))
    result = ConnectorQueryResultV1.build(
        source=read.source,
        status=read.status,
        requested_services=services,
        covered_services=services,
        records=read.records,
        window=window,
        truncated=False,
        safe_error_code=None,
        latency_ms=0.0,
    )
    return {
        "read_outcome": read.model_dump(mode="json"),
        "connector_result": result.model_dump(mode="json"),
    }


def corroboration(kind, service):
    if kind in {"restart", "unhealthy"}:
        catalog = build_action_catalog_v22(
            candidate_services=(service,),
            topology=StaticTopologyV22.build(services=(service,), edges=()),
            capability_registry=build_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=100.0,
        )
        action = next(
            a for a in catalog.registry_actions if a.source is EvidenceSourceV22.RUNTIME
        )
        result = ConnectorQueryResultV1.build(
            source=action.source,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=(service,),
            covered_services=(service,),
            window=ConnectorWindowV1(
                started_at=NOW, ended_at=NOW + timedelta(seconds=10)
            ),
            records=(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service=service,
                    state=RuntimeStateV22.RUNNING,
                    healthy=kind != "unhealthy",
                    restart_count=2 if kind == "restart" else 0,
                ),
            ),
            truncated=False,
            safe_error_code=None,
            latency_ms=0.0,
        )
        return _runtime_memory(
            incident=SimpleNamespace(incident_sha256="0" * 64),
            action=action,
            outcome=_build_outcome(action, result),
            window=result.window,
            latency_ms=0.0,
            authority=build_fake_read_authority(),
        )
    if kind in {"memory_log", "other_log"}:
        source = EvidenceSourceV22.LOGS
        record = LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            service=service,
            observed_at=NOW,
            severity="ERROR",
            message="out of memory" if kind == "memory_log" else "configuration failed",
        )
    elif kind == "metric_error":
        source = EvidenceSourceV22.METRICS
        record = MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service=service,
            metric_kind=MetricKindV22.ERROR_RATE,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=5,
            value=0.5,
            unit=MetricUnitV22.RATIO,
            window_started_at=NOW,
            window_ended_at=NOW + timedelta(seconds=10),
        )
    else:
        source = EvidenceSourceV22.TRACES
        record = TraceSpanV22(
            schema_version="dta-v22.trace-span.v1",
            observed_at=NOW,
            service=service,
            operation="work",
            parent_service=None,
            duration_ms=10.0,
            status=SpanStatusV22.ERROR,
            service_path=(service,),
            first_error_location=True,
        )
    return outcome(f"a:{source.value.lower()}:{service}", source, (record,))


def extract(outcomes, snapshots=()):
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=BASELINE,
        observed_at=NOW + timedelta(seconds=40),
        top_k=64,
    )
    original = memory.model_dump(mode="json")
    anomalies = extract_product_anomalies_v1(
        memory=memory, candidate_services=SERVICES, snapshots=snapshots
    )
    assert memory.model_dump(mode="json") == original
    return memory, anomalies


@pytest.mark.parametrize(
    "kind", ["memory_log", "restart", "unhealthy", "metric_error", "trace_error"]
)
@pytest.mark.parametrize("service", SERVICES)
def test_only_same_service_allowed_corroboration_retains_memory(kind, service):
    memory, anomalies = extract(
        (
            outcome("a:resources:checkout", EvidenceSourceV22.RESOURCES, (resource(),)),
            corroboration(kind, service),
        )
    )
    retained = any(
        a.kind is GenericAnomalyKindV23.RESOURCE_MEMORY_TREND for a in anomalies
    )
    assert retained is (service == "checkout")
    if retained and kind in {"memory_log", "restart"}:
        admission = build_known_admission_state_v23(
            view=build_active_ontology_view_v23(candidate_services=SERVICES),
            memory=memory,
            topology_edges=(),
            evidence_source_unavailable=False,
        )
        accepted = _effective_admissions_v024(
            admission=admission, memory=memory, anomalies=anomalies
        )
        assert any(
            a.mechanism.value == "MEMORY_LEAK" and a.root_service == "checkout"
            for a in accepted
        )


def test_unrelated_error_log_does_not_corroborate_memory():
    _, anomalies = extract(
        (
            outcome("a:resources:checkout", EvidenceSourceV22.RESOURCES, (resource(),)),
            corroboration("other_log", "checkout"),
        )
    )
    assert not any(
        a.kind is GenericAnomalyKindV23.RESOURCE_MEMORY_TREND for a in anomalies
    )


@pytest.mark.parametrize(
    "offset,duplicate,include_windows,expected",
    [
        (20, False, True, True),
        (0, False, True, False),
        (5, False, True, False),
        (20, True, True, False),
        (20, False, False, False),
        (40, False, True, False),
    ],
)
def test_second_resource_window_requires_independent_bound_observations(
    offset, duplicate, include_windows, expected
):
    first = outcome(
        "a:resources:checkout:first", EvidenceSourceV22.RESOURCES, (resource(),)
    )
    second = outcome(
        "a:resources:checkout:second",
        EvidenceSourceV22.RESOURCES,
        (resource(start=10_000_000 if duplicate else 30_000_000),),
    )
    _, anomalies = extract(
        (first, second),
        (snapshot(first), snapshot(second, offset)) if include_windows else (),
    )
    assert (
        any(a.kind is GenericAnomalyKindV23.RESOURCE_MEMORY_TREND for a in anomalies)
        is expected
    )
