"""Three-arm runtime comparison plumbing for DTA v2.3.3."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.contracts_v233 import (
    ProvisionalIncidentReportV233,
    build_provisional_report_v233,
    build_runtime_hypotheses_v233,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
)
from ecomsre.dta_v2.v23.discovery_provider_v233 import (
    build_discovery_synthesis_request_v233,
    call_discovery_provider_v233,
    deterministic_synthesis_response_v233,
)
from ecomsre.dta_v2.v23.discovery_runtime_v233 import (
    RuntimeDiscoveryStateV233,
    run_discovery_case_v233,
)
from ecomsre.dta_v2.v23.domain_projection_v233 import (
    DomainProjectionV233,
)
from ecomsre.dta_v2.v23.domain_audit_v233 import project_development_case_v233
from ecomsre.dta_v2.v23.evaluation import ProviderCostV23, _CommonContextV23
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    EvaluationTruthSetV233,
    EvaluationTruthV233,
    load_evaluation_truth_v233,
)
from ecomsre.dta_v2.v23.evaluation_v232 import (
    _run_conflict_aware_total_arm_with_trace_v232,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationOntologyViewSpecV231,
    _residual_graph_v231,
)
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDecisionV233,
    evaluate_irreconcilable_guard_v233,
)


class EvaluationPolicyV233(str, Enum):
    V232_CONFLICT_AWARE_BASELINE = "V232_CONFLICT_AWARE_BASELINE"
    V233_DOMAIN_BOUND = "V233_DOMAIN_BOUND"
    V233_DOMAIN_BOUND_WITNESS_GUARD = "V233_DOMAIN_BOUND_WITNESS_GUARD"


class EvaluationArmRunV233(DtaModelV22):
    schema_version: Literal["dta-v233.evaluation-arm-run.v1"]
    case_id: str
    policy: EvaluationPolicyV233
    case_bytes_sha256: str
    active_view_sha256: str
    bootstrap_memory_sha256: str
    common_memory_sha256: str
    common_read_count: int
    discovery_read_count: int = Field(ge=0, le=3)
    final_disposition: str
    known_root_service: str | None
    no_incident_admissible: bool
    runtime_root_service: str | None
    runtime_broad_domain: ProvisionalFaultDomainV23 | None
    domain_projection: DomainProjectionV233 | None
    guard_decision: IrreconcilableGuardDecisionV233 | None
    guard_read_used: bool
    provisional_report: ProvisionalIncidentReportV233 | None
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]
    residual_anomaly_ids: tuple[str, ...]
    memory_evidence_refs: tuple[str, ...]
    provider_error_code: str | None
    provider_cost: ProviderCostV23
    baseline_v232_run_sha256: str | None
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    action_authority_violations: Literal[0]
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_run(self) -> "EvaluationArmRunV233":
        for values, label in (
            (self.supporting_evidence_refs, "support refs"),
            (self.contradicting_evidence_refs, "contradiction refs"),
            (self.residual_anomaly_ids, "residual IDs"),
            (self.memory_evidence_refs, "memory refs"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 arm {label} are not canonical")
        if not set(
            (*self.supporting_evidence_refs, *self.contradicting_evidence_refs)
        ).issubset(self.memory_evidence_refs):
            raise ValueError("v2.3.3 arm report evidence escapes memory")
        if self.provisional_report is not None:
            if self.provisional_report.action_authority != "NONE":
                raise ValueError("v2.3.3 arm report gained action authority")
            if (
                self.runtime_root_service
                != self.provisional_report.runtime_selected_root_service
                or self.runtime_broad_domain
                is not self.provisional_report.broad_fault_domain
            ):
                raise ValueError("v2.3.3 arm report mechanical binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("v2.3.3 evaluation arm digest differs")
        return self


def _zero_cost() -> ProviderCostV23:
    return ProviderCostV23(
        provider_calls=0,
        protocol_repairs=0,
        transport_retries=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0.0,
    )


def _transport_counters(transport: object | None) -> tuple[int, int, int, float]:
    if transport is None:
        return (0, 0, 0, 0.0)
    return (
        int(getattr(transport, "input_tokens", 0)),
        int(getattr(transport, "output_tokens", 0)),
        int(getattr(transport, "total_tokens", 0)),
        float(getattr(transport, "latency_ms", 0.0)),
    )


def _provider_cost(
    *,
    before: tuple[int, int, int, float],
    transport: object | None,
    calls: int,
    repairs: int,
    retries: int,
) -> ProviderCostV23:
    after = _transport_counters(transport)
    return ProviderCostV23(
        provider_calls=calls,
        protocol_repairs=repairs,
        transport_retries=retries,
        input_tokens=after[0] - before[0],
        output_tokens=after[1] - before[1],
        total_tokens=after[2] - before[2],
        latency_ms=after[3] - before[3],
    )


def _hashed_run(payload: dict[str, Any]) -> EvaluationArmRunV233:
    draft = EvaluationArmRunV233.model_construct(
        **payload,
        run_sha256="0" * 64,
    )
    return EvaluationArmRunV233.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


def _context_specs(
    context: _CommonContextV23,
) -> tuple[EvaluationCaseSpecV231, EvaluationOntologyViewSpecV231]:
    return (
        EvaluationCaseSpecV231(
            case_id=context.case.case_id,
            source_bytes_sha256=context.case.source_bytes_sha256,
            candidate_services=context.case.candidate_services,
            topology_edges=context.case.topology_edges,
            capture=context.case.capture,
        ),
        EvaluationOntologyViewSpecV231(
            case_id=context.case.case_id,
            hidden_mechanism=(
                context.view.hidden_mechanisms[0]
                if context.view.hidden_mechanisms
                else None
            ),
        ),
    )
def run_v232_baseline_arm_v233(
    *,
    context: _CommonContextV23,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV233:
    run, memory, _interpretations, _conflicts = (
        _run_conflict_aware_total_arm_with_trace_v232(
            context,
            provider_transport=provider_transport,
        )
    )
    legacy = run.provisional_report
    roots = () if legacy is None else legacy.suspected_root_services
    support = () if legacy is None else legacy.supporting_evidence_refs
    contradict = () if legacy is None else legacy.contradicting_evidence_refs
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.evaluation-arm-run.v1",
        "case_id": context.case.case_id,
        "policy": EvaluationPolicyV233.V232_CONFLICT_AWARE_BASELINE,
        "case_bytes_sha256": run.case_bytes_sha256,
        "active_view_sha256": run.active_view_sha256,
        "bootstrap_memory_sha256": run.bootstrap_memory_sha256,
        "common_memory_sha256": run.common_memory_sha256,
        "common_read_count": run.common_read_count,
        "discovery_read_count": run.discovery_read_count,
        "final_disposition": run.final_disposition,
        "known_root_service": run.known_root_service,
        "no_incident_admissible": run.no_incident_admissible,
        "runtime_root_service": roots[0] if roots else run.known_root_service,
        "runtime_broad_domain": None if legacy is None else legacy.broad_fault_domain,
        "domain_projection": None,
        "guard_decision": None,
        "guard_read_used": False,
        "provisional_report": None,
        "supporting_evidence_refs": support,
        "contradicting_evidence_refs": contradict,
        "residual_anomaly_ids": run.residual_graph.residual_anomaly_ids,
        "memory_evidence_refs": tuple(
            sorted(item.evidence_ref for item in memory.evidence_refs)
        ),
        "provider_error_code": run.provider_error_code,
        "provider_cost": run.provider_cost,
        "baseline_v232_run_sha256": run.run_sha256,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority_violations": 0,
    }
    return _hashed_run(payload)


_REPORTABLE_V232 = {
    "UNREGISTERED_INCIDENT_SUSPECTED",
    "UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES",
    "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
}


def run_domain_bound_arm_v233(
    *,
    context: _CommonContextV23,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV233:
    baseline, memory, _interpretations, _conflicts = (
        _run_conflict_aware_total_arm_with_trace_v232(
            context,
            provider_transport=None,
        )
    )
    graph = baseline.residual_graph
    discovery_read_count = baseline.discovery_read_count
    report: ProvisionalIncidentReportV233 | None = None
    projection: DomainProjectionV233 | None = None
    guard: IrreconcilableGuardDecisionV233 | None = None
    provider_error: str | None = None
    calls = 0
    repairs = 0
    retries = 0
    before = _transport_counters(provider_transport)
    if baseline.final_disposition in _REPORTABLE_V232:
        runtime_spec, runtime_view = _context_specs(context)
        projection, memory, domain_reads = project_development_case_v233(
            repository_root=Path("."),
            spec=runtime_spec,
            view_spec=runtime_view,
        )
        graph = _residual_graph_v231(context=context, memory=memory)
        discovery_read_count = len(domain_reads)
        if (
            projection.selected_root_service is not None
            and projection.supporting_evidence_refs
        ):
            guard = evaluate_irreconcilable_guard_v233(
                witnesses=(),
                legal_sources=(),
                remaining_reads=0,
                guard_read_used=False,
            )
            hypotheses = build_runtime_hypotheses_v233(
                graph=graph,
                projection=projection,
            )
            unresolved = {"CAUSAL_MECHANISM"}
            if projection.selected_domain is ProvisionalFaultDomainV23.UNKNOWN:
                unresolved.add("BROAD_FAULT_DOMAIN")
            request = build_discovery_synthesis_request_v233(
                graph=graph,
                projection=projection,
                guard=guard,
                hypotheses=hypotheses,
                unresolved_dimensions=tuple(sorted(unresolved)),
                top_shadow_matches=(),
            )
            if provider_transport is None:
                synthesis = deterministic_synthesis_response_v233(request=request)
            else:
                try:
                    outcome = call_discovery_provider_v233(
                        request=request,
                        transport=provider_transport,
                    )
                except DiscoveryProviderProtocolFailureV23:
                    provider_error = "PROTOCOL_FAILED"
                    synthesis = None
                except DiscoveryProviderTransportErrorV23 as exc:
                    provider_error = f"TRANSPORT_FAILED:{exc.safe_code}"
                    synthesis = None
                else:
                    synthesis = outcome.synthesis
                    calls = outcome.provider_calls
                    repairs = outcome.protocol_repairs
                    retries = outcome.transport_retries
            if synthesis is not None:
                report = build_provisional_report_v233(
                    terminal=(
                        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY"
                        if baseline.final_disposition
                        == "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY"
                        else "UNREGISTERED_INCIDENT_SUSPECTED"
                    ),
                    request=request,
                    synthesis=synthesis,
                )
    cost = _provider_cost(
        before=before,
        transport=provider_transport,
        calls=calls,
        repairs=repairs,
        retries=retries,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.evaluation-arm-run.v1",
        "case_id": context.case.case_id,
        "policy": EvaluationPolicyV233.V233_DOMAIN_BOUND,
        "case_bytes_sha256": baseline.case_bytes_sha256,
        "active_view_sha256": baseline.active_view_sha256,
        "bootstrap_memory_sha256": baseline.bootstrap_memory_sha256,
        "common_memory_sha256": baseline.common_memory_sha256,
        "common_read_count": baseline.common_read_count,
        "discovery_read_count": discovery_read_count,
        "final_disposition": (
            "PROVIDER_FAILED" if provider_error is not None else baseline.final_disposition
        ),
        "known_root_service": baseline.known_root_service,
        "no_incident_admissible": baseline.no_incident_admissible,
        "runtime_root_service": (
            baseline.known_root_service
            if projection is None
            else projection.selected_root_service
        ),
        "runtime_broad_domain": (
            None if projection is None else projection.selected_domain
        ),
        "domain_projection": projection,
        "guard_decision": guard,
        "guard_read_used": False,
        "provisional_report": report,
        "supporting_evidence_refs": (
            () if report is None else report.supporting_evidence_refs
        ),
        "contradicting_evidence_refs": (
            () if report is None else report.contradicting_evidence_refs
        ),
        "residual_anomaly_ids": graph.residual_anomaly_ids,
        "memory_evidence_refs": tuple(
            sorted(item.evidence_ref for item in memory.evidence_refs)
        ),
        "provider_error_code": provider_error,
        "provider_cost": cost,
        "baseline_v232_run_sha256": baseline.run_sha256,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority_violations": 0,
    }
    return _hashed_run(payload)


def run_combined_arm_v233(
    *,
    repository_root: Path,
    context: _CommonContextV23,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV233:
    before = _transport_counters(provider_transport)
    runtime_spec, runtime_view = _context_specs(context)
    state: RuntimeDiscoveryStateV233 = run_discovery_case_v233(
        repository_root=repository_root,
        spec=runtime_spec,
        view_spec=runtime_view,
        provider_transport=provider_transport,
    )
    cost = _provider_cost(
        before=before,
        transport=provider_transport,
        calls=state.provider_calls,
        repairs=state.protocol_repairs,
        retries=state.transport_retries,
    )
    report = state.provisional_report
    graph_ids = (
        ()
        if state.synthesis_request is None
        else state.synthesis_request.validation_graph.residual_anomaly_ids
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.evaluation-arm-run.v1",
        "case_id": context.case.case_id,
        "policy": EvaluationPolicyV233.V233_DOMAIN_BOUND_WITNESS_GUARD,
        "case_bytes_sha256": context.case.source_bytes_sha256,
        "active_view_sha256": context.view.view_sha256,
        "bootstrap_memory_sha256": context.bootstrap_memory_sha256,
        "common_memory_sha256": context.memory.memory_sha256,
        "common_read_count": context.common_read_count,
        "discovery_read_count": len(state.discovery_reads),
        "final_disposition": state.terminal,
        "known_root_service": (
            context.admission.admitted_diagnosis.root_service
            if context.admission.admitted_diagnosis is not None
            else None
        ),
        "no_incident_admissible": context.admission.no_incident_admissible,
        "runtime_root_service": (
            context.admission.admitted_diagnosis.root_service
            if context.admission.admitted_diagnosis is not None
            else None
            if state.domain_projection is None
            else state.domain_projection.selected_root_service
        ),
        "runtime_broad_domain": (
            None
            if state.domain_projection is None
            else state.domain_projection.selected_domain
        ),
        "domain_projection": state.domain_projection,
        "guard_decision": state.guard_decision,
        "guard_read_used": state.guard_read_used,
        "provisional_report": report,
        "supporting_evidence_refs": (
            () if report is None else report.supporting_evidence_refs
        ),
        "contradicting_evidence_refs": (
            () if report is None else report.contradicting_evidence_refs
        ),
        "residual_anomaly_ids": graph_ids,
        "memory_evidence_refs": tuple(
            sorted(item.evidence_ref for item in context.memory.evidence_refs)
        )
        if state.synthesis_request is None
        else tuple(
            sorted(
                {
                    ref
                    for item in state.synthesis_request.residual_anomaly_summaries
                    for ref in item.evidence_refs
                }
                | set(
                    state.domain_projection.supporting_evidence_refs
                    if state.domain_projection is not None
                    else ()
                )
                | set(
                    state.domain_projection.contradicting_evidence_refs
                    if state.domain_projection is not None
                    else ()
                )
            )
        ),
        "provider_error_code": state.provider_error_code,
        "provider_cost": cost,
        "baseline_v232_run_sha256": None,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority_violations": 0,
    }
    return _hashed_run(payload)


class LazyTruthStoreV233:
    """Open evaluator truth only after three completed arm digests exist."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._truth: EvaluationTruthSetV233 | None = None
        self._loaded_case_ids: list[str] = []

    @property
    def loaded_case_ids(self) -> tuple[str, ...]:
        return tuple(self._loaded_case_ids)

    def load_case_after_three_arms(
        self,
        *,
        case_id: str,
        runs: tuple[EvaluationArmRunV233, ...],
    ) -> EvaluationTruthV233:
        if tuple(item.policy for item in runs) != tuple(EvaluationPolicyV233):
            raise ValueError("v2.3.3 truth gate lacks the exact three arms")
        if any(item.case_id != case_id or not item.run_sha256 for item in runs):
            raise ValueError("v2.3.3 truth gate received incomplete arm runs")
        if self._truth is None:
            self._truth = load_evaluation_truth_v233(self._path)
        if case_id in set(self._loaded_case_ids):
            raise ValueError("v2.3.3 truth case was already loaded")
        self._loaded_case_ids.append(case_id)
        return self._truth.require(case_id)


__all__ = (
    "EvaluationArmRunV233",
    "EvaluationPolicyV233",
    "LazyTruthStoreV233",
    "run_combined_arm_v233",
    "run_domain_bound_arm_v233",
    "run_v232_baseline_arm_v233",
)
