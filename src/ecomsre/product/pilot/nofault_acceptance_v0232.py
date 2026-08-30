"""Explicit-index No-Fault evidence scoring for Product v0.2.3.2."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorQueryResultV1
from ecomsre.product.contracts import ConnectorKindV1, ProductModelV1
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    ConnectorEvidenceBindingV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
    OpenSearchProfileEvidenceBindingV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)


NOFAULT_FULLY_SUPPORTED_V0232 = (
    "ECOMSRE_PRODUCT_V0232_NOFAULT_FULLY_SUPPORTED"
)
NOFAULT_CAPABILITY_LIMITED_V0232 = (
    "ECOMSRE_PRODUCT_V0232_NOFAULT_CAPABILITY_LIMITED"
)
NOFAULT_NOT_SUPPORTED_V0232 = "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED"
_SUCCESS_STATUSES = {"SUCCESS_EMPTY", "SUCCESS_NONEMPTY"}
_REQUIRED_SOURCES = {
    EvidenceSourceV22.LOGS,
    EvidenceSourceV22.METRICS,
    EvidenceSourceV22.RUNTIME,
}
_EXPECTED_CONNECTOR_KIND_BY_SOURCE = {
    EvidenceSourceV22.LOGS: ConnectorKindV1.OPENSEARCH,
    EvidenceSourceV22.METRICS: ConnectorKindV1.PROMETHEUS,
    EvidenceSourceV22.RUNTIME: ConnectorKindV1.PILOT_RUNTIME,
}


class NoFaultMeasuredTerminalV0232(str, Enum):
    FULLY_SUPPORTED = NOFAULT_FULLY_SUPPORTED_V0232
    CAPABILITY_LIMITED = NOFAULT_CAPABILITY_LIMITED_V0232
    NOT_SUPPORTED = NOFAULT_NOT_SUPPORTED_V0232


def _canonical_object_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _connector_result(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = payload.get("connector_result")
    return value if isinstance(value, Mapping) else None


def _validated_connector_result(
    payload: Mapping[str, Any],
) -> ConnectorQueryResultV1 | None:
    value = _connector_result(payload)
    if (
        value is None
        or value.get("schema_version")
        != "ecomsre.product.connector-query-result.v1"
    ):
        return None
    try:
        return ConnectorQueryResultV1.model_validate_json(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return None


def _validated_component_results(
    payload: Mapping[str, Any],
) -> tuple[ConnectorQueryResultV1, ...] | None:
    values = payload.get("connector_components")
    if not isinstance(values, (list, tuple)) or not values:
        return None
    results: list[ConnectorQueryResultV1] = []
    for value in values:
        if (
            not isinstance(value, Mapping)
            or value.get("schema_version")
            != "ecomsre.product.connector-query-result.v1"
        ):
            return None
        try:
            results.append(
                ConnectorQueryResultV1.model_validate_json(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            )
        except (TypeError, ValueError):
            return None
    return tuple(results)


def _generic_provenance_valid(
    *,
    evidence: Any,
    index: DiagnosisEvidenceIndexV0232,
) -> bool:
    result = _validated_connector_result(evidence.payload)
    components = _validated_component_results(evidence.payload)
    entries = evidence.payload.get("connector_bindings_v0232")
    if (
        result is None
        or components is None
        or not isinstance(entries, (list, tuple))
        or len(entries) != len(components)
        or not entries
    ):
        return False
    bindings: list[ConnectorEvidenceBindingV0232] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            return False
        payload = entry.get("connector_binding")
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version")
            != "ecomsre.product.connector-evidence-binding.v0232"
        ):
            return False
        try:
            binding = ConnectorEvidenceBindingV0232.model_validate_json(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValueError):
            return False
        component = next(
            (
                item
                for item in components
                if item.result_sha256 == binding.component_result_sha256
            ),
            None,
        )
        specialized = entry.get("binding_payload")
        if (
            component is None
            or binding.incident_id != index.incident_id
            or binding.action_id != evidence.action_id
            or binding.source is not evidence.source
            or binding.source is not result.source
            or binding.source is not component.source
            or binding.connector_kind
            is not _EXPECTED_CONNECTOR_KIND_BY_SOURCE[evidence.source]
            or binding.combined_result_sha256 != result.result_sha256
            or binding.requested_services != component.requested_services
            or binding.covered_services != component.covered_services
            or binding.window != component.window
            or binding.window != result.window
            or (
                binding.binding_kind.value == "GENERIC"
                and (
                    specialized is not None
                    or binding.binding_payload_sha256
                    != component.result_sha256
                )
            )
            or (
                binding.binding_kind.value != "GENERIC"
                and not isinstance(specialized, Mapping)
            )
        ):
            return False
        bindings.append(binding)
    if (
        len({item.binding_id for item in bindings}) != len(bindings)
        or len({item.environment_id for item in bindings}) != 1
        or sorted(item.component_result_sha256 for item in bindings)
        != sorted(item.result_sha256 for item in components)
        or set().union(*(set(item.requested_services) for item in components))
        != set(result.requested_services)
    ):
        return False
    if result.status.value in _SUCCESS_STATUSES and set().union(
        *(set(item.covered_services) for item in components)
    ) != set(result.covered_services):
        return False
    return True


def _failed_object_is_explicit(item: Any) -> bool:
    if item is None:
        return False
    if item.payload.get("schema_version") == (
        "ecomsre.product.capability-evidence-observation.v0232"
    ):
        return True
    result = _validated_connector_result(item.payload)
    return result is not None and bool(result.safe_error_code)


def _has_one_healthy_checkout_runtime_record(
    connector_result: Mapping[str, Any],
) -> bool:
    records = connector_result.get("records")
    if not isinstance(records, (list, tuple)) or len(records) != 1:
        return False
    record = records[0]
    return (
        isinstance(record, Mapping)
        and record.get("service") == "checkout"
        and record.get("state") == "RUNNING"
        and record.get("healthy") is True
        and record.get("restart_count") == 0
    )


def _derived_dispositions(
    bundle: EvidenceBundleV1,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    successful: list[str] = []
    failed: list[str] = []
    for item in bundle.objects:
        if item.payload.get("schema_version") == (
            "ecomsre.product.capability-evidence-observation.v0232"
        ):
            failed.append(item.evidence_ref)
            continue
        result = _validated_connector_result(item.payload)
        if result is None:
            continue
        if result.status.value in _SUCCESS_STATUSES:
            successful.append(item.evidence_ref)
        elif result.status.value.startswith("FAILURE_"):
            failed.append(item.evidence_ref)
    return tuple(sorted(successful)), tuple(sorted(failed))


def _typed_binding(
    *,
    bundle: EvidenceBundleV1,
    evidence_ref: str | None,
    binding_kind: str,
) -> tuple[ConnectorEvidenceBindingV0232, Mapping[str, Any]] | None:
    if evidence_ref is None:
        return None
    evidence = next(
        (item for item in bundle.objects if item.evidence_ref == evidence_ref),
        None,
    )
    if evidence is None:
        return None
    expected_source = (
        EvidenceSourceV22.LOGS
        if binding_kind == "OPENSEARCH_PROFILE"
        else EvidenceSourceV22.RUNTIME
    )
    if evidence.source is not expected_source:
        return None
    entries = evidence.payload.get("connector_bindings_v0232")
    if not isinstance(entries, (list, tuple)):
        return None
    matches: list[tuple[ConnectorEvidenceBindingV0232, Mapping[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        generic_payload = entry.get("connector_binding")
        specialized_payload = entry.get("binding_payload")
        if not isinstance(generic_payload, Mapping) or not isinstance(
            specialized_payload,
            Mapping,
        ):
            continue
        try:
            generic = ConnectorEvidenceBindingV0232.model_validate(generic_payload)
        except (TypeError, ValueError):
            continue
        if (
            generic.binding_kind.value == binding_kind
            and generic.source is expected_source
        ):
            matches.append((generic, specialized_payload))
    if len(matches) != 1:
        return None
    return matches[0]


def _profile_binding_valid(
    bundle: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
) -> bool:
    resolved = _typed_binding(
        bundle=bundle,
        evidence_ref=index.open_search_profile_binding_ref,
        binding_kind="OPENSEARCH_PROFILE",
    )
    if resolved is None:
        return False
    generic, payload = resolved
    evidence = next(
        item
        for item in bundle.objects
        if item.evidence_ref == index.open_search_profile_binding_ref
    )
    connector_result = _validated_connector_result(evidence.payload)
    if connector_result is None:
        return False
    try:
        specialized = OpenSearchProfileEvidenceBindingV0232.model_validate(payload)
    except (TypeError, ValueError):
        return False
    return (
        generic.binding_payload_sha256 == specialized.binding_sha256
        and generic.connector_kind is ConnectorKindV1.OPENSEARCH
        and generic.component_result_sha256
        == specialized.connector_result_sha256
        and generic.incident_id == index.incident_id
        and generic.action_id == evidence.action_id
        and generic.combined_result_sha256 == connector_result.result_sha256
        and generic.window == specialized.query_window
        and generic.requested_services == ("checkout",)
        and generic.covered_services == ("checkout",)
        and connector_result.source is EvidenceSourceV22.LOGS
        and connector_result.status.value in _SUCCESS_STATUSES
        and connector_result.result_sha256 == specialized.connector_result_sha256
        and connector_result.requested_services == ("checkout",)
        and connector_result.covered_services == ("checkout",)
        and connector_result.window == specialized.query_window
    )


def _runtime_binding_valid(
    bundle: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
) -> bool:
    resolved = _typed_binding(
        bundle=bundle,
        evidence_ref=index.runtime_snapshot_binding_ref,
        binding_kind="RUNTIME_SNAPSHOT",
    )
    if resolved is None:
        return False
    generic, payload = resolved
    evidence = next(
        item
        for item in bundle.objects
        if item.evidence_ref == index.runtime_snapshot_binding_ref
    )
    connector_result = _validated_connector_result(evidence.payload)
    if connector_result is None:
        return False
    try:
        specialized = RuntimeSnapshotEvidenceBindingV0232.model_validate(payload)
    except (TypeError, ValueError):
        return False
    return (
        generic.binding_payload_sha256 == specialized.binding_sha256
        and generic.connector_kind is ConnectorKindV1.PILOT_RUNTIME
        and generic.environment_id
        == specialized.runtime_snapshot_environment_id
        and generic.component_result_sha256
        == specialized.connector_result_sha256
        and generic.incident_id == index.incident_id
        and generic.action_id == evidence.action_id
        and generic.combined_result_sha256 == connector_result.result_sha256
        and generic.window == specialized.query_window
        and generic.requested_services == specialized.requested_services
        and generic.covered_services == specialized.covered_services
        and specialized.age_at_query_seconds <= specialized.maximum_age_seconds
        and connector_result.source is EvidenceSourceV22.RUNTIME
        and connector_result.status.value == "SUCCESS_NONEMPTY"
        and connector_result.result_sha256 == specialized.connector_result_sha256
        and connector_result.requested_services == ("checkout",)
        and connector_result.covered_services == ("checkout",)
        and connector_result.window == specialized.query_window
        and _has_one_healthy_checkout_runtime_record(
            connector_result.model_dump(mode="json")
        )
    )


def _required_provenance_valid(
    bundle: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
) -> bool:
    required = tuple(
        item
        for item in bundle.objects
        if item.source in _REQUIRED_SOURCES
        and item.payload.get("schema_version")
        != "ecomsre.product.capability-evidence-observation.v0232"
    )
    if {item.source for item in required} != _REQUIRED_SOURCES or any(
        not _generic_provenance_valid(evidence=item, index=index)
        for item in required
    ):
        return False
    resolved = _typed_binding(
        bundle=bundle,
        evidence_ref=index.runtime_snapshot_binding_ref,
        binding_kind="RUNTIME_SNAPSHOT",
    )
    if resolved is None:
        return False
    try:
        runtime = RuntimeSnapshotEvidenceBindingV0232.model_validate(resolved[1])
    except (TypeError, ValueError):
        return False
    environment_ids: set[str] = set()
    for evidence in required:
        for entry in evidence.payload["connector_bindings_v0232"]:
            try:
                generic = ConnectorEvidenceBindingV0232.model_validate(
                    entry["connector_binding"]
                )
            except (KeyError, TypeError, ValueError):
                return False
            environment_ids.add(generic.environment_id)
    return environment_ids == {runtime.runtime_snapshot_environment_id}


def _limitations_exactly_bound(
    *,
    diagnosis: DiagnosisResultV1,
    bundle: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
) -> bool:
    bindings = {
        item.limitation_code: item
        for item in index.capability_limitation_bindings
    }
    if len(bindings) != len(index.capability_limitation_bindings) or set(
        bindings
    ) != set(diagnosis.capability_limitations):
        return False
    objects = {item.evidence_ref: item for item in bundle.objects}
    for binding in bindings.values():
        evidence = objects.get(binding.evidence_ref)
        if evidence is None:
            return False
        if binding.connector_result_sha256 is not None:
            result = _validated_connector_result(evidence.payload)
            if result is None or result.result_sha256 != (
                binding.connector_result_sha256
            ):
                return False
            if binding.category.value == "QUERY_FAILURE" and (
                not result.status.value.startswith("FAILURE_")
                or result.safe_error_code != binding.safe_error_code
            ):
                return False
            if binding.category.value == "COVERAGE_GAP" and (
                result.status.value not in _SUCCESS_STATUSES
                or set(result.covered_services) == set(result.requested_services)
            ):
                return False
            if binding.category.value == "RUNTIME_AUTHORITY_UNAVAILABLE" and (
                evidence.source is not EvidenceSourceV22.RUNTIME
                or result.source is not EvidenceSourceV22.RUNTIME
            ):
                return False
        else:
            if (
                evidence.payload.get("observation_sha256")
                != binding.capability_observation_sha256
                or evidence.payload.get("reason_code") != binding.limitation_code
                or evidence.payload.get("source") != binding.source.value
                or evidence.source is not binding.source
            ):
                return False
    return True


class NoFaultEvidenceAssessmentV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.nofault-evidence-assessment.v0232"
    ] = "ecomsre.product.nofault-evidence-assessment.v0232"
    terminal: NoFaultMeasuredTerminalV0232
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    diagnosis_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reasons: tuple[str, ...]
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_sealed_assessment(self) -> "NoFaultEvidenceAssessmentV0232":
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("No-Fault v0.2.3.2 reasons are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("No-Fault v0.2.3.2 assessment digest differs")
        return self


def score_nofault_evidence_v0232(
    *,
    diagnosis: DiagnosisResultV1,
    bundle: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
    decision_trace: DiagnosisDecisionTraceV0232,
) -> NoFaultEvidenceAssessmentV0232:
    reasons: set[str] = set()
    objects_by_ref = {item.evidence_ref: item for item in bundle.objects}
    bundle_sha256 = semantic_sha256_v22(bundle.model_dump(mode="json"))
    identity_valid = (
        diagnosis.incident_id == bundle.incident_id == index.incident_id
        and diagnosis.diagnosis_id == bundle.diagnosis_id == index.diagnosis_id
        and decision_trace.incident_id == diagnosis.incident_id
        and decision_trace.diagnosis_id == diagnosis.diagnosis_id
        and decision_trace.trace_sha256 == index.decision_trace_sha256
    )
    exact_refs = (
        len(objects_by_ref) == len(bundle.objects)
        and tuple(sorted(objects_by_ref)) == index.all_object_refs
        and index.all_object_sha256_by_ref
        == {key: objects_by_ref[key].object_sha256 for key in sorted(objects_by_ref)}
        and index.linked_support_refs == diagnosis.supporting_evidence_refs
        and index.linked_contradiction_refs
        == diagnosis.contradicting_evidence_refs
        and bundle.supporting_evidence_refs == diagnosis.supporting_evidence_refs
        and bundle.contradicting_evidence_refs
        == diagnosis.contradicting_evidence_refs
    )
    if not identity_valid or bundle_sha256 != index.evidence_bundle_sha256:
        reasons.add("EVIDENCE_INDEX_BINDING_INVALID")
    if not exact_refs:
        reasons.add("EVIDENCE_REFERENCE_UNRESOLVED")
    if any(
        _canonical_object_sha256(item.payload) != item.object_sha256
        for item in bundle.objects
    ):
        reasons.add("EVIDENCE_OBJECT_SHA256_UNRESOLVED")
    if any(
        item.payload.get("schema_version")
        != "ecomsre.product.capability-evidence-observation.v0232"
        and _validated_connector_result(item.payload) is None
        for item in bundle.objects
    ):
        reasons.add("CONNECTOR_RESULT_INVALID")
    if not _required_provenance_valid(bundle, index):
        reasons.add("CONNECTOR_PROVENANCE_INVALID")
    successful, failed = _derived_dispositions(bundle)
    if (
        successful != index.successful_source_refs
        or failed != index.failed_source_refs
    ):
        reasons.add("SOURCE_DISPOSITION_INDEX_INVALID")
    if not _profile_binding_valid(bundle, index):
        reasons.add("LOGS_PROFILE_BINDING_MISSING")
    if not _runtime_binding_valid(bundle, index):
        reasons.add("FRESH_HEALTHY_RUNTIME_MISSING")
    failed_objects = (objects_by_ref.get(reference) for reference in failed)
    if any(not _failed_object_is_explicit(item) for item in failed_objects):
        reasons.add("HIDDEN_CONNECTOR_FAILURE")
    limitations_bound = _limitations_exactly_bound(
        diagnosis=diagnosis,
        bundle=bundle,
        index=index,
    )
    if set(diagnosis.capability_limitations).intersection(
        decision_trace.novelty_gate_reason_codes
    ):
        reasons.add("ALGORITHMIC_REASON_MASQUERADES_AS_CAPABILITY")
    successful_sources = {
        objects_by_ref[reference].source
        for reference in successful
        if reference in objects_by_ref
    }
    classified_or_conflicting = diagnosis.terminal in {
        DiagnosisTerminalV1.CORE_KNOWN,
        DiagnosisTerminalV1.EXTENSION_KNOWN,
        DiagnosisTerminalV1.OPEN_WORLD,
        DiagnosisTerminalV1.CONFLICTING_EVIDENCE,
    }
    if classified_or_conflicting:
        reasons.add("FALSE_INCIDENT_TERMINAL")
    if diagnosis.terminal is DiagnosisTerminalV1.NO_INCIDENT:
        if diagnosis.capability_limitations:
            reasons.add("NOFAULT_DIAGNOSIS_LIMITATIONS_PRESENT")
        if not _REQUIRED_SOURCES.issubset(successful_sources):
            reasons.add("REQUIRED_SOURCE_COVERAGE_INSUFFICIENT")
    elif diagnosis.terminal is DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE:
        if not diagnosis.capability_limitations or not limitations_bound:
            reasons.add("CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED")

    if not reasons and diagnosis.terminal is DiagnosisTerminalV1.NO_INCIDENT:
        measured = NoFaultMeasuredTerminalV0232.FULLY_SUPPORTED
    elif (
        not reasons
        and diagnosis.terminal is DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE
        and limitations_bound
    ):
        measured = NoFaultMeasuredTerminalV0232.CAPABILITY_LIMITED
    else:
        measured = NoFaultMeasuredTerminalV0232.NOT_SUPPORTED
    body = {
        "schema_version": "ecomsre.product.nofault-evidence-assessment.v0232",
        "terminal": measured.value,
        "incident_id": diagnosis.incident_id,
        "diagnosis_id": diagnosis.diagnosis_id,
        "diagnosis_result_sha256": diagnosis.result_sha256,
        "evidence_bundle_sha256": bundle_sha256,
        "evidence_index_sha256": index.index_sha256,
        "decision_trace_sha256": decision_trace.trace_sha256,
        "reasons": tuple(sorted(reasons)),
    }
    return NoFaultEvidenceAssessmentV0232.model_validate(
        {**body, "result_sha256": semantic_sha256_v22(body)}
    )


__all__ = (
    "NOFAULT_CAPABILITY_LIMITED_V0232",
    "NOFAULT_FULLY_SUPPORTED_V0232",
    "NOFAULT_NOT_SUPPORTED_V0232",
    "NoFaultEvidenceAssessmentV0232",
    "NoFaultMeasuredTerminalV0232",
    "score_nofault_evidence_v0232",
)
