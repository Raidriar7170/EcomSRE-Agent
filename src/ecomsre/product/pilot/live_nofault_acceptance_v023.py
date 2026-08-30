"""Resume the passing v0.2.3 Baseline for restart and one No-Fault episode."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Mapping

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.baselines import BaselineRepositoryV1
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.jobs.contracts import ProductJobStatusV1
from ecomsre.product.pilot.baseline_attempts_v023 import (
    BASELINE_READINESS_PASS_V023,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
    verify_queue_default_v021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditRepositoryV023,
    ProductBaselineReadinessAuditV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    PRODUCT_V023_ENVIRONMENT_PATH,
    PRODUCT_V023_RESTART_PATH,
    _ProductHostProcessesV023,
    _atomic_write,
    _baseline_candidate_identity_sha256_v023,
    _file_sha256,
    _load_public_attempts,
    _queue_counts,
    _request_json,
    _require_clean_head,
    _restart_snapshot,
    _wait_job,
    _write_json,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _authority_inputs,
    _runtime_services,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NOFAULT_FULLY_SUPPORTED_V023,
    NoFaultAcceptanceResultV023,
    NoFaultCapabilityAssessmentV023,
    NoFaultExecutionProfileV023,
    NoFaultQueueSnapshotV023,
    NoFaultTrafficResultV023,
    _successful_evidence_sources,
    score_nofault_v023,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    load_pilot_runtime_authority_v02,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOFAULT_ACCEPTANCE_COMPLETE_V023 = (
    "ECOMSRE_PRODUCT_V023_NOFAULT_ACCEPTANCE_COMPLETE"
)
NOFAULT_INFRASTRUCTURE_BLOCKED_V023 = (
    "BLOCKED_ECOMSRE_PRODUCT_V023_NOFAULT_INFRASTRUCTURE"
)
KNOWLEDGE_LOOP_HANDOFF_READY_V023 = (
    "ECOMSRE_PRODUCT_V023_KNOWLEDGE_LOOP_HANDOFF_READY"
)
KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023 = (
    "ECOMSRE_PRODUCT_V023_KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED"
)
PRODUCT_V023_NOFAULT_PROFILE_PATH = Path("config/product-v023/nofault/profile.json")
PRODUCT_V023_NOFAULT_RESULT_PATH = Path(
    "docs/results/product-v023-nofault-acceptance.json"
)
PRODUCT_V023_NOFAULT_MARKDOWN_PATH = Path(
    "docs/results/product-v023-nofault-acceptance.md"
)
PRODUCT_V023_LIMITATIONS_PATH = Path("docs/results/product-v023-limitations.md")
PRODUCT_V023_INTERVIEW_BRIEF_PATH = Path(
    "docs/results/product-v023-interview-brief.md"
)
PRODUCT_V023_HANDOFF_PATH = Path(
    "docs/analysis/product-v023-knowledge-loop-handoff.json"
)
PRODUCT_V023_HANDOFF_MARKDOWN_PATH = Path(
    "docs/analysis/product-v023-knowledge-loop-handoff.md"
)
PRODUCT_V023_PROGRESS_PATH = Path("docs/analysis/product-v023-progress.json")
_EPISODE_MINIMUM_SECONDS_V023 = 300


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3 object is invalid: {path}")
    return payload


def _attempt_context(root: Path):
    attempts = _load_public_attempts(root)
    if len(attempts) != 1:
        raise ValueError("Product v0.2.3 requires exactly one Baseline attempt")
    attempt = attempts[0]
    completion = attempt.completion
    audit = completion.per_window_audit
    if (
        completion.terminal != BASELINE_READINESS_PASS_V023
        or completion.cleanup != "CLEAN"
        or audit is None
        or not audit.final_builder_would_pass
        or completion.active_baseline_id != audit.baseline_id
        or completion.active_baseline_sha256 != audit.baseline_sha256
    ):
        raise ValueError("Product v0.2.3 passing Baseline evidence is absent")
    product_data_root = Path(attempt.start.product_data_root).resolve(strict=True)
    allowed = (root / ".local/product-v023/baseline-readiness/runs").resolve()
    if (
        not product_data_root.is_relative_to(allowed)
        or product_data_root.name != "product"
        or product_data_root.is_symlink()
    ):
        raise ValueError("Product v0.2.3 Baseline data root is outside its campaign")
    private_report = product_data_root.parent / "private/attempt-completion.json"
    public_attempt = _read_json_object(
        root / "docs/analysis/product-v023-baseline-attempt-1.json"
    )
    if (
        not private_report.is_file()
        or public_attempt.get("private_report_sha256") != _file_sha256(private_report)
    ):
        raise ValueError("Product v0.2.3 private Baseline evidence binding differs")
    return attempt, audit, product_data_root


def _database_counts(data_root: Path, environment_id: str) -> dict[str, int]:
    store = SqliteStoreV1(data_root / "product.sqlite3")
    with store.connect() as connection:
        counts = {
            "incident_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM incidents WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "diagnosis_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_results d "
                    "JOIN incidents i ON i.incident_id = d.incident_id "
                    "WHERE i.environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "fault_family_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM fault_families WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "baseline_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM baseline_versions WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()[0]
            ),
            "baseline_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs WHERE job_type = 'BASELINE_BUILD'"
                ).fetchone()[0]
            ),
            "verify_job_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_jobs "
                    "WHERE job_type = 'ENVIRONMENT_VERIFY'"
                ).fetchone()[0]
            ),
        }
        knowledge_tables = (
            "fault_families",
            "registration_drafts",
            "shadow_evaluations",
            "promotion_records",
        )
        counts["knowledge_artifact_count"] = sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in knowledge_tables
        )
    return counts


def _load_persisted_bindings(
    data_root: Path,
    audit: ProductBaselineReadinessAuditV023,
):
    store = SqliteStoreV1(data_root / "product.sqlite3")
    identity = ServiceCatalogRepositoryV1(store).get_map(audit.environment_id)
    capability = CapabilityMatrixRepositoryV1(store).get(audit.environment_id)
    persisted_audit = ProductBaselineReadinessAuditRepositoryV023(store).get_by_baseline(
        audit.baseline_id
    )
    baseline = BaselineRepositoryV1(store).get_active(audit.environment_id)
    candidate_sha256 = _baseline_candidate_identity_sha256_v023(identity, audit)
    if (
        persisted_audit.audit_sha256 != audit.audit_sha256
        or baseline.baseline_id != audit.baseline_id
        or baseline.baseline_sha256 != audit.baseline_sha256
        or capability.capability_sha256 != audit.capability_sha256
    ):
        raise ValueError("Product v0.2.3 persisted Baseline binding differs")
    return identity, capability, candidate_sha256


def verify_live_nofault_contract_v023(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    if (repository / PRODUCT_V023_NOFAULT_RESULT_PATH).is_file():
        return verify_frozen_nofault_acceptance_v023(repository)
    _attempt, audit, product_data_root = _attempt_context(repository)
    profile = NoFaultExecutionProfileV023.load(
        repository / PRODUCT_V023_NOFAULT_PROFILE_PATH
    )
    identity, capability, candidate_sha256 = _load_persisted_bindings(
        product_data_root,
        audit,
    )
    counts = _database_counts(product_data_root, audit.environment_id)
    if counts["baseline_count"] != 1 or counts["baseline_job_count"] != 1:
        raise ValueError("Product v0.2.3 Baseline count differs before No-Fault")
    if any(
        counts[name]
        for name in (
            "incident_count",
            "diagnosis_count",
            "fault_family_count",
            "knowledge_artifact_count",
        )
    ):
        raise ValueError("Product v0.2.3 No-Fault episode was already consumed")
    return {
        "terminal": "ECOMSRE_PRODUCT_V023_NOFAULT_CONTRACT_READY",
        "environment_id": audit.environment_id,
        "active_baseline_id": audit.baseline_id,
        "active_baseline_sha256": audit.baseline_sha256,
        "baseline_candidate_identity_sha256": candidate_sha256,
        "service_identity_sha256": identity.identity_sha256,
        "capability_sha256": capability.capability_sha256,
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "execution_profile_sha256": profile.profile_sha256,
        **counts,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
    }


def verify_frozen_nofault_acceptance_v023(root: Path) -> dict[str, object]:
    """Verify tracked Increment 4 outputs without requiring private live data."""

    repository = root.resolve(strict=True)
    attempts = _load_public_attempts(repository)
    if len(attempts) != 1 or attempts[0].completion.per_window_audit is None:
        raise ValueError("Product v0.2.3 frozen Baseline evidence differs")
    audit = attempts[0].completion.per_window_audit
    public = _read_json_object(repository / PRODUCT_V023_NOFAULT_RESULT_PATH)
    raw_result = public.get("result")
    if not isinstance(raw_result, dict):
        raise ValueError("Product v0.2.3 frozen No-Fault result is absent")
    result = NoFaultAcceptanceResultV023.model_validate(raw_result)
    restart = BaselineRestartProofV023.model_validate(
        _read_json_object(repository / PRODUCT_V023_RESTART_PATH)
    )
    closure = {
        key: public.get(key)
        for key in (
            "queue_default_before_sha256",
            "queue_default_unchanged",
            "outer_baseline_before_sha256",
            "outer_baseline_unchanged",
            "product_cleanup",
            "product_cleanup_observation",
            "demo_cleanup",
            "demo_cleanup_observation",
            "non_owned_resources_changed",
        )
    }
    if (
        public.get("terminal") != result.terminal.value
        or public.get("acceptance_terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V023
        or public.get("active_profile_sha256") != ACTIVE_PROFILE_SHA256_V023
        or public.get("active_baseline_id") != audit.baseline_id
        or public.get("active_baseline_sha256") != audit.baseline_sha256
        or public.get("readiness_audit_sha256") != audit.audit_sha256
        or public.get("restart_proof_sha256") != restart.proof_sha256
        or result.restart_proof_sha256 != restart.proof_sha256
        or public.get("incident_count") != 1
        or public.get("diagnosis_count") != 1
        or public.get("fault_family_count") != 0
        or public.get("fault_attempt_count") != 0
        or public.get("knowledge_campaign_count") != 0
        or public.get("action_authority") != "NONE"
        or public.get("agent_writes") != 0
        or public.get("runbook_executions") != 0
        or public.get("product_cleanup") != "CLEAN"
        or public.get("demo_cleanup") != "CLEAN"
        or public.get("queue_default_unchanged") is not True
        or public.get("outer_baseline_unchanged") is not True
        or public.get("non_owned_resources_changed") is not False
        or public.get("cleanup_proof_sha256") != semantic_sha256_v22(closure)
        or public.get("environment_configuration_sha256")
        != _file_sha256(repository / PRODUCT_V023_ENVIRONMENT_PATH)
    ):
        raise ValueError("Product v0.2.3 frozen No-Fault binding differs")
    handoff = _read_json_object(repository / PRODUCT_V023_HANDOFF_PATH)
    handoff_digest = handoff.pop("handoff_sha256", None)
    expected_handoff_ready = result.terminal.value == NOFAULT_FULLY_SUPPORTED_V023
    if (
        handoff_digest != semantic_sha256_v22(handoff)
        or handoff.get("authorized") is not expected_handoff_ready
        or handoff.get("terminal")
        != (
            KNOWLEDGE_LOOP_HANDOFF_READY_V023
            if expected_handoff_ready
            else KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023
        )
        or handoff.get("fault_calibration_authorized") is not expected_handoff_ready
    ):
        raise ValueError("Product v0.2.3 frozen handoff binding differs")
    progress = _read_json_object(repository / PRODUCT_V023_PROGRESS_PATH)
    progress_digest = progress.pop("progress_sha256", None)
    if (
        progress_digest != semantic_sha256_v22(progress)
        or progress.get("terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V023
        or progress.get("measured_nofault_terminal") != result.terminal.value
    ):
        raise ValueError("Product v0.2.3 frozen progress binding differs")
    required_paths = (
        PRODUCT_V023_NOFAULT_MARKDOWN_PATH,
        PRODUCT_V023_LIMITATIONS_PATH,
        PRODUCT_V023_INTERVIEW_BRIEF_PATH,
        PRODUCT_V023_HANDOFF_MARKDOWN_PATH,
    )
    if any(not (repository / path).is_file() for path in required_paths):
        raise ValueError("Product v0.2.3 frozen rendered output is absent")
    return {
        "terminal": "ECOMSRE_PRODUCT_V023_NOFAULT_ACCEPTANCE_VERIFIED",
        "measured_terminal": result.terminal.value,
        "result_sha256": result.result_sha256,
        "restart_proof_sha256": restart.proof_sha256,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "knowledge_campaign_count": 0,
        "action_authority": "NONE",
    }


def _rotate_runtime_snapshot(
    *,
    path: Path,
    snapshot: PilotRuntimeSnapshotV02,
    private_root: Path,
    ordinal: int,
) -> dict[str, object]:
    before = PilotRuntimeSnapshotV02.model_validate_json(path.read_bytes())
    if (
        before.environment_id != snapshot.environment_id
        or before.authority_sha256 != snapshot.authority_sha256
    ):
        raise ValueError("Product v0.2.3 Runtime snapshot rotation differs")
    encoded = (snapshot.model_dump_json() + "\n").encode("utf-8")
    temporary = path.parent / f".{path.name}.product-v023-nofault.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    if path.read_bytes() != encoded:
        raise RuntimeError("Product v0.2.3 Runtime snapshot readback differs")
    evidence: dict[str, object] = {
        "schema_version": "ecomsre.product.runtime-snapshot-rotation.v023",
        "environment_id": snapshot.environment_id,
        "authority_sha256": snapshot.authority_sha256,
        "before_snapshot_sha256": before.snapshot_sha256,
        "after_snapshot_sha256": snapshot.snapshot_sha256,
        "rotated_at": datetime.now(UTC).isoformat(),
    }
    _write_json(
        private_root / f"runtime-snapshot-rotation-{ordinal}.json",
        evidence,
        create_once=True,
    )
    return evidence


def _runtime_snapshot(
    *,
    backend: LocalSandboxReadBackend,
    authority: PilotRuntimeAuthorityV02,
) -> PilotRuntimeSnapshotV02:
    services, drift = _runtime_services(backend, run_id=secrets.token_hex(16))
    if drift:
        raise RuntimeError("Product v0.2.3 Runtime observation is unhealthy")
    return PilotRuntimeSnapshotV02.build(
        environment_id=authority.environment_id,
        authority_sha256=authority.connector_binding_sha256,
        observed_at=datetime.now(UTC),
        services=services,
    )


def _successful_runtime_ref(bundle: EvidenceBundleV1) -> str | None:
    for item in bundle.objects:
        connector = item.payload.get("connector_result")
        if item.source is not EvidenceSourceV22.RUNTIME or not isinstance(
            connector, Mapping
        ):
            continue
        try:
            result = ConnectorQueryResultV1.model_validate(connector, strict=False)
        except (TypeError, ValueError):
            continue
        if (
            result.status.value == "SUCCESS_NONEMPTY"
            and result.requested_services == ("checkout",)
            and result.covered_services == ("checkout",)
        ):
            return item.evidence_ref
    return None


def _limitation_evidence_refs(
    diagnosis: DiagnosisResultV1,
    bundle: EvidenceBundleV1,
) -> dict[str, str]:
    bound: dict[str, str] = {}
    for limitation in diagnosis.capability_limitations:
        source = next(
            (
                item
                for item in EvidenceSourceV22
                if item.value in limitation.upper().split("_")
            ),
            None,
        )
        if source is None:
            continue
        candidates: list[str] = []
        for evidence in bundle.objects:
            connector = evidence.payload.get("connector_result")
            if evidence.source is not source or not isinstance(connector, Mapping):
                continue
            status = str(connector.get("status", ""))
            if status.startswith("FAILURE_") and connector.get("safe_error_code"):
                candidates.append(evidence.evidence_ref)
        if candidates:
            bound[limitation] = sorted(candidates)[0]
    return dict(sorted(bound.items()))


def _render_acceptance_markdown(payload: Mapping[str, object]) -> str:
    result = payload.get("result")
    measured = result if isinstance(result, Mapping) else {}
    return "\n".join(
        (
            "# Product v0.2.3 No-Fault Acceptance",
            "",
            f"Measured terminal: `{payload['terminal']}`",
            "",
            f"- acceptance terminal: `{payload['acceptance_terminal']}`",
            f"- Incident / Diagnosis count: `{payload['incident_count']} / {payload['diagnosis_count']}`",
            f"- diagnosis terminal: `{measured.get('diagnosis_terminal')}`",
            f"- Baseline: `{payload['active_baseline_id']}`",
            f"- Baseline SHA: `{payload['active_baseline_sha256']}`",
            f"- restart proof SHA: `{payload['restart_proof_sha256']}`",
            f"- result SHA: `{measured.get('result_sha256')}`",
            f"- cleanup: `Product {payload['product_cleanup']} / Demo {payload['demo_cleanup']}`",
            "- fault attempts / Knowledge campaigns: `0 / 0`",
            "- action authority / Agent writes / Runbooks: `NONE / 0 / 0`",
            "",
        )
    )


def _render_limitations(result: NoFaultAcceptanceResultV023) -> str:
    reasons = result.reasons or ("NONE",)
    return "\n".join(
        (
            "# Product v0.2.3 Limitations",
            "",
            f"Measured terminal: `{result.terminal.value}`",
            "",
            *(f"- `{reason}`" for reason in reasons),
            "",
            "This result covers one owned local OTel Demo environment only.",
            "It does not authorize fault calibration, remediation, or deployment.",
            "",
        )
    )


def _render_interview_brief(result: NoFaultAcceptanceResultV023) -> str:
    return "\n".join(
        (
            "# Product v0.2.3 Interview Brief",
            "",
            "The ordinary Product path used the ACTIVE P01 OpenSearch profile,",
            "built and reloaded one immutable Baseline, then evaluated exactly one",
            "healthy No-Fault checkout episode with no action authority.",
            "",
            f"Measured terminal: `{result.terminal.value}`",
            f"Diagnosis terminal: `{result.diagnosis_terminal.value}`",
            f"Result SHA: `{result.result_sha256}`",
            "",
            "Claim boundary: local owned environment, read-only diagnosis, no fault,",
            "no Runbook, no Agent write, and no Knowledge-Loop campaign.",
            "",
        )
    )


def _render_handoff_markdown(payload: Mapping[str, object]) -> str:
    repairs = payload.get("required_repair_reasons")
    reasons = repairs if isinstance(repairs, list) and repairs else ["NONE"]
    return "\n".join(
        (
            "# Product v0.2.3 Knowledge-Loop Handoff",
            "",
            f"Terminal: `{payload['terminal']}`",
            f"Authorized: `{str(payload['authorized']).lower()}`",
            "",
            *(f"- `{reason}`" for reason in reasons),
            "",
        )
    )


def run_live_nofault_acceptance_v023(*, repository_root: Path) -> dict[str, object]:
    """Use the preserved passing Baseline for one restart and one Incident."""

    root = repository_root.resolve(strict=True)
    contract = verify_live_nofault_contract_v023(root)
    execution_head = _require_clean_head(root)
    _attempt, audit, product_data_root = _attempt_context(root)
    profile = NoFaultExecutionProfileV023.load(
        root / PRODUCT_V023_NOFAULT_PROFILE_PATH
    )
    identity, capability, candidate_sha256 = _load_persisted_bindings(
        product_data_root,
        audit,
    )
    before_counts = _database_counts(product_data_root, audit.environment_id)
    for path in (
        root / PRODUCT_V023_RESTART_PATH,
        root / PRODUCT_V023_NOFAULT_RESULT_PATH,
        root / PRODUCT_V023_HANDOFF_PATH,
    ):
        if path.exists():
            raise ValueError("Product v0.2.3 No-Fault public result already exists")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-") + secrets.token_hex(4)
    private_root = root / ".local/product-v023/nofault/runs" / run_id
    if private_root.exists():
        raise ValueError("Product v0.2.3 No-Fault private root already exists")
    private_root.mkdir(parents=True, mode=0o700)
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private_root / "demo",
        stabilization_seconds=30,
    )
    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )
    queue_before_sha256: str | None = None
    outer_baseline_before_sha256: str | None = None
    queue_default_unchanged = False
    outer_baseline_unchanged = False
    product_cleanup_observation: dict[str, object] = {
        "verdict": "BLOCKED",
        "safe_error": "cleanup not yet observed",
    }
    demo_cleanup_observation = CleanupObservation.unknown_blocked().model_dump(
        mode="json"
    )
    restart_proof: BaselineRestartProofV023 | None = None
    incident: IncidentRecordV1 | None = None
    diagnosis: DiagnosisResultV1 | None = None
    evidence: EvidenceBundleV1 | None = None
    traffic_result: NoFaultTrafficResultV023 | None = None
    queue_snapshot: NoFaultQueueSnapshotV023 | None = None
    result: NoFaultAcceptanceResultV023 | None = None
    rotation_evidence: list[dict[str, object]] = []
    error: BaseException | None = None
    stage = "PREFLIGHT"
    try:
        lifecycle.admit()
        if lifecycle.flag_file is None:
            raise RuntimeError("Product v0.2.3 owned queue file is absent")
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=profile.queue_fault_flag,
        )
        queue_before_sha256 = queue_before.before_sha256
        lifecycle.start()
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise RuntimeError("Product v0.2.3 owned Runtime backend differs")
        outer_baseline_before_sha256 = lifecycle.read_baseline_sha256()
        authority_path = product_data_root / "pilot/runtime-authority.json"
        authority = load_pilot_runtime_authority_v02(authority_path)
        rebound_authority = PilotRuntimeAuthorityV02.build(
            environment_id=audit.environment_id,
            allowed_logical_services=("checkout",),
            profile_sha256=authority.profile_sha256,
            **_authority_inputs(backend),
        )
        if rebound_authority != authority:
            raise RuntimeError("Product v0.2.3 Runtime authority changed")
        runtime_path = product_data_root / "pilot/runtime-readiness.json"
        rotation_evidence.append(
            _rotate_runtime_snapshot(
                path=runtime_path,
                snapshot=_runtime_snapshot(backend=backend, authority=authority),
                private_root=private_root,
                ordinal=1,
            )
        )
        processes.start()
        stage = "PRODUCT_STARTED"
        before = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=identity.identity_sha256,
            baseline_candidate_identity_sha256=candidate_sha256,
            capability_sha256=capability.capability_sha256,
        )
        processes.restart()
        stage = "PRODUCT_RESTARTED"
        (
            restart_identity,
            restart_capability,
            restart_candidate_sha256,
        ) = _load_persisted_bindings(
            product_data_root,
            audit,
        )
        if (
            restart_identity.identity_sha256 != identity.identity_sha256
            or restart_candidate_sha256 != candidate_sha256
            or restart_capability.capability_sha256 != capability.capability_sha256
        ):
            raise RuntimeError("Product v0.2.3 restart persistence binding changed")
        rebound_audit = ProductBaselineReadinessAuditV023.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/baselines/{audit.baseline_id}/window-audit-v023",
            )
        )
        if rebound_audit.audit_sha256 != audit.audit_sha256:
            raise RuntimeError("Product v0.2.3 restart audit changed")
        after = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=restart_identity.identity_sha256,
            baseline_candidate_identity_sha256=restart_candidate_sha256,
            capability_sha256=restart_capability.capability_sha256,
        )
        restart_proof = BaselineRestartProofV023.build(
            before=before,
            after=after,
            connector_verification_count=0,
        )
        after_restart_counts = _database_counts(
            product_data_root,
            audit.environment_id,
        )
        if (
            after_restart_counts["baseline_count"] != before_counts["baseline_count"]
            or after_restart_counts["baseline_job_count"]
            != before_counts["baseline_job_count"]
            or after_restart_counts["verify_job_count"]
            != before_counts["verify_job_count"]
        ):
            raise RuntimeError("Product v0.2.3 restart job or Baseline count changed")
        stage = "RESTART_PASS"

        episode_started_at = datetime.now(UTC)
        healthy_profile = HealthyTrafficProfileV021(
            request_seed=profile.seed,
            maximum_request_count=profile.request_count,
            requests_per_second=profile.requests_per_second,
            error_budget=max(
                1,
                int(profile.request_count * profile.maximum_error_fraction) + 1,
            ),
        )
        with httpx.Client() as traffic_client:
            measured = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=healthy_profile,
            )
        remaining = (
            episode_started_at
            + timedelta(seconds=_EPISODE_MINIMUM_SECONDS_V023)
            - datetime.now(UTC)
        ).total_seconds()
        if remaining > 0:
            time.sleep(remaining)
        rotation_evidence.append(
            _rotate_runtime_snapshot(
                path=runtime_path,
                snapshot=_runtime_snapshot(backend=backend, authority=authority),
                private_root=private_root,
                ordinal=2,
            )
        )
        episode_ended_at = datetime.now(UTC)
        incident_payload = _request_json(
            processes,
            "POST",
            "/v1/incidents",
            payload={
                "environment_id": audit.environment_id,
                "external_incident_key": f"product-v023-nofault-{run_id}",
                "alert_name": "Product v0.2.3 No-Fault acceptance",
                "summary": "Fresh healthy checkout observation with no fault active.",
                "started_at": episode_started_at.isoformat(),
                "ended_at": episode_ended_at.isoformat(),
                "candidate_service_ids": list(audit.baseline_entity_service_ids),
                "labels": {"fault": profile.incident_fault_label},
            },
        )
        incident = IncidentRecordV1.model_validate(incident_payload)
        if (
            incident.service_identity_sha256 != identity.identity_sha256
            or incident.source_capability_sha256 != capability.capability_sha256
        ):
            raise RuntimeError("Product v0.2.3 Incident binding changed")
        traffic_result = NoFaultTrafficResultV023.build(
            environment_id=audit.environment_id,
            incident_id=incident.incident_id,
            window=ConnectorWindowV1(
                started_at=incident.started_at,
                ended_at=incident.diagnosis_observed_at,
            ),
            profile_sha256=profile.profile_sha256,
            planned_request_count=profile.request_count,
            completed_request_count=measured.attempted,
            error_count=measured.failed,
            requests_per_second=profile.requests_per_second,
            maximum_error_fraction=profile.maximum_error_fraction,
            queue_fault_flag=profile.queue_fault_flag,
            passed=(
                measured.attempted == profile.request_count
                and measured.failed / max(1, measured.attempted)
                <= profile.maximum_error_fraction
            ),
        )
        stage = "INCIDENT_CREATED"
        queued_diagnosis = _request_json(
            processes,
            "POST",
            f"/v1/incidents/{incident.incident_id}/diagnosis-jobs",
            payload=None,
        )
        diagnosis_job = _wait_job(
            processes,
            str(queued_diagnosis["job_id"]),
            data_root=product_data_root,
            timeout_seconds=240,
        )
        if diagnosis_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            diagnosis_job.result, dict
        ):
            raise RuntimeError(
                diagnosis_job.safe_error_code or "Product v0.2.3 Diagnosis failed"
            )
        diagnosis = DiagnosisResultV1.model_validate(diagnosis_job.result)
        evidence = EvidenceBundleV1.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/incidents/{incident.incident_id}/evidence",
            )
        )
        after_counts = _database_counts(product_data_root, audit.environment_id)
        pending, running, failed = _queue_counts(product_data_root)
        queue_snapshot = NoFaultQueueSnapshotV023.build(
            environment_id=audit.environment_id,
            observed_at=datetime.now(UTC),
            pending_jobs=pending,
            running_jobs=running,
            failed_jobs=failed,
            queue_fault_flag=profile.queue_fault_flag,
        )
        runtime_ref = _successful_runtime_ref(evidence)
        capability_assessment = NoFaultCapabilityAssessmentV023.build(
            runtime_healthy=runtime_ref is not None,
            runtime_evidence_ref=runtime_ref,
            successful_sources=_successful_evidence_sources(
                evidence,
                incident=incident,
            ),
            healthy_traffic_passed=traffic_result.passed,
            healthy_traffic_result_sha256=traffic_result.result_sha256,
            limitation_evidence_refs=_limitation_evidence_refs(diagnosis, evidence),
        )
        if restart_proof is None:
            raise AssertionError("Product v0.2.3 restart proof is absent")
        result = score_nofault_v023(
            baseline_audit=audit,
            restart_proof=restart_proof,
            incident=incident,
            diagnosis=diagnosis,
            bundle=evidence,
            capability_assessment=capability_assessment,
            execution_profile=profile,
            traffic_result=traffic_result,
            queue_snapshot=queue_snapshot,
            active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            incident_count=after_counts["incident_count"],
            diagnosis_count=after_counts["diagnosis_count"],
            fault_family_count=after_counts["fault_family_count"],
            action_authority_violations=0,
            agent_writes=0,
            runbook_executions=0,
        )
        if (
            after_counts["incident_count"] != 1
            or after_counts["diagnosis_count"] != 1
            or after_counts["fault_family_count"] != 0
            or after_counts["knowledge_artifact_count"] != 0
        ):
            raise RuntimeError("Product v0.2.3 exact No-Fault counters differ")
        stage = "NOFAULT_MEASURED"
    except BaseException as caught:
        error = caught
    finally:
        if lifecycle.flag_file is not None and queue_before_sha256 is not None:
            try:
                verify_queue_default_v021(
                    lifecycle.flag_file,
                    expected_default_value=profile.queue_fault_flag,
                    expected_sha256=queue_before_sha256,
                )
                queue_default_unchanged = True
            except BaseException as closure_error:
                if error is None:
                    error = closure_error
        if outer_baseline_before_sha256 is not None:
            try:
                outer_baseline_unchanged = (
                    lifecycle.read_baseline_sha256()
                    == outer_baseline_before_sha256
                )
                if not outer_baseline_unchanged:
                    raise RuntimeError("Product v0.2.3 outer baseline changed")
            except BaseException as closure_error:
                if error is None:
                    error = closure_error
        product_cleanup_observation = processes.cleanup_observation()
        if product_cleanup_observation.get("verdict") != "CLEAN" and error is None:
            error = RuntimeError("Product v0.2.3 Product cleanup is blocked")
        try:
            demo_cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=outer_baseline_unchanged
            )
            demo_cleanup_observation = demo_cleanup.model_dump(mode="json")
            if demo_cleanup.verdict != "CLEAN" and error is None:
                error = RuntimeError("Product v0.2.3 Demo cleanup is blocked")
        except BaseException as closure_error:
            if error is None:
                error = closure_error

    closure = {
        "queue_default_before_sha256": queue_before_sha256,
        "queue_default_unchanged": queue_default_unchanged,
        "outer_baseline_before_sha256": outer_baseline_before_sha256,
        "outer_baseline_unchanged": outer_baseline_unchanged,
        "product_cleanup": product_cleanup_observation.get("verdict"),
        "product_cleanup_observation": product_cleanup_observation,
        "demo_cleanup": demo_cleanup_observation.get("verdict"),
        "demo_cleanup_observation": demo_cleanup_observation,
        "non_owned_resources_changed": False,
    }
    closure_sha256 = semantic_sha256_v22(closure)
    private_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.private-nofault-acceptance.v023",
        "run_id": run_id,
        "stage": stage,
        "execution_head": execution_head,
        "contract": contract,
        "restart_proof": (
            None if restart_proof is None else restart_proof.model_dump(mode="json")
        ),
        "runtime_snapshot_rotations": rotation_evidence,
        "incident": None if incident is None else incident.model_dump(mode="json"),
        "diagnosis": None if diagnosis is None else diagnosis.model_dump(mode="json"),
        "evidence": None if evidence is None else evidence.model_dump(mode="json"),
        "traffic_result": (
            None if traffic_result is None else traffic_result.model_dump(mode="json")
        ),
        "queue_snapshot": (
            None if queue_snapshot is None else queue_snapshot.model_dump(mode="json")
        ),
        "result": None if result is None else result.model_dump(mode="json"),
        "closure": closure,
        "closure_sha256": closure_sha256,
        "safe_error_type": None if error is None else type(error).__name__,
        "safe_error": None if error is None else str(error)[:1000],
        "fault_attempt_count": 0,
        "knowledge_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    private_report = private_root / "acceptance.json"
    _write_json(private_report, private_payload, create_once=True)
    if error is not None or result is None or restart_proof is None:
        raise RuntimeError(
            f"{NOFAULT_INFRASTRUCTURE_BLOCKED_V023}: stage={stage}: {error}"
        ) from error
    if not (
        queue_default_unchanged
        and outer_baseline_unchanged
        and product_cleanup_observation.get("verdict") == "CLEAN"
        and demo_cleanup_observation.get("verdict") == "CLEAN"
    ):
        raise RuntimeError(f"{NOFAULT_INFRASTRUCTURE_BLOCKED_V023}: cleanup")

    public_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.public-nofault-acceptance.v023",
        "terminal": result.terminal.value,
        "acceptance_terminal": NOFAULT_ACCEPTANCE_COMPLETE_V023,
        "execution_head": execution_head,
        "environment_id": audit.environment_id,
        "environment_configuration_sha256": _file_sha256(
            root / PRODUCT_V023_ENVIRONMENT_PATH
        ),
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "execution_profile_sha256": profile.profile_sha256,
        "active_baseline_id": audit.baseline_id,
        "active_baseline_sha256": audit.baseline_sha256,
        "readiness_audit_sha256": audit.audit_sha256,
        "restart_proof_sha256": restart_proof.proof_sha256,
        "result": result.model_dump(mode="json"),
        "incident_count": result.incident_count,
        "diagnosis_count": result.diagnosis_count,
        "fault_family_count": result.fault_family_count,
        "infrastructure_replacement_count": 1,
        "private_report_sha256": _file_sha256(private_report),
        "cleanup_proof_sha256": closure_sha256,
        **closure,
        "fault_attempt_count": 0,
        "knowledge_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    handoff_ready = result.terminal.value == NOFAULT_FULLY_SUPPORTED_V023
    handoff_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.knowledge-loop-handoff.v023",
        "terminal": (
            KNOWLEDGE_LOOP_HANDOFF_READY_V023
            if handoff_ready
            else KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023
        ),
        "authorized": handoff_ready,
        "execution_head": execution_head,
        "environment_configuration_sha256": public_payload[
            "environment_configuration_sha256"
        ],
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "service_identity_sha256": result.service_identity_sha256,
        "capability_sha256": result.capability_sha256,
        "active_baseline_id": result.baseline_id,
        "active_baseline_sha256": result.baseline_sha256,
        "readiness_audit_sha256": audit.audit_sha256,
        "restart_proof_sha256": result.restart_proof_sha256,
        "incident_sha256": result.incident_sha256,
        "diagnosis_result_sha256": result.diagnosis_result_sha256,
        "evidence_bundle_sha256": result.evidence_bundle_sha256,
        "queue_default_binding_sha256": queue_before_sha256,
        "cleanup_proof_sha256": closure_sha256,
        "required_repair_reasons": list(result.reasons),
        "fault_calibration_authorized": handoff_ready,
    }
    handoff_payload["handoff_sha256"] = semantic_sha256_v22(handoff_payload)
    progress_payload = {
        "schema_version": "ecomsre.product.progress.v023",
        "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V023,
        "measured_nofault_terminal": result.terminal.value,
        "baseline_attempt_count": 1,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "knowledge_campaign_count": 0,
        "action_authority": "NONE",
        "repository_acceptance": "REVIEW_REQUIRED",
    }
    progress_payload["progress_sha256"] = semantic_sha256_v22(progress_payload)

    _write_json(root / PRODUCT_V023_RESTART_PATH, restart_proof.model_dump(mode="json"), create_once=True)
    _write_json(root / PRODUCT_V023_NOFAULT_RESULT_PATH, public_payload, create_once=True)
    _atomic_write(
        root / PRODUCT_V023_NOFAULT_MARKDOWN_PATH,
        _render_acceptance_markdown(public_payload),
        create_once=True,
    )
    _atomic_write(
        root / PRODUCT_V023_LIMITATIONS_PATH,
        _render_limitations(result),
        create_once=True,
    )
    _atomic_write(
        root / PRODUCT_V023_INTERVIEW_BRIEF_PATH,
        _render_interview_brief(result),
        create_once=True,
    )
    _write_json(root / PRODUCT_V023_HANDOFF_PATH, handoff_payload, create_once=True)
    _atomic_write(
        root / PRODUCT_V023_HANDOFF_MARKDOWN_PATH,
        _render_handoff_markdown(handoff_payload),
        create_once=True,
    )
    _write_json(root / PRODUCT_V023_PROGRESS_PATH, progress_payload, create_once=True)
    return public_payload


__all__ = (
    "KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023",
    "KNOWLEDGE_LOOP_HANDOFF_READY_V023",
    "NOFAULT_ACCEPTANCE_COMPLETE_V023",
    "NOFAULT_INFRASTRUCTURE_BLOCKED_V023",
    "run_live_nofault_acceptance_v023",
    "verify_frozen_nofault_acceptance_v023",
    "verify_live_nofault_contract_v023",
)
