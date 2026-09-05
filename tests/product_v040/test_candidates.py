"""Offline derived Product inputs, real CAS persistence, no live adapter."""

from datetime import timedelta
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.baselines import EnvironmentBaselineV1, BaselineBuildPolicyV1
from ecomsre.product.contracts import ServiceIdentityMapV1, ServiceIdentityV1
from ecomsre.product.environment.capabilities import (
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityV1,
)
from ecomsre.product.incidents.contracts import (
    IncidentRecordV1,
    EvidenceObjectV1,
    EvidenceBundleV1,
    DiagnosisResultV1,
)
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.evidence_binding_v0232 import DiagnosisEvidenceIndexV0232
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.remediation.candidate_filter import project_candidate
from ecomsre.product.remediation.contracts import (
    RemediationRegistryV1,
    RemediationRunbookV1,
)
from ecomsre.product.storage.object_store import (
    ContentAddressedObjectStoreV1,
    ObjectStoreIntegrityError,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from tests.dta_v22.test_v22_memory_predicates_diagnosis import (
    NOW,
    _baseline,
    _incident_outcomes,
)
from ecomsre.dta_v2.v22.gap_router_v222 import SOURCE_PREDICATE_CAPABILITIES_V222
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1


def sealed(model, field, **payload):
    draft = model.model_construct(**{**payload, field: "0" * 64})
    exclude = {field, "active"} if model is EnvironmentBaselineV1 else {field}
    return model.model_validate(
        {
            **payload,
            field: semantic_sha256_v22(draft.model_dump(mode="json", exclude=exclude)),
        }
    )


@pytest.fixture
def material(tmp_path):
    env, svc = "env-" + "1" * 24, "svc-" + "2" * 24
    identity = ServiceIdentityMapV1.build(
        environment_id=env,
        services=(ServiceIdentityV1(service_id=svc, logical_service="payment"),),
    )
    capability = sealed(
        EnvironmentCapabilityMatrixV1,
        "capability_sha256",
        environment_id=env,
        logical_services=("payment",),
        sources=tuple(
            SourceCapabilityV1(
                source=source,
                status="AVAILABLE",
                connector_names=("fixture",),
                covered_services=("payment",),
                target_complete_coverage=True,
                observable_predicates=tuple(
                    sorted(
                        SOURCE_PREDICATE_CAPABILITIES_V222[source],
                        key=lambda item: item.value,
                    )
                ),
            )
            for source in sorted(EvidenceSourceV22, key=lambda item: item.value)
        ),
        mechanisms=(),
        no_incident_eligible=True,
        effective_policy_sha256="4" * 64,
        verified_at=NOW,
    )
    baseline = sealed(
        EnvironmentBaselineV1,
        "baseline_sha256",
        baseline_id="base-" + "3" * 24,
        environment_id=env,
        service_ids=(svc,),
        source_capability_sha256=capability.capability_sha256,
        v22_baseline_profile=_baseline(),
        topology_edges=(),
        normal_log_templates=(),
        build_policy=BaselineBuildPolicyV1(),
        window_count=6,
        successful_windows=6,
        built_at=NOW - timedelta(hours=1),
        active=True,
    )
    incident = sealed(
        IncidentRecordV1,
        "incident_sha256",
        environment_id=env,
        incident_id="inc-" + "5" * 24,
        external_incident_key="synthetic-config",
        alert_name="synthetic-config",
        summary="Synthetic deterministic Product configuration observation",
        started_at=NOW - timedelta(minutes=5),
        candidate_service_ids=(svc,),
        baseline_id=baseline.baseline_id,
        baseline_sha256=baseline.baseline_sha256,
        service_identity_sha256=identity.identity_sha256,
        source_capability_sha256=capability.capability_sha256,
        candidate_logical_services=("payment",),
        diagnosis_observed_at=NOW,
        created_at=NOW,
    )
    outcomes = tuple(
        sorted(
            (
                item
                for item in _incident_outcomes()
                if item.source in {EvidenceSourceV22.LOGS, EvidenceSourceV22.CHANGES}
            ),
            key=lambda item: item.action_id,
        )
    )
    snapshots = tuple(
        {
            "action": {"action_id": item.action_id, "source": item.source.value},
            "read_outcome": item.model_dump(mode="json"),
            "memory_outcome": item.model_dump(mode="json"),
            "connector_result": ConnectorQueryResultV1.build(
                source=item.source,
                status=item.status,
                requested_services=("payment",),
                covered_services=("payment",),
                window=ConnectorWindowV1(
                    started_at=NOW - timedelta(minutes=5), ended_at=NOW
                ),
                records=item.records,
                truncated=False,
                safe_error_code=None,
                latency_ms=1.0,
            ).model_dump(mode="json"),
        }
        for item in outcomes
    )
    acquisition = ProductReadAcquisitionV1(
        raw_outcomes=outcomes,
        memory_outcomes=outcomes,
        snapshots=snapshots,
        covered_services_by_source={item.source: ("payment",) for item in outcomes},
        capability_limitations=(),
        capability_observations_v0232=(),
        capability_limitation_candidates_v0232=(),
    )
    diagnosis, observations, trace = ProductDiagnosisBridgeV1().diagnose(
        incident=incident,
        baseline=baseline,
        identity_map=identity,
        acquisition=acquisition,
        diagnosis_id="diag-" + "6" * 24,
        created_at=NOW,
    )
    assert diagnosis.terminal.value == "CORE_KNOWN"
    assert diagnosis.mechanism == "CONFIGURATION_ERROR"
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    objects = ContentAddressedObjectStoreV1(tmp_path / "objects", metadata_store=store)
    objects.put_json(trace.model_dump(mode="json"))
    evidence_objects = []
    for observation in observations:
        obj = objects.put_json(observation["payload"])
        evidence_objects.append(
            EvidenceObjectV1(**observation, object_sha256=obj.object_sha256)
        )
    evidence = EvidenceBundleV1(
        incident_id=incident.incident_id,
        diagnosis_id=diagnosis.diagnosis_id,
        objects=tuple(sorted(evidence_objects, key=lambda item: item.evidence_ref)),
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        contradicting_evidence_refs=(),
    )
    index = DiagnosisEvidenceIndexV0232.build(
        incident_id=incident.incident_id,
        diagnosis_id=diagnosis.diagnosis_id,
        evidence_bundle_sha256=semantic_sha256_v22(evidence.model_dump(mode="json")),
        all_object_refs=tuple(item.evidence_ref for item in evidence.objects),
        all_object_sha256_by_ref={
            item.evidence_ref: item.object_sha256 for item in evidence.objects
        },
        linked_support_refs=diagnosis.supporting_evidence_refs,
        linked_contradiction_refs=(),
        successful_source_refs=tuple(item.evidence_ref for item in evidence.objects),
        failed_source_refs=(),
        capability_limitation_bindings=(),
        decision_trace_sha256=trace.trace_sha256,
    )
    registry = RemediationRegistryV1.build(
        entries=(RemediationRunbookV1.build(created_at=NOW),), created_at=NOW
    )
    return dict(
        incident=incident,
        diagnosis=diagnosis,
        evidence=evidence,
        index=index,
        baseline=baseline,
        identity=identity,
        capability=capability,
        registry=registry,
        expected_registry_sha256=registry.registry_sha256,
        objects=objects,
    )


def test_exact_candidate_deterministic_immutable_zero_environment_writes(material):
    before = material["diagnosis"].model_dump_json()
    result = project_candidate(**material)
    assert not result.reason_codes
    (candidate,) = result.candidates
    assert candidate.matched_clause_id == "configuration:change-and-log"
    assert candidate.action_authority == "NONE" and not candidate.executable
    assert result.environment_writes == result.provider_calls == 0
    assert result == project_candidate(**material)
    assert material["diagnosis"].model_dump_json() == before
    with pytest.raises(ValidationError):
        candidate.executable = True
    with pytest.raises(ValidationError):
        type(candidate).model_validate(
            {**candidate.model_dump(mode="json"), "candidate_id": "cand-" + "a" * 24}
        )


@pytest.mark.parametrize(
    "terminal,lane",
    [
        ("NO_INCIDENT", "NO_INCIDENT"),
        ("OPEN_WORLD", "OPEN_WORLD"),
        ("EXTENSION_KNOWN", "EXTENSION"),
        ("INSUFFICIENT_EVIDENCE", "ABSTAIN"),
        ("CONFLICTING_EVIDENCE", "ABSTAIN"),
    ],
)
def test_non_core_zero_candidates(material, terminal, lane):
    payload = material["diagnosis"].model_dump(mode="python", exclude={"result_sha256"})
    payload.update(terminal=terminal, core_or_extension_or_open_world=lane)
    if terminal in {"NO_INCIDENT", "INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE"}:
        payload.update(root_service_ids=(), mechanism=None, broad_domain=None)
    elif terminal == "OPEN_WORLD":
        payload.update(provisional_report={"action_authority": "NONE"})
    material["diagnosis"] = sealed(DiagnosisResultV1, "result_sha256", **payload)
    result = project_candidate(**material)
    assert not result.candidates and result.reason_codes[0].value == terminal


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("root_service_ids", ("svc-" + "2" * 24, "svc-" + "a" * 24), "MULTIPLE_ROOTS"),
        ("root_service_ids", ("svc-" + "a" * 24,), "WRONG_ROOT"),
        ("broad_domain", "RESOURCE", "WRONG_DOMAIN"),
        ("mechanism", "MEMORY_LEAK", "WRONG_MECHANISM"),
        ("memory_sha256", "a" * 64, "DIAGNOSIS_BINDING_MISMATCH"),
    ],
)
def test_ineligible_semantic_diagnosis(material, field, value, reason):
    payload = material["diagnosis"].model_dump(mode="python", exclude={"result_sha256"})
    payload[field] = value
    material["diagnosis"] = sealed(DiagnosisResultV1, "result_sha256", **payload)
    assert project_candidate(**material).reason_codes[0].value == reason


