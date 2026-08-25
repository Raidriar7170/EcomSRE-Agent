"""Versioned total-interpretation evaluation plumbing for DTA v2.3.2."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import ContrastiveResourceActionV225
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, build_memory_views_v22
from ecomsre.dta_v2.v22.practical_runner import _baseline
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    AnomalyInterpretationV232,
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    build_discovery_provider_request_v23,
    call_discovery_provider_v23,
)
from ecomsre.dta_v2.v23.discovery_provider_v231 import (
    build_discovery_provider_request_v231,
    call_discovery_provider_v231,
)
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryReadOutcomeClassV23,
    NegativeCoverageLedgerV23,
    build_discovery_plan_v23,
    record_discovery_outcome_v23,
    resolve_discovery_action_v23,
)
from ecomsre.dta_v2.v23.discovery_runtime import (
    _build_read_outcome_v23,
    _classify_discovery_outcome,
)
from ecomsre.dta_v2.v23.discovery_runtime_v231 import ConflictAwareDiscoveryStateV231
from ecomsre.dta_v2.v23.discovery_runtime_v232 import (
    build_conflict_aware_state_total_v232,
)
from ecomsre.dta_v2.v23.evaluation import (
    EvaluationArmRunV23,
    EvaluationArmV23,
    ProviderCostV23,
    _CommonContextV23,
    _build_arm_run,
    _build_common_context_v23,
    _deterministic_development_report_v23,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationArmRunV231,
    EvaluationCaseSpecV231,
    EvaluationOntologyViewSpecV231,
    _build_treatment_run_v231,
    _normal_resource_services_v231,
    _provider_cost_zero_v231,
    _residual_graph_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.novelty_gate import (
    NoveltyDispositionV23,
    NoveltyGateDecisionV23,
    evaluate_novelty_gate_v23,
)
from ecomsre.dta_v2.v23.novelty_gate_v232 import (
    derive_unresolved_interpretation_conflict_v232,
    interpret_residual_anomalies_v232,
)
from ecomsre.dta_v2.v23.novelty_gate_v231 import NoveltyDispositionV231
from ecomsre.dta_v2.v23.residual_graph import (
    ResidualEvidenceGraphV23,
    build_known_terminal_candidates_v23,
    build_residual_evidence_graph_v23,
)


class EvaluationPolicyV232(str, Enum):
    V23_STRICT_CONFLICT_GATE_TOTAL = "V23_STRICT_CONFLICT_GATE_TOTAL"
    V231_CONFLICT_AWARE_GATE_TOTAL = "V231_CONFLICT_AWARE_GATE_TOTAL"


class ArmRuntimeTraceV232(DtaModelV22):
    schema_version: Literal["dta-v232.arm-runtime-trace.v1"]
    case_id: str
    policy: EvaluationPolicyV232
    registry_sha256: str
    encountered_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    interpretations: tuple[AnomalyInterpretationV232, ...]
    conflict_types: tuple[str, ...]
    final_pre_provider_state: str
    provider_selection_boundary: bool
    provider_calls: Literal[0]
    runtime_exception_count: Literal[0]
    keyerror_count: Literal[0]
    unmapped_anomaly_count: Literal[0]
    schema_failure_count: Literal[0]
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_trace(self) -> "ArmRuntimeTraceV232":
        if self.encountered_anomaly_kinds != tuple(
            sorted(set(self.encountered_anomaly_kinds), key=lambda item: item.value)
        ):
            raise ValueError("v2.3.2 trace anomaly kinds are not canonical")
        interpretation_ids = tuple(
            item.interpretation_sha256 for item in self.interpretations
        )
        if interpretation_ids != tuple(sorted(set(interpretation_ids))):
            raise ValueError("v2.3.2 trace interpretations are not canonical")
        if self.conflict_types != tuple(sorted(set(self.conflict_types))):
            raise ValueError("v2.3.2 trace conflict types are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"trace_sha256"})
        )
        if self.trace_sha256 != expected:
            raise ValueError("v2.3.2 runtime trace digest differs")
        return self


def _record_interpretations_v232(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    seen: dict[str, AnomalyInterpretationV232],
) -> None:
    for interpretation in interpret_residual_anomalies_v232(
        graph=graph,
        memory=memory,
    ):
        seen[interpretation.interpretation_sha256] = interpretation


def _case_state_total_v232(
    *,
    context: _CommonContextV23,
    memory: SalientEvidenceMemoryV22,
    negative_coverage: NegativeCoverageLedgerV23,
) -> tuple[ResidualEvidenceGraphV23, NoveltyGateDecisionV23]:
    known = build_known_terminal_candidates_v23(
        admitted_diagnoses=context.admission.admitted_diagnoses,
    )
    graph = build_residual_evidence_graph_v23(
        candidate_services=context.case.candidate_services,
        generic_anomalies=extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=context.case.candidate_services,
        ),
        known_terminal_candidates=known,
        memory=memory,
    )
    failures = tuple(
        sorted(
            {
                item.source
                for item in negative_coverage.entries
                if item.outcome_class is DiscoveryReadOutcomeClassV23.SOURCE_FAILURE
            },
            key=lambda item: item.value,
        )
    )
    decision = evaluate_novelty_gate_v23(
        graph=graph,
        no_incident_admissible=context.admission.no_incident_admissible,
        remaining_budget_before_discovery=3.0,
        required_source_failures=failures,
        conflicting_evidence=(
            context.admission.conflicting_evidence
            or derive_unresolved_interpretation_conflict_v232(
                graph=graph,
                memory=memory,
                bounded_reads_completed=len(negative_coverage.entries),
            )
        ),
    )
    return graph, decision


def _run_strict_total_arm_with_memory_v232(
    context: _CommonContextV23,
    *,
    provider_transport: Callable[[str], str] | None,
) -> tuple[
    EvaluationArmRunV23,
    SalientEvidenceMemoryV22,
    tuple[AnomalyInterpretationV232, ...],
    tuple[str, ...],
]:
    outcomes = context.outcomes
    memory = context.memory
    negative = NegativeCoverageLedgerV23.empty()
    graph, decision = _case_state_total_v232(
        context=context,
        memory=memory,
        negative_coverage=negative,
    )
    seen_interpretations: dict[str, AnomalyInterpretationV232] = {}
    conflict_types: set[str] = set()
    _record_interpretations_v232(
        graph=graph,
        memory=memory,
        seen=seen_interpretations,
    )
    conflict_types.add(
        "STRICT_HARD_CONFLICT"
        if decision.disposition is NoveltyDispositionV23.CONFLICTING_EVIDENCE
        else "STRICT_NO_HARD_CONFLICT"
    )
    backend = QuerySpecificReplayBackendV22(context.case.capture)
    discovery_reads = 0
    remaining_budget = 3.0
    while decision.disposition is NoveltyDispositionV23.INSUFFICIENT_EVIDENCE or (
        discovery_reads == 0
        and decision.disposition
        in {
            NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
            NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
        }
    ):
        plan = build_discovery_plan_v23(
            catalog=context.catalog,
            graph=graph,
            negative_coverage=negative,
            reads_used=discovery_reads,
            remaining_weighted_budget=remaining_budget,
            target_complete_resource_coverage=True,
            excluded_action_ids=context.common_action_ids,
        )
        if plan is None:
            break
        action = resolve_discovery_action_v23(
            option=plan.selected_action,
            catalog=context.catalog,
            target_complete_resource_coverage=True,
        )
        before_ids = {item.anomaly_id for item in graph.generic_anomalies}
        if isinstance(action, ContrastiveResourceActionV225):
            outcome = _build_read_outcome_v23(
                action=action,
                capture=context.case.capture,
            )
        elif isinstance(action, EvidenceActionV22):
            outcome = backend.execute(action)
        else:
            raise TypeError("v2.3.2 strict discovery action is unsupported")
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(context.case),
            observed_at=context.case.capture.captured_at,
            top_k=64,
        )
        after = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=context.case.candidate_services,
        )
        outcome_class, new_ids = _classify_discovery_outcome(
            outcome=outcome,
            before_anomaly_ids=before_ids,
            after_anomaly_ids={item.anomaly_id for item in after},
        )
        negative = record_discovery_outcome_v23(
            ledger=negative,
            action=plan.selected_action,
            outcome_class=outcome_class,
            new_anomaly_ids=new_ids,
        )
        discovery_reads += 1
        remaining_budget = max(
            0.0,
            remaining_budget - plan.selected_action.weighted_cost,
        )
        graph, decision = _case_state_total_v232(
            context=context,
            memory=memory,
            negative_coverage=negative,
        )
        _record_interpretations_v232(
            graph=graph,
            memory=memory,
            seen=seen_interpretations,
        )
        conflict_types.add(
            "STRICT_HARD_CONFLICT"
            if decision.disposition is NoveltyDispositionV23.CONFLICTING_EVIDENCE
            else "STRICT_NO_HARD_CONFLICT"
        )

    report = None
    provider_error = None
    cost = ProviderCostV23(
        provider_calls=0,
        protocol_repairs=0,
        transport_retries=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0.0,
    )
    if decision.disposition in {
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
        NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
    }:
        if provider_transport is None:
            report = _deterministic_development_report_v23(
                disposition=decision.disposition,
                graph=graph,
                memory=memory,
            )
        else:
            request = build_discovery_provider_request_v23(
                active_ontology=context.view,
                graph=graph,
                negative_coverage=negative,
                last_post_read_delta=None,
                top_shadow_matches=(),
            )
            before_input = int(getattr(provider_transport, "input_tokens", 0))
            before_output = int(getattr(provider_transport, "output_tokens", 0))
            before_total = int(getattr(provider_transport, "total_tokens", 0))
            before_latency = float(getattr(provider_transport, "latency_ms", 0.0))
            try:
                provider_outcome = call_discovery_provider_v23(
                    request=request,
                    memory=memory,
                    transport=provider_transport,
                )
            except DiscoveryProviderProtocolFailureV23:
                provider_error = "PROTOCOL_FAILED"
            except DiscoveryProviderTransportErrorV23 as exc:
                provider_error = f"TRANSPORT_FAILED:{exc.safe_code}"
            else:
                report = provider_outcome.report
                cost = ProviderCostV23(
                    provider_calls=provider_outcome.provider_calls,
                    protocol_repairs=provider_outcome.protocol_repairs,
                    transport_retries=provider_outcome.transport_retries,
                    input_tokens=(
                        int(getattr(provider_transport, "input_tokens", 0))
                        - before_input
                    ),
                    output_tokens=(
                        int(getattr(provider_transport, "output_tokens", 0))
                        - before_output
                    ),
                    total_tokens=(
                        int(getattr(provider_transport, "total_tokens", 0))
                        - before_total
                    ),
                    latency_ms=(
                        float(getattr(provider_transport, "latency_ms", 0.0))
                        - before_latency
                    ),
                )
    run = _build_arm_run(
        context=context,
        arm=EvaluationArmV23.OPEN_WORLD_DISCOVERY,
        graph=graph,
        decision=decision,
        negative=negative,
        discovery_reads=discovery_reads,
        report=report,
        provider_error_code=provider_error,
        provider_cost=cost,
        memory=memory,
    )
    return (
        run,
        memory,
        tuple(
            seen_interpretations[key] for key in sorted(seen_interpretations)
        ),
        tuple(sorted(conflict_types)),
    )


def run_strict_total_arm_v232(
    context: _CommonContextV23,
    *,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV23:
    run, _memory, _interpretations, _conflict_types = (
        _run_strict_total_arm_with_memory_v232(
            context,
            provider_transport=provider_transport,
        )
    )
    return run


def _run_conflict_aware_total_arm_with_trace_v232(
    context: _CommonContextV23,
    *,
    provider_transport: Callable[[str], str] | None,
) -> tuple[
    EvaluationArmRunV231,
    SalientEvidenceMemoryV22,
    tuple[AnomalyInterpretationV232, ...],
    tuple[str, ...],
]:
    """Run the frozen v2.3.1 policy with only interpretation made total."""

    outcomes = context.outcomes
    memory = context.memory
    negative = NegativeCoverageLedgerV23.empty()
    backend = QuerySpecificReplayBackendV22(context.case.capture)
    discovery_reads = 0
    remaining_budget = 3.0
    conflict_read_used = False
    conflict_assessment_before_read = None
    conflict_resolution_outcome_class = None
    state: ConflictAwareDiscoveryStateV231 | None = None
    seen_interpretations: dict[str, AnomalyInterpretationV232] = {}
    conflict_types: set[str] = set()
    while True:
        graph = _residual_graph_v231(context=context, memory=memory)
        executed_action_ids = tuple(sorted({item.action_id for item in outcomes}))
        failures = tuple(
            sorted(
                {
                    item.source
                    for item in negative.entries
                    if item.outcome_class
                    is DiscoveryReadOutcomeClassV23.SOURCE_FAILURE
                },
                key=lambda item: item.value,
            )
        )
        state = build_conflict_aware_state_total_v232(
            graph=graph,
            memory=memory,
            catalog=context.catalog,
            topology_edges=context.case.topology_edges,
            no_incident_admissible=context.admission.no_incident_admissible,
            negative_coverage=negative,
            discovery_reads_used=discovery_reads,
            remaining_weighted_budget=remaining_budget,
            conflict_resolution_read_used=conflict_read_used,
            normal_resource_services=_normal_resource_services_v231(
                graph=graph,
                memory=memory,
            ),
            required_source_failures=failures,
            excluded_action_ids=executed_action_ids,
        )
        _record_interpretations_v232(
            graph=graph,
            memory=memory,
            seen=seen_interpretations,
        )
        conflict_types.add(state.conflict_assessment.conflict_type.value)
        selected = None
        is_conflict_read = False
        if (
            state.novelty_decision.disposition
            is NoveltyDispositionV231.DISCOVERY_READ_REQUIRED
        ):
            if state.discriminating_plan is None:
                raise ValueError("v2.3.2 conflict read lacks a plan")
            selected = state.discriminating_plan.selected_action
            is_conflict_read = True
        elif (
            (
                state.novelty_decision.disposition
                is NoveltyDispositionV231.INSUFFICIENT_EVIDENCE
                or (
                    discovery_reads == 0
                    and state.novelty_decision.disposition
                    is NoveltyDispositionV231.UNREGISTERED_INCIDENT_SUSPECTED
                )
            )
            and discovery_reads < 3
        ):
            coverage_plan = build_discovery_plan_v23(
                catalog=context.catalog,
                graph=graph,
                negative_coverage=negative,
                reads_used=discovery_reads,
                remaining_weighted_budget=remaining_budget,
                target_complete_resource_coverage=True,
                excluded_action_ids=executed_action_ids,
            )
            if coverage_plan is not None:
                selected = coverage_plan.selected_action
        if selected is None:
            break
        if is_conflict_read:
            conflict_assessment_before_read = state.conflict_assessment
        action = resolve_discovery_action_v23(
            option=selected,
            catalog=context.catalog,
            target_complete_resource_coverage=True,
        )
        before_ids = {item.anomaly_id for item in graph.generic_anomalies}
        if isinstance(action, ContrastiveResourceActionV225):
            outcome = _build_read_outcome_v23(
                action=action,
                capture=context.case.capture,
            )
        elif isinstance(action, EvidenceActionV22):
            outcome = backend.execute(action)
        else:
            raise TypeError("v2.3.2 treatment discovery action is unsupported")
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(context.case),
            observed_at=context.case.capture.captured_at,
            top_k=64,
        )
        after = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=context.case.candidate_services,
        )
        outcome_class, new_ids = _classify_discovery_outcome(
            outcome=outcome,
            before_anomaly_ids=before_ids,
            after_anomaly_ids={item.anomaly_id for item in after},
        )
        if is_conflict_read:
            conflict_resolution_outcome_class = outcome_class
        negative = record_discovery_outcome_v23(
            ledger=negative,
            action=selected,
            outcome_class=outcome_class,
            new_anomaly_ids=new_ids,
        )
        discovery_reads += 1
        remaining_budget = max(0.0, remaining_budget - selected.weighted_cost)
        conflict_read_used = conflict_read_used or is_conflict_read

    if state is None:
        raise ValueError("v2.3.2 treatment did not build an initial state")
    report = state.provisional_report
    provider_error = None
    cost = _provider_cost_zero_v231()
    if report is not None and provider_transport is not None:
        hypotheses = state.competing_hypothesis_set
        if hypotheses is not None:
            request = build_discovery_provider_request_v231(
                active_ontology=context.view,
                graph=state.residual_graph,
                assessment=state.conflict_assessment,
                hypothesis_set=hypotheses,
                top_shadow_matches=(),
            )
            before_input = int(getattr(provider_transport, "input_tokens", 0))
            before_output = int(getattr(provider_transport, "output_tokens", 0))
            before_total = int(getattr(provider_transport, "total_tokens", 0))
            before_latency = float(getattr(provider_transport, "latency_ms", 0.0))
            before_calls = int(getattr(provider_transport, "provider_calls", 0))
            before_repairs = int(getattr(provider_transport, "protocol_repairs", 0))
            before_retries = int(getattr(provider_transport, "transport_retries", 0))
            outcome_calls = 0
            outcome_repairs = 0
            outcome_retries = 0
            try:
                provider_outcome = call_discovery_provider_v231(
                    request=request,
                    transport=provider_transport,
                )
            except DiscoveryProviderProtocolFailureV23:
                provider_error = "PROTOCOL_FAILED"
                report = None
            except DiscoveryProviderTransportErrorV23 as exc:
                provider_error = f"TRANSPORT_FAILED:{exc.safe_code}"
                report = None
            else:
                report = provider_outcome.report
                outcome_calls = provider_outcome.provider_calls
                outcome_repairs = provider_outcome.protocol_repairs
                outcome_retries = provider_outcome.transport_retries
            cost = ProviderCostV23(
                provider_calls=max(
                    outcome_calls,
                    int(getattr(provider_transport, "provider_calls", before_calls))
                    - before_calls,
                ),
                protocol_repairs=max(
                    outcome_repairs,
                    int(
                        getattr(
                            provider_transport,
                            "protocol_repairs",
                            before_repairs,
                        )
                    )
                    - before_repairs,
                ),
                transport_retries=max(
                    outcome_retries,
                    int(
                        getattr(
                            provider_transport,
                            "transport_retries",
                            before_retries,
                        )
                    )
                    - before_retries,
                ),
                input_tokens=(
                    int(getattr(provider_transport, "input_tokens", 0))
                    - before_input
                ),
                output_tokens=(
                    int(getattr(provider_transport, "output_tokens", 0))
                    - before_output
                ),
                total_tokens=(
                    int(getattr(provider_transport, "total_tokens", 0))
                    - before_total
                ),
                latency_ms=(
                    float(getattr(provider_transport, "latency_ms", 0.0))
                    - before_latency
                ),
            )
    run = _build_treatment_run_v231(
        context=context,
        state=state,
        negative=negative,
        memory=memory,
        discovery_reads=discovery_reads,
        conflict_resolution_read_used=conflict_read_used,
        conflict_assessment_before_read=conflict_assessment_before_read,
        conflict_resolution_outcome_class=conflict_resolution_outcome_class,
        report=report,
        provider_error_code=provider_error,
        provider_cost=cost,
    )
    return (
        run,
        memory,
        tuple(
            seen_interpretations[key] for key in sorted(seen_interpretations)
        ),
        tuple(sorted(conflict_types)),
    )


def run_conflict_aware_total_arm_v232(
    context: _CommonContextV23,
    *,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV231:
    run, _memory, _interpretations, _conflict_types = (
        _run_conflict_aware_total_arm_with_trace_v232(
            context,
            provider_transport=provider_transport,
        )
    )
    return run


def _build_runtime_trace_v232(
    *,
    run: EvaluationArmRunV23 | EvaluationArmRunV231,
    policy: EvaluationPolicyV232,
    interpretations: tuple[AnomalyInterpretationV232, ...],
    conflict_types: tuple[str, ...],
) -> ArmRuntimeTraceV232:
    if run.provider_cost.provider_calls != 0:
        raise ValueError("v2.3.2 deterministic preflight reached the Provider")
    canonical_interpretations = tuple(
        sorted(
            {item.interpretation_sha256: item for item in interpretations}.values(),
            key=lambda item: item.interpretation_sha256,
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.arm-runtime-trace.v1",
        "case_id": run.case_id,
        "policy": policy,
        "registry_sha256": (
            DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.registry_sha256
        ),
        "encountered_anomaly_kinds": tuple(
            sorted(
                {item.anomaly_kind for item in canonical_interpretations},
                key=lambda item: item.value,
            )
        ),
        "interpretations": canonical_interpretations,
        "conflict_types": tuple(sorted(set(conflict_types))),
        "final_pre_provider_state": run.final_disposition,
        "provider_selection_boundary": run.provisional_report is not None,
        "provider_calls": 0,
        "runtime_exception_count": 0,
        "keyerror_count": 0,
        "unmapped_anomaly_count": 0,
        "schema_failure_count": 0,
    }
    draft = ArmRuntimeTraceV232.model_construct(**payload, trace_sha256="0" * 64)
    return ArmRuntimeTraceV232.model_validate(
        {
            **payload,
            "trace_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"trace_sha256"})
            ),
        }
    )


def run_evaluation_policy_with_trace_v232(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
    policy: EvaluationPolicyV232,
) -> tuple[EvaluationArmRunV23 | EvaluationArmRunV231, ArmRuntimeTraceV232]:
    if view_spec.case_id != spec.case_id:
        raise ValueError("v2.3.2 evaluation case and ontology view differ")
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    run: EvaluationArmRunV23 | EvaluationArmRunV231
    if policy is EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL:
        run, _memory, interpretations, conflict_types = (
            _run_strict_total_arm_with_memory_v232(
                context,
                provider_transport=None,
            )
        )
    else:
        run, _memory, interpretations, conflict_types = (
            _run_conflict_aware_total_arm_with_trace_v232(
                context,
                provider_transport=None,
            )
        )
    return run, _build_runtime_trace_v232(
        run=run,
        policy=policy,
        interpretations=interpretations,
        conflict_types=conflict_types,
    )


def run_evaluation_policy_v232(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
    policy: EvaluationPolicyV232,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV23 | EvaluationArmRunV231:
    if view_spec.case_id != spec.case_id:
        raise ValueError("v2.3.2 evaluation case and ontology view differ")
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    if policy is EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL:
        return run_strict_total_arm_v232(
            context,
            provider_transport=provider_transport,
        )
    return run_conflict_aware_total_arm_v232(
        context,
        provider_transport=provider_transport,
    )


__all__ = (
    "ArmRuntimeTraceV232",
    "EvaluationPolicyV232",
    "run_conflict_aware_total_arm_v232",
    "run_evaluation_policy_v232",
    "run_evaluation_policy_with_trace_v232",
    "run_strict_total_arm_v232",
)
