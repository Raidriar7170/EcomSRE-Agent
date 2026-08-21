"""Replay-only multi-turn runner for the v2.2.2 gap-routing study."""

from __future__ import annotations

from enum import Enum
import hashlib
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.effective_policy_v222 import build_effective_support_policy_v222
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222, build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    GapRoutingResultV222,
    route_gap_aware_actions_v222,
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
    SelectionDecisionV222,
    SelectionProviderOutcomeV222,
    SelectionProviderProtocolFailureV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.terminal_catalog_v222 import (
    TerminalCatalogV222,
    TerminalKindV222,
    build_terminal_catalog_v222,
)


SHARED_SELECTION_SYSTEM_PROMPT_V222 = (
    "You are a read-only DTA v2.2.2 selection turn. The Post-Read Delta, when "
    "present, is the first decision context. Select exactly one current A alias "
    "with one incident H focus, or one runtime-admissible T alias with NONE focus. "
    "Prefer any admitted non-ABSTAIN T, including NO_INCIDENT; otherwise choose "
    "an A action that reduces a shortest predicate gap. Empty reads are "
    "negative coverage, not proof that a hypothesis is false. ABSTAIN is selectable "
    "only when the runtime exposes an admissible T alias. There is no Agent write, "
    "shell, remediation, Docker, or Runbook authority. Return only the forced short shape."
)


class GapStudyRunStatusV222(str, Enum):
    VALID_TERMINAL = "VALID_TERMINAL"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_EXCEPTION = "RUNNER_EXCEPTION"


class SelectionProviderProtocolV222(Protocol):
    def complete_turn(
        self, *, request: SelectionTurnRequestV222, run_id: str
    ) -> SelectionProviderOutcomeV222: ...


class AdaptiveReadEventV222(DtaModelV22):
    ordinal: StrictInt = Field(ge=1)
    action_id: str
    source: str
    targets: tuple[str, ...]
    status: ReadSourceStatusV22
    outcome_class: ReadUtilityClassV222
    new_predicate_kinds: tuple[str, ...]
    minimum_gap_before: StrictInt = Field(ge=0)
    minimum_gap_after: StrictInt = Field(ge=0)


class GapStudyCaseRunV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-study-case-run.v1"]
    case_id: str
    arm: ControllerArmV22
    router_mode: GapRouterModeV222
    case_bytes_sha256: str
    status: GapStudyRunStatusV222
    terminal: str | None
    root_service: str | None
    mechanism: str | None
    supporting_evidence_refs: tuple[str, ...]
    matched_clause_id: str | None
    adaptive_read_events: tuple[AdaptiveReadEventV222, ...]
    adaptive_reads: StrictInt = Field(ge=0)
    provider_turns: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    first_pass_protocol_successes: StrictInt = Field(ge=0)
    post_repair_protocol_successes: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    transport_retry_count: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    diagnosis_after_read: StrictBool
    terminal_candidate_available_after_read: StrictBool
    negative_coverage_count: StrictInt = Field(ge=0)
    safe_error_code: str | None
    uncaught_exceptions: StrictInt = Field(ge=0, le=1)
    agent_writes: Literal[0]
    planner_ledger_visible: StrictBool


class OracleSimulationReportV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.oracle-simulation-report.v1"]
    feasible_incident_cases: StrictInt = Field(ge=1)
    completed_incident_cases: StrictInt = Field(ge=0)
    completion_rate: StrictFloat = Field(ge=0, le=1)
    runs: tuple[GapStudyCaseRunV222, ...]
    agent_writes: Literal[0]
    oracle_result: Literal[True]


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