def test_baseline_registry_and_index_binding(material):
    assert (
        project_candidate(**{**material, "expected_registry_sha256": "a" * 64})
        .reason_codes[0]
        .value
        == "REGISTRY_MISMATCH"
    )
    baseline = material["baseline"].model_copy(update={"active": False})
    assert (
        project_candidate(**{**material, "baseline": baseline}).reason_codes[0].value
        == "BASELINE_MISMATCH"
    )
    index = material["index"].model_dump(mode="python", exclude={"index_sha256"})
    index["decision_trace_sha256"] = "b" * 64
    changed = DiagnosisEvidenceIndexV0232.build(**index)
    assert (
        project_candidate(**{**material, "index": changed}).reason_codes[0].value
        == "MISSING_DECISION_TRACE"
    )


def test_missing_or_forged_cas_evidence_fails_closed(material):
    obj = material["evidence"].objects[0]
    path = material["objects"]._path_for(obj.object_sha256)
    path.write_text("{}")
    with pytest.raises(ObjectStoreIntegrityError):
        project_candidate(**material)


@pytest.mark.parametrize(
    "changes",
    [
        {"risk_level": "HIGH"},
        {"target_logical_service": "email"},
        {"maximum_forward_steps": 2},
        {"parameters": ["command"]},
        {"allowed_diagnosis_clause_ids": ["invented"]},
        {"executor_id": "shell"},
        {"command": "echo hi"},
    ],
)
def test_registry_rejects_broadened_even_rehashed(changes):
    with pytest.raises(ValueError):
        RemediationRunbookV1.build(created_at=NOW, **changes)


