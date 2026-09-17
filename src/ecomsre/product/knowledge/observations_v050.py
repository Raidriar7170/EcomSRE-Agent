"""Bound supplemental reads to an incident and a deployable finite query."""

import json
from datetime import timedelta
from typing import Literal

from pydantic import Field

from ecomsre.product.investigation.contracts import StrictModel
from ecomsre.product.connectors.base import ConnectorQueryResultV1
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


class ResourceDependency(StrictModel):
    template: Literal["RESOURCE_USAGE_SAMPLES"] = "RESOURCE_USAGE_SAMPLES"
    window_offset_seconds: Literal[0, 30]
    query_window_seconds: Literal[30] = 30
    sampling_window_seconds: int = Field(ge=1, le=30)
    sample_count: int = Field(ge=2, le=10)


def dependency_for(incident, action, window):
    if action.source.value != "RESOURCES":
        return None
    return ResourceDependency(
        window_offset_seconds=int(
            (incident.diagnosis_observed_at - window.ended_at).total_seconds()
        ),
        sampling_window_seconds=action.request.sampling_window_seconds,
        sample_count=action.request.sample_count,
    ).model_dump(mode="json")


def ensure_table(store):
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS supplemental_observations_v050 (
            incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
            query_sha256 TEXT NOT NULL, object_sha256 TEXT NOT NULL,
            PRIMARY KEY(incident_id, query_sha256))""")


def projection(envelope, digest):
    from ecomsre.product.investigation.reads import project_record

    result = ConnectorQueryResultV1.model_validate_json(json.dumps(envelope["result"]))
    return dict(
        evidence_ref="investigation:" + digest,
        object_sha256=digest,
        source=result.source.value,
        targets=list(result.requested_services),
        window=result.window.model_dump(mode="json"),
        status=result.status.value,
        truncated=result.truncated,
        covered_services=list(result.covered_services),
        records=[project_record(r.model_dump(mode="json")) for r in result.records],
        cached=False,
        resource_dependency=envelope["resource_dependency"],
    )


def save_observation(
    *,
    incident,
    action,
    window,
    result,
    objects,
    capability_sha256,
    fence=None,
    parent_diagnosis_id=None,
):
    dependency = dependency_for(incident, action, window)
    if result.window != window or tuple(result.requested_services) != tuple(
        action.target_services
    ):
        raise ValueError("SUPPLEMENTAL_QUERY_RESULT_MISMATCH")
    envelope = dict(
        schema_version="ecomsre.product.supplemental-observation.v050",
        incident_id=incident.incident_id,
        incident_sha256=incident.incident_sha256,
        environment_id=incident.environment_id,
        parent_diagnosis_id=parent_diagnosis_id,
        capability_sha256=capability_sha256,
        action=action.model_dump(mode="json"),
        window=window.model_dump(mode="json"),
        resource_dependency=dependency,
        result=result.model_dump(mode="json"),
    )
    digest = objects.put_json(envelope).object_sha256
    ensure_table(objects.metadata_store)
    key = semantic_sha256_v22(
        {"request": action.request_sha256, "window": envelope["window"]}
    )
    with objects.metadata_store.connect() as c:
        from ecomsre.product.jobs.fencing import require_live_job_fence

        c.execute("BEGIN IMMEDIATE")
        require_live_job_fence(c, fence)
        c.execute(
            "INSERT OR IGNORE INTO supplemental_observations_v050 VALUES (?,?,?)",
            (incident.incident_id, key, digest),
        )
        saved = c.execute(
            "SELECT object_sha256 FROM supplemental_observations_v050 WHERE incident_id=? AND query_sha256=?",
            (incident.incident_id, key),
        ).fetchone()[0]
        c.execute("COMMIT")
    if saved != digest:
        raise ValueError("SUPPLEMENTAL_READ_ALREADY_COMMITTED")
    return projection(envelope, digest)


def load_observations(incident, objects):
    ensure_table(objects.metadata_store)
    with objects.metadata_store.connect() as c:
        rows = c.execute(
            "SELECT query_sha256,object_sha256 FROM supplemental_observations_v050 WHERE incident_id=?",
            (incident.incident_id,),
        ).fetchall()
    result = []
    for row in rows:
        envelope = json.loads(objects.read_bytes(row["object_sha256"]))
        if (
            envelope["incident_id"] != incident.incident_id
            or envelope["incident_sha256"] != incident.incident_sha256
            or envelope["environment_id"] != incident.environment_id
            or envelope["capability_sha256"] != incident.source_capability_sha256
        ):
            raise ValueError("SUPPLEMENTAL_EVENT_BINDING_MISMATCH")
        from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22

        action = EvidenceActionV22.model_validate_json(json.dumps(envelope["action"]))
        from ecomsre.product.connectors.base import ConnectorWindowV1

        window = ConnectorWindowV1.model_validate_json(json.dumps(envelope["window"]))
        typed = ConnectorQueryResultV1.model_validate_json(
            json.dumps(envelope["result"])
        )
        if (
            typed.window != window
            or typed.source != action.source
            or tuple(typed.requested_services) != tuple(action.target_services)
            or envelope["resource_dependency"]
            != dependency_for(incident, action, window)
            or row["query_sha256"]
            != semantic_sha256_v22(
                {"request": action.request_sha256, "window": envelope["window"]}
            )
        ):
            raise ValueError("SUPPLEMENTAL_QUERY_BINDING_MISMATCH")
        result.append(projection(envelope, row["object_sha256"]))
    return result


def select_dependency(dependency, *, incident_end, observations, target=None):
    expected_end = incident_end - timedelta(seconds=dependency.window_offset_seconds)
    expected_start = expected_end - timedelta(seconds=dependency.query_window_seconds)
    from ecomsre.product.connectors.base import ConnectorWindowV1

    window = ConnectorWindowV1(
        started_at=expected_start, ended_at=expected_end
    ).model_dump(mode="json")
    return [
        o
        for o in observations
        if o.get("resource_dependency") == dependency.model_dump(mode="json")
        and o["window"] == window
        and (target is None or o.get("targets") == [target])
    ]


def acquire_dependencies(candidates, reads):
    """Normal diagnosis/harness reads: no Provider and no old-event copying."""
    observations = load_observations(reads.incident, reads.objects)
    for candidate in candidates:
        dependency = candidate.proposal.resource_dependency
        if dependency is None:
            continue
        if select_dependency(
            dependency,
            incident_end=reads.incident.diagnosis_observed_at,
            observations=observations,
            target=candidate.proposal.target,
        ):
            continue
        for key, (action, window) in reads.entries.items():
            if action.target_services == (
                candidate.proposal.target,
            ) and dependency_for(
                reads.incident, action, window
            ) == dependency.model_dump(mode="json"):
                observations.append(reads.read(key))
                break
    return observations
