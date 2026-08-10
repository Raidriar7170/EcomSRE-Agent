from __future__ import annotations

import json
from pathlib import Path

from ecomsre_rca_unified.analysis import (
    UnifiedMetricCandidate,
    UnifiedRCACase,
    classify_fault_phrase_relation,
    classify_m3_failure,
    classify_strong_single_failure,
    evidence_sufficiency,
    fault_phrase_relation,
    rate,
)
from ecomsre_rca_unified.adapters import (
    classify_fault_ontology,
    classify_propagation_role,
    load_rca_schedule,
    metric_family,
    read_rca_topology,
)
from ecomsre_rca_unified.propagation import EvidenceGraph
from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    EvidenceVisibilitySummary,
    EntityHierarchyPath,
    FaultOntologyClass,
    PropagationDisposition,
    RootProvenance,
)
from ecomsre_rca_unified.runtime import (
    StrongSingleHierarchicalInput,
    execute_unified_hierarchical_rca,
)
from ecomsre_rca100.prompt import OpenAICompatibleRCA100Provider


def _case(**updates: object) -> UnifiedRCACase:
    values: dict[str, object] = {
        "private_case_key": "private-fixture-1",
        "fixture": "RCA100",
        "benchmark": "RCA100",
        "system": "RCA100",
        "fault_family": "F001",
        "fault_type_truth": "memory pressure",
        "fault_type_raw": "Memory Pressure",
        "fault_regime": FaultOntologyClass.LOCAL_RESOURCE,
        "ground_truth_fault_regime": FaultOntologyClass.LOCAL_RESOURCE,
        "metric_family": "MEMORY",
        "ground_truth_entity": "service|truth",
        "ground_truth_equivalent_entities": frozenset({"service|truth"}),
        "ground_truth_layer": CanonicalEntityLayer.SERVICE,
        "ground_truth_service": "service|truth",
        "ground_truth_workload": None,
        "ground_truth_node": None,
        "initial_entity": "service|symptom",
        "initial_layer": CanonicalEntityLayer.SERVICE,
        "initial_hierarchy_path": EntityHierarchyPath(
            entity="service|symptom",
            explicit_parents=(),
            service_ancestor_or_none="service|symptom",
            infrastructure_ancestor_or_none=None,
        ),
        "initial_supporting_evidence_refs": ("metric:0001",),
        "initial_service": "service|symptom",
        "initial_correct_exact": False,
        "initial_correct_service": False,
        "initial_pair_correct": False,
        "initial_relation": "CONNECTED_DOWNSTREAM",
        "m3_action": "OVERRIDE_METRICS_TOP1",
        "m3_final_entity": "service|symptom",
        "m3_final_layer": CanonicalEntityLayer.SERVICE,
        "m3_final_service": "service|symptom",
        "m3_correct_exact": False,
        "m3_correct_service": False,
        "m3_pair_correct": False,
        "m3_relation": "CONNECTED_DOWNSTREAM",
        "metrics_candidates": (
            UnifiedMetricCandidate(
                entity="service|symptom",
                service_ancestor="service|symptom",
                layer=CanonicalEntityLayer.SERVICE,
                rank=1,
                score=4.0,
                metric_family="MEMORY",
                first_anomaly_time=20.0,
                source_support=1,
                relation_to_symptom="ROOT",
            ),
            UnifiedMetricCandidate(
                entity="service|truth",
                service_ancestor="service|truth",
                layer=CanonicalEntityLayer.SERVICE,
                rank=2,
                score=3.0,
                metric_family="MEMORY",
                first_anomaly_time=10.0,
                source_support=2,
                relation_to_symptom="UPSTREAM",
            ),
        ),
        "metrics_initial_rank": None,
        "metrics_margin": 0.25,
        "metrics_top1_is_downstream": True,
        "propagation_disposition": PropagationDisposition.PRESENT,
        "visibility": EvidenceVisibilitySummary(
            catalog_entities=frozenset({"service|truth", "service|symptom"}),
            metrics_entities=frozenset({"service|truth", "service|symptom"}),
            logs_entities=frozenset({"service|truth"}),
            traces_entities=frozenset(),
            events_entities=frozenset(),
            alerts_entities=frozenset({"service|symptom"}),
            topology_entities=frozenset({"service|truth", "service|symptom"}),
        ),
        "causal_visible_entities": frozenset({"service|truth"}),
        "alert_entity": "service|symptom",
        "terminal_failure": False,
    }
    values.update(updates)
    return UnifiedRCACase(**values)  # type: ignore[arg-type]


