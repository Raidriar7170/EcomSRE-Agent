"""Immutable typed boundaries for Phase 2 diagnosis coordination."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    Strict,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ecomsre.phase1.contracts import (
    MAX_EVIDENCE_REFS,
    MAX_HYPOTHESIS_LENGTH,
    MAX_ID_LENGTH,
    MAX_MISSING_EVIDENCE_ITEMS,
    MAX_SERVICE_LENGTH,
    MAX_TEXT_ENTRY_LENGTH,
    ChangesAction,
    Evidence,
    EvidenceAttribute,
    EvidenceRef,
    EvidenceSource,
    FaultMechanism,
    Incident,
    LogsAction,
    MetricsAction,
    Phase1Model,
    RCADecision,
    RCAResult,
    ReadOnlyToolName,
    ToolAction,
    ToolCallRecord,
    TracesAction,
    _reject_evaluator_markers,
    _reject_executable_text,
)
from ecomsre.phase1.validator import revalidate_phase1_model


Identifier = Annotated[
    str,
    Strict(),
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_ID_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
RunId = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Sha256 = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{64}$")]

MAX_PLAN_NODES = 3
MAX_REFINEMENT_NODES = 2
MAX_FINDING_HYPOTHESES = 5
MAX_OBJECTIVE_LENGTH = 1_000
MAX_RATIONALE_LENGTH = 2_000
MAX_ACTIVE_LEASES = 16
COMPARISON_MAX_TOTAL_TOKENS = 32_000

_READ_ONLY_TEXT_RE = re.compile(
    r"(?i)(?:\bevidence://|[;&|`$<>]|\b(?:query_metrics|search_logs|"
    r"search_traces|list_changes)\b|\b(?:docker|kubectl|helm|terraform|"
    r"sudo|bash|zsh|sh|rm|curl|wget|git)\s+)"
)


def _bounded_read_only_text(
    value: object,
    *,
    field_name: str,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must not be empty")
    if len(trimmed) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    try:
        _reject_executable_text(trimmed, field_name=field_name)
    except ValueError as error:
        raise ValueError(f"{field_name} must remain read-only text") from error
    if _READ_ONLY_TEXT_RE.search(trimmed):
        raise ValueError(f"{field_name} must remain read-only text")
    return trimmed


def _require_unique(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicates")
    return values


def _require_current_run_refs(
    values: tuple[str, ...],
    *,
    run_id: str,
    label: str,
) -> None:
    _require_unique(values, label=label)
    for reference in values:
        if reference.split("/")[2] != run_id:
            raise ValueError(f"{label} contains a reference outside the current run")


def _declared_model_input(value: Phase1Model) -> dict[str, object]:
    """Return declared fields without serializing a potentially invalid copy."""

    return {
        field_name: getattr(value, field_name)
        for field_name in type(value).model_fields
    }


def _query_input(value: object) -> object:
    if isinstance(value, (MetricsAction, LogsAction, TracesAction, ChangesAction)):
        return _declared_model_input(value)
    return value


def _evidence_input(value: object) -> object:
    if not isinstance(value, Evidence):
        return value
    payload = _declared_model_input(value)
    payload["attributes"] = tuple(
        _declared_model_input(attribute)
        if isinstance(attribute, EvidenceAttribute)
        else attribute
        for attribute in value.attributes
    )
    return payload


def _evidence_tuple_input(value: object) -> object:
    if not isinstance(value, (tuple, list)):
        return value
    return tuple(_evidence_input(item) for item in value)


def _tool_call_record_input(value: object) -> object:
    if not isinstance(value, ToolCallRecord):
        return value
    payload = _declared_model_input(value)
    payload["action"] = _query_input(value.action)
    payload["evidence"] = _evidence_tuple_input(value.evidence)
    return payload


def _incident_input(value: object) -> object:
    if isinstance(value, Incident):
        return _declared_model_input(value)
    return value


def _rca_result_input(value: object) -> object:
    if isinstance(value, RCAResult):
        return _declared_model_input(value)
    return value


class SpecialistRole(str, Enum):
    METRICS_AGENT = "METRICS_AGENT"
    LOGS_AGENT = "LOGS_AGENT"
    TRACE_AGENT = "TRACE_AGENT"
    CHANGE_AGENT = "CHANGE_AGENT"


class Phase2Variant(str, Enum):
    SINGLE_AGENT = "SINGLE_AGENT"
    FIXED_SPECIALIST_WORKFLOW = "FIXED_SPECIALIST_WORKFLOW"
    DYNAMIC_MULTI_AGENT = "DYNAMIC_MULTI_AGENT"


class BudgetOwnerRole(str, Enum):
    INCIDENT_COMMANDER = "INCIDENT_COMMANDER"
    METRICS_AGENT = SpecialistRole.METRICS_AGENT.value
    LOGS_AGENT = SpecialistRole.LOGS_AGENT.value
    TRACE_AGENT = SpecialistRole.TRACE_AGENT.value
    CHANGE_AGENT = SpecialistRole.CHANGE_AGENT.value
    RCA_JUDGE = "RCA_JUDGE"


class ModelOperation(str, Enum):
    SINGLE_AGENT_MODEL = "SINGLE_AGENT_MODEL"
    COMMANDER_MODEL = "COMMANDER_MODEL"
    SPECIALIST_MODEL = "SPECIALIST_MODEL"
    FIRST_JUDGE_MODEL = "FIRST_JUDGE_MODEL"
    FINAL_JUDGE_MODEL = "FINAL_JUDGE_MODEL"


class ModelAllowedActions(str, Enum):
    PHASE1_ACTION_CATALOG = "PHASE1_ACTION_CATALOG"
    PLAN_ONLY = "PLAN_ONLY"
    FINDING_ONLY = "FINDING_ONLY"
    FINAL_ONLY = "FINAL_ONLY"
    FINAL_OR_REFINEMENT = "FINAL_OR_REFINEMENT"


MODEL_OPERATION_ACTION_KEYS = (
    (
        ModelOperation.SINGLE_AGENT_MODEL,
        ModelAllowedActions.PHASE1_ACTION_CATALOG,
    ),
    (ModelOperation.COMMANDER_MODEL, ModelAllowedActions.PLAN_ONLY),
    (ModelOperation.SPECIALIST_MODEL, ModelAllowedActions.FINDING_ONLY),
    (ModelOperation.FIRST_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY),
    (
        ModelOperation.FIRST_JUDGE_MODEL,
        ModelAllowedActions.FINAL_OR_REFINEMENT,
    ),
    (ModelOperation.FINAL_JUDGE_MODEL, ModelAllowedActions.FINAL_ONLY),
)


class Phase2FailureCode(str, Enum):
    TOKEN_POLICY_MISSING = "TOKEN_POLICY_MISSING"
    TOKEN_POLICY_CORE_HASH_MISMATCH = "TOKEN_POLICY_CORE_HASH_MISMATCH"
    TOKEN_GOLDEN_MANIFEST_MISMATCH = "TOKEN_GOLDEN_MANIFEST_MISMATCH"
    TOKENIZER_VERSION_MISMATCH = "TOKENIZER_VERSION_MISMATCH"
    TOKENIZER_ASSET_MISSING = "TOKENIZER_ASSET_MISSING"
    TOKENIZER_ASSET_SIZE_MISMATCH = "TOKENIZER_ASSET_SIZE_MISMATCH"
    TOKENIZER_ASSET_HASH_MISMATCH = "TOKENIZER_ASSET_HASH_MISMATCH"
    TOKEN_MODEL_MAPPING_MISMATCH = "TOKEN_MODEL_MAPPING_MISMATCH"
    TOKEN_CANONICALIZATION_FAILED = "TOKEN_CANONICALIZATION_FAILED"
    TOKEN_INPUT_TOO_LARGE = "TOKEN_INPUT_TOO_LARGE"
    BUDGET_MINIMUM_FLOOR_UNAVAILABLE = "BUDGET_MINIMUM_FLOOR_UNAVAILABLE"
    BUDGET_SLOT_STALE = "BUDGET_SLOT_STALE"
    BUDGET_SLOT_OWNER_MISMATCH = "BUDGET_SLOT_OWNER_MISMATCH"
    BUDGET_SLOT_ALREADY_CONSUMED = "BUDGET_SLOT_ALREADY_CONSUMED"
    BUDGET_EXACT_EXPANSION_FAILED = "BUDGET_EXACT_EXPANSION_FAILED"
    BUDGET_CAS_CONFLICT = "BUDGET_CAS_CONFLICT"
    PROVIDER_USAGE_MISSING = "PROVIDER_USAGE_MISSING"
    PROVIDER_USAGE_INCONSISTENT = "PROVIDER_USAGE_INCONSISTENT"
    PROVIDER_USAGE_EXCEEDS_LEASE = "PROVIDER_USAGE_EXCEEDS_LEASE"
    PROVIDER_PARAMETER_MISMATCH = "PROVIDER_PARAMETER_MISMATCH"
    COMPARISON_ADAPTER_BYPASS = "COMPARISON_ADAPTER_BYPASS"
    BUDGET_CUMULATIVE_OVERFLOW = "BUDGET_CUMULATIVE_OVERFLOW"
    TOOL_DISPATCH_FAILED = "TOOL_DISPATCH_FAILED"


SourceQueryActionType = Literal["metrics", "logs", "traces", "changes"]


class BudgetLeaseStatus(str, Enum):
    RESERVED = "RESERVED"
    CHARGED = "CHARGED"
    RETURNED = "RETURNED"
    EXPIRED = "EXPIRED"


class CapacitySlotStatus(str, Enum):
    HELD = "HELD"
    MATERIALIZED = "MATERIALIZED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class SpecialistAuthorizationStatus(str, Enum):
    TOOL_AUTHORIZED = "TOOL_AUTHORIZED"
    TOOL_DISPATCHING = "TOOL_DISPATCHING"
    TOOL_CHARGED = "TOOL_CHARGED"
    MODEL_LEASED = "MODEL_LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RELEASED = "RELEASED"


class ConditionalRefinementBundleStatus(str, Enum):
    HELD = "HELD"
    PARTIALLY_CONSUMED = "PARTIALLY_CONSUMED"
    RELEASED = "RELEASED"
    COMPLETED = "COMPLETED"


class Phase2Model(Phase1Model):
    """Closed Phase 2 model that always revalidates copied instances."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class ModelInputEnvelope(Phase2Model):
    """Canonical model-visible input plus its exact response contract."""

    schema_version: Literal["phase2.model-input-envelope.v1"]
    operation: ModelOperation
    allowed_actions: ModelAllowedActions
    model_snapshot: Literal["gpt-5.4-mini-2026-03-17"]
    system_instruction: str = Field(min_length=1)
    request: dict[str, JsonValue]
    response_schema: dict[str, JsonValue]

    @model_validator(mode="before")
    @classmethod
    def reject_evaluator_only_markers(cls, value: object) -> object:
        """Scan nested data while allowing the hash-bound protective instruction."""

        if isinstance(value, cls):
            _reject_evaluator_markers(value.request)
            _reject_evaluator_markers(value.response_schema)
        elif isinstance(value, dict):
            _reject_evaluator_markers(value.get("request"))
            _reject_evaluator_markers(value.get("response_schema"))
        return value

    @model_validator(mode="after")
    def require_closed_operation_action_key(self) -> ModelInputEnvelope:
        if (self.operation, self.allowed_actions) not in MODEL_OPERATION_ACTION_KEYS:
            raise ValueError("operation and allowed actions are not a closed key")
        return self


