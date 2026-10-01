"""Real evaluator over fixture inputs; these variants are not live episodes."""

import json

from fastapi.testclient import TestClient

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.knowledge.candidates_v050 import (
    CompiledKnowledge,
    KnowledgeProposal,
    snapshot_observations,
    evaluate_candidate,
)
from ecomsre.product.knowledge.shadow_controls_v050 import (
    derived_controls,
    source_failure_inputs,
    target_evidence_exclusion,
)
from test_investigation import prepare


def test_derived_source_failure_rebuilds_memory_and_keeps_parent_unchanged(tmp_path):
    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        iid = prepare(client, settings, services=("checkout", "payment"))
        knowledge = client.app.state.knowledge
        material = knowledge._shadow_runtime_material(iid)
        evidence = knowledge._evidence(iid, knowledge._diagnosis(iid).diagnosis_id)
        snapshots = [
            o.payload for o in evidence.objects if "connector_result" in o.payload
        ]
        observations = snapshot_observations(snapshots, material.runtime_input.memory)
        resource = next(
            o
            for o in observations
            if o["source"] == "RESOURCES" and "payment" in o["covered_services"]
        )
        proposal = KnowledgeProposal(
            name="fixture-only-derived-controls",
            kind="PATTERN_ONLY",
            target="payment",
            broad_domain="RESOURCE",
            member_incidents=["fixture-a", "fixture-b"],
            predicates=["core:RUNTIME_HEALTHY"],
            expression=dict(
                numerator=dict(field="cpu_percent", operator="mean"),
                comparator="gt",
                threshold=1.0,
                threshold_unit="PERCENT",
                threshold_provenance="fixture",
                window_seconds=resource["records"][0]["sampling_window_seconds"],
                minimum_samples=2,
            ),
            supporting_refs=[resource["evidence_ref"]],
            counter_evidence_refs=[],
            confusable_patterns=["healthy"],
            prediction="fixture only",
            inapplicable_conditions=["missing source"],
        )
        payload = dict(
            schema_version="ecomsre.product.compiled-knowledge.v050",
            registration_id="fixture-controls",
            environment_id=material.incident.environment_id,
            capability_sha256=material.incident.source_capability_sha256,
            origin="FIXTURE_ONLY",
            source_request_key=None,
            discovery_snapshot_sha256="0" * 64,
            discovery_incident_ids=("fixture-a", "fixture-b"),
            proposal=proposal.model_dump(mode="json"),
            action_authority="NONE",
        )
        candidate = CompiledKnowledge.model_validate(
            payload | {"compiled_sha256": sha(payload)}
        )
        before = sha(
            dict(
                runtime=material.runtime_input.model_dump(mode="json"),
                snapshots=snapshots,
                observations=observations,
            )
        )
        positive = evaluate_candidate(
            candidate,
            target="payment",
            memory=material.runtime_input.memory,
            anomalies=material.runtime_input.generic_anomalies,
            observations=observations,
            incident_end=material.incident.diagnosis_observed_at,
        )
        assert positive.status == "TRUE"
        outcomes, details = derived_controls(
            candidate, material, snapshots, observations
        )
        assert len(outcomes) == 3
        counter_runtime, counter_observations = target_evidence_exclusion(
            material, snapshots, observations, "payment"
        )
        assert counter_observations
        assert counter_runtime.memory.evidence_refs
        assert any(
            r.get("service") == "checkout"
            for o in counter_observations
            for r in o["records"]
        )
        assert all(p.service != "payment" for p in counter_runtime.memory.predicates)
        assert any(
            o["source"] == "RESOURCES" and o["records"] for o in counter_observations
        )
        assert all("payment" not in o["covered_services"] for o in counter_observations)
        assert all(o.incident_id == iid and not o.matched for o in outcomes)
        assert all(o.evaluated_target_services == ("payment",) for o in outcomes)
        assert all(d["raw_result"]["reason"] != "TARGET_MISMATCH" for d in details)
        assert {o.origin.value for o in outcomes} == {
            "DERIVED_COUNTERFACTUAL",
            "DERIVED_SOURCE_FAILURE",
        }
        assert [d["raw_result"]["status"] for d in details] == [
            "UNKNOWN",
            "UNKNOWN",
            "UNKNOWN",
        ]
        assert all(d["lineage"]["parent_incident_id"] == iid for d in details)
        for source in candidate.proposal.required_sources:
            runtime, changed = source_failure_inputs(
                material, snapshots, observations, source
            )
            assert all(r.source.value != source for r in runtime.memory.evidence_refs)
            refs = {r.evidence_ref for r in runtime.memory.evidence_refs}
            assert all(set(p.evidence_refs) <= refs for p in runtime.memory.predicates)
            assert all(set(a.evidence_refs) <= refs for a in runtime.generic_anomalies)
            assert all(
                not o["records"]
                and not o["covered_services"]
                and o["status"] == "FAILURE_UNAVAILABLE"
                for o in changed
                if o["source"] == source
            )
            coverage = next(
                c for c in runtime.source_coverage if c.source.value == source
            )
            assert not coverage.reachable
            assert (
                runtime.runtime_input_sha256
                != material.runtime_input.runtime_input_sha256
            )
        assert (
            sha(
                dict(
                    runtime=material.runtime_input.model_dump(mode="json"),
                    snapshots=snapshots,
                    observations=observations,
                )
            )
            == before
        )
        assert (
            json.loads(knowledge._incident(iid).model_dump_json())["incident_id"] == iid
        )