def test_rca_schedule_loader_accepts_the_frozen_json_list_shape(tmp_path: Path) -> None:
    records = [
        {
            "schema_version": "rca100.schedule-record.v1",
            "position": index,
            "source_task_id": f"t{index:03d}",
            "opaque_case_id": f"rca100-case-{index:04d}",
            "run_id": f"{index:032x}",
        }
        for index in range(1, 104)
    ]
    path = tmp_path / "schedule.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "rca100.private-schedule.v1",
                "seed": 20260810,
                "records": records,
            }
        ),
        encoding="utf-8",
    )
    assert len(load_rca_schedule(path).records) == 103


def test_strong_single_and_m3_failure_taxonomies_are_deterministic() -> None:
    case = _case()
    assert classify_strong_single_failure(case) == "DOWNSTREAM_SYMPTOM_SELECTED"
    assert classify_m3_failure(case) == "DOWNSTREAM_SYMPTOM_OVERRIDE"
    assert evidence_sufficiency(case) == "CROSS_SOURCE_SUFFICIENT"


def test_terminal_and_visibility_failures_take_priority() -> None:
    assert classify_strong_single_failure(_case(terminal_failure=True)) == "TERMINAL_FAILURE"
    invisible = _case(
        visibility=EvidenceVisibilitySummary(
            catalog_entities=frozenset({"service|truth", "service|symptom"}),
            metrics_entities=frozenset(),
            logs_entities=frozenset(),
            traces_entities=frozenset(),
            events_entities=frozenset(),
            alerts_entities=frozenset(),
            topology_entities=frozenset({"service|truth"}),
        ),
        causal_visible_entities=frozenset(),
        metrics_candidates=(
            UnifiedMetricCandidate(
                entity="service|symptom",
                service_ancestor="service|symptom",
                layer=CanonicalEntityLayer.SERVICE,
                rank=1,
                score=4.0,
                metric_family="MEMORY",
                first_anomaly_time=20.0,
                source_support=1,
                relation_to_symptom="ROOT",
            ),
        ),
    )
    assert classify_strong_single_failure(invisible) == "ROOT_NOT_IN_MODEL_VISIBLE_CONTEXT"
    assert evidence_sufficiency(invisible) == "ROOT_NOT_VISIBLE"


def test_fault_phrase_audit_and_rate_contract() -> None:
    assert fault_phrase_relation(" Memory Pressure ", "memory pressure") == "EXACT_NORMALIZED"
    assert fault_phrase_relation("memory-pressure", "memory pressure") == "CASING_OR_SEPARATOR"
    assert fault_phrase_relation("heap exhausted", "memory exhausted") == "TOKEN_OVERLAP"
    assert fault_phrase_relation("socket error", "cpu full load") == "COMPLETELY_DIFFERENT"
    assert rate(2, 5) == {"numerator": 2, "denominator": 5, "value": 0.4}


def test_goal_taxonomy_branches_are_deterministic() -> None:
    synonym = _case(
        fault_type_raw="oom",
        fault_type_truth="out of memory",
        fault_regime=FaultOntologyClass.LOCAL_RESOURCE,
        ground_truth_fault_regime=FaultOntologyClass.LOCAL_RESOURCE,
    )
    assert (
        classify_fault_phrase_relation(synonym)
        == "SYNONYM_OR_HIERARCHY_MISMATCH"
    )

    prompt_mismatch = _case(
        initial_entity="service|outside-prompt-catalog",
        initial_hierarchy_path=EntityHierarchyPath(
            entity="service|outside-prompt-catalog",
            explicit_parents=(),
            service_ancestor_or_none=None,
            infrastructure_ancestor_or_none=None,
        ),
    )
    assert (
        classify_strong_single_failure(prompt_mismatch)
        == "PROMPT_ENTITY_TASK_MISMATCH"
    )

    regime_mismatch = _case(
        initial_relation="UNRELATED",
        fault_regime=FaultOntologyClass.NETWORK,
        ground_truth_fault_regime=FaultOntologyClass.LOCAL_RESOURCE,
    )
    assert (
        classify_strong_single_failure(regime_mismatch)
        == "FAULT_REGIME_MISMATCH"
    )

    projection_error = _case(
        m3_final_entity="service|not-metrics-top1",
        m3_relation="UNRELATED",
    )
    assert classify_m3_failure(projection_error) == "RANKING_PROJECTION_ERROR"


def test_frontier_projection_preserves_native_fault_and_top6() -> None:
    projected = _case().to_frontier_case()
    assert projected.initial_fault_type == "Memory Pressure"
    assert projected.metrics_top1 == "service|symptom"
    assert projected.causal_candidates[1].metrics_rank == 2
    assert projected.propagation_disposition is PropagationDisposition.PRESENT


