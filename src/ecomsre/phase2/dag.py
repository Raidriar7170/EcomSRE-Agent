"""Context-aware admission and deterministic scheduling for Phase 2 DAGs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import ValidationError, field_validator, model_validator

from ecomsre.phase1.contracts import Incident
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.contracts import (
    AdditionalInvestigationRequest,
    AdmittedInvestigationGraph,
    AdmittedRefinementFragment,
    BudgetSnapshot,
    COMPARISON_MAX_TOTAL_TOKENS,
    CapacitySlotRequest,
    CapacitySlotStatus,
    Identifier,
    InitialDagAdmission,
    InitialNodeCapacityBinding,
    InvestigationNode,
    InvestigationPlan,
    FixedInvestigationPlan,
    ModelAllowedActions,
    ModelOperation,
    Phase2Model,
    Phase2FailureCode,
    Phase2Variant,
    RunId,
    _initial_dependency_layers,
    _incident_input,
    build_initial_admitted_graph,
    _canonical_sha256,
)


class DagValidationErrorCode(str, Enum):
    RUN_ID_MISMATCH = "RUN_ID_MISMATCH"
    INCIDENT_ID_MISMATCH = "INCIDENT_ID_MISMATCH"
    STALE_BUDGET_SNAPSHOT = "STALE_BUDGET_SNAPSHOT"
    OUTSIDE_ALLOWED_WINDOW = "OUTSIDE_ALLOWED_WINDOW"
    INVALID_DAG = "INVALID_DAG"


class DagValidationError(ValueError):
    """Stable fail-closed error raised before any specialist tool call."""

    def __init__(
        self,
        code: DagValidationErrorCode | Phase2FailureCode,
        detail: str,
    ) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class DagAdmissionContext(Phase2Model):
    schema_version: Literal["phase2.dag-admission-context.v2"]
    run_id: RunId
    incident: Incident
    allowed_started_at: datetime
    allowed_ended_at: datetime
    commander_request_snapshot_id: Identifier
    current_budget_snapshot: BudgetSnapshot

    @field_validator("incident", mode="before")
    @classmethod
    def revalidate_incident(cls, value: object) -> object:
        return _incident_input(value)

    @model_validator(mode="after")
    def require_context_consistency(self) -> DagAdmissionContext:
        if self.allowed_ended_at < self.allowed_started_at:
            raise ValueError("allowed window end precedes its start")
        if self.current_budget_snapshot.run_id != self.run_id:
            raise ValueError("budget snapshot is outside the context run")
        return self


def admit_initial_plan(
    plan: InvestigationPlan,
    context: DagAdmissionContext,
    budget: BudgetLedger,
    *,
    commander_slot_id: str,
    bootstrap_specialist_slot_id: str,
    first_judge_slot_id: str,
    specialist_floor_tokens: int,
    first_judge_floor_tokens: int,
) -> tuple[InitialDagAdmission, BudgetSnapshot]:
    """Atomically bind a validated initial graph to live capacity slots."""

    try:
        validated_plan = InvestigationPlan.model_validate(plan)
    except ValidationError as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "initial plan violates its typed DAG contract",
        ) from error
    try:
        validated_context = DagAdmissionContext.model_validate(context)
    except ValidationError as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "admission context violates its typed contract",
        ) from error

    if (
        type(specialist_floor_tokens) is not int
        or specialist_floor_tokens <= 0
        or type(first_judge_floor_tokens) is not int
        or first_judge_floor_tokens <= 0
    ):
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "admission floors must be positive exact integers",
        )

    if validated_plan.run_id != validated_context.run_id:
        raise DagValidationError(
            DagValidationErrorCode.RUN_ID_MISMATCH,
            "plan run_id does not match the current run",
        )
    if validated_plan.incident_id != validated_context.incident.incident_id:
        raise DagValidationError(
            DagValidationErrorCode.INCIDENT_ID_MISMATCH,
            "plan incident_id does not match the current incident",
        )
    if (
        validated_plan.budget_snapshot_id
        != validated_context.commander_request_snapshot_id
    ):
        raise DagValidationError(
            DagValidationErrorCode.STALE_BUDGET_SNAPSHOT,
            "plan does not bind the Commander request snapshot",
        )

    live = budget.snapshot()
    if validated_context.current_budget_snapshot != live:
        raise DagValidationError(
            Phase2FailureCode.BUDGET_CAS_CONFLICT,
            "complete live admission snapshot differs from the ledger",
        )
    if (
        live.max_model_calls != 8
        or live.max_tool_calls != 8
        or live.max_total_tokens != COMPARISON_MAX_TOTAL_TOKENS
    ):
        raise DagValidationError(
            Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
            "Phase 2 comparison caps must be exactly 8 / 8 / 32000",
        )
    if live.variant is not Phase2Variant.DYNAMIC_MULTI_AGENT:
        raise DagValidationError(
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
            "Commander plan requires a Dynamic Multi-Agent budget",
        )

    incident = validated_context.incident
    for item in validated_plan.nodes:
        inside_incident = (
            incident.started_at <= item.query_started_at
            and item.query_ended_at <= incident.ended_at
        )
        inside_allowed_window = (
            validated_context.allowed_started_at <= item.query_started_at
            and item.query_ended_at <= validated_context.allowed_ended_at
        )
        if not inside_incident or not inside_allowed_window:
            raise DagValidationError(
                DagValidationErrorCode.OUTSIDE_ALLOWED_WINDOW,
                f"node {item.node_id} query escapes its admitted window",
            )

    if (
        live.run_id != validated_context.run_id
        or live.charged_model_calls != 1
        or live.charged_tool_calls != 0
        or live.active_lease_ids
        or live.active_specialist_authorization_ids
    ):
        raise DagValidationError(
            Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
            "live ledger is not at the exact post-Commander boundary",
        )
    if len(
        {
            commander_slot_id,
            bootstrap_specialist_slot_id,
            first_judge_slot_id,
        }
    ) != 3:
        raise DagValidationError(
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
            "named admission slot IDs must be distinct",
        )
    try:
        commander = budget.capacity_slot(commander_slot_id)
        bootstrap = budget.capacity_slot(bootstrap_specialist_slot_id)
        judge = budget.capacity_slot(first_judge_slot_id)
    except BudgetLedgerError as error:
        raise DagValidationError(error.code, str(error)) from error

    exact_active = {bootstrap.slot_id, judge.slot_id}
    if set(live.active_capacity_slot_ids) != exact_active:
        raise DagValidationError(
            Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
            "live active capacity set is not the exact bootstrap/Judge pair",
        )
    for slot in (commander, bootstrap, judge):
        if (
            slot.run_id != live.run_id
            or slot.variant is not live.variant
            or slot.case_id != live.case_id
        ):
            raise DagValidationError(
                Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                "named admission slot is outside the live scope",
            )
    if (
        commander.status is not CapacitySlotStatus.MATERIALIZED
        or commander.permitted_operation is not ModelOperation.COMMANDER_MODEL
        or commander.allowed_actions is not ModelAllowedActions.PLAN_ONLY
        or commander.reserved_model_calls != 1
        or commander.reserved_tool_calls != 0
    ):
        raise DagValidationError(
            (
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
                if commander.status is not CapacitySlotStatus.MATERIALIZED
                else Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
            ),
            "named Commander slot is not the materialized PLAN_ONLY lineage",
        )
    if (
        bootstrap.status is not CapacitySlotStatus.HELD
        or bootstrap.permitted_operation is not ModelOperation.SPECIALIST_MODEL
        or bootstrap.allowed_actions is not ModelAllowedActions.FINDING_ONLY
        or bootstrap.reserved_model_calls != 1
        or bootstrap.reserved_tool_calls != 1
        or bootstrap.minimum_token_floor != specialist_floor_tokens
    ):
        raise DagValidationError(
            (
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
                if bootstrap.status is not CapacitySlotStatus.HELD
                else Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
            ),
            "bootstrap Specialist slot shape or floor is incompatible",
        )
    if (
        judge.status is not CapacitySlotStatus.HELD
        or judge.permitted_operation is not ModelOperation.FIRST_JUDGE_MODEL
        or judge.allowed_actions is not ModelAllowedActions.FINAL_ONLY
        or judge.reserved_model_calls != 1
        or judge.reserved_tool_calls != 0
        or judge.minimum_token_floor != first_judge_floor_tokens
    ):
        raise DagValidationError(
            (
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
                if judge.status is not CapacitySlotStatus.HELD
                else Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
            ),
            "first-Judge slot shape or floor is incompatible",
        )

    try:
        admitted_graph = build_initial_admitted_graph(validated_plan)
        admitted_graph = AdmittedInvestigationGraph.model_validate(admitted_graph)
    except (TypeError, ValidationError, ValueError) as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "initial graph projection or hash is invalid",
        ) from error
    canonical_nodes = tuple(
        item for layer in schedule_layers(validated_plan) for item in layer
    )
    new_requests = tuple(
        CapacitySlotRequest(
            permitted_operation=ModelOperation.SPECIALIST_MODEL,
            allowed_actions=ModelAllowedActions.FINDING_ONLY,
            reserved_model_calls=1,
            reserved_tool_calls=1,
            minimum_token_floor=specialist_floor_tokens,
            expires_at=bootstrap.expires_at,
        )
        for _ in canonical_nodes[1:]
    )
    try:
        ordered_slots, admitted_snapshot = budget.resize_dynamic_initial_plan(
            expected_snapshot_sequence=live.sequence,
            commander_slot_id=commander_slot_id,
            retained_specialist_slot_ids=(bootstrap_specialist_slot_id,),
            new_specialists=new_requests,
            first_judge_slot_id=first_judge_slot_id,
        )
    except BudgetLedgerError as error:
        raise DagValidationError(error.code, str(error)) from error

    if len(ordered_slots) != len(canonical_nodes):
        raise DagValidationError(
            Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
            "ledger returned the wrong number of Specialist slots",
        )
    specialist_ids: list[str] = []
    for slot in ordered_slots:
        if (
            slot.status is not CapacitySlotStatus.HELD
            or slot.run_id != live.run_id
            or slot.variant is not Phase2Variant.DYNAMIC_MULTI_AGENT
            or slot.case_id != live.case_id
            or slot.permitted_operation is not ModelOperation.SPECIALIST_MODEL
            or slot.allowed_actions is not ModelAllowedActions.FINDING_ONLY
            or slot.reserved_model_calls != 1
            or slot.reserved_tool_calls != 1
            or slot.minimum_token_floor != specialist_floor_tokens
        ):
            raise DagValidationError(
                Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                "returned Specialist slot violates the admission shape",
            )
        specialist_ids.append(slot.slot_id)
    expected_active_after = {*specialist_ids, first_judge_slot_id}
    if (
        set(admitted_snapshot.active_capacity_slot_ids) != expected_active_after
        or admitted_snapshot.active_lease_ids
        or admitted_snapshot.active_specialist_authorization_ids
    ):
        raise DagValidationError(
            Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
            "admitted snapshot active set violates the runtime envelope",
        )
    try:
        admission = InitialDagAdmission.model_validate(
            {
                "schema_version": "phase2.initial-dag-admission.v1",
                "admitted_graph": admitted_graph,
                "node_slot_bindings": tuple(
                    InitialNodeCapacityBinding(
                        node_id=item.node_id,
                        specialist_capacity_slot_id=slot.slot_id,
                    )
                    for item, slot in zip(canonical_nodes, ordered_slots, strict=True)
                ),
                "first_judge_capacity_slot_id": first_judge_slot_id,
                "admission_snapshot_id": admitted_snapshot.snapshot_id,
                "admission_snapshot_sequence": admitted_snapshot.sequence,
            }
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise DagValidationError(
            Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
            "runtime admission envelope failed exact validation",
        ) from error
    return admission, admitted_snapshot


def schedule_layers(
    plan: InvestigationPlan | FixedInvestigationPlan,
) -> tuple[tuple[InvestigationNode, ...], ...]:
    """Return deterministic parallel-ready layers for a validated initial DAG."""

    try:
        validated: InvestigationPlan | FixedInvestigationPlan
        if isinstance(plan, FixedInvestigationPlan):
            validated = FixedInvestigationPlan.model_validate(plan)
        else:
            validated = InvestigationPlan.model_validate(plan)
    except ValidationError as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "plan violates its typed DAG contract",
        ) from error
    try:
        return _initial_dependency_layers(validated)
    except ValueError as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "validated plan has no dependency-ready nodes",
        ) from error


def admit_refinement_request(
    request: AdditionalInvestigationRequest,
    initial_graph: AdmittedInvestigationGraph,
    *,
    allowed_started_at: datetime,
    allowed_ended_at: datetime,
) -> AdmittedInvestigationGraph:
    """Validate one refinement fragment and build its canonical combined graph."""

    try:
        validated_request = AdditionalInvestigationRequest.model_validate(request)
        validated_graph = AdmittedInvestigationGraph.model_validate(initial_graph)
    except (TypeError, ValidationError, ValueError) as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "refinement input violates its closed contract",
        ) from error
    if validated_graph.refinement_fragment is not None:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "a second refinement round is forbidden",
        )
    if isinstance(validated_graph.initial_plan, FixedInvestigationPlan):
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "Fixed control graph cannot be refined",
        )
    if (
        validated_request.run_id != validated_graph.run_id
        or validated_request.incident_id != validated_graph.incident_id
        or validated_request.parent_plan_id
        != validated_graph.initial_plan.plan_id
    ):
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "refinement scope differs from the initial graph",
        )
    known_ids = {item.node_id for item in validated_graph.all_nodes}
    for node in validated_request.nodes:
        if node.node_id in known_ids:
            raise DagValidationError(
                DagValidationErrorCode.INVALID_DAG,
                "refinement reuses an admitted node ID",
            )
        if any(dependency not in known_ids for dependency in node.depends_on):
            raise DagValidationError(
                DagValidationErrorCode.INVALID_DAG,
                "refinement depends on a non-initial node",
            )
        if (
            node.query_started_at < allowed_started_at
            or node.query_ended_at > allowed_ended_at
        ):
            raise DagValidationError(
                DagValidationErrorCode.OUTSIDE_ALLOWED_WINDOW,
                "refinement node escapes the admitted replay window",
            )
    fragment = AdmittedRefinementFragment(
        schema_version="phase2.admitted-refinement-fragment.v1",
        request_id=validated_request.request_id,
        parent_plan_id=validated_request.parent_plan_id,
        nodes=validated_request.nodes,
    )
    all_nodes = (*validated_graph.initial_plan.nodes, *fragment.nodes)
    dependency_edges = tuple(
        (dependency, node.node_id)
        for node in all_nodes
        for dependency in node.depends_on
    )
    projection = {
        "schema_version": "phase2.admitted-investigation-graph.v1",
        "run_id": validated_graph.run_id,
        "incident_id": validated_graph.incident_id,
        "initial_plan": validated_graph.initial_plan.model_dump(mode="json"),
        "refinement_fragment": fragment.model_dump(mode="json"),
        "all_nodes": [item.model_dump(mode="json") for item in all_nodes],
        "dependency_edges": [list(edge) for edge in dependency_edges],
    }
    try:
        return AdmittedInvestigationGraph(
            schema_version="phase2.admitted-investigation-graph.v1",
            run_id=validated_graph.run_id,
            incident_id=validated_graph.incident_id,
            initial_plan=validated_graph.initial_plan,
            refinement_fragment=fragment,
            all_nodes=all_nodes,
            dependency_edges=dependency_edges,
            graph_sha256=_canonical_sha256(projection),
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise DagValidationError(
            DagValidationErrorCode.INVALID_DAG,
            "combined refinement graph is invalid",
        ) from error
