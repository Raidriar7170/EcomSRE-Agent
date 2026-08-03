"""Shared, fail-closed accounting boundary for every Phase 2 model call."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Literal, Protocol, cast

from pydantic import Field, JsonValue, StrictBool, StrictFloat, StrictInt, ValidationError, model_validator

from ecomsre.model.gateway import ModelGateway
from ecomsre.phase1.contracts import (
    EvidenceSource,
    ModelRequest,
    ModelResponse,
    Phase1Model,
    ReadOnlyToolName,
)
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.contracts import (
    BudgetLease,
    BudgetSnapshot,
    COMPARISON_MAX_TOTAL_TOKENS,
    CapacitySlotRequest,
    CommanderRequest,
    Identifier,
    JudgeRequest,
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2FailureCode,
    Phase2Model,
    Phase2Variant,
    RunId,
    Sha256,
    SpecialistModelRequest,
)
from ecomsre.phase2.token_policy import (
    MODEL_SNAPSHOT,
    TokenAuthority,
    TokenPolicyError,
    build_model_input_envelope,
    canonical_json_bytes,
    validate_model_response,
)


ADAPTER_VERSION: Literal["phase2.comparison-adapter.v1"] = (
    "phase2.comparison-adapter.v1"
)
_SINGLE_KEY = (
    ModelOperation.SINGLE_AGENT_MODEL,
    ModelAllowedActions.PHASE1_ACTION_CATALOG,
)
_VARIANT_OPERATIONS: dict[Phase2Variant, frozenset[ModelOperation]] = {
    Phase2Variant.SINGLE_AGENT: frozenset({ModelOperation.SINGLE_AGENT_MODEL}),
    Phase2Variant.FIXED_SPECIALIST_WORKFLOW: frozenset(
        {ModelOperation.SPECIALIST_MODEL, ModelOperation.FINAL_JUDGE_MODEL}
    ),
    Phase2Variant.DYNAMIC_MULTI_AGENT: frozenset(
        {
            ModelOperation.COMMANDER_MODEL,
            ModelOperation.SPECIALIST_MODEL,
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelOperation.FINAL_JUDGE_MODEL,
        }
    ),
}


class ComparisonAdapterError(ValueError):
    """Stable fail-closed adapter error in the shared Phase 2 code space."""

    def __init__(self, code: Phase2FailureCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class BudgetCaps(Phase2Model):
    """The one equal outer budget used independently by every variant/case."""

    model_calls: StrictInt = 8
    tool_calls: StrictInt = 8
    total_tokens: StrictInt = COMPARISON_MAX_TOTAL_TOKENS

    @model_validator(mode="after")
    def require_exact_caps(self) -> BudgetCaps:
        if (self.model_calls, self.tool_calls, self.total_tokens) != (
            8,
            8,
            COMPARISON_MAX_TOTAL_TOKENS,
        ):
            raise ValueError("comparison outer caps must be exactly 8 / 8 / 32000")
        return self


class ProviderParameters(Phase2Model):
    """Auditable provider parameters; exact frozen values are checked by adapter."""

    model_snapshot: str = Field(min_length=1, max_length=128)
    provider_identity: Identifier
    temperature: StrictFloat
    n: StrictInt = Field(ge=1)
    parallel_tool_calls: StrictBool


ModelRequestBody = ModelRequest | CommanderRequest | SpecialistModelRequest | JudgeRequest


class ModelInvocation(Phase2Model):
    """One closed request entering the shared comparison boundary."""

    schema_version: Literal["phase2.model-invocation.v1"]
    invocation_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    operation: ModelOperation
    allowed_actions: ModelAllowedActions
    request: ModelRequestBody
    provider_parameters: ProviderParameters
    token_policy_core_sha256: Sha256
    response_schema_sha256: Sha256
    expected_snapshot_sequence: StrictInt = Field(ge=0)
    source_record_id: Identifier | None = None

    @model_validator(mode="after")
    def require_exact_request_and_source_shape(self) -> ModelInvocation:
        expected_type: dict[ModelOperation, type[Phase1Model]] = {
            ModelOperation.SINGLE_AGENT_MODEL: ModelRequest,
            ModelOperation.COMMANDER_MODEL: CommanderRequest,
            ModelOperation.SPECIALIST_MODEL: SpecialistModelRequest,
            ModelOperation.FIRST_JUDGE_MODEL: JudgeRequest,
            ModelOperation.FINAL_JUDGE_MODEL: JudgeRequest,
        }
        if type(self.request) is not expected_type[self.operation]:
            raise ValueError("operation conflicts with the exact request contract")
        request_run_id = (
            self.request.run_id
            if not isinstance(self.request, SpecialistModelRequest)
            else self.request.task.run_id
        )
        if request_run_id != self.run_id:
            raise ValueError("request belongs to a different run")
        if isinstance(self.request, JudgeRequest):
            if self.request.allowed_actions is not self.allowed_actions:
                raise ValueError("Judge request conflicts with allowed actions")
        if self.operation is ModelOperation.SINGLE_AGENT_MODEL:
            if self.source_record_id is not None:
                raise ValueError("Single-Agent capacity is admitted just in time")
        elif self.source_record_id is None:
            raise ValueError("non-Single invocation requires a held source record")
        return self


class ModelCompletion(Phase2Model):
    """Typed backend result before lease charging."""

    schema_version: Literal["phase2.model-completion.v1"]
    provider_identity: Identifier
    response: dict[str, JsonValue]
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    phase1_response: ModelResponse | None = None


class TypedModelBackend(Protocol):
    """Only provider-facing protocol reachable from Phase 2 workflows."""

    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion: ...


class ModelCallAuditRecord(Phase2Model):
    """Immutable attribution for one backend attempt through the adapter."""

    schema_version: Literal["phase2.model-call-audit.v1"]
    adapter_version: Literal["phase2.comparison-adapter.v1"]
    invocation_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    operation: ModelOperation
    allowed_actions: ModelAllowedActions
    outer_caps: BudgetCaps
    model_snapshot: str
    expected_provider_identity: Identifier
    observed_provider_identity: Identifier | None = None
    token_policy_core_sha256: Sha256
    response_schema_sha256: Sha256
    envelope_sha256: Sha256
    response_sha256: Sha256 | None = None
    source_record_id: Identifier
    lease_id: Identifier
    exact_input_tokens: StrictInt = Field(gt=0)
    minimum_completion_tokens: StrictInt = Field(gt=0)
    max_completion_tokens: StrictInt = Field(gt=0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    lease_snapshot_sequence: StrictInt = Field(ge=0)
    final_snapshot_sequence: StrictInt = Field(ge=0)
    status: Literal["CHARGED", "FAILED"]
    failure_code: Phase2FailureCode | None = None

    @model_validator(mode="after")
    def require_status_consistency(self) -> ModelCallAuditRecord:
        usage = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.status == "CHARGED":
            if any(item is None for item in usage) or self.failure_code is not None:
                raise ValueError("charged audit requires complete usage and no failure")
            if self.response_sha256 is None:
                raise ValueError("charged audit requires a response digest")
            input_tokens = cast(int, self.input_tokens)
            output_tokens = cast(int, self.output_tokens)
            total_tokens = cast(int, self.total_tokens)
            if input_tokens + output_tokens != total_tokens:
                raise ValueError("charged audit usage is inconsistent")
        elif self.failure_code is None:
            raise ValueError("failed audit requires a shared failure code")
        return self


class ToolCallAuditRecord(Phase2Model):
    """Common tool-side audit projection populated by later workflow slices."""

    schema_version: Literal["phase2.tool-call-audit.v1"]
    call_id: Identifier
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    source: EvidenceSource
    tool_name: ReadOnlyToolName
    charged_tool_calls: Literal[1]
    final_snapshot_sequence: StrictInt = Field(ge=0)


class VariantCaseAudit(Phase2Model):
    """One variant/case audit container with the same frozen outer caps."""

    schema_version: Literal["phase2.variant-case-audit.v1"]
    run_id: RunId
    variant: Phase2Variant
    case_id: Identifier
    outer_caps: BudgetCaps
    model_calls: tuple[ModelCallAuditRecord, ...]
    tool_calls: tuple[ToolCallAuditRecord, ...]
    terminal_failure_code: Phase2FailureCode | None = None


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """Validated response together with the exact charged accounting state."""

    response: Phase1Model
    completion: ModelCompletion
    lease: BudgetLease
    snapshot: BudgetSnapshot
    audit_record: ModelCallAuditRecord


class ComparisonAdapter:
    """Validate, admit, invoke once, and atomically charge one model call."""

    def __init__(
        self,
        *,
        ledger: BudgetLedger,
        token_authority: TokenAuthority,
        backend: TypedModelBackend,
        expected_provider_identity: str,
        utc_clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(ledger, BudgetLedger):
            raise TypeError("ledger must be BudgetLedger")
        if not isinstance(token_authority, TokenAuthority):
            raise TypeError("token_authority must be TokenAuthority")
        try:
            frozen_parameters = ProviderParameters(
                model_snapshot=token_authority.core.model_snapshot,
                provider_identity=expected_provider_identity,
                temperature=0.0,
                n=1,
                parallel_tool_calls=False,
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise TypeError("expected provider identity is invalid") from error
        if not callable(getattr(backend, "complete", None)):
            raise TypeError("backend must implement complete")
        if not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        self._ledger = ledger
        self._token_authority = token_authority
        self._backend = backend
        self._provider_parameters = frozen_parameters
        self._utc_clock = utc_clock
        self._outer_caps = BudgetCaps()
        self._audit_records: tuple[ModelCallAuditRecord, ...] = ()
        self._seen_invocation_ids: set[str] = set()
        self._lock = RLock()

    @property
    def outer_caps(self) -> BudgetCaps:
        return self._outer_caps

    @property
    def provider_parameters(self) -> ProviderParameters:
        return self._provider_parameters

    @property
    def token_authority(self) -> TokenAuthority:
        return self._token_authority

    @property
    def audit_records(self) -> tuple[ModelCallAuditRecord, ...]:
        with self._lock:
            return self._audit_records

    def snapshot(self) -> BudgetSnapshot:
        return self._ledger.snapshot()

    def charge_single_agent_tool_attempt(
        self,
        *,
        attempt_id: str,
        tool_name: ReadOnlyToolName,
    ) -> BudgetSnapshot:
        """Authorize and charge one Phase 1 backend entry before dispatch."""

        with self._lock:
            try:
                return self._ledger.charge_single_agent_tool_attempt(
                    expected_snapshot_sequence=self._ledger.snapshot().sequence,
                    attempt_id=attempt_id,
                    tool_name=tool_name,
                )
            except BudgetLedgerError as error:
                raise ComparisonAdapterError(
                    error.code,
                    "Single-Agent tool attempt admission failed",
                ) from error

    def invoke(self, invocation: ModelInvocation) -> ModelCallResult:
        """Run one admitted backend call; no provider attempt can escape charging."""

        with self._lock:
            try:
                invocation = ModelInvocation.model_validate(invocation)
            except (TypeError, ValidationError, ValueError) as error:
                raise ComparisonAdapterError(
                    Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                    "invocation does not match the closed adapter contract",
                ) from error
            self._require_preflight_identity(invocation)

            try:
                envelope = build_model_input_envelope(
                    self._token_authority.core,
                    invocation.operation,
                    invocation.allowed_actions,
                    invocation.request,
                )
                golden = self._token_authority.golden(
                    invocation.operation,
                    invocation.allowed_actions,
                )
                response_schema_sha256 = hashlib.sha256(
                    canonical_json_bytes(envelope.response_schema)
                ).hexdigest()
                if (
                    invocation.token_policy_core_sha256
                    != self._token_authority.core_sha256
                    or golden.token_policy_core_sha256
                    != self._token_authority.core_sha256
                    or invocation.response_schema_sha256
                    != golden.response_schema_sha256
                    or response_schema_sha256 != golden.response_schema_sha256
                ):
                    raise ComparisonAdapterError(
                        Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
                        "invocation is not bound to the reproduced response schema",
                    )
                exact_input_tokens = self._token_authority.exact_input_tokens(envelope)
                minimum_completion_tokens = golden.minimum_completion_tokens
                source_record_id, source_floor, admitted_snapshot = self._admit_source(
                    invocation,
                    exact_input_tokens=exact_input_tokens,
                    minimum_completion_tokens=minimum_completion_tokens,
                    golden_minimum_call_floor_tokens=(
                        golden.minimum_call_floor_tokens
                    ),
                )
                available_for_call = admitted_snapshot.remaining_tokens + source_floor
                max_completion_tokens = available_for_call - exact_input_tokens
                if max_completion_tokens < minimum_completion_tokens:
                    raise ComparisonAdapterError(
                        Phase2FailureCode.TOKEN_INPUT_TOO_LARGE,
                        "canonical input leaves less than the frozen completion floor",
                    )
                lease, lease_snapshot = self._ledger.expand_exact_model_lease(
                    expected_snapshot_sequence=admitted_snapshot.sequence,
                    source_record_id=source_record_id,
                    exact_input_tokens=exact_input_tokens,
                    minimum_completion_tokens=minimum_completion_tokens,
                    max_completion_tokens=max_completion_tokens,
                )
            except ComparisonAdapterError:
                raise
            except (BudgetLedgerError, TokenPolicyError) as error:
                raise ComparisonAdapterError(error.code, "model preflight failed") from error
            except (TypeError, ValidationError, ValueError) as error:
                raise ComparisonAdapterError(
                    Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH,
                    "model envelope violates its exact schema",
                ) from error

            self._seen_invocation_ids.add(invocation.invocation_id)
            envelope_sha256 = hashlib.sha256(
                canonical_json_bytes(envelope.model_dump(mode="json"))
            ).hexdigest()
            try:
                raw_completion = self._backend.complete(
                    invocation,
                    envelope=envelope,
                    exact_input_tokens=exact_input_tokens,
                    max_completion_tokens=max_completion_tokens,
                )
            except Exception as error:
                code = Phase2FailureCode.PROVIDER_USAGE_MISSING
                terminal_snapshot = self._latch_terminal(code, lease_snapshot.sequence)
                self._append_failed_audit(
                    invocation=invocation,
                    envelope_sha256=envelope_sha256,
                    source_record_id=source_record_id,
                    lease=lease,
                    minimum_completion_tokens=minimum_completion_tokens,
                    completion=None,
                    code=code,
                    final_snapshot=terminal_snapshot,
                )
                raise ComparisonAdapterError(code, "backend failed without chargeable usage") from error

            completion: ModelCompletion | None = None
            try:
                completion = ModelCompletion.model_validate(raw_completion)
                if (
                    completion.provider_identity
                    != invocation.provider_parameters.provider_identity
                    or completion.provider_identity
                    != self._provider_parameters.provider_identity
                ):
                    raise ComparisonAdapterError(
                        Phase2FailureCode.PROVIDER_PARAMETER_MISMATCH,
                        "provider identity differs from the frozen mapping",
                    )
                response = validate_model_response(
                    invocation.operation,
                    invocation.allowed_actions,
                    completion.response,
                )
                self._require_phase1_completion_consistency(
                    invocation,
                    completion,
                    response,
                )
                charged_lease, charged_snapshot = self._ledger.charge_exact_model_lease(
                    expected_snapshot_sequence=lease_snapshot.sequence,
                    lease_id=lease.lease_id,
                    owner_role=lease.owner_role,
                    owner_node_id=lease.owner_node_id,
                    source_record_id=source_record_id,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    total_tokens=completion.total_tokens,
                )
            except ComparisonAdapterError as error:
                terminal_snapshot = self._latch_terminal(error.code, self._ledger.snapshot().sequence)
                self._append_failed_audit(
                    invocation=invocation,
                    envelope_sha256=envelope_sha256,
                    source_record_id=source_record_id,
                    lease=lease,
                    minimum_completion_tokens=minimum_completion_tokens,
                    completion=completion,
                    code=error.code,
                    final_snapshot=terminal_snapshot,
                )
                raise
            except TokenPolicyError as error:
                terminal_snapshot = self._latch_terminal(error.code, self._ledger.snapshot().sequence)
                self._append_failed_audit(
                    invocation=invocation,
                    envelope_sha256=envelope_sha256,
                    source_record_id=source_record_id,
                    lease=lease,
                    minimum_completion_tokens=minimum_completion_tokens,
                    completion=completion,
                    code=error.code,
                    final_snapshot=terminal_snapshot,
                )
                raise ComparisonAdapterError(error.code, "provider response failed validation") from error
            except BudgetLedgerError as error:
                terminal_snapshot = self._latch_terminal(error.code, self._ledger.snapshot().sequence)
                self._append_failed_audit(
                    invocation=invocation,
                    envelope_sha256=envelope_sha256,
                    source_record_id=source_record_id,
                    lease=lease,
                    minimum_completion_tokens=minimum_completion_tokens,
                    completion=completion,
                    code=error.code,
                    final_snapshot=terminal_snapshot,
                )
                raise ComparisonAdapterError(error.code, "provider usage could not be charged") from error
            except (TypeError, ValidationError, ValueError) as error:
                code = Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT
                terminal_snapshot = self._latch_terminal(code, self._ledger.snapshot().sequence)
                self._append_failed_audit(
                    invocation=invocation,
                    envelope_sha256=envelope_sha256,
                    source_record_id=source_record_id,
                    lease=lease,
                    minimum_completion_tokens=minimum_completion_tokens,
                    completion=completion,
                    code=code,
                    final_snapshot=terminal_snapshot,
                )
                raise ComparisonAdapterError(code, "backend returned an invalid completion") from error

            response_sha256 = hashlib.sha256(
                canonical_json_bytes(completion.response)
            ).hexdigest()
            audit = ModelCallAuditRecord(
                schema_version="phase2.model-call-audit.v1",
                adapter_version=ADAPTER_VERSION,
                invocation_id=invocation.invocation_id,
                run_id=invocation.run_id,
                variant=invocation.variant,
                case_id=invocation.case_id,
                operation=invocation.operation,
                allowed_actions=invocation.allowed_actions,
                outer_caps=self._outer_caps,
                model_snapshot=invocation.provider_parameters.model_snapshot,
                expected_provider_identity=self._provider_parameters.provider_identity,
                observed_provider_identity=completion.provider_identity,
                token_policy_core_sha256=self._token_authority.core_sha256,
                response_schema_sha256=invocation.response_schema_sha256,
                envelope_sha256=envelope_sha256,
                response_sha256=response_sha256,
                source_record_id=source_record_id,
                lease_id=charged_lease.lease_id,
                exact_input_tokens=exact_input_tokens,
                minimum_completion_tokens=minimum_completion_tokens,
                max_completion_tokens=max_completion_tokens,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                total_tokens=completion.total_tokens,
                lease_snapshot_sequence=lease_snapshot.sequence,
                final_snapshot_sequence=charged_snapshot.sequence,
                status="CHARGED",
                failure_code=None,
            )
            self._audit_records = (*self._audit_records, audit)
            return ModelCallResult(
                response=response,
                completion=completion,
                lease=charged_lease,
                snapshot=charged_snapshot,
                audit_record=audit,
            )

    def _require_preflight_identity(self, invocation: ModelInvocation) -> None:
        snapshot = self._ledger.snapshot()
        terminal_code = self._ledger.terminal_failure_code
        if terminal_code is not None:
            raise ComparisonAdapterError(
                terminal_code,
                "variant/case is terminal and forbids another model call",
            )
        if invocation.invocation_id in self._seen_invocation_ids:
            raise ComparisonAdapterError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "invocation ID has already crossed the adapter",
            )
        if (
            snapshot.run_id != invocation.run_id
            or snapshot.variant is not invocation.variant
            or snapshot.case_id != invocation.case_id
        ):
            raise ComparisonAdapterError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "invocation scope differs from the run-local budget ledger",
            )
        if invocation.expected_snapshot_sequence != snapshot.sequence:
            raise ComparisonAdapterError(
                Phase2FailureCode.BUDGET_CAS_CONFLICT,
                "invocation does not name the exact live budget sequence",
            )
        if invocation.operation not in _VARIANT_OPERATIONS[invocation.variant]:
            raise ComparisonAdapterError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "variant cannot use this model operation",
            )
        if invocation.provider_parameters != self._provider_parameters:
            raise ComparisonAdapterError(
                Phase2FailureCode.PROVIDER_PARAMETER_MISMATCH,
                "provider parameters differ from the frozen comparison mapping",
            )
        if (
            invocation.operation is ModelOperation.SINGLE_AGENT_MODEL
            and cast(ModelRequest, invocation.request).model_name != MODEL_SNAPSHOT
        ):
            raise ComparisonAdapterError(
                Phase2FailureCode.PROVIDER_PARAMETER_MISMATCH,
                "Phase 1 request model differs from the frozen snapshot",
            )

    def _admit_source(
        self,
        invocation: ModelInvocation,
        *,
        exact_input_tokens: int,
        minimum_completion_tokens: int,
        golden_minimum_call_floor_tokens: int,
    ) -> tuple[str, int, BudgetSnapshot]:
        required_floor = exact_input_tokens + minimum_completion_tokens
        snapshot = self._ledger.snapshot()
        if invocation.operation is ModelOperation.SINGLE_AGENT_MODEL:
            if required_floor > snapshot.remaining_tokens:
                raise ComparisonAdapterError(
                    Phase2FailureCode.TOKEN_INPUT_TOO_LARGE,
                    "Single-Agent input cannot preserve its completion floor",
                )
            now = self._utc_clock()
            try:
                request = CapacitySlotRequest(
                    permitted_operation=invocation.operation,
                    allowed_actions=invocation.allowed_actions,
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    minimum_token_floor=required_floor,
                    expires_at=now + timedelta(minutes=5),
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise ComparisonAdapterError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "Single-Agent admission clock is not valid UTC",
                ) from error
            slots, admitted_snapshot = self._ledger.hold_capacity_slots(
                expected_snapshot_sequence=snapshot.sequence,
                requests=(request,),
            )
            return slots[0].slot_id, required_floor, admitted_snapshot

        source_record_id = cast(str, invocation.source_record_id)
        source_floor = self._ledger.reserved_floor_for(source_record_id)
        expected_source_floor = (
            required_floor
            if invocation.operation is ModelOperation.COMMANDER_MODEL
            else golden_minimum_call_floor_tokens
        )
        if source_floor != expected_source_floor:
            raise ComparisonAdapterError(
                Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED,
                "held source floor differs from its operation-specific authority",
            )
        return source_record_id, source_floor, snapshot

    def _latch_terminal(
        self,
        code: Phase2FailureCode,
        expected_snapshot_sequence: int,
    ) -> BudgetSnapshot:
        if self._ledger.terminal_failure_code is not None:
            return self._ledger.snapshot()
        try:
            return self._ledger.record_terminal_failure(
                expected_snapshot_sequence=expected_snapshot_sequence,
                code=code,
            )
        except BudgetLedgerError:
            if self._ledger.terminal_failure_code is not None:
                return self._ledger.snapshot()
            current = self._ledger.snapshot()
            try:
                return self._ledger.record_terminal_failure(
                    expected_snapshot_sequence=current.sequence,
                    code=code,
                )
            except BudgetLedgerError as error:
                raise ComparisonAdapterError(
                    code,
                    "provider failure could not produce a stable terminal snapshot",
                ) from error

    @staticmethod
    def _require_phase1_completion_consistency(
        invocation: ModelInvocation,
        completion: ModelCompletion,
        response: Phase1Model,
    ) -> None:
        phase1_response = completion.phase1_response
        if phase1_response is None:
            return
        if invocation.operation is not ModelOperation.SINGLE_AGENT_MODEL:
            raise ComparisonAdapterError(
                Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                "only the Phase 1 wrapper may attach a Phase 1 response",
            )
        if (
            phase1_response.action.model_dump(mode="json")
            != response.model_dump(mode="json")
            or phase1_response.provider_name != completion.provider_identity
            or phase1_response.usage.input_tokens != completion.input_tokens
            or phase1_response.usage.output_tokens != completion.output_tokens
            or phase1_response.usage.total_tokens != completion.total_tokens
        ):
            raise ComparisonAdapterError(
                Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                "Phase 1 response differs from the adapter completion projection",
            )

    def _append_failed_audit(
        self,
        *,
        invocation: ModelInvocation,
        envelope_sha256: str,
        source_record_id: str,
        lease: BudgetLease,
        minimum_completion_tokens: int,
        completion: ModelCompletion | None,
        code: Phase2FailureCode,
        final_snapshot: BudgetSnapshot,
    ) -> None:
        response_sha256 = None
        if completion is not None:
            try:
                response_sha256 = hashlib.sha256(
                    canonical_json_bytes(completion.response)
                ).hexdigest()
            except TokenPolicyError:
                response_sha256 = None
        audit = ModelCallAuditRecord(
            schema_version="phase2.model-call-audit.v1",
            adapter_version=ADAPTER_VERSION,
            invocation_id=invocation.invocation_id,
            run_id=invocation.run_id,
            variant=invocation.variant,
            case_id=invocation.case_id,
            operation=invocation.operation,
            allowed_actions=invocation.allowed_actions,
            outer_caps=self._outer_caps,
            model_snapshot=invocation.provider_parameters.model_snapshot,
            expected_provider_identity=self._provider_parameters.provider_identity,
            observed_provider_identity=(
                completion.provider_identity if completion is not None else None
            ),
            token_policy_core_sha256=self._token_authority.core_sha256,
            response_schema_sha256=invocation.response_schema_sha256,
            envelope_sha256=envelope_sha256,
            response_sha256=response_sha256,
            source_record_id=source_record_id,
            lease_id=lease.lease_id,
            exact_input_tokens=lease.exact_input_tokens,
            minimum_completion_tokens=minimum_completion_tokens,
            max_completion_tokens=lease.max_completion_tokens,
            input_tokens=(completion.input_tokens if completion is not None else None),
            output_tokens=(completion.output_tokens if completion is not None else None),
            total_tokens=(completion.total_tokens if completion is not None else None),
            lease_snapshot_sequence=lease.snapshot_sequence + 1,
            final_snapshot_sequence=final_snapshot.sequence,
            status="FAILED",
            failure_code=code,
        )
        self._audit_records = (*self._audit_records, audit)


class Phase1GatewayBackend:
    """Compatibility backend that forwards the exact Phase 1 request unchanged."""

    def __init__(self, inner: ModelGateway) -> None:
        if not callable(getattr(inner, "complete", None)):
            raise TypeError("inner must implement ModelGateway")
        self.inner = inner

    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        del envelope, exact_input_tokens, max_completion_tokens
        if invocation.operation is not ModelOperation.SINGLE_AGENT_MODEL:
            raise ComparisonAdapterError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "Phase 1 gateway backend accepts only Single-Agent requests",
            )
        request = cast(ModelRequest, invocation.request)
        response = self.inner.complete(request)
        return ModelCompletion(
            schema_version="phase2.model-completion.v1",
            provider_identity=response.provider_name,
            response=cast(dict[str, JsonValue], response.action.model_dump(mode="json")),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            phase1_response=response,
        )


class Phase1ComparisonGateway:
    """ModelGateway-compatible entry point for the unchanged Phase 1 loop."""

    def __init__(self, inner: ModelGateway, adapter: ComparisonAdapter) -> None:
        backend = adapter._backend  # noqa: SLF001 - exact factory binding check
        if not isinstance(backend, Phase1GatewayBackend) or backend.inner is not inner:
            raise ComparisonAdapterError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "adapter is not bound to this exact Phase 1 gateway",
            )
        if adapter.snapshot().variant is not Phase2Variant.SINGLE_AGENT:
            raise ComparisonAdapterError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "Phase 1 wrapper requires a Single-Agent ledger",
            )
        self._adapter = adapter

    def complete(self, request: ModelRequest) -> ModelResponse:
        snapshot = self._adapter.snapshot()
        key = _SINGLE_KEY
        golden = self._adapter.token_authority.golden(*key)
        invocation = ModelInvocation(
            schema_version="phase2.model-invocation.v1",
            invocation_id=request.request_id,
            run_id=request.run_id,
            variant=Phase2Variant.SINGLE_AGENT,
            case_id=snapshot.case_id,
            operation=key[0],
            allowed_actions=key[1],
            request=request,
            provider_parameters=self._adapter.provider_parameters,
            token_policy_core_sha256=self._adapter.token_authority.core_sha256,
            response_schema_sha256=golden.response_schema_sha256,
            expected_snapshot_sequence=snapshot.sequence,
            source_record_id=None,
        )
        result = self._adapter.invoke(invocation)
        if result.completion.phase1_response is None:
            raise ComparisonAdapterError(
                Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                "Phase 1 backend omitted the exact response",
            )
        return result.completion.phase1_response


def make_phase1_comparison_gateway(
    inner: ModelGateway,
    adapter: ComparisonAdapter,
) -> Phase1ComparisonGateway:
    """Bind the unchanged Phase 1 loop to the one common comparison adapter."""

    return Phase1ComparisonGateway(inner, adapter)
