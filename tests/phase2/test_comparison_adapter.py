"""Focused contract tests for the shared Phase 2 comparison adapter."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest

from ecomsre.phase1.contracts import ModelRequest, ModelResponse, ModelUsage
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.comparison_adapter import (
    BudgetCaps,
    ComparisonAdapter,
    ComparisonAdapterError,
    ModelCompletion,
    ModelInvocation,
    Phase1GatewayBackend,
    ProviderParameters,
    make_phase1_comparison_gateway,
)
from ecomsre.phase2.contracts import (
    BudgetSnapshot,
    CapacitySlotRequest,
    CommanderRequest,
    JudgeRequest,
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
)
from ecomsre.phase2.token_policy import (
    GoldenKey,
    TokenAuthority,
    build_model_input_envelope,
    load_token_authority,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
RUN_ID = "a" * 32
CASE_ID = "case-001"
PROVIDER_ID = "scripted"


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


class RecordingBackend:
    def __init__(
        self,
        authority: TokenAuthority,
        *,
        mode: Literal[
            "ok",
            "missing",
            "inconsistent",
            "over",
            "provider-mismatch",
            "invalid-response",
            "raise",
        ] = "ok",
    ) -> None:
        self.authority = authority
        self.mode = mode
        self.calls = 0
        self.invocations: list[ModelInvocation] = []
        self.max_completion_tokens: list[int] = []

    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        del envelope
        self.calls += 1
        self.invocations.append(invocation)
        self.max_completion_tokens.append(max_completion_tokens)
        if self.mode == "raise":
            raise RuntimeError("injected provider failure")
        golden = self.authority.golden(
            invocation.operation,
            invocation.allowed_actions,
        )
        output_tokens = golden.minimal_response_tokens
        total_tokens = exact_input_tokens + output_tokens
        input_value: int | None = exact_input_tokens
        output_value: int | None = output_tokens
        total_value: int | None = total_tokens
        if self.mode == "missing":
            input_value = output_value = total_value = None
        elif self.mode == "inconsistent":
            total_value += 1
        elif self.mode == "over":
            output_value = max_completion_tokens + 1
            total_value = exact_input_tokens + output_value
        response = deepcopy(
            self.authority.minimal_responses[
                (invocation.operation, invocation.allowed_actions)
            ]
        )
        if self.mode == "invalid-response":
            response = {}
        return ModelCompletion(
            schema_version="phase2.model-completion.v1",
            provider_identity=(
                "other-provider"
                if self.mode == "provider-mismatch"
                else PROVIDER_ID
            ),
            response=response,
            input_tokens=input_value,
            output_tokens=output_value,
            total_tokens=total_value,
            phase1_response=None,
        )


class RecordingPhase1Gateway:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


@pytest.fixture(scope="module")
def authority() -> TokenAuthority:
    return load_token_authority(PROJECT_ROOT)


def ledger(variant: Phase2Variant) -> BudgetLedger:
    return BudgetLedger(
        run_id=RUN_ID,
        variant=variant,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(),
        monotonic_clock=lambda: 10.0,
        utc_clock=lambda: NOW,
    )


def key_for_variant(variant: Phase2Variant) -> GoldenKey:
    return {
        Phase2Variant.SINGLE_AGENT: (
            ModelOperation.SINGLE_AGENT_MODEL,
            ModelAllowedActions.PHASE1_ACTION_CATALOG,
        ),
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW: (
            ModelOperation.FINAL_JUDGE_MODEL,
            ModelAllowedActions.FINAL_ONLY,
        ),
        Phase2Variant.DYNAMIC_MULTI_AGENT: (
            ModelOperation.COMMANDER_MODEL,
            ModelAllowedActions.PLAN_ONLY,
        ),
    }[variant]


def request_for(
    authority: TokenAuthority,
    key: GoldenKey,
    request_snapshot: BudgetSnapshot,
) -> ModelRequest | CommanderRequest | JudgeRequest:
    payload = deepcopy(authority.minimal_requests[key])
    request_type: type[ModelRequest] | type[CommanderRequest] | type[JudgeRequest]
    if key[0] is ModelOperation.SINGLE_AGENT_MODEL:
        request_type = ModelRequest
    elif key[0] is ModelOperation.COMMANDER_MODEL:
        request_type = CommanderRequest
        payload["budget_snapshot"] = request_snapshot.model_dump(mode="json")
    else:
        request_type = JudgeRequest
        payload["budget_snapshot"] = request_snapshot.model_dump(mode="json")
    return request_type.model_validate(payload)


def prepared_adapter(
    authority: TokenAuthority,
    variant: Phase2Variant,
    *,
    mode: Literal[
        "ok",
        "missing",
        "inconsistent",
        "over",
        "provider-mismatch",
        "invalid-response",
        "raise",
    ] = "ok",
    floor_adjustment: int = 0,
    key: GoldenKey | None = None,
) -> tuple[ComparisonAdapter, RecordingBackend, ModelInvocation, BudgetLedger]:
    selected_key = key or key_for_variant(variant)
    budget = ledger(variant)
    request = request_for(authority, selected_key, budget.snapshot())
    envelope = build_model_input_envelope(
        authority.core,
        selected_key[0],
        selected_key[1],
        request,
    )
    exact_input_tokens = authority.exact_input_tokens(envelope)
    golden = authority.golden(*selected_key)
    source_record_id = None
    if variant is not Phase2Variant.SINGLE_AGENT:
        minimum_floor = (
            exact_input_tokens + golden.minimum_completion_tokens
            if selected_key[0] is ModelOperation.COMMANDER_MODEL
            else golden.minimum_call_floor_tokens
        )
        slots, _ = budget.hold_capacity_slots(
            expected_snapshot_sequence=budget.snapshot().sequence,
            requests=(
                CapacitySlotRequest(
                    permitted_operation=selected_key[0],
                    allowed_actions=selected_key[1],
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    minimum_token_floor=(
                        minimum_floor + floor_adjustment
                    ),
                    expires_at=NOW + timedelta(minutes=5),
                ),
            ),
        )
        source_record_id = slots[0].slot_id
    backend = RecordingBackend(authority, mode=mode)
    adapter = ComparisonAdapter(
        ledger=budget,
        token_authority=authority,
        backend=backend,
        expected_provider_identity=PROVIDER_ID,
        utc_clock=lambda: NOW,
    )
    invocation = ModelInvocation(
        schema_version="phase2.model-invocation.v1",
        invocation_id=f"invoke-{variant.value.lower()}",
        run_id=RUN_ID,
        variant=variant,
        case_id=CASE_ID,
        operation=selected_key[0],
        allowed_actions=selected_key[1],
        request=request,
        provider_parameters=adapter.provider_parameters,
        token_policy_core_sha256=authority.core_sha256,
        response_schema_sha256=golden.response_schema_sha256,
        expected_snapshot_sequence=budget.snapshot().sequence,
        source_record_id=source_record_id,
    )
    return adapter, backend, invocation, budget


def test_comparison_adapter_freezes_the_common_outer_caps() -> None:
    assert BudgetCaps() == BudgetCaps(
        model_calls=8,
        tool_calls=8,
        total_tokens=32_000,
    )


@pytest.mark.parametrize("variant", tuple(Phase2Variant))
def test_every_variant_crosses_one_comparison_adapter(
    authority: TokenAuthority,
    variant: Phase2Variant,
) -> None:
    adapter, backend, invocation, budget = prepared_adapter(authority, variant)

    result = adapter.invoke(invocation)

    assert backend.calls == 1
    assert result.snapshot == budget.snapshot()
    assert result.lease.actual_model_calls == 1
    assert result.snapshot.charged_model_calls == 1
    assert result.snapshot.cumulative_tokens == result.completion.total_tokens
    assert adapter.audit_records == (result.audit_record,)
    assert result.audit_record.variant is variant
    assert result.audit_record.outer_caps == BudgetCaps()
    assert result.audit_record.status == "CHARGED"


def test_parameter_mismatch_fails_before_backend_call(
    authority: TokenAuthority,
) -> None:
    adapter, backend, invocation, budget = prepared_adapter(
        authority,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
    )
    before = budget.snapshot()
    invocation = invocation.model_copy(
        update={
            "provider_parameters": ProviderParameters(
                model_snapshot=authority.core.model_snapshot,
                provider_identity=PROVIDER_ID,
                temperature=0.5,
                n=1,
                parallel_tool_calls=False,
            )
        }
    )

    with pytest.raises(ComparisonAdapterError) as captured:
        adapter.invoke(invocation)

    assert captured.value.code is Phase2FailureCode.PROVIDER_PARAMETER_MISMATCH
    assert backend.calls == 0
    assert budget.snapshot() == before


def test_schema_hash_drift_fails_before_backend_call(
    authority: TokenAuthority,
) -> None:
    adapter, backend, invocation, _ = prepared_adapter(
        authority,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
    )
    invocation = invocation.model_copy(update={"response_schema_sha256": "f" * 64})

    with pytest.raises(ComparisonAdapterError) as captured:
        adapter.invoke(invocation)

    assert captured.value.code is Phase2FailureCode.TOKEN_GOLDEN_MANIFEST_MISMATCH
    assert backend.calls == 0


def test_held_floor_above_exact_requirement_fails_before_backend_call(
    authority: TokenAuthority,
) -> None:
    adapter, backend, invocation, _ = prepared_adapter(
        authority,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
        floor_adjustment=1,
    )

    with pytest.raises(ComparisonAdapterError) as captured:
        adapter.invoke(invocation)

    assert captured.value.code is Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED
    assert backend.calls == 0


def test_success_is_charged_once_and_duplicate_invocation_never_reaches_backend(
    authority: TokenAuthority,
) -> None:
    adapter, backend, invocation, budget = prepared_adapter(
        authority,
        Phase2Variant.SINGLE_AGENT,
    )
    adapter.invoke(invocation)

    with pytest.raises(ComparisonAdapterError) as captured:
        adapter.invoke(invocation)

    assert captured.value.code is Phase2FailureCode.COMPARISON_ADAPTER_BYPASS
    assert backend.calls == 1
    assert budget.snapshot().charged_model_calls == 1
    assert len(adapter.audit_records) == 1


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    (
        ("missing", Phase2FailureCode.PROVIDER_USAGE_MISSING),
        ("inconsistent", Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT),
        ("over", Phase2FailureCode.PROVIDER_USAGE_EXCEEDS_LEASE),
        ("provider-mismatch", Phase2FailureCode.PROVIDER_PARAMETER_MISMATCH),
        ("invalid-response", Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT),
        ("raise", Phase2FailureCode.PROVIDER_USAGE_MISSING),
    ),
)
def test_provider_failure_is_terminal_and_forbids_a_later_call(
    authority: TokenAuthority,
    mode: Literal[
        "missing",
        "inconsistent",
        "over",
        "provider-mismatch",
        "invalid-response",
        "raise",
    ],
    expected_code: Phase2FailureCode,
) -> None:
    adapter, backend, invocation, budget = prepared_adapter(
        authority,
        Phase2Variant.SINGLE_AGENT,
        mode=mode,
    )

    with pytest.raises(ComparisonAdapterError) as first:
        adapter.invoke(invocation)
    with pytest.raises(ComparisonAdapterError) as second:
        adapter.invoke(invocation.model_copy(update={"invocation_id": "invoke-later"}))

    assert first.value.code is expected_code
    assert second.value.code is expected_code
    assert budget.terminal_failure_code is expected_code
    assert backend.calls == 1
    assert budget.snapshot().charged_model_calls == 0
    assert len(adapter.audit_records) == 1
    assert adapter.audit_records[0].status == "FAILED"
    assert adapter.audit_records[0].failure_code is expected_code


def test_fixed_workflow_rejects_first_judge_key_before_backend(
    authority: TokenAuthority,
) -> None:
    adapter, backend, invocation, _ = prepared_adapter(
        authority,
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        key=(
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelAllowedActions.FINAL_ONLY,
        ),
    )

    with pytest.raises(ComparisonAdapterError) as captured:
        adapter.invoke(invocation)

    assert captured.value.code is Phase2FailureCode.COMPARISON_ADAPTER_BYPASS
    assert backend.calls == 0


def test_phase1_gateway_wrapper_preserves_request_response_and_action(
    authority: TokenAuthority,
) -> None:
    key = key_for_variant(Phase2Variant.SINGLE_AGENT)
    request = cast(
        ModelRequest,
        request_for(authority, key, ledger(Phase2Variant.SINGLE_AGENT).snapshot()),
    )
    envelope = build_model_input_envelope(
        authority.core,
        key[0],
        key[1],
        request,
    )
    input_tokens = authority.exact_input_tokens(envelope)
    action = authority.minimal_responses[key]
    response = ModelResponse(
        schema_version="phase1.model-response.v1",
        request_id=request.request_id,
        response_id="response-phase1-001",
        run_id=request.run_id,
        agent_id=request.agent_id,
        incident_id=request.incident_id,
        task_id=request.task_id,
        provider_name=PROVIDER_ID,
        model_name=request.model_name,
        action=action,
        usage=ModelUsage(
            input_tokens=input_tokens,
            output_tokens=1,
            total_tokens=input_tokens + 1,
        ),
        started_at=NOW,
        ended_at=NOW,
        monotonic_duration_seconds=0.0,
        error_code=None,
    )
    inner = RecordingPhase1Gateway(response)
    budget = ledger(Phase2Variant.SINGLE_AGENT)
    adapter = ComparisonAdapter(
        ledger=budget,
        token_authority=authority,
        backend=Phase1GatewayBackend(inner),
        expected_provider_identity=PROVIDER_ID,
        utc_clock=lambda: NOW,
    )
    wrapped = make_phase1_comparison_gateway(inner, adapter)

    observed = wrapped.complete(request)

    assert inner.requests == [request]
    assert observed == response
    assert observed.action == response.action
    assert adapter.audit_records[0].input_tokens == input_tokens
