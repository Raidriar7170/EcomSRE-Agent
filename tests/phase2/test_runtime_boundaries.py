from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from ecomsre.phase1.contracts import (
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    MetricsAction,
    ReadOnlyToolName,
    Severity,
    ToolCallRecord,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    BudgetOwnerRole,
    CapacitySlotRequest,
    FindingHypothesis,
    HypothesisEvidenceGroup,
    InvestigationNode,
    InvestigationPlan,
    ModelAllowedActions,
    ModelOperation,
    Phase2Variant,
    SpecialistFinding,
    SpecialistRole,
    SpecialistTask,
    build_initial_admitted_graph,
)
from ecomsre.phase2.evidence_views import (
    EvidenceResolutionError,
    EvidenceResolutionErrorCode,
    FindingStore,
    build_judge_request,
    build_specialist_model_request,
    resolve_evidence_view,
)
from ecomsre.phase2.tool_isolation import SpecialistToolRegistry


RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
CASE_ID = "case-001"
START = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
END = START + timedelta(minutes=5)


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


class NoEnumerationEvidenceStore(EvidenceStore):
    def snapshot(self):
        raise AssertionError("store enumeration is forbidden")


class SpyEvidenceStore(NoEnumerationEvidenceStore):
    def __init__(self, run_id: str = RUN_ID) -> None:
        super().__init__(run_id)
        self.resolved: list[str] = []

    def resolve(self, reference: str):
        self.resolved.append(reference)
        return super().resolve(reference)


def add_metrics(store: EvidenceStore, *, observation_type: str = "latency"):
    return store.add(
        source=EvidenceSource.METRICS,
        observation_type=observation_type,
        attributes=(EvidenceAttribute(name="p95_ms", value=920.0),),
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256="0" * 64,
        limitations=(),
        summary=f"Checkout {observation_type} increased.",
        started_at=START,
        ended_at=END,
        service="checkoutservice",
    )


def incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id="inc-001",
        alert_source_service="frontend",
        summary="Checkout latency exceeds the SLO.",
        started_at=START,
        ended_at=END,
        affected_sli="checkout p95 latency",
        severity=Severity.SEV2,
    )


def query() -> MetricsAction:
    return MetricsAction(
        action_type="metrics",
        started_at=START,
        ended_at=END,
        service="checkoutservice",
    )


def graph() -> AdmittedInvestigationGraph:
    node = InvestigationNode(
        schema_version="phase2.investigation-node.v1",
        node_id="node-metrics-001",
        source=EvidenceSource.METRICS,
        specialist_role=SpecialistRole.METRICS_AGENT,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        query=query(),
        depends_on=(),
        objective="Determine whether checkout latency increased.",
        query_started_at=START,
        query_ended_at=END,
        priority=1,
    )
    plan = InvestigationPlan(
        schema_version="phase2.investigation-plan.v1",
        run_id=RUN_ID,
        incident_id="inc-001",
        plan_id="plan-001",
        nodes=(node,),
        planning_rationale="Metrics establish whether the alert is real.",
        budget_snapshot_id="commander-request-snapshot",
    )
    return build_initial_admitted_graph(plan)


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
        utc_clock=lambda: START,
    )


