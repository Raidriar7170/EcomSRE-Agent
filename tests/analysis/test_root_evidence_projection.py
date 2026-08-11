from __future__ import annotations

import ast
from pathlib import Path

from ecomsre_rca_unified.compact_index_serialization import (
    B0_SYSTEM_PROMPT,
    build_full_request,
    compact_rows,
    contract_hashes,
    load_frozen_encoding,
    offline_full_request_tokens,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.root_candidate_index import build_candidate_index
from ecomsre_rca_unified.root_evidence_projection import (
    AliasDisposition,
    CanonicalTopology,
    EvidenceObservation,
    ProjectedEntity,
    ProjectionCase,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _topology() -> CanonicalTopology:
    return CanonicalTopology(
        {
            "entities": [
                {
                    "id": "svc",
                    "type": "apm.service",
                    "name": " Checkout ",
                    "props": {"service": "checkout"},
                },
                {"id": "svc-alias", "type": "k8s.service", "name": "checkout"},
                {"id": "work", "type": "k8s.deployment", "name": "checkout-work"},
                {"id": "pod", "type": "k8s.pod", "name": "checkout-pod"},
                {
                    "id": "container",
                    "type": "k8s.container",
                    "name": "checkout-container",
                },
                {"id": "node", "type": "k8s.node", "name": "node-a"},
                {"id": "dup1", "type": "k8s.pod", "name": "duplicate"},
                {"id": "dup2", "type": "k8s.pod", "name": "duplicate"},
                {"id": "db", "type": "apm.database", "name": "orders-db"},
            ],
            "edges": [
                {"src": "svc", "dst": "svc-alias", "relation": "same_as"},
                {"src": "svc", "dst": "work", "relation": "contains"},
                {"src": "work", "dst": "pod", "relation": "contains"},
                {"src": "pod", "dst": "container", "relation": "contains"},
                {"src": "node", "dst": "pod", "relation": "hosts"},
                {"src": "work", "dst": "db", "relation": "depends_on"},
                {"src": "node", "dst": "db", "relation": "future_relation"},
            ],
        }
    )


def test_exact_alias_resolution_and_ancestor_closure() -> None:
    topology = _topology()
    service, exact = topology.resolve(entity_id="svc-alias")
    assert exact == "EXACT_ID"
    assert service == "apm|apm.service|svc"
    assert topology.resolve(entity_type="k8s.pod", entity_name=" checkout-pod ") == (
        "k8s|k8s.pod|pod",
        "UNIQUE_TYPE_NAME",
    )
    assert topology.resolve(entity_type="k8s.pod", entity_name="duplicate") == (
        None,
        "AMBIGUOUS",
    )
    assert topology.resolve(service="CHECKOUT") == (
        "apm|apm.service|svc",
        "EXACT_SERVICE",
    )
    assert topology.resolve(entity_name="checkout-podd") == (None, "UNRESOLVED")
    paths = dict(topology.ancestors("k8s|k8s.container|container", maximum_depth=4))
    assert paths["k8s|k8s.pod|pod"] == (
        "k8s|k8s.container|container",
        "k8s|k8s.pod|pod",
    )
    assert paths["apm|apm.service|svc"][-1] == "apm|apm.service|svc"
    assert len(paths["apm|apm.service|svc"]) - 1 == 3
    assert topology.ignored_relation_counts == {"future_relation": 1}
    assert any(
        item.disposition == "EXPLICIT_SAME_AS" for item in topology.alias_dispositions
    )


def _projection(service_count: int = 5) -> ProjectionCase:
    services = tuple(
        ProjectedEntity(
            entity_ref=f"apm|apm.service|svc-{index}",
            entity_id=f"svc-{index}",
            entity_type="apm.service",
            display_name=f"svc-{index}",
            normalized_name=f"svc-{index}",
            layer=CanonicalEntityLayer.SERVICE,
        )
        for index in range(service_count)
    )
    workload = ProjectedEntity(
        entity_ref="k8s|k8s.deployment|work",
        entity_id="work",
        entity_type="k8s.deployment",
        display_name="work",
        normalized_name="work",
        layer=CanonicalEntityLayer.WORKLOAD,
    )
    pod = ProjectedEntity(
        entity_ref="k8s|k8s.pod|pod",
        entity_id="pod",
        entity_type="k8s.pod",
        display_name="pod",
        normalized_name="pod",
        layer=CanonicalEntityLayer.POD,
    )
    nodes = tuple(
        ProjectedEntity(
            entity_ref=f"k8s|k8s.node|node-{index}",
            entity_id=f"node-{index}",
            entity_type="k8s.node",
            display_name=f"node-{index}",
            normalized_name=f"node-{index}",
            layer=CanonicalEntityLayer.NODE,
        )
        for index in range(3)
    )
    dependencies = tuple(
        ProjectedEntity(
            entity_ref=f"apm|apm.database|db-{index}",
            entity_id=f"db-{index}",
            entity_type="apm.database",
            display_name=f"db-{index}",
            normalized_name=f"db-{index}",
            layer=CanonicalEntityLayer.DATABASE,
        )
        for index in range(3)
    )
    observations = tuple(
        EvidenceObservation(
            entity_ref=item.entity_ref,
            source="METRICS" if index % 2 == 0 else "LOGS",
            occurrences=10 - index,
            first_anomaly_time=100.0 + index,
            evidence_refs=(
                f"metric:{index + 1:04d}",
                f"log:{index + 1:04d}",
                f"trace:{index + 1:04d}",
            ),
            resolution="EXACT_SERVICE",
        )
        for index, item in enumerate(services)
    ) + (
        EvidenceObservation(
            entity_ref=pod.entity_ref,
            source="TRACES",
            occurrences=2,
            first_anomaly_time=99.0,
            evidence_refs=("trace:0099",),
            resolution="EXACT_ID",
        ),
    )
    entities = (*services, workload, pod, *nodes, *dependencies)
    return ProjectionCase(
        entities=entities,
        alias_dispositions=tuple(
            AliasDisposition(item.entity_ref, item.entity_ref, "EXACT_ID")
            for item in entities
        ),
        parent_edges=(
            (pod.entity_ref, workload.entity_ref),
            (workload.entity_ref, services[0].entity_ref),
        ),
        directed_edges=(
            (dependencies[0].entity_ref, services[0].entity_ref),
            (dependencies[1].entity_ref, dependencies[0].entity_ref),
        ),
        undirected_edges=(
            (pod.entity_ref, nodes[0].entity_ref),
            (nodes[0].entity_ref, nodes[1].entity_ref),
            (services[1].entity_ref, dependencies[2].entity_ref),
        ),
        observations=observations,
        ancestor_provenance=(),
        metrics_ranking=tuple(item.entity_ref for item in services[:6]),
        alert_entities=(pod.entity_ref,),
        ignored_relation_counts={},
        base_context={
            "schema_version": "strong-single-live.base-context.v1",
            "alert_title": "Synthetic alert",
            "prompt_text": "Investigate.",
            "alert_entity_ref": None,
            "entities": [],
            "evidence": [
                {
                    "evidence_ref": "metric:0001",
                    "entity_ref": services[0].entity_ref,
                    "summary": "one shared summary",
                }
            ],
            "source_status": {
                "METRICS": "AVAILABLE",
                "LOGS": "AVAILABLE",
                "TRACES": "AVAILABLE",
            },
        },
    )


def test_candidate_universe_mandatory_allocation_and_stable_ids() -> None:
    projection = _projection(service_count=8)
    first = build_candidate_index(projection)
    second = build_candidate_index(projection)
    assert first.payload() == second.payload()
    assert len(first.candidates) == 12
    assert tuple(item.candidate_id for item in first.candidates) == tuple(
        f"C{index:02d}" for index in range(1, 13)
    )
    refs = {item.universe.entity_ref for item in first.candidates}
    assert "apm|apm.database|db-0" in {item.entity_ref for item in first.universe}
    assert "apm|apm.database|db-1" in {item.entity_ref for item in first.universe}
    assert "k8s|k8s.node|node-1" in {item.entity_ref for item in first.universe}
    assert "apm|apm.service|svc-0" in refs
    assert any(
        "ALERT_NEAREST_ROOT" in item.universe.mandatory_reasons
        for item in first.candidates
    )
    assert len(first.mapping) == len(first.candidates)


def test_generic_service_completeness_selects_every_exact_service_when_bounded() -> (
    None
):
    index = build_candidate_index(_projection(service_count=5))
    selected = {item.universe.entity_ref for item in index.candidates}
    expected = {f"apm|apm.service|svc-{item}" for item in range(5)}
    assert expected <= selected
    assert all(
        "EXACT_SOURCE_SERVICE_COMPLETENESS" in item.universe.mandatory_reasons
        for item in index.candidates
        if item.universe.entity_ref in expected
    )


def test_shared_evidence_compact_rows_and_real_tokenizer() -> None:
    projection = _projection(service_count=5)
    index = build_candidate_index(projection)
    rows = compact_rows(index)
    assert rows
    assert all(row.count("ref=") == 1 for row in rows)
    assert all(len(row.rsplit("ref=", 1)[1].split(",")) <= 2 for row in rows)
    assert all(len(row.split("|why=", 1)[1].split("|", 1)[0]) <= 2 for row in rows)
    request = build_full_request(base_context=projection.base_context, index=index)
    encoded = str(request)
    assert "one shared summary" in encoded
    assert encoded.count("one shared summary") == 1
    assert "CompactRootSelection" in encoded
    assert "You are selecting the causal root" in encoded
    encoding = load_frozen_encoding(PROJECT_ROOT)
    first = offline_full_request_tokens(encoding, request)
    second = offline_full_request_tokens(encoding, request)
    assert first == second
    assert first > 0
    assert contract_hashes()["b0_system_prompt_sha256"] == (
        "6b64c9e43f25029ca2f76f491faf98906c70fe888270284bf4bd3ff47e564049"
    )
    assert "rca100.initial-diagnosis" not in B0_SYSTEM_PROMPT


def test_runtime_sources_have_no_provider_evaluator_or_benchmark_routing() -> None:
    runtime_paths = (
        PROJECT_ROOT / "src/ecomsre_rca_unified/root_candidate_index.py",
        PROJECT_ROOT / "src/ecomsre_rca_unified/compact_index_serialization.py",
    )
    for path in runtime_paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any("provider" in value or "evaluator" in value for value in imports)
        assert "RE2-OB" not in source
        assert "RE2-SS" not in source
        assert "ground_truth" not in source.casefold()
    projection_source = (
        PROJECT_ROOT / "src/ecomsre_rca_unified/root_evidence_projection.py"
    ).read_text(encoding="utf-8")
    projection_imports = {
        node.module or ""
        for node in ast.walk(ast.parse(projection_source))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        "provider" in value or "evaluator" in value for value in projection_imports
    )
    cli_source = (PROJECT_ROOT / "scripts/rca_projection/cli.py").read_text(
        encoding="utf-8"
    )
    assert "from ecomsre_rca100.evaluator" in cli_source
    assert cli_source.index("def score") < cli_source.index(
        "from ecomsre_rca100.evaluator"
    )
