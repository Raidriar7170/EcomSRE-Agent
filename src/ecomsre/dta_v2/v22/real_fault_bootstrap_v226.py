"""Shared canonical Runtime/Metrics bootstrap for DTA v2.2.6."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
from typing import Any, Literal, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    MemoryReadOutcomeV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _memory_outcome
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    ActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


class RealFaultBootstrapReadBindingV226(DtaModelV22):
    source: EvidenceSourceV22
    action_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_binding(self) -> RealFaultBootstrapReadBindingV226:
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("bootstrap evidence refs are not canonical")
        if any(not item.startswith(f"e:{self.action_id}:") for item in self.evidence_refs):
            raise ValueError("bootstrap evidence ref differs from action binding")
        return self


class RealFaultCanonicalBootstrapV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.canonical-bootstrap.v1"]
    candidate_services: tuple[str, ...] = Field(min_length=2, max_length=4)
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_bindings: tuple[RealFaultBootstrapReadBindingV226, ...] = Field(
        min_length=3, max_length=5
    )
    unsupported_metric_count: StrictInt = Field(ge=0, le=20)
    resources_in_bootstrap: Literal[False]
    truth_consulted: Literal[False]
    bootstrap_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bootstrap(self) -> RealFaultCanonicalBootstrapV226:
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("bootstrap candidates are not canonical")
        sources = tuple(item.source for item in self.read_bindings)
        if sources != (
            EvidenceSourceV22.RUNTIME,
            *(EvidenceSourceV22.METRICS for _ in self.candidate_services),
        ):
            raise ValueError("bootstrap read surface differs")
        action_ids = tuple(item.action_id for item in self.read_bindings)
        outcome_ids = tuple(item.outcome_sha256 for item in self.read_bindings)
        if len(action_ids) != len(set(action_ids)) or len(outcome_ids) != len(
            set(outcome_ids)
        ):
            raise ValueError("bootstrap contains duplicate action or outcome")
        if self.bootstrap_sha256 != self.recompute_sha256():
            raise ValueError("canonical bootstrap digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"bootstrap_sha256"})
        )


@dataclass(frozen=True, slots=True)
class RealFaultBootstrapPlanV226:
    run_id: str
    actions: tuple[EvidenceActionV22, ...]


def real_fault_run_id_v226(capture: RealFaultOpaqueCaptureV1) -> str:
    return hashlib.sha256(
        f"real-fault-v226:{capture.case_id}:{capture.opaque_capture_sha256}".encode()
    ).hexdigest()[:32]


def build_real_fault_baseline_profile_v226(
    capture: RealFaultOpaqueCaptureV1,
) -> BaselineProfileV22:
    metrics = tuple(
        (
            item.service,
            item.metric_kind,
            float(item.value or 0.0),
            max(abs(float(item.value or 0.0)) * 0.01, 0.01),
        )
        for item in capture.capture.metrics
        if item.support_status is MetricSupportStatusV22.SUPPORTED
    )
    traces = tuple(
        sorted(
            {
                (item.service, item.operation, float(item.duration_ms))
                for item in capture.capture.traces
            }
        )
    )
    resources = tuple(
        (
            item.service,
            max(sample.cpu_percent for sample in item.samples),
            item.memory_slope_bytes_per_second,
        )
        for item in capture.capture.resources
    )
    return BaselineProfileV22.build(
        metric_stats=metrics,
        trace_stats=traces,
        resource_stats=resources,
    )


def _normalize_metrics_v226(
    *, action: EvidenceActionV22, outcome: ReadOutcomeV22, observed_at: object
) -> ReadOutcomeV22:
    if action.source is not EvidenceSourceV22.METRICS:
        return outcome
    if outcome.status not in {
        ReadSourceStatusV22.SUCCESS_EMPTY,
        ReadSourceStatusV22.SUCCESS_NONEMPTY,
    }:
        return outcome
    existing = {
        item.metric_kind: item
        for item in outcome.records
        if isinstance(item, MetricFactV22)
    }
    ended_at = cast(Any, observed_at)
    started_at = ended_at - timedelta(seconds=cast(int, action.request.lookback_seconds))
    records = tuple(
        existing.get(kind)
        or MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service=action.target_services[0],
            metric_kind=kind,
            support_status=MetricSupportStatusV22.UNSUPPORTED,
            sample_count=0,
            value=None,
            unit=METRIC_UNIT_BY_KIND_V22[kind],
            window_started_at=started_at,
            window_ended_at=ended_at,
        )
        for kind in action.request.metric_kinds
    )
    payload = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": outcome.action_id,
        "source": outcome.source,
        "request_sha256": outcome.request_sha256,
        "status": ReadSourceStatusV22.SUCCESS_NONEMPTY,
        "records": records,
        "truncated": outcome.truncated,
    }
    draft = cast(Any, ReadOutcomeV22).model_construct(
        **payload, outcome_sha256="0" * 64
    )
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def build_real_fault_bootstrap_plan_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
) -> RealFaultBootstrapPlanV226:
    if capture.alias_map_name != baseline_capture.alias_map_name:
        raise ValueError("bootstrap capture and baseline maps differ")
    run_id = real_fault_run_id_v226(capture)
    topology = StaticTopologyV22.build(services=capture.candidate_aliases, edges=())
    catalog = build_action_catalog_v22(
        candidate_services=capture.candidate_aliases,
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=3.0,
    )
    runtime_action = next(
        action
        for action in catalog.registry_actions
        if action.source is EvidenceSourceV22.RUNTIME
        and action.target_services == capture.candidate_aliases
    )
    metric_actions = tuple(
        next(
            action
            for action in catalog.registry_actions
            if action.source is EvidenceSourceV22.METRICS
            and action.target_services == (service,)
        )
        for service in capture.candidate_aliases
    )
    actions = (runtime_action, *metric_actions)
    return RealFaultBootstrapPlanV226(run_id=run_id, actions=actions)


def dispatch_real_fault_bootstrap_v226(
    *,
    plan: RealFaultBootstrapPlanV226,
    backend: ActionReadBackendV225,
) -> tuple[ReadOutcomeV22, ...]:
    return tuple(backend.execute(action) for action in plan.actions)


def finalize_real_fault_bootstrap_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    plan: RealFaultBootstrapPlanV226,
    source_outcomes: tuple[ReadOutcomeV22, ...],
) -> tuple[RealFaultCanonicalBootstrapV226, tuple[MemoryReadOutcomeV22, ...]]:
    if len(source_outcomes) != len(plan.actions):
        raise ValueError("bootstrap dispatch outcome count differs")
    outcomes: list[MemoryReadOutcomeV22] = []
    for ordinal, (action, source_outcome) in enumerate(
        zip(plan.actions, source_outcomes, strict=True), start=1
    ):
        normalized = _normalize_metrics_v226(
            action=action,
            outcome=source_outcome,
            observed_at=capture.capture.captured_at,
        )
        outcomes.append(
            _memory_outcome(
                action=action,
                outcome=normalized,
                run_id=plan.run_id,
                dispatch_ordinal=ordinal,
                observed_at=capture.capture.captured_at,
            )
        )
    canonical_outcomes = tuple(outcomes)
    baseline = build_real_fault_baseline_profile_v226(baseline_capture)
    memory, _ = build_memory_views_v22(
        outcomes=canonical_outcomes,
        baseline=baseline,
        observed_at=capture.capture.captured_at,
        top_k=64,
    )
    refs_by_outcome = {
        outcome.outcome_sha256: tuple(
            item.evidence_ref
            for item in memory.evidence_refs
            if item.outcome_sha256 == outcome.outcome_sha256
        )
        for outcome in canonical_outcomes
    }
    bindings = tuple(
        RealFaultBootstrapReadBindingV226(
            source=outcome.source,
            action_id=outcome.action_id,
            request_sha256=outcome.request_sha256,
            outcome_sha256=outcome.outcome_sha256,
            evidence_refs=tuple(sorted(refs_by_outcome[outcome.outcome_sha256])),
        )
        for outcome in canonical_outcomes
    )
    payload = {
        "schema_version": "dta-v226-real-fault.canonical-bootstrap.v1",
        "candidate_services": capture.candidate_aliases,
        "baseline_sha256": baseline.baseline_sha256,
        "memory_sha256": memory.memory_sha256,
        "read_bindings": bindings,
        "unsupported_metric_count": sum(
            isinstance(record, MetricFactV22)
            and record.support_status is MetricSupportStatusV22.UNSUPPORTED
            for outcome in canonical_outcomes
            for record in outcome.records
        ),
        "resources_in_bootstrap": False,
        "truth_consulted": False,
    }
    draft = cast(Any, RealFaultCanonicalBootstrapV226).model_construct(
        **payload, bootstrap_sha256="0" * 64
    )
    bootstrap = RealFaultCanonicalBootstrapV226.model_validate(
        {
            **payload,
            "bootstrap_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"bootstrap_sha256"})
            ),
        }
    )
    return bootstrap, canonical_outcomes


def build_real_fault_canonical_bootstrap_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    backend: ActionReadBackendV225,
) -> tuple[RealFaultCanonicalBootstrapV226, tuple[MemoryReadOutcomeV22, ...]]:
    plan = build_real_fault_bootstrap_plan_v226(
        capture=capture,
        baseline_capture=baseline_capture,
    )
    source_outcomes = dispatch_real_fault_bootstrap_v226(
        plan=plan,
        backend=backend,
    )
    return finalize_real_fault_bootstrap_v226(
        capture=capture,
        baseline_capture=baseline_capture,
        plan=plan,
        source_outcomes=source_outcomes,
    )


__all__ = (
    "RealFaultBootstrapReadBindingV226",
    "RealFaultBootstrapPlanV226",
    "RealFaultCanonicalBootstrapV226",
    "build_real_fault_baseline_profile_v226",
    "build_real_fault_bootstrap_plan_v226",
    "build_real_fault_canonical_bootstrap_v226",
    "dispatch_real_fault_bootstrap_v226",
    "finalize_real_fault_bootstrap_v226",
    "real_fault_run_id_v226",
)
