#!/usr/bin/env python3
"""Execute the one-shot Product v0.2.3.2.1 formal No-Fault episode."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, MutableMapping, Sequence, TypedDict

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    ConnectorEvidenceBindingV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.pilot.baseline_readiness_v021 import verify_queue_default_v021
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023
from ecomsre.product.pilot.formal_contract_v02321 import (
    FormalContractFreezeV02321,
    FormalPreExecutionReviewV02321,
    verify_formal_contract_freeze_v02321,
    verify_formal_pre_execution_review_v02321,
)
from ecomsre.product.pilot.formal_nofault_v02321 import (
    NOFAULT_ACCEPTANCE_COMPLETE_V02321,
    BaselineRestartProofV02321,
    FormalBlockerClosureV02321,
    FormalExecutionAdmissionV02321,
    FormalCloneReservationV02321,
    FormalExecutionBlockerV02321,
    FormalProgressV02321,
    FormalTrafficBlockerV02321,
    FormalTrafficConsumptionV02321,
    FormalTrafficDispatchCheckpointV02321,
    FormalTrafficObservationCheckpointV02321,
    FormalTrafficResultV02321,
    FreshRuntimeSnapshotProofV02321,
    NoFaultAcceptanceResultV02321,
    RuntimeAuthorityProofV02321,
    measured_terminal_v02321,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    CheckoutTrafficContractV0232,
    CheckoutTransactionObservationV0232,
    HealthyTrafficExecutionV0232,
    HealthyTrafficProfileV0232,
    HealthyTrafficRunV0232,
    HealthyTrafficRunnerV0232,
    IncidentTrafficBindingV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
    _queue_counts,
    _request_json,
    _require_clean_head,
    _restart_snapshot,
    _wait_job,
)
from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    _attempt_context,
    _database_counts,
    _load_persisted_bindings,
    _runtime_snapshot,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultEvidenceAssessmentV0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.pilot.product_state_clone_v02321 import (
    FormalProductPoststateV02321,
    FormalStateCloneReportV02321,
    PreflightStateCloneReportV02321,
    admit_formal_product_poststate_v02321,
)
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateSourceV0232,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre.product.pilot.runtime_session_v0231 import (
    BaselineRestartProofV0231,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    load_traffic_profile_v0232,
)
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    DemoCleanupObservationV02321,
    ProductCleanupObservationV02321,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    build_traffic_harness_typed_request_plan_v02321,
    materialize_planned_request_v02321,
)
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    load_bundle,
    write_private_json,
)
from scripts.product_v0231.run_live_authority_restart import (
    _authority_proof,
    _read_only_database_counts,
    _rotate_runtime_snapshot_v0231,
)
from scripts.product_v02321.run_harness_contract_preflight import (
    _load_successor_campaign_sha256,
)
from scripts.product_v02321.run_state_clone import (
    _admit_state,
    _bind_existing_clone,
    create_formal_state_clone_v02321,
)
from scripts.product_v02321.run_traffic_preflight import (
    _checkout_runtime,
    _database_owner_count,
    _require_preserved_runtime_root_v02321,
)
from scripts.product_v0232.run_state_clone import (
    BASELINE_ID_V0232,
    BASELINE_SHA256_V0232,
    ENVIRONMENT_ID_V0232,
    PILOT_RUNTIME_AUTHORITY_SHA256_V0232,
    PROFILE_SHA256_V0232,
    RUNTIME_CONNECTOR_BINDING_SHA256_V0232,
)


_ENDPOINT_V02321 = "http://127.0.0.1:18080/api/checkout"
_PRIVATE_LOCATOR_V02321 = ".local/product-v02321/formal"
_RESERVATION_LOCATOR_V02321 = ".local/product-v02321/formal-reservation.json"
_SOURCE_BRANCH = "codex/product-v023-fresh-baseline-nofault"
_PRIVATE_ACCEPTANCE_BRANCH = "codex/product-v0231-runtime-authority-nofault-successor"
_SOURCE_LOCATOR = (
    ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/product"
)
_PRIVATE_ACCEPTANCE_LOCATOR = (
    ".local/product-v0231/continuation-sessions/session-1/acceptance.json"
)
_PUBLICATION_OUTPUTS = (
    "docs/analysis/product-v02321-baseline-restart.json",
    "docs/analysis/product-v02321-formal-traffic.json",
    "docs/analysis/product-v02321-fresh-runtime-snapshot.json",
    "docs/analysis/product-v02321-product-state-clone-formal.json",
    "docs/analysis/product-v02321-progress.json",
    "docs/analysis/product-v02321-runtime-authority.json",
    "docs/results/product-v02321-nofault-acceptance.json",
)
_PRIVATE_PUBLICATION_FILES = (
    "acceptance.json",
    "admission.json",
    "assessment.json",
    "baseline-restart.json",
    "decision-trace.json",
    "diagnosis-job-completion.json",
    "diagnosis-job.json",
    "diagnosis.json",
    "evidence-bundle.json",
    "evidence-index.json",
    "formal-poststate.json",
    "formal-traffic.json",
    "fresh-runtime-snapshot.json",
    "incident-traffic-binding.json",
    "incident.json",
    "runtime-authority.json",
    "source-poststate.json",
    "traffic-consumption.json",
    "traffic-execution.json",
)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Product v0.2.3.2.1 JSON object differs: {path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_path(
    *, root: Path, path: Path, directory: bool, label: str
) -> Path:
    anchor = Path(root).resolve(strict=True)
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(anchor)
    except ValueError as error:
        raise ValueError(f"Product v0.2.3.2.1 {label} escapes root") from error
    current = anchor
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise ValueError(f"Product v0.2.3.2.1 {label} is a symlink")
    expected = candidate.is_dir() if directory else candidate.is_file()
    if not expected:
        kind = "directory" if directory else "file"
        raise ValueError(f"Product v0.2.3.2.1 {label} is not a regular {kind}")
    return candidate


def _load_canonical_object_v02321(
    *, root: Path, path: Path, label: str
) -> dict[str, Any]:
    regular = _require_regular_path(
        root=root,
        path=path,
        directory=False,
        label=label,
    )
    raw = regular.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise ValueError(f"Product v0.2.3.2.1 {label} is not canonical")
    return value


def _load_canonical_reservation_v02321(root: Path) -> FormalCloneReservationV02321:
    payload = _load_canonical_object_v02321(
        root=root,
        path=root / _RESERVATION_LOCATOR_V02321,
        label="formal reservation",
    )
    return FormalCloneReservationV02321.model_validate(payload)


def _write_public_once(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(dict(payload))
    if path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.2.1 output is a symlink: {path.name}")
    if path.exists():
        if not path.is_file() or path.read_bytes() != encoded:
            raise FileExistsError(f"Product v0.2.3.2.1 output differs: {path.name}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_public(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Product v0.2.3.2.1 output is a symlink: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _freeze_publication_bundle(
    *,
    project_root: Path,
    private_root: Path,
    execution_head: str,
    private_files: Mapping[str, bytes],
    public_files: Mapping[str, bytes],
) -> dict[str, Any]:
    if tuple(sorted(private_files)) != _PRIVATE_PUBLICATION_FILES:
        raise ValueError("Product v0.2.3.2.1 private publication set differs")
    if tuple(sorted(public_files)) != _PUBLICATION_OUTPUTS:
        raise ValueError("Product v0.2.3.2.1 publication output set differs")
    private_entries = []
    for path, payload in sorted(private_files.items()):
        entry = {
            "path": path,
            "mode": "CREATE_EXACT" if path == "acceptance.json" else "ASSERT_EXACT",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "content_utf8": payload.decode("utf-8"),
        }
        if path != "acceptance.json":
            target = _require_regular_path(
                root=private_root,
                path=private_root / path,
                directory=False,
                label=f"private publication input {path}",
            )
            if target.read_bytes() != payload:
                raise ValueError("Product v0.2.3.2.1 private publication input differs")
        private_entries.append(entry)
    public_entries = []
    for path, payload in sorted(public_files.items()):
        entry = {
            "path": path,
            "mode": (
                "REPLACE"
                if path == "docs/analysis/product-v02321-progress.json"
                else "CREATE_EXACT"
            ),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "content_utf8": payload.decode("utf-8"),
        }
        if entry["mode"] == "REPLACE":
            entry["expected_previous_sha256"] = _sha256(project_root / path)
        public_entries.append(entry)
    body = {
        "schema_version": "ecomsre.product.formal-publication.v02321",
        "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V02321,
        "execution_head": execution_head,
        "private_files": private_entries,
        "public_files": public_entries,
    }
    intent = {**body, "intent_sha256": semantic_sha256_v22(body)}
    write_private_json(
        private_root / "publication-intent.json",
        intent,
        create_once=True,
    )
    return _freeze_publication_bundle_from_intent(private_root=private_root)


def _validated_publication_intent_v02321(
    *, private_root: Path
) -> tuple[dict[str, Any], str]:
    raw = _load_canonical_object_v02321(
        root=private_root,
        path=private_root / "publication-intent.json",
        label="publication intent",
    )
    body = dict(raw)
    supplied = body.pop("intent_sha256", None)
    if (
        not isinstance(supplied, str)
        or supplied != semantic_sha256_v22(body)
        or body.get("schema_version") != "ecomsre.product.formal-publication.v02321"
        or body.get("terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V02321
    ):
        raise ValueError("Product v0.2.3.2.1 publication intent differs")
    return body, supplied


def _freeze_publication_bundle_from_intent(*, private_root: Path) -> dict[str, Any]:
    body, _intent_sha256 = _validated_publication_intent_v02321(
        private_root=private_root
    )
    bundle = {**body, "bundle_sha256": semantic_sha256_v22(body)}
    write_private_json(
        private_root / "publication-bundle.json",
        bundle,
        create_once=True,
    )
    return bundle


def _publication_entry_bytes(entry: Mapping[str, Any]) -> bytes:
    payload = str(entry.get("content_utf8", "")).encode("utf-8")
    if len(payload) != entry.get("size_bytes") or hashlib.sha256(
        payload
    ).hexdigest() != entry.get("sha256"):
        raise ValueError("Product v0.2.3.2.1 publication entry differs")
    return payload


def _validate_publication_payload(path: str, payload: bytes) -> dict[str, Any]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed) != payload:
        raise ValueError("Product v0.2.3.2.1 publication payload differs")
    if path == "acceptance.json" or path == (
        "docs/results/product-v02321-nofault-acceptance.json"
    ):
        NoFaultAcceptanceResultV02321.model_validate(parsed)
    elif path == "admission.json":
        FormalExecutionAdmissionV02321.model_validate(parsed)
    elif path == "formal-poststate.json":
        FormalProductPoststateV02321.model_validate(parsed)
    elif path == "source-poststate.json":
        ProductStateSourceV0232.model_validate(parsed)
    elif path == "traffic-consumption.json":
        FormalTrafficConsumptionV02321.model_validate(parsed)
    elif path == "traffic-execution.json":
        HealthyTrafficExecutionV0232.model_validate(parsed)
    elif path == "formal-traffic.json":
        FormalTrafficResultV02321.model_validate(parsed)
    elif path == "fresh-runtime-snapshot.json":
        FreshRuntimeSnapshotProofV02321.model_validate(parsed)
    elif path == "incident.json":
        IncidentRecordV1.model_validate(parsed)
    elif path == "incident-traffic-binding.json":
        IncidentTrafficBindingV0232.model_validate(parsed)
    elif path in {"diagnosis-job.json", "diagnosis-job-completion.json"}:
        ProductJobRecordV1.model_validate(parsed)
    elif path == "diagnosis.json":
        DiagnosisResultV1.model_validate(parsed)
    elif path == "evidence-bundle.json":
        EvidenceBundleV1.model_validate(parsed)
    elif path == "evidence-index.json":
        DiagnosisEvidenceIndexV0232.model_validate(parsed)
    elif path == "decision-trace.json":
        DiagnosisDecisionTraceV0232.model_validate(parsed)
    elif path == "assessment.json":
        NoFaultEvidenceAssessmentV0232.model_validate(parsed)
    elif path in {
        "runtime-authority.json",
        "docs/analysis/product-v02321-runtime-authority.json",
    }:
        RuntimeAuthorityProofV02321.model_validate(parsed)
    elif path in {
        "baseline-restart.json",
        "docs/analysis/product-v02321-baseline-restart.json",
    }:
        BaselineRestartProofV02321.model_validate(parsed)
    elif path == "docs/analysis/product-v02321-formal-traffic.json":
        FormalTrafficResultV02321.model_validate(parsed)
    elif path == "docs/analysis/product-v02321-fresh-runtime-snapshot.json":
        FreshRuntimeSnapshotProofV02321.model_validate(parsed)
    elif path == ("docs/analysis/product-v02321-product-state-clone-formal.json"):
        FormalStateCloneReportV02321.model_validate(parsed)
    elif path == "docs/analysis/product-v02321-progress.json":
        FormalProgressV02321.model_validate(parsed)
    else:
        raise ValueError("Product v0.2.3.2.1 publication path differs")
    return parsed


def _evidence_runtime_binding_v02321(
    *,
    evidence: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
) -> tuple[ConnectorEvidenceBindingV0232, RuntimeSnapshotEvidenceBindingV0232]:
    reference = index.runtime_snapshot_binding_ref
    matches = tuple(item for item in evidence.objects if item.evidence_ref == reference)
    if reference is None or len(matches) != 1:
        raise ValueError("Product v0.2.3.2.1 Runtime Evidence reference differs")
    entries = matches[0].payload.get("connector_bindings_v0232")
    if not isinstance(entries, (list, tuple)):
        raise ValueError("Product v0.2.3.2.1 Runtime Evidence bindings differ")
    resolved: list[
        tuple[ConnectorEvidenceBindingV0232, RuntimeSnapshotEvidenceBindingV0232]
    ] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        generic_payload = entry.get("connector_binding")
        specialized_payload = entry.get("binding_payload")
        if not isinstance(generic_payload, Mapping) or not isinstance(
            specialized_payload, Mapping
        ):
            continue
        try:
            generic = ConnectorEvidenceBindingV0232.model_validate_json(
                json.dumps(generic_payload)
            )
            specialized = RuntimeSnapshotEvidenceBindingV0232.model_validate_json(
                json.dumps(specialized_payload)
            )
        except (TypeError, ValueError):
            continue
        if generic.binding_kind.value == "RUNTIME_SNAPSHOT":
            resolved.append((generic, specialized))
    if len(resolved) != 1:
        raise ValueError("Product v0.2.3.2.1 Runtime Evidence binding differs")
    return resolved[0]


def _evidence_environment_ids_v02321(
    evidence: EvidenceBundleV1,
) -> set[str]:
    environment_ids: set[str] = set()
    binding_count = 0
    for item in evidence.objects:
        entries = item.payload.get("connector_bindings_v0232")
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(
                entry.get("connector_binding"), Mapping
            ):
                raise ValueError("Product v0.2.3.2.1 generic Evidence binding differs")
            generic = ConnectorEvidenceBindingV0232.model_validate_json(
                json.dumps(entry["connector_binding"])
            )
            environment_ids.add(generic.environment_id)
            binding_count += 1
    if binding_count == 0:
        raise ValueError("Product v0.2.3.2.1 generic Evidence binding is absent")
    return environment_ids


def _require_publication_cross_bindings_v02321(
    *,
    private_payloads: Mapping[str, dict[str, Any]],
    public_payloads: Mapping[str, dict[str, Any]],
) -> NoFaultAcceptanceResultV02321:
    acceptance = NoFaultAcceptanceResultV02321.model_validate(
        private_payloads["acceptance.json"]
    )
    public_acceptance = NoFaultAcceptanceResultV02321.model_validate(
        public_payloads["docs/results/product-v02321-nofault-acceptance.json"]
    )
    admission = FormalExecutionAdmissionV02321.model_validate(
        private_payloads["admission.json"]
    )
    clone = FormalStateCloneReportV02321.model_validate(
        public_payloads["docs/analysis/product-v02321-product-state-clone-formal.json"]
    )
    source_poststate = ProductStateSourceV0232.model_validate(
        private_payloads["source-poststate.json"]
    )
    formal_poststate = FormalProductPoststateV02321.model_validate(
        private_payloads["formal-poststate.json"]
    )
    consumption = FormalTrafficConsumptionV02321.model_validate(
        private_payloads["traffic-consumption.json"]
    )
    traffic_execution = HealthyTrafficExecutionV0232.model_validate(
        private_payloads["traffic-execution.json"]
    )
    traffic = FormalTrafficResultV02321.model_validate(
        private_payloads["formal-traffic.json"]
    )
    fresh_runtime = FreshRuntimeSnapshotProofV02321.model_validate(
        private_payloads["fresh-runtime-snapshot.json"]
    )
    incident = IncidentRecordV1.model_validate(private_payloads["incident.json"])
    incident_binding = IncidentTrafficBindingV0232.model_validate(
        private_payloads["incident-traffic-binding.json"]
    )
    queued_diagnosis_job = ProductJobRecordV1.model_validate(
        private_payloads["diagnosis-job.json"]
    )
    diagnosis_job = ProductJobRecordV1.model_validate(
        private_payloads["diagnosis-job-completion.json"]
    )
    diagnosis = DiagnosisResultV1.model_validate(private_payloads["diagnosis.json"])
    evidence = EvidenceBundleV1.model_validate(private_payloads["evidence-bundle.json"])
    index = DiagnosisEvidenceIndexV0232.model_validate(
        private_payloads["evidence-index.json"]
    )
    trace = DiagnosisDecisionTraceV0232.model_validate(
        private_payloads["decision-trace.json"]
    )
    assessment = NoFaultEvidenceAssessmentV0232.model_validate(
        private_payloads["assessment.json"]
    )
    runtime_authority = RuntimeAuthorityProofV02321.model_validate(
        private_payloads["runtime-authority.json"]
    )
    baseline_restart = BaselineRestartProofV02321.model_validate(
        private_payloads["baseline-restart.json"]
    )
    progress = FormalProgressV02321.model_validate(
        public_payloads["docs/analysis/product-v02321-progress.json"]
    )
    public_traffic = FormalTrafficResultV02321.model_validate(
        public_payloads["docs/analysis/product-v02321-formal-traffic.json"]
    )
    public_fresh_runtime = FreshRuntimeSnapshotProofV02321.model_validate(
        public_payloads["docs/analysis/product-v02321-fresh-runtime-snapshot.json"]
    )
    diagnosis_from_job = (
        DiagnosisResultV1.model_validate(diagnosis_job.result)
        if isinstance(diagnosis_job.result, dict)
        else None
    )
    evidence_sha256 = semantic_sha256_v22(evidence.model_dump(mode="json"))
    rescored_assessment = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=evidence,
        index=index,
        decision_trace=trace,
    )
    evidence_runtime_generic, evidence_runtime = _evidence_runtime_binding_v02321(
        evidence=evidence,
        index=index,
    )
    evidence_environment_ids = _evidence_environment_ids_v02321(evidence)
    restart_snapshot = baseline_restart.inner_proof.inner_proof.after
    expected_incident_key = f"product-v02321-nofault-{admission.admission_sha256[:16]}"
    exact = (
        acceptance == public_acceptance
        and private_payloads["runtime-authority.json"]
        == public_payloads["docs/analysis/product-v02321-runtime-authority.json"]
        and private_payloads["baseline-restart.json"]
        == public_payloads["docs/analysis/product-v02321-baseline-restart.json"]
        and traffic == public_traffic
        and fresh_runtime == public_fresh_runtime
        and acceptance.execution_head == admission.execution_head
        and acceptance.admission_sha256 == admission.admission_sha256
        and clone.formal_admission_sha256 == admission.admission_sha256
        and clone.formal_clone_plan_sha256 == admission.formal_clone_plan_sha256
        and clone.destination_locator == admission.formal_clone_destination_locator
        and admission.source_state_sha256 == clone.source_state.source_sha256
        and acceptance.formal_clone_report_sha256 == clone.report_sha256
        and acceptance.formal_poststate_sha256 == formal_poststate.poststate_sha256
        and acceptance.source_poststate_sha256 == source_poststate.source_sha256
        and formal_poststate.state_locator == clone.destination_locator
        and source_poststate == clone.source_state
        and formal_poststate.environment_id
        == clone.destination_state.source_environment_id
        and formal_poststate.active_baseline_id
        == clone.destination_state.source_active_baseline_id
        and formal_poststate.active_baseline_sha256
        == clone.destination_state.source_active_baseline_sha256
        and formal_poststate.profile_sha256
        == clone.destination_state.source_profile_sha256
        and formal_poststate.counts.evidence_object_count
        > clone.destination_state.source_counts.evidence_object_count
        and acceptance.runtime_authority_proof_sha256 == runtime_authority.proof_sha256
        and acceptance.baseline_restart_proof_sha256 == baseline_restart.proof_sha256
        and runtime_authority.execution_head == admission.execution_head
        and runtime_authority.admission_sha256 == admission.admission_sha256
        and runtime_authority.continuity_descriptor_sha256
        == admission.runtime_continuity_descriptor_sha256
        and runtime_authority.inner_proof.components["pilot_runtime_authority_sha256"][
            "observed"
        ]
        == PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        and runtime_authority.inner_proof.components["connector_binding_sha256"][
            "observed"
        ]
        == RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        and baseline_restart.execution_head == admission.execution_head
        and baseline_restart.admission_sha256 == admission.admission_sha256
        and baseline_restart.active_baseline_id
        == clone.destination_state.source_active_baseline_id
        and baseline_restart.active_baseline_sha256
        == clone.destination_state.source_active_baseline_sha256
        and baseline_restart.active_profile_sha256
        == clone.destination_state.source_profile_sha256
        and baseline_restart.inner_proof.inner_proof.after.environment_id
        == formal_poststate.environment_id
        and traffic.admission_sha256 == admission.admission_sha256
        and consumption.admission_sha256 == admission.admission_sha256
        and consumption.execution_head == admission.execution_head
        and consumption.episode_started_at == traffic.episode_started_at
        and consumption.traffic_contract_sha256 == traffic_execution.run.contract_sha256
        and consumption.formal_profile_sha256 == traffic_execution.run.profile_sha256
        and traffic.consumption_sha256 == consumption.consumption_sha256
        and traffic.execution == traffic_execution
        and acceptance.formal_traffic_result_sha256 == traffic.result_sha256
        and fresh_runtime.execution_head == admission.execution_head
        and fresh_runtime.traffic_result_sha256 == traffic.result_sha256
        and fresh_runtime.snapshot.observed_at >= traffic.episode_ended_at
        and fresh_runtime.snapshot.environment_id == formal_poststate.environment_id
        and fresh_runtime.runtime_authority_sha256
        == runtime_authority.inner_proof.components["pilot_runtime_authority_sha256"][
            "observed"
        ]
        and fresh_runtime.runtime_continuity_descriptor_sha256
        == runtime_authority.continuity_descriptor_sha256
        and fresh_runtime.connector_binding_sha256
        == runtime_authority.inner_proof.components["connector_binding_sha256"][
            "observed"
        ]
        and acceptance.fresh_runtime_snapshot_proof_sha256 == fresh_runtime.proof_sha256
        and evidence_environment_ids == {formal_poststate.environment_id}
        and evidence_runtime_generic.environment_id == formal_poststate.environment_id
        and evidence_runtime_generic.binding_payload_sha256
        == evidence_runtime.binding_sha256
        and evidence_runtime.runtime_snapshot_sha256
        == fresh_runtime.snapshot.snapshot_sha256
        and evidence_runtime.runtime_snapshot_observed_at
        == fresh_runtime.snapshot.observed_at
        and evidence_runtime.runtime_snapshot_environment_id
        == fresh_runtime.snapshot.environment_id
        and evidence_runtime.runtime_snapshot_authority_sha256
        == fresh_runtime.snapshot.authority_sha256
        and evidence_runtime.pilot_runtime_authority_sha256
        == fresh_runtime.runtime_authority_sha256
        and evidence_runtime.read_authority_sha256
        == runtime_authority.inner_proof.components["read_authority_sha256"]["observed"]
        and evidence_runtime.connector_binding_sha256
        == fresh_runtime.connector_binding_sha256
        and evidence_runtime.query_window.ended_at == incident.diagnosis_observed_at
        and incident_binding.incident_id == incident.incident_id
        and incident_binding.traffic_execution_sha256
        == traffic_execution.execution_sha256
        and incident_binding.contract_sha256 == traffic_execution.run.contract_sha256
        and incident_binding.formal_profile_sha256
        == traffic_execution.run.profile_sha256
        and incident_binding.episode_started_at == traffic.episode_started_at
        and incident_binding.episode_ended_at == traffic.episode_ended_at
        and incident.started_at == traffic.episode_started_at
        and incident.ended_at == fresh_runtime.snapshot.observed_at
        and incident.diagnosis_observed_at == incident.ended_at
        and incident.created_at >= fresh_runtime.snapshot.observed_at
        and incident.environment_id == formal_poststate.environment_id
        and incident.baseline_id == formal_poststate.active_baseline_id
        and incident.baseline_sha256 == formal_poststate.active_baseline_sha256
        and incident.external_incident_key == expected_incident_key
        and incident.candidate_logical_services == ("checkout",)
        and len(incident.candidate_service_ids) == 1
        and incident.labels == {"fault": "none"}
        and incident.service_identity_sha256 == restart_snapshot.service_identity_sha256
        and incident.source_capability_sha256 == restart_snapshot.capability_sha256
        and acceptance.incident_traffic_binding_sha256
        == incident_binding.binding_sha256
        and queued_diagnosis_job.status is ProductJobStatusV1.PENDING
        and diagnosis_job.status is ProductJobStatusV1.SUCCEEDED
        and queued_diagnosis_job.job_type is ProductJobTypeV1.DIAGNOSIS
        and diagnosis_job.job_type is ProductJobTypeV1.DIAGNOSIS
        and queued_diagnosis_job.job_id == diagnosis_job.job_id
        and queued_diagnosis_job.idempotency_key == diagnosis_job.idempotency_key
        and diagnosis_job.idempotency_key == f"diagnosis:{incident.incident_id}"
        and queued_diagnosis_job.payload == {"incident_id": incident.incident_id}
        and diagnosis_job.payload == queued_diagnosis_job.payload
        and queued_diagnosis_job.result is None
        and queued_diagnosis_job.safe_error_code is None
        and queued_diagnosis_job.claimed_by is None
        and queued_diagnosis_job.lease_expires_at is None
        and queued_diagnosis_job.attempt_count == 0
        and queued_diagnosis_job.created_at == queued_diagnosis_job.updated_at
        and diagnosis_job.created_at == queued_diagnosis_job.created_at
        and diagnosis_job.updated_at >= diagnosis_job.created_at
        and diagnosis_job.safe_error_code is None
        and diagnosis_job.claimed_by is None
        and diagnosis_job.lease_expires_at is None
        and diagnosis_job.attempt_count == 1
        and diagnosis_from_job == diagnosis
        and acceptance.incident_id
        == incident.incident_id
        == diagnosis.incident_id
        == evidence.incident_id
        == index.incident_id
        == trace.incident_id
        == assessment.incident_id
        and acceptance.diagnosis_id
        == diagnosis.diagnosis_id
        == evidence.diagnosis_id
        == index.diagnosis_id
        == trace.diagnosis_id
        == assessment.diagnosis_id
        and acceptance.diagnosis_result_sha256 == diagnosis.result_sha256
        and acceptance.evidence_bundle_sha256
        == evidence_sha256
        == assessment.evidence_bundle_sha256
        == index.evidence_bundle_sha256
        and acceptance.evidence_index_sha256
        == index.index_sha256
        == assessment.evidence_index_sha256
        and acceptance.decision_trace_sha256
        == trace.trace_sha256
        == index.decision_trace_sha256
        == assessment.decision_trace_sha256
        and acceptance.assessment_sha256 == assessment.result_sha256
        and assessment.diagnosis_result_sha256 == diagnosis.result_sha256
        and assessment == rescored_assessment
        and acceptance.source_assessment_terminal == assessment.terminal
        and acceptance.ending_incident_count == formal_poststate.counts.incident_count
        and acceptance.ending_diagnosis_count == formal_poststate.counts.diagnosis_count
        and acceptance.fault_family_count == formal_poststate.counts.fault_family_count
        and acceptance.knowledge_artifact_count
        == formal_poststate.counts.knowledge_artifact_count
        and acceptance.source_incident_count_after
        == source_poststate.source_counts.incident_count
        and acceptance.source_diagnosis_count_after
        == source_poststate.source_counts.diagnosis_count
        and progress.formal_state_clone_report_sha256 == clone.report_sha256
        and progress.formal_state_clone_sha256 == clone.clone.clone_sha256
        and progress.formal_traffic_result_sha256 == traffic.result_sha256
        and progress.fresh_runtime_snapshot_proof_sha256 == fresh_runtime.proof_sha256
        and progress.formal_poststate_sha256 == formal_poststate.poststate_sha256
        and progress.source_poststate_sha256 == source_poststate.source_sha256
        and progress.runtime_authority_proof_sha256 == runtime_authority.proof_sha256
        and progress.baseline_restart_proof_sha256 == baseline_restart.proof_sha256
        and progress.nofault_acceptance_result_sha256 == acceptance.result_sha256
        and progress.measured_terminal == acceptance.measured_terminal
        and progress.accepted_successor_incident_count
        == acceptance.successor_incident_delta
        and progress.successor_diagnosis_count == acceptance.successor_diagnosis_delta
        and public_payloads["docs/analysis/product-v02321-progress.json"].get(
            "source_state_sha256"
        )
        == clone.source_state.source_sha256
    )
    if not exact:
        raise ValueError("Product v0.2.3.2.1 publication cross-binding differs")
    return acceptance


def recover_formal_publication_v02321(
    *, project_root: Path
) -> NoFaultAcceptanceResultV02321:
    root = Path(project_root).resolve(strict=True)
    private_root = root / _PRIVATE_LOCATOR_V02321
    intent_body, intent_sha256 = _validated_publication_intent_v02321(
        private_root=private_root
    )
    publication_bundle = private_root / "publication-bundle.json"
    if not publication_bundle.exists() and not publication_bundle.is_symlink():
        _freeze_publication_bundle_from_intent(private_root=private_root)
    raw = _load_canonical_object_v02321(
        root=private_root,
        path=private_root / "publication-bundle.json",
        label="publication bundle",
    )
    body = dict(raw)
    supplied = body.pop("bundle_sha256", None)
    if (
        not isinstance(supplied, str)
        or supplied != semantic_sha256_v22(body)
        or body.get("schema_version") != "ecomsre.product.formal-publication.v02321"
        or body.get("terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V02321
        or supplied != intent_sha256
        or body != intent_body
    ):
        raise ValueError("Product v0.2.3.2.1 publication intent/bundle differs")
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != body.get("execution_head"):
        raise ValueError("Product v0.2.3.2.1 publication HEAD differs")
    reservation = _load_canonical_reservation_v02321(root)
    private_entries = body.get("private_files")
    entries = body.get("public_files")
    if (
        not isinstance(private_entries, list)
        or len(private_entries) != len(_PRIVATE_PUBLICATION_FILES)
        or not isinstance(entries, list)
        or len(entries) != len(_PUBLICATION_OUTPUTS)
    ):
        raise ValueError("Product v0.2.3.2.1 publication entries differ")
    private_paths = tuple(
        sorted(
            str(item.get("path")) for item in private_entries if isinstance(item, dict)
        )
    )
    public_paths = tuple(
        sorted(str(item.get("path")) for item in entries if isinstance(item, dict))
    )
    if (
        private_paths != _PRIVATE_PUBLICATION_FILES
        or public_paths != _PUBLICATION_OUTPUTS
    ):
        raise ValueError("Product v0.2.3.2.1 publication entries differ")

    private_payloads: dict[str, dict[str, Any]] = {}
    private_bytes: dict[str, bytes] = {}
    for raw_entry in private_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("Product v0.2.3.2.1 private publication entry differs")
        path = str(raw_entry.get("path"))
        expected_mode = "CREATE_EXACT" if path == "acceptance.json" else "ASSERT_EXACT"
        if (
            raw_entry.get("mode") != expected_mode
            or path not in _PRIVATE_PUBLICATION_FILES
        ):
            raise ValueError("Product v0.2.3.2.1 private publication mode differs")
        payload = _publication_entry_bytes(raw_entry)
        parsed = _validate_publication_payload(path, payload)
        target = private_root / path
        if target.exists() or target.is_symlink():
            regular = _require_regular_path(
                root=private_root,
                path=target,
                directory=False,
                label=f"private publication target {path}",
            )
            if regular.read_bytes() != payload:
                raise ValueError(
                    "Product v0.2.3.2.1 private publication target differs"
                )
        elif expected_mode == "ASSERT_EXACT":
            raise ValueError("Product v0.2.3.2.1 private evidence is absent")
        private_payloads[path] = parsed
        private_bytes[path] = payload

    bundled_admission = FormalExecutionAdmissionV02321.model_validate(
        private_payloads["admission.json"]
    )
    if (
        reservation.admission != bundled_admission
        or bundled_admission.execution_head != head
    ):
        raise ValueError("Product v0.2.3.2.1 reserved admission differs")

    public_payloads: dict[str, dict[str, Any]] = {}
    public_bytes: dict[str, bytes] = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("Product v0.2.3.2.1 publication entry differs")
        path = str(raw_entry.get("path"))
        expected_mode = (
            "REPLACE"
            if path == "docs/analysis/product-v02321-progress.json"
            else "CREATE_EXACT"
        )
        if raw_entry.get("mode") != expected_mode or path not in _PUBLICATION_OUTPUTS:
            raise ValueError("Product v0.2.3.2.1 publication mode differs")
        payload = _publication_entry_bytes(raw_entry)
        parsed = _validate_publication_payload(path, payload)
        target = root / path
        if expected_mode == "REPLACE":
            target = _require_regular_path(
                root=root,
                path=target,
                directory=False,
                label="progress publication target",
            )
            if target.read_bytes() != payload:
                expected_previous = raw_entry.get("expected_previous_sha256")
                if (
                    not isinstance(expected_previous, str)
                    or _sha256(target) != expected_previous
                ):
                    raise ValueError(
                        "Product v0.2.3.2.1 progress compare-and-swap differs"
                    )
        else:
            if "expected_previous_sha256" in raw_entry:
                raise ValueError("Product v0.2.3.2.1 create entry CAS differs")
            if target.exists() or target.is_symlink():
                regular = _require_regular_path(
                    root=root,
                    path=target,
                    directory=False,
                    label=f"publication target {path}",
                )
                if regular.read_bytes() != payload:
                    raise ValueError(
                        "Product v0.2.3.2.1 create publication target differs"
                    )
            else:
                _require_regular_path(
                    root=root,
                    path=target.parent,
                    directory=True,
                    label=f"publication parent {path}",
                )
        public_payloads[path] = parsed
        public_bytes[path] = payload

    acceptance = _require_publication_cross_bindings_v02321(
        private_payloads=private_payloads,
        public_payloads=public_payloads,
    )
    _verify_admission_after_reservation(
        root,
        bundled_admission,
        allowed_public_files=public_bytes,
    )
    write_private_json(
        private_root / "acceptance.json",
        private_payloads["acceptance.json"],
        create_once=True,
    )
    for raw_entry in entries:
        assert isinstance(raw_entry, dict)
        path = str(raw_entry["path"])
        parsed = public_payloads[path]
        if raw_entry["mode"] == "REPLACE":
            target = root / path
            if target.read_bytes() != public_bytes[path]:
                _replace_public(target, parsed)
        else:
            _write_public_once(root / path, parsed)
    completion = _sealed(
        schema_version="ecomsre.product.formal-publication-completion.v02321",
        terminal="ECOMSRE_PRODUCT_V02321_FORMAL_PUBLICATION_PASS",
        payload={
            "execution_head": head,
            "bundle_sha256": supplied,
            "published_output_count": len(entries),
        },
        seal="completion_sha256",
    )
    write_private_json(
        private_root / "publication-completion.json",
        completion,
        create_once=True,
    )
    published = NoFaultAcceptanceResultV02321.model_validate_json(
        (root / "docs/results/product-v02321-nofault-acceptance.json").read_bytes()
    )
    if published != acceptance:
        raise ValueError("Product v0.2.3.2.1 published acceptance differs")
    return published


def _worktree_for_branch(root: Path, branch: str) -> Path:
    output = subprocess.run(
        ("git", "worktree", "list", "--porcelain"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    current: Path | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree ")).resolve(strict=True)
        elif line == f"branch refs/heads/{branch}" and current is not None:
            return current
    raise FileNotFoundError(f"Product v0.2.3.2.1 required worktree absent: {branch}")


def _strict_admission(
    root: Path,
) -> tuple[
    FormalExecutionAdmissionV02321,
    FormalContractFreezeV02321,
    FormalPreExecutionReviewV02321,
]:
    """Perform every absence-bound review check before the first mutation."""

    review = verify_formal_pre_execution_review_v02321(root)
    freeze = verify_formal_contract_freeze_v02321(root)
    execution_head = _require_clean_head(root)
    freeze_path = root / "docs/analysis/product-v02321-formal-contract-freeze.json"
    review_path = root / "docs/external-reviews/product-v02321-pre-execution-review.md"
    runner_path = root / "scripts/product_v02321/run_formal_nofault.py"
    contract_path = root / "src/ecomsre/product/pilot/formal_nofault_v02321.py"
    runtime_descriptor_path = (
        root / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
    )
    runtime_descriptor = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _object(
            _require_regular_path(
                root=root,
                path=runtime_descriptor_path,
                directory=False,
                label="Runtime continuity descriptor",
            )
        )
    )
    admission = FormalExecutionAdmissionV02321.build(
        execution_head=execution_head,
        formal_contract_freeze_sha256=freeze.freeze_sha256,
        formal_contract_freeze_file_sha256=_sha256(freeze_path),
        pre_execution_review_sha256=review.review_sha256,
        pre_execution_review_file_sha256=_sha256(review_path),
        source_state_sha256=freeze.source_state_sha256,
        formal_clone_plan_sha256=freeze.formal_clone_plan.plan_sha256,
        formal_clone_destination_locator=(freeze.formal_clone_plan.destination_locator),
        formal_runner_file_sha256=_sha256(runner_path),
        formal_contract_file_sha256=_sha256(contract_path),
        runtime_continuity_descriptor_sha256=runtime_descriptor.descriptor_sha256,
        runtime_continuity_descriptor_file_sha256=_sha256(runtime_descriptor_path),
    )
    return admission, freeze, review


def _require_reserved_worktree_state_v02321(
    root: Path,
    *,
    expected_head: str,
    allowed_public_files: Mapping[str, bytes] | None,
) -> str:
    if allowed_public_files is None:
        head = _require_clean_head(root)
    else:
        head = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for entry in status.split("\0"):
            if not entry:
                continue
            if len(entry) < 4 or entry[2] != " ":
                raise ValueError("Product v0.2.3.2.1 reserved worktree status differs")
            code = entry[:2]
            relative = entry[3:]
            expected = allowed_public_files.get(relative)
            target = root / relative
            if (
                "R" in code
                or "C" in code
                or expected is None
                or target.is_symlink()
                or not target.is_file()
                or target.read_bytes() != expected
            ):
                raise ValueError("Product v0.2.3.2.1 reserved worktree contains drift")
    if head != expected_head:
        raise ValueError("Product v0.2.3.2.1 reserved execution HEAD drifted")
    return head


def _verify_admission_after_reservation(
    root: Path,
    admission: FormalExecutionAdmissionV02321,
    *,
    allowed_public_files: Mapping[str, bytes] | None = None,
) -> FormalContractFreezeV02321:
    """Re-admit a reserved clone without relying on pre-mutation absence."""

    head = _require_reserved_worktree_state_v02321(
        root,
        expected_head=admission.execution_head,
        allowed_public_files=allowed_public_files,
    )
    freeze_path = root / "docs/analysis/product-v02321-formal-contract-freeze.json"
    review_path = root / "docs/external-reviews/product-v02321-pre-execution-review.md"
    runner_path = root / "scripts/product_v02321/run_formal_nofault.py"
    contract_path = root / "src/ecomsre/product/pilot/formal_nofault_v02321.py"
    runtime_descriptor_path = (
        root / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
    )
    for label, path in (
        ("formal contract freeze", freeze_path),
        ("pre-execution review", review_path),
        ("formal runner", runner_path),
        ("formal contract", contract_path),
        ("Runtime continuity descriptor", runtime_descriptor_path),
    ):
        _require_regular_path(
            root=root,
            path=path,
            directory=False,
            label=label,
        )
    freeze = FormalContractFreezeV02321.model_validate_json(freeze_path.read_bytes())
    text = review_path.read_text(encoding="utf-8")
    start = "<!-- ECOMSRE_PRODUCT_V02321_REVIEW_JSON_START -->\n```json\n"
    end = "\n```\n<!-- ECOMSRE_PRODUCT_V02321_REVIEW_JSON_END -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("Product v0.2.3.2.1 reserved review differs")
    review = FormalPreExecutionReviewV02321.model_validate_json(
        text.split(start, 1)[1].split(end, 1)[0]
    )
    runtime_descriptor = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _object(runtime_descriptor_path)
    )
    frozen_files_exact = True
    for item in freeze.frozen_files:
        frozen = _require_regular_path(
            root=root,
            path=root / item.path,
            directory=False,
            label=f"frozen file {item.path}",
        )
        frozen_bytes = frozen.read_bytes()
        allowed_replacement = (
            None
            if allowed_public_files is None
            else allowed_public_files.get(item.path)
        )
        if not (
            (
                hashlib.sha256(frozen_bytes).hexdigest() == item.file_sha256
                and len(frozen_bytes) == item.size_bytes
            )
            or (allowed_replacement is not None and frozen_bytes == allowed_replacement)
        ):
            frozen_files_exact = False
            break
    if (
        head != admission.execution_head
        or freeze.freeze_sha256 != admission.formal_contract_freeze_sha256
        or _sha256(freeze_path) != admission.formal_contract_freeze_file_sha256
        or review.review_sha256 != admission.pre_execution_review_sha256
        or _sha256(review_path) != admission.pre_execution_review_file_sha256
        or _sha256(runner_path) != admission.formal_runner_file_sha256
        or _sha256(contract_path) != admission.formal_contract_file_sha256
        or runtime_descriptor.descriptor_sha256
        != admission.runtime_continuity_descriptor_sha256
        or _sha256(runtime_descriptor_path)
        != admission.runtime_continuity_descriptor_file_sha256
        or review.formal_contract_verifier_file_sha256
        != _sha256(root / "src/ecomsre/product/pilot/formal_contract_v02321.py")
        or review.formal_nofault_contract_file_sha256 != _sha256(contract_path)
        or review.formal_nofault_runner_file_sha256 != _sha256(runner_path)
        or review.formal_state_clone_contract_file_sha256
        != _sha256(root / "src/ecomsre/product/pilot/product_state_clone_v02321.py")
        or review.formal_state_clone_runner_file_sha256
        != _sha256(root / "scripts/product_v02321/run_state_clone.py")
        or freeze.source_state_sha256 != admission.source_state_sha256
        or freeze.formal_clone_plan.plan_sha256 != admission.formal_clone_plan_sha256
        or freeze.formal_clone_plan.destination_locator
        != admission.formal_clone_destination_locator
        or not frozen_files_exact
    ):
        raise ValueError("Product v0.2.3.2.1 reserved admission drifted")
    return freeze


def _sealed(
    *, schema_version: str, terminal: str, payload: Mapping[str, Any], seal: str
) -> dict[str, Any]:
    body = {"schema_version": schema_version, "terminal": terminal, **payload}
    return {**body, seal: semantic_sha256_v22(body)}


def _find_decision_trace(
    data_root: Path, *, expected_sha256: str
) -> DiagnosisDecisionTraceV0232:
    object_root = data_root / "objects/sha256"
    matches: list[DiagnosisDecisionTraceV0232] = []
    for path in sorted(object_root.glob("[0-9a-f][0-9a-f]/*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Product v0.2.3.2.1 Evidence object path differs")
        if _sha256(path) != path.stem:
            raise ValueError("Product v0.2.3.2.1 Evidence object digest differs")
        try:
            payload = _object(path)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if payload.get("schema_version") != (
            "ecomsre.product.diagnosis-decision-trace.v0232"
        ):
            continue
        trace = DiagnosisDecisionTraceV0232.model_validate(payload)
        if trace.trace_sha256 == expected_sha256:
            matches.append(trace)
    if len(matches) != 1:
        raise ValueError("Product v0.2.3.2.1 decision trace binding differs")
    return matches[0]


def _source_diagnosis_job_count(data_root: Path) -> int:
    database = (data_root / "product.sqlite3").resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_jobs WHERE job_type = 'DIAGNOSIS'"
            ).fetchone()[0]
        )
    finally:
        connection.close()


class _FormalObservedStateV02321(TypedDict):
    incident_count: int
    diagnosis_count: int
    diagnosis_job_count: int
    fault_family_count: int
    knowledge_artifact_count: int
    provider_calls: int
    agent_writes: int
    runbook_executions: int
    action_authority: str


def _source_diagnosis_authority_v02321(
    data_root: Path,
) -> tuple[DiagnosisResultV1, ...]:
    database = (data_root / "product.sqlite3").resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        rows = connection.execute(
            "SELECT payload_json FROM diagnosis_results ORDER BY incident_id"
        ).fetchall()
    finally:
        connection.close()
    return tuple(
        DiagnosisResultV1.model_validate_json(row["payload_json"]) for row in rows
    )


def _observe_formal_cardinality(
    data_root: Path,
    *,
    environment_id: str,
) -> _FormalObservedStateV02321 | None:
    try:
        counts = _read_only_database_counts(data_root, environment_id)
        diagnosis_jobs = _source_diagnosis_job_count(data_root)
        diagnoses = _source_diagnosis_authority_v02321(data_root)
    except (OSError, sqlite3.Error, ValueError):
        return None
    if len(diagnoses) != counts["diagnosis_count"] or not diagnoses:
        return None
    return {
        "incident_count": counts["incident_count"],
        "diagnosis_count": counts["diagnosis_count"],
        "diagnosis_job_count": diagnosis_jobs,
        "fault_family_count": counts["fault_family_count"],
        "knowledge_artifact_count": counts["knowledge_artifact_count"],
        "provider_calls": sum(item.provider_calls for item in diagnoses),
        "agent_writes": sum(item.agent_writes for item in diagnoses),
        "runbook_executions": sum(item.runbook_executions for item in diagnoses),
        "action_authority": (
            "NONE"
            if all(item.action_authority.value == "NONE" for item in diagnoses)
            else "NON_NONE"
        ),
    }


def _recover_incident_by_external_key(
    data_root: Path,
    *,
    environment_id: str,
    external_incident_key: str,
) -> IncidentRecordV1 | None:
    database = (data_root / "product.sqlite3").resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT payload_json FROM incidents "
            "WHERE environment_id = ? AND external_incident_key = ?",
            (environment_id, external_incident_key),
        ).fetchone()
    finally:
        connection.close()
    return (
        None
        if row is None
        else IncidentRecordV1.model_validate_json(row["payload_json"])
    )


def _recover_diagnosis_job(
    data_root: Path,
    *,
    incident_id: str,
) -> ProductJobRecordV1 | None:
    database = (data_root / "product.sqlite3").resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        row = connection.execute(
            "SELECT * FROM diagnosis_jobs "
            "WHERE job_type = 'DIAGNOSIS' AND idempotency_key = ?",
            (f"diagnosis:{incident_id}",),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return ProductJobRecordV1(
        job_id=row["job_id"],
        job_type=row["job_type"],
        status=row["status"],
        payload=json.loads(row["payload_json"]),
        result=(None if row["result_json"] is None else json.loads(row["result_json"])),
        safe_error_code=row["safe_error_code"],
        idempotency_key=row["idempotency_key"],
        claimed_by=row["claimed_by"],
        lease_expires_at=row["lease_expires_at"],
        attempt_count=row["attempt_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _request_or_recover_incident_v02321(
    *,
    request: Callable[[], Mapping[str, Any]],
    recover: Callable[[], IncidentRecordV1 | None],
) -> IncidentRecordV1:
    response: IncidentRecordV1 | None = None
    request_error: BaseException | None = None
    try:
        response = IncidentRecordV1.model_validate(request())
    except BaseException as error:
        request_error = error
    persisted = recover()
    if persisted is None:
        if request_error is not None:
            raise request_error
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
    if response is not None and response != persisted:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
    return persisted


def _request_or_recover_diagnosis_job_v02321(
    *,
    request: Callable[[], Mapping[str, Any]],
    recover: Callable[[], ProductJobRecordV1 | None],
) -> ProductJobRecordV1:
    response: ProductJobRecordV1 | None = None
    request_error: BaseException | None = None
    try:
        response = ProductJobRecordV1.model_validate(request())
    except BaseException as error:
        request_error = error
    persisted = recover()
    if persisted is None:
        if request_error is not None:
            raise request_error
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
    incident_id = persisted.payload.get("incident_id")
    if not isinstance(incident_id, str):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
    queued = ProductJobRecordV1(
        job_id=persisted.job_id,
        job_type=ProductJobTypeV1.DIAGNOSIS,
        status=ProductJobStatusV1.PENDING,
        payload={"incident_id": incident_id},
        result=None,
        safe_error_code=None,
        idempotency_key=f"diagnosis:{incident_id}",
        claimed_by=None,
        lease_expires_at=None,
        attempt_count=0,
        created_at=persisted.created_at,
        updated_at=persisted.created_at,
    )

    def valid_progression(record: ProductJobRecordV1) -> bool:
        immutable = (
            record.job_id == queued.job_id
            and record.job_type is ProductJobTypeV1.DIAGNOSIS
            and record.payload == queued.payload
            and record.idempotency_key == queued.idempotency_key
            and record.created_at == queued.created_at
            and record.updated_at >= record.created_at
        )
        if not immutable:
            return False
        if record.status is ProductJobStatusV1.PENDING:
            return record == queued
        if record.status is ProductJobStatusV1.RUNNING:
            return (
                record.result is None
                and record.safe_error_code is None
                and record.claimed_by is not None
                and record.lease_expires_at is not None
                and record.attempt_count >= 1
            )
        if record.status is ProductJobStatusV1.SUCCEEDED:
            return (
                isinstance(record.result, dict)
                and record.safe_error_code is None
                and record.claimed_by is None
                and record.lease_expires_at is None
                and record.attempt_count >= 1
            )
        if record.status is ProductJobStatusV1.FAILED:
            return (
                record.result is None
                and record.safe_error_code is not None
                and record.claimed_by is None
                and record.lease_expires_at is None
                and record.attempt_count >= 1
            )
        return False

    if not valid_progression(persisted) or (
        response is not None and not valid_progression(response)
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
    return queued


def run_formal_traffic_journaled_v02321(
    *,
    runner: HealthyTrafficRunnerV0232,
    endpoint: str,
    profile: HealthyTrafficProfileV0232,
    contract: CheckoutTrafficContractV0232,
    consumption: FormalTrafficConsumptionV02321,
    dispatch_checkpoints: list[FormalTrafficDispatchCheckpointV02321],
    observation_checkpoints: list[FormalTrafficObservationCheckpointV02321],
    observations: list[CheckoutTransactionObservationV0232],
    state: MutableMapping[str, Any],
    persist: Callable[[str, Mapping[str, Any]], None],
) -> HealthyTrafficExecutionV0232:
    """Run the frozen traffic semantics with an append-only per-send journal."""

    started_at = runner.clock()
    for ordinal in range(1, profile.transactions + 1):
        cart_payload, checkout_payload = runner._payloads(profile.request_seed, ordinal)
        dispatch = FormalTrafficDispatchCheckpointV02321.build(
            consumption_sha256=consumption.consumption_sha256,
            ordinal=ordinal,
            cart_payload_sha256=semantic_sha256_v22(cart_payload),
            checkout_payload_sha256=semantic_sha256_v22(checkout_payload),
        )
        persist(
            f"traffic-dispatch-{ordinal:03d}.json",
            dispatch.model_dump(mode="json"),
        )
        dispatch_checkpoints.append(dispatch)
        state.update(
            {
                "stage": "DISPATCH_REQUESTED",
                "pending_dispatch_ordinal": ordinal,
                "remote_delivery": "UNKNOWN",
            }
        )
        observation = runner.observe_transaction(
            endpoint=endpoint,
            ordinal=ordinal,
            contract=contract,
            cart_payload=cart_payload,
            checkout_payload=checkout_payload,
        )
        checkpoint = FormalTrafficObservationCheckpointV02321.build(
            consumption_sha256=consumption.consumption_sha256,
            dispatch_checkpoint_sha256=dispatch.checkpoint_sha256,
            observation=observation,
        )
        persist(
            f"traffic-observation-{ordinal:03d}.json",
            checkpoint.model_dump(mode="json"),
        )
        observation_checkpoints.append(checkpoint)
        observations.append(observation)
        state.update(
            {
                "stage": "OBSERVATION_PERSISTED",
                "pending_dispatch_ordinal": None,
                "remote_delivery": "OBSERVED",
            }
        )
        if ordinal < profile.transactions:
            runner.sleep(1.0 / profile.requests_per_second)
    failures = Counter(
        item.failure_stage.value
        for item in observations
        if item.failure_stage is not None
    )
    successful = sum(item.business_success for item in observations)
    run = HealthyTrafficRunV0232.build(
        role="FORMAL",
        profile_sha256=profile.profile_sha256,
        contract_sha256=contract.contract_sha256,
        planned_transactions=profile.transactions,
        completed_transactions=len(observations),
        successful_transactions=successful,
        failed_transactions=len(observations) - successful,
        stage_failure_counts=dict(sorted(failures.items())),
        transport_retry_count=0,
        started_at=started_at,
        ended_at=runner.clock(),
        passed=(
            len(observations) == profile.transactions
            and successful == profile.transactions
        ),
        transaction_observation_sha256s=[
            item.observation_sha256 for item in observations
        ],
    )
    execution = HealthyTrafficExecutionV0232.build(
        run=run,
        observations=tuple(observations),
    )
    state.update(
        {
            "stage": "EXECUTION_RETURNED",
            "pending_dispatch_ordinal": None,
            "remote_delivery": "OBSERVED",
        }
    )
    return execution


_TRAFFIC_JOURNAL_NAME = re.compile(r"^traffic-(dispatch|observation)-([0-9]{3})\.json$")


def _load_traffic_journal_v02321(
    *,
    private_root: Path,
    consumption: FormalTrafficConsumptionV02321,
) -> tuple[
    tuple[FormalTrafficDispatchCheckpointV02321, ...],
    tuple[FormalTrafficObservationCheckpointV02321, ...],
]:
    journal = private_root / "traffic-journal"
    if not journal.exists() and not journal.is_symlink():
        return (), ()
    _require_regular_path(
        root=private_root,
        path=journal,
        directory=True,
        label="traffic journal",
    )
    dispatch_by_ordinal: dict[int, FormalTrafficDispatchCheckpointV02321] = {}
    observation_by_ordinal: dict[int, FormalTrafficObservationCheckpointV02321] = {}
    for path in journal.iterdir():
        _require_regular_path(
            root=private_root,
            path=path,
            directory=False,
            label=f"traffic journal member {path.name}",
        )
        matched = _TRAFFIC_JOURNAL_NAME.fullmatch(path.name)
        if matched is None:
            raise ValueError("Product v0.2.3.2.1 traffic journal member differs")
        kind, ordinal_text = matched.groups()
        ordinal = int(ordinal_text)
        if ordinal < 1 or ordinal > 30:
            raise ValueError("Product v0.2.3.2.1 traffic journal ordinal differs")
        if kind == "dispatch":
            raw = path.read_bytes()
            checkpoint = FormalTrafficDispatchCheckpointV02321.model_validate_json(raw)
            if raw != canonical_json_bytes(checkpoint.model_dump(mode="json")):
                raise ValueError("Product v0.2.3.2.1 dispatch journal is not canonical")
            if checkpoint.ordinal != ordinal or ordinal in dispatch_by_ordinal:
                raise ValueError("Product v0.2.3.2.1 dispatch journal differs")
            dispatch_by_ordinal[ordinal] = checkpoint
        else:
            raw = path.read_bytes()
            observation = FormalTrafficObservationCheckpointV02321.model_validate_json(
                raw
            )
            if raw != canonical_json_bytes(observation.model_dump(mode="json")):
                raise ValueError(
                    "Product v0.2.3.2.1 observation journal is not canonical"
                )
            if (
                observation.observation.ordinal != ordinal
                or ordinal in observation_by_ordinal
            ):
                raise ValueError("Product v0.2.3.2.1 observation journal differs")
            observation_by_ordinal[ordinal] = observation
    dispatches = tuple(
        dispatch_by_ordinal[ordinal] for ordinal in sorted(dispatch_by_ordinal)
    )
    observations = tuple(
        observation_by_ordinal[ordinal] for ordinal in sorted(observation_by_ordinal)
    )
    if (
        tuple(sorted(dispatch_by_ordinal)) != tuple(range(1, len(dispatches) + 1))
        or tuple(sorted(observation_by_ordinal))
        != tuple(range(1, len(observations) + 1))
        or len(dispatches) not in {len(observations), len(observations) + 1}
        or any(
            item.consumption_sha256 != consumption.consumption_sha256
            for item in dispatches
        )
        or any(
            item.consumption_sha256 != consumption.consumption_sha256
            for item in observations
        )
        or any(
            observation.dispatch_checkpoint_sha256
            != dispatches[index].checkpoint_sha256
            for index, observation in enumerate(observations)
        )
    ):
        raise ValueError("Product v0.2.3.2.1 traffic journal chain differs")
    return dispatches, observations


def _validated_formal_clone_report_bytes_v02321(
    *,
    root: Path,
    admission: FormalExecutionAdmissionV02321,
) -> bytes | None:
    """Admit only the exact formal clone report produced after reservation."""

    relative = "docs/analysis/product-v02321-product-state-clone-formal.json"
    report_path = root / relative
    if not report_path.exists() and not report_path.is_symlink():
        return None
    report_path = _require_regular_path(
        root=root,
        path=report_path,
        directory=False,
        label="formal clone report",
    )
    freeze_path = _require_regular_path(
        root=root,
        path=root / "docs/analysis/product-v02321-formal-contract-freeze.json",
        directory=False,
        label="formal contract freeze",
    )
    preflight_path = _require_regular_path(
        root=root,
        path=(root / "docs/analysis/product-v02321-product-state-clone-preflight.json"),
        directory=False,
        label="preflight clone report",
    )
    raw = report_path.read_bytes()
    report = FormalStateCloneReportV02321.model_validate_json(raw)
    if raw != canonical_json_bytes(report.model_dump(mode="json")):
        raise ValueError("Product v0.2.3.2.1 formal clone report is not canonical")
    freeze = FormalContractFreezeV02321.model_validate_json(freeze_path.read_bytes())
    preflight = PreflightStateCloneReportV02321.model_validate_json(
        preflight_path.read_bytes()
    )
    destination = _admit_state(
        root / report.destination_locator,
        locator=report.destination_locator,
    )
    expected_clone = _bind_existing_clone(
        source=preflight.source_state,
        destination=destination,
        destination_locator=report.destination_locator,
    )
    if (
        freeze.freeze_sha256 != admission.formal_contract_freeze_sha256
        or freeze.source_state_sha256 != admission.source_state_sha256
        or freeze.formal_clone_plan.plan_sha256 != admission.formal_clone_plan_sha256
        or freeze.formal_clone_plan.destination_locator
        != admission.formal_clone_destination_locator
        or report.formal_admission_sha256 != admission.admission_sha256
        or report.formal_clone_plan_sha256 != admission.formal_clone_plan_sha256
        or report.destination_locator != admission.formal_clone_destination_locator
        or report.source_repository_binding != preflight.source_repository_binding
        or report.predecessor_private_acceptance
        != preflight.predecessor_private_acceptance
        or report.source_state != preflight.source_state
        or report.destination_state != destination
        or report.clone != expected_clone
    ):
        raise ValueError("Product v0.2.3.2.1 recovered formal clone differs")
    return raw


def _reserved_admission_for_private_recovery_v02321(
    *,
    root: Path,
    private_root: Path,
    allowed_public_files: Mapping[str, bytes] | None = None,
) -> FormalExecutionAdmissionV02321:
    reservation = _load_canonical_reservation_v02321(root)
    admission_path = private_root / "admission.json"
    if admission_path.exists() or admission_path.is_symlink():
        admission_path = _require_regular_path(
            root=private_root,
            path=admission_path,
            directory=False,
            label="formal admission",
        )
        admission_raw = admission_path.read_bytes()
        admission = FormalExecutionAdmissionV02321.model_validate_json(admission_raw)
        if (
            admission_raw != canonical_json_bytes(admission.model_dump(mode="json"))
            or admission != reservation.admission
        ):
            raise ValueError("Product v0.2.3.2.1 recovery admission differs")
    else:
        admission = reservation.admission
    allowed = dict(allowed_public_files or {})
    clone_report = _validated_formal_clone_report_bytes_v02321(
        root=root,
        admission=admission,
    )
    if clone_report is not None:
        allowed["docs/analysis/product-v02321-product-state-clone-formal.json"] = (
            clone_report
        )
    _verify_admission_after_reservation(
        root,
        admission,
        allowed_public_files=allowed or None,
    )
    return admission


def _load_bound_consumption_v02321(
    *,
    root: Path,
    private_root: Path,
    admission: FormalExecutionAdmissionV02321,
) -> FormalTrafficConsumptionV02321 | None:
    path = private_root / "traffic-consumption.json"
    if not path.exists() and not path.is_symlink():
        return None
    path = _require_regular_path(
        root=private_root,
        path=path,
        directory=False,
        label="formal traffic consumption",
    )
    raw = path.read_bytes()
    consumption = FormalTrafficConsumptionV02321.model_validate_json(raw)
    freeze = FormalContractFreezeV02321.model_validate_json(
        (root / "docs/analysis/product-v02321-formal-contract-freeze.json").read_bytes()
    )
    if (
        raw != canonical_json_bytes(consumption.model_dump(mode="json"))
        or consumption.execution_head != admission.execution_head
        or consumption.admission_sha256 != admission.admission_sha256
        or consumption.traffic_contract_sha256 != freeze.traffic_contract_sha256
        or consumption.formal_profile_sha256 != freeze.formal_profile_sha256
    ):
        raise ValueError("Product v0.2.3.2.1 traffic consumption binding differs")
    return consumption


def _load_bound_traffic_execution_v02321(
    *,
    private_root: Path,
    consumption: FormalTrafficConsumptionV02321,
) -> HealthyTrafficExecutionV0232 | None:
    path = private_root / "traffic-execution.json"
    if not path.exists() and not path.is_symlink():
        return None
    path = _require_regular_path(
        root=private_root,
        path=path,
        directory=False,
        label="traffic execution",
    )
    raw = path.read_bytes()
    execution = HealthyTrafficExecutionV0232.model_validate_json(raw)
    if (
        raw != canonical_json_bytes(execution.model_dump(mode="json"))
        or execution.run.contract_sha256 != consumption.traffic_contract_sha256
        or execution.run.profile_sha256 != consumption.formal_profile_sha256
    ):
        raise ValueError("Product v0.2.3.2.1 traffic execution binding differs")
    return execution


def _load_bound_formal_traffic_result_v02321(
    *,
    private_root: Path,
    admission: FormalExecutionAdmissionV02321,
    consumption: FormalTrafficConsumptionV02321,
    execution: HealthyTrafficExecutionV0232 | None,
) -> FormalTrafficResultV02321 | None:
    path = private_root / "formal-traffic.json"
    if not path.exists() and not path.is_symlink():
        return None
    path = _require_regular_path(
        root=private_root,
        path=path,
        directory=False,
        label="formal traffic result",
    )
    raw = path.read_bytes()
    result = FormalTrafficResultV02321.model_validate_json(raw)
    if (
        raw != canonical_json_bytes(result.model_dump(mode="json"))
        or result.admission_sha256 != admission.admission_sha256
        or result.consumption_sha256 != consumption.consumption_sha256
        or result.execution.run.contract_sha256 != consumption.traffic_contract_sha256
        or result.execution.run.profile_sha256 != consumption.formal_profile_sha256
        or execution is None
        or result.execution != execution
    ):
        raise ValueError("Product v0.2.3.2.1 formal traffic result binding differs")
    return result


@dataclass(frozen=True)
class _FormalRecoveryStateV02321:
    consumption: FormalTrafficConsumptionV02321 | None
    consumption_invalid: bool
    dispatches: tuple[FormalTrafficDispatchCheckpointV02321, ...]
    observations: tuple[FormalTrafficObservationCheckpointV02321, ...]
    execution: HealthyTrafficExecutionV0232 | None
    traffic_result: FormalTrafficResultV02321 | None
    observed: _FormalObservedStateV02321 | None
    terminal_kind: str
    stage: str
    pending_dispatch_ordinal: int | None
    remote_delivery: str


def _observe_authoritative_recovery_state_v02321(
    *,
    root: Path,
    private_root: Path,
    admission: FormalExecutionAdmissionV02321,
) -> _FormalRecoveryStateV02321:
    consumption_invalid = False
    try:
        consumption = _load_bound_consumption_v02321(
            root=root,
            private_root=private_root,
            admission=admission,
        )
    except (OSError, ValueError):
        consumption_path = private_root / "traffic-consumption.json"
        if not consumption_path.exists() and not consumption_path.is_symlink():
            raise
        consumption = None
        consumption_invalid = True

    dependent_paths = tuple(
        private_root / relative
        for relative in (
            "traffic-journal",
            "traffic-execution.json",
            "formal-traffic.json",
        )
    )
    if consumption is None and any(
        path.exists() or path.is_symlink() for path in dependent_paths
    ):
        raise ValueError(
            "Product v0.2.3.2.1 traffic evidence exists without valid consumption"
        )

    dispatches: tuple[FormalTrafficDispatchCheckpointV02321, ...] = ()
    observations: tuple[FormalTrafficObservationCheckpointV02321, ...] = ()
    execution: HealthyTrafficExecutionV0232 | None = None
    traffic_result: FormalTrafficResultV02321 | None = None
    if consumption is not None:
        dispatches, observations = _load_traffic_journal_v02321(
            private_root=private_root,
            consumption=consumption,
        )
        execution = _load_bound_traffic_execution_v02321(
            private_root=private_root,
            consumption=consumption,
        )
        if execution is not None and execution.observations != tuple(
            item.observation for item in observations
        ):
            raise ValueError("Product v0.2.3.2.1 recovery journal differs")
        traffic_result = _load_bound_formal_traffic_result_v02321(
            private_root=private_root,
            admission=admission,
            consumption=consumption,
            execution=execution,
        )

    observed = _observe_formal_cardinality(
        root / admission.formal_clone_destination_locator,
        environment_id=ENVIRONMENT_ID_V0232,
    )
    traffic_blocked = (
        consumption is not None
        and traffic_result is None
        and observed
        == {
            "incident_count": 1,
            "diagnosis_count": 1,
            "diagnosis_job_count": 1,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "provider_calls": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "action_authority": "NONE",
        }
    )
    if traffic_blocked:
        if len(dispatches) > len(observations):
            stage = "DISPATCH_REQUESTED"
            pending = dispatches[-1].ordinal
            remote = "UNKNOWN"
        elif execution is not None:
            stage = "EXECUTION_RETURNED"
            pending = None
            remote = "OBSERVED"
        elif observations:
            stage = "OBSERVATION_PERSISTED"
            pending = None
            remote = "OBSERVED"
        else:
            stage = "CONSUMED_BEFORE_FIRST_CART"
            pending = None
            remote = "NOT_STARTED"
        terminal_kind = "TRAFFIC"
    else:
        stage = (
            "PROCESS_INTERRUPTED_AFTER_FORMAL_TRAFFIC_PASS"
            if traffic_result is not None
            else "PROCESS_INTERRUPTED_AFTER_FORMAL_START"
        )
        pending = None
        remote = "NOT_STARTED"
        terminal_kind = "INFRASTRUCTURE"
    return _FormalRecoveryStateV02321(
        consumption=consumption,
        consumption_invalid=consumption_invalid,
        dispatches=dispatches,
        observations=observations,
        execution=execution,
        traffic_result=traffic_result,
        observed=observed,
        terminal_kind=terminal_kind,
        stage=stage,
        pending_dispatch_ordinal=pending,
        remote_delivery=remote,
    )


def _unproven_blocker_closure_v02321(
    admission: FormalExecutionAdmissionV02321,
) -> FormalBlockerClosureV02321:
    return FormalBlockerClosureV02321.build(
        product_cleanup=ProductCleanupObservationV02321(
            observation_complete=False,
            verdict="BLOCKED",
            safe_error_code="RECOVERY_PRODUCT_CLEANUP_UNPROVEN",
        ).model_dump(mode="json"),
        demo_cleanup=DemoCleanupObservationV02321(
            observation_complete=False,
            verdict="BLOCKED",
            safe_error_code="RECOVERY_DEMO_CLEANUP_UNPROVEN",
        ).model_dump(mode="json"),
        evidence_origin="RECOVERY_UNPROVEN",
        queue_state_status="UNPROVEN",
        queue_before_sha256=None,
        queue_after_sha256=None,
        outer_baseline_state_status="UNPROVEN",
        outer_baseline_before_sha256=None,
        outer_baseline_after_sha256=None,
        source_state_status="UNPROVEN",
        source_state_before_sha256=admission.source_state_sha256,
        source_state_after_sha256=None,
    )


def _is_nonnegative_int_v02321(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _live_blocker_closure_v02321(
    *,
    admission: FormalExecutionAdmissionV02321,
    product_data_root: Path,
    product_cleanup: Mapping[str, Any],
    demo_cleanup: Any,
    queue_before_sha256: str | None,
    queue_after_sha256: str | None,
    outer_baseline_before_sha256: str | None,
    outer_baseline_after_sha256: str | None,
    source_before: ProductStateSourceV0232,
    source_after: ProductStateSourceV0232 | None,
) -> FormalBlockerClosureV02321:
    if source_before.source_sha256 != admission.source_state_sha256:
        raise ValueError("Product v0.2.3.2.1 blocker source prestate differs")

    try:
        database_owner_count_after: int | None = _database_owner_count(
            product_data_root / "product.sqlite3"
        )
    except Exception:
        database_owner_count_after = None
    owned_host_processes = product_cleanup.get("owned_host_processes")
    product_api_port_available = product_cleanup.get("product_api_port_available")
    product_non_owned_changed = product_cleanup.get("non_owned_resources_changed")
    product_complete = (
        isinstance(owned_host_processes, int)
        and not isinstance(owned_host_processes, bool)
        and owned_host_processes >= 0
        and database_owner_count_after is not None
        and isinstance(product_api_port_available, bool)
        and isinstance(product_non_owned_changed, bool)
    )
    product_clean = (
        product_complete
        and product_cleanup.get("verdict") == "CLEAN"
        and owned_host_processes == 0
        and database_owner_count_after == 0
        and product_api_port_available is True
        and product_non_owned_changed is False
        and product_cleanup.get("safe_error") is None
    )
    product_safe_error = product_cleanup.get("safe_error")
    product_observation = ProductCleanupObservationV02321(
        observation_complete=product_complete,
        verdict="CLEAN" if product_clean else "BLOCKED",
        owned_host_processes=(owned_host_processes if product_complete else None),
        database_owner_count_before=0 if product_complete else None,
        database_owner_count_after=(
            database_owner_count_after if product_complete else None
        ),
        product_api_port_available=(
            product_api_port_available if product_complete else None
        ),
        non_owned_resources_changed=(
            product_non_owned_changed if product_complete else None
        ),
        safe_error_code=(
            None
            if product_clean
            else str(product_safe_error or "PRODUCT_CLEANUP_UNPROVEN")[:120]
        ),
    )

    demo_values = {
        name: getattr(demo_cleanup, name, None)
        for name in (
            "owned_containers",
            "owned_networks",
            "owned_volumes",
            "non_owned_resources_changed",
            "verdict",
        )
    }
    demo_complete = all(
        _is_nonnegative_int_v02321(demo_values[name])
        for name in ("owned_containers", "owned_networks", "owned_volumes")
    ) and isinstance(demo_values["non_owned_resources_changed"], bool)
    demo_clean = (
        demo_complete
        and demo_values["verdict"] == "CLEAN"
        and demo_values["owned_containers"] == 0
        and demo_values["owned_networks"] == 0
        and demo_values["owned_volumes"] == 0
        and demo_values["non_owned_resources_changed"] is False
    )
    demo_observation = DemoCleanupObservationV02321(
        observation_complete=demo_complete,
        verdict="CLEAN" if demo_clean else "BLOCKED",
        owned_containers=(demo_values["owned_containers"] if demo_complete else None),
        owned_networks=(demo_values["owned_networks"] if demo_complete else None),
        owned_volumes=(demo_values["owned_volumes"] if demo_complete else None),
        non_owned_resources_changed=(
            demo_values["non_owned_resources_changed"] if demo_complete else None
        ),
        safe_error_code=(None if demo_clean else "DEMO_CLEANUP_UNPROVEN"),
    )

    source_after_sha256 = None if source_after is None else source_after.source_sha256
    source_status = (
        "UNPROVEN"
        if source_after_sha256 is None
        else (
            "UNCHANGED"
            if source_after_sha256 == source_before.source_sha256
            else "CHANGED"
        )
    )

    def pair_status(before: str | None, after: str | None) -> str:
        if before is None and after is None:
            return "UNPROVEN"
        if before is None or after is None:
            return "PARTIAL"
        return "OBSERVED"

    return FormalBlockerClosureV02321.build(
        product_cleanup=product_observation.model_dump(mode="json"),
        demo_cleanup=demo_observation.model_dump(mode="json"),
        evidence_origin="LIVE_OBSERVATION",
        queue_state_status=pair_status(queue_before_sha256, queue_after_sha256),
        queue_before_sha256=queue_before_sha256,
        queue_after_sha256=queue_after_sha256,
        outer_baseline_state_status=pair_status(
            outer_baseline_before_sha256,
            outer_baseline_after_sha256,
        ),
        outer_baseline_before_sha256=outer_baseline_before_sha256,
        outer_baseline_after_sha256=outer_baseline_after_sha256,
        source_state_status=source_status,
        source_state_before_sha256=source_before.source_sha256,
        source_state_after_sha256=source_after_sha256,
    )


def _blocker_from_recovery_state_v02321(
    *,
    state: _FormalRecoveryStateV02321,
    admission: FormalExecutionAdmissionV02321,
    closure: FormalBlockerClosureV02321,
    safe_error_code: str,
) -> FormalTrafficBlockerV02321 | FormalExecutionBlockerV02321:
    if state.terminal_kind == "TRAFFIC":
        assert state.consumption is not None
        return FormalTrafficBlockerV02321.build(
            execution_head=admission.execution_head,
            admission_sha256=admission.admission_sha256,
            consumption_sha256=state.consumption.consumption_sha256,
            stage=state.stage,
            traffic_execution=state.execution,
            dispatch_checkpoints=state.dispatches,
            observation_checkpoints=state.observations,
            pending_dispatch_ordinal=state.pending_dispatch_ordinal,
            remote_delivery=state.remote_delivery,
            safe_error_code=safe_error_code,
            closure=closure.model_dump(mode="json"),
        )
    observed = state.observed
    return FormalExecutionBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        stage=state.stage,
        safe_error_code=(
            "INVALID_DURABLE_TRAFFIC_CONSUMPTION"
            if state.consumption_invalid
            else safe_error_code
        ),
        formal_healthy_traffic_execution_count=(
            1 if state.consumption is not None else 0
        ),
        observed_state_status="OBSERVED" if observed is not None else "UNAVAILABLE",
        observed_incident_count=(
            None if observed is None else observed["incident_count"]
        ),
        observed_diagnosis_count=(
            None if observed is None else observed["diagnosis_count"]
        ),
        observed_diagnosis_job_count=(
            None if observed is None else observed["diagnosis_job_count"]
        ),
        observed_fault_family_count=(
            None if observed is None else observed["fault_family_count"]
        ),
        observed_knowledge_artifact_count=(
            None if observed is None else observed["knowledge_artifact_count"]
        ),
        observed_provider_calls=(
            None if observed is None else observed["provider_calls"]
        ),
        observed_agent_writes=(None if observed is None else observed["agent_writes"]),
        observed_runbook_executions=(
            None if observed is None else observed["runbook_executions"]
        ),
        accepted_successor_incident_count=(
            None if observed is None else observed["incident_count"] - 1
        ),
        successor_diagnosis_count=(
            None if observed is None else observed["diagnosis_count"] - 1
        ),
        action_authority=(None if observed is None else observed["action_authority"]),
        closure=closure.model_dump(mode="json"),
    )


def _validate_existing_blocker_v02321(
    *,
    root: Path,
    private_root: Path,
    admission: FormalExecutionAdmissionV02321,
    blocker_bytes: bytes,
) -> tuple[str, dict[str, Any]]:
    raw = json.loads(blocker_bytes)
    if not isinstance(raw, dict):
        raise ValueError("Product v0.2.3.2.1 formal blocker differs")
    terminal = raw.get("terminal")
    blocker: FormalTrafficBlockerV02321 | FormalExecutionBlockerV02321
    if terminal == "BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC":
        blocker = FormalTrafficBlockerV02321.model_validate(raw)
    elif terminal == "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE":
        blocker = FormalExecutionBlockerV02321.model_validate(raw)
    else:
        raise ValueError("Product v0.2.3.2.1 formal blocker type differs")

    if blocker_bytes != canonical_json_bytes(blocker.model_dump(mode="json")):
        raise ValueError("Product v0.2.3.2.1 formal blocker is not canonical")

    closure = blocker.closure
    if closure.source_state_before_sha256 != admission.source_state_sha256:
        raise ValueError("Product v0.2.3.2.1 formal blocker closure differs")
    if closure.evidence_origin == "LIVE_OBSERVATION":
        closure_path = _require_regular_path(
            root=private_root,
            path=private_root / "blocker-closure-evidence.json",
            directory=False,
            label="formal blocker closure evidence",
        )
        closure_raw = closure_path.read_bytes()
        observed_closure = FormalBlockerClosureV02321.model_validate_json(closure_raw)
        if (
            closure_raw
            != canonical_json_bytes(observed_closure.model_dump(mode="json"))
            or observed_closure != closure
        ):
            raise ValueError("Product v0.2.3.2.1 formal blocker closure differs")
    if closure.source_state_status != "UNPROVEN":
        source_path = _require_regular_path(
            root=private_root,
            path=private_root / "source-poststate.json",
            directory=False,
            label="formal source poststate",
        )
        source_after = ProductStateSourceV0232.model_validate_json(
            source_path.read_bytes()
        )
        if (
            source_path.read_bytes()
            != canonical_json_bytes(source_after.model_dump(mode="json"))
            or source_after.source_sha256 != closure.source_state_after_sha256
        ):
            raise ValueError("Product v0.2.3.2.1 formal source poststate differs")

    state = _observe_authoritative_recovery_state_v02321(
        root=root,
        private_root=private_root,
        admission=admission,
    )
    expected = _blocker_from_recovery_state_v02321(
        state=state,
        admission=admission,
        closure=closure,
        safe_error_code=blocker.safe_error_code,
    )
    if type(expected) is not type(blocker) or expected != blocker:
        raise ValueError("Product v0.2.3.2.1 formal blocker binding differs")
    return blocker.terminal, blocker.model_dump(mode="json")


def _recover_interrupted_private_run_v02321(
    *, root: Path, private_root: Path
) -> NoFaultAcceptanceResultV02321:
    """Seal an interrupted run or finish an already-sealed publication."""

    publication = private_root / "publication-bundle.json"
    intent = private_root / "publication-intent.json"
    publication_exists = publication.exists() or publication.is_symlink()
    intent_exists = intent.exists() or intent.is_symlink()
    if publication_exists:
        _require_regular_path(
            root=private_root,
            path=publication,
            directory=False,
            label="publication bundle",
        )
    if intent_exists:
        _require_regular_path(
            root=private_root,
            path=intent,
            directory=False,
            label="publication intent",
        )
    if publication_exists:
        return recover_formal_publication_v02321(project_root=root)
    if intent_exists:
        _freeze_publication_bundle_from_intent(private_root=private_root)
        return recover_formal_publication_v02321(project_root=root)
    blocker_path = private_root / "blocker.json"
    blocker_bytes: bytes | None = None
    allowed_public_files: dict[str, bytes] | None = None
    if blocker_path.exists() or blocker_path.is_symlink():
        blocker_path = _require_regular_path(
            root=private_root,
            path=blocker_path,
            directory=False,
            label="formal blocker",
        )
        blocker_bytes = blocker_path.read_bytes()
        public_blocker = root / "docs/analysis/product-v02321-formal-blocker.json"
        if public_blocker.exists() or public_blocker.is_symlink():
            public_blocker = _require_regular_path(
                root=root,
                path=public_blocker,
                directory=False,
                label="public formal blocker",
            )
            if public_blocker.read_bytes() != blocker_bytes:
                raise ValueError("Product v0.2.3.2.1 public blocker differs")
            allowed_public_files = {
                "docs/analysis/product-v02321-formal-blocker.json": blocker_bytes
            }

    admission = _reserved_admission_for_private_recovery_v02321(
        root=root,
        private_root=private_root,
        allowed_public_files=allowed_public_files,
    )
    if blocker_bytes is not None:
        terminal, blocker_payload = _validate_existing_blocker_v02321(
            root=root,
            private_root=private_root,
            admission=admission,
            blocker_bytes=blocker_bytes,
        )
        _write_public_once(
            root / "docs/analysis/product-v02321-formal-blocker.json",
            blocker_payload,
        )
        raise RuntimeError(terminal)
    state = _observe_authoritative_recovery_state_v02321(
        root=root,
        private_root=private_root,
        admission=admission,
    )
    closure = _unproven_blocker_closure_v02321(admission)
    blocker = _blocker_from_recovery_state_v02321(
        state=state,
        admission=admission,
        closure=closure,
        safe_error_code=(
            "PROCESS_INTERRUPTED_AFTER_DURABLE_CONSUMPTION"
            if state.terminal_kind == "TRAFFIC"
            else "PROCESS_INTERRUPTED_BEFORE_SEALED_TERMINAL"
        ),
    )
    blocker_payload = blocker.model_dump(mode="json")
    write_private_json(
        blocker_path,
        blocker_payload,
        create_once=True,
    )
    _write_public_once(
        root / "docs/analysis/product-v02321-formal-blocker.json",
        blocker_payload,
    )
    raise RuntimeError(blocker.terminal)


def _seal_formal_failure_v02321(
    *,
    root: Path,
    private_root: Path,
    admission: FormalExecutionAdmissionV02321,
    live_error: BaseException,
    stage: str,
    product_data_root: Path,
    environment_id: str,
    consumption: FormalTrafficConsumptionV02321 | None,
    traffic_result: FormalTrafficResultV02321 | None,
    execution: HealthyTrafficExecutionV0232 | None,
    dispatch_checkpoints: Sequence[FormalTrafficDispatchCheckpointV02321],
    observation_checkpoints: Sequence[FormalTrafficObservationCheckpointV02321],
    traffic_journal_state: Mapping[str, Any],
    product_cleanup: Mapping[str, Any],
    demo_cleanup: Any,
    queue_before_sha256: str | None,
    queue_after_sha256: str | None,
    outer_baseline_before_sha256: str | None,
    outer_baseline_after_sha256: str | None,
    source_before: ProductStateSourceV0232,
    source_after: ProductStateSourceV0232 | None,
) -> str:
    del (
        stage,
        environment_id,
        consumption,
        traffic_result,
        execution,
        dispatch_checkpoints,
        observation_checkpoints,
        traffic_journal_state,
    )
    closure = _live_blocker_closure_v02321(
        admission=admission,
        product_data_root=product_data_root,
        product_cleanup=product_cleanup,
        demo_cleanup=demo_cleanup,
        queue_before_sha256=queue_before_sha256,
        queue_after_sha256=queue_after_sha256,
        outer_baseline_before_sha256=outer_baseline_before_sha256,
        outer_baseline_after_sha256=outer_baseline_after_sha256,
        source_before=source_before,
        source_after=source_after,
    )
    write_private_json(
        private_root / "blocker-closure-evidence.json",
        closure.model_dump(mode="json"),
        create_once=True,
    )
    state = _observe_authoritative_recovery_state_v02321(
        root=root,
        private_root=private_root,
        admission=admission,
    )
    blocker = _blocker_from_recovery_state_v02321(
        state=state,
        admission=admission,
        closure=closure,
        safe_error_code=f"{type(live_error).__name__}: {live_error}"[:1000],
    )
    blocker_payload = blocker.model_dump(mode="json")
    write_private_json(private_root / "blocker.json", blocker_payload, create_once=True)
    _write_public_once(
        root / "docs/analysis/product-v02321-formal-blocker.json",
        blocker_payload,
    )
    return blocker.terminal


def _updated_progress(
    root: Path,
    *,
    result: NoFaultAcceptanceResultV02321,
    clone_report: FormalStateCloneReportV02321,
    authority_proof: RuntimeAuthorityProofV02321,
    restart_proof: BaselineRestartProofV02321,
    traffic: FormalTrafficResultV02321,
) -> dict[str, Any]:
    current = _object(root / "docs/analysis/product-v02321-progress.json")
    body = dict(current)
    supplied = body.pop("progress_sha256", None)
    if supplied != semantic_sha256_v22(body):
        raise ValueError("Product v0.2.3.2.1 progress digest differs")
    body.update(
        {
            "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V02321,
            "increment": 4,
            "infrastructure_session_count": 2,
            "formal_state_clone_status": "PASS",
            "formal_state_clone_report_sha256": clone_report.report_sha256,
            "formal_state_clone_sha256": clone_report.clone.clone_sha256,
            "runtime_authority_status": "PASS",
            "runtime_authority_proof_sha256": authority_proof.proof_sha256,
            "baseline_restart_status": "PASS",
            "baseline_restart_proof_sha256": restart_proof.proof_sha256,
            "formal_traffic_status": "PASS",
            "formal_traffic_result_sha256": traffic.result_sha256,
            "formal_healthy_traffic_execution_count": 1,
            "fresh_runtime_snapshot_status": "PASS",
            "fresh_runtime_snapshot_proof_sha256": (
                result.fresh_runtime_snapshot_proof_sha256
            ),
            "formal_poststate_sha256": result.formal_poststate_sha256,
            "source_poststate_sha256": result.source_poststate_sha256,
            "accepted_successor_incident_count": 1,
            "successor_diagnosis_count": 1,
            "measured_terminal": result.measured_terminal,
            "nofault_acceptance_result_sha256": result.result_sha256,
            "fault_attempt_count": 0,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "knowledge_loop_campaign_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "provider_calls": 0,
            "action_authority": "NONE",
        }
    )
    progress = {**body, "progress_sha256": semantic_sha256_v22(body)}
    return FormalProgressV02321.model_validate(progress).model_dump(mode="json")


def run_formal_nofault_v02321(
    *,
    project_root: Path,
    predecessor_root: Path | None = None,
    source_product_root: Path | None = None,
    predecessor_private_acceptance: Path | None = None,
) -> NoFaultAcceptanceResultV02321:
    root = Path(project_root).resolve(strict=True)
    result_path = root / "docs/results/product-v02321-nofault-acceptance.json"
    private_root = root / _PRIVATE_LOCATOR_V02321
    if private_root.exists() or private_root.is_symlink():
        if private_root.is_symlink() or not private_root.is_dir():
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE: "
                "formal private root differs"
            )
        reservation_path = root / _RESERVATION_LOCATOR_V02321
        if not reservation_path.is_file() or reservation_path.is_symlink():
            raise RuntimeError(
                "Product v0.2.3.2.1 formal execution already began "
                "without a valid reservation"
            )
        return _recover_interrupted_private_run_v02321(
            root=root,
            private_root=private_root,
        )
    if result_path.exists() or result_path.is_symlink():
        raise FileExistsError(
            "Product v0.2.3.2.1 result exists without private publication"
        )

    predecessor = (
        Path(predecessor_root).resolve(strict=True)
        if predecessor_root is not None
        else _worktree_for_branch(root, _SOURCE_BRANCH)
    )
    source_root = (
        Path(source_product_root).resolve(strict=True)
        if source_product_root is not None
        else (predecessor / _SOURCE_LOCATOR).resolve(strict=True)
    )
    private_acceptance = (
        Path(predecessor_private_acceptance).resolve(strict=True)
        if predecessor_private_acceptance is not None
        else (
            _worktree_for_branch(root, _PRIVATE_ACCEPTANCE_BRANCH)
            / _PRIVATE_ACCEPTANCE_LOCATOR
        ).resolve(strict=True)
    )
    _require_preserved_runtime_root_v02321(predecessor, source_root)

    reservation_path = root / _RESERVATION_LOCATOR_V02321
    if reservation_path.exists() or reservation_path.is_symlink():
        reservation = _load_canonical_reservation_v02321(root)
        admission = reservation.admission
        clone_report_bytes = _validated_formal_clone_report_bytes_v02321(
            root=root,
            admission=admission,
        )
        allowed_public_files = (
            None
            if clone_report_bytes is None
            else {
                "docs/analysis/product-v02321-product-state-clone-formal.json": (
                    clone_report_bytes
                )
            }
        )
        freeze = _verify_admission_after_reservation(
            root,
            admission,
            allowed_public_files=allowed_public_files,
        )
    else:
        admission, freeze, _review = _strict_admission(root)
        reservation = FormalCloneReservationV02321.build(admission=admission)
        write_private_json(
            reservation_path,
            reservation.model_dump(mode="json"),
            create_once=True,
        )
    source_before = _admit_state(source_root, locator=_SOURCE_LOCATOR)
    if source_before.source_sha256 != freeze.source_state_sha256:
        raise ValueError("Product v0.2.3.2.1 frozen source state differs")
    clone_report = create_formal_state_clone_v02321(
        project_root=root,
        source_root=source_root,
        predecessor_private_acceptance=private_acceptance,
        admission=admission,
        strict_gate_already_verified=True,
    )

    product_data_root = root / clone_report.destination_locator
    product_before = _admit_state(
        product_data_root, locator=clone_report.destination_locator
    )
    before_counts = _read_only_database_counts(product_data_root, ENVIRONMENT_ID_V0232)
    if (
        product_before != clone_report.destination_state
        or before_counts["incident_count"] != 1
        or before_counts["diagnosis_count"] != 1
        or before_counts["fault_family_count"] != 0
        or before_counts["knowledge_artifact_count"] != 0
        or before_counts["baseline_count"] != 1
        or before_counts["baseline_job_count"] != 1
        or before_counts["verify_job_count"] != 1
        or any(
            before_counts[name]
            for name in (
                "pending_job_count",
                "running_job_count",
                "failed_job_count",
            )
        )
    ):
        raise ValueError("Product v0.2.3.2.1 formal clone starting state differs")
    diagnosis_job_count_before = _source_diagnosis_job_count(product_data_root)
    if diagnosis_job_count_before != 1:
        raise ValueError("Product v0.2.3.2.1 starting Diagnosis jobs differ")

    campaign_sha256 = _load_successor_campaign_sha256(root)
    typed_plan = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256=campaign_sha256,
        role="FORMAL",
        state_clone_sha256=clone_report.clone.clone_sha256,
        attempt_ordinal=1,
    )
    runtime_request = materialize_planned_request_v02321(
        typed_plan, tool_name="inspect_service_runtime"
    )
    manifest = _object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    context = ProductBaselineContinuationContextV0231.model_validate(
        _object(root / "docs/analysis/product-v0231-baseline-continuation-context.json")
    )
    tracked_runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _object(root / "docs/analysis/product-v0231-runtime-authority-descriptor.json")
    )
    bundle = load_bundle(
        predecessor / "config/live-telemetry-controlled-remediation-v1"
    )
    preserved_authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=binding,
    )
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=private_root / "demo",
        binding=binding,
        context=context,
        bundle=bundle,
        preserved_authority=preserved_authority,
        preserved_resolved_compose=resolved_compose,
    )
    formal_profile = load_traffic_profile_v0232(root, role="FORMAL")
    contract = load_checkout_traffic_contract_v0232(root)
    if (
        formal_profile.profile_sha256 != freeze.formal_profile_sha256
        or contract.contract_sha256 != freeze.traffic_contract_sha256
        or formal_profile.transactions != 30
        or formal_profile.minimum_full_episode_duration_seconds != 300
    ):
        raise ValueError("Product v0.2.3.2.1 frozen formal profile differs")

    _attempt, audit, _source_data_root = _attempt_context(predecessor)
    if audit.environment_id != ENVIRONMENT_ID_V0232:
        raise ValueError("Product v0.2.3.2.1 formal environment differs")
    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )
    if (
        _database_owner_count(product_data_root / "product.sqlite3") != 0
        or processes.cleanup_observation().get("verdict") != "CLEAN"
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_PRODUCT_RESTART")

    private_root.mkdir(parents=True, mode=0o700)

    authority_output: RuntimeAuthorityProofV02321 | None = None
    restart_output: BaselineRestartProofV02321 | None = None
    traffic_result: FormalTrafficResultV02321 | None = None
    runtime_snapshot_proof: FreshRuntimeSnapshotProofV02321 | None = None
    incident_binding: IncidentTrafficBindingV0232 | None = None
    incident: IncidentRecordV1 | None = None
    diagnosis: DiagnosisResultV1 | None = None
    evidence: EvidenceBundleV1 | None = None
    index: DiagnosisEvidenceIndexV0232 | None = None
    decision_trace: DiagnosisDecisionTraceV0232 | None = None
    assessment: NoFaultEvidenceAssessmentV0232 | None = None
    result: NoFaultAcceptanceResultV02321 | None = None
    consumption: FormalTrafficConsumptionV02321 | None = None
    execution: HealthyTrafficExecutionV0232 | None = None
    traffic_observations: list[CheckoutTransactionObservationV0232] = []
    traffic_dispatch_checkpoints: list[FormalTrafficDispatchCheckpointV02321] = []
    traffic_observation_checkpoints: list[FormalTrafficObservationCheckpointV02321] = []
    traffic_journal_state: dict[str, Any] = {
        "stage": "CONSUMED_BEFORE_FIRST_CART",
        "pending_dispatch_ordinal": None,
        "remote_delivery": "NOT_STARTED",
    }
    outer_baseline_before: str | None = None
    outer_baseline_after: str | None = None
    queue_before_sha256: str | None = None
    queue_after_sha256: str | None = None
    product_cleanup: Mapping[str, Any] = {"verdict": "BLOCKED"}
    demo_cleanup: Any = None
    live_error: BaseException | None = None
    stage = "ADMITTED"
    try:
        write_private_json(
            private_root / "admission.json",
            admission.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "typed-request-plan.json",
            typed_plan.model_dump(mode="json"),
            create_once=True,
        )
        lifecycle.admit_prestart()
        if lifecycle.runtime_descriptor != tracked_runtime:
            raise ValueError("Product v0.2.3.2.1 Runtime descriptor differs")
        lifecycle.start()
        stage = "SANDBOX_STARTED"
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if lifecycle.rebound_authority != preserved_authority:
            raise ValueError("Product v0.2.3.2.1 fresh Runtime authority differs")
        checkout = _checkout_runtime(backend, runtime_request)
        if checkout != ("RUNNING", True, 0):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_RUNTIME_AUTHORITY")
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=formal_profile.queue_fault_flag,
        )
        queue_before_sha256 = queue_before.before_sha256
        outer_baseline_before = lifecycle.read_baseline_sha256()

        runtime_path = product_data_root / "pilot/runtime-readiness.json"
        first_rotation = _rotate_runtime_snapshot_v0231(
            data_root=product_data_root,
            path=runtime_path,
            snapshot=_runtime_snapshot(
                backend=backend,
                authority=preserved_authority,
            ),
            private_root=private_root,
            ordinal=1,
        )
        inner_authority = _authority_proof(
            descriptor=tracked_runtime,
            authority=preserved_authority,
            backend=backend,
            rotation=first_rotation,
        )
        authority_output = RuntimeAuthorityProofV02321.build(
            execution_head=admission.execution_head,
            admission_sha256=admission.admission_sha256,
            continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            inner_proof=inner_authority,
            checkout_state=checkout[0],
            checkout_healthy=checkout[1],
            checkout_restart_count=checkout[2],
        )
        write_private_json(
            private_root / "runtime-authority.json",
            authority_output.model_dump(mode="json"),
            create_once=True,
        )
        stage = "RUNTIME_AUTHORITY_VERIFIED"

        processes.start()
        before_restart = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=context.service_identity_sha256,
            baseline_candidate_identity_sha256=audit.service_identity_sha256,
            capability_sha256=context.capability_sha256,
        )
        processes.restart()
        identity, capability, candidate = _load_persisted_bindings(
            product_data_root, audit
        )
        rebound_audit = ProductBaselineReadinessAuditV023.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/baselines/{audit.baseline_id}/window-audit-v023",
            )
        )
        if (
            identity.identity_sha256 != context.service_identity_sha256
            or capability.capability_sha256 != context.capability_sha256
            or candidate != audit.service_identity_sha256
            or rebound_audit.audit_sha256 != audit.audit_sha256
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_PRODUCT_RESTART")
        after_restart = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=identity.identity_sha256,
            baseline_candidate_identity_sha256=candidate,
            capability_sha256=capability.capability_sha256,
        )
        inner_restart = BaselineRestartProofV023.build(
            before=before_restart,
            after=after_restart,
            connector_verification_count=0,
        )
        after_restart_counts = _database_counts(product_data_root, audit.environment_id)
        pending, running, failed = _queue_counts(product_data_root)
        if any(
            after_restart_counts[name] != before_counts[name]
            for name in (
                "incident_count",
                "diagnosis_count",
                "fault_family_count",
                "knowledge_artifact_count",
                "baseline_count",
                "baseline_job_count",
                "verify_job_count",
            )
        ) or any((pending, running, failed)):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_PRODUCT_RESTART")
        inner_restart_v0231 = BaselineRestartProofV0231.build(
            inner_proof=inner_restart,
            active_profile_sha256=audit.active_opensearch_profile_sha256,
            readiness_audit_sha256=audit.audit_sha256,
            baseline_job_count_before=before_counts["baseline_job_count"],
            baseline_job_count_after=after_restart_counts["baseline_job_count"],
            verify_job_count_before=before_counts["verify_job_count"],
            verify_job_count_after=after_restart_counts["verify_job_count"],
            pending_jobs_after=pending,
            running_jobs_after=running,
            failed_jobs_after=failed,
            terminal="ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS",
        )
        restart_output = BaselineRestartProofV02321.build(
            execution_head=admission.execution_head,
            admission_sha256=admission.admission_sha256,
            active_baseline_id=audit.baseline_id,
            active_baseline_sha256=audit.baseline_sha256,
            active_profile_sha256=audit.active_opensearch_profile_sha256,
            inner_proof=inner_restart_v0231,
            new_baseline_count=0,
        )
        write_private_json(
            private_root / "baseline-restart.json",
            restart_output.model_dump(mode="json"),
            create_once=True,
        )
        stage = "BASELINE_RESTART_VERIFIED"

        episode_started_at = datetime.now(UTC)
        episode_started_monotonic = time.monotonic()
        consumption = FormalTrafficConsumptionV02321.build(
            admission_sha256=admission.admission_sha256,
            execution_head=admission.execution_head,
            traffic_contract_sha256=contract.contract_sha256,
            formal_profile_sha256=formal_profile.profile_sha256,
            episode_started_at=episode_started_at,
        )
        write_private_json(
            private_root / "traffic-consumption.json",
            consumption.model_dump(mode="json"),
            create_once=True,
        )
        stage = "FORMAL_TRAFFIC_CONSUMED"

        def persist_traffic_checkpoint(name: str, payload: Mapping[str, Any]) -> None:
            write_private_json(
                private_root / "traffic-journal" / name,
                dict(payload),
                create_once=True,
            )

        with HealthyTrafficRunnerV0232() as runner:
            execution = run_formal_traffic_journaled_v02321(
                runner=runner,
                endpoint=_ENDPOINT_V02321,
                profile=formal_profile,
                contract=contract,
                consumption=consumption,
                dispatch_checkpoints=traffic_dispatch_checkpoints,
                observation_checkpoints=traffic_observation_checkpoints,
                observations=traffic_observations,
                state=traffic_journal_state,
                persist=persist_traffic_checkpoint,
            )
        try:
            write_private_json(
                private_root / "traffic-execution.json",
                execution.model_dump(mode="json"),
                create_once=True,
            )
        except BaseException:
            traffic_journal_state["stage"] = "EXECUTION_PERSISTENCE_FAILED"
            raise
        if not execution.run.passed:
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC")
        stage = "FORMAL_TRAFFIC_EXECUTION_PASS"
        remaining = formal_profile.minimum_full_episode_duration_seconds - (
            time.monotonic() - episode_started_monotonic
        )
        if remaining > 0:
            time.sleep(remaining)
        episode_ended_at = datetime.now(UTC)
        monotonic_duration_ms = int(
            (time.monotonic() - episode_started_monotonic) * 1000
        )
        traffic_candidate = FormalTrafficResultV02321.build(
            admission_sha256=admission.admission_sha256,
            consumption_sha256=consumption.consumption_sha256,
            execution=execution,
            episode_started_at=episode_started_at,
            episode_ended_at=episode_ended_at,
            monotonic_duration_ms=monotonic_duration_ms,
        )
        write_private_json(
            private_root / "formal-traffic.json",
            traffic_candidate.model_dump(mode="json"),
            create_once=True,
        )
        traffic_result = traffic_candidate
        stage = "FORMAL_TRAFFIC_PASS"

        checkout_after = _checkout_runtime(backend, runtime_request)
        if checkout_after != ("RUNNING", True, 0):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_RUNTIME_AUTHORITY")
        fresh_runtime_snapshot = _runtime_snapshot(
            backend=backend,
            authority=preserved_authority,
        )
        second_rotation = _rotate_runtime_snapshot_v0231(
            data_root=product_data_root,
            path=runtime_path,
            snapshot=fresh_runtime_snapshot,
            private_root=private_root,
            ordinal=2,
        )
        if second_rotation["after_snapshot_sha256"] != (
            fresh_runtime_snapshot.snapshot_sha256
        ):
            raise ValueError("Product v0.2.3.2.1 fresh Runtime snapshot differs")
        runtime_snapshot_proof = FreshRuntimeSnapshotProofV02321.build(
            execution_head=admission.execution_head,
            traffic_result_sha256=traffic_result.result_sha256,
            snapshot=fresh_runtime_snapshot,
            runtime_authority_sha256=(preserved_authority.pilot_authority_sha256),
            runtime_continuity_descriptor_sha256=(tracked_runtime.descriptor_sha256),
            connector_binding_sha256=(preserved_authority.connector_binding_sha256),
        )
        write_private_json(
            private_root / "fresh-runtime-snapshot.json",
            runtime_snapshot_proof.model_dump(mode="json"),
            create_once=True,
        )
        stage = "FRESH_RUNTIME_SNAPSHOT_VERIFIED"

        incident_ended_at = fresh_runtime_snapshot.observed_at

        external_incident_key = (
            f"product-v02321-nofault-{admission.admission_sha256[:16]}"
        )
        incident = _request_or_recover_incident_v02321(
            request=lambda: _request_json(
                processes,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": audit.environment_id,
                    "external_incident_key": external_incident_key,
                    "alert_name": "Product v0.2.3.2.1 No-Fault acceptance",
                    "summary": (
                        "Fresh formal healthy checkout observation with no fault active."
                    ),
                    "started_at": episode_started_at.isoformat(),
                    "ended_at": incident_ended_at.isoformat(),
                    "candidate_service_ids": list(audit.baseline_entity_service_ids),
                    "labels": {"fault": "none"},
                },
            ),
            recover=lambda: _recover_incident_by_external_key(
                product_data_root,
                environment_id=audit.environment_id,
                external_incident_key=external_incident_key,
            ),
        )
        write_private_json(
            private_root / "incident.json",
            incident.model_dump(mode="json"),
            create_once=True,
        )
        if (
            incident.service_identity_sha256 != context.service_identity_sha256
            or incident.source_capability_sha256 != context.capability_sha256
            or incident.baseline_id != audit.baseline_id
            or incident.baseline_sha256 != audit.baseline_sha256
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
        incident_binding = IncidentTrafficBindingV0232.build(
            incident_id=incident.incident_id,
            execution=execution,
            episode_started_at=episode_started_at,
            episode_ended_at=episode_ended_at,
        )
        write_private_json(
            private_root / "incident-traffic-binding.json",
            incident_binding.model_dump(mode="json"),
            create_once=True,
        )
        stage = "INCIDENT_CREATED"

        queued = _request_or_recover_diagnosis_job_v02321(
            request=lambda: _request_json(
                processes,
                "POST",
                f"/v1/incidents/{incident.incident_id}/diagnosis-jobs",
                payload=None,
            ),
            recover=lambda: _recover_diagnosis_job(
                product_data_root,
                incident_id=incident.incident_id,
            ),
        )
        write_private_json(
            private_root / "diagnosis-job.json",
            queued.model_dump(mode="json"),
            create_once=True,
        )
        diagnosis_job = _wait_job(
            processes,
            queued.job_id,
            data_root=product_data_root,
            timeout_seconds=240,
        )
        write_private_json(
            private_root / "diagnosis-job-completion.json",
            diagnosis_job.model_dump(mode="json"),
            create_once=True,
        )
        if diagnosis_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            diagnosis_job.result, dict
        ):
            raise RuntimeError(
                diagnosis_job.safe_error_code
                or "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
            )
        diagnosis = DiagnosisResultV1.model_validate(diagnosis_job.result)
        write_private_json(
            private_root / "diagnosis.json",
            diagnosis.model_dump(mode="json"),
            create_once=True,
        )
        evidence = EvidenceBundleV1.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/incidents/{incident.incident_id}/evidence",
            )
        )
        index = DiagnosisEvidenceIndexV0232.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/incidents/{incident.incident_id}/evidence-index",
            )
        )
        decision_trace = _find_decision_trace(
            product_data_root,
            expected_sha256=index.decision_trace_sha256,
        )
        assessment = score_nofault_evidence_v0232(
            diagnosis=diagnosis,
            bundle=evidence,
            index=index,
            decision_trace=decision_trace,
        )
        if (
            diagnosis.incident_id != incident.incident_id
            or evidence.incident_id != incident.incident_id
            or index.incident_id != incident.incident_id
            or decision_trace.incident_id != incident.incident_id
            or assessment.incident_id != incident.incident_id
            or evidence.diagnosis_id != diagnosis.diagnosis_id
            or index.diagnosis_id != diagnosis.diagnosis_id
            or decision_trace.diagnosis_id != diagnosis.diagnosis_id
            or assessment.diagnosis_id != diagnosis.diagnosis_id
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
        write_private_json(
            private_root / "evidence-bundle.json",
            evidence.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "evidence-index.json",
            index.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "decision-trace.json",
            decision_trace.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "assessment.json",
            assessment.model_dump(mode="json"),
            create_once=True,
        )
        after_counts = _database_counts(product_data_root, audit.environment_id)
        pending, running, failed = _queue_counts(product_data_root)
        if (
            after_counts["incident_count"] != 2
            or after_counts["diagnosis_count"] != 2
            or after_counts["fault_family_count"] != 0
            or after_counts["knowledge_artifact_count"] != 0
            or _source_diagnosis_job_count(product_data_root) != 2
            or any((pending, running, failed))
            or diagnosis.provider_calls != 0
            or diagnosis.agent_writes != 0
            or diagnosis.runbook_executions != 0
            or diagnosis.action_authority.value != "NONE"
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE")
        stage = "DIAGNOSIS_COMPLETED"
    except BaseException as error:
        live_error = error
    finally:
        try:
            if queue_before_sha256 is not None:
                queue_after = verify_queue_default_v021(
                    lifecycle.flag_file,
                    expected_default_value=formal_profile.queue_fault_flag,
                    expected_sha256=queue_before_sha256,
                )
                queue_after_sha256 = queue_after.after_sha256
            if outer_baseline_before is not None:
                outer_baseline_after = lifecycle.read_baseline_sha256()
        except BaseException as error:
            if live_error is None:
                live_error = error
        try:
            product_cleanup = processes.cleanup_observation()
        except BaseException as error:
            if live_error is None:
                live_error = error
        try:
            demo_cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=(
                    outer_baseline_before is not None
                    and outer_baseline_before == outer_baseline_after
                )
            )
        except BaseException as error:
            if live_error is None:
                live_error = error

    source_after: ProductStateSourceV0232 | None = None
    try:
        source_after = _admit_state(source_root, locator=_SOURCE_LOCATOR)
        write_private_json(
            private_root / "source-poststate.json",
            source_after.model_dump(mode="json"),
            create_once=True,
        )
        if source_after != source_before:
            raise ValueError("Product v0.2.3.2.1 source state changed")
    except BaseException as error:
        if live_error is None:
            live_error = error
    closure_clean = (
        product_cleanup.get("verdict") == "CLEAN"
        and getattr(demo_cleanup, "verdict", None) == "CLEAN"
        and getattr(demo_cleanup, "owned_containers", None) == 0
        and getattr(demo_cleanup, "owned_networks", None) == 0
        and getattr(demo_cleanup, "owned_volumes", None) == 0
        and product_cleanup.get("owned_host_processes") == 0
        and product_cleanup.get("non_owned_resources_changed") is False
        and getattr(demo_cleanup, "non_owned_resources_changed", None) is False
        and queue_before_sha256 is not None
        and queue_before_sha256 == queue_after_sha256
        and outer_baseline_before is not None
        and outer_baseline_before == outer_baseline_after
        and _database_owner_count(product_data_root / "product.sqlite3") == 0
    )
    if not closure_clean and live_error is None:
        live_error = RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
        )
    final_state = None
    final_counts: dict[str, int] | None = None
    if live_error is None:
        try:
            final_state = admit_formal_product_poststate_v02321(
                product_data_root,
                state_locator=clone_report.destination_locator,
                expected_environment_id=ENVIRONMENT_ID_V0232,
                expected_baseline_id=BASELINE_ID_V0232,
                expected_baseline_sha256=BASELINE_SHA256_V0232,
                expected_profile_sha256=PROFILE_SHA256_V0232,
                expected_pilot_runtime_authority_sha256=(
                    PILOT_RUNTIME_AUTHORITY_SHA256_V0232
                ),
                expected_runtime_connector_binding_sha256=(
                    RUNTIME_CONNECTOR_BINDING_SHA256_V0232
                ),
            )
            final_counts = _database_counts(product_data_root, audit.environment_id)
            if (
                final_state.counts.incident_count != 2
                or final_state.counts.diagnosis_count != 2
                or final_state.counts.fault_family_count != 0
                or final_state.counts.knowledge_artifact_count != 0
                or final_state.active_baseline_id
                != clone_report.destination_state.source_active_baseline_id
                or final_state.active_baseline_sha256
                != clone_report.destination_state.source_active_baseline_sha256
                or final_state.profile_sha256
                != clone_report.destination_state.source_profile_sha256
            ):
                raise RuntimeError(
                    "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
                )
            write_private_json(
                private_root / "formal-poststate.json",
                final_state.model_dump(mode="json"),
                create_once=True,
            )
        except BaseException as error:
            live_error = error
    if live_error is not None:
        blocker_terminal = _seal_formal_failure_v02321(
            root=root,
            private_root=private_root,
            admission=admission,
            live_error=live_error,
            stage=stage,
            product_data_root=product_data_root,
            environment_id=audit.environment_id,
            consumption=consumption,
            traffic_result=traffic_result,
            execution=execution,
            dispatch_checkpoints=traffic_dispatch_checkpoints,
            observation_checkpoints=traffic_observation_checkpoints,
            traffic_journal_state=traffic_journal_state,
            product_cleanup=product_cleanup,
            demo_cleanup=demo_cleanup,
            queue_before_sha256=queue_before_sha256,
            queue_after_sha256=queue_after_sha256,
            outer_baseline_before_sha256=outer_baseline_before,
            outer_baseline_after_sha256=outer_baseline_after,
            source_before=source_before,
            source_after=source_after,
        )
        raise RuntimeError(blocker_terminal) from live_error

    if any(
        value is None
        for value in (
            authority_output,
            restart_output,
            traffic_result,
            runtime_snapshot_proof,
            incident_binding,
            incident,
            diagnosis,
            evidence,
            index,
            decision_trace,
            assessment,
            source_after,
            final_state,
            final_counts,
        )
    ):
        raise AssertionError("Product v0.2.3.2.1 formal result is incomplete")
    assert authority_output is not None
    assert restart_output is not None
    assert traffic_result is not None
    assert runtime_snapshot_proof is not None
    assert incident_binding is not None
    assert incident is not None
    assert diagnosis is not None
    assert evidence is not None
    assert index is not None
    assert decision_trace is not None
    assert assessment is not None
    assert source_after is not None
    assert final_state is not None
    assert final_counts is not None
    result = NoFaultAcceptanceResultV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        formal_clone_report_sha256=clone_report.report_sha256,
        formal_poststate_sha256=final_state.poststate_sha256,
        source_poststate_sha256=source_after.source_sha256,
        runtime_authority_proof_sha256=authority_output.proof_sha256,
        baseline_restart_proof_sha256=restart_output.proof_sha256,
        formal_traffic_result_sha256=traffic_result.result_sha256,
        fresh_runtime_snapshot_proof_sha256=runtime_snapshot_proof.proof_sha256,
        incident_traffic_binding_sha256=incident_binding.binding_sha256,
        incident_id=incident.incident_id,
        diagnosis_id=diagnosis.diagnosis_id,
        diagnosis_incident_id=diagnosis.incident_id,
        evidence_incident_id=evidence.incident_id,
        evidence_diagnosis_id=evidence.diagnosis_id,
        index_incident_id=index.incident_id,
        index_diagnosis_id=index.diagnosis_id,
        trace_incident_id=decision_trace.incident_id,
        trace_diagnosis_id=decision_trace.diagnosis_id,
        assessment_incident_id=assessment.incident_id,
        assessment_diagnosis_id=assessment.diagnosis_id,
        diagnosis_result_sha256=diagnosis.result_sha256,
        evidence_bundle_sha256=semantic_sha256_v22(evidence.model_dump(mode="json")),
        evidence_index_sha256=index.index_sha256,
        decision_trace_sha256=decision_trace.trace_sha256,
        assessment_sha256=assessment.result_sha256,
        source_assessment_terminal=assessment.terminal,
        measured_terminal=measured_terminal_v02321(assessment.terminal),
        source_incident_count_after=source_after.source_counts.incident_count,
        source_diagnosis_count_after=source_after.source_counts.diagnosis_count,
        starting_incident_count=before_counts["incident_count"],
        starting_diagnosis_count=before_counts["diagnosis_count"],
        ending_incident_count=final_counts["incident_count"],
        ending_diagnosis_count=final_counts["diagnosis_count"],
        fault_family_count=final_counts["fault_family_count"],
        knowledge_artifact_count=final_counts["knowledge_artifact_count"],
        fault_attempt_count=0,
        knowledge_loop_campaign_count=0,
        agent_writes=diagnosis.agent_writes,
        runbook_executions=diagnosis.runbook_executions,
        provider_calls=diagnosis.provider_calls,
        action_authority=diagnosis.action_authority.value,
        product_cleanup="CLEAN",
        demo_cleanup="CLEAN",
        source_product_state_unchanged=True,
    )
    progress = _updated_progress(
        root,
        result=result,
        clone_report=clone_report,
        authority_proof=authority_output,
        restart_proof=restart_output,
        traffic=traffic_result,
    )
    public_files = {
        "docs/analysis/product-v02321-baseline-restart.json": (
            canonical_json_bytes(restart_output.model_dump(mode="json"))
        ),
        "docs/analysis/product-v02321-formal-traffic.json": (
            canonical_json_bytes(traffic_result.model_dump(mode="json"))
        ),
        "docs/analysis/product-v02321-fresh-runtime-snapshot.json": (
            canonical_json_bytes(runtime_snapshot_proof.model_dump(mode="json"))
        ),
        "docs/analysis/product-v02321-product-state-clone-formal.json": (
            root / "docs/analysis/product-v02321-product-state-clone-formal.json"
        ).read_bytes(),
        "docs/analysis/product-v02321-progress.json": canonical_json_bytes(progress),
        "docs/analysis/product-v02321-runtime-authority.json": (
            canonical_json_bytes(authority_output.model_dump(mode="json"))
        ),
        "docs/results/product-v02321-nofault-acceptance.json": (
            canonical_json_bytes(result.model_dump(mode="json"))
        ),
    }
    private_files = {
        path: (
            canonical_json_bytes(result.model_dump(mode="json"))
            if path == "acceptance.json"
            else _require_regular_path(
                root=private_root,
                path=private_root / path,
                directory=False,
                label=f"private publication evidence {path}",
            ).read_bytes()
        )
        for path in _PRIVATE_PUBLICATION_FILES
    }
    _freeze_publication_bundle(
        project_root=root,
        private_root=private_root,
        execution_head=admission.execution_head,
        private_files=private_files,
        public_files=public_files,
    )
    recovered = recover_formal_publication_v02321(project_root=root)
    if recovered != result:
        raise ValueError("Product v0.2.3.2.1 recovered result differs")
    return recovered


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--predecessor-root", type=Path)
    parser.add_argument("--source-product-root", type=Path)
    parser.add_argument("--predecessor-private-acceptance", type=Path)
    arguments = parser.parse_args(argv)
    result = run_formal_nofault_v02321(
        project_root=arguments.project_root,
        predecessor_root=arguments.predecessor_root,
        source_product_root=arguments.source_product_root,
        predecessor_private_acceptance=arguments.predecessor_private_acceptance,
    )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "recover_formal_publication_v02321",
    "run_formal_nofault_v02321",
)
