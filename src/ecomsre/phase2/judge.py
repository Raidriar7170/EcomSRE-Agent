"""Final-only and one-refinement Judge runtime for Phase 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Literal, cast

from pydantic import Field, StrictBool, ValidationError, field_validator, model_validator

from ecomsre.phase1.contracts import Incident, RCAResult
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase1.validator import EvidenceValidationError, validate_rca_result
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.comparison_adapter import (
    ComparisonAdapter,
    ComparisonAdapterError,
    ModelCallResult,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    AdditionalInvestigationRequest,
    AdmittedInvestigationGraph,
    BudgetOwnerRole,
    BudgetSnapshot,
    CapacitySlotRequest,
    ConditionalRefinementBundle,
    Identifier,
    JudgeFinalResult,
    JudgeRequest,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Model,
    Phase2Variant,
    RunId,
)
from ecomsre.phase2.dag import (
    DagValidationError,
    DagValidationErrorCode,
    admit_refinement_request,
)
from ecomsre.phase2.evidence_views import (
    EvidenceResolutionError,
    EvidenceResolutionErrorCode,
    FindingStore,
    build_judge_request,
)
from ecomsre.phase2.specialists import SpecialistExecutionContext


class JudgeErrorCode(str, Enum):
    INVALID_CONTEXT = "INVALID_CONTEXT"
    ALREADY_JUDGED = "ALREADY_JUDGED"
    NO_PENDING_REFINEMENT = "NO_PENDING_REFINEMENT"
    INVALID_RESULT = "INVALID_RESULT"
    INVALID_REFINEMENT = "INVALID_REFINEMENT"


class JudgeError(ValueError):
    def __init__(
        self,
        code: JudgeErrorCode
        | Phase2FailureCode
        | DagValidationErrorCode
        | EvidenceResolutionErrorCode,
        detail: str,
    ) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class JudgeContext(Phase2Model):
    schema_version: Literal["phase2.judge-context.v1"]
    run_id: RunId
    incident: Incident
    admitted_graph: AdmittedInvestigationGraph
    finding_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=5)
    judge_capacity_slot_id: Identifier
    allow_refinement: StrictBool

    @field_validator("incident", mode="before")
    @classmethod
    def revalidate_incident(cls, value: object) -> Incident:
        return Incident.model_validate(value)

    @model_validator(mode="after")
    def require_scope(self) -> JudgeContext:
        if (
            self.run_id != self.admitted_graph.run_id
            or self.incident.incident_id != self.admitted_graph.incident_id
            or self.admitted_graph.refinement_fragment is not None
        ):
            raise ValueError("first Judge context scope is inconsistent")
        return self


@dataclass(frozen=True, slots=True)
class JudgeOutcome:
    request: JudgeRequest
    call: ModelCallResult
    action: JudgeFinalResult | AdditionalInvestigationRequest
    result: RCAResult | None
    admitted_graph: AdmittedInvestigationGraph
    refinement_contexts: tuple[SpecialistExecutionContext, ...]
    bundle_id: str | None
    fallback_used: bool
    snapshot: BudgetSnapshot


@dataclass(frozen=True, slots=True)
class _PendingRefinement:
    incident: Incident
    admitted_graph: AdmittedInvestigationGraph
    bundle: ConditionalRefinementBundle
    refinement_contexts: tuple[SpecialistExecutionContext, ...]


class JudgeRuntime:
    """Make one first/final decision and at most one post-refinement final call."""

    def __init__(
        self,
        *,
        ledger: BudgetLedger,
        adapter: ComparisonAdapter,
        evidence_store: EvidenceStore,
        finding_store: FindingStore,
        utc_clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(ledger, BudgetLedger) or not isinstance(
            adapter, ComparisonAdapter
        ):
            raise TypeError("Judge requires exact ledger and adapter")
        if adapter._ledger is not ledger:  # noqa: SLF001 - same-ledger authority
            raise JudgeError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "Judge ledger differs from adapter ledger",
            )
        snapshot = ledger.snapshot()
        if (
            not isinstance(evidence_store, EvidenceStore)
            or evidence_store.run_id != snapshot.run_id
            or not isinstance(finding_store, FindingStore)
            or finding_store.run_id != snapshot.run_id
            or not callable(utc_clock)
        ):
            raise JudgeError(
                JudgeErrorCode.INVALID_CONTEXT,
                "Judge stores or clock are outside the current run",
            )
        self._ledger = ledger
        self._adapter = adapter
        self._evidence_store = evidence_store
        self._finding_store = finding_store
        self._first_called = False
        self._pending: _PendingRefinement | None = None
        self._done = False
        self._lock = RLock()

    def judge(self, context: JudgeContext) -> JudgeOutcome:
        """Run Fixed final Judge or Dynamic first Judge exactly once."""

        with self._lock:
            if self._first_called:
                raise JudgeError(
                    JudgeErrorCode.ALREADY_JUDGED,
                    "first Judge cannot be called twice",
                )
            try:
                context = JudgeContext.model_validate(context)
            except (TypeError, ValidationError, ValueError) as error:
                raise JudgeError(
                    JudgeErrorCode.INVALID_CONTEXT,
                    "Judge context violates its closed contract",
                ) from error
            live = self._ledger.snapshot()
            if context.run_id != live.run_id:
                raise JudgeError(
                    JudgeErrorCode.INVALID_CONTEXT,
                    "Judge context is outside the current run",
                )
            self._first_called = True
            bundle: ConditionalRefinementBundle | None = None
            operation: ModelOperation
            actions: ModelAllowedActions
            source_slot_id = context.judge_capacity_slot_id
            if live.variant is Phase2Variant.FIXED_SPECIALIST_WORKFLOW:
                if context.allow_refinement:
                    raise JudgeError(
                        JudgeErrorCode.INVALID_CONTEXT,
                        "Fixed workflow cannot expose refinement",
                    )
                operation = ModelOperation.FINAL_JUDGE_MODEL
                actions = ModelAllowedActions.FINAL_ONLY
            elif live.variant is Phase2Variant.DYNAMIC_MULTI_AGENT:
                operation = ModelOperation.FIRST_JUDGE_MODEL
                if context.allow_refinement:
                    bundle, _ = self._create_bundle(context.judge_capacity_slot_id)
                    source_slot_id = bundle.first_judge_capacity_slot_id
                    actions = ModelAllowedActions.FINAL_OR_REFINEMENT
                else:
                    actions = ModelAllowedActions.FINAL_ONLY
            else:
                raise JudgeError(
                    JudgeErrorCode.INVALID_CONTEXT,
                    "Single-Agent does not use the Phase 2 Judge",
                )
            request = self._build_request(
                context=context,
                operation=operation,
                actions=actions,
                source_slot_id=source_slot_id,
                bundle_id=None if bundle is None else bundle.bundle_id,
                refinement_round=0,
            )
            call = self._invoke(
                request=request,
                operation=operation,
                actions=actions,
                source_slot_id=source_slot_id,
            )
            action = call.response
            if type(action) is JudgeFinalResult:
                final = cast(JudgeFinalResult, action)
                result = self._validate_final(final, request, refinement_used=False)
                snapshot = call.snapshot
                if bundle is not None:
                    _, snapshot = self._ledger.release_unused_refinement_members(
                        expected_snapshot_sequence=snapshot.sequence,
                        bundle_id=bundle.bundle_id,
                        used_specialist_slot_ids=(),
                        retain_final_judge=False,
                    )
                self._done = True
                return JudgeOutcome(
                    request=request,
                    call=call,
                    action=final,
                    result=result,
                    admitted_graph=context.admitted_graph,
                    refinement_contexts=(),
                    bundle_id=None if bundle is None else bundle.bundle_id,
                    fallback_used=False,
                    snapshot=snapshot,
                )
            if type(action) is not AdditionalInvestigationRequest or bundle is None:
                self._terminalize()
                raise JudgeError(
                    JudgeErrorCode.INVALID_RESULT,
                    "Judge response does not match admitted capability",
                )
            refinement = cast(AdditionalInvestigationRequest, action)
            return self._admit_or_fallback(
                context=context,
                request=request,
                call=call,
                action=refinement,
                bundle=bundle,
            )

    def finalize(self, finding_ids: tuple[str, ...]) -> JudgeOutcome:
        """After completed refinement Specialists, make one FINAL_ONLY call."""

        with self._lock:
            pending = self._pending
            if pending is None or self._done:
                raise JudgeError(
                    JudgeErrorCode.NO_PENDING_REFINEMENT,
                    "no valid one-round refinement awaits finalization",
                )
            request = build_judge_request(
                judge_request_id=(
                    f"judge-final-{pending.bundle.final_judge_capacity_slot_id}"
                ),
                run_id=pending.admitted_graph.run_id,
                incident=pending.incident,
                admitted_graph=pending.admitted_graph,
                finding_ids=finding_ids,
                finding_store=self._finding_store,
                evidence_store=self._evidence_store,
                budget_snapshot=self._ledger.snapshot(),
                refinement_round=1,
                allowed_actions=ModelAllowedActions.FINAL_ONLY,
                conditional_refinement_bundle_id=None,
            )
            call = self._invoke(
                request=request,
                operation=ModelOperation.FINAL_JUDGE_MODEL,
                actions=ModelAllowedActions.FINAL_ONLY,
                source_slot_id=pending.bundle.final_judge_capacity_slot_id,
            )
            if type(call.response) is not JudgeFinalResult:
                self._terminalize()
                raise JudgeError(
                    JudgeErrorCode.INVALID_RESULT,
                    "final Judge returned a non-final action",
                )
            final = cast(JudgeFinalResult, call.response)
            result = self._validate_final(final, request, refinement_used=True)
            try:
                _, snapshot = self._ledger.complete_conditional_refinement_bundle(
                    expected_snapshot_sequence=call.snapshot.sequence,
                    bundle_id=pending.bundle.bundle_id,
                )
            except BudgetLedgerError as error:
                raise JudgeError(error.code, "conditional bundle completion failed") from error
            self._done = True
            self._pending = None
            return JudgeOutcome(
                request=request,
                call=call,
                action=final,
                result=result,
                admitted_graph=pending.admitted_graph,
                refinement_contexts=(),
                bundle_id=pending.bundle.bundle_id,
                fallback_used=False,
                snapshot=snapshot,
            )

    def _create_bundle(
        self,
        replaced_slot_id: str,
    ) -> tuple[ConditionalRefinementBundle, BudgetSnapshot]:
        authority = self._adapter.token_authority
        first = authority.golden(
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelAllowedActions.FINAL_OR_REFINEMENT,
        )
        specialist = authority.golden(
            ModelOperation.SPECIALIST_MODEL,
            ModelAllowedActions.FINDING_ONLY,
        )
        final = authority.golden(
            ModelOperation.FINAL_JUDGE_MODEL,
            ModelAllowedActions.FINAL_ONLY,
        )
        expires_at = self._ledger.capacity_slot(replaced_slot_id).expires_at
        try:
            return self._ledger.replace_first_judge_with_conditional_bundle(
                expected_snapshot_sequence=self._ledger.snapshot().sequence,
                replaced_first_judge_slot_id=replaced_slot_id,
                first_judge=CapacitySlotRequest(
                    permitted_operation=ModelOperation.FIRST_JUDGE_MODEL,
                    allowed_actions=ModelAllowedActions.FINAL_OR_REFINEMENT,
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    minimum_token_floor=first.minimum_call_floor_tokens,
                    expires_at=expires_at,
                ),
                specialists=tuple(
                    CapacitySlotRequest(
                        permitted_operation=ModelOperation.SPECIALIST_MODEL,
                        allowed_actions=ModelAllowedActions.FINDING_ONLY,
                        reserved_model_calls=1,
                        reserved_tool_calls=1,
                        minimum_token_floor=specialist.minimum_call_floor_tokens,
                        expires_at=expires_at,
                    )
                    for _ in range(2)
                ),
                final_judge=CapacitySlotRequest(
                    permitted_operation=ModelOperation.FINAL_JUDGE_MODEL,
                    allowed_actions=ModelAllowedActions.FINAL_ONLY,
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    minimum_token_floor=final.minimum_call_floor_tokens,
                    expires_at=expires_at,
                ),
            )
        except BudgetLedgerError as error:
            raise JudgeError(error.code, "conditional bundle admission failed") from error

    def _build_request(
        self,
        *,
        context: JudgeContext,
        operation: ModelOperation,
        actions: ModelAllowedActions,
        source_slot_id: str,
        bundle_id: str | None,
        refinement_round: int,
    ) -> JudgeRequest:
        del operation
        try:
            return build_judge_request(
                judge_request_id=f"judge-{source_slot_id}",
                run_id=context.run_id,
                incident=context.incident,
                admitted_graph=context.admitted_graph,
                finding_ids=context.finding_ids,
                finding_store=self._finding_store,
                evidence_store=self._evidence_store,
                budget_snapshot=self._ledger.snapshot(),
                refinement_round=refinement_round,
                allowed_actions=actions,
                conditional_refinement_bundle_id=bundle_id,
            )
        except EvidenceResolutionError as error:
            raise JudgeError(error.code, "Judge request reconstruction failed") from error

    def _invoke(
        self,
        *,
        request: JudgeRequest,
        operation: ModelOperation,
        actions: ModelAllowedActions,
        source_slot_id: str,
    ) -> ModelCallResult:
        golden = self._adapter.token_authority.golden(operation, actions)
        invocation = ModelInvocation(
            schema_version="phase2.model-invocation.v1",
            invocation_id=request.judge_request_id,
            run_id=request.run_id,
            variant=self._ledger.snapshot().variant,
            case_id=self._ledger.snapshot().case_id,
            operation=operation,
            allowed_actions=actions,
            request=request,
            provider_parameters=self._adapter.provider_parameters,
            token_policy_core_sha256=self._adapter.token_authority.core_sha256,
            response_schema_sha256=golden.response_schema_sha256,
            expected_snapshot_sequence=self._ledger.snapshot().sequence,
            source_record_id=source_slot_id,
        )
        try:
            return self._adapter.invoke(invocation)
        except ComparisonAdapterError as error:
            raise JudgeError(error.code, "Judge model call failed") from error

    def _admit_or_fallback(
        self,
        *,
        context: JudgeContext,
        request: JudgeRequest,
        call: ModelCallResult,
        action: AdditionalInvestigationRequest,
        bundle: ConditionalRefinementBundle,
    ) -> JudgeOutcome:
        try:
            if (
                action.conditional_refinement_bundle_id != bundle.bundle_id
                or action.run_id != context.run_id
                or action.incident_id != context.incident.incident_id
                or action.parent_plan_id
                != context.admitted_graph.initial_plan.plan_id
            ):
                raise JudgeError(
                    JudgeErrorCode.INVALID_REFINEMENT,
                    "refinement scope or opaque bundle ID is invalid",
                )
            available_hypotheses = {
                hypothesis.hypothesis_id
                for finding in request.findings
                for hypothesis in finding.hypotheses
            }
            if not set(action.target_hypothesis_ids).issubset(available_hypotheses):
                raise JudgeError(
                    JudgeErrorCode.INVALID_REFINEMENT,
                    "refinement targets an unknown hypothesis",
                )
            refined_graph = admit_refinement_request(
                action,
                context.admitted_graph,
                allowed_started_at=context.incident.started_at,
                allowed_ended_at=context.incident.ended_at,
            )
        except (JudgeError, DagValidationError):
            return self._fallback(context, request, call, action, bundle)
        contexts: list[SpecialistExecutionContext] = []
        used_slots: list[str] = []
        finding_id_by_node = {
            finding.node_id: finding.finding_id for finding in request.findings
        }
        try:
            for node, slot_id in zip(
                action.nodes,
                bundle.specialist_capacity_slot_ids,
                strict=False,
            ):
                authorization, _ = self._ledger.materialize_specialist_authorization(
                    expected_snapshot_sequence=self._ledger.snapshot().sequence,
                    slot_id=slot_id,
                    owner_role=BudgetOwnerRole(node.specialist_role.value),
                    owner_node_id=node.node_id,
                    source=node.source,
                    tool_name=node.tool_name,
                )
                used_slots.append(slot_id)
                contexts.append(
                    SpecialistExecutionContext(
                        schema_version="phase2.specialist-execution-context.v1",
                        admitted_graph=refined_graph,
                        node_id=node.node_id,
                        specialist_capacity_slot_id=slot_id,
                        specialist_authorization_id=authorization.authorization_id,
                        dependency_finding_ids=tuple(
                            finding_id_by_node[dependency]
                            for dependency in node.depends_on
                        ),
                    )
                )
            updated_bundle, snapshot = self._ledger.release_unused_refinement_members(
                expected_snapshot_sequence=self._ledger.snapshot().sequence,
                bundle_id=bundle.bundle_id,
                used_specialist_slot_ids=tuple(used_slots),
                retain_final_judge=True,
            )
        except BudgetLedgerError as error:
            raise JudgeError(error.code, "refinement capacity binding failed") from error
        pending = _PendingRefinement(
            incident=context.incident,
            admitted_graph=refined_graph,
            bundle=updated_bundle,
            refinement_contexts=tuple(contexts),
        )
        self._pending = pending
        return JudgeOutcome(
            request=request,
            call=call,
            action=action,
            result=None,
            admitted_graph=refined_graph,
            refinement_contexts=pending.refinement_contexts,
            bundle_id=bundle.bundle_id,
            fallback_used=False,
            snapshot=snapshot,
        )

    def _fallback(
        self,
        context: JudgeContext,
        request: JudgeRequest,
        call: ModelCallResult,
        action: AdditionalInvestigationRequest,
        bundle: ConditionalRefinementBundle,
    ) -> JudgeOutcome:
        try:
            result = validate_rca_result(
                action.fallback_rca_result,
                self._evidence_store,
                context.incident,
            )
        except EvidenceValidationError as error:
            self._terminalize()
            raise JudgeError(
                JudgeErrorCode.INVALID_RESULT,
                "same-response fallback failed the Phase 1 validator",
            ) from error
        try:
            _, snapshot = self._ledger.release_unused_refinement_members(
                expected_snapshot_sequence=call.snapshot.sequence,
                bundle_id=bundle.bundle_id,
                used_specialist_slot_ids=(),
                retain_final_judge=False,
            )
        except BudgetLedgerError as error:
            raise JudgeError(error.code, "fallback bundle release failed") from error
        self._done = True
        return JudgeOutcome(
            request=request,
            call=call,
            action=action,
            result=result,
            admitted_graph=context.admitted_graph,
            refinement_contexts=(),
            bundle_id=bundle.bundle_id,
            fallback_used=True,
            snapshot=snapshot,
        )

    def _validate_final(
        self,
        action: JudgeFinalResult,
        request: JudgeRequest,
        *,
        refinement_used: bool,
    ) -> RCAResult:
        if (
            action.run_id != request.run_id
            or action.incident_id != request.incident.incident_id
            or action.finding_ids_considered != request.finding_ids
            or action.refinement_used is not refinement_used
            or action.judge_request_id != request.judge_request_id
        ):
            self._terminalize()
            raise JudgeError(
                JudgeErrorCode.INVALID_RESULT,
                "final result identity differs from the exact Judge request",
            )
        try:
            return validate_rca_result(
                action.rca_result,
                self._evidence_store,
                request.incident,
            )
        except EvidenceValidationError as error:
            self._terminalize()
            raise JudgeError(
                JudgeErrorCode.INVALID_RESULT,
                "final RCA failed the unchanged Phase 1 validator",
            ) from error

    def _terminalize(self) -> None:
        if self._ledger.terminal_failure_code is None:
            self._ledger.record_terminal_failure(
                expected_snapshot_sequence=self._ledger.snapshot().sequence,
                code=Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
            )
