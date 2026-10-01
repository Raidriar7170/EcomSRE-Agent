"""Zero-dispatch, SQLite read-only audit of retained, already-seen episodes."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.knowledge.repository import KnowledgeRepositoryV1
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
from ecomsre.product.knowledge.candidates_v050 import (
    snapshot_observations,
    candidate_components,
)
from ecomsre.product.knowledge.observations_v050 import load_observations
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / ".local/product-v050"


class ReadOnlyStore:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        try:
            yield c
        finally:
            c.close()


def material(data=DATA):
    store = ReadOnlyStore(data / "product.sqlite3")
    # Deliberately bypass the migration/table-creating constructors. All existing
    # readers still verify typed objects and CAS hashes against a read-only DB.
    objects = object.__new__(ContentAddressedObjectStoreV1)
    objects.root = data / "objects"
    objects.sha_root = objects.root / "sha256"
    objects.metadata_store = store
    investigations = object.__new__(InvestigationRepository)
    investigations.store, investigations.objects = store, objects
    knowledge = KnowledgeRepositoryV1(store, objects)
    evo = object.__new__(KnowledgeEvolutionV050)
    evo.store, evo.knowledge, evo.investigations = store, knowledge, investigations
    env = json.loads((data / "live-02/postgres-user-01/environment.json").read_text())[
        "environment_id"
    ]
    with store.connect() as c:
        split = json.loads(
            c.execute(
                "SELECT manifest_json FROM knowledge_split_v050 WHERE environment_id=?",
                (env,),
            ).fetchone()[0]
        )
        roster = [
            dict(r)
            for r in c.execute(
                "SELECT incident_id,episode_id FROM knowledge_episode_incidents_v050 WHERE environment_id=? ORDER BY episode_id",
                (env,),
            )
        ]
    # Historical audit stays on the original manifest; future successor events
    # must neither crash this reader nor enter the preserved five-event result.
    roster = [
        dict(r, role=split[r["episode_id"]])
        for r in roster
        if split.get(r["episode_id"]) in {"DISCOVERY", "DEVELOPMENT"}
    ]
    discovery = evo.discovery_view(
        env, [r["incident_id"] for r in roster], record_exposure=False
    )
    return evo, roster, discovery


def audit(evo, roster, discovery):
    rows = []
    for member in roster:
        iid = member["incident_id"]
        session = next(s for s in discovery["sessions"] if s["incident_id"] == iid)
        material = evo.knowledge._shadow_runtime_material(iid)
        evidence = evo.knowledge._evidence(
            iid, evo.knowledge._diagnosis(iid).diagnosis_id
        )
        observations = snapshot_observations(
            [e.payload for e in evidence.objects if "connector_result" in e.payload],
            material.runtime_input.memory,
        )
        observations += load_observations(material.incident, evo.investigations.objects)
        targets = []
        for target in material.incident.candidate_logical_services:
            components = candidate_components(
                SimpleNamespace(
                    proposal=SimpleNamespace(
                        target=target,
                        predicates=discovery["predicate_catalog"],
                        expression=None,
                    )
                ),
                memory=material.runtime_input.memory,
                anomalies=material.runtime_input.generic_anomalies,
                observations=observations,
                incident_end=material.incident.diagnosis_observed_at,
            )
            source_rows = []
            for o in session["observations"]:
                records = [r for r in o["records"] if r.get("service") == target]
                if (
                    target not in o.get("targets", o["covered_services"])
                    and not records
                ):
                    continue
                complete = (
                    o["status"] == "SUCCESS_NONEMPTY"
                    and not o["truncated"]
                    and target in o["covered_services"]
                    and bool(records)
                )
                source_rows.append(
                    dict(
                        evidence_ref=o["evidence_ref"],
                        source=o["source"],
                        window=o["window"],
                        status=o["status"],
                        truncated=o["truncated"],
                        covered=target in o["covered_services"],
                        target_record_count=len(records),
                        record_count=o.get("record_count", len(o["records"])),
                        fields=sorted({k for r in records for k in r}),
                        usable=complete,
                        acquisition="SUPPLEMENTAL"
                        if o["evidence_ref"].startswith("investigation:")
                        else "INITIAL",
                        exclusion_reason=None
                        if complete
                        else "INCOMPLETE_EMPTY_OR_NO_TARGET_RECORDS",
                    )
                )
            from ecomsre.product.knowledge.compiler import _predicate_parts

            for cell in components["predicates"]:
                source = _predicate_parts(cell["predicate"])[1].value
                related = [o for o in source_rows if o["source"] == source]
                cell["source"] = source
                cell["evidence_state"] = (
                    "PRESENT"
                    if cell["status"] == "TRUE"
                    else "CONCLUSIVE_ABSENCE_BY_EXISTING_EVALUATOR"
                    if cell["status"] == "FALSE"
                    else "SOURCE_FAILED"
                    if any(o["status"].startswith("FAILURE") for o in related)
                    else "UNKNOWN"
                )
            targets.append(
                dict(
                    target=target,
                    observations=source_rows,
                    predicates=components["predicates"],
                    dependencies=[
                        d
                        for d in session["deployable_resource_dependencies"]
                        if d["target"] == target
                    ],
                )
            )
        rows.append(dict(**member, session_status=session["status"], targets=targets))
    dev = [r for r in rows if r["role"] == "DEVELOPMENT"]
    shared = {}
    for row in rows:
        for target in row["targets"]:
            for d in target["dependencies"]:
                if d["availability"] == "BOUND_OBSERVATION":
                    key = json.dumps(
                        dict(target=target["target"], dependency=d["dependency"]),
                        sort_keys=True,
                    )
                    shared.setdefault(key, []).append(row["incident_id"])
    return dict(
        claim="INPUT_EXPRESSIBILITY_ONLY_NOT_MODEL_OUTPUT_OR_EFFECTIVENESS",
        provider_requests=0,
        live_episodes=0,
        rows=rows,
        shared_collected_dependencies=[
            dict(
                **json.loads(k),
                members=v,
                all_declared_development_members=all(
                    r["incident_id"] in v for r in dev
                ),
            )
            for k, v in shared.items()
        ],
        controls="ORIGINAL_FIVE_TARGET_EPISODES_ONLY; NO_CONFUSABLE_OR_HEALTHY_DEVELOPMENT_INCIDENTS",
        snapshot_sha256=discovery["snapshot_sha256"],
    )


if __name__ == "__main__":
    evo, roster, discovery = material()
    print(json.dumps(audit(evo, roster, discovery), indent=2, sort_keys=True))
