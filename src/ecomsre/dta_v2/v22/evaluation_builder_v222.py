"""Build the new synthetic/derived 16-case DTA v2.2.2 evaluation freeze."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
)
from ecomsre.dta_v2.v22.practical_campaign import PracticalTruthSetV22
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaptureKindV22,
    PracticalCaseModifierV22,
    PracticalCaseSetV22,
    PracticalCaseSpecV22,
    SyntheticEvaluationSourceV222,
    load_practical_case_set_v22,
)
from ecomsre.dta_v2.v22.practical_replay import NormalizedPracticalCaseV22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22


@dataclass(frozen=True, slots=True)
class _Blueprint:
    case_id: str
    terminal: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN"]
    target: str
    parent: str
    mechanism: str | None
    counterfactual_pair_ids: tuple[str, ...]
    capture_kind: PracticalCaptureKindV22


_BLUEPRINTS = (
    _Blueprint("e01", "DIAGNOSED", "catalog", "frontend", "CONFIGURATION_ERROR", ("cf-config",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e02", "DIAGNOSED", "checkout", "gateway", "CONFIGURATION_ERROR", (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e03", "DIAGNOSED", "inventory", "frontend", "SERVICE_UNAVAILABLE", ("cf-service",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e04", "DIAGNOSED", "payment", "checkout", "SERVICE_UNAVAILABLE", (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e05", "DIAGNOSED", "recommendation", "frontend", "CPU_SATURATION", ("cf-cpu",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e06", "DIAGNOSED", "ad", "frontend", "CPU_SATURATION", (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e07", "DIAGNOSED", "email", "checkout", "MEMORY_LEAK", (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e08", "DIAGNOSED", "cart", "frontend", "MEMORY_LEAK", (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e09", "DIAGNOSED", "shipping", "checkout", "DEPENDENCY_LATENCY", ("cf-dependency",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e10", "DIAGNOSED", "currency", "checkout", "DEPENDENCY_LATENCY", (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e11", "NO_INCIDENT", "catalog", "frontend", None, ("cf-config",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e12", "NO_INCIDENT", "inventory", "frontend", None, ("cf-service",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e13", "NO_INCIDENT", "recommendation", "frontend", None, ("cf-cpu",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e14", "ABSTAIN", "shipping", "checkout", None, ("cf-dependency",), PracticalCaptureKindV22.SYNTHETIC_COUNTERFACTUAL_DERIVED),
    _Blueprint("e15", "ABSTAIN", "email", "checkout", None, (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
    _Blueprint("e16", "ABSTAIN", "payment", "checkout", None, (), PracticalCaptureKindV22.SYNTHETIC_V22_FIXTURE_DERIVED),
)


def _metric(
    *, service: str, kind: MetricKindV22, value: float | None, captured_at: datetime
) -> MetricFactV22:
    supported = value is not None
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service=service,
        metric_kind=kind,
        support_status=(
            MetricSupportStatusV22.SUPPORTED
            if supported
            else MetricSupportStatusV22.UNSUPPORTED
        ),
        sample_count=3 if supported else 0,
        value=value,
        unit=METRIC_UNIT_BY_KIND_V22[kind],
        window_started_at=captured_at - timedelta(seconds=300),
        window_ended_at=captured_at,
    )


def _resources(blueprint: _Blueprint) -> tuple[ResourceUsageRecordV22, ...]:
    if blueprint.mechanism not in {"CPU_SATURATION", "MEMORY_LEAK"}:
        return ()
    cpu = 96.0 if blueprint.mechanism == "CPU_SATURATION" else 20.0
    slope = 2_000_000.0 if blueprint.mechanism == "MEMORY_LEAK" else 0.0
    return (
        ResourceUsageRecordV22(
            schema_version="dta-v22.resource-usage-record.v1",
            service=blueprint.target,
            sampling_window_seconds=10,
            samples=tuple(
                ResourceSampleV22(
                    offset_ms=offset,
                    cpu_percent=cpu,
                    memory_bytes=100_000_000 + round(slope * offset / 1000),
                )
                for offset in (0, 2500, 5000, 7500, 10_000)
            ),
            memory_slope_bytes_per_second=slope,
        ),
    )


def _traces(
    *, blueprint: _Blueprint, captured_at: datetime
) -> tuple[TraceSpanV22, ...]:
    if blueprint.mechanism not in {
        "CONFIGURATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "DEPENDENCY_LATENCY",
    }:
        return ()
    dependency = blueprint.mechanism == "DEPENDENCY_LATENCY"
    return (
        TraceSpanV22(
            schema_version="dta-v22.trace-span.v1",
            observed_at=captured_at,
            service_path=(blueprint.parent, blueprint.target),
            service=blueprint.target,
            parent_service=blueprint.parent,
            operation=f"observe-{blueprint.case_id}",
            status=SpanStatusV22.UNSET if dependency else SpanStatusV22.ERROR,
            duration_ms=600.0 if dependency else 10.0,
            first_error_location=not dependency,
        ),
    )


def _source(blueprint: _Blueprint, index: int) -> SyntheticEvaluationSourceV222:
    captured_at = datetime(2026, 8, 21, 0, index, tzinfo=UTC)
    services = tuple(sorted((blueprint.parent, blueprint.target)))
    metrics: list[MetricFactV22] = []
    for service in services:
        error_rate = (
            0.45
            if blueprint.mechanism == "CONFIGURATION_ERROR"
            and service == blueprint.target
            else 0.01
        )
        latency = (
            450.0
            if blueprint.mechanism == "DEPENDENCY_LATENCY"
            and service == blueprint.parent
            else 10.0
        )
        request_support: float | None = 100.0
        if blueprint.terminal == "ABSTAIN" and service == blueprint.target:
            request_support = None
        metrics.extend(
            (
                _metric(
                    service=service,
                    kind=MetricKindV22.ERROR_RATE,
                    value=error_rate,
                    captured_at=captured_at,
                ),
                _metric(
                    service=service,
                    kind=MetricKindV22.LATENCY_P95_MS,
                    value=latency,
                    captured_at=captured_at,
                ),
                _metric(
                    service=service,
                    kind=MetricKindV22.REQUEST_SUPPORT,
                    value=request_support,
                    captured_at=captured_at,
                ),
            )
        )
    runtime = tuple(
        RuntimeRecordV22(
            schema_version="dta-v22.runtime-record.v1",
            service=service,
            state=RuntimeStateV22.RUNNING,
            healthy=not (
                blueprint.mechanism == "SERVICE_UNAVAILABLE"
                and service == blueprint.target
            ),
            restart_count=0,
        )
        for service in services
    )
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=tuple(sorted(metrics, key=lambda item: (item.service, item.metric_kind.value))),
        logs=(),
        traces=_traces(blueprint=blueprint, captured_at=captured_at),
        runtime=runtime,
        resources=_resources(blueprint),
        changes=(),
        source_failures=(),
    )
    normalized = NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=blueprint.case_id,
        source_bytes_sha256=semantic_sha256_v22(capture.model_dump(mode="json")),
        candidate_services=services,
        topology_edges=((min(blueprint.parent, blueprint.target), max(blueprint.parent, blueprint.target)),),
        capture=capture,
        normalization_notes=(
            "Synthetic/derived DTA v2.2.2 evaluator fixture; no Docker capture.",
        ),
    )
    return SyntheticEvaluationSourceV222(
        schema_version="dta-v22.2.synthetic-evaluation-source.v1",
        captured_sources=tuple(EvidenceSourceV22),
        normalized_case=normalized,
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def build_evaluation_assets_v222(*, repository_root: Path) -> dict[str, object]:
    evaluation_root = repository_root / "config/dta-v22-2/evaluation"
    specs: list[PracticalCaseSpecV22] = []
    truths: list[PracticalTruthV22] = []
    source_hashes: set[str] = set()
    for index, blueprint in enumerate(_BLUEPRINTS, start=1):
        source = _source(blueprint, index)
        relative = Path(
            f"config/dta-v22-2/evaluation/agent-visible/{blueprint.case_id}.json"
        )
        payload = _json_bytes(source.model_dump(mode="json"))
        digest = hashlib.sha256(payload).hexdigest()
        _write_once(repository_root / relative, payload)
        source_hashes.add(digest)
        specs.append(
            PracticalCaseSpecV22(
                case_id=blueprint.case_id,
                source_path=str(relative),
                source_sha256=digest,
                capture_kind=blueprint.capture_kind,
                modifier=PracticalCaseModifierV22.V222_EVALUATION_FIXTURE,
                derivation=(
                    "Deterministic synthetic/derived v2.2.2 replay fixture from a "
                    "truth-isolated evaluator blueprint."
                ),
                counterfactual_pair_ids=blueprint.counterfactual_pair_ids,
                bootstrap_insufficient_expected=blueprint.terminal != "NO_INCIDENT",
            )
        )
        truths.append(
            PracticalTruthV22(
                case_id=blueprint.case_id,
                expected_terminal=blueprint.terminal,
                expected_root_service=(
                    blueprint.target if blueprint.terminal == "DIAGNOSED" else None
                ),
                expected_mechanism=blueprint.mechanism,
                evidence_applicable=blueprint.terminal == "DIAGNOSED",
            )
        )
    case_set = PracticalCaseSetV22(
        schema_version="dta-v22.practical-case-set.v1",
        cases=tuple(specs),
    )
    truth_set = PracticalTruthSetV22(
        schema_version="dta-v22.practical-truth-set.v1",
        truths=tuple(truths),
    )
    case_path = evaluation_root / "cases.json"
    truth_path = evaluation_root / "truth.json"
    _write_once(case_path, _json_bytes(case_set.model_dump(mode="json")))
    _write_once(truth_path, _json_bytes(truth_set.model_dump(mode="json")))
    audit = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_path,
        truth_path=truth_path,
    )
    audit_path = evaluation_root / "utility-audit.json"
    _write_once(audit_path, _json_bytes(audit.model_dump(mode="json")))

    incident_paths = tuple(
        item.shortest_admissible_path
        for item in audit.cases
        if item.expected_terminal == "DIAGNOSED"
    )
    pairs = {
        pair
        for item in specs
        for pair in item.counterfactual_pair_ids
    }
    tempting_empty = sum(
        not source.normalized_case.capture.logs
        for source in (_source(item, index) for index, item in enumerate(_BLUEPRINTS, 1))
    )
    previous = {
        item.source_sha256
        for item in load_practical_case_set_v22(
            repository_root / "config/dta-v22-sprint/evaluation/cases.json"
        ).cases
        if item.source_sha256 is not None
    }
    nonidentical = sum(item not in previous for item in source_hashes)
    if (
        len(specs) != 16
        or len(incident_paths) != 10
        or any(
            item not in {ShortestAdmissiblePathV222.ONE, ShortestAdmissiblePathV222.TWO}
            for item in incident_paths
        )
        or len(pairs) < 4
        or tempting_empty < 4
        or nonidentical < 8
        or audit.infeasible_incident_cases
    ):
        raise ValueError("v2.2.2 evaluation composition or feasibility gate failed")
    return {
        "cases": len(specs),
        "core_incident_path_1_or_2": len(incident_paths),
        "counterfactual_pairs": len(pairs),
        "tempting_empty_cases": tempting_empty,
        "non_byte_identical_to_previous": nonidentical,
        "infeasible_incident_cases": audit.infeasible_incident_cases,
        "utility_audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "agent_writes": 0,
    }


if __name__ == "__main__":
    print(json.dumps(build_evaluation_assets_v222(repository_root=Path.cwd()), sort_keys=True))


__all__ = ("build_evaluation_assets_v222",)