_SOURCE_BINDINGS: dict[
    EvidenceSource,
    tuple[
        SpecialistRole,
        ReadOnlyToolName,
        str,
        type[MetricsAction]
        | type[LogsAction]
        | type[TracesAction]
        | type[ChangesAction],
    ],
] = {
    EvidenceSource.METRICS: (
        SpecialistRole.METRICS_AGENT,
        ReadOnlyToolName.QUERY_METRICS,
        "metrics",
        MetricsAction,
    ),
    EvidenceSource.LOGS: (
        SpecialistRole.LOGS_AGENT,
        ReadOnlyToolName.SEARCH_LOGS,
        "logs",
        LogsAction,
    ),
    EvidenceSource.TRACES: (
        SpecialistRole.TRACE_AGENT,
        ReadOnlyToolName.SEARCH_TRACES,
        "traces",
        TracesAction,
    ),
    EvidenceSource.CHANGES: (
        SpecialistRole.CHANGE_AGENT,
        ReadOnlyToolName.LIST_CHANGES,
        "changes",
        ChangesAction,
    ),
}

SPECIALIST_TOOL_BINDINGS: dict[
    SpecialistRole,
    tuple[BudgetOwnerRole, EvidenceSource, ReadOnlyToolName],
] = {
    SpecialistRole.METRICS_AGENT: (
        BudgetOwnerRole.METRICS_AGENT,
        EvidenceSource.METRICS,
        ReadOnlyToolName.QUERY_METRICS,
    ),
    SpecialistRole.LOGS_AGENT: (
        BudgetOwnerRole.LOGS_AGENT,
        EvidenceSource.LOGS,
        ReadOnlyToolName.SEARCH_LOGS,
    ),
    SpecialistRole.TRACE_AGENT: (
        BudgetOwnerRole.TRACE_AGENT,
        EvidenceSource.TRACES,
        ReadOnlyToolName.SEARCH_TRACES,
    ),
    SpecialistRole.CHANGE_AGENT: (
        BudgetOwnerRole.CHANGE_AGENT,
        EvidenceSource.CHANGES,
        ReadOnlyToolName.LIST_CHANGES,
    ),
}


class SourceCapability(Phase2Model):
    source: EvidenceSource
    specialist_role: SpecialistRole
    tool_name: ReadOnlyToolName
    action_type: SourceQueryActionType

    @model_validator(mode="after")
    def require_closed_source_mapping(self) -> SourceCapability:
        expected_role, expected_tool, expected_action, _ = _SOURCE_BINDINGS[
            self.source
        ]
        if (
            self.specialist_role is not expected_role
            or self.tool_name is not expected_tool
            or self.action_type != expected_action
        ):
            raise ValueError("source capability mapping is inconsistent")
        return self


def _require_source_binding(
    *,
    source: EvidenceSource,
    specialist_role: SpecialistRole,
    tool_name: ReadOnlyToolName,
    query: ToolAction,
) -> None:
    expected_role, expected_tool, action_type, query_type = _SOURCE_BINDINGS[source]
    try:
        query_type.model_validate(_declared_model_input(query))
    except ValidationError as error:
        raise ValueError(
            "source-role-tool-query binding is inconsistent"
        ) from error
    if (
        specialist_role is not expected_role
        or tool_name is not expected_tool
        or type(query) is not query_type
        or query.action_type != action_type
    ):
        raise ValueError("source-role-tool-query binding is inconsistent")


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def canonical_tool_call_record_sha256(record: ToolCallRecord) -> str:
    """Hash the exact revalidated Phase 1 tool-record JSON projection."""

    if type(record) is not ToolCallRecord:
        raise ValueError("tool_call_record must be the exact ToolCallRecord type")
    validated = revalidate_phase1_model(record, ToolCallRecord)
    return _canonical_sha256(validated.model_dump(mode="json"))


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field_name} must be a time-aware UTC timestamp")
    return value