def test_registry_has_exactly_one_entry():
    for entries in [
        (),
        (
            RemediationRunbookV1.build(created_at=NOW),
            RemediationRunbookV1.build(created_at=NOW),
        ),
    ]:
        with pytest.raises(ValidationError):
            RemediationRegistryV1.build(entries=entries, created_at=NOW)


def test_committed_registry():
    root = Path(__file__).resolve().parents[2]
    registry = RemediationRegistryV1.model_validate_json(
        (root / "config/product-v040/remediation-registry.v1.json").read_bytes()
    )
    assert len(registry.entries) == 1
    assert json.loads(registry.model_dump_json())["entries"][0]["parameters"] == []


def rebuild_index(index, evidence=None, **changes):
    payload = index.model_dump(mode="python", exclude={"index_sha256"})
    if evidence is not None:
        payload.update(
            evidence_bundle_sha256=semantic_sha256_v22(
                evidence.model_dump(mode="json")
            ),
            all_object_refs=tuple(item.evidence_ref for item in evidence.objects),
            all_object_sha256_by_ref={
                item.evidence_ref: item.object_sha256 for item in evidence.objects
            },
        )
    return DiagnosisEvidenceIndexV0232.build(**{**payload, **changes})


def test_ref_swap_cannot_forge_resolving_support(material):
    evidence = material["evidence"]
    first, second = evidence.objects
    swapped = tuple(
        sorted(
            (
                first.model_copy(update={"evidence_ref": second.evidence_ref}),
                second.model_copy(update={"evidence_ref": first.evidence_ref}),
            ),
            key=lambda item: item.evidence_ref,
        )
    )
    forged = evidence.model_copy(update={"objects": swapped})
    result = project_candidate(
        **{
            **material,
            "evidence": forged,
            "index": rebuild_index(material["index"], forged),
        }
    )
    assert not result.candidates
    assert result.reason_codes[0].value == "EVIDENCE_BINDING_MISMATCH"