def _build_turn(
    *,
    case: NormalizedPracticalCaseV22,
    arm: ControllerArmV22,
    router_mode: GapRouterModeV222,
    replay_capabilities: ReplayCapabilitiesV222,
    outcomes: tuple[MemoryReadOutcomeV22, ...],
    executed_action_ids: tuple[str, ...],
    remaining_budget: float,
    planner_focus_hypothesis_id: str | None,
    negative_coverage: NegativeCoverageLedgerV222,
    last_delta: PostReadDeltaV222 | None,
    required_source_unavailable: bool,
) -> tuple[
    SelectionTurnRequestV222,
    SalientEvidenceMemoryV22,
    GapGraphV222,
    GapRoutingResultV222,
    TerminalCatalogV222,
]:
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
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
        planner_focus_hypothesis_id=(
            planner_focus_hypothesis_id
            if arm is ControllerArmV22.PLANNER_LITE
            else None
        ),
        prior_negative_coverage=negative_coverage.empty_source_target_keys,
    )
    catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=replay_capabilities,
        executed_action_ids=executed_action_ids,
        remaining_budget=remaining_budget,
    )
    routing = route_gap_aware_actions_v222(
        mode=router_mode,
        catalog=catalog,
        gap_graph=graph,
        prior_negative_coverage=negative_coverage.empty_source_target_keys,
        top_k=4,
    )
    terminals = build_terminal_catalog_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=memory,
        gap_graph=graph,
        routed_actions=routing,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        budget_exhausted=remaining_budget <= 0,
        required_source_unavailable=required_source_unavailable,
        conflicting_evidence=False,
    )
    incident_hypotheses = tuple(
        item.hypothesis_id
        for item in hypotheses.hypotheses
        if item.target_service is not None
    )
    aliases = SelectionAliasTableV222.build(
        hypothesis_ids=incident_hypotheses,
        action_ids=tuple(item.action_id for item in routing.actions),
        terminal_ids=tuple(item.terminal_id for item in terminals.candidates),
        evidence_refs=tuple(item.evidence_ref for item in memory.evidence_refs),
    )
    evidence_alias = {
        item.canonical_id: item.alias for item in aliases.evidence
    }
    ranking_by_id = {item.action.action_id: item for item in routing.ranking}
    state: dict[str, object] = {}
    if last_delta is not None:
        state["post_read_delta"] = last_delta.model_dump(mode="json")
    state.update(
        {
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
                    "missing_requirements_observable": ranking_by_id[
                        action.action_id
                    ].distinct_missing_requirements_observable,
                    "prior_empty_penalty": ranking_by_id[
                        action.action_id
                    ].prior_empty_penalty,
                }
                for alias, action in zip(aliases.actions, routing.actions, strict=True)
            ],
            "terminals": [
                {
                    "alias": item.terminal_alias,
                    "kind": item.terminal_kind.value,
                    "root_service": item.root_service,
                    "mechanism": (
                        None if item.mechanism is None else item.mechanism.value
                    ),
                    "support": [
                        evidence_alias[ref] for ref in item.supporting_evidence_refs
                    ],
                }
                for item in terminals.candidates
            ],
            "predicate_gap_graph": [
                {
                    "hypothesis": next(
                        alias.alias
                        for alias in aliases.hypotheses
                        if alias.canonical_id == hypothesis.hypothesis_id
                    ),
                    "mechanism": hypothesis.mechanism.value,
                    "target": hypothesis.target_service,
                    "minimum_missing": hypothesis.minimum_missing_count,
                    "shortest_missing": [
                        [gap.predicate_kind.value for gap in clause.missing_requirements]
                        for clause in hypothesis.clauses
                        if clause.missing_count == hypothesis.minimum_missing_count
                    ],
                }
                for hypothesis in graph.hypotheses
                if not hypothesis.complete
            ],
            "salient_memory": {
                "predicates": [
                    {
                        "kind": item.predicate_kind.value,
                        "service": item.service,
                        "parent_service": item.parent_service,
                        "evidence": [evidence_alias[ref] for ref in item.evidence_refs],
                    }
                    for item in memory.predicates
                ],
                "read_outcomes": [
                    {
                        "source": item.source.value,
                        "status": item.status.value,
                    }
                    for item in memory.observation_summaries
                ],
            },
            "remaining_evidence_budget": remaining_budget,
        }
    )
    if arm is ControllerArmV22.PLANNER_LITE:
        state["planner_ledger"] = {
            "working_hypothesis": (
                None
                if planner_focus_hypothesis_id is None
                else next(
                    item.alias
                    for item in aliases.hypotheses
                    if item.canonical_id == planner_focus_hypothesis_id
                )
            ),
            "negative_coverage": [
                {
                    "source": item.source,
                    "targets": item.target_services,
                    "outcome": item.outcome_class.value,
                }
                for item in negative_coverage.entries
            ],
        }
    request = SelectionTurnRequestV222.build(
        system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V222,
        aliases=aliases,
        visible_state=state,
    )
    return request, memory, graph, routing, terminals