def specialist_runtime(
    *,
    store: EvidenceStore | None = None,
    dependency: bool = False,
):
    evidence_store = store or NoEnumerationEvidenceStore(RUN_ID)
    dependency_item = (
        add_metrics(evidence_store, observation_type="dependency")
        if dependency
        else None
    )
    new_item = add_metrics(evidence_store, observation_type="new")
    instance = ledger()
    slots, _ = instance.hold_capacity_slots(
        expected_snapshot_sequence=0,
        requests=(
            CapacitySlotRequest(
                permitted_operation=ModelOperation.SPECIALIST_MODEL,
                allowed_actions=ModelAllowedActions.FINDING_ONLY,
                reserved_model_calls=1,
                reserved_tool_calls=1,
                minimum_token_floor=720,
                expires_at=END,
            ),
        ),
    )
    authorization, precharge = instance.materialize_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=slots[0].slot_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source=EvidenceSource.METRICS,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
    )
    selected_task = SpecialistTask(
        schema_version="phase2.specialist-task.v1",
        run_id=RUN_ID,
        incident_id="inc-001",
        plan_id="plan-001",
        node_id="node-metrics-001",
        source=EvidenceSource.METRICS,
        specialist_role=SpecialistRole.METRICS_AGENT,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        query=query(),
        objective="Determine whether checkout latency increased.",
        dependency_finding_ids=("finding-dependency",) if dependency else (),
        dependency_evidence_refs=(
            (dependency_item.evidence_ref,) if dependency_item is not None else ()
        ),
        tool_authorization_id=authorization.authorization_id,
        model_capacity_slot_id=authorization.capacity_slot_id,
    )
    tool_record = ToolCallRecord(
        schema_version="phase1.tool-call-record.v1",
        call_id="tool-call-0001",
        run_id=RUN_ID,
        agent_id=SpecialistRole.METRICS_AGENT.value,
        incident_id="inc-001",
        task_id="node-metrics-001",
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        action=query(),
        evidence=(new_item,),
        evidence_refs=(new_item.evidence_ref,),
        started_at=START,
        ended_at=END,
        monotonic_duration_seconds=0.1,
        budget_consumed=True,
        dispatched=True,
        evidence_quarantined=False,
        usable=True,
        status="OK",
        error_code=None,
    )
    dispatch_result = SpecialistToolRegistry(
        run_id=RUN_ID,
        case_id=CASE_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        specialist_role=SpecialistRole.METRICS_AGENT,
        ledger=instance,
        executor=lambda _query: tool_record,
    ).dispatch(selected_task)
    return (
        instance,
        selected_task,
        dispatch_result,
        evidence_store,
        precharge,
        dependency_item,
        new_item,
    )


def finding(*evidence_refs: str, **overrides: object) -> SpecialistFinding:
    refs = evidence_refs
    payload: dict[str, object] = {
        "schema_version": "phase2.specialist-finding.v1",
        "finding_id": "finding-001",
        "run_id": RUN_ID,
        "incident_id": "inc-001",
        "plan_id": "plan-001",
        "node_id": "node-metrics-001",
        "source": EvidenceSource.METRICS,
        "specialist_role": SpecialistRole.METRICS_AGENT,
        "evidence_refs": refs,
        "hypotheses": (
            FindingHypothesis(
                schema_version="phase2.finding-hypothesis.v1",
                hypothesis_id="hypothesis-001",
                root_service="checkoutservice",
                fault_mechanism=None,
                claim="Checkout latency is elevated.",
            ),
        ),
        "supporting_evidence_refs": (
            HypothesisEvidenceGroup(
                schema_version="phase2.hypothesis-evidence-group.v1",
                hypothesis_id="hypothesis-001",
                evidence_refs=refs,
            ),
        ),
        "contradicting_evidence_refs": (),
        "missing_evidence": (),
        "confidence": 0.6,
        "finding_rationale": "Metrics support elevated latency.",
    }
    payload.update(overrides)
    return SpecialistFinding.model_validate(payload)


def test_resolved_view_exposes_only_requested_refs_in_requested_order() -> None:
    store = NoEnumerationEvidenceStore(RUN_ID)
    first = add_metrics(store, observation_type="latency")
    second = add_metrics(store, observation_type="errors")
    view = resolve_evidence_view(
        evidence_store=store,
        run_id=RUN_ID,
        evidence_refs=(second.evidence_ref, first.evidence_ref),
    )
    assert view.evidence == (second, first)


