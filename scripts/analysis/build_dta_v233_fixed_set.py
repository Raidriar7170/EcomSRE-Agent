#!/usr/bin/env python3
"""Build fresh, opaque, recipe-derived DTA v2.3.3 fixed evaluation bytes."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    EvaluationCaseSetV233,
    EvaluationOntologyViewSetV233,
    EvaluationStrataV233,
    EvaluationTruthSetV233,
)


_SALT = "dta-v233-domain-witness-final-a-c69e314d"


_RECIPES: tuple[tuple[str, str | None, str | None], ...] = (
    ("HIDDEN_CONFIGURATION", "CONFIGURATION_ERROR", "CONFIGURATION"),
    ("HIDDEN_CONFIGURATION", "CONFIGURATION_ERROR", "CONFIGURATION"),
    ("HIDDEN_DEPENDENCY_LATENCY", "DEPENDENCY_LATENCY", "DEPENDENCY"),
    ("HIDDEN_DEPENDENCY_LATENCY", "DEPENDENCY_LATENCY", "DEPENDENCY"),
    ("HIDDEN_RUNTIME", "SERVICE_UNAVAILABLE", "RUNTIME"),
    ("HIDDEN_RUNTIME", "SERVICE_UNAVAILABLE", "RUNTIME"),
    ("HIDDEN_CPU", "CPU_SATURATION", "RESOURCE"),
    ("HIDDEN_CPU", "CPU_SATURATION", "RESOURCE"),
    ("HIDDEN_MEMORY", "MEMORY_LEAK", "RESOURCE"),
    ("HIDDEN_MEMORY", "MEMORY_LEAK", "RESOURCE"),
    ("UNREGISTERED_CONCURRENCY", None, "CONCURRENCY"),
    ("UNREGISTERED_CONCURRENCY", None, "CONCURRENCY"),
    ("UNREGISTERED_QUEUE_BACKLOG", None, "CONCURRENCY"),
    ("UNREGISTERED_QUEUE_BACKLOG", None, "CONCURRENCY"),
    ("UNREGISTERED_NETWORK_EXTERNAL", None, "NETWORK"),
    ("UNREGISTERED_NETWORK_EXTERNAL", None, "EXTERNAL"),
    ("REGISTERED_KNOWN", "CONFIGURATION_ERROR", "CONFIGURATION"),
    ("REGISTERED_KNOWN", "DEPENDENCY_LATENCY", "DEPENDENCY"),
    ("REGISTERED_KNOWN", "SERVICE_UNAVAILABLE", "RUNTIME"),
    ("REGISTERED_KNOWN", "CPU_SATURATION", "RESOURCE"),
    ("NO_INCIDENT", None, None),
    ("NO_INCIDENT", None, None),
    ("NO_INCIDENT", None, None),
    ("IRRECONCILABLE_CONTROL", None, None),
    ("IRRECONCILABLE_CONTROL", None, None),
    ("IRRECONCILABLE_CONTROL", None, None),
    ("IRRECONCILABLE_CONTROL", None, None),
    ("INSUFFICIENT_EVIDENCE", None, None),
)


def _opaque(prefix: str, *parts: object, length: int) -> str:
    raw = ":".join((_SALT, prefix, *(str(item) for item in parts)))
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def _services(case_id: str) -> tuple[str, str]:
    return tuple(
        sorted(
            (
                f"svc-{_opaque('service', case_id, 'a', length=10)}",
                f"svc-{_opaque('service', case_id, 'b', length=10)}",
            )
        )
    )  # type: ignore[return-value]


def _metric(
    *,
    service: str,
    kind: str,
    value: float,
    unit: str,
    captured_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": "dta-v22.metric-fact.v1",
        "service": service,
        "metric_kind": kind,
        "support_status": "SUPPORTED",
        "sample_count": 73,
        "value": value,
        "unit": unit,
        "window_started_at": (captured_at - timedelta(seconds=300)).isoformat().replace(
            "+00:00", "Z"
        ),
        "window_ended_at": captured_at.isoformat().replace("+00:00", "Z"),
    }


def _resource(
    *,
    service: str,
    cpu: float,
    memory_growth: bool,
) -> dict[str, Any]:
    offsets = (0, 2500, 5000, 7500, 10000)
    memory = (
        tuple(300_000_000 + index * 45_000_000 for index in range(5))
        if memory_growth
        else (300_000_000,) * 5
    )
    return {
        "schema_version": "dta-v22.resource-usage-record.v1",
        "service": service,
        "sampling_window_seconds": 10,
        "samples": [
            {
                "offset_ms": offset,
                "cpu_percent": cpu + index * 0.25,
                "memory_bytes": memory[index],
            }
            for index, offset in enumerate(offsets)
        ],
        "memory_slope_bytes_per_second": 18_000_000.0 if memory_growth else 0.0,
    }


def _trace(
    *,
    captured_at: datetime,
    parent: str,
    service: str,
    operation: str,
    duration_ms: float,
    first_error: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "dta-v22.trace-span.v1",
        "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
        "service_path": [parent, service],
        "service": service,
        "parent_service": parent,
        "operation": operation,
        "status": "ERROR" if first_error else "OK",
        "duration_ms": duration_ms,
        "first_error_location": first_error,
    }


def _capture(
    *,
    ordinal: int,
    stratum: str,
    mechanism: str | None,
    domain: str | None,
    services: tuple[str, str],
    target: str,
) -> ReplayCaptureV22:
    captured_at = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc) + timedelta(
        minutes=ordinal
    )
    peer = next(item for item in services if item != target)
    error = {service: 0.01 for service in services}
    latency = {service: 10.0 for service in services}
    if stratum == "IRRECONCILABLE_CONTROL":
        error = {service: 0.38 + index * 0.02 for index, service in enumerate(services)}
    elif stratum not in {"NO_INCIDENT", "INSUFFICIENT_EVIDENCE"} and not (
        stratum == "REGISTERED_KNOWN" and mechanism == "CPU_SATURATION"
    ):
        error[target] = 0.31
    if domain in {"DEPENDENCY", "CONCURRENCY", "NETWORK", "EXTERNAL"} or (
        stratum.startswith("HIDDEN_")
    ):
        latency[target] = 118.0
    metrics = [
        item
        for service in services
        for item in (
            _metric(
                service=service,
                kind="ERROR_RATE",
                value=error[service],
                unit="RATIO",
                captured_at=captured_at,
            ),
            _metric(
                service=service,
                kind="LATENCY_P95_MS",
                value=latency[service],
                unit="MILLISECONDS",
                captured_at=captured_at,
            ),
            _metric(
                service=service,
                kind="REQUEST_SUPPORT",
                value=137.0,
                unit="COUNT",
                captured_at=captured_at,
            ),
        )
    ]
    if stratum == "REGISTERED_KNOWN" and mechanism == "CPU_SATURATION":
        metrics = [
            item for item in metrics if item["metric_kind"] != "REQUEST_SUPPORT"
        ]
    logs: list[dict[str, Any]] = []
    if mechanism == "CONFIGURATION_ERROR":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": "configuration parser rejected opaque setting value",
            }
        )
    elif mechanism == "DEPENDENCY_LATENCY":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": f"dependency timeout downstream={peer}",
            }
        )
    elif mechanism == "MEMORY_LEAK":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": "memory pressure increased steadily in local process",
            }
        )
    elif stratum == "UNREGISTERED_CONCURRENCY":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": "worker pool semaphore wait observed under load",
            }
        )
    elif stratum == "UNREGISTERED_QUEUE_BACKLOG":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": "queue backlog throttle worker wait observed",
            }
        )
    elif domain == "NETWORK":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": "connection reset during socket read",
            }
        )
    elif domain == "EXTERNAL":
        logs.append(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": target,
                "severity": "ERROR",
                "message": "upstream external returned http 429 rate limit",
            }
        )
    elif stratum == "IRRECONCILABLE_CONTROL":
        logs.extend(
            {
                "schema_version": "dta-v22.log-record.v1",
                "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
                "service": service,
                "severity": "ERROR",
                "message": "opaque mutually exclusive origin remains unresolved",
            }
            for service in services
        )

    traces: list[dict[str, Any]] = []
    if mechanism == "DEPENDENCY_LATENCY":
        traces.append(
            _trace(
                captured_at=captured_at,
                parent=peer,
                service=target,
                operation=f"opaque-fetch-{ordinal}",
                duration_ms=132.0,
                first_error=True,
            )
        )
    elif stratum == "IRRECONCILABLE_CONTROL":
        left, right = services
        traces.extend(
            (
                _trace(
                    captured_at=captured_at,
                    parent=left,
                    service=right,
                    operation=f"opaque-alpha-{ordinal}",
                    duration_ms=91.0,
                    first_error=True,
                ),
                _trace(
                    captured_at=captured_at,
                    parent=right,
                    service=left,
                    operation=f"opaque-beta-{ordinal}",
                    duration_ms=93.0,
                    first_error=True,
                ),
            )
        )

    runtime = [
        {
            "schema_version": "dta-v22.runtime-record.v1",
            "service": service,
            "state": (
                "ABSENT"
                if mechanism == "SERVICE_UNAVAILABLE" and service == target
                else "RUNNING"
            ),
            "healthy": not (
                mechanism == "SERVICE_UNAVAILABLE" and service == target
            ),
            "restart_count": 0,
        }
        for service in services
    ]
    resources = [
        _resource(
            service=service,
            cpu=(92.0 if mechanism == "CPU_SATURATION" and service == target else 20.0),
            memory_growth=(mechanism == "MEMORY_LEAK" and service == target),
        )
        for service in services
    ]
    changes: list[dict[str, Any]] = []
    if mechanism == "CONFIGURATION_ERROR":
        changes.append(
            {
                "schema_version": "dta-v22.recent-change-record.v1",
                "opaque_change_id": f"chg_{_opaque('change', ordinal, length=16)}",
                "service": target,
                "observed_at": (captured_at - timedelta(seconds=90)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "category": "CONFIGURATION",
                "rollout_state": "COMPLETED",
                "revision_digest": _opaque("revision", ordinal, length=64),
            }
        )
    source_failures = (
        [
            {
                "schema_version": "dta-v22.replay-source-failure.v1",
                "source": "METRICS",
                "status": "FAILURE_UNAVAILABLE",
            }
        ]
        if stratum == "INSUFFICIENT_EVIDENCE"
        else []
    )
    raw = {
        "schema_version": "dta-v22.replay-capture.v1",
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "logs": logs,
        "traces": traces,
        "runtime": runtime,
        "resources": resources,
        "changes": changes,
        "source_failures": source_failures,
    }
    return ReplayCaptureV22.model_validate_json(json.dumps(raw))


def build() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    truths: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    strata: dict[str, list[str]] = {}
    for index, (stratum, mechanism, domain) in enumerate(_RECIPES):
        ordinal = 301 + index
        case_id = f"vx-{ordinal:03d}"
        services = _services(case_id)
        target = services[index % 2]
        capture = _capture(
            ordinal=ordinal,
            stratum=stratum,
            mechanism=mechanism,
            domain=domain,
            services=services,
            target=target,
        )
        observer = {
            "case_id": case_id,
            "candidate_services": services,
            "topology_edges": ((services[0], services[1]),),
            "capture": capture.model_dump(mode="json"),
        }
        cases.append(
            {
                **observer,
                "source_bytes_sha256": semantic_sha256_v22(observer),
            }
        )
        novelty = index < 16
        known = stratum == "REGISTERED_KNOWN"
        evaluation_class = (
            "NOVELTY"
            if novelty
            else stratum
        )
        expected_terminal = (
            "UNREGISTERED_INCIDENT_SUSPECTED"
            if novelty
            else "REGISTERED_KNOWN"
            if known
            else "NO_INCIDENT"
            if stratum == "NO_INCIDENT"
            else "CONFLICTING_EVIDENCE"
            if stratum == "IRRECONCILABLE_CONTROL"
            else "INSUFFICIENT_EVIDENCE"
        )
        pair_id = f"v233-cf-{index // 2 + 1:02d}" if novelty else None
        truths.append(
            {
                "case_id": case_id,
                "stratum": stratum,
                "evaluation_class": evaluation_class,
                "expected_terminal": expected_terminal,
                "expected_root_service": target if novelty or known else None,
                "expected_broad_domain": domain if novelty or known else None,
                "expected_known_mechanism": mechanism if known else None,
                "hidden_mechanism": mechanism if novelty else None,
                "counterfactual_pair_id": pair_id,
                "counterfactual_target_role": (
                    "TARGET_LOW" if target == min(services) else "TARGET_HIGH"
                )
                if novelty
                else None,
            }
        )
        views.append(
            {
                "case_id": case_id,
                "hidden_mechanism": mechanism if novelty else None,
            }
        )
        strata.setdefault(stratum, []).append(case_id)

    cases_payload = {
        "schema_version": "dta-v233.evaluation-case-set.v1",
        "freeze_id": "dta-v233-domain-witness-freeze-20260826-a",
        "cases": cases,
    }
    truth_payload_without_sha = {
        "schema_version": "dta-v233.evaluation-truth-set.v1",
        "truths": truths,
    }
    truth_payload = {
        **truth_payload_without_sha,
        "truth_sha256": semantic_sha256_v22(truth_payload_without_sha),
    }
    views_payload = {
        "schema_version": "dta-v233.ontology-view-set.v1",
        "views": views,
    }
    strata_payload = {
        "schema_version": "dta-v233.evaluation-strata.v1",
        "strata": [
            {"name": name, "case_ids": ids}
            for name, ids in sorted(strata.items())
        ],
    }
    EvaluationCaseSetV233.model_validate_json(json.dumps(cases_payload))
    EvaluationTruthSetV233.model_validate_json(json.dumps(truth_payload))
    EvaluationOntologyViewSetV233.model_validate_json(json.dumps(views_payload))
    EvaluationStrataV233.model_validate_json(json.dumps(strata_payload))
    return cases_payload, truth_payload, views_payload, strata_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    cases, truth, views, strata = build()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("cases.json", cases),
        ("truth.json", truth),
        ("ontology-views.json", views),
        ("strata.json", strata),
    ):
        (args.output_root / name).write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