def _result(
    *,
    case: NormalizedPracticalCaseV22,
    arm: ControllerArmV22,
    router_mode: GapRouterModeV222,
    status: GapStudyRunStatusV222,
    terminal: str | None,
    root_service: str | None,
    mechanism: str | None,
    supporting_evidence_refs: tuple[str, ...],
    matched_clause_id: str | None,
    events: tuple[AdaptiveReadEventV222, ...],
    provider_turns: int,
    provider_calls: int,
    first_pass: int,
    post_repair: int,
    repairs: int,
    retries: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    latency_ms: float,
    terminal_after_read: bool,
    negative_coverage_count: int,
    safe_error_code: str | None,
    uncaught_exceptions: int = 0,
) -> GapStudyCaseRunV222:
    return GapStudyCaseRunV222(
        schema_version="dta-v22.2.gap-study-case-run.v1",
        case_id=case.case_id,
        arm=arm,
        router_mode=router_mode,
        case_bytes_sha256=semantic_sha256_v22(case.model_dump(mode="json")),
        status=status,
        terminal=terminal,
        root_service=root_service,
        mechanism=mechanism,
        supporting_evidence_refs=supporting_evidence_refs,
        matched_clause_id=matched_clause_id,
        adaptive_read_events=events,
        adaptive_reads=len(events),
        provider_turns=provider_turns,
        provider_calls=provider_calls,
        first_pass_protocol_successes=first_pass,
        post_repair_protocol_successes=post_repair,
        protocol_repairs=repairs,
        transport_retry_count=retries,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        diagnosis_after_read=terminal == "DIAGNOSED" and bool(events),
        terminal_candidate_available_after_read=terminal_after_read,
        negative_coverage_count=negative_coverage_count,
        safe_error_code=safe_error_code,
        uncaught_exceptions=uncaught_exceptions,
        agent_writes=0,
        planner_ledger_visible=arm is ControllerArmV22.PLANNER_LITE,
    )