def test_rehashed_source_status_lie_rejected(material):
    index = material["index"]
    forged = rebuild_index(
        index, successful_source_refs=(), failed_source_refs=index.all_object_refs
    )
    result = project_candidate(**{**material, "index": forged})
    assert (
        not result.candidates
        and result.reason_codes[0].value == "EVIDENCE_BINDING_MISMATCH"
    )


def test_missing_trace_object_fails_closed(material):
    hashes = {item.object_sha256 for item in material["evidence"].objects}
    for path in material["objects"].sha_root.rglob("*.json"):
        if path.stem not in hashes:
            path.unlink()
    with pytest.raises(ObjectStoreIntegrityError):
        project_candidate(**material)


def test_history_and_goal_are_byte_bound():
    from scripts.ci.verify_product_v040_history import verify

    result = verify(Path(__file__).resolve().parents[2])
    assert result["terminal"] == "ECOMSRE_PRODUCT_V040_HISTORY_BINDING_PASS"
    assert result["frozen_files"] > 0


def test_selected_source_unavailable_denies_after_parent_rebinding(material):
    capability_payload = material["capability"].model_dump(
        mode="python", exclude={"capability_sha256"}
    )
    capability_payload["sources"] = tuple(
        item.model_copy(update={"status": "UNAVAILABLE"})
        if item.source is EvidenceSourceV22.LOGS
        else item
        for item in material["capability"].sources
    )
    capability = sealed(
        EnvironmentCapabilityMatrixV1, "capability_sha256", **capability_payload
    )
    baseline_payload = material["baseline"].model_dump(
        mode="python", exclude={"baseline_sha256"}
    )
    baseline_payload["source_capability_sha256"] = capability.capability_sha256
    baseline = sealed(EnvironmentBaselineV1, "baseline_sha256", **baseline_payload)
    incident_payload = material["incident"].model_dump(
        mode="python", exclude={"incident_sha256"}
    )
    incident_payload.update(
        source_capability_sha256=capability.capability_sha256,
        baseline_sha256=baseline.baseline_sha256,
    )
    incident = sealed(IncidentRecordV1, "incident_sha256", **incident_payload)
    result = project_candidate(
        **{
            **material,
            "capability": capability,
            "baseline": baseline,
            "incident": incident,
        }
    )
    assert result.reason_codes[0].value == "REQUIRED_SOURCE_UNAVAILABLE"


