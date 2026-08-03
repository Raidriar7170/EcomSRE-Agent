"""Focused tests for final-only and one-refinement Judge execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from ecomsre.phase1.contracts import (
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    MetricsAction,
    ReadOnlyToolName,
    Severity,
    ToolAction,
    ToolCallRecord,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.comparison_adapter import (
    ComparisonAdapter,
    ModelCompletion,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    AdditionalInvestigationRequest,
    CapacitySlotRequest,
    ConditionalRefinementBundleStatus,
    FindingHypothesis,
    HypothesisEvidenceGroup,
    InvestigationNode,
    InvestigationPlan,
    JudgeFinalResult,
    MissingEvidenceItem,
    ModelAllowedActions,
    ModelInputEnvelope,
    ModelOperation,
    Phase2Variant,
    SpecialistFinding,
    SpecialistRole,
    build_initial_admitted_graph,
)
from ecomsre.phase2.evidence_views import FindingStore
from ecomsre.phase2.judge import (
    JudgeContext,
    JudgeError,
    JudgeErrorCode,
    JudgeRuntime,
)
from ecomsre.phase2.scripted import ScriptedModelBackend
from ecomsre.phase2.specialists import SpecialistRuntime
from ecomsre.phase2.token_policy import TokenAuthority, load_token_authority
from ecomsre.phase2.tool_isolation import SpecialistToolRegistry
import ecomsre.phase2.judge as judge_module


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
END = NOW + timedelta(minutes=5)
EXPIRES = END + timedelta(minutes=5)
RUN_ID = "a" * 32
CASE_ID = "case-001"
INCIDENT_ID = "inc-001"
PROVIDER_ID = "phase2-scripted"


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


class InvalidRefinementBackend(ScriptedModelBackend):
    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        completion = super().complete(
            invocation,
            envelope=envelope,
            exact_input_tokens=exact_input_tokens,
            max_completion_tokens=max_completion_tokens,
        )
        response = dict(completion.response)
        if response.get("action_type") == "ADDITIONAL_INVESTIGATION":
            response["target_hypothesis_ids"] = ["unknown-hypothesis"]
        return completion.model_copy(update={"response": response})


@pytest.fixture(scope="module")
def authority() -> TokenAuthority:
    return load_token_authority(PROJECT_ROOT)


def incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id=INCIDENT_ID,
        alert_source_service="frontend",
        summary="Checkout latency exceeds the SLO.",
        started_at=NOW,
        ended_at=END,
        affected_sli="checkout p95 latency",
        severity=Severity.SEV2,
    )


def initial_graph():
    query = MetricsAction(
        action_type="metrics",
        started_at=NOW,
        ended_at=END,
        service="checkoutservice",
    )
    node = InvestigationNode(
        schema_version="phase2.investigation-node.v1",
        node_id="node-metrics-001",
        source=EvidenceSource.METRICS,
        specialist_role=SpecialistRole.METRICS_AGENT,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        query=query,
        depends_on=(),
        objective="Inspect bounded metrics observations.",
        query_started_at=NOW,
        query_ended_at=END,
        priority=0,
    )
    return build_initial_admitted_graph(
        InvestigationPlan(
            schema_version="phase2.investigation-plan.v1",
            run_id=RUN_ID,
            incident_id=INCIDENT_ID,
            plan_id="plan-001",
            nodes=(node,),
            planning_rationale="Use bounded metrics observations.",
            budget_snapshot_id="commander-request-snapshot",
        )
    )


def add_initial_finding(
    evidence_store: EvidenceStore,
    finding_store: FindingStore,
    *,
    missing_trace: bool,
) -> SpecialistFinding:
    evidence = evidence_store.add(
        source=EvidenceSource.METRICS,
        observation_type="latency",
        attributes=(EvidenceAttribute(name="p95_ms", value=920.0),),
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256="0" * 64,
        limitations=(),
        summary="Checkout latency increased.",
        started_at=NOW,
        ended_at=END,
        service="checkoutservice",
    )
    finding = SpecialistFinding(
        schema_version="phase2.specialist-finding.v1",
        finding_id="finding-metrics-001",
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        plan_id="plan-001",
        node_id="node-metrics-001",
        source=EvidenceSource.METRICS,
        specialist_role=SpecialistRole.METRICS_AGENT,
        evidence_refs=(evidence.evidence_ref,),
        hypotheses=(
            FindingHypothesis(
                schema_version="phase2.finding-hypothesis.v1",
                hypothesis_id="hypothesis-latency",
                root_service="checkoutservice",
                fault_mechanism=None,
                claim="Checkout latency is elevated.",
            ),
        ),
        supporting_evidence_refs=(
            HypothesisEvidenceGroup(
                schema_version="phase2.hypothesis-evidence-group.v1",
                hypothesis_id="hypothesis-latency",
                evidence_refs=(evidence.evidence_ref,),
            ),
        ),
        contradicting_evidence_refs=(),
        missing_evidence=(
            (
                MissingEvidenceItem(
                    schema_version="phase2.missing-evidence-item.v1",
                    question="Which trace path contains the latency?",
                    desired_source=EvidenceSource.TRACES,
                ),
            )
            if missing_trace
            else ()
        ),
        confidence=0.6,
        finding_rationale="Metrics show elevated latency.",
    )
    return finding_store.add(finding)


def harness(
    authority: TokenAuthority,
    *,
    variant: Phase2Variant = Phase2Variant.DYNAMIC_MULTI_AGENT,
    allow_refinement: bool,
    missing_trace: bool = False,
    invalid_refinement: bool = False,
):
    budget = BudgetLedger(
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
    operation = (
        ModelOperation.FINAL_JUDGE_MODEL
        if variant is Phase2Variant.FIXED_SPECIALIST_WORKFLOW
        else ModelOperation.FIRST_JUDGE_MODEL
    )
    golden = authority.golden(operation, ModelAllowedActions.FINAL_ONLY)
    slots, _ = budget.hold_capacity_slots(
        expected_snapshot_sequence=0,
        requests=(
            CapacitySlotRequest(
                permitted_operation=operation,
                allowed_actions=ModelAllowedActions.FINAL_ONLY,
                reserved_model_calls=1,
                reserved_tool_calls=0,
                minimum_token_floor=golden.minimum_call_floor_tokens,
                expires_at=EXPIRES,
            ),
        ),
    )
    evidence_store = EvidenceStore(RUN_ID)
    finding_store = FindingStore(RUN_ID)
    finding = add_initial_finding(
        evidence_store,
        finding_store,
        missing_trace=missing_trace,
    )
    backend: ScriptedModelBackend = (
        InvalidRefinementBackend(
            token_authority=authority,
            provider_identity=PROVIDER_ID,
        )
        if invalid_refinement
        else ScriptedModelBackend(
            token_authority=authority,
            provider_identity=PROVIDER_ID,
        )
    )
    adapter = ComparisonAdapter(
        ledger=budget,
        token_authority=authority,
        backend=backend,
        expected_provider_identity=PROVIDER_ID,
        utc_clock=lambda: NOW,
    )
    runtime = JudgeRuntime(
        ledger=budget,
        adapter=adapter,
        evidence_store=evidence_store,
        finding_store=finding_store,
        utc_clock=lambda: NOW,
    )
    context = JudgeContext(
        schema_version="phase2.judge-context.v1",
        run_id=RUN_ID,
        incident=incident(),
        admitted_graph=initial_graph(),
        finding_ids=(finding.finding_id,),
        judge_capacity_slot_id=slots[0].slot_id,
        allow_refinement=allow_refinement,
    )
    return runtime, backend, budget, evidence_store, finding_store, context


@pytest.mark.parametrize(
    "variant",
    (
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
    ),
)
def test_final_only_path_calls_one_judge_and_uses_phase1_validator(
    authority: TokenAuthority,
    monkeypatch: pytest.MonkeyPatch,
    variant: Phase2Variant,
) -> None:
    runtime, backend, budget, _, _, context = harness(
        authority,
        variant=variant,
        allow_refinement=False,
    )
    calls = 0
    real_validator = judge_module.validate_rca_result

    def recording_validator(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_validator(*args, **kwargs)

    monkeypatch.setattr(judge_module, "validate_rca_result", recording_validator)

    outcome = runtime.judge(context)

    assert backend.calls == 1
    assert calls == 1
    assert isinstance(outcome.action, JudgeFinalResult)
    assert outcome.result == outcome.action.rca_result
    assert outcome.request.allowed_actions is ModelAllowedActions.FINAL_ONLY
    assert budget.snapshot().charged_model_calls == 1


def test_conditional_bundle_can_finish_without_refinement_in_one_call(
    authority: TokenAuthority,
) -> None:
    runtime, backend, budget, _, _, context = harness(
        authority,
        allow_refinement=True,
    )

    outcome = runtime.judge(context)

    assert backend.calls == 1
    assert isinstance(outcome.action, JudgeFinalResult)
    assert outcome.request.allowed_actions is ModelAllowedActions.FINAL_OR_REFINEMENT
    assert outcome.bundle_id is not None
    assert budget.conditional_bundle(outcome.bundle_id).status is (
        ConditionalRefinementBundleStatus.RELEASED
    )


def test_valid_one_node_refinement_executes_then_finalizes_final_only(
    authority: TokenAuthority,
) -> None:
    runtime, backend, budget, evidence_store, finding_store, context = harness(
        authority,
        allow_refinement=True,
        missing_trace=True,
    )
    first = runtime.judge(context)
    assert isinstance(first.action, AdditionalInvestigationRequest)
    assert len(first.refinement_contexts) == 1
    refinement_node = first.admitted_graph.refinement_fragment.nodes[0]
    tool_calls: list[ToolAction] = []

    def execute_trace(query: ToolAction) -> ToolCallRecord:
        tool_calls.append(query)
        evidence = evidence_store.add(
            source=EvidenceSource.TRACES,
            observation_type="trace-path",
            attributes=(EvidenceAttribute(name="duration_ms", value=930.0),),
            raw_artifact_ref="traces.json#0",
            raw_artifact_sha256="1" * 64,
            limitations=(),
            summary="Trace path contains bounded latency.",
            started_at=NOW,
            ended_at=END,
            service="checkoutservice",
        )
        return ToolCallRecord(
            schema_version="phase1.tool-call-record.v1",
            call_id="tool-call-traces-refinement",
            run_id=RUN_ID,
            agent_id=SpecialistRole.TRACE_AGENT.value,
            incident_id=INCIDENT_ID,
            task_id=refinement_node.node_id,
            tool_name=ReadOnlyToolName.SEARCH_TRACES,
            action=query,
            evidence=(evidence,),
            evidence_refs=(evidence.evidence_ref,),
            started_at=NOW,
            ended_at=END,
            monotonic_duration_seconds=0.1,
            budget_consumed=True,
            dispatched=True,
            evidence_quarantined=False,
            usable=True,
            status="OK",
            error_code=None,
        )

    specialist = SpecialistRuntime(
        ledger=budget,
        adapter=runtime._adapter,  # noqa: SLF001 - shared workflow adapter
        evidence_store=evidence_store,
        finding_store=finding_store,
        registries={
            SpecialistRole.TRACE_AGENT: SpecialistToolRegistry(
                run_id=RUN_ID,
                case_id=CASE_ID,
                variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
                specialist_role=SpecialistRole.TRACE_AGENT,
                ledger=budget,
                executor=execute_trace,
            )
        },
    )
    refined = specialist.execute_node(first.refinement_contexts[0])
    final = runtime.finalize(
        (context.finding_ids[0], refined.finding.finding_id)
    )

    assert tool_calls == [refinement_node.query]
    assert backend.calls == 3
    assert final.request.refinement_round == 1
    assert final.request.allowed_actions is ModelAllowedActions.FINAL_ONLY
    assert isinstance(final.action, JudgeFinalResult)
    assert final.action.refinement_used is True
    assert budget.conditional_bundle(cast(str, final.bundle_id)).status is (
        ConditionalRefinementBundleStatus.COMPLETED
    )


def test_invalid_refinement_uses_same_response_fallback_without_new_call(
    authority: TokenAuthority,
) -> None:
    runtime, backend, budget, _, _, context = harness(
        authority,
        allow_refinement=True,
        missing_trace=True,
        invalid_refinement=True,
    )

    outcome = runtime.judge(context)

    assert backend.calls == 1
    assert isinstance(outcome.action, AdditionalInvestigationRequest)
    assert outcome.fallback_used is True
    assert outcome.result == outcome.action.fallback_rca_result
    assert outcome.refinement_contexts == ()
    assert budget.conditional_bundle(cast(str, outcome.bundle_id)).status is (
        ConditionalRefinementBundleStatus.RELEASED
    )


def test_first_judge_and_finalization_cannot_repeat(
    authority: TokenAuthority,
) -> None:
    runtime, backend, _, _, _, context = harness(
        authority,
        allow_refinement=False,
    )
    runtime.judge(context)

    with pytest.raises(JudgeError) as repeated:
        runtime.judge(context)
    with pytest.raises(JudgeError) as no_pending:
        runtime.finalize(context.finding_ids)

    assert repeated.value.code is JudgeErrorCode.ALREADY_JUDGED
    assert no_pending.value.code is JudgeErrorCode.NO_PENDING_REFINEMENT
    assert backend.calls == 1
