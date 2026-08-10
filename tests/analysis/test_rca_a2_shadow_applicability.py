from __future__ import annotations

from dataclasses import fields, replace
import importlib.util
import json
from pathlib import Path

import pytest

from ecomsre_rca_unified.a2_shadow import (
    A2Action,
    A2RootProvenance,
    A2ShadowInput,
    A2ShadowMode,
    ApplicabilityGateId,
    evaluate_a2_shadow,
    execute_a2_shadow_case,
)
from ecomsre_rca_unified.applicability import (
    ApplicabilityCase,
    evaluate_applicability_frontier,
    evaluate_production_case,
    evaluate_reference_case,
)
from ecomsre_rca_unified.contracts import (
    ArchitectureOption,
    CanonicalEntityLayer,
    EntityHierarchyPath,
    EvidenceVisibilitySummary,
    FaultOntologyClass,
    FrontierCase,
    PropagationDisposition,
)
from ecomsre_rca_unified.frontier import load_frontier


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTIER_PATH = (
    PROJECT_ROOT / "config/rca-crossbenchmark-architecture-convergence-v1/frontier.json"
)
POLICY_PATH = PROJECT_ROOT / "config/rca-a2-shadow-applicability-v1/applicability.json"
SCRIPT_PATH = PROJECT_ROOT / "scripts/analysis/rca_a2_shadow_applicability.py"
_SPEC = importlib.util.spec_from_file_location(
    "rca_a2_shadow_applicability", SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SCRIPT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SCRIPT)
load_applicability_policy = _SCRIPT.load_applicability_policy
read_design_prefix_objects = _SCRIPT.read_design_prefix_objects
scan_public_payload = _SCRIPT.scan_public_payload


def _visibility(
    *,
    metrics: frozenset[str] = frozenset({"service|metrics"}),
    logs: frozenset[str] = frozenset(),
    traces: frozenset[str] = frozenset(),
    events: frozenset[str] = frozenset(),
    alerts: frozenset[str] = frozenset(),
    topology: frozenset[str] = frozenset({"service|metrics"}),
) -> EvidenceVisibilitySummary:
    return EvidenceVisibilitySummary(
        catalog_entities=frozenset({"service|initial", "service|metrics"}),
        metrics_entities=metrics,
        logs_entities=logs,
        traces_entities=traces,
        events_entities=events,
        alerts_entities=alerts,
        topology_entities=topology,
    )


def _input(**updates: object) -> A2ShadowInput:
    values: dict[str, object] = {
        "initial_entity": "service|initial",
        "initial_layer": CanonicalEntityLayer.SERVICE,
        "initial_hierarchy_path": EntityHierarchyPath(
            entity="service|initial",
            explicit_parents=(),
            service_ancestor_or_none="service|initial",
            infrastructure_ancestor_or_none=None,
        ),
        "initial_metrics_rank_or_none": 4,
        "metrics_top1_entity": "service|metrics",
        "metrics_top1_layer": CanonicalEntityLayer.SERVICE,
        "metrics_top1_service_ancestor": "service|metrics",
        "metrics_margin": 0.25,
        "metrics_top1_is_downstream": False,
        "propagation_disposition": PropagationDisposition.ABSENT,
        "evidence_visibility": _visibility(),
        "fault_type_raw": "cpu exhaustion",
        "fault_ontology_class": FaultOntologyClass.LOCAL_RESOURCE,
        "supporting_evidence_refs": ("metric:cpu",),
    }
    values.update(updates)
    return A2ShadowInput(**values)  # type: ignore[arg-type]