def execute_gap_study_case_v222(
    *,
    spec: PracticalCaseSpecV22,
    repository_root: Path,
    arm: ControllerArmV22,
    router_mode: GapRouterModeV222,
    provider: SelectionProviderProtocolV222,
) -> GapStudyCaseRunV222:
    """Execute replay reads only; there is no write, Docker, or Runbook path."""

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
    focus: str | None = None
    negative = NegativeCoverageLedgerV222.empty()
    last_delta: PostReadDeltaV222 | None = None
    required_source_unavailable = False
    events: list[AdaptiveReadEventV222] = []
    provider_turns = provider_calls = first_pass = post_repair = repairs = retries = 0
    input_tokens = output_tokens = total_tokens = 0
    latency_ms = 0.0
    run_id = hashlib.sha256(
        f"{case.case_id}:{arm.value}:{router_mode.value}".encode()
    ).hexdigest()[:32]
    terminal_after_read = False

    def failure(
        status: GapStudyRunStatusV222, code: str, *, uncaught: int = 0
    ) -> GapStudyCaseRunV222:
        return _result(
            case=case,
            arm=arm,
            router_mode=router_mode,
            status=status,
            terminal=None,
            root_service=None,
            mechanism=None,
            supporting_evidence_refs=(),
            matched_clause_id=None,
            events=tuple(events),
            provider_turns=provider_turns,
            provider_calls=provider_calls,
            first_pass=first_pass,
            post_repair=post_repair,
            repairs=repairs,
            retries=retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            terminal_after_read=terminal_after_read,
            negative_coverage_count=len(negative.entries),
            safe_error_code=code,
            uncaught_exceptions=uncaught,
        )

    try:
        for _ in range(5):
            request, memory, graph, routing, terminals = _build_turn(
                case=case,
                arm=arm,
                router_mode=router_mode,
                replay_capabilities=replay_capabilities,
                outcomes=outcomes,
                executed_action_ids=executed,
                remaining_budget=remaining_budget,
                planner_focus_hypothesis_id=focus,
                negative_coverage=negative,
                last_delta=last_delta,
                required_source_unavailable=required_source_unavailable,
            )
            if not request.aliases.actions and not request.aliases.terminals:
                return failure(GapStudyRunStatusV222.PROTOCOL_FAILED, "EMPTY_SELECTION_SURFACE")
            try:
                provider_outcome = provider.complete_turn(request=request, run_id=run_id)
            except SelectionProviderProtocolFailureV222 as error:
                provider_turns += 1
                provider_calls += error.provider_calls
                repairs += error.protocol_repairs
                retries += error.transport_retry_count
                input_tokens += error.input_tokens
                output_tokens += error.output_tokens
                total_tokens += error.total_tokens
                latency_ms += error.latency_ms
                return failure(
                    GapStudyRunStatusV222.TRANSPORT_FAILED
                    if error.safe_code == "TRANSPORT_FAILED"
                    else GapStudyRunStatusV222.PROTOCOL_FAILED,
                    error.safe_code,
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
                terminal = next(
                    item
                    for item in terminals.candidates
                    if item.terminal_id == decision.terminal_id
                )
                return _result(
                    case=case,
                    arm=arm,
                    router_mode=router_mode,
                    status=GapStudyRunStatusV222.VALID_TERMINAL,
                    terminal=terminal.terminal_kind.value,
                    root_service=terminal.root_service,
                    mechanism=(
                        None if terminal.mechanism is None else terminal.mechanism.value
                    ),
                    supporting_evidence_refs=terminal.supporting_evidence_refs,
                    matched_clause_id=terminal.matched_clause_id,
                    events=tuple(events),
                    provider_turns=provider_turns,
                    provider_calls=provider_calls,
                    first_pass=first_pass,
                    post_repair=post_repair,
                    repairs=repairs,
                    retries=retries,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    terminal_after_read=terminal_after_read,
                    negative_coverage_count=len(negative.entries),
                    safe_error_code=None,
                )
            action = next(
                item for item in routing.actions if item.action_id == decision.action_id
            )
            selected_focus = cast(str, decision.focus_hypothesis_id)
            before_gap = _minimum_gap(graph, selected_focus)
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
                before_memory=memory,
                after_memory=post_memory,
                read_outcome=source_outcome,
            )
            post_focus = (
                selected_focus
                if arm is ControllerArmV22.PLANNER_LITE
                else None
            )
            post_graph_before_negative = build_gap_graph_v222(
                policy=build_effective_support_policy_v222(),
                hypothesis_catalog=build_hypothesis_catalog_v22(
                    candidate_services=case.candidate_services
                ),
                memory=post_memory,
                topology_edges=case.topology_edges,
                planner_focus_hypothesis_id=post_focus,
                prior_negative_coverage=negative.empty_source_target_keys,
            )
            after_gap = _minimum_gap(post_graph_before_negative, selected_focus)
            negative = record_negative_coverage_v222(
                ledger=negative,
                action=action,
                utility=utility,
                minimum_gap_before=before_gap,
                minimum_gap_after=after_gap,
            )
            post_request, _, post_graph, post_routing, post_terminals = _build_turn(
                case=case,
                arm=arm,
                router_mode=router_mode,
                replay_capabilities=replay_capabilities,
                outcomes=post_outcomes,
                executed_action_ids=tuple(sorted({*executed, action.action_id})),
                remaining_budget=max(0.0, remaining_budget - action.weighted_cost),
                planner_focus_hypothesis_id=post_focus,
                negative_coverage=negative,
                last_delta=None,
                required_source_unavailable=(
                    required_source_unavailable
                    or source_outcome.status
                    in {
                        ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                        ReadSourceStatusV22.FAILURE_TIMEOUT,
                        ReadSourceStatusV22.FAILURE_SCHEMA,
                    }
                ),
            )
            last_delta = build_post_read_delta_v222(
                action_alias=decision.selection_alias,
                action=action,
                utility=utility,
                minimum_gap_before=before_gap,
                minimum_gap_after=after_gap,
                before_terminal_ids=tuple(
                    item.terminal_id for item in terminals.candidates
                ),
                after_terminal_catalog=post_terminals,
                remaining_top_gaps=post_graph,
                ranked_next_action_aliases=tuple(
                    item.alias for item in post_request.aliases.actions
                ),
                evidence_aliases={
                    item.canonical_id: item.alias for item in post_request.aliases.evidence
                },
            )
            events.append(
                AdaptiveReadEventV222(
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
                )
            )
            terminal_after_read = terminal_after_read or bool(post_terminals.candidates)
            outcomes = post_outcomes
            executed = tuple(sorted({*executed, action.action_id}))
            remaining_budget = max(0.0, remaining_budget - action.weighted_cost)
            focus = post_focus
            required_source_unavailable = (
                required_source_unavailable
                or source_outcome.status
                in {
                    ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                    ReadSourceStatusV22.FAILURE_TIMEOUT,
                    ReadSourceStatusV22.FAILURE_SCHEMA,
                }
            )
        return failure(GapStudyRunStatusV222.PROTOCOL_FAILED, "PROVIDER_TURN_BUDGET_EXHAUSTED")
    except Exception:
        return failure(GapStudyRunStatusV222.RUNNER_EXCEPTION, "RUNNER_EXCEPTION", uncaught=1)


