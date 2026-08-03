"""Focused tests for the strictly one-call Phase 2 Commander runtime."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.commander import (
    CommanderContext,
    CommanderError,
    CommanderErrorCode,
    CommanderRuntime,
    source_capabilities,
)
from ecomsre.phase2.comparison_adapter import (
    ComparisonAdapter,
    ModelCompletion,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    CommanderRequest,
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2Variant,
)
from ecomsre.phase2.dag import DagValidationErrorCode, schedule_layers
from ecomsre.phase2.scripted import ScriptedModelBackend
from ecomsre.phase2.token_policy import TokenAuthority, load_token_authority


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
RUN_ID = "a" * 32
CASE_ID = "case-001"
PROVIDER_ID = "phase2-scripted"
COMMANDER_KEY = (
    ModelOperation.COMMANDER_MODEL,
    ModelAllowedActions.PLAN_ONLY,
)


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


class WrongProvenanceBackend:
    def __init__(self, authority: TokenAuthority) -> None:
        self.authority = authority
        self.calls = 0

    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        del envelope, max_completion_tokens
        self.calls += 1
        payload = deepcopy(self.authority.minimal_responses[COMMANDER_KEY])
        payload["run_id"] = invocation.run_id
        request = invocation.request
        assert isinstance(request, CommanderRequest)
        payload["incident_id"] = request.incident.incident_id
        output_tokens = self.authority.golden(*COMMANDER_KEY).minimal_response_tokens
        return ModelCompletion(
            schema_version="phase2.model-completion.v1",
            provider_identity=PROVIDER_ID,
            response=payload,
            input_tokens=exact_input_tokens,
            output_tokens=output_tokens,
            total_tokens=exact_input_tokens + output_tokens,
            phase1_response=None,
        )


@pytest.fixture(scope="module")
def authority() -> TokenAuthority:
    return load_token_authority(PROJECT_ROOT)


def ledger() -> BudgetLedger:
    return BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(),
        monotonic_clock=lambda: 10.0,
        utc_clock=lambda: NOW,
    )


def context(authority: TokenAuthority) -> CommanderContext:
    request = CommanderRequest.model_validate(
        authority.minimal_requests[COMMANDER_KEY]
    )
    return CommanderContext(
        schema_version="phase2.commander-context.v1",
        run_id=RUN_ID,
        incident=request.incident,
        allowed_started_at=request.allowed_started_at,
        allowed_ended_at=request.allowed_ended_at,
    )


def runtime(
    authority: TokenAuthority,
    *,
    backend: ScriptedModelBackend | WrongProvenanceBackend | None = None,
) -> tuple[
    CommanderRuntime,
    ScriptedModelBackend | WrongProvenanceBackend,
    BudgetLedger,
    ComparisonAdapter,
]:
    budget = ledger()
    selected_backend = backend or ScriptedModelBackend(
        token_authority=authority,
        provider_identity=PROVIDER_ID,
    )
    adapter = ComparisonAdapter(
        ledger=budget,
        token_authority=authority,
        backend=selected_backend,
        expected_provider_identity=PROVIDER_ID,
        utc_clock=lambda: NOW,
    )
    return (
        CommanderRuntime(
            ledger=budget,
            adapter=adapter,
            utc_clock=lambda: NOW,
        ),
        selected_backend,
        budget,
        adapter,
    )


def test_commander_makes_exactly_one_typed_call_and_admits_plan(
    authority: TokenAuthority,
) -> None:
    commander, backend, budget, adapter = runtime(authority)

    outcome = commander.create_initial_graph(context(authority))

    assert backend.calls == 1
    assert len(outcome.plan.nodes) == 2
    assert tuple(item.node_id for item in outcome.plan.nodes) == (
        "metrics-initial-1",
        "traces-initial-1",
    )
    assert outcome.admission.admitted_graph.initial_plan == outcome.plan
    assert adapter.audit_records[0].operation is ModelOperation.COMMANDER_MODEL
    assert budget.snapshot().charged_model_calls == 1
    assert budget.snapshot().charged_tool_calls == 0
    assert budget.snapshot().reserved_model_calls == len(outcome.plan.nodes) + 1
    assert budget.snapshot().reserved_tool_calls == len(outcome.plan.nodes)


def test_commander_request_and_plan_preserve_canonical_provenance_and_order(
    authority: TokenAuthority,
) -> None:
    commander, _, _, _ = runtime(authority)

    outcome = commander.create_initial_graph(context(authority))
    scheduled = tuple(
        item.node_id
        for layer in schedule_layers(outcome.plan)
        for item in layer
    )

    assert outcome.request.source_capabilities == source_capabilities()
    assert outcome.plan.budget_snapshot_id == outcome.request.budget_snapshot.snapshot_id
    assert tuple(item.node_id for item in outcome.admission.node_slot_bindings) == scheduled
    assert tuple(item.priority for item in outcome.plan.nodes) == (0, 1)


def test_second_commander_call_is_rejected_before_backend(
    authority: TokenAuthority,
) -> None:
    commander, backend, budget, _ = runtime(authority)
    commander.create_initial_graph(context(authority))
    before = budget.snapshot()

    with pytest.raises(CommanderError) as captured:
        commander.create_initial_graph(context(authority))

    assert captured.value.code is CommanderErrorCode.ALREADY_INVOKED
    assert backend.calls == 1
    assert budget.snapshot() == before


def test_wrong_run_fails_before_capacity_or_backend(
    authority: TokenAuthority,
) -> None:
    commander, backend, budget, _ = runtime(authority)
    invalid = context(authority).model_copy(update={"run_id": "b" * 32})
    before = budget.snapshot()

    with pytest.raises(CommanderError) as captured:
        commander.create_initial_graph(invalid)

    assert captured.value.code is CommanderErrorCode.INVALID_CONTEXT
    assert backend.calls == 0
    assert budget.snapshot() == before


def test_invalid_plan_provenance_fails_before_any_specialist_or_tool(
    authority: TokenAuthority,
) -> None:
    backend = WrongProvenanceBackend(authority)
    commander, _, budget, _ = runtime(authority, backend=backend)

    with pytest.raises(CommanderError) as captured:
        commander.create_initial_graph(context(authority))

    assert captured.value.code is DagValidationErrorCode.STALE_BUDGET_SNAPSHOT
    assert backend.calls == 1
    assert budget.snapshot().charged_model_calls == 1
    assert budget.snapshot().charged_tool_calls == 0
    assert not budget.snapshot().active_specialist_authorization_ids
    assert not budget.snapshot().active_lease_ids


def test_scripted_commander_derives_at_most_three_nodes_from_visible_incident(
    authority: TokenAuthority,
) -> None:
    commander, backend, _, _ = runtime(authority)
    base = context(authority)
    incident = base.incident.model_copy(
        update={
            "summary": "Latency, service failure, and a configuration rollout are visible."
        }
    )
    richer = base.model_copy(update={"incident": incident})

    outcome = commander.create_initial_graph(richer)

    assert backend.calls == 1
    assert 1 <= len(outcome.plan.nodes) <= 3
    assert tuple(item.source.value for item in outcome.plan.nodes) == (
        "METRICS",
        "LOGS",
        "TRACES",
    )


def test_model_visible_commander_body_has_no_runtime_or_evaluator_fields(
    authority: TokenAuthority,
) -> None:
    commander, _, _, _ = runtime(authority)
    outcome = commander.create_initial_graph(context(authority))
    visible = outcome.request.model_dump(mode="json")
    serialized = repr(visible).casefold()

    assert "case_id" not in visible
    assert "variant" not in visible
    assert "expected_answer" not in serialized
    assert "fixture" not in serialized
    assert "evaluator" not in serialized