def _frontier_case(**updates: object) -> FrontierCase:
    values: dict[str, object] = {
        "private_case_key": "private-1",
        "benchmark": "RCA100",
        "system": "RCA100",
        "fault_family": "cpu",
        "fault_regime": FaultOntologyClass.LOCAL_RESOURCE,
        "metric_family": "CPU",
        "ground_truth_entity": "service|metrics",
        "ground_truth_equivalent_entities": frozenset({"service|metrics"}),
        "ground_truth_service": "service|metrics",
        "initial_entity": "service|initial",
        "initial_service": "service|initial",
        "initial_fault_type": "cpu exhaustion",
        "initial_pair_correct": False,
        "initial_layer": CanonicalEntityLayer.SERVICE,
        "metrics_top1": "service|metrics",
        "metrics_top1_service": "service|metrics",
        "metrics_top1_layer": CanonicalEntityLayer.SERVICE,
        "metrics_initial_rank": 4,
        "metrics_margin": 0.25,
        "metrics_top1_is_downstream": False,
        "propagation_disposition": PropagationDisposition.ABSENT,
        "causal_candidates": (),
        "initial_fault_correct": True,
    }
    values.update(updates)
    return FrontierCase(**values)  # type: ignore[arg-type]


def _evaluation_case(
    *,
    fixture: str = "RCA100",
    runtime_input: A2ShadowInput | None = None,
    **updates: object,
) -> ApplicabilityCase:
    frontier_case = _frontier_case(**updates)
    return ApplicabilityCase(
        fixture=fixture,
        frontier_case=frontier_case,
        runtime_input=_input() if runtime_input is None else runtime_input,
    )


@pytest.mark.parametrize("rank", (None, 3, 6))
def test_g0_reuses_frozen_a2_rank_and_margin_boundary(rank: int | None) -> None:
    decision = evaluate_a2_shadow(
        _input(initial_metrics_rank_or_none=rank, metrics_margin=0.25),
        ApplicabilityGateId.G0_A2_REFERENCE,
        A2ShadowMode.SHADOW,
    )
    assert decision.base_rule_passed is True
    assert decision.applicability_gate_passed is True
    assert decision.action is A2Action.WOULD_OVERRIDE


@pytest.mark.parametrize(
    "updates",
    (
        {"initial_metrics_rank_or_none": 2},
        {"metrics_margin": 0.249999},
        {"metrics_top1_entity": "service|initial"},
        {"metrics_top1_is_downstream": True},
        {"metrics_top1_layer": CanonicalEntityLayer.NODE},
    ),
)
def test_g0_fails_every_frozen_a2_guard(updates: dict[str, object]) -> None:
    decision = evaluate_a2_shadow(
        _input(**updates),
        ApplicabilityGateId.G0_A2_REFERENCE,
        A2ShadowMode.SHADOW,
    )
    assert decision.base_rule_passed is False
    assert decision.applicability_gate_passed is False
    assert decision.action is A2Action.WOULD_KEEP


def test_g1_requires_same_exact_non_operation_known_layer() -> None:
    assert evaluate_a2_shadow(
        _input(), ApplicabilityGateId.G1_EXACT_LAYER_A2, A2ShadowMode.SHADOW
    ).applicability_gate_passed
    assert not evaluate_a2_shadow(
        _input(metrics_top1_layer=CanonicalEntityLayer.POD),
        ApplicabilityGateId.G1_EXACT_LAYER_A2,
        A2ShadowMode.SHADOW,
    ).applicability_gate_passed
    for layer in (CanonicalEntityLayer.OPERATION, CanonicalEntityLayer.UNKNOWN):
        assert not evaluate_a2_shadow(
            _input(initial_layer=layer, metrics_top1_layer=layer),
            ApplicabilityGateId.G1_EXACT_LAYER_A2,
            A2ShadowMode.SHADOW,
        ).applicability_gate_passed