@pytest.mark.parametrize(
    ("refs", "code"),
    (
        (
            (f"evidence://{OTHER_RUN_ID}/metrics/0001",),
            EvidenceResolutionErrorCode.CROSS_RUN_REF,
        ),
        (
            (f"evidence://{RUN_ID}/metrics/9999",),
            EvidenceResolutionErrorCode.UNKNOWN_REF,
        ),
        (
            (f"evidence://{RUN_ID}/metrics/0001",) * 2,
            EvidenceResolutionErrorCode.INVALID_INPUT,
        ),
        (("not-an-evidence-ref",), EvidenceResolutionErrorCode.MALFORMED_REF),
    ),
)
def test_resolved_view_rejects_cross_run_unknown_duplicate_and_malformed_refs(
    refs: tuple[str, ...],
    code: EvidenceResolutionErrorCode,
) -> None:
    store = NoEnumerationEvidenceStore(RUN_ID)
    add_metrics(store)
    with pytest.raises(EvidenceResolutionError) as captured:
        resolve_evidence_view(
            evidence_store=store,
            run_id=RUN_ID,
            evidence_refs=refs,
        )
    assert captured.value.code is code


def test_specialist_request_uses_exact_dispatch_result_without_model_lease() -> None:
    (
        _instance,
        selected_task,
        dispatch_result,
        store,
        _precharge,
        dependency_item,
        new_item,
    ) = specialist_runtime(dependency=True)
    request = build_specialist_model_request(
        task=selected_task,
        dispatch_result=dispatch_result,
        budget_snapshot=dispatch_result.budget_snapshot,
        evidence_store=store,
    )
    assert request.new_evidence == (new_item,)
    assert request.resolved_dependency_evidence_view.evidence == (
        dependency_item,
    )
    assert request.task.tool_authorization_id in (
        request.budget_snapshot.active_specialist_authorization_ids
    )
    assert request.task.model_capacity_slot_id not in (
        request.budget_snapshot.active_capacity_slot_ids
    )
    assert request.model_dump(mode="json").keys() == {
        "schema_version",
        "task",
        "tool_call_record",
        "new_evidence",
        "dependency_finding_ids",
        "resolved_dependency_evidence_view",
        "budget_snapshot",
    }


def test_specialist_request_rejects_precharge_and_postlease_snapshots() -> None:
    instance, selected_task, result, store, precharge, *_ = specialist_runtime()
    with pytest.raises(EvidenceResolutionError) as precharge_error:
        build_specialist_model_request(
            task=selected_task,
            dispatch_result=result,
            budget_snapshot=precharge,
            evidence_store=store,
        )
    assert precharge_error.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH

    lease, leased_snapshot = instance.expand_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        source_record_id=result.specialist_authorization.authorization_id,
        exact_input_tokens=500,
        minimum_completion_tokens=220,
        max_completion_tokens=220,
    )
    assert lease.source_record_id == result.specialist_authorization.authorization_id
    with pytest.raises(EvidenceResolutionError) as leased_error:
        build_specialist_model_request(
            task=selected_task,
            dispatch_result=result,
            budget_snapshot=leased_snapshot,
            evidence_store=store,
        )
    assert leased_error.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH


def test_task8_rebuild_accepts_later_snapshot_without_redispatch() -> None:
    instance, selected_task, result, store, *_ = specialist_runtime()
    calls_before = instance.snapshot().charged_tool_calls
    _, later = instance.hold_capacity_slots(
        expected_snapshot_sequence=instance.snapshot().sequence,
        requests=(
            CapacitySlotRequest(
                permitted_operation=ModelOperation.COMMANDER_MODEL,
                allowed_actions=ModelAllowedActions.PLAN_ONLY,
                reserved_model_calls=1,
                reserved_tool_calls=0,
                minimum_token_floor=100,
                expires_at=END,
            ),
        ),
    )
    request = build_specialist_model_request(
        task=selected_task,
        dispatch_result=result,
        budget_snapshot=later,
        evidence_store=store,
    )
    assert request.budget_snapshot.sequence > result.budget_snapshot.sequence
    assert instance.snapshot().charged_tool_calls == calls_before


