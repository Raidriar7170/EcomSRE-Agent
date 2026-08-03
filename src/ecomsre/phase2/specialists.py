"""Source-bound one-tool Specialist runtime for Fixed and Dynamic Phase 2."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Literal, cast

from pydantic import ValidationError, model_validator

from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.comparison_adapter import (
    ComparisonAdapter,
    ComparisonAdapterError,
    ModelCallResult,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    BudgetOwnerRole,
    BudgetSnapshot,
    InvestigationNode,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Model,
    Phase2Variant,
    SpecialistAuthorizationStatus,
    SpecialistExecutionAuthorization,
    SpecialistFinding,
    SpecialistModelRequest,
    SpecialistRole,
    SpecialistTask,
    SpecialistToolDispatchResult,
)
from ecomsre.phase2.evidence_views import (
    EvidenceResolutionError,
    EvidenceResolutionErrorCode,
    FindingStore,
    build_specialist_model_request,
)
from ecomsre.phase2.tool_isolation import (
    SpecialistToolRegistry,
    ToolIsolationError,
    ToolIsolationErrorCode,
)


_SPECIALIST_KEY = (
    ModelOperation.SPECIALIST_MODEL,
    ModelAllowedActions.FINDING_ONLY,
)


class SpecialistErrorCode(str, Enum):
    INVALID_CONTEXT = "INVALID_CONTEXT"
    DEPENDENCY_NOT_READY = "DEPENDENCY_NOT_READY"
    ALREADY_EXECUTED = "ALREADY_EXECUTED"
    FINDING_MISMATCH = "FINDING_MISMATCH"


class SpecialistError(ValueError):
    """Typed Specialist failure preserving the originating stable code."""

    def __init__(
        self,
        code: SpecialistErrorCode
        | Phase2FailureCode
        | EvidenceResolutionErrorCode
        | ToolIsolationErrorCode,
        detail: str,
    ) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class SpecialistExecutionContext(Phase2Model):
    """Runtime-owned graph/slot binding without a caller-supplied snapshot."""

    schema_version: Literal["phase2.specialist-execution-context.v1"]
    admitted_graph: AdmittedInvestigationGraph
    node_id: str
    specialist_capacity_slot_id: str
    specialist_authorization_id: str | None = None
    dependency_finding_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_graph_node(self) -> SpecialistExecutionContext:
        matches = tuple(
            node for node in self.admitted_graph.all_nodes if node.node_id == self.node_id
        )
        if len(matches) != 1:
            raise ValueError("execution node is not uniquely present in admitted graph")
        return self


@dataclass(frozen=True, slots=True)
class SpecialistOutcome:
    """One fully charged and stored Specialist execution."""

    task: SpecialistTask
    dispatch_result: SpecialistToolDispatchResult
    request: SpecialistModelRequest
    call: ModelCallResult
    finding: SpecialistFinding
    authorization: SpecialistExecutionAuthorization
    snapshot: BudgetSnapshot


class SpecialistRuntime:
    """Execute admitted nodes with only their exact source-bound registry."""

    def __init__(
        self,
        *,
        ledger: BudgetLedger,
        adapter: ComparisonAdapter,
        evidence_store: EvidenceStore,
        finding_store: FindingStore,
        registries: Mapping[SpecialistRole, SpecialistToolRegistry],
        dispatch_observer: Callable[
            [SpecialistTask, SpecialistToolDispatchResult], None
        ]
        | None = None,
    ) -> None:
        if not isinstance(ledger, BudgetLedger):
            raise TypeError("ledger must be BudgetLedger")
        if not isinstance(adapter, ComparisonAdapter):
            raise TypeError("adapter must be ComparisonAdapter")
        if adapter._ledger is not ledger:  # noqa: SLF001 - same-ledger authority
            raise SpecialistError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "Specialist ledger differs from adapter ledger",
            )
        snapshot = ledger.snapshot()
        if snapshot.variant not in {
            Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
            Phase2Variant.DYNAMIC_MULTI_AGENT,
        }:
            raise SpecialistError(
                SpecialistErrorCode.INVALID_CONTEXT,
                "Specialists require a Fixed or Dynamic ledger",
            )
        if not isinstance(evidence_store, EvidenceStore) or (
            evidence_store.run_id != snapshot.run_id
        ):
            raise SpecialistError(
                SpecialistErrorCode.INVALID_CONTEXT,
                "Evidence Store is outside the current run",
            )
        if not isinstance(finding_store, FindingStore) or (
            finding_store.run_id != snapshot.run_id
        ):
            raise SpecialistError(
                SpecialistErrorCode.INVALID_CONTEXT,
                "Finding Store is outside the current run",
            )
        copied_registries = dict(registries)
        for role, registry in copied_registries.items():
            if (
                type(role) is not SpecialistRole
                or not isinstance(registry, SpecialistToolRegistry)
                or registry.specialist_role is not role
                or registry.ledger is not ledger
            ):
                raise SpecialistError(
                    SpecialistErrorCode.INVALID_CONTEXT,
                    "registry mapping is not exact and ledger-bound",
                )
        if dispatch_observer is not None and not callable(dispatch_observer):
            raise SpecialistError(
                SpecialistErrorCode.INVALID_CONTEXT,
                "dispatch observer must be callable when provided",
            )
        self._ledger = ledger
        self._adapter = adapter
        self._evidence_store = evidence_store
        self._finding_store = finding_store
        self._registries = copied_registries
        self._dispatch_observer = dispatch_observer
        self._finding_id_by_node: dict[str, str] = {}
        self._started_node_ids: set[str] = set()
        self._lock = RLock()

    def execute_node(
        self,
        context: SpecialistExecutionContext,
    ) -> SpecialistOutcome:
        """Run one admitted node through tool, model, store, and live lineage."""

        with self._lock:
            try:
                context = SpecialistExecutionContext.model_validate(context)
            except (TypeError, ValidationError, ValueError) as error:
                raise SpecialistError(
                    SpecialistErrorCode.INVALID_CONTEXT,
                    "Specialist context violates its closed contract",
                ) from error
            graph = context.admitted_graph
            live = self._ledger.snapshot()
            if graph.run_id != live.run_id:
                raise SpecialistError(
                    SpecialistErrorCode.INVALID_CONTEXT,
                    "admitted graph is outside the current run",
                )
            if context.node_id in self._started_node_ids:
                raise SpecialistError(
                    SpecialistErrorCode.ALREADY_EXECUTED,
                    "Specialist node cannot execute twice",
                )
            node = next(
                item for item in graph.all_nodes if item.node_id == context.node_id
            )
            dependency_findings = self._resolve_dependencies(
                node,
                context.dependency_finding_ids,
            )
            dependency_refs = tuple(
                dict.fromkeys(
                    reference
                    for finding in dependency_findings
                    for reference in finding.evidence_refs
                )
            )
            registry = self._registries.get(node.specialist_role)
            if registry is None:
                raise SpecialistError(
                    SpecialistErrorCode.INVALID_CONTEXT,
                    "no exact source-bound registry exists for this node",
                )
            self._started_node_ids.add(context.node_id)
            try:
                if context.specialist_authorization_id is None:
                    authorization, _ = (
                        self._ledger.materialize_specialist_authorization(
                            expected_snapshot_sequence=live.sequence,
                            slot_id=context.specialist_capacity_slot_id,
                            owner_role=BudgetOwnerRole(node.specialist_role.value),
                            owner_node_id=node.node_id,
                            source=node.source,
                            tool_name=node.tool_name,
                        )
                    )
                else:
                    authorization = self._ledger.specialist_authorization(
                        context.specialist_authorization_id
                    )
                    if (
                        authorization.status
                        is not SpecialistAuthorizationStatus.TOOL_AUTHORIZED
                        or authorization.capacity_slot_id
                        != context.specialist_capacity_slot_id
                        or authorization.owner_node_id != node.node_id
                        or authorization.owner_role
                        is not BudgetOwnerRole(node.specialist_role.value)
                        or authorization.source is not node.source
                        or authorization.tool_name is not node.tool_name
                    ):
                        raise BudgetLedgerError(
                            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                            "pre-authorized refinement lineage conflicts with node",
                        )
            except BudgetLedgerError as error:
                raise SpecialistError(error.code, "Specialist authorization failed") from error
            task = SpecialistTask(
                schema_version="phase2.specialist-task.v1",
                run_id=graph.run_id,
                incident_id=graph.incident_id,
                plan_id=graph.initial_plan.plan_id,
                node_id=node.node_id,
                source=node.source,
                specialist_role=node.specialist_role,
                tool_name=node.tool_name,
                query=node.query,
                objective=node.objective,
                dependency_finding_ids=tuple(
                    finding.finding_id for finding in dependency_findings
                ),
                dependency_evidence_refs=dependency_refs,
                tool_authorization_id=authorization.authorization_id,
                model_capacity_slot_id=authorization.capacity_slot_id,
            )
            try:
                dispatch_result = registry.dispatch(task)
            except ToolIsolationError as error:
                code = error.phase2_failure_code or error.code
                raise SpecialistError(code, "source-bound tool dispatch failed") from error
            if self._dispatch_observer is not None:
                self._dispatch_observer(task, dispatch_result)
            try:
                request = build_specialist_model_request(
                    task=task,
                    dispatch_result=dispatch_result,
                    budget_snapshot=self._ledger.snapshot(),
                    evidence_store=self._evidence_store,
                )
            except EvidenceResolutionError as error:
                self._fail_authorization(authorization.authorization_id)
                raise SpecialistError(
                    error.code,
                    "Specialist request could not resolve current store records",
                ) from error
            golden = self._adapter.token_authority.golden(*_SPECIALIST_KEY)
            invocation = ModelInvocation(
                schema_version="phase2.model-invocation.v1",
                invocation_id=f"specialist-{authorization.authorization_id}",
                run_id=graph.run_id,
                variant=live.variant,
                case_id=live.case_id,
                operation=_SPECIALIST_KEY[0],
                allowed_actions=_SPECIALIST_KEY[1],
                request=request,
                provider_parameters=self._adapter.provider_parameters,
                token_policy_core_sha256=self._adapter.token_authority.core_sha256,
                response_schema_sha256=golden.response_schema_sha256,
                expected_snapshot_sequence=self._ledger.snapshot().sequence,
                source_record_id=authorization.authorization_id,
            )
            try:
                call = self._adapter.invoke(invocation)
            except ComparisonAdapterError as error:
                self._fail_authorization(authorization.authorization_id)
                raise SpecialistError(error.code, "Specialist model call failed") from error
            if type(call.response) is not SpecialistFinding:
                self._fail_and_terminalize(authorization.authorization_id)
                raise SpecialistError(
                    SpecialistErrorCode.FINDING_MISMATCH,
                    "Specialist did not return one exact finding",
                )
            finding = cast(SpecialistFinding, call.response)
            available_refs = {
                *task.dependency_evidence_refs,
                *dispatch_result.tool_call_record.evidence_refs,
            }
            if (
                finding.run_id != task.run_id
                or finding.incident_id != task.incident_id
                or finding.plan_id != task.plan_id
                or finding.node_id != task.node_id
                or finding.source is not task.source
                or finding.specialist_role is not task.specialist_role
                or not set(finding.evidence_refs).issubset(available_refs)
            ):
                self._fail_and_terminalize(authorization.authorization_id)
                raise SpecialistError(
                    SpecialistErrorCode.FINDING_MISMATCH,
                    "finding identity or Evidence scope differs from its task",
                )
            try:
                stored = self._finding_store.add(finding)
                completed, completed_snapshot = (
                    self._ledger.complete_specialist_authorization(
                        expected_snapshot_sequence=call.snapshot.sequence,
                        authorization_id=authorization.authorization_id,
                    )
                )
            except EvidenceResolutionError as error:
                self._fail_and_terminalize(authorization.authorization_id)
                raise SpecialistError(error.code, "Finding Store rejected result") from error
            except BudgetLedgerError as error:
                raise SpecialistError(error.code, "authorization completion failed") from error
            self._finding_id_by_node[node.node_id] = stored.finding_id
            return SpecialistOutcome(
                task=task,
                dispatch_result=dispatch_result,
                request=request,
                call=call,
                finding=stored,
                authorization=completed,
                snapshot=completed_snapshot,
            )

    def _resolve_dependencies(
        self,
        node: InvestigationNode,
        supplied_finding_ids: tuple[str, ...],
    ) -> tuple[SpecialistFinding, ...]:
        if supplied_finding_ids:
            findings = tuple(
                self._finding_store.resolve(finding_id)
                for finding_id in supplied_finding_ids
            )
            if tuple(finding.node_id for finding in findings) != node.depends_on:
                raise SpecialistError(
                    SpecialistErrorCode.DEPENDENCY_NOT_READY,
                    "runtime dependency findings do not match node dependencies",
                )
            return findings
        missing = tuple(
            dependency
            for dependency in node.depends_on
            if dependency not in self._finding_id_by_node
        )
        if missing:
            raise SpecialistError(
                SpecialistErrorCode.DEPENDENCY_NOT_READY,
                "dependency finding is not complete",
            )
        return tuple(
            self._finding_store.resolve(self._finding_id_by_node[dependency])
            for dependency in node.depends_on
        )

    def _fail_authorization(self, authorization_id: str) -> None:
        if self._ledger.terminal_failure_code is not None:
            return
        authorization = self._ledger.specialist_authorization(authorization_id)
        if authorization.status in {
            SpecialistAuthorizationStatus.TOOL_CHARGED,
            SpecialistAuthorizationStatus.MODEL_LEASED,
        }:
            self._ledger.fail_specialist_authorization(
                expected_snapshot_sequence=self._ledger.snapshot().sequence,
                authorization_id=authorization_id,
            )

    def _fail_and_terminalize(self, authorization_id: str) -> None:
        self._fail_authorization(authorization_id)
        if self._ledger.terminal_failure_code is None:
            self._ledger.record_terminal_failure(
                expected_snapshot_sequence=self._ledger.snapshot().sequence,
                code=Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
            )