def test_g2_accepts_only_root_eligible_layers() -> None:
    assert evaluate_a2_shadow(
        _input(
            initial_layer=CanonicalEntityLayer.NODE,
            metrics_top1_layer=CanonicalEntityLayer.INFRASTRUCTURE,
        ),
        ApplicabilityGateId.G2_ROOT_ELIGIBLE_LAYER_A2,
        A2ShadowMode.SHADOW,
    ).applicability_gate_passed
    for excluded in (
        CanonicalEntityLayer.OPERATION,
        CanonicalEntityLayer.POD,
        CanonicalEntityLayer.CONTAINER,
        CanonicalEntityLayer.UNKNOWN,
    ):
        assert not evaluate_a2_shadow(
            _input(initial_layer=excluded, metrics_top1_layer=excluded),
            ApplicabilityGateId.G2_ROOT_ELIGIBLE_LAYER_A2,
            A2ShadowMode.SHADOW,
        ).applicability_gate_passed


@pytest.mark.parametrize("source", ("logs", "traces", "events", "alerts"))
def test_g3_requires_exact_non_metrics_source_support(source: str) -> None:
    visibility = _visibility(**{source: frozenset({"service|metrics"})})
    decision = evaluate_a2_shadow(
        _input(evidence_visibility=visibility),
        ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2,
        A2ShadowMode.SHADOW,
    )
    assert decision.applicability_gate_passed is True
    assert decision.non_metrics_support_sources == (source.upper(),)


def test_g3_rejects_topology_only_and_non_exact_support() -> None:
    topology_only = evaluate_a2_shadow(
        _input(evidence_visibility=_visibility()),
        ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2,
        A2ShadowMode.SHADOW,
    )
    wrong_entity = evaluate_a2_shadow(
        _input(
            evidence_visibility=_visibility(
                logs=frozenset({"service|other"}),
                topology=frozenset(),
            )
        ),
        ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2,
        A2ShadowMode.SHADOW,
    )
    assert topology_only.applicability_gate_passed is False
    assert wrong_entity.applicability_gate_passed is False


def test_g4_requires_both_g1_and_g3() -> None:
    supported = _input(
        evidence_visibility=_visibility(traces=frozenset({"service|metrics"}))
    )
    assert evaluate_a2_shadow(
        supported,
        ApplicabilityGateId.G4_EXACT_LAYER_CROSS_SOURCE_A2,
        A2ShadowMode.SHADOW,
    ).applicability_gate_passed
    assert not evaluate_a2_shadow(
        replace(supported, metrics_top1_layer=CanonicalEntityLayer.POD),
        ApplicabilityGateId.G4_EXACT_LAYER_CROSS_SOURCE_A2,
        A2ShadowMode.SHADOW,
    ).applicability_gate_passed
    assert not evaluate_a2_shadow(
        _input(),
        ApplicabilityGateId.G4_EXACT_LAYER_CROSS_SOURCE_A2,
        A2ShadowMode.SHADOW,
    ).applicability_gate_passed


def test_shadow_never_changes_authoritative_root_or_fault_type() -> None:
    decision = evaluate_a2_shadow(
        _input(),
        ApplicabilityGateId.G0_A2_REFERENCE,
        A2ShadowMode.SHADOW,
    )
    assert decision.shadow_final_entity == "service|metrics"
    assert decision.authoritative_final_entity == "service|initial"
    assert decision.action is A2Action.WOULD_OVERRIDE
    assert decision.root_provenance is A2RootProvenance.HIERARCHY_GUARDED_METRICS_SHADOW
    assert decision.fault_type_raw == "cpu exhaustion"


def test_active_replays_the_identical_shadow_outcome_and_falls_back_to_a0() -> None:
    shadow = evaluate_a2_shadow(
        _input(),
        ApplicabilityGateId.G0_A2_REFERENCE,
        A2ShadowMode.SHADOW,
    )
    active = evaluate_a2_shadow(
        _input(),
        ApplicabilityGateId.G0_A2_REFERENCE,
        A2ShadowMode.ACTIVE,
    )
    fallback = evaluate_a2_shadow(
        _input(evidence_visibility=_visibility()),
        ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2,
        A2ShadowMode.ACTIVE,
    )
    assert active.authoritative_final_entity == shadow.shadow_final_entity
    assert active.action is A2Action.OVERRIDE_METRICS_TOP1
    assert (
        active.root_provenance is A2RootProvenance.CONDITIONAL_HIERARCHY_GUARDED_METRICS
    )
    assert fallback.authoritative_final_entity == "service|initial"
    assert fallback.action is A2Action.KEEP_INITIAL
    assert fallback.root_provenance is A2RootProvenance.MODEL_INITIAL