def test_raw_failure_cannot_be_laundered_by_successful_memory(material):
    from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
    from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22

    evidence = material["evidence"]
    original = next(
        item for item in evidence.objects if item.source is EvidenceSourceV22.CHANGES
    )
    payload = json.loads(json.dumps(original.payload))
    raw = ReadOutcomeV22.model_validate_json(json.dumps(payload["read_outcome"]))
    raw_payload = raw.model_dump(mode="python", exclude={"outcome_sha256"})
    raw_payload.update(status=ReadSourceStatusV22.FAILURE_TIMEOUT, records=())
    payload["read_outcome"] = sealed(
        ReadOutcomeV22, "outcome_sha256", **raw_payload
    ).model_dump(mode="json")
    persisted = material["objects"].put_json(payload)
    replacement = original.model_copy(
        update={"payload": payload, "object_sha256": persisted.object_sha256}
    )
    forged = evidence.model_copy(
        update={
            "objects": tuple(
                replacement if item == original else item for item in evidence.objects
            )
        }
    )
    result = project_candidate(
        **{
            **material,
            "evidence": forged,
            "index": rebuild_index(material["index"], forged),
        }
    )
    assert (
        not result.candidates
        and result.reason_codes[0].value == "EVIDENCE_BINDING_MISMATCH"
    )


