"""Case-interleaved 2x2 ambiguity closure and Resources bundle campaign."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    _build_memory,
    _catalog_with_candidates,
    _minimum_gap,
)
from ecomsre.dta_v2.v22.ambiguity_dispatch_v225 import (
    ActionGranularityV225,
    dispatch_ambiguity_action_v225,
)
from ecomsre.dta_v2.v22.ambiguity_coverage_ledger_v225 import (
    AmbiguityCoverageLedgerV225,
    rebuild_ambiguity_set_coverage_v225,
    record_ambiguity_coverage_event_v225,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import build_resource_ambiguity_sets_v225
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
    ContrastiveResourceDeltaV225,
    build_contrastive_resource_delta_v225,
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.dispatch_policy_v223 import (
    AutomaticDispatchUnavailableV223,
    EvidenceDispatchModeV223,
    automatic_dispatch_v223,
)
from ecomsre.dta_v2.v22.effective_policy_v222 import build_effective_support_policy_v222
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import GapRouterModeV222
from ecomsre.dta_v2.v22.gap_router_v223 import (
    PredicateYieldPriorV223,
    route_gap_aware_actions_v223,
)
from ecomsre.dta_v2.v22.memory import MemoryReadOutcomeV22
from ecomsre.dta_v2.v22.negative_coverage_v222 import (
    NegativeCoverageEntryV222,
    NegativeCoverageLedgerV222,
    ReadUtilityClassV222,
    classify_read_utility_v222,
    record_negative_coverage_v222,
)
from ecomsre.dta_v2.v22.no_incident_set_closure_v225 import (
    NoIncidentClosureScopeV225,
    NoIncidentSetClosureStateV225,
    evaluate_no_incident_set_closure_v225,
    initial_no_incident_set_closure_state_v225,
    minimum_completion_cost_v225,
    record_no_incident_set_closure_attempt_v225,
)
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    lint_provider_payload_v225,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseSpecV22,
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _bootstrap, _memory_outcome
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay_bundle_v225 import QuerySpecificReplayBackendV225
from ecomsre.dta_v2.v22.replay_capabilities_v222 import (
    ReplaySourceAvailabilityV222,
    build_replay_capabilities_v222,
    build_source_aware_action_catalog_v222,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayCaseTargetCoverageV225,
    ReplayTargetCoverageModeV225,
    load_replay_target_coverage_set_v225,
    require_capture_matches_target_coverage_v225,
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


SHARED_SELECTION_SYSTEM_PROMPT_V225 = (
    "You are the read-only DTA v2.2.5 terminal selection turn. Evidence dispatch "
    "is runtime-owned. Select exactly one current T alias with NONE focus. Empty "
    "or normal reads cover only their declared targets; NO_INCIDENT appears only "
    "when the runtime admits it. There is no Agent write, shell, remediation, "
    "Docker, or Runbook authority. Return only the forced short shape."
    "\n"
)


class StudyCombinationV225(str, Enum):
    TARGET_ONE = "TARGET_ONE"
    TARGET_SET = "TARGET_SET"
    BUNDLE_ONE = "BUNDLE_ONE"
    BUNDLE_SET = "BUNDLE_SET"

    @property
    def action_granularity(self) -> ActionGranularityV225:
        return (
            ActionGranularityV225.PER_TARGET
            if self in {self.TARGET_ONE, self.TARGET_SET}
            else ActionGranularityV225.CONTRASTIVE_BUNDLE
        )

    @property
    def closure_scope(self) -> NoIncidentClosureScopeV225:
        return (
            NoIncidentClosureScopeV225.ONE_TARGET_ATTEMPT
            if self in {self.TARGET_ONE, self.BUNDLE_ONE}
            else NoIncidentClosureScopeV225.AMBIGUITY_SET_COMPLETE
        )


class AmbiguityBundleRunStatusV225(str, Enum):
    VALID_TERMINAL = "VALID_TERMINAL"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_EXCEPTION = "RUNNER_EXCEPTION"


class AmbiguityDispatchEventV225(DtaModelV22):
    ordinal: StrictInt = Field(ge=1)
    action_id: str
    source: EvidenceSourceV22
    targets: tuple[str, ...]
    status: ReadSourceStatusV22
    outcome_class: ReadUtilityClassV222
    new_predicate_kinds: tuple[str, ...]
    weighted_cost: StrictFloat = Field(gt=0)
    bundle: StrictBool
    ambiguity_set_id: str | None
    covered_targets_before: tuple[str, ...]
    covered_targets_after: tuple[str, ...]
    ranking_action_ids_at_dispatch: tuple[str, ...]
    automatic: Literal[True]


class AmbiguityBundleCaseRunV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-bundle-case-run.v1"]
    case_id: str
    combination: StudyCombinationV225
    action_granularity: ActionGranularityV225
    closure_scope: NoIncidentClosureScopeV225
    case_bytes_sha256: str
    status: AmbiguityBundleRunStatusV225
    terminal: str | None
    root_service: str | None
    mechanism: str | None
    supporting_evidence_refs: tuple[str, ...]
    matched_clause_id: str | None
    read_events: tuple[AmbiguityDispatchEventV225, ...]
    contrastive_deltas: tuple[ContrastiveResourceDeltaV225, ...]
    automatic_dispatches: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    provider_terminal_selections: StrictInt = Field(ge=0)
    first_pass_protocol_successes: StrictInt = Field(ge=0)
    post_repair_protocol_successes: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retry_count: StrictInt = Field(ge=0, le=9)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    ambiguity_set_count: StrictInt = Field(ge=0)
    ambiguity_set_size: StrictInt = Field(ge=0)
    resource_target_complete: StrictBool
    targets_covered_before_terminal: tuple[str, ...]
    set_complete_before_terminal: StrictBool
    bundle_eligible: StrictBool
    bundle_dispatch_count: StrictInt = Field(ge=0)
    individual_resources_reads: StrictInt = Field(ge=0)
    bundle_resources_reads: StrictInt = Field(ge=0)
    no_incident_exposed_after_partial_coverage: StrictBool
    ambiguity_coverage_ledger: AmbiguityCoverageLedgerV225
    forgotten_preclosure_read_count: Literal[0]
    closure_state: NoIncidentSetClosureStateV225
    abstain_reason: str | None
    safe_error_code: str | None
    uncaught_exceptions: StrictInt = Field(ge=0, le=1)
    agent_writes: Literal[0]


class StudyScheduleEntryV225(DtaModelV22):
    case_id: str
    execution_position: StrictInt = Field(ge=1, le=4)
    combination: StudyCombinationV225


class AmbiguityBundleCampaignResultV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.ambiguity-bundle-campaign.v1"]
    schedule: tuple[StudyScheduleEntryV225, ...]
    runs: tuple[AmbiguityBundleCaseRunV225, ...]
    truths: tuple[PracticalTruthV22, ...]
    cases_materialized: StrictInt = Field(ge=1)
    combinations_per_case: Literal[4]
    same_case_bytes_all_combinations: StrictBool
    truth_loaded_after_all_four_runs_per_case: Literal[True]
    truth_load_count: Literal[1]
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]

    @model_validator(mode="after")
    def require_campaign(self) -> "AmbiguityBundleCampaignResultV225":
        expected = {
            (truth.case_id, combination)
            for truth in self.truths
            for combination in StudyCombinationV225
        }
        if {(run.case_id, run.combination) for run in self.runs} != expected:
            raise ValueError("v2.2.5 campaign factorial grid differs")
        if len(self.runs) != len(expected) or not self.same_case_bytes_all_combinations:
            raise ValueError("v2.2.5 campaign case-byte binding differs")
        if self.uncaught_exceptions != sum(item.uncaught_exceptions for item in self.runs):
            raise ValueError("v2.2.5 campaign exception accounting differs")
        return self


def balanced_combination_order_v225(case_index: int) -> tuple[StudyCombinationV225, ...]:
    if case_index < 0:
        raise ValueError("case index must be nonnegative")
    base = tuple(StudyCombinationV225)
    offset = case_index % len(base)
    return (*base[offset:], *base[:offset])


def _resource_hypothesis_count(graph: object) -> int:
    return sum(
        not item.complete
        and any(
            gap.predicate_kind.value.startswith("RESOURCE_")
            for clause in item.clauses
            for gap in clause.missing_requirements
        )
        for item in graph.hypotheses  # type: ignore[attr-defined]
    )


def execute_ambiguity_bundle_case_v225(
    *,
    spec: PracticalCaseSpecV22,
    coverage: ReplayCaseTargetCoverageV225,
    repository_root: Path,
    combination: StudyCombinationV225,
    provider: SelectionProviderProtocolV223,
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
) -> AmbiguityBundleCaseRunV225:
    """Execute one replay-only case without loading evaluator truth."""

    case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
    resource_coverage = coverage.require(EvidenceSourceV22.RESOURCES)
    require_capture_matches_target_coverage_v225(
        coverage=resource_coverage,
        capture=case.capture,
    )
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    bootstrap, _, _, _ = _bootstrap(case=case, topology=topology, run_id="0" * 32)
    outcomes: tuple[MemoryReadOutcomeV22, ...] = tuple(bootstrap)
    executed = tuple(item.action_id for item in outcomes)
    covered_keys: tuple[str, ...] = ()
    replay_capabilities = build_replay_capabilities_v222(
        spec=spec,
        repository_root=repository_root,
    )
    resources_enabled = (
        replay_capabilities.require(EvidenceSourceV22.RESOURCES).availability
        is ReplaySourceAvailabilityV222.CAPTURED
    )
    remaining_budget = 3.0
    negative = NegativeCoverageLedgerV222.empty()
    coverage_ledger = AmbiguityCoverageLedgerV225.empty()
    closure = initial_no_incident_set_closure_state_v225(
        combination.closure_scope
    )
    events: list[AmbiguityDispatchEventV225] = []
    deltas: list[ContrastiveResourceDeltaV225] = []
    provider_calls = terminal_selections = first_pass = post_repair = 0
    repairs = retries = input_tokens = output_tokens = total_tokens = 0
    latency_ms = 0.0
    required_source_unavailable = False
    bundle_ever_eligible = False
    no_incident_after_partial = False
    run_id = hashlib.sha256(
        f"v225:{case.case_id}:{combination.value}".encode()
    ).hexdigest()[:32]

    def result(
        *,
        status: AmbiguityBundleRunStatusV225,
        terminal: TerminalCandidateV222 | None,
        code: str | None,
        uncaught: int = 0,
    ) -> AmbiguityBundleCaseRunV225:
        ambiguity = closure.ambiguity_set
        return AmbiguityBundleCaseRunV225(
            schema_version="dta-v22.5.ambiguity-bundle-case-run.v1",
            case_id=case.case_id,
            combination=combination,
            action_granularity=combination.action_granularity,
            closure_scope=combination.closure_scope,
            case_bytes_sha256=semantic_sha256_v22(case.model_dump(mode="json")),
            status=status,
            terminal=None if terminal is None else terminal.terminal_kind.value,
            root_service=None if terminal is None else terminal.root_service,
            mechanism=(
                None
                if terminal is None or terminal.mechanism is None
                else terminal.mechanism.value
            ),
            supporting_evidence_refs=(
                () if terminal is None else terminal.supporting_evidence_refs
            ),
            matched_clause_id=None if terminal is None else terminal.matched_clause_id,
            read_events=tuple(events),
            contrastive_deltas=tuple(deltas),
            automatic_dispatches=len(events),
            provider_calls=provider_calls,
            provider_terminal_selections=terminal_selections,
            first_pass_protocol_successes=first_pass,
            post_repair_protocol_successes=post_repair,
            protocol_repairs=repairs,
            transport_retry_count=retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            ambiguity_set_count=int(ambiguity is not None),
            ambiguity_set_size=0 if ambiguity is None else len(ambiguity.target_services),
            resource_target_complete=(
                resource_coverage.coverage_mode
                is ReplayTargetCoverageModeV225.TARGET_COMPLETE
            ),
            targets_covered_before_terminal=(
                () if ambiguity is None else ambiguity.covered_target_services
            ),
            set_complete_before_terminal=bool(ambiguity and ambiguity.complete),
            bundle_eligible=bundle_ever_eligible,
            bundle_dispatch_count=sum(item.bundle for item in events),
            individual_resources_reads=sum(
                item.source is EvidenceSourceV22.RESOURCES and not item.bundle
                for item in events
            ),
            bundle_resources_reads=sum(item.bundle for item in events),
            no_incident_exposed_after_partial_coverage=no_incident_after_partial,
            ambiguity_coverage_ledger=coverage_ledger,
            forgotten_preclosure_read_count=0,
            closure_state=closure,
            abstain_reason=closure.abstain_reason,
            safe_error_code=code,
            uncaught_exceptions=uncaught,
            agent_writes=0,
        )

    try:
        for _turn_index in range(8):
            memory = _build_memory(case=case, outcomes=outcomes)
            hypotheses = build_hypothesis_catalog_v22(
                candidate_services=case.candidate_services
            )
            policy = build_effective_support_policy_v222()
            graph = build_gap_graph_v222(
                policy=policy,
                hypothesis_catalog=hypotheses,
                memory=memory,
                topology_edges=case.topology_edges,
                planner_focus_hypothesis_id=None,
                prior_negative_coverage=negative.empty_source_target_keys,
            )
            catalog = build_source_aware_action_catalog_v222(
                candidate_services=case.candidate_services,
                topology=topology,
                replay_capabilities=replay_capabilities,
                executed_action_ids=executed,
                covered_capability_keys=covered_keys,
                remaining_budget=remaining_budget,
            )
            routing = route_gap_aware_actions_v223(
                mode=GapRouterModeV222.GAP_RANKED_TOP_K,
                catalog=catalog,
                gap_graph=graph,
                prior_negative_coverage=negative.empty_source_target_keys,
                predicate_yield_priors=predicate_yield_priors,
                top_k=4,
            )
            bundle = contrastive_resource_action_if_eligible_v225(
                coverage=resource_coverage,
                resources_enabled=resources_enabled,
                unresolved_resource_hypotheses=_resource_hypothesis_count(graph),
                remaining_budget=remaining_budget,
                bundle_mode=True,
            )
            bundle_ever_eligible = bundle_ever_eligible or bundle is not None
            if closure.ambiguity_set is None:
                sets = build_resource_ambiguity_sets_v225(
                    memory=memory,
                    gap_graph=graph,
                    candidate_services=case.candidate_services,
                    topology_edges=case.topology_edges,
                    individual_actions=tuple(
                        item
                        for item in catalog.registry_actions
                        if item.source is EvidenceSourceV22.RESOURCES
                    ),
                    bundle_action=bundle,
                    covered_target_services=(),
                    negative_coverage=negative.empty_source_target_keys,
                )
                ambiguity = (
                    None
                    if not sets
                    else rebuild_ambiguity_set_coverage_v225(
                        ambiguity_set=sets[0],
                        ledger=coverage_ledger,
                    )
                )
            else:
                ambiguity = rebuild_ambiguity_set_coverage_v225(
                    ambiguity_set=closure.ambiguity_set,
                    ledger=coverage_ledger,
                )
            legacy = build_terminal_catalog_v222(
                policy=policy,
                hypothesis_catalog=hypotheses,
                memory=memory,
                gap_graph=graph,
                routed_actions=routing,  # type: ignore[arg-type]
                candidate_services=case.candidate_services,
                topology_edges=case.topology_edges,
                budget_exhausted=remaining_budget <= 0,
                required_source_unavailable=required_source_unavailable,
                conflicting_evidence=False,
            )
            legacy_no_incident = any(
                item.terminal_kind is TerminalKindV222.NO_INCIDENT
                for item in legacy.candidates
            )
            completion_cost = (
                0.0
                if ambiguity is None
                else minimum_completion_cost_v225(
                    ambiguity_set=ambiguity,
                    individual_actions=tuple(
                        item
                        for item in catalog.registry_actions
                        if item.source is EvidenceSourceV22.RESOURCES
                    ),
                    bundle_action=bundle,
                    prefer_bundle=(
                        combination.action_granularity
                        is ActionGranularityV225.CONTRASTIVE_BUNDLE
                    ),
                )
            )
            closure = evaluate_no_incident_set_closure_v225(
                state=closure,
                legacy_no_incident_exposed=legacy_no_incident,
                ambiguity_set=ambiguity,
                target_complete=(
                    resource_coverage.coverage_mode
                    is ReplayTargetCoverageModeV225.TARGET_COMPLETE
                ),
                remaining_evidence_budget=remaining_budget,
                minimum_completion_cost=completion_cost,
            )
            candidates = tuple(
                item
                for item in legacy.candidates
                if not (
                    closure.no_incident_withheld
                    and item.terminal_kind is TerminalKindV222.NO_INCIDENT
                )
            )
            if (
                not candidates
                and closure.abstain_reason is not None
            ):
                candidates = (
                    TerminalCandidateV222(
                        terminal_alias="T00",
                        terminal_id="terminal:abstain",
                        terminal_kind=TerminalKindV222.ABSTAIN,
                        hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
                        root_service=None,
                        mechanism=None,
                        matched_clause_id=None,
                        supporting_evidence_refs=(),
                    ),
                )
            terminals = _catalog_with_candidates(base=legacy, candidates=candidates)
            if (
                ambiguity is not None
                and 0 < len(ambiguity.covered_target_services) < len(ambiguity.target_services)
                and any(
                    item.terminal_kind is TerminalKindV222.NO_INCIDENT
                    for item in terminals.candidates
                )
            ):
                no_incident_after_partial = True

            if terminals.candidates:
                incident_hypotheses = tuple(
                    item.hypothesis_id
                    for item in hypotheses.hypotheses
                    if item.target_service is not None
                )
                aliases = SelectionAliasTableV222.build(
                    hypothesis_ids=incident_hypotheses,
                    action_ids=(),
                    terminal_ids=tuple(item.terminal_id for item in terminals.candidates),
                    evidence_refs=tuple(item.evidence_ref for item in memory.evidence_refs),
                )
                evidence_alias = {
                    item.canonical_id: item.alias for item in aliases.evidence
                }
                request = SelectionTurnRequestV222.build(
                    system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V225,
                    aliases=aliases,
                    visible_state={
                        "candidate_services": case.candidate_services,
                        "terminals": [
                            {
                                "alias": item.terminal_alias,
                                "kind": item.terminal_kind.value,
                                "root_service": item.root_service,
                                "mechanism": (
                                    None
                                    if item.mechanism is None
                                    else item.mechanism.value
                                ),
                                "support": [
                                    evidence_alias[ref]
                                    for ref in item.supporting_evidence_refs
                                ],
                            }
                            for item in terminals.candidates
                        ],
                        "ambiguity_set": (
                            None
                            if closure.ambiguity_set is None
                            else closure.ambiguity_set.model_dump(mode="json")
                        ),
                        "closure": closure.model_dump(mode="json"),
                        "last_contrast": (
                            None
                            if not deltas
                            else deltas[-1].model_dump(mode="json")
                        ),
                    },
                )
                lint_provider_payload_v225(
                    {
                        "visible_state": request.visible_state,
                        "required_shape": {
                            "selection": "Txx",
                            "focus": "NONE",
                        },
                    },
                    payload_class=(
                        "post-bundle-read"
                        if deltas
                        else "post-individual-read"
                        if events
                        else "bootstrap"
                    ),
                )
                try:
                    provider_outcome = provider.complete_turn(
                        request=request,
                        run_id=run_id,
                        max_protocol_repairs=2 - repairs,
                    )
                except SelectionProviderProtocolFailureV222 as error:
                    provider_calls += error.provider_calls
                    repairs += error.protocol_repairs
                    retries += error.transport_retry_count
                    input_tokens += error.input_tokens
                    output_tokens += error.output_tokens
                    total_tokens += error.total_tokens
                    latency_ms += error.latency_ms
                    return result(
                        status=(
                            AmbiguityBundleRunStatusV225.TRANSPORT_FAILED
                            if error.safe_code == "TRANSPORT_FAILED"
                            else AmbiguityBundleRunStatusV225.PROTOCOL_FAILED
                        ),
                        terminal=None,
                        code=error.safe_code,
                    )
                provider_calls += provider_outcome.provider_calls
                first_pass += int(provider_outcome.first_pass_protocol_success)
                post_repair += int(provider_outcome.post_repair_protocol_success)
                repairs += provider_outcome.protocol_repairs
                retries += provider_outcome.transport_retry_count
                input_tokens += provider_outcome.input_tokens
                output_tokens += provider_outcome.output_tokens
                total_tokens += provider_outcome.total_tokens
                latency_ms += provider_outcome.latency_ms
                if provider_outcome.decision.terminal_id is None:
                    return result(
                        status=AmbiguityBundleRunStatusV225.PROTOCOL_FAILED,
                        terminal=None,
                        code="PROVIDER_ACTION_SELECTION_FORBIDDEN",
                    )
                terminal_selections += 1
                terminal = next(
                    item
                    for item in terminals.candidates
                    if item.terminal_id == provider_outcome.decision.terminal_id
                )
                return result(
                    status=AmbiguityBundleRunStatusV225.VALID_TERMINAL,
                    terminal=terminal,
                    code=None,
                )

            ambiguity_decision = (
                None
                if closure.ambiguity_set is None
                else dispatch_ambiguity_action_v225(
                    granularity=combination.action_granularity,
                    ambiguity_set=closure.ambiguity_set,
                    individual_actions=tuple(
                        item
                        for item in catalog.actions
                        if item.source is EvidenceSourceV22.RESOURCES
                    ),
                    bundle_action=bundle,
                    ranked_action_ids=tuple(
                        item.action.action_id for item in routing.ranking
                    ),
                    terminal_ids=(),
                    remaining_evidence_budget=remaining_budget,
                )
            )
            if ambiguity_decision is not None:
                action = ambiguity_decision.action
                is_bundle = ambiguity_decision.reason == "CONTRASTIVE_BUNDLE"
            else:
                try:
                    automatic = automatic_dispatch_v223(
                        mode=EvidenceDispatchModeV223.RUNTIME_TOP1,
                        routing=routing,
                        gap_graph=graph,
                        terminal_ids=(),
                    )
                except AutomaticDispatchUnavailableV223 as error:
                    return result(
                        status=AmbiguityBundleRunStatusV225.PROTOCOL_FAILED,
                        terminal=None,
                        code=str(error),
                    )
                if automatic is None:
                    return result(
                        status=AmbiguityBundleRunStatusV225.PROTOCOL_FAILED,
                        terminal=None,
                        code="AUTOMATIC_DISPATCH_ABSENT",
                    )
                action = next(
                    item for item in routing.actions if item.action_id == automatic.action_id
                )
                is_bundle = False

            before_covered = (
                ()
                if closure.ambiguity_set is None
                else closure.ambiguity_set.covered_target_services
            )
            before_gap = min(
                (_minimum_gap(graph, item.hypothesis_id) for item in graph.hypotheses),
                default=0,
            )
            source_outcome = QuerySpecificReplayBackendV225(case.capture).execute(action)
            projected = (
                source_outcome
                if isinstance(action, ContrastiveResourceActionV225)
                else _memory_outcome(
                    action=action,
                    outcome=source_outcome,
                    run_id=run_id,
                    dispatch_ordinal=len(outcomes) + 1,
                    observed_at=case.capture.captured_at,
                )
            )
            post_outcomes = (*outcomes, projected)
            post_memory = _build_memory(case=case, outcomes=post_outcomes)
            utility = classify_read_utility_v222(
                before_memory=memory,
                after_memory=post_memory,
                read_outcome=source_outcome,
            )
            current_sets = (
                () if closure.ambiguity_set is None else (closure.ambiguity_set,)
            )
            coverage_ledger = record_ambiguity_coverage_event_v225(
                ledger=coverage_ledger,
                action_id=action.action_id,
                source=action.source,
                target_services=action.target_services,
                ambiguity_sets=current_sets,
                outcome_class=utility.outcome_class,
                new_predicate_kinds=tuple(
                    item.value for item in utility.new_predicate_kinds
                ),
                read_ordinal=len(coverage_ledger.events) + 1,
            )
            post_graph = build_gap_graph_v222(
                policy=policy,
                hypothesis_catalog=hypotheses,
                memory=post_memory,
                topology_edges=case.topology_edges,
                planner_focus_hypothesis_id=None,
                prior_negative_coverage=negative.empty_source_target_keys,
            )
            after_gap = min(
                (_minimum_gap(post_graph, item.hypothesis_id) for item in post_graph.hypotheses),
                default=0,
            )
            if isinstance(action, ContrastiveResourceActionV225):
                negative = NegativeCoverageLedgerV222(
                    schema_version="dta-v22.2.negative-coverage-ledger.v1",
                    entries=(
                        *negative.entries,
                        NegativeCoverageEntryV222(
                            action_id=action.action_id,
                            source=action.source.value,
                            target_services=action.target_services,
                            outcome_class=utility.outcome_class,
                            new_predicate_kinds=utility.new_predicate_kinds,
                            new_evidence_refs=utility.new_evidence_refs,
                            queried_capability_keys=action.coverage_keys,
                            minimum_clause_gap_decreased=after_gap < before_gap,
                            hypothesis_contradicted=False,
                        ),
                    ),
                )
            else:
                negative = record_negative_coverage_v222(
                    ledger=negative,
                    action=action,
                    utility=utility,
                    minimum_gap_before=before_gap,
                    minimum_gap_after=after_gap,
                )
            closure = record_no_incident_set_closure_attempt_v225(
                state=closure,
                action=action,
                outcome_class=utility.outcome_class,
            )
            after_covered = (
                ()
                if closure.ambiguity_set is None
                else closure.ambiguity_set.covered_target_services
            )
            if is_bundle:
                deltas.append(
                    build_contrastive_resource_delta_v225(
                        action=ContrastiveResourceActionV225.model_validate(
                            action.model_dump(mode="python")
                        ),
                        before_memory=memory,
                        after_memory=post_memory,
                    )
                )
            events.append(
                AmbiguityDispatchEventV225(
                    ordinal=len(events) + 1,
                    action_id=action.action_id,
                    source=action.source,
                    targets=action.target_services,
                    status=source_outcome.status,
                    outcome_class=utility.outcome_class,
                    new_predicate_kinds=tuple(
                        item.value for item in utility.new_predicate_kinds
                    ),
                    weighted_cost=action.weighted_cost,
                    bundle=is_bundle,
                    ambiguity_set_id=(
                        None
                        if closure.ambiguity_set is None
                        else closure.ambiguity_set.set_id
                    ),
                    covered_targets_before=before_covered,
                    covered_targets_after=after_covered,
                    ranking_action_ids_at_dispatch=tuple(
                        item.action.action_id for item in routing.ranking
                    ),
                    automatic=True,
                )
            )
            outcomes = post_outcomes
            if is_bundle:
                covered_keys = tuple(sorted({*covered_keys, *action.coverage_keys}))
            else:
                executed = tuple(sorted({*executed, action.action_id}))
            remaining_budget = max(0.0, remaining_budget - action.weighted_cost)
            required_source_unavailable = required_source_unavailable or (
                source_outcome.status
                in {
                    ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                    ReadSourceStatusV22.FAILURE_TIMEOUT,
                    ReadSourceStatusV22.FAILURE_SCHEMA,
                }
            )
        return result(
            status=AmbiguityBundleRunStatusV225.PROTOCOL_FAILED,
            terminal=None,
            code="RUNTIME_TURN_BUDGET_EXHAUSTED",
        )
    except Exception:
        return result(
            status=AmbiguityBundleRunStatusV225.RUNNER_EXCEPTION,
            terminal=None,
            code="RUNNER_EXCEPTION",
            uncaught=1,
        )


RunObserverV225 = Callable[[AmbiguityBundleCaseRunV225], None]


def run_ambiguity_bundle_campaign_v225(
    *,
    repository_root: Path,
    case_set_path: Path,
    truth_path: Path,
    coverage_path: Path,
    provider: SelectionProviderProtocolV223,
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
    observer: RunObserverV225 | None = None,
) -> AmbiguityBundleCampaignResultV225:
    case_set = load_practical_case_set_v22(case_set_path)
    coverages = load_replay_target_coverage_set_v225(coverage_path)
    schedule: list[StudyScheduleEntryV225] = []
    runs: list[AmbiguityBundleCaseRunV225] = []
    for case_index, spec in enumerate(case_set.cases):
        for position, combination in enumerate(
            balanced_combination_order_v225(case_index),
            start=1,
        ):
            schedule.append(
                StudyScheduleEntryV225(
                    case_id=spec.case_id,
                    execution_position=position,
                    combination=combination,
                )
            )
            run = execute_ambiguity_bundle_case_v225(
                spec=spec,
                coverage=coverages.require(spec.case_id),
                repository_root=repository_root,
                combination=combination,
                provider=provider,
                predicate_yield_priors=predicate_yield_priors,
            )
            runs.append(run)
            if observer is not None:
                observer(run)
    truths = load_practical_truth_set_v22(truth_path).truths
    hashes: dict[str, set[str]] = {}
    for run in runs:
        hashes.setdefault(run.case_id, set()).add(run.case_bytes_sha256)
    return AmbiguityBundleCampaignResultV225(
        schema_version="dta-v22.5.ambiguity-bundle-campaign.v1",
        schedule=tuple(schedule),
        runs=tuple(runs),
        truths=truths,
        cases_materialized=len(case_set.cases),
        combinations_per_case=4,
        same_case_bytes_all_combinations=all(len(values) == 1 for values in hashes.values()),
        truth_loaded_after_all_four_runs_per_case=True,
        truth_load_count=1,
        uncaught_exceptions=sum(item.uncaught_exceptions for item in runs),
        agent_writes=0,
    )


__all__ = (
    "AmbiguityBundleCampaignResultV225",
    "AmbiguityBundleCaseRunV225",
    "AmbiguityBundleRunStatusV225",
    "AmbiguityDispatchEventV225",
    "SHARED_SELECTION_SYSTEM_PROMPT_V225",
    "StudyCombinationV225",
    "balanced_combination_order_v225",
    "execute_ambiguity_bundle_case_v225",
    "run_ambiguity_bundle_campaign_v225",
)