def test_shadow_case_uses_one_strong_single_call_and_no_other_model_calls() -> None:
    calls = 0

    def diagnose(_: object) -> A2ShadowInput:
        nonlocal calls
        calls += 1
        return _input()

    result = execute_a2_shadow_case(
        object(),
        diagnose,
        gates=(
            ApplicabilityGateId.G0_A2_REFERENCE,
            ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2,
        ),
    )
    assert calls == 1
    assert result.model_calls == 1
    assert result.specialist_calls == 0
    assert result.fusion_calls == 0
    assert result.a0_authoritative.final_root == "service|initial"
    assert len(result.shadow_decisions) == 2


def test_runtime_contract_has_no_benchmark_identity_or_ground_truth() -> None:
    contract_fields = {item.name for item in fields(A2ShadowInput)}
    forbidden = {
        "benchmark",
        "system",
        "source_task_id",
        "ground_truth",
        "case_score",
        "fault_label",
    }
    assert contract_fields.isdisjoint(forbidden)
    source = (
        (PROJECT_ROOT / "src/ecomsre_rca_unified/a2_shadow.py")
        .read_text(encoding="utf-8")
        .casefold()
    )
    assert "if benchmark" not in source
    assert "if system" not in source
    assert "source_task_id" not in source
    assert "ground_truth" not in source


@pytest.mark.parametrize("gate", tuple(ApplicabilityGateId))
def test_reference_and_production_gate_outcomes_match(
    gate: ApplicabilityGateId,
) -> None:
    case = _evaluation_case(
        runtime_input=_input(
            evidence_visibility=_visibility(traces=frozenset({"service|metrics"}))
        )
    )
    frontier = load_frontier(FRONTIER_PATH)
    reference = evaluate_reference_case(case, gate, frontier)
    production = evaluate_production_case(case, gate, frontier)
    assert production == reference
    assert production.option in {ArchitectureOption.A0, ArchitectureOption.A2}


def test_gate_result_is_independent_of_benchmark_and_system_identity() -> None:
    frontier = load_frontier(FRONTIER_PATH)
    first = _evaluation_case()
    renamed = _evaluation_case(benchmark="RENAMED", system="OTHER")
    for gate in ApplicabilityGateId:
        assert evaluate_reference_case(first, gate, frontier).final_entity == (
            evaluate_reference_case(renamed, gate, frontier).final_entity
        )
        assert evaluate_production_case(first, gate, frontier).final_entity == (
            evaluate_production_case(renamed, gate, frontier).final_entity
        )


def test_applicability_frontier_rejects_every_gate_when_safety_and_retention_split() -> (
    None
):
    rca_damage = _evaluation_case(
        private_case_key="rca-damage",
        ground_truth_entity="service|initial",
        ground_truth_equivalent_entities=frozenset({"service|initial"}),
        ground_truth_service="service|initial",
        initial_entity="service|initial",
        initial_service="service|initial",
        initial_pair_correct=True,
    )
    fixtures = tuple(
        _evaluation_case(
            fixture=fixture,
            private_case_key=fixture,
            benchmark="OBSS",
            system="RE2-OB" if fixture != "candidate-5" else "RE2-SS",
        )
        for fixture in ("candidate-3", "candidate-4", "candidate-5")
    )
    result = evaluate_applicability_frontier(
        (rca_damage, *fixtures), load_frontier(FRONTIER_PATH)
    )
    assert result.selected_gate is None
    assert not result.evaluations[ApplicabilityGateId.G0_A2_REFERENCE].accepted
    assert not result.evaluations[
        ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2
    ].accepted
    assert (
        result.evaluations[
            ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2
        ].obss_net_retained_fraction
        == 0.0
    )