def persist_material(material):
    from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
    from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1

    incident, diagnosis = material["incident"], material["diagnosis"]
    store = material["objects"].metadata_store
    stamp = NOW.isoformat()
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO environments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                incident.environment_id,
                "test",
                "fixture",
                "UTC",
                "{}",
                '["payment"]',
                stamp,
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO baseline_versions VALUES (?, ?, ?, ?, ?)",
            (
                material["baseline"].baseline_id,
                incident.environment_id,
                material["baseline"].model_dump_json(),
                1,
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO incidents VALUES (?, ?, ?, ?, ?)",
            (
                incident.incident_id,
                incident.environment_id,
                incident.external_incident_key,
                incident.model_dump_json(),
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO diagnosis_results VALUES (?, ?, ?, ?)",
            (
                diagnosis.diagnosis_id,
                incident.incident_id,
                diagnosis.model_dump_json(),
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO diagnosis_evidence_indexes VALUES (?, ?, ?, ?, ?)",
            (
                diagnosis.diagnosis_id,
                incident.incident_id,
                material["index"].model_dump_json(),
                material["index"].index_sha256,
                stamp,
            ),
        )
        for item in material["evidence"].objects:
            connection.execute(
                "INSERT INTO diagnosis_evidence_links VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    diagnosis.diagnosis_id,
                    incident.incident_id,
                    item.object_sha256,
                    item.evidence_ref,
                    item.source.value,
                    item.action_id,
                    "OBSERVATION",
                    stamp,
                ),
            )
    ServiceCatalogRepositoryV1(store).put_map(material["identity"], created_at=stamp)
    CapabilityMatrixRepositoryV1(store).put(material["capability"])


def test_repository_wrapper_uses_persisted_parents_and_performs_no_sql_writes(material):
    from ecomsre.product.remediation.source import project_for_incident

    persist_material(material)
    store = material["objects"].metadata_store
    incident = material["incident"]
    with store.connect() as connection:
        before = tuple(connection.iterdump())
    projected = project_for_incident(
        incident.incident_id,
        store=store,
        objects=material["objects"],
        registry=material["registry"],
        expected_registry_sha256=material["expected_registry_sha256"],
    )
    assert len(projected.candidates) == 1
    with store.connect() as connection:
        assert tuple(connection.iterdump()) == before


def test_multiple_effective_core_admissions_cannot_be_resealed_as_single(material):
    from ecomsre.dta_v2.v22.memory import RuntimeReadOutcomeV22
    from ecomsre.product.incidents.evidence_binding_v0232 import (
        DiagnosisDecisionTraceV0232,
    )
    from tests.dta_v22.test_v22_memory_predicates_diagnosis import _outcome

    original_outcomes = _incident_outcomes()
    resource = next(
        item for item in original_outcomes if item.source is EvidenceSourceV22.RESOURCES
    )
    record = resource.records[0]
    sustained_cpu = record.model_copy(
        update={
            "samples": tuple(
                sample.model_copy(update={"cpu_percent": 95.0})
                for sample in record.samples
            )
        }
    )
    changed_resource = _outcome(
        action_id=resource.action_id, source=resource.source, records=(sustained_cpu,)
    )
    memory_outcomes = tuple(
        sorted(
            (
                changed_resource if item == resource else item
                for item in original_outcomes
            ),
            key=lambda item: item.action_id,
        )
    )
    raw = tuple(
        item.source_outcome if isinstance(item, RuntimeReadOutcomeV22) else item
        for item in memory_outcomes
    )
    snapshots = tuple(
        {
            "action": {"action_id": item.action_id, "source": item.source.value},
            "read_outcome": item.model_dump(mode="json"),
            "memory_outcome": memory.model_dump(mode="json"),
            "connector_result": ConnectorQueryResultV1.build(
                source=item.source,
                status=item.status,
                requested_services=("payment",),
                covered_services=("payment",),
                window=ConnectorWindowV1(
                    started_at=NOW - timedelta(minutes=5), ended_at=NOW
                ),
                records=item.records,
                truncated=item.truncated,
                safe_error_code=None,
                latency_ms=1.0,
            ).model_dump(mode="json"),
        }
        for item, memory in zip(raw, memory_outcomes, strict=True)
    )
    acquisition = ProductReadAcquisitionV1(
        raw_outcomes=raw,
        memory_outcomes=memory_outcomes,
        snapshots=snapshots,
        covered_services_by_source={item.source: ("payment",) for item in raw},
        capability_limitations=(),
        capability_observations_v0232=(),
        capability_limitation_candidates_v0232=(),
    )
    actual, observations, trace = ProductDiagnosisBridgeV1().diagnose(
        incident=material["incident"],
        baseline=material["baseline"],
        identity_map=material["identity"],
        acquisition=acquisition,
        diagnosis_id=material["diagnosis"].diagnosis_id,
        created_at=NOW,
    )
    assert actual.terminal.value == "CONFLICTING_EVIDENCE"
    assert trace.known_admission_status.value == "MULTIPLE_ADMISSIONS"
    payload = material["diagnosis"].model_dump(mode="python", exclude={"result_sha256"})
    payload["memory_sha256"] = actual.memory_sha256
    forged_diagnosis = sealed(DiagnosisResultV1, "result_sha256", **payload)
    trace_payload = trace.model_dump(mode="python", exclude={"trace_sha256"})
    trace_payload.update(
        known_admission_status="SINGLE_ADMISSION", novelty_gate_reason_codes=()
    )
    forged_trace = DiagnosisDecisionTraceV0232.build(**trace_payload)
    material["objects"].put_json(forged_trace.model_dump(mode="json"))
    evidence_objects = []
    for item in observations:
        stored = material["objects"].put_json(item["payload"])
        evidence_objects.append(
            EvidenceObjectV1(**item, object_sha256=stored.object_sha256)
        )
    evidence = material["evidence"].model_copy(
        update={
            "objects": tuple(
                sorted(evidence_objects, key=lambda item: item.evidence_ref)
            )
        }
    )
    index = rebuild_index(
        material["index"],
        evidence,
        successful_source_refs=tuple(item.evidence_ref for item in evidence.objects),
        decision_trace_sha256=forged_trace.trace_sha256,
    )
    result = project_candidate(
        **{
            **material,
            "diagnosis": forged_diagnosis,
            "evidence": evidence,
            "index": index,
        }
    )
    assert not result.candidates
    assert result.reason_codes[0].value == "DIAGNOSIS_BINDING_MISMATCH"