class OracleSelectionProviderV222:
    def __init__(
        self, *, desired_hypothesis_id: str, action_path: tuple[str, ...]
    ) -> None:
        self.desired_hypothesis_id = desired_hypothesis_id
        self.action_path = action_path

    def complete_turn(
        self, *, request: SelectionTurnRequestV222, run_id: str
    ) -> SelectionProviderOutcomeV222:
        del run_id
        terminal = next(
            (
                item
                for item in request.aliases.terminals
                if item.canonical_id.endswith(self.desired_hypothesis_id)
            ),
            None,
        )
        if terminal is not None:
            decision = SelectionDecisionV222(
                selection_alias=terminal.alias,
                focus_alias="NONE",
                action_id=None,
                terminal_id=terminal.canonical_id,
                focus_hypothesis_id=None,
            )
        else:
            action = next(
                (
                    item
                    for desired in self.action_path
                    for item in request.aliases.actions
                    if item.canonical_id == desired
                ),
                request.aliases.actions[0],
            )
            focus = next(
                item
                for item in request.aliases.hypotheses
                if item.canonical_id == self.desired_hypothesis_id
            )
            decision = SelectionDecisionV222(
                selection_alias=action.alias,
                focus_alias=focus.alias,
                action_id=action.canonical_id,
                terminal_id=None,
                focus_hypothesis_id=focus.canonical_id,
            )
        return SelectionProviderOutcomeV222(
            decision=decision,
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
        )


def run_oracle_simulation_v222(
    *, repository_root: Path, case_set_path: Path, truth_path: Path
) -> OracleSimulationReportV222:
    audit = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_set_path,
        truth_path=truth_path,
    )
    audited = {item.case_id: item for item in audit.cases}
    truths = {
        item.case_id: item for item in load_practical_truth_set_v22(truth_path).truths
    }
    runs: list[GapStudyCaseRunV222] = []
    for spec in load_practical_case_set_v22(case_set_path).cases:
        truth = truths[spec.case_id]
        path = audited[spec.case_id]
        if truth.expected_terminal != "DIAGNOSED" or path.shortest_admissible_path is (
            ShortestAdmissiblePathV222.INFEASIBLE
        ):
            continue
        mechanism_suffix = cast(str, truth.expected_mechanism).casefold().replace("_", "-")
        hypothesis_id = (
            f"h:{cast(str, truth.expected_root_service)}:{mechanism_suffix}"
        )
        runs.append(
            execute_gap_study_case_v222(
                spec=spec,
                repository_root=repository_root,
                arm=ControllerArmV22.FLAT_CANONICAL,
                router_mode=GapRouterModeV222.GAP_RANKED_TOP_K,
                provider=OracleSelectionProviderV222(
                    desired_hypothesis_id=hypothesis_id,
                    action_path=cast(tuple[str, ...], path.shortest_action_ids),
                ),
            )
        )
    completed = sum(
        item.status is GapStudyRunStatusV222.VALID_TERMINAL
        and item.terminal == TerminalKindV222.DIAGNOSED.value
        and item.root_service == truths[item.case_id].expected_root_service
        and item.mechanism == truths[item.case_id].expected_mechanism
        for item in runs
    )
    return OracleSimulationReportV222(
        schema_version="dta-v22.2.oracle-simulation-report.v1",
        feasible_incident_cases=len(runs),
        completed_incident_cases=completed,
        completion_rate=completed / len(runs),
        runs=tuple(runs),
        agent_writes=0,
        oracle_result=True,
    )


__all__ = (
    "AdaptiveReadEventV222",
    "GapStudyCaseRunV222",
    "GapStudyRunStatusV222",
    "OracleSelectionProviderV222",
    "OracleSimulationReportV222",
    "SHARED_SELECTION_SYSTEM_PROMPT_V222",
    "execute_gap_study_case_v222",
    "run_oracle_simulation_v222",
)