def test_selection_priority_prefers_supported_g3_over_more_complex_g4() -> None:
    supported = _visibility(traces=frozenset({"service|metrics"}))
    rca_damage_without_support = _evaluation_case(
        private_case_key="rca-damage",
        ground_truth_entity="service|initial",
        ground_truth_equivalent_entities=frozenset({"service|initial"}),
        ground_truth_service="service|initial",
        initial_entity="service|initial",
        initial_service="service|initial",
        initial_pair_correct=True,
    )
    rca_rescue_with_support = _evaluation_case(
        private_case_key="rca-rescue",
        runtime_input=_input(evidence_visibility=supported),
    )
    fixtures = tuple(
        _evaluation_case(
            fixture=fixture,
            private_case_key=fixture,
            benchmark="OBSS",
            system="RE2-OB" if fixture != "candidate-5" else "RE2-SS",
            runtime_input=_input(evidence_visibility=supported),
        )
        for fixture in ("candidate-3", "candidate-4", "candidate-5")
    )
    result = evaluate_applicability_frontier(
        (rca_damage_without_support, rca_rescue_with_support, *fixtures),
        load_frontier(FRONTIER_PATH),
    )
    assert result.selected_gate is ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2
    assert result.evaluations[ApplicabilityGateId.G3_CROSS_SOURCE_SUPPORTED_A2].accepted
    assert result.evaluations[
        ApplicabilityGateId.G4_EXACT_LAYER_CROSS_SOURCE_A2
    ].accepted


def test_frozen_policy_contains_exactly_g0_through_g4_and_goal_thresholds() -> None:
    policy = load_applicability_policy(POLICY_PATH)
    assert tuple(item["id"] for item in policy["gates"]) == tuple(
        gate.value for gate in ApplicabilityGateId
    )
    assert policy["a2_reference"]["minimum_normalized_margin"] == 0.25
    assert policy["acceptance"]["rca100"]["maximum_root_damage_rate"] == 0.1
    assert (
        policy["acceptance"]["obss_aggregate"]["minimum_g0_net_retained_fraction"]
        == 0.5
    )
    assert policy["no_gate_verdict"] == ("A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0")


def test_design_prefix_reader_stops_before_later_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    rows = (
        {"fixture": "RCA100", "value": 1},
        {"fixture": "candidate-3", "value": 2},
        {"fixture": "candidate-4", "value": 3},
        {"fixture": "candidate-5", "value": 4},
    )
    text = "".join(json.dumps(item) + "\n" for item in rows)
    path.write_text(text + "THIS_LATER_OUTCOME_MUST_NOT_BE_PARSED\n", encoding="utf-8")
    loaded = read_design_prefix_objects(
        path,
        (
            ("RCA100", 1),
            ("candidate-3", 1),
            ("candidate-4", 1),
            ("candidate-5", 1),
        ),
    )
    assert loaded == rows


def test_design_prefix_reader_fails_closed_on_order_drift(tmp_path: Path) -> None:
    path = tmp_path / "wrong.jsonl"
    path.write_text(json.dumps({"fixture": "candidate-3"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fixture order"):
        read_design_prefix_objects(path, (("RCA100", 1),))


@pytest.mark.parametrize(
    "payload",
    (
        {"private_case_key": "secret"},
        {"note": "/Users/raidriar/.ecomsre-private/secret"},
        {"run_id": "run-secret"},
        {"entity": "service|checkout"},
    ),
)
def test_public_scanner_rejects_case_and_private_material(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="public leakage"):
        scan_public_payload(payload)


def test_analysis_entrypoint_has_no_provider_or_live_execution_import() -> None:
    source = (
        PROJECT_ROOT / "scripts/analysis/rca_a2_shadow_applicability.py"
    ).read_text(encoding="utf-8")
    assert "OpenAICompatibleRCA100Provider" not in source
    assert "run_v2_development" not in source
    assert "execute_schedule" not in source