class InvestigationNode(Phase2Model):
    schema_version: Literal["phase2.investigation-node.v1"]
    node_id: Identifier
    source: EvidenceSource
    specialist_role: SpecialistRole
    tool_name: ReadOnlyToolName
    query: ToolAction
    depends_on: tuple[Identifier, ...] = Field(max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE_LENGTH)
    query_started_at: datetime
    query_ended_at: datetime
    priority: StrictInt = Field(ge=0, le=10_000)

    @field_validator("objective", mode="before")
    @classmethod
    def require_read_only_objective(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="objective",
            maximum=MAX_OBJECTIVE_LENGTH,
        )

    @field_validator("query", mode="before")
    @classmethod
    def revalidate_query_input(cls, value: object) -> object:
        return _query_input(value)

    @field_validator("depends_on")
    @classmethod
    def require_unique_dependencies(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _require_unique(values, label="depends_on")

    @model_validator(mode="after")
    def require_node_consistency(self) -> InvestigationNode:
        _require_source_binding(
            source=self.source,
            specialist_role=self.specialist_role,
            tool_name=self.tool_name,
            query=self.query,
        )
        if self.node_id in self.depends_on:
            raise ValueError("node has a self-dependency")
        if (
            self.query_started_at != self.query.started_at
            or self.query_ended_at != self.query.ended_at
        ):
            raise ValueError("query window conflicts with typed query window")
        return self


class InvestigationPlan(Phase2Model):
    schema_version: Literal["phase2.investigation-plan.v1"]
    run_id: RunId
    incident_id: Identifier
    plan_id: Identifier
    nodes: tuple[InvestigationNode, ...] = Field(
        min_length=1,
        max_length=MAX_PLAN_NODES,
    )
    planning_rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    budget_snapshot_id: Identifier

    @field_validator("planning_rationale", mode="before")
    @classmethod
    def require_read_only_rationale(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="planning_rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @model_validator(mode="after")
    def require_valid_initial_dag(self) -> InvestigationPlan:
        node_by_id: dict[str, InvestigationNode] = {}
        for item in self.nodes:
            if item.node_id in node_by_id:
                raise ValueError("plan contains duplicate node_id")
            node_by_id[item.node_id] = item
        for item in self.nodes:
            for dependency in item.depends_on:
                if dependency not in node_by_id:
                    raise ValueError("plan contains an unknown dependency")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("plan contains a dependency cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in node_by_id[node_id].depends_on:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_by_id:
            visit(node_id)
        return self


class FixedInvestigationPlan(Phase2Model):
    """Runtime-owned four-source control plan; never a Commander response."""

    schema_version: Literal["phase2.fixed-investigation-plan.v1"]
    run_id: RunId
    incident_id: Identifier
    plan_id: Identifier
    nodes: tuple[InvestigationNode, ...] = Field(min_length=4, max_length=4)
    planning_rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    budget_snapshot_id: Identifier

    @field_validator("planning_rationale", mode="before")
    @classmethod
    def require_read_only_rationale(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="planning_rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @model_validator(mode="after")
    def require_valid_fixed_dag(self) -> FixedInvestigationPlan:
        node_by_id: dict[str, InvestigationNode] = {}
        for item in self.nodes:
            if item.node_id in node_by_id:
                raise ValueError("plan contains duplicate node_id")
            node_by_id[item.node_id] = item
        for item in self.nodes:
            if any(dependency not in node_by_id for dependency in item.depends_on):
                raise ValueError("plan contains an unknown dependency")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("plan contains a dependency cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in node_by_id[node_id].depends_on:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_by_id:
            visit(node_id)
        return self


class AdmittedRefinementFragment(Phase2Model):
    schema_version: Literal["phase2.admitted-refinement-fragment.v1"]
    request_id: Identifier
    parent_plan_id: Identifier
    nodes: tuple[InvestigationNode, ...] = Field(
        min_length=1,
        max_length=MAX_REFINEMENT_NODES,
    )

    @model_validator(mode="after")
    def require_unique_refinement_nodes(self) -> AdmittedRefinementFragment:
        node_ids = tuple(item.node_id for item in self.nodes)
        _require_unique(node_ids, label="refinement nodes")
        return self


def _initial_dependency_layers(
    plan: InvestigationPlan | FixedInvestigationPlan,
) -> tuple[tuple[InvestigationNode, ...], ...]:
    remaining = {item.node_id: item for item in plan.nodes}
    completed: set[str] = set()
    layers: list[tuple[InvestigationNode, ...]] = []
    while remaining:
        ready = tuple(
            sorted(
                (
                    item
                    for item in remaining.values()
                    if set(item.depends_on).issubset(completed)
                ),
                key=lambda item: (item.priority, item.node_id),
            )
        )
        if not ready:
            raise ValueError("validated initial plan has no dependency-ready nodes")
        layers.append(ready)
        for item in ready:
            completed.add(item.node_id)
            del remaining[item.node_id]
    return tuple(layers)


class AdmittedInvestigationGraph(Phase2Model):
    """Runtime-owned initial and optional one-round refinement graph."""

    schema_version: Literal["phase2.admitted-investigation-graph.v1"]
    run_id: RunId
    incident_id: Identifier
    initial_plan: InvestigationPlan | FixedInvestigationPlan
    refinement_fragment: AdmittedRefinementFragment | None = None
    all_nodes: tuple[InvestigationNode, ...] = Field(
        min_length=1,
        max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES,
    )
    dependency_edges: tuple[tuple[Identifier, Identifier], ...] = Field(
        max_length=(MAX_PLAN_NODES + MAX_REFINEMENT_NODES) ** 2,
    )
    graph_sha256: Sha256

    @model_validator(mode="before")
    @classmethod
    def require_combined_node_limit(cls, value: object) -> object:
        if isinstance(value, cls):
            initial_plan: object = value.initial_plan
            refinement_fragment: object = value.refinement_fragment
        elif isinstance(value, dict):
            initial_plan = value.get("initial_plan")
            refinement_fragment = value.get("refinement_fragment")
        else:
            return value

        def declared_nodes(container: object) -> tuple[object, ...]:
            if isinstance(container, (InvestigationPlan, AdmittedRefinementFragment)):
                return tuple(container.nodes)
            if isinstance(container, dict):
                nodes = container.get("nodes")
                if isinstance(nodes, (tuple, list)):
                    return tuple(nodes)
            return ()

        combined_count = len(declared_nodes(initial_plan)) + len(
            declared_nodes(refinement_fragment)
        )
        if combined_count > MAX_PLAN_NODES + MAX_REFINEMENT_NODES:
            raise ValueError("combined graph cannot exceed five nodes")
        return value

    def _canonical_projection(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "incident_id": self.incident_id,
            "initial_plan": self.initial_plan.model_dump(mode="json"),
            "refinement_fragment": (
                None
                if self.refinement_fragment is None
                else self.refinement_fragment.model_dump(mode="json")
            ),
            "all_nodes": [item.model_dump(mode="json") for item in self.all_nodes],
            "dependency_edges": [list(edge) for edge in self.dependency_edges],
        }

    @model_validator(mode="after")
    def require_exact_runtime_projection(self) -> AdmittedInvestigationGraph:
        if (
            self.run_id != self.initial_plan.run_id
            or self.incident_id != self.initial_plan.incident_id
        ):
            raise ValueError("admitted graph contains cross-run or cross-incident data")
        fragment = self.refinement_fragment
        if isinstance(self.initial_plan, FixedInvestigationPlan) and fragment is not None:
            raise ValueError("Fixed control graph cannot contain refinement nodes")
        if fragment is not None and fragment.parent_plan_id != self.initial_plan.plan_id:
            raise ValueError("refinement fragment conflicts with its parent plan")

        expected_nodes = self.initial_plan.nodes + (
            () if fragment is None else fragment.nodes
        )
        if self.all_nodes != expected_nodes:
            raise ValueError("all_nodes projection conflicts with admitted graph")

        node_by_id: dict[str, InvestigationNode] = {}
        for item in expected_nodes:
            if item.node_id in node_by_id:
                raise ValueError("combined graph contains duplicate node_id")
            node_by_id[item.node_id] = item

        for item in expected_nodes:
            for dependency in item.depends_on:
                if dependency not in node_by_id:
                    raise ValueError("combined graph contains an unknown dependency")

        available_dependency_ids = {
            item.node_id for item in self.initial_plan.nodes
        }
        if fragment is not None:
            for item in fragment.nodes:
                if any(
                    dependency not in available_dependency_ids
                    for dependency in item.depends_on
                ):
                    raise ValueError(
                        "refinement node depends on a later refinement node"
                    )
                available_dependency_ids.add(item.node_id)

        expected_edges = tuple(
            (dependency, item.node_id)
            for item in expected_nodes
            for dependency in item.depends_on
        )
        if self.dependency_edges != expected_edges:
            raise ValueError(
                "dependency_edges projection conflicts with admitted graph"
            )

        if self.graph_sha256 != _canonical_sha256(self._canonical_projection()):
            raise ValueError("graph hash conflicts with canonical graph projection")
        return self


def build_initial_admitted_graph(
    plan: InvestigationPlan,
) -> AdmittedInvestigationGraph:
    validated = InvestigationPlan.model_validate(plan)
    all_nodes = validated.nodes
    dependency_edges = tuple(
        (dependency, item.node_id)
        for item in all_nodes
        for dependency in item.depends_on
    )
    projection = {
        "schema_version": "phase2.admitted-investigation-graph.v1",
        "run_id": validated.run_id,
        "incident_id": validated.incident_id,
        "initial_plan": validated.model_dump(mode="json"),
        "refinement_fragment": None,
        "all_nodes": [item.model_dump(mode="json") for item in all_nodes],
        "dependency_edges": [list(edge) for edge in dependency_edges],
    }
    return AdmittedInvestigationGraph(
        schema_version="phase2.admitted-investigation-graph.v1",
        run_id=validated.run_id,
        incident_id=validated.incident_id,
        initial_plan=validated,
        refinement_fragment=None,
        all_nodes=all_nodes,
        dependency_edges=dependency_edges,
        graph_sha256=_canonical_sha256(projection),
    )


def build_fixed_admitted_graph(
    plan: FixedInvestigationPlan,
) -> AdmittedInvestigationGraph:
    """Build the four-source Fixed control graph outside Commander contracts."""

    validated = FixedInvestigationPlan.model_validate(plan)
    all_nodes = validated.nodes
    dependency_edges = tuple(
        (dependency, item.node_id)
        for item in all_nodes
        for dependency in item.depends_on
    )
    projection = {
        "schema_version": "phase2.admitted-investigation-graph.v1",
        "run_id": validated.run_id,
        "incident_id": validated.incident_id,
        "initial_plan": validated.model_dump(mode="json"),
        "refinement_fragment": None,
        "all_nodes": [item.model_dump(mode="json") for item in all_nodes],
        "dependency_edges": [list(edge) for edge in dependency_edges],
    }
    return AdmittedInvestigationGraph(
        schema_version="phase2.admitted-investigation-graph.v1",
        run_id=validated.run_id,
        incident_id=validated.incident_id,
        initial_plan=validated,
        refinement_fragment=None,
        all_nodes=all_nodes,
        dependency_edges=dependency_edges,
        graph_sha256=_canonical_sha256(projection),
    )


class InitialNodeCapacityBinding(Phase2Model):
    node_id: Identifier
    specialist_capacity_slot_id: Identifier


class InitialDagAdmission(Phase2Model):
    schema_version: Literal["phase2.initial-dag-admission.v1"]
    admitted_graph: AdmittedInvestigationGraph
    node_slot_bindings: tuple[InitialNodeCapacityBinding, ...]
    first_judge_capacity_slot_id: Identifier
    admission_snapshot_id: Identifier
    admission_snapshot_sequence: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_exact_initial_binding(self) -> InitialDagAdmission:
        expected = tuple(
            item.node_id
            for layer in _initial_dependency_layers(self.admitted_graph.initial_plan)
            for item in layer
        )
        actual = tuple(item.node_id for item in self.node_slot_bindings)
        specialist_ids = tuple(
            item.specialist_capacity_slot_id for item in self.node_slot_bindings
        )
        if not actual or actual != expected:
            raise ValueError("initial node-slot projection is not canonical and exact")
        _require_unique(specialist_ids, label="initial Specialist capacity slots")
        if self.first_judge_capacity_slot_id in specialist_ids:
            raise ValueError("first-Judge slot collides with a Specialist slot")
        return self


class CapacitySlotRequest(Phase2Model):
    permitted_operation: ModelOperation
    allowed_actions: ModelAllowedActions
    reserved_model_calls: StrictInt = Field(ge=1, le=1)
    reserved_tool_calls: StrictInt = Field(ge=0, le=1)
    minimum_token_floor: StrictInt = Field(gt=0)
    expires_at: datetime

    @model_validator(mode="after")
    def require_operation_shape(self) -> CapacitySlotRequest:
        expected_actions: dict[ModelOperation, set[ModelAllowedActions]] = {
            ModelOperation.SINGLE_AGENT_MODEL: {
                ModelAllowedActions.PHASE1_ACTION_CATALOG
            },
            ModelOperation.COMMANDER_MODEL: {ModelAllowedActions.PLAN_ONLY},
            ModelOperation.SPECIALIST_MODEL: {ModelAllowedActions.FINDING_ONLY},
            ModelOperation.FIRST_JUDGE_MODEL: {
                ModelAllowedActions.FINAL_ONLY,
                ModelAllowedActions.FINAL_OR_REFINEMENT,
            },
            ModelOperation.FINAL_JUDGE_MODEL: {ModelAllowedActions.FINAL_ONLY},
        }
        expected_tool_calls = (
            1 if self.permitted_operation is ModelOperation.SPECIALIST_MODEL else 0
        )
        if self.allowed_actions not in expected_actions[self.permitted_operation]:
            raise ValueError("operation and allowed actions are incompatible")
        if self.reserved_tool_calls != expected_tool_calls:
            raise ValueError("operation and reserved tool count are incompatible")
        _require_utc(self.expires_at, field_name="expires_at")
        return self


class UnboundCapacitySlot(Phase2Model):
    schema_version: Literal["phase2.unbound-capacity-slot.v1"]
    slot_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    permitted_operation: ModelOperation
    allowed_actions: ModelAllowedActions
    reserved_model_calls: StrictInt = Field(ge=1, le=1)
    reserved_tool_calls: StrictInt = Field(ge=0, le=1)
    minimum_token_floor: StrictInt = Field(gt=0)
    creating_snapshot_sequence: StrictInt = Field(ge=0)
    issued_at: datetime
    expires_at: datetime
    status: CapacitySlotStatus

    @model_validator(mode="after")
    def require_slot_shape(self) -> UnboundCapacitySlot:
        CapacitySlotRequest(
            permitted_operation=self.permitted_operation,
            allowed_actions=self.allowed_actions,
            reserved_model_calls=self.reserved_model_calls,
            reserved_tool_calls=self.reserved_tool_calls,
            minimum_token_floor=self.minimum_token_floor,
            expires_at=self.expires_at,
        )
        _require_utc(self.issued_at, field_name="issued_at")
        if self.expires_at < self.issued_at:
            raise ValueError("capacity slot expires before it is issued")
        return self


class SpecialistExecutionAuthorization(Phase2Model):
    schema_version: Literal["phase2.specialist-execution-authorization.v2"]
    authorization_id: Identifier
    capacity_slot_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    creating_snapshot_sequence: StrictInt = Field(ge=0)
    owner_role: BudgetOwnerRole
    owner_node_id: Identifier
    source: EvidenceSource
    tool_name: ReadOnlyToolName
    permitted_operation: Literal[ModelOperation.SPECIALIST_MODEL]
    allowed_actions: Literal[ModelAllowedActions.FINDING_ONLY]
    reserved_model_calls: StrictInt = Field(ge=1, le=1)
    reserved_tool_calls: StrictInt = Field(ge=1, le=1)
    minimum_token_floor: StrictInt = Field(gt=0)
    issued_at: datetime
    expires_at: datetime
    status: SpecialistAuthorizationStatus
    actual_tool_calls: StrictInt = Field(ge=0, le=1)
    model_lease_id: Identifier | None = None
    dispatch_claim_snapshot_sequence: StrictInt | None = None
    tool_charged_snapshot_sequence: StrictInt | None = None
    tool_call_record_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def require_authorization_shape(self) -> SpecialistExecutionAuthorization:
        expected_specialist, expected_tool, _, _ = _SOURCE_BINDINGS[self.source]
        expected_role, expected_source, explicit_tool = SPECIALIST_TOOL_BINDINGS[
            expected_specialist
        ]
        if (
            self.owner_role is not expected_role
            or self.source is not expected_source
            or self.tool_name is not expected_tool
            or self.tool_name is not explicit_tool
        ):
            raise ValueError("specialist owner, source, and tool are inconsistent")
        if self.authorization_id == self.capacity_slot_id:
            raise ValueError("authorization and originating slot IDs must differ")
        _require_utc(self.issued_at, field_name="issued_at")
        _require_utc(self.expires_at, field_name="expires_at")
        if self.expires_at < self.issued_at:
            raise ValueError("authorization expires before it is issued")

        claim = self.dispatch_claim_snapshot_sequence
        charge = self.tool_charged_snapshot_sequence
        record_hash = self.tool_call_record_sha256
        if (charge is None) != (record_hash is None):
            raise ValueError("charge sequence and record hash must be paired")
        if claim is not None and claim <= self.creating_snapshot_sequence:
            raise ValueError("dispatch claim must follow authorization creation")
        if charge is not None and (claim is None or charge <= claim):
            raise ValueError("tool charge must follow the dispatch claim")

        if self.status is SpecialistAuthorizationStatus.TOOL_AUTHORIZED:
            if (
                self.actual_tool_calls != 0
                or self.model_lease_id is not None
                or claim is not None
                or charge is not None
                or record_hash is not None
            ):
                raise ValueError("tool-authorized record contains later-stage state")
        elif self.status is SpecialistAuthorizationStatus.TOOL_DISPATCHING:
            if (
                self.actual_tool_calls != 0
                or self.model_lease_id is not None
                or claim is None
                or charge is not None
                or record_hash is not None
            ):
                raise ValueError("tool-dispatching record is inconsistent")
        elif self.status is SpecialistAuthorizationStatus.TOOL_CHARGED:
            if (
                self.actual_tool_calls != 1
                or self.model_lease_id is not None
                or claim is None
                or charge is None
                or record_hash is None
            ):
                raise ValueError("tool-charged record is inconsistent")
        elif self.status in {
            SpecialistAuthorizationStatus.MODEL_LEASED,
            SpecialistAuthorizationStatus.COMPLETED,
        }:
            if (
                self.actual_tool_calls != 1
                or self.model_lease_id is None
                or claim is None
                or charge is None
                or record_hash is None
            ):
                raise ValueError("model-stage authorization is inconsistent")
        elif self.status is SpecialistAuthorizationStatus.RELEASED:
            if (
                self.actual_tool_calls != 0
                or self.model_lease_id is not None
                or claim is not None
                or charge is not None
                or record_hash is not None
            ):
                raise ValueError("released authorization used capacity")
        elif self.status is SpecialistAuthorizationStatus.FAILED:
            if self.actual_tool_calls != 1 or claim is None:
                raise ValueError("failed authorization requires one claimed attempt")
            if charge is None:
                if record_hash is not None or self.model_lease_id is not None:
                    raise ValueError(
                        "pre-charge failed authorization has later-stage state"
                    )
            elif record_hash is None:
                raise ValueError("post-charge failed authorization lost provenance")
        return self


class BudgetAuditEvent(Phase2Model):
    schema_version: Literal["phase2.budget-audit-event.v1"]
    event_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    snapshot_sequence: StrictInt = Field(ge=1)
    event_type: Identifier
    record_ids: tuple[Identifier, ...] = Field(max_length=MAX_ACTIVE_LEASES)
    occurred_at: datetime
    failure_code: Phase2FailureCode | None = None

    @field_validator("record_ids")
    @classmethod
    def require_unique_record_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique(values, label="budget audit record IDs")

    @field_validator("occurred_at")
    @classmethod
    def require_utc_occurrence(cls, value: datetime) -> datetime:
        return _require_utc(value, field_name="occurred_at")


class BudgetSnapshot(Phase2Model):
    schema_version: Literal["phase2.budget-snapshot.v1"]
    snapshot_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    max_model_calls: StrictInt = Field(ge=8, le=8)
    max_tool_calls: StrictInt = Field(ge=8, le=8)
    max_total_tokens: StrictInt = Field(
        ge=COMPARISON_MAX_TOTAL_TOKENS,
        le=COMPARISON_MAX_TOTAL_TOKENS,
    )
    charged_model_calls: StrictInt = Field(ge=0)
    charged_tool_calls: StrictInt = Field(ge=0)
    cumulative_tokens: StrictInt = Field(ge=0)
    reserved_model_calls: StrictInt = Field(ge=0)
    reserved_tool_calls: StrictInt = Field(ge=0)
    reserved_tokens: StrictInt = Field(ge=0)
    remaining_model_calls: StrictInt = Field(ge=0)
    remaining_tool_calls: StrictInt = Field(ge=0)
    remaining_tokens: StrictInt = Field(ge=0)
    monotonic_elapsed_seconds: StrictFloat = Field(ge=0)
    sequence: StrictInt = Field(ge=0)
    active_capacity_slot_ids: tuple[Identifier, ...] = Field(max_length=MAX_ACTIVE_LEASES)
    active_specialist_authorization_ids: tuple[Identifier, ...] = Field(
        max_length=MAX_ACTIVE_LEASES
    )
    active_lease_ids: tuple[Identifier, ...] = Field(max_length=MAX_ACTIVE_LEASES)

    @field_validator(
        "active_capacity_slot_ids",
        "active_specialist_authorization_ids",
        "active_lease_ids",
    )
    @classmethod
    def require_unique_active_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique(values, label="active budget IDs")

    @model_validator(mode="after")
    def require_exact_accounting(self) -> BudgetSnapshot:
        if (
            self.charged_model_calls
            + self.reserved_model_calls
            + self.remaining_model_calls
            != self.max_model_calls
        ):
            raise ValueError("model call accounting does not equal the hard maximum")
        if (
            self.charged_tool_calls
            + self.reserved_tool_calls
            + self.remaining_tool_calls
            != self.max_tool_calls
        ):
            raise ValueError("tool call accounting does not equal the hard maximum")
        if (
            self.cumulative_tokens + self.reserved_tokens + self.remaining_tokens
            != self.max_total_tokens
        ):
            raise ValueError("token accounting does not equal the hard maximum")
        all_active_ids = (
            self.active_capacity_slot_ids
            + self.active_specialist_authorization_ids
            + self.active_lease_ids
        )
        _require_unique(all_active_ids, label="active budget IDs")
        return self


class SpecialistToolOutcomeKind(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class SpecialistToolOutcomeReceipt(Phase2Model):
    schema_version: Literal["phase2.specialist-tool-outcome-receipt.v1"]
    outcome_kind: SpecialistToolOutcomeKind
    authorization_id: Identifier
    dispatch_claim_snapshot_sequence: StrictInt = Field(ge=0)
    post_outcome_authorization: SpecialistExecutionAuthorization
    outcome_snapshot: BudgetSnapshot
    tool_call_record_sha256: Sha256 | None
    failure_kind: Literal["EXECUTOR_FAILURE", "RECORD_MISMATCH"] | None
    failure_code: Literal[Phase2FailureCode.TOOL_DISPATCH_FAILED] | None

    @model_validator(mode="after")
    def require_exact_outcome(self) -> SpecialistToolOutcomeReceipt:
        authorization = self.post_outcome_authorization
        snapshot = self.outcome_snapshot
        if (
            self.authorization_id != authorization.authorization_id
            or self.dispatch_claim_snapshot_sequence
            != authorization.dispatch_claim_snapshot_sequence
        ):
            raise ValueError("outcome receipt key conflicts with authorization")
        if (
            authorization.run_id != snapshot.run_id
            or authorization.case_id != snapshot.case_id
            or authorization.variant is not snapshot.variant
        ):
            raise ValueError("outcome receipt contains mixed budget scope")
        if snapshot.sequence <= self.dispatch_claim_snapshot_sequence:
            raise ValueError("outcome snapshot must follow the dispatch claim")
        if authorization.capacity_slot_id in snapshot.active_capacity_slot_ids:
            raise ValueError("originating capacity slot became active again")

        active_authorizations = snapshot.active_specialist_authorization_ids
        if self.outcome_kind is SpecialistToolOutcomeKind.SUCCESS:
            if (
                authorization.status
                is not SpecialistAuthorizationStatus.TOOL_CHARGED
                or authorization.actual_tool_calls != 1
                or authorization.model_lease_id is not None
                or authorization.tool_charged_snapshot_sequence
                != snapshot.sequence
                or self.tool_call_record_sha256 is None
                or self.tool_call_record_sha256
                != authorization.tool_call_record_sha256
                or self.failure_kind is not None
                or self.failure_code is not None
                or active_authorizations.count(authorization.authorization_id) != 1
            ):
                raise ValueError("success receipt has an invalid exact outcome")
        elif (
            authorization.status is not SpecialistAuthorizationStatus.FAILED
            or authorization.actual_tool_calls != 1
            or authorization.model_lease_id is not None
            or authorization.tool_charged_snapshot_sequence is not None
            or authorization.tool_call_record_sha256 is not None
            or self.tool_call_record_sha256 is not None
            or self.failure_kind is None
            or self.failure_code is not Phase2FailureCode.TOOL_DISPATCH_FAILED
            or authorization.authorization_id in active_authorizations
        ):
            raise ValueError("failure receipt has an invalid exact outcome")
        return self


class SpecialistToolDispatchResult(Phase2Model):
    schema_version: Literal["phase2.specialist-tool-dispatch-result.v1"]
    tool_call_record: ToolCallRecord
    specialist_authorization: SpecialistExecutionAuthorization
    budget_snapshot: BudgetSnapshot
    outcome_receipt: SpecialistToolOutcomeReceipt

    @field_validator("tool_call_record", mode="before")
    @classmethod
    def revalidate_tool_record_input(cls, value: object) -> object:
        return _tool_call_record_input(value)

    @model_validator(mode="after")
    def require_exact_success_projection(self) -> SpecialistToolDispatchResult:
        record = self.tool_call_record
        authorization = self.specialist_authorization
        snapshot = self.budget_snapshot
        receipt = self.outcome_receipt
        ToolCallRecord.model_validate(_declared_model_input(record))
        expected_binding = SPECIALIST_TOOL_BINDINGS[
            SpecialistRole(record.agent_id)
        ] if record.agent_id in {item.value for item in SpecialistRole} else None
        if (
            authorization.status
            is not SpecialistAuthorizationStatus.TOOL_CHARGED
            or authorization.actual_tool_calls != 1
            or authorization.model_lease_id is not None
            or authorization.tool_charged_snapshot_sequence != snapshot.sequence
            or authorization.run_id != snapshot.run_id
            or authorization.case_id != snapshot.case_id
            or authorization.variant is not snapshot.variant
            or snapshot.active_specialist_authorization_ids.count(
                authorization.authorization_id
            )
            != 1
            or authorization.capacity_slot_id in snapshot.active_capacity_slot_ids
            or record.status != "OK"
            or not record.usable
            or not record.dispatched
            or record.evidence_quarantined
            or record.run_id != authorization.run_id
            or record.task_id != authorization.owner_node_id
            or record.tool_name is not authorization.tool_name
            or expected_binding
            != (
                authorization.owner_role,
                authorization.source,
                authorization.tool_name,
            )
            or any(
                item.run_id != authorization.run_id
                or item.source is not authorization.source
                for item in record.evidence
            )
        ):
            raise ValueError("dispatch result conflicts with its exact charge")
        record_hash = canonical_tool_call_record_sha256(record)
        if (
            authorization.tool_call_record_sha256 != record_hash
            or receipt.outcome_kind is not SpecialistToolOutcomeKind.SUCCESS
            or receipt.post_outcome_authorization != authorization
            or receipt.outcome_snapshot != snapshot
            or receipt.tool_call_record_sha256 != record_hash
        ):
            raise ValueError("dispatch result conflicts with its frozen receipt")
        return self


class CommanderRequest(Phase2Model):
    schema_version: Literal["phase2.commander-request.v1"]
    run_id: RunId
    incident: Incident
    source_capabilities: tuple[
        SourceCapability,
        SourceCapability,
        SourceCapability,
        SourceCapability,
    ]
    allowed_started_at: datetime
    allowed_ended_at: datetime
    budget_snapshot: BudgetSnapshot
    model_snapshot: Literal["gpt-5.4-mini-2026-03-17"]
    token_policy_core_sha256: Sha256

    @field_validator("incident", mode="before")
    @classmethod
    def revalidate_incident_input(cls, value: object) -> object:
        return _incident_input(value)

    @model_validator(mode="after")
    def require_commander_boundary(self) -> CommanderRequest:
        Incident.model_validate(_declared_model_input(self.incident))
        expected_capabilities = tuple(
            (
                source,
                _SOURCE_BINDINGS[source][0],
                _SOURCE_BINDINGS[source][1],
                _SOURCE_BINDINGS[source][2],
            )
            for source in (
                EvidenceSource.METRICS,
                EvidenceSource.LOGS,
                EvidenceSource.TRACES,
                EvidenceSource.CHANGES,
            )
        )
        actual_capabilities = tuple(
            (
                item.source,
                item.specialist_role,
                item.tool_name,
                item.action_type,
            )
            for item in self.source_capabilities
        )
        if actual_capabilities != expected_capabilities:
            raise ValueError("Commander requires the frozen source capability order")
        if self.allowed_ended_at < self.allowed_started_at:
            raise ValueError("Commander allowed window is reversed")
        if self.budget_snapshot.run_id != self.run_id:
            raise ValueError("Commander budget snapshot is outside the current run")
        return self


class BudgetLease(Phase2Model):
    schema_version: Literal["phase2.budget-lease.v1"]
    lease_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    snapshot_sequence: StrictInt = Field(ge=0)
    owner_role: BudgetOwnerRole
    owner_node_id: Identifier | None = None
    permitted_operation: ModelOperation
    allowed_actions: ModelAllowedActions
    source_record_id: Identifier
    reserved_model_calls: StrictInt = Field(ge=0)
    reserved_tool_calls: StrictInt = Field(ge=0)
    reserved_tokens: StrictInt = Field(ge=0)
    exact_input_tokens: StrictInt = Field(gt=0)
    minimum_completion_tokens: StrictInt = Field(gt=0)
    max_completion_tokens: StrictInt = Field(gt=0)
    issued_at: datetime
    expires_at: datetime
    status: BudgetLeaseStatus
    actual_model_calls: StrictInt = Field(ge=0)
    actual_tool_calls: StrictInt = Field(ge=0)
    actual_tokens: StrictInt = Field(ge=0)
    actual_input_tokens: StrictInt = Field(ge=0, default=0)
    actual_output_tokens: StrictInt = Field(ge=0, default=0)

    @model_validator(mode="after")
    def require_lease_consistency(self) -> BudgetLease:
        _require_utc(self.issued_at, field_name="issued_at")
        _require_utc(self.expires_at, field_name="expires_at")
        if self.expires_at < self.issued_at:
            raise ValueError("lease expires before it is issued")
        specialist_owners = {
            BudgetOwnerRole.METRICS_AGENT,
            BudgetOwnerRole.LOGS_AGENT,
            BudgetOwnerRole.TRACE_AGENT,
            BudgetOwnerRole.CHANGE_AGENT,
        }
        if self.permitted_operation is ModelOperation.SPECIALIST_MODEL:
            if self.owner_role not in specialist_owners or self.owner_node_id is None:
                raise ValueError("specialist lease requires its exact owner node")
        else:
            expected_owner = {
                ModelOperation.SINGLE_AGENT_MODEL: BudgetOwnerRole.INCIDENT_COMMANDER,
                ModelOperation.COMMANDER_MODEL: BudgetOwnerRole.INCIDENT_COMMANDER,
                ModelOperation.FIRST_JUDGE_MODEL: BudgetOwnerRole.RCA_JUDGE,
                ModelOperation.FINAL_JUDGE_MODEL: BudgetOwnerRole.RCA_JUDGE,
            }.get(self.permitted_operation)
            if expected_owner is not None and (
                self.owner_role is not expected_owner or self.owner_node_id is not None
            ):
                raise ValueError("model lease conflicts with its owner role")
        if (
            self.reserved_model_calls != 1
            or self.reserved_tool_calls != 0
            or self.reserved_tokens == 0
        ):
            raise ValueError("model-only lease requires one call and positive tokens")

        expected_actions: dict[ModelOperation, set[ModelAllowedActions]] = {
            ModelOperation.SINGLE_AGENT_MODEL: {
                ModelAllowedActions.PHASE1_ACTION_CATALOG
            },
            ModelOperation.COMMANDER_MODEL: {ModelAllowedActions.PLAN_ONLY},
            ModelOperation.SPECIALIST_MODEL: {ModelAllowedActions.FINDING_ONLY},
            ModelOperation.FIRST_JUDGE_MODEL: {
                ModelAllowedActions.FINAL_ONLY,
                ModelAllowedActions.FINAL_OR_REFINEMENT,
            },
            ModelOperation.FINAL_JUDGE_MODEL: {ModelAllowedActions.FINAL_ONLY},
        }
        if self.allowed_actions not in expected_actions[self.permitted_operation]:
            raise ValueError("lease operation and allowed actions are incompatible")
        if (
            self.max_completion_tokens < self.minimum_completion_tokens
            or self.reserved_tokens
            != self.exact_input_tokens + self.max_completion_tokens
        ):
            raise ValueError("exact model lease token accounting is inconsistent")

        actual = (
            self.actual_model_calls,
            self.actual_tool_calls,
            self.actual_tokens,
            self.actual_input_tokens,
            self.actual_output_tokens,
        )
        if self.status is BudgetLeaseStatus.RESERVED and any(actual):
            raise ValueError("reserved lease cannot contain an actual charge")
        if self.status in {BudgetLeaseStatus.RETURNED, BudgetLeaseStatus.EXPIRED} and any(actual):
            raise ValueError("returned or expired lease cannot contain an actual charge")
        if self.status is BudgetLeaseStatus.CHARGED:
            if not any(actual):
                raise ValueError("charged lease requires an actual charge")
            if (
                self.actual_model_calls > self.reserved_model_calls
                or self.actual_tool_calls > self.reserved_tool_calls
                or self.actual_tokens > self.reserved_tokens
            ):
                raise ValueError("actual charge exceeds the reserved lease")
            if (self.actual_tokens > 0) != (self.actual_model_calls > 0):
                raise ValueError("token charge requires a charged model call")
            if (
                self.actual_model_calls != 1
                or self.actual_tool_calls != 0
                or self.actual_tokens == 0
            ):
                raise ValueError("model-only lease requires one complete model charge")
            if (
                self.actual_input_tokens != self.exact_input_tokens
                or self.actual_input_tokens + self.actual_output_tokens
                != self.actual_tokens
            ):
                raise ValueError("provider usage is not attributable to the exact lease")
        return self


class ConditionalRefinementBundle(Phase2Model):
    schema_version: Literal["phase2.conditional-refinement-bundle.v1"]
    bundle_id: Identifier
    run_id: RunId
    case_id: Identifier
    variant: Literal[Phase2Variant.DYNAMIC_MULTI_AGENT]
    first_judge_capacity_slot_id: Identifier
    specialist_capacity_slot_ids: tuple[Identifier, Identifier]
    final_judge_capacity_slot_id: Identifier
    creating_snapshot_sequence: StrictInt = Field(ge=0)
    status: ConditionalRefinementBundleStatus

    @model_validator(mode="after")
    def require_distinct_frozen_slices(self) -> ConditionalRefinementBundle:
        slot_ids = (
            self.first_judge_capacity_slot_id,
            *self.specialist_capacity_slot_ids,
            self.final_judge_capacity_slot_id,
        )
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("conditional bundle capacity slot IDs must be distinct")
        return self


class SpecialistTask(Phase2Model):
    schema_version: Literal["phase2.specialist-task.v1"]
    run_id: RunId
    incident_id: Identifier
    plan_id: Identifier
    node_id: Identifier
    source: EvidenceSource
    specialist_role: SpecialistRole
    tool_name: ReadOnlyToolName
    query: ToolAction
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE_LENGTH)
    dependency_finding_ids: tuple[Identifier, ...] = Field(max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES)
    dependency_evidence_refs: tuple[EvidenceRef, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    tool_authorization_id: Identifier
    model_capacity_slot_id: Identifier

    @field_validator("objective", mode="before")
    @classmethod
    def require_read_only_objective(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="objective",
            maximum=MAX_OBJECTIVE_LENGTH,
        )

    @field_validator("query", mode="before")
    @classmethod
    def revalidate_query_input(cls, value: object) -> object:
        return _query_input(value)

    @field_validator("dependency_finding_ids")
    @classmethod
    def require_unique_finding_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _require_unique(values, label="dependency_finding_ids")

    @model_validator(mode="after")
    def require_task_consistency(self) -> SpecialistTask:
        _require_source_binding(
            source=self.source,
            specialist_role=self.specialist_role,
            tool_name=self.tool_name,
            query=self.query,
        )
        _require_current_run_refs(
            self.dependency_evidence_refs,
            run_id=self.run_id,
            label="dependency_evidence_refs",
        )
        return self


class ResolvedEvidenceView(Phase2Model):
    schema_version: Literal["phase2.resolved-evidence-view.v1"]
    run_id: RunId
    evidence: tuple[Evidence, ...] = Field(max_length=MAX_EVIDENCE_REFS)

    @field_validator("evidence", mode="before")
    @classmethod
    def revalidate_evidence_input(cls, value: object) -> object:
        return _evidence_tuple_input(value)

    @model_validator(mode="after")
    def require_current_run_evidence(self) -> ResolvedEvidenceView:
        for item in self.evidence:
            Evidence.model_validate(_declared_model_input(item))
        refs = tuple(item.evidence_ref for item in self.evidence)
        _require_current_run_refs(refs, run_id=self.run_id, label="resolved evidence")
        if any(item.run_id != self.run_id for item in self.evidence):
            raise ValueError("resolved evidence must belong to the current run")
        return self


class SpecialistModelRequest(Phase2Model):
    schema_version: Literal["phase2.specialist-model-request.v1"]
    task: SpecialistTask
    tool_call_record: ToolCallRecord
    new_evidence: tuple[Evidence, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    dependency_finding_ids: tuple[Identifier, ...] = Field(max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES)
    resolved_dependency_evidence_view: ResolvedEvidenceView
    budget_snapshot: BudgetSnapshot

    @field_validator("tool_call_record", mode="before")
    @classmethod
    def revalidate_tool_record_input(cls, value: object) -> object:
        return _tool_call_record_input(value)

    @field_validator("new_evidence", mode="before")
    @classmethod
    def revalidate_new_evidence_input(cls, value: object) -> object:
        return _evidence_tuple_input(value)

    @model_validator(mode="after")
    def require_request_bindings(self) -> SpecialistModelRequest:
        record = self.tool_call_record
        ToolCallRecord.model_validate(_declared_model_input(record))
        for item in self.new_evidence:
            Evidence.model_validate(_declared_model_input(item))
        if (
            record.status != "OK"
            or not record.usable
            or not record.dispatched
            or record.evidence_quarantined
        ):
            raise ValueError("tool_call_record must be a successful usable call")
        if (
            record.run_id != self.task.run_id
            or record.incident_id != self.task.incident_id
            or record.task_id != self.task.node_id
            or record.agent_id != self.task.specialist_role.value
            or record.tool_name is not self.task.tool_name
            or record.action != self.task.query
        ):
            raise ValueError("tool_call_record conflicts with specialist task")
        if self.new_evidence != record.evidence:
            raise ValueError("new_evidence conflicts with tool_call_record")
        if any(
            item.run_id != self.task.run_id or item.source is not self.task.source
            for item in self.new_evidence
        ):
            raise ValueError("new_evidence is outside the task scope")
        if self.dependency_finding_ids != self.task.dependency_finding_ids:
            raise ValueError("dependency_finding_ids conflict with specialist task")
        view = self.resolved_dependency_evidence_view
        if view.run_id != self.task.run_id:
            raise ValueError("resolved evidence view is outside the current run")
        view_refs = tuple(item.evidence_ref for item in view.evidence)
        if view_refs != self.task.dependency_evidence_refs:
            raise ValueError("resolved evidence view conflicts with dependency refs")
        if self.budget_snapshot.run_id != self.task.run_id:
            raise ValueError("budget snapshot is outside the current run")
        if (
            self.task.tool_authorization_id
            not in self.budget_snapshot.active_specialist_authorization_ids
        ):
            raise ValueError(
                "budget snapshot does not contain the active specialist authorization"
            )
        if (
            self.task.model_capacity_slot_id
            in self.budget_snapshot.active_capacity_slot_ids
        ):
            raise ValueError(
                "budget snapshot reactivates the originating capacity slot"
            )
        return self


class FindingHypothesis(Phase2Model):
    schema_version: Literal["phase2.finding-hypothesis.v1"]
    hypothesis_id: Identifier
    root_service: str | None = Field(default=None, max_length=MAX_SERVICE_LENGTH)
    fault_mechanism: FaultMechanism | None = None
    claim: str = Field(min_length=1, max_length=MAX_HYPOTHESIS_LENGTH)

    @field_validator("root_service", mode="before")
    @classmethod
    def trim_optional_service(cls, value: object | None) -> str | None:
        if value is None:
            return None
        return _bounded_read_only_text(
            value,
            field_name="root_service",
            maximum=MAX_SERVICE_LENGTH,
        )

    @field_validator("claim", mode="before")
    @classmethod
    def require_read_only_claim(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="claim",
            maximum=MAX_HYPOTHESIS_LENGTH,
        )


class HypothesisEvidenceGroup(Phase2Model):
    schema_version: Literal["phase2.hypothesis-evidence-group.v1"]
    hypothesis_id: Identifier
    evidence_refs: tuple[EvidenceRef, ...] = Field(max_length=MAX_EVIDENCE_REFS)

    @field_validator("evidence_refs")
    @classmethod
    def require_unique_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique(values, label="evidence_refs")


class MissingEvidenceItem(Phase2Model):
    schema_version: Literal["phase2.missing-evidence-item.v1"]
    question: str = Field(min_length=1, max_length=MAX_TEXT_ENTRY_LENGTH)
    desired_source: EvidenceSource

    @field_validator("question", mode="before")
    @classmethod
    def require_read_only_question(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="question",
            maximum=MAX_TEXT_ENTRY_LENGTH,
        )


class SpecialistFinding(Phase2Model):
    schema_version: Literal["phase2.specialist-finding.v1"]
    finding_id: Identifier
    run_id: RunId
    incident_id: Identifier
    plan_id: Identifier
    node_id: Identifier
    source: EvidenceSource
    specialist_role: SpecialistRole
    evidence_refs: tuple[EvidenceRef, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    hypotheses: tuple[FindingHypothesis, ...] = Field(
        min_length=1,
        max_length=MAX_FINDING_HYPOTHESES,
    )
    supporting_evidence_refs: tuple[HypothesisEvidenceGroup, ...] = Field(max_length=MAX_FINDING_HYPOTHESES)
    contradicting_evidence_refs: tuple[HypothesisEvidenceGroup, ...] = Field(max_length=MAX_FINDING_HYPOTHESES)
    missing_evidence: tuple[MissingEvidenceItem, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    confidence: StrictFloat = Field(ge=0, le=1)
    finding_rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)

    @field_validator("finding_rationale", mode="before")
    @classmethod
    def require_read_only_rationale(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="finding_rationale",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @model_validator(mode="after")
    def require_finding_consistency(self) -> SpecialistFinding:
        expected_role = _SOURCE_BINDINGS[self.source][0]
        if self.specialist_role is not expected_role:
            raise ValueError("specialist role conflicts with finding source")
        _require_current_run_refs(
            self.evidence_refs,
            run_id=self.run_id,
            label="evidence_refs",
        )
        hypothesis_ids = tuple(item.hypothesis_id for item in self.hypotheses)
        _require_unique(hypothesis_ids, label="hypotheses")
        available_refs = set(self.evidence_refs)
        for label, groups in (
            ("supporting", self.supporting_evidence_refs),
            ("contradicting", self.contradicting_evidence_refs),
        ):
            grouped_ids = tuple(group.hypothesis_id for group in groups)
            _require_unique(grouped_ids, label=f"{label} hypothesis groups")
            for group in groups:
                if group.hypothesis_id not in hypothesis_ids:
                    raise ValueError(f"{label} group names an unknown hypothesis")
                if not set(group.evidence_refs).issubset(available_refs):
                    raise ValueError(f"{label} group contains an unexamined ref")
                _require_current_run_refs(
                    group.evidence_refs,
                    run_id=self.run_id,
                    label=f"{label} evidence refs",
                )
        supporting = {
            (group.hypothesis_id, reference)
            for group in self.supporting_evidence_refs
            for reference in group.evidence_refs
        }
        contradicting = {
            (group.hypothesis_id, reference)
            for group in self.contradicting_evidence_refs
            for reference in group.evidence_refs
        }
        if supporting & contradicting:
            raise ValueError("one ref cannot both support and contradict a hypothesis")
        return self


class JudgeRequest(Phase2Model):
    schema_version: Literal["phase2.judge-request.v1"]
    judge_request_id: Identifier
    run_id: RunId
    incident: Incident
    admitted_graph: AdmittedInvestigationGraph
    finding_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES)
    findings: tuple[SpecialistFinding, ...] = Field(
        min_length=1,
        max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES,
    )
    available_evidence_refs: tuple[EvidenceRef, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    resolved_evidence_view: ResolvedEvidenceView
    budget_snapshot: BudgetSnapshot
    refinement_round: StrictInt = Field(ge=0, le=1)
    allowed_actions: ModelAllowedActions
    conditional_refinement_bundle_id: Identifier | None = None

    @field_validator("incident", mode="before")
    @classmethod
    def revalidate_incident_input(cls, value: object) -> object:
        return _incident_input(value)

    @model_validator(mode="after")
    def require_judge_capability_consistency(self) -> JudgeRequest:
        Incident.model_validate(_declared_model_input(self.incident))
        projected_finding_ids = tuple(item.finding_id for item in self.findings)
        _require_unique(projected_finding_ids, label="finding_ids")
        if self.finding_ids != projected_finding_ids:
            raise ValueError("finding_ids projection conflicts with finding bodies")
        if (
            self.admitted_graph.run_id != self.run_id
            or self.admitted_graph.incident_id != self.incident.incident_id
            or self.budget_snapshot.run_id != self.run_id
            or self.resolved_evidence_view.run_id != self.run_id
        ):
            raise ValueError("Judge request contains cross-run or cross-incident data")

        graph_nodes = {item.node_id: item for item in self.admitted_graph.all_nodes}
        projected_refs: list[str] = []
        seen_refs: set[str] = set()
        for finding in self.findings:
            node = graph_nodes.get(finding.node_id)
            if (
                finding.run_id != self.run_id
                or finding.incident_id != self.incident.incident_id
                or finding.plan_id != self.admitted_graph.initial_plan.plan_id
                or node is None
                or finding.source is not node.source
                or finding.specialist_role is not node.specialist_role
            ):
                raise ValueError("finding body conflicts with the admitted graph")
            for reference in finding.evidence_refs:
                if reference not in seen_refs:
                    seen_refs.add(reference)
                    projected_refs.append(reference)

        if self.available_evidence_refs != tuple(projected_refs):
            raise ValueError(
                "evidence ref projection conflicts with canonical finding bodies"
            )
        resolved_refs = tuple(
            item.evidence_ref for item in self.resolved_evidence_view.evidence
        )
        if resolved_refs != self.available_evidence_refs:
            raise ValueError("unresolved evidence remains in the Judge request")

        if self.allowed_actions not in {
            ModelAllowedActions.FINAL_ONLY,
            ModelAllowedActions.FINAL_OR_REFINEMENT,
        }:
            raise ValueError("Judge allowed action is outside its closed capability")
        if self.allowed_actions is ModelAllowedActions.FINAL_OR_REFINEMENT:
            if self.conditional_refinement_bundle_id is None:
                raise ValueError("conditional refinement bundle is required")
            if self.refinement_round != 0:
                raise ValueError("second refinement cannot be exposed")
        elif self.conditional_refinement_bundle_id is not None:
            raise ValueError("FINAL_ONLY cannot expose a conditional refinement bundle")
        if self.refinement_round == 0 and self.admitted_graph.refinement_fragment is not None:
            raise ValueError("round zero Judge request cannot contain refinement nodes")
        if self.refinement_round == 1 and self.admitted_graph.refinement_fragment is None:
            raise ValueError("round one Judge request requires admitted refinement nodes")
        return self


class AdditionalInvestigationRequest(Phase2Model):
    schema_version: Literal["phase2.additional-investigation-request.v1"]
    action_type: Literal["ADDITIONAL_INVESTIGATION"]
    run_id: RunId
    incident_id: Identifier
    parent_plan_id: Identifier
    request_id: Identifier
    nodes: tuple[InvestigationNode, ...] = Field(
        min_length=1,
        max_length=MAX_REFINEMENT_NODES,
    )
    target_hypothesis_ids: tuple[Identifier, ...] = Field(
        min_length=1,
        max_length=MAX_FINDING_HYPOTHESES,
    )
    reason: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    conditional_refinement_bundle_id: Identifier
    fallback_rca_result: RCAResult

    @field_validator("fallback_rca_result", mode="before")
    @classmethod
    def revalidate_fallback_input(cls, value: object) -> object:
        return _rca_result_input(value)

    @field_validator("reason", mode="before")
    @classmethod
    def require_read_only_reason(cls, value: object) -> str:
        return _bounded_read_only_text(
            value,
            field_name="reason",
            maximum=MAX_RATIONALE_LENGTH,
        )

    @model_validator(mode="after")
    def require_bounded_fail_closed_request(self) -> AdditionalInvestigationRequest:
        node_ids = tuple(item.node_id for item in self.nodes)
        _require_unique(node_ids, label="refinement nodes")
        _require_unique(self.target_hypothesis_ids, label="target_hypothesis_ids")
        result = self.fallback_rca_result
        if type(result) is not RCAResult:
            raise ValueError("fallback must be the exact Phase 1 RCAResult type")
        RCAResult.model_validate(_declared_model_input(result))
        if result.decision not in {
            RCADecision.NEED_MORE_EVIDENCE,
            RCADecision.ABSTAIN,
        }:
            raise ValueError("fallback decision must fail closed")
        if result.root_service is not None or result.fault_mechanism is not None:
            raise ValueError("fallback cannot assert root service or fault mechanism")
        _require_current_run_refs(
            result.supporting_evidence + result.contradicting_evidence,
            run_id=self.run_id,
            label="fallback evidence refs",
        )
        return self


class JudgeFinalResult(Phase2Model):
    schema_version: Literal["phase2.judge-final-result.v1"]
    action_type: Literal["FINAL_RCA"]
    run_id: RunId
    incident_id: Identifier
    rca_result: RCAResult
    finding_ids_considered: tuple[Identifier, ...] = Field(
        min_length=1,
        max_length=MAX_PLAN_NODES + MAX_REFINEMENT_NODES,
    )
    refinement_used: StrictBool
    judge_request_id: Identifier

    @field_validator("rca_result", mode="before")
    @classmethod
    def revalidate_rca_input(cls, value: object) -> object:
        return _rca_result_input(value)

    @model_validator(mode="after")
    def require_exact_current_run_result(self) -> JudgeFinalResult:
        if type(self.rca_result) is not RCAResult:
            raise ValueError("rca_result must be the exact Phase 1 RCAResult type")
        RCAResult.model_validate(_declared_model_input(self.rca_result))
        _require_unique(self.finding_ids_considered, label="finding_ids_considered")
        _require_current_run_refs(
            self.rca_result.supporting_evidence
            + self.rca_result.contradicting_evidence,
            run_id=self.run_id,
            label="RCA evidence refs",
        )
        return self


FirstJudgeAction = Annotated[
    JudgeFinalResult | AdditionalInvestigationRequest,
    Field(discriminator="action_type"),
]
