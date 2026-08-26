"""Integrated runtime-owned domain, witness, and synthesis lane for DTA v2.3.3."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import StrictBool

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import ContrastiveResourceActionV225
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, build_memory_views_v22
from ecomsre.dta_v2.v22.practical_runner import _baseline
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.contradiction_witness_v233 import (
    WitnessStrengthV233,
    build_contradiction_witnesses_v233,
)
from ecomsre.dta_v2.v23.contracts_v233 import (
    DiscoverySynthesisRequestV233,
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
from ecomsre.dta_v2.v23.discovery_runtime import _build_read_outcome_v23
from ecomsre.dta_v2.v23.domain_audit_v233 import _next_development_read_v233
from ecomsre.dta_v2.v23.domain_projection_v233 import (
    DomainProjectionStatusV233,
    DomainProjectionV233,
    project_domain_v233,
)
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationOntologyViewSpecV231,
    _residual_graph_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDecisionV233,
    IrreconcilableGuardDispositionV233,
    evaluate_irreconcilable_guard_v233,
)
from ecomsre.dta_v2.v23.witness_audit_v233 import _action, _legal_sources


class RuntimeDiscoveryReadV233(DtaModelV22):
    source: EvidenceSourceV22
    target_services: tuple[str, ...]
    reason: str
    guard_directed: StrictBool


class RuntimeDiscoveryStateV233(DtaModelV22):
    schema_version: Literal["dta-v233.runtime-discovery-state.v1"]
    case_id: str
    terminal: Literal[
        "REGISTERED_KNOWN",
        "NO_INCIDENT",
        "CONFLICTING_EVIDENCE",
        "INSUFFICIENT_EVIDENCE",
        "UNREGISTERED_INCIDENT_SUSPECTED",
        "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
        "PROVIDER_PROTOCOL_FAILED",
        "PROVIDER_TRANSPORT_FAILED",
    ]
    discovery_reads: tuple[RuntimeDiscoveryReadV233, ...]
    guard_read_used: StrictBool
    guard_decision: IrreconcilableGuardDecisionV233 | None
    domain_projection: DomainProjectionV233 | None
    synthesis_request: DiscoverySynthesisRequestV233 | None
    provisional_report: ProvisionalIncidentReportV233 | None
    provider_calls: int
    protocol_repairs: int
    transport_retries: int
    provider_error_code: str | None


def _execute_runtime_read_v233(
    *,
    action: EvidenceActionV22 | ContrastiveResourceActionV225,
    context: Any,
    backend: QuerySpecificReplayBackendV22,
    outcomes: tuple[object, ...],
) -> tuple[tuple[object, ...], SalientEvidenceMemoryV22]:
    if isinstance(action, ContrastiveResourceActionV225):
        outcome = _build_read_outcome_v23(
            action=action,
            capture=context.case.capture,
        )
    else:
        outcome = backend.execute(action)
    updated = (*outcomes, outcome)
    memory, _ = build_memory_views_v22(
        outcomes=updated,  # type: ignore[arg-type]
        baseline=_baseline(context.case),
        observed_at=context.case.capture.captured_at,
        top_k=64,
    )
    return updated, memory


def _state(
    *,
    case_id: str,
    terminal: str,
    reads: list[RuntimeDiscoveryReadV233],
    guard_read_used: bool,
    guard: IrreconcilableGuardDecisionV233 | None,
    projection: DomainProjectionV233 | None,
    request: DiscoverySynthesisRequestV233 | None = None,
    report: ProvisionalIncidentReportV233 | None = None,
    provider_calls: int = 0,
    protocol_repairs: int = 0,
    transport_retries: int = 0,
    provider_error_code: str | None = None,
) -> RuntimeDiscoveryStateV233:
    return RuntimeDiscoveryStateV233.model_validate(
        {
            "schema_version": "dta-v233.runtime-discovery-state.v1",
            "case_id": case_id,
            "terminal": terminal,
            "discovery_reads": tuple(reads),
            "guard_read_used": guard_read_used,
            "guard_decision": guard,
            "domain_projection": projection,
            "synthesis_request": request,
            "provisional_report": report,
            "provider_calls": provider_calls,
            "protocol_repairs": protocol_repairs,
            "transport_retries": transport_retries,
            "provider_error_code": provider_error_code,
        }
    )


def run_discovery_case_v233(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
    provider_transport: Callable[[str], str] | None,
) -> RuntimeDiscoveryStateV233:
    """Run witness first, then projection, then minimal Provider synthesis."""

    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    reads: list[RuntimeDiscoveryReadV233] = []
    if len(context.admission.admitted_diagnoses) == 1:
        return _state(
            case_id=spec.case_id,
            terminal="REGISTERED_KNOWN",
            reads=reads,
            guard_read_used=False,
            guard=None,
            projection=None,
        )
    if context.admission.no_incident_admissible:
        return _state(
            case_id=spec.case_id,
            terminal="NO_INCIDENT",
            reads=reads,
            guard_read_used=False,
            guard=None,
            projection=None,
        )

    outcomes: tuple[object, ...] = tuple(context.outcomes)
    memory = context.memory
    backend = QuerySpecificReplayBackendV22(case.capture)
    guard_read_used = False
    exclusive_root_read_used = False
    guard: IrreconcilableGuardDecisionV233 | None = None
    projection: DomainProjectionV233 | None = None
    graph = _residual_graph_v231(context=context, memory=memory)

    while True:
        graph = _residual_graph_v231(context=context, memory=memory)
        witnesses = build_contradiction_witnesses_v233(
            graph=graph,
            memory=memory,
            observation_scope=spec.case_id,
        )
        guard = evaluate_irreconcilable_guard_v233(
            witnesses=witnesses,
            legal_sources=_legal_sources(context, outcomes),
            remaining_reads=3 - len(reads),
            guard_read_used=guard_read_used,
        )
        if guard.disposition is IrreconcilableGuardDispositionV233.IRRECONCILABLE:
            return _state(
                case_id=spec.case_id,
                terminal="CONFLICTING_EVIDENCE",
                reads=reads,
                guard_read_used=guard_read_used,
                guard=guard,
                projection=None,
            )
        if (
            guard.disposition
            is IrreconcilableGuardDispositionV233.INSUFFICIENT_COVERAGE
        ):
            return _state(
                case_id=spec.case_id,
                terminal="INSUFFICIENT_EVIDENCE",
                reads=reads,
                guard_read_used=guard_read_used,
                guard=guard,
                projection=None,
            )
        if guard.disposition is IrreconcilableGuardDispositionV233.RESOLVABLE:
            source = guard.required_additional_reads[0]
            targets = tuple(
                dict.fromkeys(
                    service
                    for witness in guard.witnesses
                    for service in witness.services
                    if service in set(case.candidate_services)
                )
            )
            action = _action(
                context=context,
                source=source,
                targets=targets,
                outcomes=outcomes,
            )
            if action is None or len(reads) >= 3:
                guard = evaluate_irreconcilable_guard_v233(
                    witnesses=witnesses,
                    legal_sources=(),
                    remaining_reads=0,
                    guard_read_used=guard_read_used,
                )
                return _state(
                    case_id=spec.case_id,
                    terminal="CONFLICTING_EVIDENCE",
                    reads=reads,
                    guard_read_used=guard_read_used,
                    guard=guard,
                    projection=None,
                )
            outcomes, memory = _execute_runtime_read_v233(
                action=action,
                context=context,
                backend=backend,
                outcomes=outcomes,
            )
            reads.append(
                RuntimeDiscoveryReadV233(
                    source=action.source,
                    target_services=action.target_services,
                    reason="TEST_WITNESS_CLOSURE",
                    guard_directed=True,
                )
            )
            guard_read_used = True
            continue

        error_services = tuple(
            sorted(
                {
                    item.service
                    for item in graph.generic_anomalies
                    if item.kind is GenericAnomalyKindV23.METRIC_ERROR_OUTLIER
                }
            )
        )
        if (
            not exclusive_root_read_used
            and not any(
                item.strength is WitnessStrengthV233.STRONG
                for item in witnesses
            )
            and len(error_services) >= 2
            and len(reads) < 3
        ):
            trace_action = _action(
                context=context,
                source=EvidenceSourceV22.TRACES,
                targets=error_services,
                outcomes=outcomes,
            )
            exclusive_root_read_used = True
            if trace_action is not None:
                outcomes, memory = _execute_runtime_read_v233(
                    action=trace_action,
                    context=context,
                    backend=backend,
                    outcomes=outcomes,
                )
                reads.append(
                    RuntimeDiscoveryReadV233(
                        source=trace_action.source,
                        target_services=trace_action.target_services,
                        reason="LOCALIZE_EXCLUSIVE_ROOTS",
                        guard_directed=False,
                    )
                )
                continue

        projection = project_domain_v233(graph=graph, memory=memory)
        if (
            projection.status is DomainProjectionStatusV233.RESOLVED
            or len(reads) == 3
        ):
            break
        planned = _next_development_read_v233(
            context=context,
            graph=graph,
            projection=projection,
            outcomes=outcomes,
        )
        if planned is None:
            break
        planned_action, reason = planned
        if not isinstance(
            planned_action,
            (EvidenceActionV22, ContrastiveResourceActionV225),
        ):
            raise TypeError("v2.3.3 discovery read action is unsupported")
        outcomes, memory = _execute_runtime_read_v233(
            action=planned_action,
            context=context,
            backend=backend,
            outcomes=outcomes,
        )
        reads.append(
            RuntimeDiscoveryReadV233(
                source=planned_action.source,
                target_services=planned_action.target_services,
                reason=reason.value,
                guard_directed=False,
            )
        )

    assert guard is not None
    assert projection is not None
    if projection.selected_root_service is None or not projection.supporting_evidence_refs:
        return _state(
            case_id=spec.case_id,
            terminal="INSUFFICIENT_EVIDENCE",
            reads=reads,
            guard_read_used=guard_read_used,
            guard=guard,
            projection=projection,
        )
    hypotheses = build_runtime_hypotheses_v233(
        graph=graph,
        projection=projection,
    )
    unresolved = {"CAUSAL_MECHANISM"}
    if projection.status is not DomainProjectionStatusV233.RESOLVED:
        unresolved.add("BROAD_FAULT_DOMAIN")
    request = build_discovery_synthesis_request_v233(
        graph=graph,
        projection=projection,
        guard=guard,
        hypotheses=hypotheses,
        unresolved_dimensions=tuple(sorted(unresolved)),
        top_shadow_matches=(),
    )
    provider_calls = 0
    protocol_repairs = 0
    transport_retries = 0
    if provider_transport is None:
        synthesis = deterministic_synthesis_response_v233(request=request)
    else:
        try:
            outcome = call_discovery_provider_v233(
                request=request,
                transport=provider_transport,
            )
        except DiscoveryProviderProtocolFailureV23:
            return _state(
                case_id=spec.case_id,
                terminal="PROVIDER_PROTOCOL_FAILED",
                reads=reads,
                guard_read_used=guard_read_used,
                guard=guard,
                projection=projection,
                request=request,
                provider_error_code="PROTOCOL_FAILED",
            )
        except DiscoveryProviderTransportErrorV23 as exc:
            return _state(
                case_id=spec.case_id,
                terminal="PROVIDER_TRANSPORT_FAILED",
                reads=reads,
                guard_read_used=guard_read_used,
                guard=guard,
                projection=projection,
                request=request,
                provider_error_code=f"TRANSPORT_FAILED:{exc.safe_code}",
            )
        synthesis = outcome.synthesis
        provider_calls = outcome.provider_calls
        protocol_repairs = outcome.protocol_repairs
        transport_retries = outcome.transport_retries
    terminal: Literal["UNREGISTERED_INCIDENT_SUSPECTED"] = (
        "UNREGISTERED_INCIDENT_SUSPECTED"
    )
    report = build_provisional_report_v233(
        terminal=terminal,
        request=request,
        synthesis=synthesis,
    )
    return _state(
        case_id=spec.case_id,
        terminal=terminal,
        reads=reads,
        guard_read_used=guard_read_used,
        guard=guard,
        projection=projection,
        request=request,
        report=report,
        provider_calls=provider_calls,
        protocol_repairs=protocol_repairs,
        transport_retries=transport_retries,
    )


__all__ = (
    "RuntimeDiscoveryReadV233",
    "RuntimeDiscoveryStateV233",
    "run_discovery_case_v233",
)
