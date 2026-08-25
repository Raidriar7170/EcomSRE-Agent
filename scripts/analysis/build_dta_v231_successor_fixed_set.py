#!/usr/bin/env python3
"""Build independent observer bytes for the DTA v2.3.1 successor study."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    RecentChangeRecordV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RolloutStateV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22


_SALT = "dta-v231-successor-independent-set-a-4fb7a15d"
_CAPTURE_START = datetime(2026, 8, 25, 16, 0, tzinfo=timezone.utc)


def _service(case_id: str, slot: str) -> str:
    digest = hashlib.sha256(f"{_SALT}:{case_id}:{slot}".encode()).hexdigest()
    return f"svc-{digest[:10]}"


def _metric(
    service: str,
    kind: MetricKindV22,
    value: float,
    captured_at: datetime,
) -> MetricFactV22:
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service=service,
        metric_kind=kind,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=60,
        value=value,
        unit=METRIC_UNIT_BY_KIND_V22[kind],
        window_started_at=captured_at - timedelta(minutes=5),
        window_ended_at=captured_at,
    )


def _metrics(
    services: tuple[str, str],
    captured_at: datetime,
    *,
    error_services: tuple[str, ...] = (),
    latency_services: tuple[str, ...] = (),
) -> tuple[MetricFactV22, ...]:
    return tuple(
        _metric(service, kind, value, captured_at)
        for service in services
        for kind, value in (
            (MetricKindV22.ERROR_RATE, 0.34 if service in error_services else 0.01),
            (
                MetricKindV22.LATENCY_P95_MS,
                84.0 if service in latency_services else 10.0,
            ),
            (MetricKindV22.REQUEST_SUPPORT, 120.0),
        )
    )


def _runtime(
    services: tuple[str, str],
    *,
    unavailable_service: str | None = None,
) -> tuple[RuntimeRecordV22, ...]:
    return tuple(
        RuntimeRecordV22(
            schema_version="dta-v22.runtime-record.v1",
            service=service,
            state=(
                RuntimeStateV22.ABSENT
                if service == unavailable_service
                else RuntimeStateV22.RUNNING
            ),
            healthy=service != unavailable_service,
            restart_count=0,
        )
        for service in services
    )


def _resource(
    service: str,
    *,
    cpu_high: bool = False,
    memory_growth: bool = False,
) -> ResourceUsageRecordV22:
    start_memory = 256_000_000
    end_memory = start_memory + (90_000_000 if memory_growth else 0)
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=(94.0 + index / 2 if cpu_high else 20.0 + index / 4),
                memory_bytes=(
                    start_memory
                    + ((end_memory - start_memory) * index // 4)
                ),
            )
            for index, offset in enumerate((0, 2500, 5000, 7500, 10000))
        ),
        memory_slope_bytes_per_second=(9_000_000.0 if memory_growth else 0.0),
    )


def _log(service: str, captured_at: datetime, message: str) -> LogRecordV22:
    return LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=captured_at,
        service=service,
        severity="ERROR",
        message=message,
    )


def _trace(
    parent: str,
    service: str,
    captured_at: datetime,
    operation: str,
    *,
    first_error: bool,
    duration_ms: float,
) -> TraceSpanV22:
    return TraceSpanV22(
        schema_version="dta-v22.trace-span.v1",
        observed_at=captured_at,
        service_path=(parent, service),
        service=service,
        parent_service=parent,
        operation=operation,
        status=SpanStatusV22.ERROR if first_error else SpanStatusV22.OK,
        duration_ms=duration_ms,
        first_error_location=first_error,
    )


def _change(service: str, captured_at: datetime, ordinal: int) -> RecentChangeRecordV22:
    identity = hashlib.sha256(f"{_SALT}:change:{ordinal}".encode()).hexdigest()
    return RecentChangeRecordV22(
        schema_version="dta-v22.recent-change-record.v1",
        opaque_change_id=f"chg_{identity[:16]}",
        service=service,
        observed_at=captured_at - timedelta(minutes=2),
        category=ChangeCategoryV22.CONFIGURATION,
        rollout_state=RolloutStateV22.COMPLETED,
        revision_digest=hashlib.sha256(
            f"{_SALT}:revision:{ordinal}".encode()
        ).hexdigest(),
    )


def _design(ordinal: int) -> dict[str, Any]:
    case_id = f"vx-{ordinal:03d}"
    left, right = _service(case_id, "a"), _service(case_id, "b")
    services = cast(tuple[str, str], tuple(sorted((left, right))))
    target = services[0] if ordinal % 2 else services[1]
    peer = services[1] if target == services[0] else services[0]
    captured_at = _CAPTURE_START + timedelta(minutes=ordinal - 100)
    error_services: tuple[str, ...] = ()
    latency_services: tuple[str, ...] = ()
    unavailable_service = None
    logs: tuple[LogRecordV22, ...] = ()
    traces: tuple[TraceSpanV22, ...] = ()
    resources = tuple(_resource(service) for service in services)
    changes: tuple[RecentChangeRecordV22, ...] = ()
    omit_request_support = False

    if ordinal in {101, 102}:  # hidden configuration, metric competition
        error_services = (target,)
        latency_services = (peer,)
        changes = (_change(target, captured_at, ordinal),)
        logs = (_log(target, captured_at, "opaque setting rejected by local parser"),)
    elif ordinal in {103, 104}:  # hidden dependency latency
        error_services = (peer,)
        latency_services = (target,)
        traces = (
            _trace(
                peer,
                target,
                captured_at,
                "opaque-fetch",
                first_error=True,
                duration_ms=112.0,
            ),
        )
    elif ordinal in {105, 106}:  # unregistered worker/channel pool
        error_services = (target,)
        latency_services = (peer,)
        logs = (
            _log(target, captured_at, "worker slots unavailable on opaque lane"),
            _log(peer, captured_at, "reusable channel wait exceeded local watermark"),
        )
    elif ordinal in {107, 108}:  # unregistered queue/throttling
        error_services = (peer,)
        latency_services = (target,)
        logs = (
            _log(target, captured_at, "backlog watermark exceeded on opaque lane"),
            _log(peer, captured_at, "admission pace reduced while peer drains"),
        )
    elif ordinal in {109, 110}:  # hidden service unavailable
        error_services = (target,)
        unavailable_service = target
    elif ordinal in {111, 112}:  # hidden CPU saturation
        error_services = (target,)
        resources = tuple(
            _resource(service, cpu_high=service == target) for service in services
        )
    elif ordinal in {113, 114}:  # hidden memory leak
        error_services = (target,)
        resources = tuple(
            _resource(service, memory_growth=service == target)
            for service in services
        )
        logs = (_log(target, captured_at, "memory pressure increased on opaque worker"),)
    elif ordinal == 115:  # registered configuration
        error_services = (target,)
        changes = (_change(target, captured_at, ordinal),)
    elif ordinal == 116:  # registered service unavailable
        error_services = (target,)
        unavailable_service = target
    elif ordinal == 117:  # registered CPU saturation
        omit_request_support = True
        resources = tuple(
            _resource(service, cpu_high=service == target) for service in services
        )
    elif ordinal == 118:  # registered dependency latency
        latency_services = (target,)
        traces = (
            _trace(
                peer,
                target,
                captured_at,
                "opaque-call",
                first_error=True,
                duration_ms=118.0,
            ),
        )
    elif ordinal in {122, 123, 124}:  # no-known explicit contradiction
        error_services = services
        traces = (
            _trace(
                peer,
                target,
                captured_at,
                "opaque-alpha",
                first_error=True,
                duration_ms=12.0,
            ),
            _trace(
                target,
                peer,
                captured_at,
                "opaque-beta",
                first_error=True,
                duration_ms=12.0,
            ),
        )

    metrics = _metrics(
        services,
        captured_at,
        error_services=error_services,
        latency_services=latency_services,
    )
    if omit_request_support:
        metrics = tuple(
            item
            for item in metrics
            if item.metric_kind is not MetricKindV22.REQUEST_SUPPORT
        )
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=metrics,
        logs=logs,
        traces=traces,
        runtime=_runtime(services, unavailable_service=unavailable_service),
        resources=resources,
        changes=changes,
        source_failures=(),
    )
    observer = {
        "case_id": case_id,
        "candidate_services": services,
        "topology_edges": (services,),
        "capture": capture.model_dump(mode="json"),
    }
    return {
        **observer,
        "source_bytes_sha256": semantic_sha256_v22(observer),
        "target": target,
    }


def _truth(designs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hidden = set(range(101, 105)) | set(range(109, 115))
    unregistered = set(range(105, 109))
    known = set(range(115, 119))
    no_incident = set(range(119, 122))
    conflict = set(range(122, 125))
    mechanisms = {
        101: "CONFIGURATION_ERROR", 102: "CONFIGURATION_ERROR",
        103: "DEPENDENCY_LATENCY", 104: "DEPENDENCY_LATENCY",
        109: "SERVICE_UNAVAILABLE", 110: "SERVICE_UNAVAILABLE",
        111: "CPU_SATURATION", 112: "CPU_SATURATION",
        113: "MEMORY_LEAK", 114: "MEMORY_LEAK",
        115: "CONFIGURATION_ERROR", 116: "SERVICE_UNAVAILABLE",
        117: "CPU_SATURATION", 118: "DEPENDENCY_LATENCY",
    }
    domains = {
        101: "CONFIGURATION", 102: "CONFIGURATION",
        103: "DEPENDENCY", 104: "DEPENDENCY",
        105: "CONCURRENCY", 106: "CONCURRENCY",
        107: "CONCURRENCY", 108: "CONCURRENCY",
        109: "RUNTIME", 110: "RUNTIME",
        111: "RESOURCE", 112: "RESOURCE",
        113: "RESOURCE", 114: "RESOURCE",
        115: "CONFIGURATION", 116: "RUNTIME",
        117: "RESOURCE", 118: "DEPENDENCY",
    }
    concepts = {
        "CONFIGURATION": ("configuration", "setting"),
        "DEPENDENCY": ("dependency", "latency"),
        "CONCURRENCY": ("pool", "queue"),
        "RUNTIME": ("runtime", "unavailable"),
        "RESOURCE": ("resource", "pressure"),
    }
    pairs = {
        101: "scf-01", 102: "scf-01", 103: "scf-02", 104: "scf-02",
        105: "scf-03", 106: "scf-03", 107: "scf-04", 108: "scf-04",
    }
    records = []
    for design in designs:
        ordinal = int(design["case_id"].split("-")[1])
        if ordinal in hidden:
            category = "NOVEL_HIDDEN"
            stratum = "NOVEL_HIDDEN"
        elif ordinal in unregistered:
            category = "NOVEL_UNREGISTERED"
            stratum = "NOVEL_UNREGISTERED"
        elif ordinal in known:
            category = "REGISTERED_KNOWN"
            stratum = "REGISTERED_KNOWN"
        elif ordinal in no_incident:
            category = "NO_INCIDENT"
            stratum = "NO_INCIDENT"
        elif ordinal in conflict:
            category = "INSUFFICIENT_CONFLICT"
            stratum = "INSUFFICIENT_IRRECONCILABLE"
        else:
            raise ValueError("successor truth ordinal is unknown")
        domain = domains.get(ordinal)
        pair_id = pairs.get(ordinal)
        role = None
        if pair_id is not None:
            role = (
                "TARGET_LOW"
                if design["target"] == min(design["candidate_services"])
                else "TARGET_HIGH"
            )
        root = design["target"] if ordinal <= 118 else None
        records.append(
            {
                "evaluator_truth": {
                    "case_id": design["case_id"],
                    "category": category,
                    "expected_disposition": (
                        "KNOWN_INCIDENT" if ordinal in known
                        else "NO_INCIDENT" if ordinal in no_incident
                        else "CONFLICTING_EVIDENCE" if ordinal in conflict
                        else "UNREGISTERED_INCIDENT"
                    ),
                    "expected_root_service": root,
                    "expected_broad_domain": domain,
                    "expected_mechanism": mechanisms.get(ordinal),
                    "semantic_concepts": concepts.get(domain or "", ()),
                    "counterfactual_pair_id": pair_id,
                    "requires_discovery_read": ordinal in hidden | unregistered,
                    "empty_or_misleading_action": ordinal in {102, 104, 106, 108},
                    "conflict_prone_novelty": ordinal in set(range(101, 109)),
                    "multi_coherent_interpretations": ordinal in set(range(101, 109)),
                    "true_irreconcilable_conflict": ordinal in conflict,
                },
                "admission_stratum": stratum,
                "expected_known_mechanism": (
                    mechanisms.get(ordinal) if ordinal in known else None
                ),
                "counterfactual_target_role": role,
            }
        )
    return records


def _views() -> dict[str, Any]:
    hidden = {
        101: "CONFIGURATION_ERROR", 102: "CONFIGURATION_ERROR",
        103: "DEPENDENCY_LATENCY", 104: "DEPENDENCY_LATENCY",
        109: "SERVICE_UNAVAILABLE", 110: "SERVICE_UNAVAILABLE",
        111: "CPU_SATURATION", 112: "CPU_SATURATION",
        113: "MEMORY_LEAK", 114: "MEMORY_LEAK",
    }
    return {
        "schema_version": "dta-v231.successor-ontology-view-set.v1",
        "views": [
            {"case_id": f"vx-{ordinal:03d}", "hidden_mechanism": hidden.get(ordinal)}
            for ordinal in range(101, 125)
        ],
    }


def build() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    designs = [_design(ordinal) for ordinal in range(101, 125)]
    cases = {
        "schema_version": "dta-v231.successor-case-set.v1",
        "freeze_id": "dta-v231-successor-independent-freeze-20260825-a",
        "cases": [
            {key: value for key, value in design.items() if key != "target"}
            for design in designs
        ],
    }
    return cases, _truth(designs), _views()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    cases, truth_records, views = build()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for name, payload in (("cases.json", cases), ("ontology-views.json", views)):
        (args.output_root / name).write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    truth_root = args.output_root / "truth"
    truth_root.mkdir(parents=True, exist_ok=True)
    bindings = []
    for record in truth_records:
        case_id = record["evaluator_truth"]["case_id"]
        payload = {
            "schema_version": "dta-v231.successor-truth-shard.v1",
            "record": record,
        }
        raw = json.dumps(payload, indent=2, sort_keys=False) + "\n"
        (truth_root / f"{case_id}.json").write_text(raw, encoding="utf-8")
        bindings.append(
            {
                "case_id": case_id,
                "path": f"truth/{case_id}.json",
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            }
        )
    index_payload = {
        "schema_version": "dta-v231.successor-truth-index.v1",
        "shards": bindings,
    }
    index = {
        **index_payload,
        "index_sha256": semantic_sha256_v22(index_payload),
    }
    (args.output_root / "truth-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
