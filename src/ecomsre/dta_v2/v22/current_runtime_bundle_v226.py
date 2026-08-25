"""Runtime-owned target-complete Resources bundle arm for DTA v2.2.6."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ValidationError

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
)
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    ActionReadBackendV225,
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_bootstrap_v226 import (
    build_real_fault_baseline_profile_v226,
    build_real_fault_canonical_bootstrap_v226,
    real_fault_run_id_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultOpaqueCaptureV1,
    require_provider_payload_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultArmRunV226,
    RealFaultArmStatusV226,
    RealFaultPredictionV226,
    RealFaultStudyArmV226,
    build_real_fault_arm_run_v226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionProviderV226,
    build_real_fault_selection_surface_v226,
)
from ecomsre.dta_v2.v22.real_fault_selection_provider_v226 import (
    RealFaultSelectionProtocolFailureV226,
)
from ecomsre.dta_v2.v22.real_fault_stage_trace_v226 import (
    RealFaultSafeFailureCodeV226,
    RealFaultStageV226,
    build_failed_real_fault_trace_v226,
    build_successful_real_fault_trace_v226,
)
from ecomsre.dta_v2.v22.real_fault_terminalizer_v226 import (
    RealFaultAdmittedTerminalV226,
    RealFaultTerminalKindV226,
    terminalize_real_fault_v226,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayTargetCoverageModeV225,
    build_replay_target_coverage_v225,
)
from ecomsre.dta_v2.v22.resource_comparison_set_v226 import (
    build_resource_comparison_set_v226,
)


_SUCCESS_STATUSES = {
    ReadSourceStatusV22.SUCCESS_EMPTY,
    ReadSourceStatusV22.SUCCESS_NONEMPTY,
}


def _failed_prediction_v226() -> RealFaultPredictionV226:
    return RealFaultPredictionV226(
        schema_version="dta-v226-real-fault.prediction.v1",
        terminal="FAILED",
        terminal_id=None,
        root_service_alias=None,
        fault_domain=None,
        mechanism=None,
        supporting_evidence_refs=(),
        evidence_clause_valid=False,
    )


def _prediction_v226(
    terminal: RealFaultAdmittedTerminalV226,
) -> RealFaultPredictionV226:
    label = cast(
        Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN"],
        {
            RealFaultTerminalKindV226.CPU_SATURATION: "DIAGNOSED",
            RealFaultTerminalKindV226.NO_INCIDENT: "NO_INCIDENT",
            RealFaultTerminalKindV226.ABSTAIN: "ABSTAIN",
        }[terminal.terminal_kind],
    )
    return RealFaultPredictionV226(
        schema_version="dta-v226-real-fault.prediction.v1",
        terminal=label,
        terminal_id=terminal.terminal_id,
        root_service_alias=terminal.root_service_alias,
        fault_domain=terminal.fault_domain,
        mechanism=terminal.mechanism,
        supporting_evidence_refs=terminal.supporting_evidence_refs,
        evidence_clause_valid=terminal.evidence_clause_valid,
    )


def _safe_validation_codes(error: BaseException) -> tuple[str, ...]:
    if not isinstance(error, ValidationError):
        return ()
    return tuple(sorted({str(item["type"]) for item in error.errors()}))


def _failure_code(stage: RealFaultStageV226) -> RealFaultSafeFailureCodeV226:
    return {
        RealFaultStageV226.INPUT_VALIDATION: RealFaultSafeFailureCodeV226.INPUT_INVALID,
        RealFaultStageV226.BOOTSTRAP_ACTION_BUILD: RealFaultSafeFailureCodeV226.BOOTSTRAP_ACTION_MISSING,
        RealFaultStageV226.BOOTSTRAP_DISPATCH: RealFaultSafeFailureCodeV226.BOOTSTRAP_READ_FAILED,
        RealFaultStageV226.BOOTSTRAP_MEMORY_BUILD: RealFaultSafeFailureCodeV226.MEMORY_CONSTRUCTION_FAILED,
        RealFaultStageV226.BASELINE_PROFILE_BUILD: RealFaultSafeFailureCodeV226.BASELINE_PROFILE_INVALID,
        RealFaultStageV226.HYPOTHESIS_CATALOG_BUILD: RealFaultSafeFailureCodeV226.INTERNAL_CONTRACT_FAILURE,
        RealFaultStageV226.GAP_GRAPH_BUILD: RealFaultSafeFailureCodeV226.GAP_GRAPH_CONSTRUCTION_FAILED,
        RealFaultStageV226.ACTION_CATALOG_BUILD: RealFaultSafeFailureCodeV226.ACTION_CATALOG_EMPTY,
        RealFaultStageV226.RESOURCE_COMPARISON_SET_BUILD: RealFaultSafeFailureCodeV226.RESOURCE_COMPARISON_SET_EMPTY,
        RealFaultStageV226.BUNDLE_BUILD: RealFaultSafeFailureCodeV226.BUNDLE_NOT_ELIGIBLE,
        RealFaultStageV226.BUNDLE_DISPATCH: RealFaultSafeFailureCodeV226.BUNDLE_READ_FAILED,
        RealFaultStageV226.POST_READ_MEMORY_BUILD: RealFaultSafeFailureCodeV226.MEMORY_CONSTRUCTION_FAILED,
        RealFaultStageV226.POST_READ_GAP_BUILD: RealFaultSafeFailureCodeV226.GAP_GRAPH_CONSTRUCTION_FAILED,
        RealFaultStageV226.TERMINAL_CATALOG_BUILD: RealFaultSafeFailureCodeV226.TERMINAL_CATALOG_EMPTY,
        RealFaultStageV226.PROVIDER_TERMINAL_SELECTION: RealFaultSafeFailureCodeV226.PROVIDER_OUTPUT_INVALID,
        RealFaultStageV226.TERMINAL_BIND: RealFaultSafeFailureCodeV226.TERMINAL_ALIAS_INVALID,
        RealFaultStageV226.COMPLETE: RealFaultSafeFailureCodeV226.INTERNAL_CONTRACT_FAILURE,
    }[stage]


def run_current_runtime_bundle_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    model_id: str,
    provider: RealFaultSelectionProviderV226,
    _action_backend: ActionReadBackendV225 | None = None,
) -> RealFaultArmRunV226:
    """Run one read-only Current arm without exposing any write surface."""

    arm = RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
    completed: list[RealFaultStageV226] = []
    active = RealFaultStageV226.INPUT_VALIDATION
    run_id = real_fault_run_id_v226(capture)
    backend = _action_backend or RealFaultActionReadBackendV225.snapshot(
        capture=capture,
        run_id=run_id,
    )
    status = RealFaultArmStatusV226.RUNNER_FAILED
    prediction = _failed_prediction_v226()
    strictly_ambiguous: bool | None = None
    comparison_set_size = 0
    bundle_eligible = False
    bundle_dispatched = False
    bundle_target_count = 0
    resources_selected = False
    resource_read_shape = "NONE"
    all_candidates_covered = False
    semantic_actions = 0
    target_reads = 0
    first_useful_ordinal: int | None = None
    predicate_yield_count = 0
    empty_read_count = 0
    provider_turns = 0
    provider_calls = 0
    first_pass = False
    post_repair = False
    protocol_repairs = 0
    input_tokens = output_tokens = total_tokens = 0
    latency_ms = 0.0
    transport_retries = 0
    protocol_failures = runner_failures = transport_failures = 0

    try:
        if capture.alias_map_name != baseline_capture.alias_map_name:
            raise ValueError("current case and baseline alias maps differ")
        if capture.candidate_aliases != baseline_capture.candidate_aliases:
            raise ValueError("current case and baseline candidates differ")
        if not model_id.strip():
            raise ValueError("model ID is empty")
        completed.append(active)

        topology = StaticTopologyV22.build(
            services=capture.candidate_aliases,
            edges=(),
        )
        active = RealFaultStageV226.BOOTSTRAP_ACTION_BUILD
        bootstrap, outcomes = build_real_fault_canonical_bootstrap_v226(
            capture=capture,
            baseline_capture=baseline_capture,
            backend=backend,
        )
        completed.append(active)
        active = RealFaultStageV226.BOOTSTRAP_DISPATCH
        completed.append(active)

        active = RealFaultStageV226.BOOTSTRAP_MEMORY_BUILD
        baseline = build_real_fault_baseline_profile_v226(baseline_capture)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=baseline,
            observed_at=capture.capture.captured_at,
            top_k=64,
        )
        if memory.memory_sha256 != bootstrap.memory_sha256:
            raise ValueError("canonical bootstrap memory differs")
        completed.append(active)

        active = RealFaultStageV226.BASELINE_PROFILE_BUILD
        if baseline.baseline_sha256 != bootstrap.baseline_sha256:
            raise ValueError("canonical bootstrap baseline differs")
        completed.append(active)

        active = RealFaultStageV226.HYPOTHESIS_CATALOG_BUILD
        hypotheses = build_hypothesis_catalog_v22(
            candidate_services=capture.candidate_aliases
        )
        completed.append(active)

        policy = build_effective_support_policy_v222()
        active = RealFaultStageV226.GAP_GRAPH_BUILD
        graph = build_gap_graph_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=memory,
            topology_edges=(),
            planner_focus_hypothesis_id=None,
            prior_negative_coverage=(),
        )
        completed.append(active)

        active = RealFaultStageV226.ACTION_CATALOG_BUILD
        catalog = build_action_catalog_v22(
            candidate_services=capture.candidate_aliases,
            topology=topology,
            capability_registry=build_default_tool_capability_registry_v22(),
            executed_action_ids=tuple(
                item.action_id for item in bootstrap.read_bindings
            ),
            remaining_budget=3.0,
        )
        resource_actions = tuple(
            item
            for item in catalog.registry_actions
            if item.source is EvidenceSourceV22.RESOURCES
            and len(item.target_services) == 1
        )
        if len(resource_actions) != len(capture.candidate_aliases):
            raise ValueError("Resources action catalog is not target-complete")
        completed.append(active)

        coverage = build_replay_target_coverage_v225(
            source=EvidenceSourceV22.RESOURCES,
            candidate_services=capture.candidate_aliases,
            covered_target_services=tuple(
                sorted(item.service for item in capture.capture.resources)
            ),
        )
        active = RealFaultStageV226.RESOURCE_COMPARISON_SET_BUILD
        bundle = contrastive_resource_action_if_eligible_v225(
            coverage=coverage,
            resources_enabled=not any(
                item.source is EvidenceSourceV22.RESOURCES
                for item in capture.capture.source_failures
            ),
            unresolved_resource_hypotheses=sum(
                not item.complete
                and any(
                    gap.predicate_kind.value.startswith("RESOURCE_")
                    for clause in item.clauses
                    for gap in clause.missing_requirements
                )
                for item in graph.hypotheses
            ),
            remaining_budget=3.0,
            bundle_mode=True,
        )
        comparison_set = build_resource_comparison_set_v226(
            memory=memory,
            gap_graph=graph,
            candidate_services=capture.candidate_aliases,
            topology_edges=(),
            individual_actions=resource_actions,
            bundle_action=bundle,
            target_complete=(
                coverage.coverage_mode
                is ReplayTargetCoverageModeV225.TARGET_COMPLETE
            ),
            covered_targets=(),
        )
        if comparison_set is None:
            raise ValueError("target-complete Resources comparison set is absent")
        strictly_ambiguous = comparison_set.strictly_ambiguous
        comparison_set_size = len(comparison_set.candidate_services)
        completed.append(active)

        active = RealFaultStageV226.BUNDLE_BUILD
        if bundle is None or bundle.action_id != comparison_set.bundle_action_id:
            raise ValueError("target-complete Resources bundle is absent")
        bundle_eligible = True
        bundle_target_count = len(bundle.target_services)
        completed.append(active)

        active = RealFaultStageV226.BUNDLE_DISPATCH
        resource_outcome = backend.execute(bundle)
        bundle_dispatched = True
        resources_selected = True
        resource_read_shape = "MULTI_TARGET"
        semantic_actions = 1
        target_reads = len(bundle.target_services)
        empty_read_count = int(
            resource_outcome.status is ReadSourceStatusV22.SUCCESS_EMPTY
        )
        completed.append(active)

        active = RealFaultStageV226.POST_READ_MEMORY_BUILD
        post_memory, _ = build_memory_views_v22(
            outcomes=(*outcomes, resource_outcome),
            baseline=baseline,
            observed_at=capture.capture.captured_at,
            top_k=64,
        )
        before_predicates = {item.predicate_id for item in memory.predicates}
        predicate_yield_count = sum(
            item.predicate_id not in before_predicates
            for item in post_memory.predicates
        )
        first_useful_ordinal = 1 if post_memory.memory_sha256 != memory.memory_sha256 else None
        covered_targets = tuple(
            sorted(
                {
                    item.service
                    for item in resource_outcome.records
                    if isinstance(item, ResourceUsageRecordV22)
                    and item.service in set(capture.candidate_aliases)
                }
            )
        )
        all_candidates_covered = (
            resource_outcome.status in _SUCCESS_STATUSES
            and covered_targets == capture.candidate_aliases
        )
        completed.append(active)

        active = RealFaultStageV226.POST_READ_GAP_BUILD
        post_graph = build_gap_graph_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=post_memory,
            topology_edges=(),
            planner_focus_hypothesis_id=None,
            prior_negative_coverage=(),
        )
        completed.append(active)

        active = RealFaultStageV226.TERMINAL_CATALOG_BUILD
        terminals = terminalize_real_fault_v226(
            candidate_services=capture.candidate_aliases,
            baseline=baseline,
            memory=post_memory,
            gap_graph=post_graph,
            resource_covered_targets=covered_targets,
            remaining_budget=max(0.0, 3.0 - bundle.weighted_cost),
            required_source_failures=(
                (EvidenceSourceV22.RESOURCES,)
                if resource_outcome.status not in _SUCCESS_STATUSES
                else ()
            ),
            budget_prevented_required_coverage=False,
            conflicting_evidence=False,
        )
        if not terminals.terminal_candidates:
            raise ValueError("shared terminalizer produced no candidate")
        completed.append(active)

        active = RealFaultStageV226.PROVIDER_TERMINAL_SELECTION
        focuses = tuple(
            (
                item.hypothesis_id,
                item.target_service,
                item.mechanism.value,
            )
            for item in hypotheses.hypotheses
            if item.target_service is not None
        )
        surface = build_real_fault_selection_surface_v226(
            actions=(),
            terminals=terminals.terminal_candidates,
            focuses=focuses,
            remaining_semantic_actions=0,
            remaining_target_equivalent_reads=0,
        )
        require_provider_payload_opaque_v225(surface.request.model_dump(mode="json"))
        outcome = provider.complete_selection(
            request=surface.request,
            run_id=run_id,
            max_protocol_repairs=2,
        )
        provider_turns = 1
        provider_calls = outcome.provider_calls
        first_pass = outcome.first_pass_protocol_success
        post_repair = outcome.post_repair_protocol_success
        protocol_repairs = outcome.protocol_repairs
        input_tokens = outcome.input_tokens
        output_tokens = outcome.output_tokens
        total_tokens = outcome.total_tokens
        latency_ms = outcome.latency_ms
        transport_retries = outcome.transport_retry_count
        completed.append(active)

        active = RealFaultStageV226.TERMINAL_BIND
        if not outcome.decision.selection.startswith("T"):
            raise ValueError("Current terminal selection chose a read action")
        if outcome.decision.focus != "NONE":
            raise ValueError("terminal selection carried an active focus")
        try:
            selected = surface.terminal_by_alias[outcome.decision.selection]
        except KeyError as error:
            raise ValueError("selected terminal alias is absent") from error
        prediction = _prediction_v226(selected)
        completed.append(active)

        active = RealFaultStageV226.COMPLETE
        completed.append(active)
        trace = build_successful_real_fault_trace_v226(
            arm=arm.value,
            completed_stages=tuple(completed),
        )
        status = RealFaultArmStatusV226.VALID_TERMINAL
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        code = _failure_code(active)
        if isinstance(error, RealFaultSelectionProtocolFailureV226):
            provider_calls += error.provider_calls
            first_pass = False
            post_repair = False
            protocol_repairs += error.protocol_repairs
            input_tokens += error.input_tokens
            output_tokens += error.output_tokens
            total_tokens += error.total_tokens
            latency_ms += error.latency_ms
            transport_retries += error.transport_retry_count
        if active is RealFaultStageV226.PROVIDER_TERMINAL_SELECTION:
            if (
                isinstance(error, RealFaultSelectionProtocolFailureV226)
                and error.transport_failure
            ) or isinstance(error, (TimeoutError, ConnectionError)):
                code = RealFaultSafeFailureCodeV226.PROVIDER_TRANSPORT_FAILED
                status = RealFaultArmStatusV226.TRANSPORT_FAILED
                transport_failures = 1
            else:
                status = RealFaultArmStatusV226.PROTOCOL_FAILED
                protocol_failures = 1
        else:
            status = RealFaultArmStatusV226.RUNNER_FAILED
            runner_failures = 1
        trace = build_failed_real_fault_trace_v226(
            arm=arm.value,
            completed_stages=tuple(completed),
            failure_stage=active,
            safe_error_code=code,
            local_exception_class=type(error).__name__,
            safe_validation_codes=_safe_validation_codes(error),
        )

    return build_real_fault_arm_run_v226(
        case_id=capture.case_id,
        arm=arm,
        case_bytes_sha256=capture.opaque_capture_sha256,
        model_id=model_id,
        status=status,
        prediction=prediction,
        trace=trace,
        strictly_ambiguous=strictly_ambiguous,
        comparison_set_size=comparison_set_size,
        bundle_eligible=bundle_eligible,
        bundle_dispatched=bundle_dispatched,
        bundle_target_count=bundle_target_count,
        first_useful_evidence_ordinal=first_useful_ordinal,
        resources_selected=resources_selected,
        resource_read_shape=resource_read_shape,
        all_candidates_covered=all_candidates_covered,
        semantic_evidence_actions=semantic_actions,
        target_equivalent_reads=target_reads,
        predicate_yield_count=predicate_yield_count,
        duplicate_read_attempts=backend.duplicate_request_count,
        empty_read_count=empty_read_count,
        provider_turns=provider_turns,
        provider_calls=provider_calls,
        first_pass_protocol_success=first_pass,
        post_repair_protocol_success=post_repair,
        protocol_repairs=protocol_repairs,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=float(latency_ms),
        transport_retries=transport_retries,
        protocol_failures=protocol_failures,
        runner_failures=runner_failures,
        transport_failures=transport_failures,
    )


__all__ = ("run_current_runtime_bundle_v226",)
