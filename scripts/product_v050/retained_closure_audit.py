"""Read-only N4–N7 audit; no governance transitions, registry or live adapters."""

import hashlib
import json
from pathlib import Path

from scripts.product_v050.knowledge_feasibility import DATA, material
from ecomsre.product.knowledge.candidates_v050 import (
    CompiledKnowledge,
    candidate_components,
    evaluate_candidate,
    snapshot_observations,
)
from ecomsre.product.knowledge.observations_v050 import load_observations
from ecomsre.product.incidents.diagnosis_bridge import (
    ProductDiagnosisBridgeV1,
    _effective_admissions_v024,
    build_active_ontology_view_v23,
    build_known_admission_state_v23,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22

REGISTRATION = "registration-ae3902fb32fd96d87608cb93"


def audit(data=DATA):
    db = data / "product.sqlite3"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    evo, _, _ = material(data)
    with evo.store.connect() as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM knowledge_candidate_pool_v050 WHERE registration_id=?",
                (REGISTRATION,),
            ).fetchone()
        )
    candidate = CompiledKnowledge.model_validate_json(row["payload_json"])
    frozen = json.loads(row["freeze_json"])
    product = Path(__file__).resolve().parents[2] / "src/ecomsre/product"
    pure_paths = (
        "knowledge/candidates_v050.py",
        "incidents/diagnosis_bridge.py",
        "knowledge/expressions.py",
        "incidents/anomaly_policy.py",
    )
    pure_bindings = {
        p: hashlib.sha256((product / p).read_bytes()).hexdigest()
        == frozen["evaluator_and_protocol_sha256"][p]
        for p in pure_paths
    }
    assert all(pure_bindings.values())
    result = {
        "frozen_pure_code_bindings_equal": pure_bindings,
        "diagnostic_only": True,
        "registration_state": row["state"],
        "compiled_sha256": candidate.compiled_sha256,
        "episodes": {},
    }
    cases = {}
    for slot in ("N4", "N5", "N6", "N7"):
        episode = json.loads(
            (data / f"live-final-closure-02/episodes/{slot}/result.json").read_text()
        )
        iid = episode["incident_id"]
        m = evo.knowledge._shadow_runtime_material(iid)
        d = evo.knowledge._diagnosis(iid)
        ev = evo.knowledge._evidence(iid, d.diagnosis_id)
        by_action = {
            o.action_id: o for o in ev.objects if "connector_result" in o.payload
        }
        snapshots = [o.payload for o in by_action.values()]
        assert all(p["incident_id"] == iid for p in snapshots)
        obs = snapshot_observations(
            snapshots, m.runtime_input.memory
        ) + load_observations(m.incident, evo.investigations.objects)
        kw = dict(
            memory=m.runtime_input.memory,
            anomalies=m.runtime_input.generic_anomalies,
            observations=obs,
            incident_end=m.incident.diagnosis_observed_at,
        )
        sources = []
        for action, obj in sorted(by_action.items()):
            p = obj.payload
            r = p["connector_result"]
            sources.append(
                dict(
                    action_id=action,
                    object_sha256=obj.object_sha256,
                    source=r["source"],
                    window=r["window"],
                    status=r["status"],
                    truncated=r["truncated"],
                    covered_services=r["covered_services"],
                    records=r["records"],
                )
            )
        item = dict(
            incident_id=iid,
            episode_start=episode["start"],
            activation=episode["activated"],
            restoration=episode["restored"],
            episode_end=episode["end"],
            incident=m.incident.model_dump(mode="json"),
            normal_diagnosis=d.model_dump(mode="json"),
            sources=sources,
            pure_candidate=evaluate_candidate(
                candidate, target=candidate.proposal.target, **kw
            ).model_dump(mode="json"),
            components=candidate_components(candidate, **kw),
        )
        result["episodes"][slot] = item
        if slot != "N7":
            cases[iid] = {
                "N4": "POSITIVE_INCIDENT",
                "N5": "NO_INCIDENT",
                "N6": "CONFUSABLE_CORE_KNOWN",
            }[slot]
            continue
        admission = build_known_admission_state_v23(
            view=build_active_ontology_view_v23(
                candidate_services=m.runtime_input.candidate_services
            ),
            memory=m.runtime_input.memory,
            topology_edges=tuple(
                (e.parent_service, e.child_service) for e in m.baseline.topology_edges
            ),
            evidence_source_unavailable=any(
                o.status.value not in ("SUCCESS_EMPTY", "SUCCESS_NONEMPTY")
                for o in m.raw_outcomes
            ),
        )
        item["core_admissions"] = [
            x.model_dump(mode="json")
            for x in _effective_admissions_v024(
                admission=admission,
                memory=m.runtime_input.memory,
                anomalies=m.runtime_input.generic_anomalies,
            )
        ]
        support = []
        for ref in m.runtime_input.memory.evidence_refs:
            if ref.evidence_ref not in d.supporting_evidence_refs:
                continue
            obj = by_action[ref.action_id]
            payload = obj.payload
            assert payload["memory_outcome"]["outcome_sha256"] == ref.outcome_sha256
            record = payload["memory_outcome"]["records"][ref.record_index]
            assert semantic_sha256_v22(record) == ref.record_sha256
            support.append(
                dict(
                    ref=ref.model_dump(mode="json"),
                    record=record,
                    query_window=payload["connector_result"]["window"],
                    object_sha256=obj.object_sha256,
                    predicates=[
                        p.model_dump(mode="json")
                        for p in m.runtime_input.memory.predicates
                        if ref.evidence_ref in p.evidence_refs
                    ],
                )
            )
        item["core_support"] = support

        class Spy:
            calls = 0

            def match(self, **kwargs):
                self.calls += 1
                raise AssertionError("Extension matcher unexpectedly executed")

        spy = Spy()
        coverage = {}
        for p in snapshots:
            r = p["connector_result"]
            source = EvidenceSourceV22(r["source"])
            coverage[source] = tuple(
                sorted(set(coverage.get(source, ())) | set(r["covered_services"]))
            )
        replay, _, trace = ProductDiagnosisBridgeV1(spy).diagnose(
            incident=m.incident,
            baseline=m.baseline,
            identity_map=ServiceCatalogRepositoryV1(evo.store).get_map(
                m.incident.environment_id
            ),
            acquisition=ProductReadAcquisitionV1(
                m.raw_outcomes,
                m.memory_outcomes,
                tuple(snapshots),
                coverage,
                d.capability_limitations,
                (),
                (),
            ),
            diagnosis_id=d.diagnosis_id,
            created_at=d.created_at,
        )
        assert (
            replay.terminal,
            replay.mechanism,
            replay.root_service_ids,
            replay.supporting_evidence_refs,
            replay.memory_sha256,
        ) == (
            d.terminal,
            d.mechanism,
            d.root_service_ids,
            d.supporting_evidence_refs,
            d.memory_sha256,
        )
        item["bridge_replay"] = dict(
            extension_matcher_calls=spy.calls,
            semantic_diagnosis_equal=True,
            full_diagnosis_equal=replay == d,
            trace=trace.model_dump(mode="json"),
            disposition="NOT_EXECUTED_CORE_ADMITTED",
        )
    result["new_protocol_diagnostic_qualifications"] = evo.control_qualifications(
        candidate, cases
    )
    deployment = json.loads(
        (data / "live-final-closure-07/deployment.json").read_text()
    )
    result["actual_metric_queries"] = deployment["actual_queries"]
    result["N6_change_audits"] = {}
    for phase in ("activation", "restoration"):
        path = (
            data / f"live-final-closure-02/episodes/N6/{phase}-configuration-audit.json"
        )
        raw = path.read_bytes()
        change = json.loads(raw)
        result["N6_change_audits"][phase] = {
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "started_at": change["started_at"],
            "response": change["response"],
        }
    support_change = next(
        x["record"]
        for x in result["episodes"]["N7"]["core_support"]
        if x["ref"]["source"] == "CHANGES"
    )
    assert (
        support_change
        == result["N6_change_audits"]["restoration"]["response"]["body"]["v22_record"]
    )
    result["N7_core_change_exactly_N6_restoration"] = True
    result["database_sha256_before"] = before
    result["database_sha256_after"] = hashlib.sha256(db.read_bytes()).hexdigest()
    assert result["database_sha256_after"] == before
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "database_unchanged": True,
                "N7_pure": report["episodes"]["N7"]["pure_candidate"],
                "N7_bridge": report["episodes"]["N7"]["bridge_replay"]["disposition"],
                "qualifications": {
                    k: v["qualified"]
                    for k, v in report["new_protocol_diagnostic_qualifications"].items()
                },
            }
        )
    )