def test_specialist_builder_accepts_unrelated_active_model_lease() -> None:
    store = SpyEvidenceStore()
    instance, selected_task, result, _store, *_ = specialist_runtime(store=store)
    slots, _ = instance.hold_capacity_slots(
        expected_snapshot_sequence=instance.snapshot().sequence,
        requests=(
            CapacitySlotRequest(
                permitted_operation=ModelOperation.COMMANDER_MODEL,
                allowed_actions=ModelAllowedActions.PLAN_ONLY,
                reserved_model_calls=1,
                reserved_tool_calls=0,
                minimum_token_floor=100,
                expires_at=END,
            ),
        ),
    )
    _lease, unrelated_lease_snapshot = instance.expand_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        source_record_id=slots[0].slot_id,
        exact_input_tokens=50,
        minimum_completion_tokens=50,
        max_completion_tokens=50,
    )
    request = build_specialist_model_request(
        task=selected_task,
        dispatch_result=result,
        budget_snapshot=unrelated_lease_snapshot,
        evidence_store=store,
    )
    assert request.budget_snapshot.active_lease_ids == (_lease.lease_id,)
    assert store.resolved == list(result.tool_call_record.evidence_refs)


@pytest.mark.parametrize("successor", ("RESERVED", "CHARGED", "RETURNED"))
def test_specialist_builder_rejects_same_authorization_lease_successor_before_store_reads(
    successor: str,
) -> None:
    store = SpyEvidenceStore()
    instance, selected_task, result, _store, *_ = specialist_runtime(store=store)
    lease, successor_snapshot = instance.expand_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        source_record_id=result.specialist_authorization.authorization_id,
        exact_input_tokens=500,
        minimum_completion_tokens=220,
        max_completion_tokens=220,
    )
    if successor == "CHARGED":
        _charged, successor_snapshot = instance.charge_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.METRICS_AGENT,
            owner_node_id="node-metrics-001",
            source_record_id=result.specialist_authorization.authorization_id,
            input_tokens=500,
            output_tokens=220,
            total_tokens=720,
        )
    elif successor == "RETURNED":
        _returned, successor_snapshot = instance.return_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.METRICS_AGENT,
            owner_node_id="node-metrics-001",
            source_record_id=result.specialist_authorization.authorization_id,
        )
    with pytest.raises(EvidenceResolutionError) as captured:
        build_specialist_model_request(
            task=selected_task,
            dispatch_result=result,
            budget_snapshot=successor_snapshot,
            evidence_store=store,
        )
    assert captured.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH
    assert store.resolved == []


def test_specialist_builder_rejects_task_result_and_store_mismatch_before_reads() -> None:
    class SpyStore(NoEnumerationEvidenceStore):
        def __init__(self) -> None:
            super().__init__(RUN_ID)
            self.resolved: list[str] = []

        def resolve(self, reference: str):
            self.resolved.append(reference)
            return super().resolve(reference)

    store = SpyStore()
    instance, selected_task, result, _other_store, *_ = specialist_runtime(
        store=store
    )
    wrong = selected_task.model_copy(update={"node_id": "node-other"})
    with pytest.raises(EvidenceResolutionError) as captured:
        build_specialist_model_request(
            task=wrong,
            dispatch_result=result,
            budget_snapshot=instance.snapshot(),
            evidence_store=store,
        )
    assert captured.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH
    assert store.resolved == []


def test_specialist_builder_rejects_forged_tool_evidence_body() -> None:
    _instance, selected_task, result, store, *_rest, new_item = specialist_runtime()
    forged = new_item.model_copy(update={"summary": "Forged observation."})
    forged_record = result.tool_call_record.model_copy(update={"evidence": (forged,)})
    forged_result = result.model_copy(update={"tool_call_record": forged_record})
    with pytest.raises(EvidenceResolutionError) as captured:
        build_specialist_model_request(
            task=selected_task,
            dispatch_result=forged_result,
            budget_snapshot=result.budget_snapshot,
            evidence_store=store,
        )
    assert captured.value.code is EvidenceResolutionErrorCode.INVALID_INPUT


