from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.diagnosis import filter_candidates_v22
from ecomsre.dta_v2.v22.memory import SignalStrengthV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    build_discovery_provider_request_v23,
    call_discovery_provider_v23,
)
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryReadOutcomeClassV23,
    NegativeCoverageLedgerV23,
    build_discovery_plan_v23,
    record_discovery_outcome_v23,
)
from ecomsre.dta_v2.v23.discovery_runtime import (
    assert_v23_artifact_is_non_actionable,
    run_development_leave_one_out_v23,
)
from ecomsre.dta_v2.v23.novelty_gate import NoveltyDispositionV23


ROOT = Path(__file__).resolve().parents[2]


def _cpu_demo_context():
    from ecomsre.dta_v2.v23.discovery_runtime import run_cpu_development_demo_v23

    return run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)


def _catalog():
    candidates = ("svc-a", "svc-b")
    return build_action_catalog_v22(
        candidate_services=candidates,
        topology=StaticTopologyV22.build(
            services=candidates,
            edges=(("svc-a", "svc-b"),),
        ),
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=3.0,
    )


def test_router_is_bounded_and_negative_coverage_blocks_equivalent_repeat() -> None:
    context = _cpu_demo_context()
    catalog = _catalog()
    ledger = NegativeCoverageLedgerV23.empty()

    plan = build_discovery_plan_v23(
        catalog=catalog,
        graph=context.residual_graph,
        negative_coverage=ledger,
        reads_used=0,
        remaining_weighted_budget=3.0,
        target_complete_resource_coverage=True,
    )

    assert plan is not None
    assert len(plan.ranked_actions) <= 3
    assert plan.selected_action == plan.ranked_actions[0]
    assert plan.selected_action.source is EvidenceSourceV22.RESOURCES
    assert plan.selected_action.target_services == catalog.candidate_services

    ledger = record_discovery_outcome_v23(
        ledger=ledger,
        action=plan.selected_action,
        outcome_class=DiscoveryReadOutcomeClassV23.NONEMPTY_NO_NEW_ANOMALY,
        new_anomaly_ids=(),
    )
    next_plan = build_discovery_plan_v23(
        catalog=catalog,
        graph=context.residual_graph,
        negative_coverage=ledger,
        reads_used=1,
        remaining_weighted_budget=3.0,
        target_complete_resource_coverage=True,
    )

    assert next_plan is not None
    assert all(
        not (
            item.source is EvidenceSourceV22.RESOURCES
            and item.target_services == catalog.candidate_services
        )
        for item in next_plan.ranked_actions
    )
    assert (
        build_discovery_plan_v23(
            catalog=catalog,
            graph=context.residual_graph,
            negative_coverage=ledger,
            reads_used=3,
            remaining_weighted_budget=3.0,
            target_complete_resource_coverage=True,
        )
        is None
    )


def test_source_failure_also_blocks_equivalent_repeat() -> None:
    context = _cpu_demo_context()
    catalog = _catalog()
    first = build_discovery_plan_v23(
        catalog=catalog,
        graph=context.residual_graph,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        reads_used=0,
        remaining_weighted_budget=3.0,
        target_complete_resource_coverage=False,
    )
    assert first is not None
    ledger = record_discovery_outcome_v23(
        ledger=NegativeCoverageLedgerV23.empty(),
        action=first.selected_action,
        outcome_class=DiscoveryReadOutcomeClassV23.SOURCE_FAILURE,
        new_anomaly_ids=(),
    )
    second = build_discovery_plan_v23(
        catalog=catalog,
        graph=context.residual_graph,
        negative_coverage=ledger,
        reads_used=1,
        remaining_weighted_budget=3.0,
        target_complete_resource_coverage=False,
    )
    assert second is not None
    assert second.selected_action != first.selected_action


def _valid_provider_payload() -> dict[str, object]:
    context = _cpu_demo_context()
    assert context.provisional_report is not None
    report = context.provisional_report
    return report.model_dump(
        mode="json",
        exclude={"schema_version", "report_id", "report_sha256"},
    )


def test_provider_protocol_repairs_are_bounded_to_two() -> None:
    context = _cpu_demo_context()
    request = build_discovery_provider_request_v23(
        active_ontology=context.active_ontology,
        graph=context.residual_graph,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        last_post_read_delta=None,
        top_shadow_matches=(),
    )
    bodies: list[str] = []

    def invalid_transport(body: str) -> str:
        bodies.append(body)
        return json.dumps({"terminal": "INVALID"})

    with pytest.raises(DiscoveryProviderProtocolFailureV23):
        call_discovery_provider_v23(
            request=request,
            memory=_cpu_demo_context_memory(),
            transport=invalid_transport,
        )

    assert len(bodies) == 3
    assert len(set(bodies)) == 3


