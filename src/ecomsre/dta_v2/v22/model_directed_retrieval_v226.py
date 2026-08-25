"""Model-directed source/target selection with runtime-owned requests and terminals."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ValidationError

from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    HypothesisCatalogV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222, build_gap_graph_v222
from ecomsre.dta_v2.v22.memory import (
    MemoryReadOutcomeV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _memory_outcome
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
)
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    ActionReadBackendV225,
    ActionV225,
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
    RealFaultSelectionSurfaceV226,
    build_real_fault_selection_surface_v226,
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
        RealFaultStageV226.BOOTSTRAP_BUILD: RealFaultSafeFailureCodeV226.BOOTSTRAP_READ_FAILED,
        RealFaultStageV226.ACTION_SURFACE_BUILD: RealFaultSafeFailureCodeV226.ACTION_CATALOG_EMPTY,
        RealFaultStageV226.PROVIDER_ACTION_SELECTION: RealFaultSafeFailureCodeV226.PROVIDER_OUTPUT_INVALID,
        RealFaultStageV226.ACTION_BIND: RealFaultSafeFailureCodeV226.PROVIDER_OUTPUT_INVALID,
        RealFaultStageV226.READ_DISPATCH: RealFaultSafeFailureCodeV226.BUNDLE_READ_FAILED,
        RealFaultStageV226.OBSERVATION_BIND: RealFaultSafeFailureCodeV226.OBSERVATION_CONVERSION_FAILED,
        RealFaultStageV226.MEMORY_BUILD: RealFaultSafeFailureCodeV226.MEMORY_CONSTRUCTION_FAILED,
        RealFaultStageV226.TERMINAL_CATALOG_BUILD: RealFaultSafeFailureCodeV226.TERMINAL_CATALOG_EMPTY,
        RealFaultStageV226.PROVIDER_TERMINAL_SELECTION: RealFaultSafeFailureCodeV226.PROVIDER_OUTPUT_INVALID,
        RealFaultStageV226.TERMINAL_BIND: RealFaultSafeFailureCodeV226.TERMINAL_ALIAS_INVALID,
        RealFaultStageV226.COMPLETE: RealFaultSafeFailureCodeV226.INTERNAL_CONTRACT_FAILURE,
    }[stage]


def _focuses(hypotheses: HypothesisCatalogV22) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (item.hypothesis_id, item.target_service, item.mechanism.value)
        for item in hypotheses.hypotheses
        if item.target_service is not None
    )


def _resource_bundle(
    *,
    capture: RealFaultOpaqueCaptureV1,
    graph: GapGraphV222,
    remaining_budget: float,
) -> ContrastiveResourceActionV225 | None:
    coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=capture.candidate_aliases,
        covered_target_services=tuple(
            sorted(item.service for item in capture.capture.resources)
        ),
    )
    return contrastive_resource_action_if_eligible_v225(
        coverage=coverage,
        resources_enabled=(
            coverage.coverage_mode is ReplayTargetCoverageModeV225.TARGET_COMPLETE
            and not any(
                item.source is EvidenceSourceV22.RESOURCES
                for item in capture.capture.source_failures
            )
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
        remaining_budget=remaining_budget,
        bundle_mode=True,
    )


def _action_surface(
    *,
    capture: RealFaultOpaqueCaptureV1,
    topology: StaticTopologyV22,
    graph: GapGraphV222,
    executed_action_ids: tuple[str, ...],
    covered_capability_keys: tuple[str, ...],
    remaining_budget: float,
    remaining_semantic_actions: int,
    remaining_target_reads: int,
) -> tuple[ActionV225, ...]:
    if remaining_semantic_actions <= 0 or remaining_target_reads <= 0:
        return ()
    catalog = build_action_catalog_v22(
        candidate_services=capture.candidate_aliases,
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=executed_action_ids,
        remaining_budget=remaining_budget,
        covered_capability_keys=covered_capability_keys,
    )
    actions: tuple[ActionV225, ...] = tuple(
        item
        for item in catalog.actions
        if len(item.target_services) <= remaining_target_reads
    )
    bundle = _resource_bundle(
        capture=capture,
        graph=graph,
        remaining_budget=remaining_budget,
    )
    if (
        bundle is not None
        and len(bundle.target_services) <= remaining_target_reads
        and not set(bundle.coverage_keys).issubset(covered_capability_keys)
    ):
        actions = (*actions, bundle)
    return tuple(sorted(actions, key=lambda item: item.action_id))


def _surface(
    *,
    actions: tuple[ActionV225, ...],
    terminals: tuple[RealFaultAdmittedTerminalV226, ...],
    hypotheses: HypothesisCatalogV22,
    remaining_semantic_actions: int,
    remaining_target_reads: int,
) -> RealFaultSelectionSurfaceV226:
    return build_real_fault_selection_surface_v226(
        actions=actions,
        terminals=terminals,
        focuses=_focuses(hypotheses),
        remaining_semantic_actions=remaining_semantic_actions,
        remaining_target_equivalent_reads=remaining_target_reads,
    )


def run_model_directed_retrieval_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    model_id: str,
    provider: RealFaultSelectionProviderV226,
    _action_backend: ActionReadBackendV225 | None = None,
) -> RealFaultArmRunV226:
    """Run bounded free A/H selection with no model-constructed read request."""

    arm = RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
    completed: list[RealFaultStageV226] = []
    active = RealFaultStageV226.INPUT_VALIDATION
    run_id = real_fault_run_id_v226(capture)
    backend = _action_backend or RealFaultActionReadBackendV225.snapshot(
        capture=capture,
        run_id=run_id,
    )
    status = RealFaultArmStatusV226.RUNNER_FAILED
    prediction = _failed_prediction_v226()
    resources_selected = False
    resource_read_shape: Literal["NONE", "SINGLE_TARGET", "MULTI_TARGET"] = "NONE"
    resource_covered_targets: set[str] = set()
    semantic_actions = 0
    target_reads = 0
    first_useful_ordinal: int | None = None
    predicate_yield_count = 0
    empty_read_count = 0
    provider_turns = 0
    provider_calls = 0
    first_pass = True
    post_repair = True
    protocol_repairs = 0
    input_tokens = output_tokens = total_tokens = 0
    latency_ms = 0.0
    transport_retries = 0
    protocol_failures = runner_failures = transport_failures = 0
    remaining_budget = 3.0

    try:
        if capture.alias_map_name != baseline_capture.alias_map_name:
            raise ValueError("model-directed case and baseline maps differ")
        if capture.candidate_aliases != baseline_capture.candidate_aliases:
            raise ValueError("model-directed case and baseline candidates differ")
        if not model_id.strip():
            raise ValueError("model ID is empty")
        completed.append(active)

        active = RealFaultStageV226.BOOTSTRAP_BUILD
        topology = StaticTopologyV22.build(
            services=capture.candidate_aliases,
            edges=(),
        )
        bootstrap, bootstrap_outcomes = build_real_fault_canonical_bootstrap_v226(
            capture=capture,
            baseline_capture=baseline_capture,
            backend=backend,
        )
        baseline = build_real_fault_baseline_profile_v226(baseline_capture)
        outcomes: tuple[MemoryReadOutcomeV22, ...] = bootstrap_outcomes
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=baseline,
            observed_at=capture.capture.captured_at,
            top_k=64,
        )
        if (
            memory.memory_sha256 != bootstrap.memory_sha256
            or baseline.baseline_sha256 != bootstrap.baseline_sha256
        ):
            raise ValueError("model-directed canonical bootstrap differs")
        hypotheses = build_hypothesis_catalog_v22(
            candidate_services=capture.candidate_aliases
        )
        policy = build_effective_support_policy_v222()
        graph = build_gap_graph_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=memory,
            topology_edges=(),
            planner_focus_hypothesis_id=None,
            prior_negative_coverage=(),
        )
        executed_registry_ids = tuple(
            item.action_id for item in bootstrap.read_bindings
        )
        covered_keys: tuple[str, ...] = ()
        required_source_failures: set[EvidenceSourceV22] = set()
        completed.append(active)

        terminal_candidates: tuple[RealFaultAdmittedTerminalV226, ...] = ()
        while True:
            remaining_actions = 4 - semantic_actions
            remaining_target_reads = 4 - target_reads
            actions = _action_surface(
                capture=capture,
                topology=topology,
                graph=graph,
                executed_action_ids=executed_registry_ids,
                covered_capability_keys=covered_keys,
                remaining_budget=remaining_budget,
                remaining_semantic_actions=remaining_actions,
                remaining_target_reads=remaining_target_reads,
            )

            active = (
                RealFaultStageV226.PROVIDER_TERMINAL_SELECTION
                if terminal_candidates
                else RealFaultStageV226.ACTION_SURFACE_BUILD
            )
            if not actions and not terminal_candidates:
                terminals = terminalize_real_fault_v226(
                    candidate_services=capture.candidate_aliases,
                    baseline=baseline,
                    memory=memory,
                    gap_graph=graph,
                    resource_covered_targets=tuple(sorted(resource_covered_targets)),
                    remaining_budget=remaining_budget,
                    required_source_failures=tuple(required_source_failures),
                    budget_prevented_required_coverage=True,
                    conflicting_evidence=False,
                )
                terminal_candidates = terminals.terminal_candidates
                if not terminal_candidates:
                    raise ValueError("model-directed budget ended without a terminal")
                active = RealFaultStageV226.PROVIDER_TERMINAL_SELECTION

            surface = _surface(
                actions=actions,
                terminals=terminal_candidates,
                hypotheses=hypotheses,
                remaining_semantic_actions=remaining_actions,
                remaining_target_reads=remaining_target_reads,
            )
            require_provider_payload_opaque_v225(
                surface.request.model_dump(mode="json")
            )
            if active is RealFaultStageV226.ACTION_SURFACE_BUILD:
                completed.append(active)
                active = RealFaultStageV226.PROVIDER_ACTION_SELECTION
            selection = provider.complete_selection(
                request=surface.request,
                run_id=run_id,
                max_protocol_repairs=2,
            )
            provider_turns += 1
            provider_calls += selection.provider_calls
            first_pass = first_pass and selection.first_pass_protocol_success
            post_repair = post_repair and selection.post_repair_protocol_success
            protocol_repairs += selection.protocol_repairs
            input_tokens += selection.input_tokens
            output_tokens += selection.output_tokens
            total_tokens += selection.total_tokens
            latency_ms += selection.latency_ms
            transport_retries += selection.transport_retry_count
            completed.append(active)

            if selection.decision.selection.startswith("T"):
                active = RealFaultStageV226.TERMINAL_BIND
                if selection.decision.focus != "NONE":
                    raise ValueError("terminal selection carried an active focus")
                try:
                    terminal = surface.terminal_by_alias[
                        selection.decision.selection
                    ]
                except KeyError as error:
                    raise ValueError("selected terminal alias is absent") from error
                prediction = _prediction_v226(terminal)
                completed.append(active)
                break

            active = RealFaultStageV226.ACTION_BIND
            if selection.decision.focus != "NONE" and (
                selection.decision.focus not in surface.focus_by_alias
            ):
                raise ValueError("selected focus alias is absent")
            try:
                action = surface.action_by_alias[selection.decision.selection]
            except KeyError as error:
                raise ValueError("selected action alias is absent") from error
            if (
                action.weighted_cost > remaining_budget
                or semantic_actions >= 4
                or target_reads + len(action.target_services) > 4
            ):
                raise ValueError("selected action exceeds the bounded budget")
            completed.append(active)

            active = RealFaultStageV226.READ_DISPATCH
            source_outcome = backend.execute(action)
            semantic_actions += 1
            target_reads += len(action.target_services)
            remaining_budget = max(0.0, remaining_budget - action.weighted_cost)
            completed.append(active)

            active = RealFaultStageV226.OBSERVATION_BIND
            if (
                source_outcome.action_id != action.action_id
                or source_outcome.request_sha256 != action.request_sha256
                or source_outcome.source is not action.source
            ):
                raise ValueError("read outcome differs from selected action")
            memory_outcome = _memory_outcome(
                action=cast(EvidenceActionV22, action),
                outcome=source_outcome,
                run_id=run_id,
                dispatch_ordinal=len(outcomes) + 1,
                observed_at=capture.capture.captured_at,
            ) if isinstance(action, EvidenceActionV22) else source_outcome
            outcomes = (*outcomes, memory_outcome)
            if isinstance(action, EvidenceActionV22):
                executed_registry_ids = tuple(
                    sorted({*executed_registry_ids, action.action_id})
                )
            covered_keys = tuple(sorted({*covered_keys, *action.coverage_keys}))
            empty_read_count += int(
                source_outcome.status is ReadSourceStatusV22.SUCCESS_EMPTY
            )
            if source_outcome.status not in _SUCCESS_STATUSES:
                required_source_failures.add(action.source)
            if action.source is EvidenceSourceV22.RESOURCES:
                resources_selected = True
                if len(action.target_services) > 1:
                    resource_read_shape = "MULTI_TARGET"
                elif resource_read_shape == "NONE":
                    resource_read_shape = "SINGLE_TARGET"
                resource_covered_targets.update(
                    item.service
                    for item in source_outcome.records
                    if isinstance(item, ResourceUsageRecordV22)
                    and item.service in set(capture.candidate_aliases)
                )
            completed.append(active)

            active = RealFaultStageV226.MEMORY_BUILD
            next_memory, _ = build_memory_views_v22(
                outcomes=outcomes,
                baseline=baseline,
                observed_at=capture.capture.captured_at,
                top_k=64,
            )
            prior_predicates = {item.predicate_id for item in memory.predicates}
            new_predicates = sum(
                item.predicate_id not in prior_predicates
                for item in next_memory.predicates
            )
            predicate_yield_count += new_predicates
            if first_useful_ordinal is None and new_predicates:
                first_useful_ordinal = semantic_actions
            memory = next_memory
            graph = build_gap_graph_v222(
                policy=policy,
                hypothesis_catalog=hypotheses,
                memory=memory,
                topology_edges=(),
                planner_focus_hypothesis_id=None,
                prior_negative_coverage=(),
            )
            completed.append(active)

            active = RealFaultStageV226.TERMINAL_CATALOG_BUILD
            budget_prevented = (
                semantic_actions >= 4
                or target_reads >= 4
                or not _action_surface(
                    capture=capture,
                    topology=topology,
                    graph=graph,
                    executed_action_ids=executed_registry_ids,
                    covered_capability_keys=covered_keys,
                    remaining_budget=remaining_budget,
                    remaining_semantic_actions=4 - semantic_actions,
                    remaining_target_reads=4 - target_reads,
                )
            ) and set(resource_covered_targets) != set(capture.candidate_aliases)
            terminals = terminalize_real_fault_v226(
                candidate_services=capture.candidate_aliases,
                baseline=baseline,
                memory=memory,
                gap_graph=graph,
                resource_covered_targets=tuple(sorted(resource_covered_targets)),
                remaining_budget=remaining_budget,
                required_source_failures=tuple(required_source_failures),
                budget_prevented_required_coverage=budget_prevented,
                conflicting_evidence=False,
            )
            terminal_candidates = terminals.terminal_candidates
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
        if active in {
            RealFaultStageV226.PROVIDER_ACTION_SELECTION,
            RealFaultStageV226.PROVIDER_TERMINAL_SELECTION,
        }:
            if isinstance(error, (TimeoutError, ConnectionError)):
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
        strictly_ambiguous=None,
        comparison_set_size=0,
        bundle_eligible=False,
        bundle_dispatched=False,
        bundle_target_count=0,
        first_useful_evidence_ordinal=first_useful_ordinal,
        resources_selected=resources_selected,
        resource_read_shape=resource_read_shape,
        all_candidates_covered=(
            set(resource_covered_targets) == set(capture.candidate_aliases)
        ),
        semantic_evidence_actions=semantic_actions,
        target_equivalent_reads=target_reads,
        predicate_yield_count=predicate_yield_count,
        duplicate_read_attempts=backend.duplicate_request_count,
        empty_read_count=empty_read_count,
        provider_turns=provider_turns,
        provider_calls=provider_calls,
        first_pass_protocol_success=first_pass if provider_turns else False,
        post_repair_protocol_success=post_repair if provider_turns else False,
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


__all__ = ("run_model_directed_retrieval_v226",)