def test_fault_and_metric_ontologies_use_only_frozen_markers() -> None:
    assert classify_fault_ontology("pod CPU full load") is FaultOntologyClass.LOCAL_RESOURCE
    assert classify_fault_ontology("connection loss") is FaultOntologyClass.NETWORK
    assert classify_fault_ontology("downstream cache timeout") is FaultOntologyClass.DEPENDENCY
    assert metric_family("container_memory_usage") == "MEMORY"
    assert metric_family("rpc latency") == "NETWORK"


def test_runtime_fault_regime_is_separate_from_truth_and_earliest_is_temporal() -> None:
    case = _case(
        fault_regime=FaultOntologyClass.UNKNOWN,
        ground_truth_fault_regime=FaultOntologyClass.LOCAL_RESOURCE,
    )
    assert case.to_frontier_case().fault_regime is FaultOntologyClass.UNKNOWN
    graph = EvidenceGraph(nodes=frozenset({"root", "other"}), directed_edges=())
    assert (
        classify_propagation_role(
            "root",
            "root",
            alert_entity=None,
            first_times={"root": {"metrics": 20.0}, "other": {"metrics": 10.0}},
            graph=graph,
        )
        == "NO_TEMPORAL_SIGNAL"
    )
    assert (
        classify_propagation_role(
            "root",
            "root",
            alert_entity=None,
            first_times={"root": {"metrics": 10.0}, "other": {"metrics": 20.0}},
            graph=graph,
        )
        == "ROOT_EARLIEST_ANOMALY"
    )


def test_rca_topology_adapter_preserves_edge_authority_without_provider(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        OpenAICompatibleRCA100Provider,
        "__init__",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Provider constructor forbidden")
        ),
    )
    topology = tmp_path / "topology.json"
    topology.write_text(
        """{
          "entities": [
            {"id":"svc","type":"apm.service","name":"svc","props":{}},
            {"id":"op","type":"apm.operation","name":"op","props":{}},
            {"id":"dep","type":"apm.service","name":"dep","props":{}}
          ],
          "edges": [
            {"src":"svc","dst":"op","relation":"contains"},
            {"src":"svc","dst":"dep","relation":"calls"},
            {"src":"op","dst":"dep","relation":"hosts"}
          ]
        }""",
        encoding="utf-8",
    )
    adapted = read_rca_topology(topology)
    assert adapted.parent_edges == (("apm|apm.operation|op", "apm|apm.service|svc"),)
    assert adapted.directed_edges == (("apm|apm.service|dep", "apm|apm.service|svc"),)
    assert adapted.undirected_edges == (("apm|apm.operation|op", "apm|apm.service|dep"),)


def test_selected_a0_runtime_preserves_typed_initial_without_override() -> None:
    case = _case()
    runtime_input = StrongSingleHierarchicalInput(
        initial_root=case.initial_entity,
        initial_layer=case.initial_layer,
        initial_hierarchy_path=case.initial_hierarchy_path,
        fault_type_raw=case.fault_type_raw,
        fault_ontology_class=case.fault_regime,
        evidence_visibility=case.visibility,
        supporting_evidence_refs=case.initial_supporting_evidence_refs,
    )
    result = execute_unified_hierarchical_rca(runtime_input)
    assert result.evaluation_version == "unified-hierarchical-rca-v1"
    assert result.initial_root == case.initial_entity
    assert result.final_root == case.initial_entity
    assert result.initial_layer is CanonicalEntityLayer.SERVICE
    assert result.final_layer is CanonicalEntityLayer.SERVICE
    assert result.root_provenance is RootProvenance.MODEL_INITIAL
    assert result.fault_type_raw == "Memory Pressure"
    assert result.fault_ontology_class is FaultOntologyClass.LOCAL_RESOURCE
    assert result.decision_reason == "STRONG_SINGLE_HIERARCHICAL_KEEP_INITIAL"
    assert result.supporting_evidence_refs == ("metric:0001",)


def test_selected_a0_runtime_has_no_benchmark_identity_route() -> None:
    case = _case()
    runtime_input = StrongSingleHierarchicalInput(
        initial_root=case.initial_entity,
        initial_layer=case.initial_layer,
        initial_hierarchy_path=case.initial_hierarchy_path,
        fault_type_raw=case.fault_type_raw,
        fault_ontology_class=case.fault_regime,
        evidence_visibility=case.visibility,
        supporting_evidence_refs=case.initial_supporting_evidence_refs,
    )
    left = execute_unified_hierarchical_rca(runtime_input)
    right = execute_unified_hierarchical_rca(runtime_input)
    assert left == right