def test_provider_transport_retries_exact_body_at_most_three_times() -> None:
    context = _cpu_demo_context()
    request = build_discovery_provider_request_v23(
        active_ontology=context.active_ontology,
        graph=context.residual_graph,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        last_post_read_delta=None,
        top_shadow_matches=(),
    )
    bodies: list[str] = []

    def flaky_transport(body: str) -> str:
        bodies.append(body)
        if len(bodies) <= 3:
            raise DiscoveryProviderTransportErrorV23("transient")
        return json.dumps(_valid_provider_payload())

    outcome = call_discovery_provider_v23(
        request=request,
        memory=_cpu_demo_context_memory(),
        transport=flaky_transport,
    )

    assert outcome.report.action_authority == "NONE"
    assert outcome.transport_retries == 3
    assert outcome.protocol_repairs == 0
    assert len(set(bodies)) == 1


def test_semantically_weak_but_valid_provider_report_is_not_retried() -> None:
    context = _cpu_demo_context()
    request = build_discovery_provider_request_v23(
        active_ontology=context.active_ontology,
        graph=context.residual_graph,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        last_post_read_delta=None,
        top_shadow_matches=(),
    )
    payload = _valid_provider_payload()
    payload["confidence"] = 0.01
    calls = 0

    def weak_transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(payload)

    outcome = call_discovery_provider_v23(
        request=request,
        memory=_cpu_demo_context_memory(),
        transport=weak_transport,
    )

    assert outcome.report.confidence == 0.01
    assert calls == 1


def _cpu_demo_context_memory():
    from ecomsre.dta_v2.v23.discovery_runtime import build_cpu_development_memory_v23

    _capture, memory = build_cpu_development_memory_v23(repository_root=ROOT)
    return memory


@pytest.mark.parametrize(
    ("case_id", "hidden"),
    (
        ("d01", MechanismV22.CONFIGURATION_ERROR),
        ("d02", MechanismV22.SERVICE_UNAVAILABLE),
        ("d03", MechanismV22.MEMORY_LEAK),
        ("d04", MechanismV22.CPU_SATURATION),
        ("d06", MechanismV22.DEPENDENCY_LATENCY),
    ),
)
def test_all_five_registered_mechanisms_run_leave_one_out(
    case_id: str,
    hidden: MechanismV22,
) -> None:
    result = run_development_leave_one_out_v23(
        repository_root=ROOT,
        case_id=case_id,
        hidden_mechanism=hidden,
    )

    assert result.hidden_mechanism is hidden
    assert hidden not in result.active_ontology.enabled_mechanisms
    assert result.discovery_reads_used <= 3
    assert result.final_disposition in {
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
        NoveltyDispositionV23.INSUFFICIENT_EVIDENCE,
        NoveltyDispositionV23.CONFLICTING_EVIDENCE,
    }
    assert result.agent_writes == 0
    assert result.runbook_executions == 0
    if result.provisional_report is not None:
        assert result.provisional_report.action_authority == "NONE"


def test_v23_provisional_type_is_not_an_action_lane_input() -> None:
    context = _cpu_demo_context()
    assert context.provisional_report is not None

    with pytest.raises(TypeError, match="non-actionable"):
        assert_v23_artifact_is_non_actionable(context.provisional_report)

    signature = inspect.signature(filter_candidates_v22)
    assert "ProvisionalIncidentReportV23" not in str(signature)
    assert all(
        annotation is not type(context.provisional_report)
        for annotation in filter_candidates_v22.__annotations__.values()
    )


def test_development_provider_payload_contains_generic_not_hidden_ontology() -> None:
    context = _cpu_demo_context()
    request = build_discovery_provider_request_v23(
        active_ontology=context.active_ontology,
        graph=context.residual_graph,
        negative_coverage=NegativeCoverageLedgerV23.empty(),
        last_post_read_delta=None,
        top_shadow_matches=(),
    )
    rendered_ontology = json.dumps(request.active_ontology, sort_keys=True).casefold()

    assert "cpu_saturation" not in rendered_ontology
    assert "cpu-saturation" not in rendered_ontology
    assert "cpu saturation" not in rendered_ontology
    assert any(
        item.strength is SignalStrengthV22.STRONG
        for item in context.residual_graph.generic_anomalies
    )
