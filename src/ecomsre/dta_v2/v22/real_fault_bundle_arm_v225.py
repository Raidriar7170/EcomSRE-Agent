"""Current v2.2.5 BUNDLE_ONE arm over one opaque real-fault capture."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import build_resource_ambiguity_sets_v225
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    build_contrastive_resource_delta_v225,
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    route_gap_aware_actions_v222,
)
from ecomsre.dta_v2.v22.memory import BaselineProfileV22, build_memory_views_v22
from ecomsre.dta_v2.v22.negative_coverage_v222 import (
    ReadUtilityClassV222,
    classify_read_utility_v222,
)
from ecomsre.dta_v2.v22.no_incident_set_closure_v225 import (
    ClosureDispositionV225,
    NoIncidentClosureScopeV225,
    evaluate_no_incident_set_closure_v225,
    initial_no_incident_set_closure_state_v225,
    minimum_completion_cost_v225,
    record_no_incident_set_closure_attempt_v225,
)
from ecomsre.dta_v2.v22.practical_runner import _memory_outcome
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultOpaqueCaptureV1,
    require_provider_payload_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultArmRun,
    RealFaultArmStatus,
    RealFaultShadowPrediction,
    RealFaultStudyArm,
    build_real_fault_arm_run_v225,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v22.replay_bundle_v225 import QuerySpecificReplayBackendV225
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayTargetCoverageModeV225,
    build_replay_target_coverage_v225,
)
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionAliasTableV222,
    SelectionProviderProtocolFailureV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.selection_provider_v223 import SelectionProviderProtocolV223
from ecomsre.dta_v2.v22.terminal_catalog_v222 import (
    TerminalCandidateV222,
    TerminalKindV222,
    build_terminal_catalog_v222,
)


def _run_id(capture: RealFaultOpaqueCaptureV1) -> str:
    return hashlib.sha256(
        f"real-fault:{capture.case_id}:{capture.opaque_capture_sha256}".encode()
    ).hexdigest()[:32]


def _source_failure(
    *, capture: RealFaultOpaqueCaptureV1, source: EvidenceSourceV22
) -> ReadSourceStatusV22 | None:
    return next(
        (
            item.status
            for item in capture.capture.source_failures
            if item.source is source
        ),
        None,
    )


def _outcome(
    *,
    action: EvidenceActionV22,
    capture: RealFaultOpaqueCaptureV1,
    records: tuple[Any, ...],
) -> ReadOutcomeV22:
    failure = _source_failure(capture=capture, source=action.source)
    status = (
        failure
        if failure is not None
        else ReadSourceStatusV22.SUCCESS_NONEMPTY
        if records
        else ReadSourceStatusV22.SUCCESS_EMPTY
    )
    bound_records = () if failure is not None else records
    payload: dict[str, object] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": status,
        "records": bound_records,
        "truncated": False,
    }
    draft = ReadOutcomeV22.model_construct(
        schema_version="dta-v22.read-outcome.v1",
        action_id=action.action_id,
        source=action.source,
        request_sha256=action.request_sha256,
        status=status,
        records=bound_records,
        truncated=False,
        outcome_sha256="0" * 64,
    )
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def _baseline(capture: RealFaultOpaqueCaptureV1) -> BaselineProfileV22:
    metrics = tuple(
        (
            item.service,
            item.metric_kind,
            float(item.value or 0.0),
            max(abs(float(item.value or 0.0)) * 0.01, 0.01),
        )
        for item in capture.capture.metrics
        if item.support_status.value == "SUPPORTED"
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


def _bootstrap(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    topology: StaticTopologyV22,
    run_id: str,
) -> tuple[tuple[object, ...], tuple[str, ...]]:
    catalog = build_action_catalog_v22(
        candidate_services=capture.candidate_aliases,
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=3.0,
    )
    runtime_action = next(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and item.target_services == capture.candidate_aliases
    )
    metric_actions = tuple(
        next(
            item
            for item in catalog.registry_actions
            if item.source is EvidenceSourceV22.METRICS
            and item.target_services == (service,)
        )
        for service in capture.candidate_aliases
    )
    source_runtime = _outcome(
        action=runtime_action,
        capture=capture,
        records=capture.capture.runtime,
    )
    outcomes: list[object] = [
        _memory_outcome(
            action=runtime_action,
            outcome=source_runtime,
            run_id=run_id,
            dispatch_ordinal=1,
            observed_at=capture.capture.captured_at,
        )
    ]
    for ordinal, action in enumerate(metric_actions, start=2):
        outcomes.append(
            _outcome(
                action=action,
                capture=capture,
                records=tuple(
                    item
                    for item in capture.capture.metrics
                    if item.service == action.target_services[0]
                ),
            )
        )
    # Revalidate the derived baseline here so bootstrap construction cannot drift
    # from the paired real baseline capture supplied by the caller.
    _baseline(baseline_capture)
    return tuple(outcomes), tuple(
        item.action_id for item in (runtime_action, *metric_actions)
    )


def _prediction(
    *,
    terminal: TerminalCandidateV222,
    closure_disposition: ClosureDispositionV225,
    all_candidates_covered: bool,
) -> RealFaultShadowPrediction:
    label: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN", "FAILED"]
    if terminal.terminal_kind is TerminalKindV222.DIAGNOSED:
        label = "DIAGNOSED"
    elif terminal.terminal_kind is TerminalKindV222.NO_INCIDENT:
        label = "NO_INCIDENT"
    else:
        label = "ABSTAIN"
    valid = (
        bool(terminal.supporting_evidence_refs)
        if label == "DIAGNOSED"
        else closure_disposition is ClosureDispositionV225.COMPLETE_NORMAL
        and all_candidates_covered
        if label == "NO_INCIDENT"
        else True
    )
    return RealFaultShadowPrediction(
        schema_version="dta-v225-real-fault.shadow-prediction.v1",
        terminal=label,
        root_service_alias=terminal.root_service,
        fault_domain=("LOCAL_RESOURCE" if label == "DIAGNOSED" else None),
        mechanism=None if terminal.mechanism is None else terminal.mechanism.value,
        supporting_evidence_refs=terminal.supporting_evidence_refs,
        evidence_clause_valid=valid,
    )


def _failed_prediction() -> RealFaultShadowPrediction:
    return RealFaultShadowPrediction(
        schema_version="dta-v225-real-fault.shadow-prediction.v1",
        terminal="FAILED",
        root_service_alias=None,
        fault_domain=None,
        mechanism=None,
        supporting_evidence_refs=(),
        evidence_clause_valid=False,
    )


def run_current_runtime_bundle_v225(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    model_id: str,
    provider: SelectionProviderProtocolV223,
) -> RealFaultArmRun:
    """Run the current runtime-owned BUNDLE_ONE path without any write stage."""

    if capture.alias_map_name != baseline_capture.alias_map_name:
        raise ValueError("current case and baseline alias maps differ")
    run_id = _run_id(capture)
    topology = StaticTopologyV22.build(
        services=capture.candidate_aliases,
        edges=(),
    )
    backend = QuerySpecificReplayBackendV225(capture.capture)
    provider_calls = retries = input_tokens = output_tokens = total_tokens = 0
    latency_ms = 0.0
    semantic_actions = target_reads = bundle_reads = 0
    predicate_yield = empty_reads = 0
    status = RealFaultArmStatus.RUNNER_FAILED
    prediction = _failed_prediction()
    all_covered = False

    try:
        raw_bootstrap, executed = _bootstrap(
            capture=capture,
            baseline_capture=baseline_capture,
            topology=topology,
            run_id=run_id,
        )
        baseline = _baseline(baseline_capture)
        outcomes = tuple(raw_bootstrap)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,  # type: ignore[arg-type]
            baseline=baseline,
            observed_at=capture.capture.captured_at,
            top_k=64,
        )
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
        catalog = build_action_catalog_v22(
            candidate_services=capture.candidate_aliases,
            topology=topology,
            capability_registry=build_default_tool_capability_registry_v22(),
            executed_action_ids=executed,
            remaining_budget=3.0,
        )
        routing = route_gap_aware_actions_v222(
            mode=GapRouterModeV222.GAP_RANKED_TOP_K,
            catalog=catalog,
            gap_graph=graph,
            prior_negative_coverage=(),
            top_k=4,
        )
        coverage = build_replay_target_coverage_v225(
            source=EvidenceSourceV22.RESOURCES,
            candidate_services=capture.candidate_aliases,
            covered_target_services=tuple(
                sorted(item.service for item in capture.capture.resources)
            ),
        )
        bundle = contrastive_resource_action_if_eligible_v225(
            coverage=coverage,
            resources_enabled=_source_failure(
                capture=capture, source=EvidenceSourceV22.RESOURCES
            )
            is None,
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
        if bundle is None:
            raise ValueError("BUNDLE_ONE was not eligible for the opaque capture")
        ambiguity_sets = build_resource_ambiguity_sets_v225(
            memory=memory,
            gap_graph=graph,
            candidate_services=capture.candidate_aliases,
            topology_edges=(),
            individual_actions=tuple(
                item
                for item in catalog.registry_actions
                if item.source is EvidenceSourceV22.RESOURCES
            ),
            bundle_action=bundle,
            covered_target_services=(),
        )
        if len(ambiguity_sets) != 1:
            raise ValueError("BUNDLE_ONE requires one two-target ambiguity set")
        pre_terminals = build_terminal_catalog_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=memory,
            gap_graph=graph,
            routed_actions=routing,
            candidate_services=capture.candidate_aliases,
            topology_edges=(),
            budget_exhausted=False,
            required_source_unavailable=False,
            conflicting_evidence=False,
        )
        closure = evaluate_no_incident_set_closure_v225(
            state=initial_no_incident_set_closure_state_v225(
                NoIncidentClosureScopeV225.ONE_TARGET_ATTEMPT
            ),
            legacy_no_incident_exposed=any(
                item.terminal_kind is TerminalKindV222.NO_INCIDENT
                for item in pre_terminals.candidates
            ),
            ambiguity_set=ambiguity_sets[0],
            target_complete=(
                coverage.coverage_mode is ReplayTargetCoverageModeV225.TARGET_COMPLETE
            ),
            remaining_evidence_budget=3.0,
            minimum_completion_cost=minimum_completion_cost_v225(
                ambiguity_set=ambiguity_sets[0],
                individual_actions=tuple(
                    item
                    for item in catalog.registry_actions
                    if item.source is EvidenceSourceV22.RESOURCES
                ),
                bundle_action=bundle,
                prefer_bundle=True,
            ),
        )
        source_outcome = backend.execute(bundle)
        semantic_actions = 1
        target_reads = len(bundle.target_services)
        bundle_reads = 1
        post_outcomes = (*outcomes, source_outcome)
        post_memory, _ = build_memory_views_v22(
            outcomes=post_outcomes,  # type: ignore[arg-type]
            baseline=baseline,
            observed_at=capture.capture.captured_at,
            top_k=64,
        )
        utility = classify_read_utility_v222(
            before_memory=memory,
            after_memory=post_memory,
            read_outcome=source_outcome,
        )
        predicate_yield = int(
            utility.outcome_class is ReadUtilityClassV222.PREDICATE_YIELD
        )
        empty_reads = int(
            utility.outcome_class is ReadUtilityClassV222.EMPTY_CAPTURED
        )
        closure = record_no_incident_set_closure_attempt_v225(
            state=closure,
            action=bundle,
            outcome_class=utility.outcome_class,
        )
        post_graph = build_gap_graph_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=post_memory,
            topology_edges=(),
            planner_focus_hypothesis_id=None,
            prior_negative_coverage=(),
        )
        post_catalog = build_action_catalog_v22(
            candidate_services=capture.candidate_aliases,
            topology=topology,
            capability_registry=build_default_tool_capability_registry_v22(),
            executed_action_ids=executed,
            remaining_budget=max(0.0, 3.0 - bundle.weighted_cost),
            covered_capability_keys=bundle.coverage_keys,
        )
        post_routing = route_gap_aware_actions_v222(
            mode=GapRouterModeV222.GAP_RANKED_TOP_K,
            catalog=post_catalog,
            gap_graph=post_graph,
            prior_negative_coverage=(),
            top_k=4,
        )
        terminals = build_terminal_catalog_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=post_memory,
            gap_graph=post_graph,
            routed_actions=post_routing,
            candidate_services=capture.candidate_aliases,
            topology_edges=(),
            budget_exhausted=False,
            required_source_unavailable=(
                source_outcome.status
                not in {
                    ReadSourceStatusV22.SUCCESS_EMPTY,
                    ReadSourceStatusV22.SUCCESS_NONEMPTY,
                }
            ),
            conflicting_evidence=False,
        )
        candidates = tuple(
            item
            for item in terminals.candidates
            if not (
                closure.no_incident_withheld
                and item.terminal_kind is TerminalKindV222.NO_INCIDENT
            )
        )
        if not candidates:
            raise ValueError("BUNDLE_ONE produced no valid terminal")
        aliases = SelectionAliasTableV222.build(
            hypothesis_ids=tuple(
                item.hypothesis_id
                for item in hypotheses.hypotheses
                if item.target_service is not None
            ),
            action_ids=(),
            terminal_ids=tuple(item.terminal_id for item in candidates),
            evidence_refs=tuple(item.evidence_ref for item in post_memory.evidence_refs),
        )
        evidence_alias = {
            item.canonical_id: item.alias for item in aliases.evidence
        }
        delta = build_contrastive_resource_delta_v225(
            action=bundle,
            before_memory=memory,
            after_memory=post_memory,
        )
        request = SelectionTurnRequestV222.build(
            system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V225,
            aliases=aliases,
            visible_state={
                "candidate_services": capture.candidate_aliases,
                "terminals": [
                    {
                        "alias": f"T{index:02d}",
                        "kind": item.terminal_kind.value,
                        "root_service": item.root_service,
                        "mechanism": (
                            None if item.mechanism is None else item.mechanism.value
                        ),
                        "support": [
                            evidence_alias[ref]
                            for ref in item.supporting_evidence_refs
                        ],
                    }
                    for index, item in enumerate(candidates)
                ],
                "closure": {
                    "scope": closure.scope.value,
                    "satisfied": closure.closure_satisfied,
                    "disposition": closure.closure_disposition.value,
                },
                "last_contrast": {
                    "contrast_rows": tuple(
                        row.model_dump(mode="json") for row in delta.contrast_rows
                    )
                },
            },
        )
        require_provider_payload_opaque_v225(
            {
                "system_prompt": request.system_prompt,
                "visible_state": request.visible_state,
            }
        )
        provider_outcome = provider.complete_turn(
            request=request,
            run_id=run_id,
            max_protocol_repairs=2,
        )
        provider_calls = provider_outcome.provider_calls
        retries = provider_outcome.transport_retry_count
        input_tokens = provider_outcome.input_tokens
        output_tokens = provider_outcome.output_tokens
        total_tokens = provider_outcome.total_tokens
        latency_ms = provider_outcome.latency_ms
        if provider_outcome.decision.terminal_id is None:
            raise ValueError("current comparison Provider selected a forbidden action")
        terminal = next(
            item
            for item in candidates
            if item.terminal_id == provider_outcome.decision.terminal_id
        )
        all_covered = set(bundle.target_services) == set(capture.candidate_aliases)
        prediction = _prediction(
            terminal=terminal,
            closure_disposition=closure.closure_disposition,
            all_candidates_covered=all_covered,
        )
        status = RealFaultArmStatus.VALID_TERMINAL
    except SelectionProviderProtocolFailureV222 as error:
        provider_calls = error.provider_calls
        retries = error.transport_retry_count
        input_tokens = error.input_tokens
        output_tokens = error.output_tokens
        total_tokens = error.total_tokens
        latency_ms = error.latency_ms
        status = (
            RealFaultArmStatus.TRANSPORT_FAILED
            if error.safe_code == "TRANSPORT_FAILED"
            else RealFaultArmStatus.PROTOCOL_FAILED
        )
    except (StopIteration, TypeError, ValueError):
        status = RealFaultArmStatus.PROTOCOL_FAILED

    return build_real_fault_arm_run_v225(
        case_id=capture.case_id,
        arm=RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE,
        case_bytes_sha256=capture.opaque_capture_sha256,
        model_id=model_id,
        status=status,
        prediction=prediction,
        first_useful_evidence_ordinal=(1 if bundle_reads else None),
        resources_requested=bool(bundle_reads),
        resource_read_shape="MULTI_TARGET" if bundle_reads else "NONE",
        all_candidates_covered=all_covered,
        semantic_evidence_actions=semantic_actions,
        target_equivalent_reads=target_reads,
        provider_turns=int(provider_calls > 0),
        provider_calls=provider_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        protocol_failures=int(status is RealFaultArmStatus.PROTOCOL_FAILED),
        transport_retries=retries,
        duplicate_read_attempts=0,
        empty_read_count=empty_reads,
        predicate_yield_count=predicate_yield,
        bundle_resources_reads=bundle_reads,
    )


__all__ = ("run_current_runtime_bundle_v225",)
