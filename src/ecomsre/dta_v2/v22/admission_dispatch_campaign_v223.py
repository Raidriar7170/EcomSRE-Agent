"""Case-interleaved 2x2 admission-closure and dispatch campaign for v2.2.3."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
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
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222, build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import GapRouterModeV222
from ecomsre.dta_v2.v22.gap_router_v223 import (
    GapRoutingResultV223,
    PredicateYieldPriorV223,
    route_gap_aware_actions_v223,
)
from ecomsre.dta_v2.v22.memory import (
    MemoryReadOutcomeV22,
    SalientEvidenceMemoryV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import (
    NegativeCoverageLedgerV222,
    ReadUtilityClassV222,
    classify_read_utility_v222,
    record_negative_coverage_v222,
)
from ecomsre.dta_v2.v22.no_incident_closure_v223 import (
    ClosureOutcomeClassV223,
    NoIncidentClosureModeV223,
    NoIncidentClosureStateV223,
    closure_candidates_from_routing_v223,
    evaluate_no_incident_closure_v223,
    initial_no_incident_closure_state_v223,
    record_no_incident_closure_attempt_v223,
)
from ecomsre.dta_v2.v22.post_read_delta_v222 import (
    PostReadDeltaV222,
    build_post_read_delta_v222,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseSpecV22,
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_replay import NormalizedPracticalCaseV22
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap, _memory_outcome
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v22.replay_capabilities_v222 import (
    ReplayCapabilitiesV222,
    build_replay_capabilities_v222,
    build_source_aware_action_catalog_v222,
)
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionAliasTableV222,
    SelectionProviderProtocolFailureV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.selection_provider_v223 import SelectionProviderProtocolV223
from ecomsre.dta_v2.v22.terminal_catalog_v222 import (
    TerminalCandidateV222,
    TerminalCatalogV222,
    TerminalKindV222,
    build_terminal_catalog_v222,
)


SHARED_SELECTION_SYSTEM_PROMPT_V223 = (
    "You are a read-only DTA v2.2.3 selection turn. Select exactly one current "
    "A alias with one incident H focus, or one runtime-admissible T alias with "
    "NONE focus. A valid T is preferred. Empty reads are negative coverage, not "
    "proof that a hypothesis is false. NO_INCIDENT appears only when the runtime "
    "admits it. There is no Agent write, shell, remediation, Docker, or Runbook "
    "authority. Return only the forced short shape."
)


class StudyCombinationV223(str, Enum):
    MODEL_LEGACY = "MODEL_LEGACY"
    MODEL_CLOSED = "MODEL_CLOSED"
    AUTO_LEGACY = "AUTO_LEGACY"
    AUTO_CLOSED = "AUTO_CLOSED"

    @property
    def dispatch_mode(self) -> EvidenceDispatchModeV223:
        return (
            EvidenceDispatchModeV223.MODEL_TOP4
            if self in {self.MODEL_LEGACY, self.MODEL_CLOSED}
            else EvidenceDispatchModeV223.RUNTIME_TOP1
        )

    @property
    def closure_mode(self) -> NoIncidentClosureModeV223:
        return (
            NoIncidentClosureModeV223.LEGACY
            if self in {self.MODEL_LEGACY, self.AUTO_LEGACY}
            else NoIncidentClosureModeV223.ONE_GAP_RELEVANT_READ
        )


class AdmissionDispatchRunStatusV223(str, Enum):
    VALID_TERMINAL = "VALID_TERMINAL"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_EXCEPTION = "RUNNER_EXCEPTION"


class DispatchReadEventV223(DtaModelV22):
    ordinal: StrictInt = Field(ge=1)
    action_id: str
    source: str
    targets: tuple[str, ...]
    status: ReadSourceStatusV22
    outcome_class: ReadUtilityClassV222
    new_predicate_kinds: tuple[str, ...]
    minimum_gap_before: StrictInt = Field(ge=0)
    minimum_gap_after: StrictInt = Field(ge=0)
    automatic_dispatch: StrictBool
    rank_at_dispatch: StrictInt = Field(ge=1)
    ranking_action_ids_at_dispatch: tuple[str, ...]
    gap_relevant_at_dispatch: StrictBool
    closure_attempt: StrictBool


class AdmissionDispatchCaseRunV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.admission-dispatch-case-run.v1"]
    case_id: str
    combination: StudyCombinationV223
    dispatch_mode: EvidenceDispatchModeV223
    closure_mode: NoIncidentClosureModeV223
    case_bytes_sha256: str
    status: AdmissionDispatchRunStatusV223
    terminal: str | None
    root_service: str | None
    mechanism: str | None
    supporting_evidence_refs: tuple[str, ...]
    matched_clause_id: str | None
    adaptive_read_events: tuple[DispatchReadEventV223, ...]
    adaptive_reads: StrictInt = Field(ge=0)
    model_action_selections: StrictInt = Field(ge=0)
    automatic_top1_dispatches: StrictInt = Field(ge=0)
    provider_turns: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    provider_terminal_selections: StrictInt = Field(ge=0)
    first_pass_protocol_successes: StrictInt = Field(ge=0)
    post_repair_protocol_successes: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retry_count: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    diagnosis_after_read: StrictBool
    terminal_candidate_available_after_read: StrictBool
    negative_coverage_count: StrictInt = Field(ge=0)
    no_incident_first_open_turn: StrictInt | None = Field(default=None, ge=0)
    no_incident_withheld_count: StrictInt = Field(ge=0)
    closure_required_count: StrictInt = Field(ge=0)
    closure_state: NoIncidentClosureStateV223
    turn_zero_top4_action_ids: tuple[str, ...]
    legacy_no_incident_exposed_turn_zero: StrictBool
    safe_error_code: str | None
    uncaught_exceptions: StrictInt = Field(ge=0, le=1)
    agent_writes: Literal[0]


class StudyScheduleEntryV223(DtaModelV22):
    case_id: str
    execution_position: StrictInt = Field(ge=1, le=4)
    combination: StudyCombinationV223


class AdmissionDispatchCampaignResultV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.admission-dispatch-campaign.v1"]
    schedule: tuple[StudyScheduleEntryV223, ...]
    runs: tuple[AdmissionDispatchCaseRunV223, ...]
    truths: tuple[PracticalTruthV22, ...]
    cases_materialized: StrictInt = Field(ge=1)
    combinations_per_case: Literal[4]
    same_case_bytes_all_combinations: StrictBool
    truth_loaded_after_all_four_runs_per_case: StrictBool
    truth_load_count: Literal[1]
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]

    @model_validator(mode="after")
    def require_campaign(self) -> "AdmissionDispatchCampaignResultV223":
        expected = {
            (truth.case_id, combination)
            for truth in self.truths
            for combination in StudyCombinationV223
        }
        actual = {(run.case_id, run.combination) for run in self.runs}
        if actual != expected or len(self.runs) != len(expected):
            raise ValueError("v2.2.3 campaign factorial grid differs")
        if not self.same_case_bytes_all_combinations:
            raise ValueError("v2.2.3 campaign case bytes differ by combination")
        if not self.truth_loaded_after_all_four_runs_per_case:
            raise ValueError("v2.2.3 campaign truth was loaded too early")
        if self.uncaught_exceptions != sum(item.uncaught_exceptions for item in self.runs):
            raise ValueError("v2.2.3 campaign exception accounting differs")
        return self


def balanced_combination_order_v223(case_index: int) -> tuple[StudyCombinationV223, ...]:
    if case_index < 0:
        raise ValueError("case index must be nonnegative")
    base = tuple(StudyCombinationV223)
    offset = case_index % len(base)
    return (*base[offset:], *base[:offset])


def load_frozen_predicate_yield_priors_v223(
    path: Path,
) -> tuple[PredicateYieldPriorV223, ...]:
    raw = json.loads(path.read_bytes())
    if raw.get("schema_version") != "dta-v22.3.development-predicate-yield-prior.v1":
        raise ValueError("v2.2.3 frozen predicate-yield prior schema differs")
    return tuple(
        PredicateYieldPriorV223.model_validate_json(json.dumps(item))
        for item in raw["priors"]
    )


def _minimum_gap(graph: GapGraphV222, hypothesis_id: str) -> int:
    item = next(
        (item for item in graph.hypotheses if item.hypothesis_id == hypothesis_id),
        None,
    )
    return 0 if item is None or item.complete else item.minimum_missing_count


def _build_memory(
    *, case: NormalizedPracticalCaseV22, outcomes: tuple[MemoryReadOutcomeV22, ...]
) -> SalientEvidenceMemoryV22:
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    return memory


def _catalog_with_candidates(
    *, base: TerminalCatalogV222, candidates: tuple[TerminalCandidateV222, ...]
) -> TerminalCatalogV222:
    rebound = tuple(
        item.model_copy(update={"terminal_alias": f"T{index:02d}"})
        for index, item in enumerate(candidates)
    )
    early = any(item.terminal_kind is TerminalKindV222.ABSTAIN for item in rebound)
    payload = {
        "schema_version": "dta-v22.2.terminal-catalog.v1",
        "policy_sha256": base.policy_sha256,
        "memory_sha256": base.memory_sha256,
        "candidates": tuple(item.model_dump(mode="json") for item in rebound),
        "early_abstain_exposed": early,
    }
    return TerminalCatalogV222(
        schema_version="dta-v22.2.terminal-catalog.v1",
        policy_sha256=base.policy_sha256,
        memory_sha256=base.memory_sha256,
        candidates=rebound,
        early_abstain_exposed=early,
        catalog_sha256=semantic_sha256_v22(payload),
    )


@dataclass(frozen=True)
class _TurnV223:
    request: SelectionTurnRequestV222
    memory: SalientEvidenceMemoryV22
    graph: GapGraphV222
    routing: GapRoutingResultV223
    terminals: TerminalCatalogV222
    closure: NoIncidentClosureStateV223
    legacy_no_incident_exposed: bool


def _build_turn(
    *,
    case: NormalizedPracticalCaseV22,
    combination: StudyCombinationV223,
    replay_capabilities: ReplayCapabilitiesV222,
    outcomes: tuple[MemoryReadOutcomeV22, ...],
    executed_action_ids: tuple[str, ...],
    remaining_budget: float,
    negative_coverage: NegativeCoverageLedgerV222,
    last_delta: PostReadDeltaV222 | None,
    required_source_unavailable: bool,
    closure_state: NoIncidentClosureStateV223,
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
) -> _TurnV223:
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    memory = _build_memory(case=case, outcomes=outcomes)
    hypotheses = build_hypothesis_catalog_v22(candidate_services=case.candidate_services)
    policy = build_effective_support_policy_v222()
    graph = build_gap_graph_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=memory,
        topology_edges=case.topology_edges,
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=negative_coverage.empty_source_target_keys,
    )
    catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=replay_capabilities,
        executed_action_ids=executed_action_ids,
        remaining_budget=remaining_budget,
    )
    routing = route_gap_aware_actions_v223(
        mode=GapRouterModeV222.GAP_RANKED_TOP_K,
        catalog=catalog,
        gap_graph=graph,
        prior_negative_coverage=negative_coverage.empty_source_target_keys,
        predicate_yield_priors=predicate_yield_priors,
        top_k=4,
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
        item.terminal_kind is TerminalKindV222.NO_INCIDENT for item in legacy.candidates
    )
    closure = evaluate_no_incident_closure_v223(
        state=closure_state,
        legacy_no_incident_exposed=legacy_no_incident,
        remaining_evidence_budget=remaining_budget,
        ranked_actions=closure_candidates_from_routing_v223(routing),
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
        and closure.closure_attempted
        and not closure.closure_satisfied
        and required_source_unavailable
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
    incident_hypotheses = tuple(
        item.hypothesis_id
        for item in hypotheses.hypotheses
        if item.target_service is not None
    )
    action_ids = (
        ()
        if combination.dispatch_mode is EvidenceDispatchModeV223.RUNTIME_TOP1
        and terminals.candidates
        else tuple(item.action_id for item in routing.actions)
    )
    visible_actions = routing.actions if action_ids else ()
    aliases = SelectionAliasTableV222.build(
        hypothesis_ids=incident_hypotheses,
        action_ids=action_ids,
        terminal_ids=tuple(item.terminal_id for item in terminals.candidates),
        evidence_refs=tuple(item.evidence_ref for item in memory.evidence_refs),
    )
    evidence_alias = {item.canonical_id: item.alias for item in aliases.evidence}
    ranking_by_id = {item.action.action_id: item for item in routing.ranking}
    visible: dict[str, object] = {
        "post_read_delta": None if last_delta is None else last_delta.model_dump(mode="json"),
        "candidate_services": case.candidate_services,
        "topology_edges": case.topology_edges,
        "actions": [
            {
                "alias": alias.alias,
                "source": action.source.value,
                "targets": action.target_services,
                "shortest_clauses_completable": ranking_by_id[
                    action.action_id
                ].shortest_clauses_completable,
                "active_shortest_clauses_completable": ranking_by_id[
                    action.action_id
                ].active_shortest_clauses_completable,
                "prior_empty_penalty": ranking_by_id[action.action_id].prior_empty_penalty,
            }
            for alias, action in zip(aliases.actions, visible_actions, strict=True)
        ],
        "terminals": [
            {
                "alias": item.terminal_alias,
                "kind": item.terminal_kind.value,
                "root_service": item.root_service,
                "mechanism": None if item.mechanism is None else item.mechanism.value,
                "support": [evidence_alias[ref] for ref in item.supporting_evidence_refs],
            }
            for item in terminals.candidates
        ],
        "closure": closure.model_dump(mode="json"),
        "remaining_evidence_budget": remaining_budget,
    }
    request = SelectionTurnRequestV222.build(
        system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V223,
        aliases=aliases,
        visible_state=visible,
    )
    return _TurnV223(
        request=request,
        memory=memory,
        graph=graph,
        routing=routing,
        terminals=terminals,
        closure=closure,
        legacy_no_incident_exposed=legacy_no_incident,
    )


def execute_admission_dispatch_case_v223(
    *,
    spec: PracticalCaseSpecV22,
    repository_root: Path,
    combination: StudyCombinationV223,
    provider: SelectionProviderProtocolV223,
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
) -> AdmissionDispatchCaseRunV223:
    """Execute one replay-only case without loading evaluator truth."""

    case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    bootstrap, _, _, _ = _bootstrap(case=case, topology=topology, run_id="0" * 32)
    outcomes = tuple(bootstrap)
    executed = tuple(item.action_id for item in outcomes)
    replay_capabilities = build_replay_capabilities_v222(
        spec=spec,
        repository_root=repository_root,
    )
    remaining_budget = 3.0
    negative = NegativeCoverageLedgerV222.empty()
    last_delta: PostReadDeltaV222 | None = None
    required_source_unavailable = False
    closure = initial_no_incident_closure_state_v223(combination.closure_mode)
    events: list[DispatchReadEventV223] = []
    provider_turns = provider_calls = first_pass = post_repair = repairs = retries = 0
    terminal_selections = model_actions = automatic_dispatches = 0
    input_tokens = output_tokens = total_tokens = 0
    latency_ms = 0.0
    closure_required_count = withheld_count = 0
    closure_required_seen = False
    no_incident_first_open_turn: int | None = None
    terminal_after_read = False
    turn_zero_top4: tuple[str, ...] = ()
    legacy_no_incident_turn_zero = False
    run_id = hashlib.sha256(
        f"{case.case_id}:{combination.value}".encode()
    ).hexdigest()[:32]

    def result(
        *,
        status: AdmissionDispatchRunStatusV223,
        terminal: TerminalCandidateV222 | None,
        code: str | None,
        uncaught: int = 0,
    ) -> AdmissionDispatchCaseRunV223:
        return AdmissionDispatchCaseRunV223(
            schema_version="dta-v22.3.admission-dispatch-case-run.v1",
            case_id=case.case_id,
            combination=combination,
            dispatch_mode=combination.dispatch_mode,
            closure_mode=combination.closure_mode,
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
            adaptive_read_events=tuple(events),
            adaptive_reads=len(events),
            model_action_selections=model_actions,
            automatic_top1_dispatches=automatic_dispatches,
            provider_turns=provider_turns,
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
            diagnosis_after_read=(
                terminal is not None
                and terminal.terminal_kind is TerminalKindV222.DIAGNOSED
                and bool(events)
            ),
            terminal_candidate_available_after_read=terminal_after_read,
            negative_coverage_count=len(negative.entries),
            no_incident_first_open_turn=no_incident_first_open_turn,
            no_incident_withheld_count=withheld_count,
            closure_required_count=closure_required_count,
            closure_state=closure,
            turn_zero_top4_action_ids=turn_zero_top4,
            legacy_no_incident_exposed_turn_zero=legacy_no_incident_turn_zero,
            safe_error_code=code,
            uncaught_exceptions=uncaught,
            agent_writes=0,
        )

    try:
        for turn_index in range(6):
            turn = _build_turn(
                case=case,
                combination=combination,
                replay_capabilities=replay_capabilities,
                outcomes=outcomes,
                executed_action_ids=executed,
                remaining_budget=remaining_budget,
                negative_coverage=negative,
                last_delta=last_delta,
                required_source_unavailable=required_source_unavailable,
                closure_state=closure,
                predicate_yield_priors=predicate_yield_priors,
            )
            closure = turn.closure
            if turn_index == 0:
                turn_zero_top4 = tuple(
                    item.action.action_id for item in turn.routing.ranking[:4]
                )
                legacy_no_incident_turn_zero = turn.legacy_no_incident_exposed
            if any(
                item.terminal_kind is TerminalKindV222.NO_INCIDENT
                for item in turn.terminals.candidates
            ) and no_incident_first_open_turn is None:
                no_incident_first_open_turn = turn_index
            if closure.closure_required and not closure_required_seen:
                closure_required_count += 1
                closure_required_seen = True
            withheld_count += int(closure.no_incident_withheld)
            if not turn.request.aliases.actions and not turn.request.aliases.terminals:
                return result(
                    status=AdmissionDispatchRunStatusV223.PROTOCOL_FAILED,
                    terminal=None,
                    code="EMPTY_SELECTION_SURFACE",
                )

            automatic = None
            try:
                automatic = automatic_dispatch_v223(
                    mode=combination.dispatch_mode,
                    routing=turn.routing,
                    gap_graph=turn.graph,
                    terminal_ids=tuple(
                        item.terminal_id for item in turn.terminals.candidates
                    ),
                )
            except AutomaticDispatchUnavailableV223 as error:
                return result(
                    status=AdmissionDispatchRunStatusV223.PROTOCOL_FAILED,
                    terminal=None,
                    code=str(error),
                )
            if automatic is not None:
                automatic_dispatches += 1
                action_id = automatic.action_id
                focus = automatic.focus_hypothesis_id
                action_alias = next(
                    item.alias
                    for item in turn.request.aliases.actions
                    if item.canonical_id == action_id
                )
            else:
                try:
                    provider_outcome = provider.complete_turn(
                        request=turn.request,
                        run_id=run_id,
                        max_protocol_repairs=2 - repairs,
                    )
                except SelectionProviderProtocolFailureV222 as error:
                    provider_turns += 1
                    provider_calls += error.provider_calls
                    repairs += error.protocol_repairs
                    retries += error.transport_retry_count
                    input_tokens += error.input_tokens
                    output_tokens += error.output_tokens
                    total_tokens += error.total_tokens
                    latency_ms += error.latency_ms
                    return result(
                        status=(
                            AdmissionDispatchRunStatusV223.TRANSPORT_FAILED
                            if error.safe_code == "TRANSPORT_FAILED"
                            else AdmissionDispatchRunStatusV223.PROTOCOL_FAILED
                        ),
                        terminal=None,
                        code=error.safe_code,
                    )
                provider_turns += 1
                provider_calls += provider_outcome.provider_calls
                first_pass += int(provider_outcome.first_pass_protocol_success)
                post_repair += int(provider_outcome.post_repair_protocol_success)
                repairs += provider_outcome.protocol_repairs
                retries += provider_outcome.transport_retry_count
                input_tokens += provider_outcome.input_tokens
                output_tokens += provider_outcome.output_tokens
                total_tokens += provider_outcome.total_tokens
                latency_ms += provider_outcome.latency_ms
                decision = provider_outcome.decision
                if decision.terminal_id is not None:
                    terminal_selections += 1
                    terminal = next(
                        item
                        for item in turn.terminals.candidates
                        if item.terminal_id == decision.terminal_id
                    )
                    return result(
                        status=AdmissionDispatchRunStatusV223.VALID_TERMINAL,
                        terminal=terminal,
                        code=None,
                    )
                model_actions += 1
                action_id = cast(str, decision.action_id)
                focus = cast(str, decision.focus_hypothesis_id)
                action_alias = decision.selection_alias

            action = next(
                item for item in turn.routing.actions if item.action_id == action_id
            )
            ranked = next(
                item for item in turn.routing.ranking if item.action.action_id == action_id
            )
            before_gap = _minimum_gap(turn.graph, focus)
            source_outcome = QuerySpecificReplayBackendV22(case.capture).execute(action)
            projected = _memory_outcome(
                action=action,
                outcome=source_outcome,
                run_id=run_id,
                dispatch_ordinal=len(outcomes) + 1,
                observed_at=case.capture.captured_at,
            )
            post_outcomes = (*outcomes, projected)
            post_memory = _build_memory(case=case, outcomes=post_outcomes)
            utility = classify_read_utility_v222(
                before_memory=turn.memory,
                after_memory=post_memory,
                read_outcome=source_outcome,
            )
            post_graph_before_negative = build_gap_graph_v222(
                policy=build_effective_support_policy_v222(),
                hypothesis_catalog=build_hypothesis_catalog_v22(
                    candidate_services=case.candidate_services
                ),
                memory=post_memory,
                topology_edges=case.topology_edges,
                planner_focus_hypothesis_id=None,
                prior_negative_coverage=negative.empty_source_target_keys,
            )
            after_gap = _minimum_gap(post_graph_before_negative, focus)
            negative = record_negative_coverage_v222(
                ledger=negative,
                action=action,
                utility=utility,
                minimum_gap_before=before_gap,
                minimum_gap_after=after_gap,
            )
            closure_before = closure
            closure = record_no_incident_closure_attempt_v223(
                state=closure,
                action=next(
                    item
                    for item in closure_candidates_from_routing_v223(turn.routing)
                    if item.action_id == action.action_id
                ),
                outcome_class=ClosureOutcomeClassV223(utility.outcome_class.value),
            )
            next_unavailable = required_source_unavailable or source_outcome.status in {
                ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                ReadSourceStatusV22.FAILURE_TIMEOUT,
                ReadSourceStatusV22.FAILURE_SCHEMA,
            }
            post_turn = _build_turn(
                case=case,
                combination=combination,
                replay_capabilities=replay_capabilities,
                outcomes=post_outcomes,
                executed_action_ids=tuple(sorted({*executed, action.action_id})),
                remaining_budget=max(0.0, remaining_budget - action.weighted_cost),
                negative_coverage=negative,
                last_delta=None,
                required_source_unavailable=next_unavailable,
                closure_state=closure,
                predicate_yield_priors=predicate_yield_priors,
            )
            last_delta = build_post_read_delta_v222(
                action_alias=action_alias,
                action=action,
                utility=utility,
                minimum_gap_before=before_gap,
                minimum_gap_after=after_gap,
                before_terminal_ids=tuple(
                    item.terminal_id for item in turn.terminals.candidates
                ),
                after_terminal_catalog=post_turn.terminals,
                remaining_top_gaps=post_turn.graph,
                ranked_next_action_aliases=tuple(
                    item.alias for item in post_turn.request.aliases.actions
                ),
                evidence_aliases={
                    item.canonical_id: item.alias
                    for item in post_turn.request.aliases.evidence
                },
            )
            events.append(
                DispatchReadEventV223(
                    ordinal=len(events) + 1,
                    action_id=action.action_id,
                    source=action.source.value,
                    targets=action.target_services,
                    status=source_outcome.status,
                    outcome_class=utility.outcome_class,
                    new_predicate_kinds=tuple(
                        item.value for item in utility.new_predicate_kinds
                    ),
                    minimum_gap_before=before_gap,
                    minimum_gap_after=after_gap,
                    automatic_dispatch=automatic is not None,
                    rank_at_dispatch=ranked.rank_ordinal,
                    ranking_action_ids_at_dispatch=tuple(
                        item.action.action_id for item in turn.routing.ranking
                    ),
                    gap_relevant_at_dispatch=(
                        ranked.shortest_clauses_completable > 0
                    ),
                    closure_attempt=(
                        not closure_before.closure_attempted
                        and closure.closure_attempted
                    ),
                )
            )
            terminal_after_read = terminal_after_read or bool(post_turn.terminals.candidates)
            outcomes = post_outcomes
            executed = tuple(sorted({*executed, action.action_id}))
            remaining_budget = max(0.0, remaining_budget - action.weighted_cost)
            required_source_unavailable = next_unavailable
        return result(
            status=AdmissionDispatchRunStatusV223.PROTOCOL_FAILED,
            terminal=None,
            code="PROVIDER_TURN_BUDGET_EXHAUSTED",
        )
    except Exception:
        return result(
            status=AdmissionDispatchRunStatusV223.RUNNER_EXCEPTION,
            terminal=None,
            code="RUNNER_EXCEPTION",
            uncaught=1,
        )


RunObserverV223 = Callable[[AdmissionDispatchCaseRunV223], None]


def run_admission_dispatch_campaign_v223(
    *,
    repository_root: Path,
    case_set_path: Path,
    truth_path: Path,
    provider: SelectionProviderProtocolV223,
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
    observer: RunObserverV223 | None = None,
) -> AdmissionDispatchCampaignResultV223:
    case_set = load_practical_case_set_v22(case_set_path)
    schedule: list[StudyScheduleEntryV223] = []
    runs: list[AdmissionDispatchCaseRunV223] = []
    for case_index, spec in enumerate(case_set.cases):
        for position, combination in enumerate(
            balanced_combination_order_v223(case_index),
            start=1,
        ):
            schedule.append(
                StudyScheduleEntryV223(
                    case_id=spec.case_id,
                    execution_position=position,
                    combination=combination,
                )
            )
            run = execute_admission_dispatch_case_v223(
                spec=spec,
                repository_root=repository_root,
                combination=combination,
                provider=provider,
                predicate_yield_priors=predicate_yield_priors,
            )
            runs.append(run)
            if observer is not None:
                observer(run)
    truths = load_practical_truth_set_v22(truth_path).truths
    case_hashes: dict[str, set[str]] = {}
    for run in runs:
        case_hashes.setdefault(run.case_id, set()).add(run.case_bytes_sha256)
    return AdmissionDispatchCampaignResultV223(
        schema_version="dta-v22.3.admission-dispatch-campaign.v1",
        schedule=tuple(schedule),
        runs=tuple(runs),
        truths=truths,
        cases_materialized=len(case_set.cases),
        combinations_per_case=4,
        same_case_bytes_all_combinations=all(
            len(values) == 1 for values in case_hashes.values()
        ),
        truth_loaded_after_all_four_runs_per_case=True,
        truth_load_count=1,
        uncaught_exceptions=sum(item.uncaught_exceptions for item in runs),
        agent_writes=0,
    )


__all__ = (
    "AdmissionDispatchCampaignResultV223",
    "AdmissionDispatchCaseRunV223",
    "AdmissionDispatchRunStatusV223",
    "DispatchReadEventV223",
    "SHARED_SELECTION_SYSTEM_PROMPT_V223",
    "StudyCombinationV223",
    "balanced_combination_order_v223",
    "execute_admission_dispatch_case_v223",
    "load_frozen_predicate_yield_priors_v223",
    "run_admission_dispatch_campaign_v223",
)
