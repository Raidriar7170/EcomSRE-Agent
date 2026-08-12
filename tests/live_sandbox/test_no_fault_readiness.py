from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ecomsre_live_sandbox.e2e_source_batch import OrderedSourceBatch
from ecomsre_live_sandbox.instrumentation_v2 import (
    SourceProbeResult,
    SourceProbeStatus,
)
from ecomsre_live_sandbox.no_fault_readiness import evaluate_no_fault_readiness


NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def _source(source: str, count: int) -> SourceProbeResult:
    backend = {
        "METRICS": "PROMETHEUS_HTTP_API",
        "LOGS": "OPENSEARCH_HTTP_API",
        "TRACES": "JAEGER_QUERY_API",
    }[source]
    return SourceProbeResult(
        source=source,
        backend_kind=backend,
        status=SourceProbeStatus.AVAILABLE,
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        probe_started_at=NOW,
        probe_ended_at=NOW + timedelta(seconds=1),
        attempt_count=1,
        backend_reachable=True,
        raw_response_count=count,
        parsed_record_count=count,
        target_record_count=count,
        service_catalog_count=25,
        target_service_present=True,
        evidence_refs=tuple(f"{source.casefold()}:{index:04d}" for index in range(1, count + 1)),
    )


def _batch() -> OrderedSourceBatch:
    results = (_source("METRICS", 5), _source("LOGS", 28), _source("TRACES", 14))
    return OrderedSourceBatch(
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        source_results=results,
        source_counts={item.source: item.target_record_count for item in results},
        invalid_ref_count=0,
        all_refs_resolve=True,
        combined_resolver_sha256="a" * 64,
        source_results_sha256="b" * 64,
    )


def test_healthy_no_fault_readiness_passes_without_anomaly_evidence() -> None:
    readiness = evaluate_no_fault_readiness(
        run_id="probe-01",
        source_batch=_batch(),
        services_healthy_count=25,
        baseline_exact=True,
        broad_metric_service_count=4,
        logs_query_contract_completed=True,
        traces_query_contract_completed=True,
        private_permissions_valid=True,
        control_truth_findings=(),
    )

    assert readiness.passed is True
    assert readiness.reason_codes == ()
    assert readiness.source_statuses == {
        "METRICS": "AVAILABLE",
        "LOGS": "AVAILABLE",
        "TRACES": "AVAILABLE",
    }
    assert readiness.source_counts == {"METRICS": 5, "LOGS": 28, "TRACES": 14}
    assert readiness.semantic_sha256


def test_no_fault_readiness_reports_broad_metric_failure_without_diagnosis() -> None:
    readiness = evaluate_no_fault_readiness(
        run_id="probe-01",
        source_batch=_batch(),
        services_healthy_count=25,
        baseline_exact=True,
        broad_metric_service_count=2,
        logs_query_contract_completed=True,
        traces_query_contract_completed=True,
        private_permissions_valid=True,
        control_truth_findings=(),
    )

    assert readiness.passed is False
    assert readiness.reason_codes == ("BROAD_METRIC_SERVICE_COUNT_BELOW_MINIMUM",)