def test_finding_store_is_run_scoped_immutable_and_non_enumerable() -> None:
    store = FindingStore(RUN_ID)
    item = finding()
    assert store.add(item) == item
    assert store.resolve(item.finding_id) == item
    assert not hasattr(store, "snapshot")
    with pytest.raises(EvidenceResolutionError) as duplicate:
        store.add(item)
    assert duplicate.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH
    with pytest.raises(EvidenceResolutionError) as cross_run:
        store.add(finding(run_id=OTHER_RUN_ID))
    assert cross_run.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH


def test_judge_request_reconstructs_bodies_refs_and_exact_runtime_owned_id() -> None:
    evidence_store = NoEnumerationEvidenceStore(RUN_ID)
    first = add_metrics(evidence_store, observation_type="latency")
    second = add_metrics(evidence_store, observation_type="errors")
    finding_store = FindingStore(RUN_ID)
    stored = finding(first.evidence_ref, second.evidence_ref)
    finding_store.add(stored)
    instance, *_ = specialist_runtime()
    first_request = build_judge_request(
        judge_request_id="judge-request-runtime-001",
        run_id=RUN_ID,
        incident=incident(),
        admitted_graph=graph(),
        finding_ids=(stored.finding_id,),
        finding_store=finding_store,
        evidence_store=evidence_store,
        budget_snapshot=instance.snapshot(),
        refinement_round=0,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    second_request = build_judge_request(
        judge_request_id="judge-request-runtime-002",
        run_id=RUN_ID,
        incident=incident(),
        admitted_graph=graph(),
        finding_ids=(stored.finding_id,),
        finding_store=finding_store,
        evidence_store=evidence_store,
        budget_snapshot=instance.snapshot(),
        refinement_round=0,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    assert first_request.judge_request_id == "judge-request-runtime-001"
    assert second_request.judge_request_id == "judge-request-runtime-002"
    assert first_request.judge_request_id != second_request.judge_request_id
    assert first_request.findings == (stored,)
    assert first_request.available_evidence_refs == (
        first.evidence_ref,
        second.evidence_ref,
    )
    assert first_request.resolved_evidence_view.evidence == (first, second)


@pytest.mark.parametrize(
    ("updates", "code"),
    (
        (
            {"judge_request_id": "bad id with spaces"},
            EvidenceResolutionErrorCode.INVALID_INPUT,
        ),
        (
            {"finding_ids": ("finding-001", "finding-001")},
            EvidenceResolutionErrorCode.INVALID_INPUT,
        ),
        (
            {"finding_ids": ("finding-unknown",)},
            EvidenceResolutionErrorCode.STORE_MISMATCH,
        ),
        (
            {"refinement_round": 2},
            EvidenceResolutionErrorCode.INVALID_INPUT,
        ),
        (
            {"conditional_refinement_bundle_id": "bad bundle id"},
            EvidenceResolutionErrorCode.INVALID_INPUT,
        ),
    ),
)
def test_judge_builder_rejects_malformed_duplicate_and_unknown_identity(
    updates: dict[str, object],
    code: EvidenceResolutionErrorCode,
) -> None:
    evidence_store = NoEnumerationEvidenceStore(RUN_ID)
    item = add_metrics(evidence_store)
    finding_store = FindingStore(RUN_ID)
    finding_store.add(finding(item.evidence_ref))
    instance, *_ = specialist_runtime()
    kwargs: dict[str, object] = {
        "judge_request_id": "judge-request-runtime-001",
        "run_id": RUN_ID,
        "incident": incident(),
        "admitted_graph": graph(),
        "finding_ids": ("finding-001",),
        "finding_store": finding_store,
        "evidence_store": evidence_store,
        "budget_snapshot": instance.snapshot(),
        "refinement_round": 0,
        "allowed_actions": ModelAllowedActions.FINAL_ONLY,
        "conditional_refinement_bundle_id": None,
    }
    kwargs.update(updates)
    with pytest.raises(EvidenceResolutionError) as captured:
        build_judge_request(**kwargs)  # type: ignore[arg-type]
    assert captured.value.code is code


def test_judge_builder_multi_finding_union_is_first_seen_and_reordered_ids_fail() -> None:
    evidence_store = NoEnumerationEvidenceStore(RUN_ID)
    first = add_metrics(evidence_store, observation_type="first")
    second = add_metrics(evidence_store, observation_type="second")
    third = add_metrics(evidence_store, observation_type="third")
    initial = graph().initial_plan
    second_node = initial.nodes[0].model_copy(
        update={"node_id": "node-metrics-002", "priority": 2}
    )
    multi_plan = InvestigationPlan.model_validate(
        {
            **initial.model_dump(mode="python"),
            "nodes": (initial.nodes[0], second_node),
        }
    )
    multi_graph = build_initial_admitted_graph(multi_plan)
    finding_store = FindingStore(RUN_ID)
    first_finding = finding(
        second.evidence_ref,
        first.evidence_ref,
        finding_id="finding-001",
        node_id="node-metrics-001",
    )
    second_finding = finding(
        first.evidence_ref,
        third.evidence_ref,
        finding_id="finding-002",
        node_id="node-metrics-002",
    )
    finding_store.add(first_finding)
    finding_store.add(second_finding)
    instance, *_ = specialist_runtime()
    request = build_judge_request(
        judge_request_id="judge-request-runtime-001",
        run_id=RUN_ID,
        incident=incident(),
        admitted_graph=multi_graph,
        finding_ids=("finding-001", "finding-002"),
        finding_store=finding_store,
        evidence_store=evidence_store,
        budget_snapshot=instance.snapshot(),
        refinement_round=0,
        allowed_actions=ModelAllowedActions.FINAL_ONLY,
        conditional_refinement_bundle_id=None,
    )
    assert request.available_evidence_refs == (
        second.evidence_ref,
        first.evidence_ref,
        third.evidence_ref,
    )
    with pytest.raises(EvidenceResolutionError) as reordered:
        build_judge_request(
            judge_request_id="judge-request-runtime-002",
            run_id=RUN_ID,
            incident=incident(),
            admitted_graph=multi_graph,
            finding_ids=("finding-002", "finding-001"),
            finding_store=finding_store,
            evidence_store=evidence_store,
            budget_snapshot=instance.snapshot(),
            refinement_round=0,
            allowed_actions=ModelAllowedActions.FINAL_ONLY,
            conditional_refinement_bundle_id=None,
        )
    assert reordered.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH


def test_judge_builder_rejects_finding_body_outside_admitted_graph() -> None:
    evidence_store = NoEnumerationEvidenceStore(RUN_ID)
    item = add_metrics(evidence_store)
    finding_store = FindingStore(RUN_ID)
    finding_store.add(finding(item.evidence_ref, node_id="node-other"))
    instance, *_ = specialist_runtime()
    with pytest.raises(EvidenceResolutionError) as captured:
        build_judge_request(
            judge_request_id="judge-request-runtime-001",
            run_id=RUN_ID,
            incident=incident(),
            admitted_graph=graph(),
            finding_ids=("finding-001",),
            finding_store=finding_store,
            evidence_store=evidence_store,
            budget_snapshot=instance.snapshot(),
            refinement_round=0,
            allowed_actions=ModelAllowedActions.FINAL_ONLY,
            conditional_refinement_bundle_id=None,
        )
    assert captured.value.code is EvidenceResolutionErrorCode.STORE_MISMATCH


def test_admitted_graph_hash_fixture_is_stable_for_judge_reconstruction() -> None:
    payload = graph().model_dump(mode="json")
    supplied = payload.pop("graph_sha256")
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert supplied == hashlib.sha256(canonical).hexdigest()
