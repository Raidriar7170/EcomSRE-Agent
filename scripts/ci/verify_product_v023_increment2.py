#!/usr/bin/env python3
"""Verify the Product v0.2.3 strict Baseline preflight checkpoint."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.product.baselines import (
    BaselineBuildModeV1,
    BaselineBuildPolicyV1,
    build_environment_baseline,
)
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
)
from ecomsre.product.contracts import (
    ConnectorKindV1,
    ServiceIdentityMapV1,
    ServiceIdentityV1,
)
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineConnectorBindingV021,
    BaselineConnectorExpectationV021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    BASELINE_PREFLIGHT_PASS_V023,
    OpenSearchWindowDiagnosticsV023,
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessProfileV023,
    PrometheusTemplateDiagnosticV023,
    PrometheusWindowDiagnosticsV023,
    evaluate_baseline_windows_v023,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NOFAULT_CAPABILITY_LIMITED_V023,
    NOFAULT_FULLY_SUPPORTED_V023,
    NOFAULT_NOT_SUPPORTED_V023,
    NoFaultExecutionProfileV023,
)
from ecomsre.product.storage.schema import SCHEMA_VERSION
from scripts.ci.verify_product_v023_increment1 import verify_product_v023_increment1


NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3 Increment 2 artifact must be an object")
    return payload


def _window(index: int) -> ConnectorWindowV1:
    started = NOW + timedelta(seconds=index * 36)
    return ConnectorWindowV1(
        started_at=started,
        ended_at=started + timedelta(seconds=36),
    )


def _result(
    source: EvidenceSourceV22,
    window: ConnectorWindowV1,
    records: tuple[Any, ...] = (),
) -> ConnectorQueryResultV1:
    return ConnectorQueryResultV1.build(
        source=source,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        requested_services=("checkout",),
        covered_services=("checkout",) if records else (),
        window=window,
        records=records,
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )


def _fixture_window(index: int):
    window = _window(index)
    metrics = tuple(
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service="checkout",
            metric_kind=kind,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=4,
            value=value,
            unit=METRIC_UNIT_BY_KIND_V22[kind],
            window_started_at=window.started_at,
            window_ended_at=window.ended_at,
        )
        for kind, value in (
            (MetricKindV22.REQUEST_SUPPORT, 20.0),
            (MetricKindV22.ERROR_RATE, 0.0),
            (MetricKindV22.LATENCY_P95_MS, 30.0 + index),
        )
    )
    logs = tuple(
        LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=window.started_at + timedelta(seconds=offset + 1),
            service="checkout",
            severity="DIAGNOSTIC",
            message=f"healthy checkout request {index}-{offset}",
        )
        for offset in range(3)
    )
    results = (
        _result(EvidenceSourceV22.METRICS, window, metrics),
        _result(EvidenceSourceV22.RESOURCES, window),
        _result(EvidenceSourceV22.LOGS, window, logs),
        _result(EvidenceSourceV22.TRACES, window),
    )
    prom = PrometheusWindowDiagnosticsV023.build(
        window=window,
        metric_result_sha256=results[0].result_sha256,
        resource_result_sha256=results[1].result_sha256,
        templates=tuple(
            PrometheusTemplateDiagnosticV023.build(
                template_name=name,
                logical_service="checkout",
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                sample_count=4,
            )
            for name in ("error_rate", "latency", "request_support")
        ),
    )
    opensearch = OpenSearchWindowDiagnosticsV023.build(
        window=window,
        log_result_sha256=results[2].result_sha256,
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        query_status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        sampled_record_count=3,
        accepted_record_count=3,
        rejected_record_count=0,
        rejection_fraction=0.0,
        rejection_codes_by_count={},
    )
    bindings = (
        BaselineConnectorBindingV021(
            connector_name="prometheus", connector_kind=ConnectorKindV1.PROMETHEUS
        ),
        BaselineConnectorBindingV021(
            connector_name="prometheus", connector_kind=ConnectorKindV1.PROMETHEUS
        ),
        BaselineConnectorBindingV021(
            connector_name="opensearch", connector_kind=ConnectorKindV1.OPENSEARCH
        ),
        BaselineConnectorBindingV021(
            connector_name="jaeger", connector_kind=ConnectorKindV1.JAEGER
        ),
    )
    expectations = (
        BaselineConnectorExpectationV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
            expected_sources=(EvidenceSourceV22.METRICS, EvidenceSourceV22.RESOURCES),
        ),
        BaselineConnectorExpectationV021(
            connector_name="opensearch",
            connector_kind=ConnectorKindV1.OPENSEARCH,
            expected_sources=(EvidenceSourceV22.LOGS,),
        ),
        BaselineConnectorExpectationV021(
            connector_name="jaeger",
            connector_kind=ConnectorKindV1.JAEGER,
            expected_sources=(EvidenceSourceV22.TRACES,),
        ),
    )
    return results, prom, opensearch, bindings, expectations


def _checkpoint(root: Path) -> dict[str, object]:
    profile = ProductBaselineReadinessProfileV023.load(
        root / "config/product-v023/baseline-readiness/profile.json"
    )
    nofault = NoFaultExecutionProfileV023.load(
        root / "config/product-v023/nofault/profile.json"
    )
    rows = tuple(_fixture_window(index) for index in range(5))
    evaluation = evaluate_baseline_windows_v023(
        profile=profile,
        window_results=tuple(item[0] for item in rows),
        expected_windows=tuple(_window(index) for index in range(5)),
        connector_bindings=tuple(item[3] for item in rows),
        connector_expectations=tuple(item[4] for item in rows),
        prometheus_diagnostics=tuple(item[1] for item in rows),
        opensearch_diagnostics=tuple(item[2] for item in rows),
    )
    identity = ServiceIdentityMapV1.build(
        environment_id="env-" + "a" * 24,
        services=(
            ServiceIdentityV1(
                service_id="svc-" + "b" * 24,
                logical_service="checkout",
            ),
        ),
    )
    policy = BaselineBuildPolicyV1(
        mode=BaselineBuildModeV1.DEMO_ONLY,
        lookback_seconds=180,
        window_count=5,
        minimum_successful_windows=4,
        warmup_seconds=180,
    )
    baseline = build_environment_baseline(
        environment_id=identity.environment_id,
        identity_map=identity,
        source_capability_sha256="c" * 64,
        build_policy=policy,
        window_results=tuple(item[0] for item in rows),
        built_at=NOW + timedelta(minutes=10),
        baseline_id="base-" + "d" * 24,
        evaluation_v023=evaluation,
    )
    audit = ProductBaselineReadinessAuditV023.build(
        environment_id=identity.environment_id,
        baseline_id=baseline.baseline_id,
        baseline_sha256=baseline.baseline_sha256,
        service_ids=("checkout",),
        baseline_entity_service_ids=baseline.service_ids,
        build_policy=policy.model_dump(mode="json"),
        service_identity_sha256=identity.identity_sha256,
        capability_sha256="c" * 64,
        evaluation=evaluation,
    )
    return {
        "terminal": evaluation.terminal,
        "baseline_readiness_profile_sha256": profile.profile_sha256,
        "nofault_profile_sha256": nofault.profile_sha256,
        "active_profile_sha256": evaluation.active_opensearch_profile_sha256,
        "scheduled_window_count": len(evaluation.windows),
        "accepted_window_count": len(evaluation.accepted_ordinals),
        "logs_nonempty_window_count": evaluation.logs_nonempty_window_count,
        "accepted_checkout_log_record_count": (
            evaluation.accepted_checkout_log_record_count
        ),
        "normal_checkout_log_template": (
            evaluation.has_normal_checkout_log_template
        ),
        "window_evaluation_parity_sha256": evaluation.parity_sha256,
        "fixture_baseline_id": baseline.baseline_id,
        "fixture_baseline_sha256": baseline.baseline_sha256,
        "fixture_baseline_audit_sha256": audit.audit_sha256,
        "fixture_baseline_successful_windows": baseline.successful_windows,
        "fixture_baseline_metric_stat_count": len(
            baseline.v22_baseline_profile.metric_stats
        ),
        "fixture_baseline_normal_log_template_count": len(
            baseline.normal_log_templates
        ),
        "sqlite_schema_version": SCHEMA_VERSION,
        "attempt_budget": 2,
        "measured_nofault_terminals": [
            NOFAULT_FULLY_SUPPORTED_V023,
            NOFAULT_CAPABILITY_LIMITED_V023,
            NOFAULT_NOT_SUPPORTED_V023,
        ],
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }


def verify_product_v023_increment2(root: Path) -> dict[str, object]:
    repository = Path(root).resolve(strict=True)
    increment1 = verify_product_v023_increment1(repository)
    expected = _checkpoint(repository)
    report = _load_object(
        repository / "docs/analysis/product-v023-baseline-preflight.json"
    )
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if (
        increment1["status"] != "ECOMSRE_PRODUCT_V023_PROFILE_BINDING_PASS"
        or report.get("schema_version")
        != "ecomsre.product.v023.baseline-preflight-report.v1"
        or report.get("goal_version")
        != "ecomsre-product-v023-fresh-baseline-nofault-v1"
        or report.get("starting_main")
        != "613f6203e4a174b4549b912cb16ca7998cf6238c"
        or any(report.get(key) != value for key, value in expected.items())
        or report.get("report_sha256") != semantic_sha256_v22(body)
    ):
        raise ValueError("Product v0.2.3 Increment 2 baseline preflight differs")
    return {"status": BASELINE_PREFLIGHT_PASS_V023, **expected}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v023_increment2(args.project_root),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v023_increment2",)
