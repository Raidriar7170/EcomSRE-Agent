#!/usr/bin/env python3
"""Run the ten-case deterministic Product v0.2.3.2 Evidence preflight."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
    ACTIVE_PROFILE_SHA256_V023,
    CANDIDATE_SET_SHA256_V023,
    OPERATOR_DECISION_SHA256_V023,
)
from ecomsre.product.contracts import ConnectorKindV1
from ecomsre.product.errors import ProductError
from ecomsre.product.incidents.contracts import (
    ActionAuthorityV1,
    DiagnosisLaneV1,
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityLimitationBindingV0232,
    ConnectorEvidenceBindingV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
    OpenSearchProfileEvidenceBindingV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)
from ecomsre.product.incidents.repository import DiagnosisRepositoryV1
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NOFAULT_CAPABILITY_LIMITED_V0232,
    NOFAULT_FULLY_SUPPORTED_V0232,
    NOFAULT_NOT_SUPPORTED_V0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.ci.verify_product_v0231_result import verify_product_v0231_result


_INCIDENT_ID = f"inc-{'1' * 24}"
_DIAGNOSIS_ID = f"diag-{'2' * 24}"
_SERVICE_ID = f"svc-{'3' * 24}"
_ENVIRONMENT_ID = f"env-{'4' * 24}"
_NOW = datetime(2026, 8, 30, 5, 0, tzinfo=UTC)
_WINDOW = ConnectorWindowV1(
    started_at=_NOW - timedelta(minutes=1),
    ended_at=_NOW,
)
_SHA = {
    name: character * 64
    for name, character in {
        "profile_diagnostics": "1",
        "runtime_snapshot": "2",
        "runtime_authority": "3",
        "pilot_authority": "4",
        "read_authority": "5",
        "runtime_connector": "6",
        "memory": "7",
    }.items()
}


def _object_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _generic_binding(
    *,
    result: ConnectorQueryResultV1,
    binding_kind: str,
    binding_payload_sha256: str,
) -> ConnectorEvidenceBindingV0232:
    source = result.source
    kind = {
        EvidenceSourceV22.LOGS: ConnectorKindV1.OPENSEARCH,
        EvidenceSourceV22.METRICS: ConnectorKindV1.PROMETHEUS,
        EvidenceSourceV22.RUNTIME: ConnectorKindV1.PILOT_RUNTIME,
    }[source]
    return ConnectorEvidenceBindingV0232.build(
        binding_id=f"binding:v0232:{source.value.lower()}-fixture",
        incident_id=_INCIDENT_ID,
        action_id=f"a:{source.value.lower()}:checkout",
        source=source,
        connector_name=f"fixture-{source.value.lower()}",
        connector_kind=kind,
        environment_id=_ENVIRONMENT_ID,
        connector_config_sha256=semantic_sha256_v22(
            {"kind": kind.value, "source": source.value}
        ),
        query_context_sha256=semantic_sha256_v22(
            {"source": source.value, "window": _WINDOW.model_dump(mode="json")}
        ),
        component_result_sha256=result.result_sha256,
        combined_result_sha256=result.result_sha256,
        requested_services=result.requested_services,
        covered_services=result.covered_services,
        window=result.window,
        binding_kind=binding_kind,
        binding_payload_sha256=binding_payload_sha256,
    )


def _profile_binding(result_sha256: str) -> OpenSearchProfileEvidenceBindingV0232:
    return OpenSearchProfileEvidenceBindingV0232.build(
        active_profile_id="product-v0222-operator-selected-profile",
        active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        profile_binding_sha256=ACTIVE_PROFILE_BINDING_SHA256_V023,
        selected_candidate_alias="P01",
        candidate_set_sha256=CANDIDATE_SET_SHA256_V023,
        operator_decision_sha256=OPERATOR_DECISION_SHA256_V023,
        query_diagnostics_sha256=_SHA["profile_diagnostics"],
        accepted_record_count=1,
        rejected_record_count=0,
        rejection_reason_codes=(),
        connector_result_sha256=result_sha256,
        query_window=_WINDOW,
    )


def _runtime_binding(result_sha256: str) -> RuntimeSnapshotEvidenceBindingV0232:
    return RuntimeSnapshotEvidenceBindingV0232.build(
        runtime_snapshot_sha256=_SHA["runtime_snapshot"],
        runtime_snapshot_observed_at=_NOW - timedelta(seconds=10),
        runtime_snapshot_environment_id=_ENVIRONMENT_ID,
        runtime_snapshot_authority_sha256=_SHA["runtime_connector"],
        pilot_runtime_authority_sha256=_SHA["pilot_authority"],
        read_authority_sha256=_SHA["read_authority"],
        connector_binding_sha256=_SHA["runtime_connector"],
        maximum_age_seconds=600,
        age_at_query_seconds=10.0,
        requested_services=("checkout",),
        covered_services=("checkout",),
        connector_result_sha256=result_sha256,
        query_window=_WINDOW,
    )


def _source_object(
    *,
    source: EvidenceSourceV22,
    status: str = "SUCCESS_NONEMPTY",
    specialized: str | None = None,
    stale_runtime: bool = False,
) -> EvidenceObjectV1:
    status_value = ReadSourceStatusV22(status)
    records: tuple[Any, ...]
    if status_value is ReadSourceStatusV22.SUCCESS_NONEMPTY:
        if source is EvidenceSourceV22.LOGS:
            records = (
                LogRecordV22(
                    schema_version="dta-v22.log-record.v1",
                    observed_at=_NOW - timedelta(seconds=30),
                    service="checkout",
                    severity="DIAGNOSTIC",
                    message="healthy checkout",
                ),
            )
        elif source is EvidenceSourceV22.METRICS:
            records = (
                MetricFactV22(
                    schema_version="dta-v22.metric-fact.v1",
                    service="checkout",
                    metric_kind=MetricKindV22.REQUEST_SUPPORT,
                    support_status=MetricSupportStatusV22.SUPPORTED,
                    sample_count=1,
                    value=1.0,
                    unit=METRIC_UNIT_BY_KIND_V22[MetricKindV22.REQUEST_SUPPORT],
                    window_started_at=_WINDOW.started_at,
                    window_ended_at=_WINDOW.ended_at,
                ),
            )
        else:
            records = (
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service="checkout",
                    state=RuntimeStateV22.RUNNING,
                    healthy=True,
                    restart_count=0,
                ),
            )
    else:
        records = ()
    result = ConnectorQueryResultV1.build(
        source=source,
        status=status_value,
        requested_services=("checkout",),
        covered_services=(
            () if status_value.value.startswith("FAILURE_") else ("checkout",)
        ),
        window=_WINDOW,
        records=records,
        truncated=False,
        safe_error_code=(
            "FIXTURE_SOURCE_UNAVAILABLE"
            if status_value.value.startswith("FAILURE_")
            else None
        ),
        latency_ms=1.0,
    )
    result_sha256 = result.result_sha256
    specialized_payload: dict[str, Any] | None = None
    binding_kind = "GENERIC"
    if specialized == "OPENSEARCH_PROFILE":
        profile = _profile_binding(result_sha256)
        specialized_payload = profile.model_dump(mode="json")
        binding_kind = specialized
        payload_sha256 = profile.binding_sha256
    elif specialized == "RUNTIME_SNAPSHOT":
        runtime = _runtime_binding(result_sha256)
        specialized_payload = runtime.model_dump(mode="json")
        if stale_runtime:
            specialized_payload["age_at_query_seconds"] = 601.0
        binding_kind = specialized
        payload_sha256 = runtime.binding_sha256
    else:
        payload_sha256 = result_sha256
    generic = _generic_binding(
        result=result,
        binding_kind=binding_kind,
        binding_payload_sha256=payload_sha256,
    )
    payload = {
        "schema_version": "ecomsre.product.read-snapshot.v1",
        "connector_components": [result.model_dump(mode="json")],
        "connector_result": result.model_dump(mode="json"),
        "connector_bindings_v0232": [
            {
                "connector_binding": generic.model_dump(mode="json"),
                "binding_payload": specialized_payload,
            }
        ],
    }
    reference = f"e:v0232:{source.value.lower()}:{status.lower()}"
    return EvidenceObjectV1(
        evidence_ref=reference,
        source=source,
        action_id=f"a:{source.value.lower()}:checkout",
        object_sha256=_object_sha256(payload),
        payload=payload,
    )


def _diagnosis(
    *,
    terminal: DiagnosisTerminalV1,
    support: tuple[str, ...],
    limitations: tuple[str, ...] = (),
) -> DiagnosisResultV1:
    classified = terminal in {
        DiagnosisTerminalV1.CORE_KNOWN,
        DiagnosisTerminalV1.EXTENSION_KNOWN,
        DiagnosisTerminalV1.OPEN_WORLD,
    }
    lane = {
        DiagnosisTerminalV1.NO_INCIDENT: DiagnosisLaneV1.NO_INCIDENT,
        DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE: DiagnosisLaneV1.ABSTAIN,
        DiagnosisTerminalV1.OPEN_WORLD: DiagnosisLaneV1.OPEN_WORLD,
    }[terminal]
    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.diagnosis-result.v1",
        "diagnosis_id": _DIAGNOSIS_ID,
        "incident_id": _INCIDENT_ID,
        "terminal": terminal,
        "core_or_extension_or_open_world": lane,
        "root_service_ids": ((_SERVICE_ID,) if classified else ()),
        "mechanism": ("UNKNOWN_MECHANISM" if classified else None),
        "broad_domain": ("UNKNOWN" if classified else None),
        "supporting_evidence_refs": tuple(sorted(support)),
        "contradicting_evidence_refs": (),
        "capability_limitations": tuple(sorted(limitations)),
        "provisional_report": ({"bounded": True} if terminal is DiagnosisTerminalV1.OPEN_WORLD else None),
        "action_authority": ActionAuthorityV1.NONE,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "memory_sha256": _SHA["memory"],
        "created_at": _NOW,
    }
    normalized = DiagnosisResultV1.model_construct(
        **body,
        result_sha256="0" * 64,
    ).model_dump(mode="json", exclude={"result_sha256"})
    return DiagnosisResultV1.model_validate(
        {**normalized, "result_sha256": semantic_sha256_v22(normalized)}
    )


def _fixture(
    *,
    terminal: DiagnosisTerminalV1 = DiagnosisTerminalV1.NO_INCIDENT,
    stale_runtime: bool = False,
    profile_bound: bool = True,
    metrics_failure: bool = False,
    limitation_code: str | None = None,
    limitation_bound: bool = False,
    algorithmic_reason: str | None = None,
) -> tuple[
    DiagnosisResultV1,
    EvidenceBundleV1,
    DiagnosisEvidenceIndexV0232,
    DiagnosisDecisionTraceV0232,
]:
    logs = _source_object(
        source=EvidenceSourceV22.LOGS,
        specialized=("OPENSEARCH_PROFILE" if profile_bound else None),
    )
    metrics = _source_object(
        source=EvidenceSourceV22.METRICS,
        status=("FAILURE_UNAVAILABLE" if metrics_failure else "SUCCESS_NONEMPTY"),
    )
    runtime = _source_object(
        source=EvidenceSourceV22.RUNTIME,
        specialized="RUNTIME_SNAPSHOT",
        stale_runtime=stale_runtime,
    )
    objects = (logs, metrics, runtime)
    successful = tuple(
        sorted(
            item.evidence_ref
            for item in objects
            if item.payload["connector_result"]["status"] in {
                "SUCCESS_EMPTY",
                "SUCCESS_NONEMPTY",
            }
        )
    )
    failed = tuple(sorted(set(item.evidence_ref for item in objects) - set(successful)))
    limitations = (() if limitation_code is None else (limitation_code,))
    diagnosis = _diagnosis(
        terminal=terminal,
        support=successful,
        limitations=limitations,
    )
    bundle = EvidenceBundleV1(
        incident_id=_INCIDENT_ID,
        diagnosis_id=_DIAGNOSIS_ID,
        objects=objects,
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        contradicting_evidence_refs=(),
    )
    trace = DiagnosisDecisionTraceV0232.build(
        incident_id=_INCIDENT_ID,
        diagnosis_id=_DIAGNOSIS_ID,
        known_admission_status="NONE",
        extension_match_count=0,
        no_incident_admissible=terminal is DiagnosisTerminalV1.NO_INCIDENT,
        required_coverage_satisfied=not metrics_failure,
        failed_sources=(EvidenceSourceV22.METRICS,) if metrics_failure else (),
        novelty_gate_disposition=(
            "INSUFFICIENT_EVIDENCE" if algorithmic_reason else None
        ),
        novelty_gate_reason_codes=(
            () if algorithmic_reason is None else (algorithmic_reason,)
        ),
        residual_anomaly_ids=(),
    )
    limitation_bindings: tuple[CapabilityLimitationBindingV0232, ...] = ()
    if limitation_code is not None and limitation_bound:
        connector_result = metrics.payload["connector_result"]
        limitation_bindings = (
            CapabilityLimitationBindingV0232.build(
                limitation_code=limitation_code,
                category="QUERY_FAILURE",
                source=EvidenceSourceV22.METRICS,
                evidence_ref=metrics.evidence_ref,
                connector_result_sha256=connector_result["result_sha256"],
                capability_observation_sha256=None,
                safe_error_code=connector_result["safe_error_code"],
                coverage_status="NONE",
            ),
        )
    index = DiagnosisEvidenceIndexV0232.build(
        incident_id=_INCIDENT_ID,
        diagnosis_id=_DIAGNOSIS_ID,
        evidence_bundle_sha256=semantic_sha256_v22(bundle.model_dump(mode="json")),
        all_object_refs=tuple(item.evidence_ref for item in objects),
        all_object_sha256_by_ref={
            item.evidence_ref: item.object_sha256 for item in objects
        },
        linked_support_refs=diagnosis.supporting_evidence_refs,
        linked_contradiction_refs=(),
        successful_source_refs=successful,
        failed_source_refs=failed,
        open_search_profile_binding_ref=(logs.evidence_ref if profile_bound else None),
        runtime_snapshot_binding_ref=runtime.evidence_ref,
        capability_limitation_bindings=limitation_bindings,
        decision_trace_sha256=trace.trace_sha256,
    )
    return diagnosis, bundle, index, trace


def _run_case(
    case_id: str,
    expected_terminal: str,
    *,
    required_reason: str | None = None,
    forbidden_reason: str | None = None,
    **fixture_options: Any,
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    fixture = _fixture(**fixture_options)
    assessment = score_nofault_evidence_v0232(
        diagnosis=fixture[0],
        bundle=fixture[1],
        index=fixture[2],
        decision_trace=fixture[3],
    )
    passed = assessment.terminal.value == expected_terminal
    if required_reason is not None:
        passed = passed and required_reason in assessment.reasons
    if forbidden_reason is not None:
        passed = passed and forbidden_reason not in assessment.reasons
    return (
        {
            "case_id": case_id,
            "expected_terminal": expected_terminal,
            "observed_terminal": assessment.terminal.value,
            "reasons": list(assessment.reasons),
            "assessment_sha256": assessment.result_sha256,
            "passed": passed,
        },
        fixture,
    )


def _sqlite_index_immutability_probe(
    fixture: tuple[
        DiagnosisResultV1,
        EvidenceBundleV1,
        DiagnosisEvidenceIndexV0232,
        DiagnosisDecisionTraceV0232,
    ],
) -> bool:
    diagnosis, bundle, reference_index, trace = fixture
    observations = tuple(
        {
            "evidence_ref": item.evidence_ref,
            "source": item.source.value,
            "action_id": item.action_id,
            "payload": item.payload,
        }
        for item in bundle.objects
    )
    with TemporaryDirectory(prefix="ecomsre-v0232-index-") as temporary:
        root = Path(temporary)
        store = SqliteStoreV1(root / "product.sqlite3")
        object_store = ContentAddressedObjectStoreV1(
            root / "objects",
            metadata_store=store,
        )
        diagnoses = DiagnosisRepositoryV1(store, object_store)
        jobs = JobRepositoryV1(store)
        with store.connect() as connection:
            connection.execute(
                "INSERT INTO environments("
                "environment_id, name, description, timezone, "
                "service_identity_policy_json, explicit_service_catalog_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _ENVIRONMENT_ID,
                    "v0232-preflight",
                    "temporary Evidence Index persistence probe",
                    "UTC",
                    "{}",
                    "[]",
                    _NOW.isoformat(),
                    _NOW.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO incidents("
                "incident_id, environment_id, external_incident_key, "
                "payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    _INCIDENT_ID,
                    _ENVIRONMENT_ID,
                    "v0232-evidence-index-preflight",
                    "{}",
                    _NOW.isoformat(),
                ),
            )
        queued = jobs.enqueue(ProductJobTypeV1.DIAGNOSIS, {}, now=100.0)
        claimed = jobs.claim_next(
            "v0232-preflight",
            lease_seconds=60,
            now=100.0,
        )
        if claimed is None or claimed.job_id != queued.job_id:
            return False
        fence = JobLeaseFenceV1(
            job_id=claimed.job_id,
            claimed_by="v0232-preflight",
            attempt_count=claimed.attempt_count,
            checked_at=100.0,
        )
        diagnoses.put(
            result=diagnosis,
            observations=observations,
            fence=fence,
            decision_trace_v0232=trace,
            limitation_candidates_v0232=(),
        )
        persisted = diagnoses.evidence_index(_INCIDENT_ID)
        if persisted.index_sha256 != reference_index.index_sha256:
            return False
        diagnoses.put(
            result=diagnosis,
            observations=observations,
            fence=fence,
            decision_trace_v0232=trace,
            limitation_candidates_v0232=(),
        )
        with store.connect() as connection:
            row_count = connection.execute(
                "SELECT COUNT(*) FROM diagnosis_evidence_indexes "
                "WHERE incident_id = ?",
                (_INCIDENT_ID,),
            ).fetchone()[0]
        if row_count != 1:
            return False
        conflicting_trace = DiagnosisDecisionTraceV0232.build(
            incident_id=diagnosis.incident_id,
            diagnosis_id=diagnosis.diagnosis_id,
            known_admission_status="NONE",
            extension_match_count=0,
            no_incident_admissible=True,
            required_coverage_satisfied=True,
            failed_sources=(),
            novelty_gate_disposition="INSUFFICIENT_EVIDENCE",
            novelty_gate_reason_codes=("IMMUTABLE_CONFLICT_PROBE",),
            residual_anomaly_ids=(),
        )
        try:
            diagnoses.put(
                result=diagnosis,
                observations=observations,
                fence=fence,
                decision_trace_v0232=conflicting_trace,
                limitation_candidates_v0232=(),
            )
        except ProductError as error:
            if error.code != "DIAGNOSIS_EVIDENCE_INDEX_IMMUTABLE_CONFLICT":
                return False
        else:
            return False
        return (
            diagnoses.evidence_index(_INCIDENT_ID).index_sha256
            == reference_index.index_sha256
        )


def run_preflight(project_root: Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    predecessor = verify_product_v0231_result(root)
    definitions: tuple[tuple[str, str, dict[str, Any]], ...] = (
        ("01_FRESH_RUNTIME_EXPLICIT", NOFAULT_FULLY_SUPPORTED_V0232, {}),
        (
            "02_STALE_RUNTIME",
            NOFAULT_NOT_SUPPORTED_V0232,
            {
                "stale_runtime": True,
                "required_reason": "FRESH_HEALTHY_RUNTIME_MISSING",
            },
        ),
        ("03_ACTIVE_P01_EXPLICIT", NOFAULT_FULLY_SUPPORTED_V0232, {}),
        (
            "04_LOGS_WITHOUT_PROFILE",
            NOFAULT_NOT_SUPPORTED_V0232,
            {
                "profile_bound": False,
                "required_reason": "LOGS_PROFILE_BINDING_MISSING",
            },
        ),
        (
            "05_SOURCE_FAILURE_BOUND",
            NOFAULT_CAPABILITY_LIMITED_V0232,
            {
                "terminal": DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE,
                "metrics_failure": True,
                "limitation_code": "SOURCE_METRICS_QUERY_FAILURE",
                "limitation_bound": True,
            },
        ),
        (
            "06_SOURCE_LIMITATION_UNBOUND",
            NOFAULT_NOT_SUPPORTED_V0232,
            {
                "terminal": DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE,
                "metrics_failure": True,
                "limitation_code": "SOURCE_METRICS_QUERY_FAILURE",
                "required_reason": "CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED",
            },
        ),
        (
            "07_ALGORITHMIC_REASON_SEPARATED",
            NOFAULT_NOT_SUPPORTED_V0232,
            {
                "terminal": DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE,
                "algorithmic_reason": "NOVELTY_GATE_NO_STRONG_ANOMALY",
                "required_reason": "CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED",
                "forbidden_reason": "ALGORITHMIC_REASON_MASQUERADES_AS_CAPABILITY",
            },
        ),
        ("08_NO_INCIDENT_COMPLETE", NOFAULT_FULLY_SUPPORTED_V0232, {}),
        (
            "09_INSUFFICIENT_EVIDENCE_BOUND",
            NOFAULT_CAPABILITY_LIMITED_V0232,
            {
                "terminal": DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE,
                "metrics_failure": True,
                "limitation_code": "SOURCE_METRICS_QUERY_FAILURE",
                "limitation_bound": True,
            },
        ),
        (
            "10_FALSE_OPEN_WORLD_HEALTHY",
            NOFAULT_NOT_SUPPORTED_V0232,
            {
                "terminal": DiagnosisTerminalV1.OPEN_WORLD,
                "required_reason": "FALSE_INCIDENT_TERMINAL",
            },
        ),
    )
    cases: list[dict[str, Any]] = []
    fixtures: list[tuple[Any, ...]] = []
    for case_id, expected, options in definitions:
        options = dict(options)
        required: str | None = options.pop("required_reason", None)
        forbidden: str | None = options.pop("forbidden_reason", None)
        case, fixture = _run_case(
            case_id,
            expected,
            required_reason=required,
            forbidden_reason=forbidden,
            **options,
        )
        cases.append(case)
        fixtures.append(fixture)
    first_index = fixtures[0][2]
    rebuilt_index = DiagnosisEvidenceIndexV0232.build(
        **first_index.model_dump(mode="python", exclude={"index_sha256"})
    )
    deterministic = rebuilt_index.index_sha256 == first_index.index_sha256
    immutable = False
    try:
        DiagnosisEvidenceIndexV0232.model_validate(
            {
                **first_index.model_dump(mode="python"),
                "evidence_bundle_sha256": "0" * 64,
            }
        )
    except ValueError:
        immutable = True
    immutable_persistence = _sqlite_index_immutability_probe(fixtures[0])
    passed_count = sum(1 for case in cases if case["passed"])
    predecessor_verified = (
        predecessor["measured_terminal"]
        == "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED"
    )
    evidence_bundle_compatible = all(
        isinstance(fixture[1], EvidenceBundleV1) for fixture in fixtures
    )
    contract_passed = (
        passed_count == len(cases)
        and predecessor_verified
        and evidence_bundle_compatible
        and deterministic
        and immutable
        and immutable_persistence
    )
    body = {
        "schema_version": "ecomsre.product.evidence-binding-preflight.v0232",
        "terminal": (
            "ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_PASS"
            if contract_passed
            else "ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_FAIL"
        ),
        "case_count": len(cases),
        "passed_case_count": passed_count,
        "cases": cases,
        "predecessor_result_verified": predecessor_verified,
        "evidence_bundle_v1_compatible": evidence_bundle_compatible,
        "index_deterministic": deterministic,
        "index_seal_rejects_mutation": immutable,
        "index_immutable_persistence": immutable_persistence,
        "index_deterministic_and_immutable": (
            deterministic and immutable and immutable_persistence
        ),
        "reference_evidence_index_sha256": first_index.index_sha256,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
    }
    return {**body, "preflight_sha256": semantic_sha256_v22(body)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_preflight(arguments.project_root)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized, encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ("run_preflight",)
