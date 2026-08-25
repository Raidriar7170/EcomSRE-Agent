"""Build the independent, observer-complete DTA v2.3.1 fixed replay set."""

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


_SALT = "dta-v231-independent-observer-set-c-8c715d4e"
_CAPTURE_START = datetime(2026, 8, 25, 11, 0, tzinfo=timezone.utc)


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
    error_service: str | None = None,
    latency_service: str | None = None,
) -> tuple[MetricFactV22, ...]:
    return tuple(
        _metric(service, kind, value, captured_at)
        for service in services
        for kind, value in (
            (
                MetricKindV22.ERROR_RATE,
                0.32 if service == error_service else 0.01,
            ),
            (
                MetricKindV22.LATENCY_P95_MS,
                72.0 if service == latency_service else 10.0,
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
    end_memory = start_memory + (80_000_000 if memory_growth else 0)
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=(
            ResourceSampleV22(
                offset_ms=0,
                cpu_percent=92.0 if cpu_high else 20.0,
                memory_bytes=start_memory,
            ),
            ResourceSampleV22(
                offset_ms=10000,
                cpu_percent=94.0 if cpu_high else 21.0,
                memory_bytes=end_memory,
            ),
        ),
        memory_slope_bytes_per_second=(8_000_000.0 if memory_growth else 0.0),
    )


def _log(
    service: str,
    captured_at: datetime,
    message: str,
) -> LogRecordV22:
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
        revision_digest=hashlib.sha256(f"{_SALT}:revision:{ordinal}".encode()).hexdigest(),
    )


def _design(ordinal: int) -> dict[str, Any]:
    case_id = f"vx-{ordinal:03d}"
    left, right = _service(case_id, "a"), _service(case_id, "b")
    services = cast(tuple[str, str], tuple(sorted((left, right))))
    target = services[(ordinal + 1) % 2]
    peer = services[0] if target == services[1] else services[1]
    captured_at = _CAPTURE_START + timedelta(minutes=ordinal)
    profile: dict[str, Any] = {
        "case_id": case_id,
        "services": services,
        "target": target,
        "peer": peer,
        "captured_at": captured_at,
        "error_service": None,
        "latency_service": None,
        "unavailable_service": None,
        "logs": (),
        "traces": (),
        "resources": tuple(_resource(service) for service in services),
        "changes": (),
    }
    if ordinal in {1, 2}:  # hidden configuration, cross-domain coherent evidence
        profile.update(
            error_service=target,
            logs=(
                _log(target, captured_at, "opaque setting rejected during request handling"),
                _log(peer, captured_at, "peer observed propagated opaque request failure"),
            ),
            traces=(_trace(peer, target, captured_at, "opaque-write", first_error=True, duration_ms=35.0),),
            changes=(_change(target, captured_at, ordinal),),
        )
    elif ordinal in {3, 4}:  # hidden dependency latency
        profile.update(
            latency_service=target,
            error_service=peer,
            logs=(_log(peer, captured_at, "upstream observed delayed opaque response"),),
            traces=(_trace(peer, target, captured_at, "opaque-fetch", first_error=True, duration_ms=95.0),),
        )
    elif ordinal in {5, 6}:  # unregistered connection/worker-pool exhaustion
        profile.update(
            error_service=target,
            latency_service=peer,
            logs=(
                _log(target, captured_at, "worker slots unavailable under opaque fanout"),
                _log(peer, captured_at, "peer requests waiting for reusable channel"),
            ),
            traces=(_trace(peer, target, captured_at, "opaque-acquire", first_error=True, duration_ms=88.0),),
        )
    elif ordinal in {7, 8}:  # unregistered queue backlog/throttling
        profile.update(
            latency_service=target,
            error_service=peer,
            logs=(
                _log(target, captured_at, "backlog watermark exceeded on opaque lane"),
                _log(peer, captured_at, "admission pace reduced while peer drains"),
            ),
            traces=(_trace(peer, target, captured_at, "opaque-drain", first_error=True, duration_ms=110.0),),
        )
    elif ordinal in {9, 10}:  # hidden runtime/service unavailable
        profile.update(error_service=target, unavailable_service=target)
    elif ordinal in {11, 12}:  # hidden CPU saturation
        profile.update(
            error_service=target,
            resources=tuple(
                _resource(service, cpu_high=service == target) for service in services
            ),
        )
    elif ordinal in {13, 14}:  # hidden memory leak
        profile.update(
            error_service=target,
            resources=tuple(
                _resource(service, memory_growth=service == target)
                for service in services
            ),
        )
    elif ordinal == 15:  # registered configuration
        profile.update(
            error_service=target,
            logs=(_log(target, captured_at, "configuration value invalid for opaque field"),),
            changes=(_change(target, captured_at, ordinal),),
        )
    elif ordinal == 16:  # registered unavailable
        profile.update(error_service=target, unavailable_service=target)
    elif ordinal == 17:  # registered CPU
        profile.update(
            error_service=target,
            resources=tuple(
                _resource(service, cpu_high=service == target) for service in services
            ),
        )
    elif ordinal == 18:  # registered dependency latency
        profile.update(
            latency_service=target,
            logs=(_log(peer, captured_at, "dependency timeout while calling opaque peer"),),
            traces=(_trace(peer, target, captured_at, "opaque-call", first_error=True, duration_ms=105.0),),
        )
    elif ordinal in {22, 23, 24}:  # explicit irreconcilable controls
        profile.update(
            error_service=target,
            latency_service=peer,
            logs=tuple(
                _log(
                    service,
                    captured_at,
                    "exclusive causal origin asserted here while the peer location is explicitly cleared",
                )
                for service in services
            ),
            traces=(
                _trace(peer, target, captured_at, "opaque-alpha", first_error=True, duration_ms=40.0),
                _trace(target, peer, captured_at, "opaque-beta", first_error=True, duration_ms=40.0),
            ),
        )
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=_metrics(
            services,
            captured_at,
            error_service=profile["error_service"],
            latency_service=profile["latency_service"],
        ),
        logs=profile["logs"],
        traces=profile["traces"],
        runtime=_runtime(services, unavailable_service=profile["unavailable_service"]),
        resources=profile["resources"],
        changes=profile["changes"],
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


def _truth(designs: list[dict[str, Any]]) -> dict[str, Any]:
    categories = {
        **{index: "NOVEL_HIDDEN" for index in (*range(1, 5), *range(9, 15))},
        **{index: "NOVEL_UNREGISTERED" for index in range(5, 9)},
        **{index: "REGISTERED_KNOWN" for index in range(15, 19)},
        **{index: "NO_INCIDENT" for index in range(19, 22)},
        **{index: "INSUFFICIENT_CONFLICT" for index in range(22, 25)},
    }
    mechanisms = {
        1: "CONFIGURATION_ERROR", 2: "CONFIGURATION_ERROR",
        3: "DEPENDENCY_LATENCY", 4: "DEPENDENCY_LATENCY",
        9: "SERVICE_UNAVAILABLE", 10: "SERVICE_UNAVAILABLE",
        11: "CPU_SATURATION", 12: "CPU_SATURATION",
        13: "MEMORY_LEAK", 14: "MEMORY_LEAK",
        15: "CONFIGURATION_ERROR", 16: "SERVICE_UNAVAILABLE",
        17: "CPU_SATURATION", 18: "DEPENDENCY_LATENCY",
    }
    domains = {
        1: "CONFIGURATION", 2: "CONFIGURATION", 3: "DEPENDENCY", 4: "DEPENDENCY",
        5: "CONCURRENCY", 6: "CONCURRENCY", 7: "CONCURRENCY", 8: "CONCURRENCY",
        9: "RUNTIME", 10: "RUNTIME", 11: "RESOURCE", 12: "RESOURCE",
        13: "RESOURCE", 14: "RESOURCE", 15: "CONFIGURATION", 16: "RUNTIME",
        17: "RESOURCE", 18: "DEPENDENCY",
    }
    concepts: dict[str, tuple[str, ...]] = {
        "CONFIGURATION": ("configuration", "setting"),
        "DEPENDENCY": ("dependency", "latency"),
        "CONCURRENCY": ("pool", "queue"),
        "RUNTIME": ("runtime", "unavailable"),
        "RESOURCE": ("resource", "pressure"),
    }
    pair_by_ordinal = {1: "cf-01", 2: "cf-01", 3: "cf-02", 4: "cf-02", 5: "cf-03", 6: "cf-03", 7: "cf-04", 8: "cf-04"}
    truths = []
    for ordinal, design in enumerate(designs, start=1):
        category = categories[ordinal]
        domain = domains.get(ordinal)
        novelty = category in {"NOVEL_HIDDEN", "NOVEL_UNREGISTERED"}
        truths.append(
            {
                "case_id": design["case_id"],
                "category": category,
                "expected_disposition": (
                    "KNOWN_INCIDENT" if category == "REGISTERED_KNOWN"
                    else "NO_INCIDENT" if category == "NO_INCIDENT"
                    else "CONFLICTING_EVIDENCE" if category == "INSUFFICIENT_CONFLICT"
                    else "UNREGISTERED_INCIDENT"
                ),
                "expected_root_service": design["target"] if ordinal <= 18 else None,
                "expected_broad_domain": domain,
                "expected_mechanism": mechanisms.get(ordinal),
                "semantic_concepts": concepts.get(domain or "", ()),
                "counterfactual_pair_id": pair_by_ordinal.get(ordinal),
                "requires_discovery_read": novelty,
                "empty_or_misleading_action": ordinal in {2, 4, 6, 8},
                "conflict_prone_novelty": ordinal in set(range(1, 9)),
                "multi_coherent_interpretations": ordinal in set(range(1, 9)),
                "true_irreconcilable_conflict": ordinal in {22, 23, 24},
            }
        )
    return {"schema_version": "dta-v231.evaluation-truth-set.v1", "truths": truths}


def _views() -> dict[str, Any]:
    hidden = {
        1: "CONFIGURATION_ERROR", 2: "CONFIGURATION_ERROR",
        3: "DEPENDENCY_LATENCY", 4: "DEPENDENCY_LATENCY",
        9: "SERVICE_UNAVAILABLE", 10: "SERVICE_UNAVAILABLE",
        11: "CPU_SATURATION", 12: "CPU_SATURATION",
        13: "MEMORY_LEAK", 14: "MEMORY_LEAK",
        22: "SERVICE_UNAVAILABLE", 23: "SERVICE_UNAVAILABLE", 24: "SERVICE_UNAVAILABLE",
    }
    return {
        "schema_version": "dta-v231.evaluation-ontology-view-set.v1",
        "views": [
            {"case_id": f"vx-{ordinal:03d}", "hidden_mechanism": hidden.get(ordinal)}
            for ordinal in range(1, 25)
        ],
    }


def build() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    designs = [_design(ordinal) for ordinal in range(1, 25)]
    cases = {
        "schema_version": "dta-v231.evaluation-case-set.v1",
        "freeze_id": "dta-v231-independent-freeze-20260825-c",
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
    cases, truth, views = build()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("cases.json", cases),
        ("truth.json", truth),
        ("ontology-views.json", views),
    ):
        (args.output_root / name).write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
