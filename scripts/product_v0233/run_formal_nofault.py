#!/usr/bin/env python3
"""Execute the one authorized Product v0.2.3.3 formal No-Fault campaign."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, cast, Literal, Mapping, NoReturn, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.incidents.contracts import DiagnosisResultV1, EvidenceBundleV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPrivateFailureEnvelopeV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.jobs.contracts import ProductJobStatusV1
from ecomsre.product.pilot.baseline_readiness_v021 import verify_queue_default_v021
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023
from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
    FormalActionEventV0233,
    FormalActionJournalV0233,
    FormalClosureProofV0233,
    FormalExecutionAdmissionV0233,
    FormalExecutionBlockerV0233,
    FormalExecutionReservationV0233,
    FormalObservedStateCountsV0233,
    FormalSafetyObservationV0233,
    FormalTrafficResultV0233,
    FreshRuntimeSnapshotProofV0233,
    RuntimeAuthorityProofV0233,
)
from ecomsre.product.pilot.formal_nofault_v02321 import (
    FormalTrafficConsumptionV02321,
    FormalTrafficDispatchCheckpointV02321,
    FormalTrafficObservationCheckpointV02321,
)
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    DiagnosisPipelineAcceptanceV0233,
    FormalIncidentDiagnosisCardinalityV0233,
    NoFaultAcceptanceResultV0233,
    SafetyCountersV0233,
    admit_incident_creation_v0233,
    load_fresh_formal_campaign_v0233,
    load_fresh_traffic_profile_v0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalSourceKindV0233,
    FreshFormalSourceSelectionV0233,
    FreshFormalStateCloneV0233,
    FreshFormalStateCountsV0233,
    admit_fresh_formal_source_v0233,
    clone_fresh_formal_state_v0233,
    configured_source_candidates_v0233,
    read_formal_active_binding_v0233,
    read_formal_diagnosis_action_totals_v0233,
    read_fresh_formal_state_counts_v0233,
    read_raw_formal_state_counts_v0233,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficRunnerV0232,
    IncidentTrafficBindingV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
    _queue_counts,
    _request_json,
    _restart_snapshot,
    _wait_job,
)
from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    _attempt_context,
    _load_persisted_bindings,
    _runtime_snapshot,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultEvidenceAssessmentV0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre.product.pilot.traffic_preflight_v0233 import (
    DiagnosisSemanticSourceManifestV0233,
    FormalClonePlanV0233,
    FormalContractFreezeV0233,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    build_traffic_harness_typed_request_plan_v02321,
    materialize_planned_request_v02321,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre_live_sandbox.contracts import load_bundle, write_private_json
from scripts.ci.verify_product_v0233_pre_execution import (
    verify_product_v0233_pre_execution,
)
from scripts.product_v0231.run_live_authority_restart import (
    _authority_proof,
    _rotate_runtime_snapshot_v0231,
)
from scripts.product_v02321.run_formal_nofault import (  # noqa: PLC2701
    _find_decision_trace,
    _recover_diagnosis_job,
    _recover_incident_by_external_key,
    _request_or_recover_diagnosis_job_v02321,
    _request_or_recover_incident_v02321,
    _worktree_for_branch,
    run_formal_traffic_journaled_v02321,
)
from scripts.product_v02321.run_traffic_preflight import (  # noqa: PLC2701
    _checkout_runtime,
    _database_owner_count,
    _require_preserved_runtime_root_v02321,
)
from scripts.product_v0232.run_state_clone import ENVIRONMENT_ID_V0232
from scripts.product_v0233.run_traffic_preflight import (  # noqa: PLC2701
    _diagnosis_source_manifest,
    _replace_public,
    _write_public_create_once,
)


_BRANCH = "codex/product-v0233-fresh-formal-nofault-acceptance"
_SOURCE_BRANCH = "codex/product-v023-fresh-baseline-nofault"
_FALLBACK_BRANCH = "codex/product-v02323-schema8-reconstruction-replay"
_PRIVATE_ACCEPTANCE_BRANCH = "codex/product-v0231-runtime-authority-nofault-successor"
_SOURCE_LOCATOR = (
    ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/product"
)
_FALLBACK_LOCATOR = ".local/product-v02323/reconstruction/20260831T051548Z/product"
_PRIVATE_ACCEPTANCE_LOCATOR = (
    ".local/product-v0231/continuation-sessions/session-1/acceptance.json"
)
_RESERVATION_LOCATOR = ".local/product-v0233/formal-reservation.json"
_PRIVATE_LOCATOR = ".local/product-v0233/formal-execution"
_ENDPOINT = "http://127.0.0.1:18080/api/checkout"
_FORMAL_DESTINATION = (
    ".local/product-v0233/formal-state/product-v0233-fresh-formal-nofault/product"
)
_EXPECTED_UPSTREAM = f"refs/remotes/origin/{_BRANCH}"


class _DiagnosisJobFailedV0233(RuntimeError):
    def __init__(
        self,
        *,
        acceptance: DiagnosisPipelineAcceptanceV0233,
        safe_error_code: str,
    ) -> None:
        super().__init__(safe_error_code)
        self.acceptance = acceptance
        self.safe_error_code = safe_error_code


def _object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.3 JSON object differs: {path.name}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frozen_semantic_surface_sha256_v0233(root: Path) -> str:
    """Rebuild the frozen Product/Diagnosis/scorer surface without Git state."""

    sources = DiagnosisSemanticSourceManifestV0233.model_validate_json(
        (
            root / "docs/analysis/product-v0233-diagnosis-source-manifest.json"
        ).read_bytes()
    )
    observed_sources = {
        path: _sha256_file(root / path) for path in sources.source_sha256_by_path
    }
    freeze = FormalContractFreezeV0233.model_validate_json(
        (root / "docs/analysis/product-v0233-formal-contract-freeze.json").read_bytes()
    )
    campaign = load_fresh_formal_campaign_v0233(root)
    preflight_profile = load_fresh_traffic_profile_v0233(root, role="PREFLIGHT")
    formal_profile = load_fresh_traffic_profile_v0233(root, role="FORMAL")
    traffic_contract = load_checkout_traffic_contract_v0232(root)
    scorer_sha256 = _sha256_file(
        root / "src/ecomsre/product/pilot/nofault_acceptance_v0232.py"
    )
    execution_paths = (
        "scripts/product_v0233/run_formal_nofault.py",
        "scripts/product_v02321/run_formal_nofault.py",
        "scripts/product_v02321/run_traffic_preflight.py",
        "scripts/product_v0231/run_live_authority_restart.py",
        "src/ecomsre/product/pilot/formal_live_v0233.py",
        "src/ecomsre/product/pilot/fresh_formal_source_v0233.py",
        "src/ecomsre/product/pilot/fresh_formal_acceptance_v0233.py",
        "src/ecomsre/product/pilot/repository_state_v0233.py",
    )
    execution_sha256_by_path = {
        path: _sha256_file(root / path) for path in execution_paths
    }
    if (
        observed_sources != sources.source_sha256_by_path
        or _diagnosis_source_manifest(root) != sources
        or sources.manifest_sha256 != freeze.diagnosis_semantic_source_manifest_sha256
        or campaign.campaign_sha256 != freeze.campaign_sha256
        or preflight_profile.profile_sha256 != freeze.preflight_profile_sha256
        or formal_profile.profile_sha256 != freeze.formal_profile_sha256
        or _sha256_file(root / "config/product-v0233/traffic/preflight-profile.json")
        != freeze.preflight_profile_file_sha256
        or _sha256_file(root / "config/product-v0233/traffic/formal-profile.json")
        != freeze.formal_profile_file_sha256
        or traffic_contract.contract_sha256 != freeze.traffic_contract_sha256
        or scorer_sha256 != freeze.nofault_scorer_source_sha256
        or scorer_sha256 != campaign.nofault_scorer_source_sha256
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    body = {
        "schema_version": "ecomsre.product.frozen-semantic-surface.v0233",
        "formal_contract_freeze_sha256": freeze.freeze_sha256,
        "diagnosis_semantic_source_manifest_sha256": sources.manifest_sha256,
        "diagnosis_source_count": sources.source_count,
        "preflight_profile_sha256": preflight_profile.profile_sha256,
        "formal_profile_sha256": formal_profile.profile_sha256,
        "traffic_contract_sha256": traffic_contract.contract_sha256,
        "nofault_scorer_source_sha256": scorer_sha256,
        "runtime_continuity_descriptor_sha256": (
            freeze.runtime_continuity_descriptor_sha256
        ),
        "flagd_bind_descriptor_sha256": freeze.flagd_bind_descriptor_sha256,
        "pilot_runtime_authority_sha256": freeze.pilot_runtime_authority_sha256,
        "active_profile_sha256": freeze.active_profile_sha256,
        "active_baseline_sha256": freeze.active_baseline_sha256,
        "stage_journal_contract_sha256": freeze.stage_journal_contract_sha256,
        "private_failure_contract_sha256": freeze.private_failure_contract_sha256,
        "execution_sha256_by_path": execution_sha256_by_path,
    }
    return semantic_sha256_v22(body)


def _safety_observation(
    *,
    starting_counts: FormalObservedStateCountsV0233,
    source_action_totals: Mapping[str, int | bool],
    product_root: Path | None,
    action_journal: FormalActionJournalV0233,
) -> FormalSafetyObservationV0233:
    if product_root is None:
        return FormalSafetyObservationV0233.build(
            observation_status="UNAVAILABLE",
            action_journal=action_journal.model_dump(mode="json"),
            starting_counts=starting_counts.model_dump(mode="json"),
            ending_counts=None,
            new_incident_count=0,
            new_diagnosis_count=0,
            provider_calls=None,
            agent_writes=None,
            runbook_executions=None,
            fault_attempts=None,
            knowledge_loop_executions=None,
            observed_action_authority=None,
            safe=False,
        )
    try:
        ending_counts = FormalObservedStateCountsV0233.model_validate(
            read_raw_formal_state_counts_v0233(product_root)
        )
        ending_actions = read_formal_diagnosis_action_totals_v0233(product_root)
        provider_calls = int(ending_actions["provider_calls"]) - int(
            source_action_totals["provider_calls"]
        )
        agent_writes = int(ending_actions["agent_writes"]) - int(
            source_action_totals["agent_writes"]
        )
        runbook_executions = int(ending_actions["runbook_executions"]) - int(
            source_action_totals["runbook_executions"]
        )
        action_authority_none = bool(source_action_totals["action_authority_none"])
        action_authority_none = action_authority_none and bool(
            ending_actions["action_authority_none"]
        )
        new_incident_count = (
            ending_counts.incident_count - starting_counts.incident_count
        )
        new_diagnosis_count = (
            ending_counts.diagnosis_job_count - starting_counts.diagnosis_job_count
        )
        safe = (
            action_journal.observation_status == "COMPLETE"
            and ending_counts.baseline_count == starting_counts.baseline_count
            and ending_counts.active_baseline_count
            == starting_counts.active_baseline_count
            and ending_counts.baseline_job_count == starting_counts.baseline_job_count
            and ending_counts.verify_job_count == starting_counts.verify_job_count
            and ending_counts.fault_family_count == starting_counts.fault_family_count
            and ending_counts.knowledge_artifact_count
            == starting_counts.knowledge_artifact_count
            and ending_counts.pending_job_count == 0
            and ending_counts.running_job_count == 0
            and provider_calls == 0
            and agent_writes == 0
            and runbook_executions == 0
            and action_journal.fault_attempts == 0
            and action_journal.knowledge_loop_executions == 0
            and action_authority_none
        )
        return FormalSafetyObservationV0233.build(
            observation_status="OBSERVED",
            action_journal=action_journal.model_dump(mode="json"),
            starting_counts=starting_counts.model_dump(mode="json"),
            ending_counts=ending_counts.model_dump(mode="json"),
            new_incident_count=new_incident_count,
            new_diagnosis_count=new_diagnosis_count,
            provider_calls=provider_calls,
            agent_writes=agent_writes,
            runbook_executions=runbook_executions,
            fault_attempts=action_journal.fault_attempts,
            knowledge_loop_executions=action_journal.knowledge_loop_executions,
            observed_action_authority="NONE" if action_authority_none else None,
            safe=safe,
        )
    except (OSError, ValueError, RuntimeError):
        return FormalSafetyObservationV0233.build(
            observation_status="UNAVAILABLE",
            action_journal=action_journal.model_dump(mode="json"),
            starting_counts=starting_counts.model_dump(mode="json"),
            ending_counts=None,
            new_incident_count=None,
            new_diagnosis_count=None,
            provider_calls=None,
            agent_writes=None,
            runbook_executions=None,
            fault_attempts=None,
            knowledge_loop_executions=None,
            observed_action_authority=None,
            safe=False,
        )


def _write_public_text_create_once(path: Path, text: str) -> None:
    payload = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError(f"Product v0.2.3.3 public text differs: {path.name}")
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def strict_formal_admission_v0233(root: Path) -> FormalExecutionAdmissionV0233:
    """Recheck the committed, pushed, review-passing one-shot boundary."""

    project = Path(root).resolve(strict=True)
    try:
        status = _git(project, "status", "--porcelain", "--untracked-files=all")
        head = _git(project, "rev-parse", "HEAD")
        branch = _git(project, "branch", "--show-current")
        upstream = _git(project, "rev-parse", "--symbolic-full-name", "@{upstream}")
        upstream_head = _git(project, "rev-parse", _EXPECTED_UPSTREAM)
    except subprocess.CalledProcessError as error:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_STARTING_HEAD") from error
    if (
        status
        or branch != _BRANCH
        or upstream != _EXPECTED_UPSTREAM
        or upstream_head != head
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_STARTING_HEAD")
    forbidden = (
        project / _RESERVATION_LOCATOR,
        project / _PRIVATE_LOCATOR,
        project / "docs/analysis/product-v0233-formal-state-clone.json",
        project / "docs/analysis/product-v0233-formal-closure.json",
        project / "docs/analysis/product-v0233-formal-blocker.json",
        project / "docs/analysis/product-v0233-diagnosis-stage-journal.json",
        project / "docs/analysis/product-v0233-diagnosis-blocker.json",
        project / "docs/analysis/product-v0233-diagnosis-blocker.md",
        project / "docs/analysis/product-v0233-runtime-authority.json",
        project / "docs/analysis/product-v0233-baseline-restart.json",
        project / "docs/analysis/product-v0233-formal-traffic.json",
        project / "docs/analysis/product-v0233-fresh-runtime-snapshot.json",
        project / "docs/analysis/product-v0233-incident-traffic-binding.json",
        project / "docs/analysis/product-v0233-evidence-assessment.json",
        project / "docs/analysis/product-v0233-knowledge-loop-handoff.json",
        project / "docs/analysis/product-v0233-knowledge-loop-handoff.md",
        project / "docs/results/product-v0233-nofault-acceptance.json",
        project / "docs/results/product-v0233-nofault-acceptance.md",
        project / "docs/results/product-v0233-limitations.md",
        project / "docs/results/product-v0233-interview-brief.md",
        project / _FORMAL_DESTINATION,
        (project / _FORMAL_DESTINATION).parent,
    )
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")

    gate = verify_product_v0233_pre_execution(project)
    campaign = load_fresh_formal_campaign_v0233(project)
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (project / "config/product-v0233/source-selection.json").read_bytes()
    )
    clone_plan = FormalClonePlanV0233.model_validate_json(
        (project / "docs/analysis/product-v0233-formal-clone-plan.json").read_bytes()
    )
    manifest = ProductV0233RepositoryStateManifest.model_validate_json(
        (project / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    if (
        manifest.phase is not RepositoryPhaseV0233.TRAFFIC_PREFLIGHT_PASS
        or manifest.formal_clone_count != 0
        or manifest.formal_execution_count != 0
        or manifest.new_incident_count != 0
        or manifest.new_diagnosis_count != 0
        or manifest.measured_result_count != 0
        or clone_plan.destination_locator != _FORMAL_DESTINATION
        or clone_plan.source_selection_sha256 != selection.selection_sha256
        or campaign.source_selection_sha256 != selection.selection_sha256
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    return FormalExecutionAdmissionV0233.build(
        execution_head=head,
        campaign_sha256=campaign.campaign_sha256,
        source_selection_sha256=selection.selection_sha256,
        formal_clone_plan_sha256=clone_plan.plan_sha256,
        formal_contract_freeze_sha256=str(gate["formal_contract_freeze_sha256"]),
        pre_execution_review_sha256=str(gate["pre_execution_review_sha256"]),
        repository_state_manifest_sha256=manifest.manifest_sha256,
    )


def _selected_source(
    root: Path,
) -> tuple[
    Path,
    Path,
    FreshFormalSourceSelectionV0233,
]:
    predecessor = _worktree_for_branch(root, _SOURCE_BRANCH)
    fallback_worktree = _worktree_for_branch(root, _FALLBACK_BRANCH)
    preferred, fallback = configured_source_candidates_v0233(
        preferred_root=predecessor / _SOURCE_LOCATOR,
        fallback_root=fallback_worktree / _FALLBACK_LOCATOR,
    )
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (root / "config/product-v0233/source-selection.json").read_bytes()
    )
    candidate = (
        preferred
        if selection.source_kind is FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE
        else fallback
    )
    observed = admit_fresh_formal_source_v0233(
        candidate,
        owner_counter=_database_owner_count,
        selection_reason=selection.selection_reason,
    )
    if observed != selection:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_PRIVATE_PRODUCT_STATE")
    return predecessor, candidate.source_root.resolve(strict=True), selection


def _updated_manifest(
    root: Path,
    **updates: Any,
) -> ProductV0233RepositoryStateManifest:
    current = ProductV0233RepositoryStateManifest.model_validate_json(
        (root / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    body = {
        **current.model_dump(mode="json", exclude={"manifest_sha256"}),
        **updates,
    }
    return ProductV0233RepositoryStateManifest.model_validate(
        {**body, "manifest_sha256": semantic_sha256_v22(body)}
    )


def _publish_manifest(
    root: Path,
    manifest: ProductV0233RepositoryStateManifest,
) -> None:
    _replace_public(
        root / "config/product-v0233/repository-state-manifest.json",
        manifest.model_dump(mode="json"),
    )


def _update_progress(root: Path, **updates: Any) -> dict[str, Any]:
    payload = _progress_payload(root, **updates)
    _replace_public(root / "docs/analysis/product-v0233-progress.json", payload)
    return payload


def _progress_payload(root: Path, **updates: Any) -> dict[str, Any]:
    path = root / "docs/analysis/product-v0233-progress.json"
    current = _object(path)
    body = {
        **{key: value for key, value in current.items() if key != "progress_sha256"},
        **updates,
    }
    payload = {**body, "progress_sha256": semantic_sha256_v22(body)}
    return payload


_TERMINAL_PUBLICATION_ALLOWED = frozenset(
    {
        "config/product-v0233/repository-state-manifest.json",
        "docs/analysis/product-v0233-progress.json",
        "docs/analysis/product-v0233-formal-closure.json",
        "docs/analysis/product-v0233-formal-state-clone.json",
        "docs/analysis/product-v0233-formal-blocker.json",
        "docs/analysis/product-v0233-diagnosis-stage-journal.json",
        "docs/analysis/product-v0233-diagnosis-blocker.json",
        "docs/analysis/product-v0233-diagnosis-blocker.md",
        "docs/analysis/product-v0233-runtime-authority.json",
        "docs/analysis/product-v0233-baseline-restart.json",
        "docs/analysis/product-v0233-formal-traffic.json",
        "docs/analysis/product-v0233-fresh-runtime-snapshot.json",
        "docs/analysis/product-v0233-incident-traffic-binding.json",
        "docs/analysis/product-v0233-evidence-assessment.json",
        "docs/analysis/product-v0233-knowledge-loop-handoff.json",
        "docs/analysis/product-v0233-knowledge-loop-handoff.md",
        "docs/results/product-v0233-nofault-acceptance.json",
        "docs/results/product-v0233-nofault-acceptance.md",
        "docs/results/product-v0233-limitations.md",
        "docs/results/product-v0233-interview-brief.md",
    }
)


def _terminal_publication_bundle(
    *,
    reservation: FormalExecutionReservationV0233,
    kind: str,
    terminal: str,
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    body = {
        "schema_version": "ecomsre.product.terminal-publication.v0233",
        "kind": kind,
        "terminal": terminal,
        "reservation_sha256": reservation.reservation_sha256,
        "artifacts": [dict(item) for item in artifacts],
    }
    return {**body, "publication_sha256": semantic_sha256_v22(body)}


def _apply_terminal_publication_bundle(
    root: Path,
    bundle: Mapping[str, Any],
) -> None:
    body = dict(bundle)
    supplied = body.pop("publication_sha256", None)
    artifacts = body.get("artifacts")
    kind = body.get("kind")
    if (
        supplied != semantic_sha256_v22(body)
        or kind not in {"BLOCKER", "MEASURED"}
        or not isinstance(artifacts, list)
        or not artifacts
    ):
        raise ValueError("Product v0.2.3.3 terminal publication differs")
    normalized: list[tuple[str, str, Mapping[str, Any] | str]] = []
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise ValueError("Product v0.2.3.3 terminal publication differs")
        relative = item.get("path")
        mode = item.get("mode")
        payload = item.get("payload")
        if (
            not isinstance(relative, str)
            or relative not in _TERMINAL_PUBLICATION_ALLOWED
            or mode not in {"CREATE_JSON", "CREATE_TEXT", "REPLACE_JSON"}
            or (mode == "CREATE_TEXT" and not isinstance(payload, str))
            or (mode != "CREATE_TEXT" and not isinstance(payload, Mapping))
        ):
            raise ValueError("Product v0.2.3.3 terminal publication differs")
        assert isinstance(relative, str)
        assert isinstance(mode, str)
        if mode == "CREATE_TEXT":
            assert isinstance(payload, str)
            normalized.append((relative, mode, payload))
        else:
            assert isinstance(payload, Mapping)
            normalized.append((relative, mode, payload))
    paths = [relative for relative, _mode, _payload in normalized]
    if len(paths) != len(set(paths)) or set(paths) - _TERMINAL_PUBLICATION_ALLOWED:
        raise ValueError("Product v0.2.3.3 terminal publication paths differ")
    blocker_present = "docs/analysis/product-v0233-formal-blocker.json" in paths
    result_present = "docs/results/product-v0233-nofault-acceptance.json" in paths
    manifest_present = "config/product-v0233/repository-state-manifest.json" in paths
    progress_present = "docs/analysis/product-v0233-progress.json" in paths
    if (
        blocker_present == result_present
        or blocker_present != (kind == "BLOCKER")
        or not manifest_present
        or not progress_present
    ):
        raise ValueError("Product v0.2.3.3 terminal publication shape differs")
    for relative, mode, payload in normalized:
        path = root / relative
        if mode == "CREATE_JSON":
            assert isinstance(payload, Mapping)
            _write_public_create_once(path, payload)
        elif mode == "CREATE_TEXT":
            assert isinstance(payload, str)
            _write_public_text_create_once(path, payload)
        else:
            assert isinstance(payload, Mapping)
            _replace_public(path, payload)


def _persist_and_apply_terminal_publication(
    *,
    root: Path,
    private_root: Path,
    bundle: Mapping[str, Any],
) -> None:
    intent_path = private_root / "terminal-publication.json"
    write_private_json(intent_path, dict(bundle), create_once=True)
    _apply_terminal_publication_bundle(root, bundle)
    completion_body = {
        "schema_version": "ecomsre.product.terminal-publication-completion.v0233",
        "publication_sha256": bundle["publication_sha256"],
        "terminal": bundle["terminal"],
    }
    write_private_json(
        private_root / "terminal-publication-completion.json",
        {
            **completion_body,
            "completion_sha256": semantic_sha256_v22(completion_body),
        },
        create_once=True,
    )


def _recover_terminal_publication(
    root: Path,
) -> NoFaultAcceptanceResultV0233 | None:
    reservation_path = root / _RESERVATION_LOCATOR
    if not reservation_path.exists():
        return None
    private_root = root / _PRIVATE_LOCATOR
    intent_path = private_root / "terminal-publication.json"
    if not intent_path.is_file():
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    reservation = FormalExecutionReservationV0233.model_validate_json(
        reservation_path.read_bytes()
    )
    bundle = _object(intent_path)
    if bundle.get("reservation_sha256") != reservation.reservation_sha256:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    _persist_and_apply_terminal_publication(
        root=root,
        private_root=private_root,
        bundle=bundle,
    )
    if bundle.get("kind") == "MEASURED":
        return NoFaultAcceptanceResultV0233.model_validate_json(
            (root / "docs/results/product-v0233-nofault-acceptance.json").read_bytes()
        )
    raise RuntimeError(str(bundle.get("terminal")))


def _cardinality(
    *,
    source: FreshFormalStateCountsV0233,
    current: FreshFormalStateCountsV0233,
    phase: str,
) -> FormalIncidentDiagnosisCardinalityV0233:
    return FormalIncidentDiagnosisCardinalityV0233.build(
        phase=phase,
        source_incident_count=source.incident_count,
        source_diagnosis_job_count=source.diagnosis_job_count,
        source_diagnosis_result_count=source.diagnosis_count,
        source_evidence_index_count=source.diagnosis_evidence_index_count,
        source_fault_family_count=source.fault_family_count,
        source_knowledge_artifact_count=source.knowledge_artifact_count,
        source_baseline_job_count=source.baseline_job_count,
        current_incident_count=current.incident_count,
        current_diagnosis_job_count=current.diagnosis_job_count,
        current_diagnosis_result_count=current.diagnosis_count,
        current_evidence_index_count=current.diagnosis_evidence_index_count,
        current_fault_family_count=current.fault_family_count,
        current_knowledge_artifact_count=current.knowledge_artifact_count,
        current_baseline_job_count=current.baseline_job_count,
    )


def _diagnosis_acceptance(
    *,
    product_root: Path,
    job: Any,
    diagnosis: DiagnosisResultV1 | None,
    evidence: EvidenceBundleV1 | None,
    index: DiagnosisEvidenceIndexV0232 | None,
    decision_trace_sha256: str | None,
) -> DiagnosisPipelineAcceptanceV0233:
    store = SqliteStoreV1(product_root / "product.sqlite3")
    events = DiagnosisStageJournalRepositoryV02322(store).list_events(job.job_id)
    if not events or job.journal_tail_sha256 != events[-1].event_sha256:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE")
    if job.status is ProductJobStatusV1.SUCCEEDED:
        failure_root = product_root / "private/diagnosis-failures" / job.job_id
        if (
            diagnosis is None
            or evidence is None
            or index is None
            or decision_trace_sha256 is None
            or events[-1].stage.value != "JOB_SUCCEEDED"
            or events[-1].status.value != "PASSED"
            or (failure_root.exists() and any(failure_root.iterdir()))
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE")
        return DiagnosisPipelineAcceptanceV0233.build_success(
            job_id=job.job_id,
            journal_tail_sha256=events[-1].event_sha256,
            event_count=len(events),
            diagnosis_result_sha256=diagnosis.result_sha256,
            evidence_bundle_sha256=semantic_sha256_v22(
                evidence.model_dump(mode="json")
            ),
            evidence_index_sha256=index.index_sha256,
            decision_trace_sha256=decision_trace_sha256,
        )
    if (
        job.status is not ProductJobStatusV1.FAILED
        or events[-1].stage.value != "FAILED"
        or events[-1].status.value != "FAILED"
        or job.failure_stage is None
        or job.safe_error_code is None
        or job.exception_fingerprint is None
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE")
    failure_root = product_root / "private/diagnosis-failures" / job.job_id
    files = tuple(sorted(failure_root.glob("*.json"))) if failure_root.is_dir() else ()
    if len(files) != 1:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE")
    envelope = DiagnosisPrivateFailureEnvelopeV02322.model_validate_json(
        files[0].read_bytes()
    )
    if (
        envelope.failure_envelope_sha256 != events[-1].output_artifact_sha256
        or envelope.exception_fingerprint != job.exception_fingerprint
        or envelope.failing_stage.value != job.failure_stage
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE")
    return DiagnosisPipelineAcceptanceV0233.build_failure(
        job_id=job.job_id,
        journal_tail_sha256=events[-1].event_sha256,
        event_count=len(events),
        failure_stage=job.failure_stage,
        safe_error_code=job.safe_error_code,
        exception_fingerprint=job.exception_fingerprint,
        private_failure_envelope_sha256=envelope.failure_envelope_sha256,
    )


def _closure_observation(
    *,
    queue_before_sha256: str | None,
    queue_after_sha256: str | None,
    outer_baseline_before_sha256: str | None,
    outer_baseline_after_sha256: str | None,
    source_before: FreshFormalSourceSelectionV0233,
    source_after: FreshFormalSourceSelectionV0233 | None,
    product_cleanup: Mapping[str, Any],
    demo_cleanup: Any,
    clone_owner_count: int | None,
    clone_baseline_binding_exact: bool,
    frozen_semantic_surface_before_sha256: str | None,
    frozen_semantic_surface_after_sha256: str | None,
    safety_observation: FormalSafetyObservationV0233,
) -> tuple[dict[str, Any], bool]:
    demo_payload = (
        demo_cleanup.model_dump(mode="json")
        if hasattr(demo_cleanup, "model_dump")
        else None
    )
    clean = (
        queue_before_sha256 is not None
        and queue_before_sha256 == queue_after_sha256
        and outer_baseline_before_sha256 is not None
        and outer_baseline_before_sha256 == outer_baseline_after_sha256
        and source_after == source_before
        and product_cleanup.get("verdict") == "CLEAN"
        and product_cleanup.get("owned_host_processes") == 0
        and product_cleanup.get("non_owned_resources_changed") is False
        and demo_payload is not None
        and demo_payload.get("verdict") == "CLEAN"
        and demo_payload.get("owned_containers") == 0
        and demo_payload.get("owned_networks") == 0
        and demo_payload.get("owned_volumes") == 0
        and demo_payload.get("non_owned_resources_changed") is False
        and clone_owner_count == 0
        and clone_baseline_binding_exact
        and frozen_semantic_surface_before_sha256 is not None
        and frozen_semantic_surface_before_sha256
        == frozen_semantic_surface_after_sha256
        and safety_observation.safe
    )
    if clean:
        assert source_after is not None
        proof = FormalClosureProofV0233.build(
            queue_before_sha256=queue_before_sha256,
            queue_after_sha256=queue_after_sha256,
            outer_baseline_before_sha256=outer_baseline_before_sha256,
            outer_baseline_after_sha256=outer_baseline_after_sha256,
            source_selection_before_sha256=source_before.selection_sha256,
            source_selection_after_sha256=source_after.selection_sha256,
            source_database_before_sha256=source_before.source_database_file_sha256,
            source_database_after_sha256=source_after.source_database_file_sha256,
            product_cleanup="CLEAN",
            demo_cleanup="CLEAN",
            owned_host_processes=0,
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            formal_clone_database_owner_count=0,
            non_owned_resources_changed=False,
            clone_baseline_binding_exact=True,
            frozen_semantic_surface_before_sha256=(
                frozen_semantic_surface_before_sha256
            ),
            frozen_semantic_surface_after_sha256=(frozen_semantic_surface_after_sha256),
            safety_observation=safety_observation.model_dump(mode="json"),
        )
        return proof.model_dump(mode="json"), True
    body = {
        "schema_version": "ecomsre.product.formal-closure-observation.v0233",
        "verdict": "BLOCKED",
        "queue_before_sha256": queue_before_sha256,
        "queue_after_sha256": queue_after_sha256,
        "outer_baseline_before_sha256": outer_baseline_before_sha256,
        "outer_baseline_after_sha256": outer_baseline_after_sha256,
        "source_selection_before_sha256": source_before.selection_sha256,
        "source_selection_after_sha256": (
            None if source_after is None else source_after.selection_sha256
        ),
        "source_database_before_sha256": source_before.source_database_file_sha256,
        "source_database_after_sha256": (
            None if source_after is None else source_after.source_database_file_sha256
        ),
        "product_cleanup": dict(product_cleanup),
        "demo_cleanup": demo_payload,
        "formal_clone_database_owner_count": clone_owner_count,
        "clone_baseline_binding_exact": clone_baseline_binding_exact,
        "frozen_semantic_surface_before_sha256": (
            frozen_semantic_surface_before_sha256
        ),
        "frozen_semantic_surface_after_sha256": frozen_semantic_surface_after_sha256,
        "safety_observation": safety_observation.model_dump(mode="json"),
    }
    return {**body, "closure_sha256": semantic_sha256_v22(body)}, False


def _blocker_terminal(stage: str, *, diagnosis_failed: bool) -> str:
    if stage == "CLONE_PENDING":
        return "BLOCKED_ECOMSRE_PRODUCT_V0233_STATE_CLONE"
    if stage in {"CLONE_CREATED", "SANDBOX_STARTED"}:
        return "BLOCKED_ECOMSRE_PRODUCT_V0233_RUNTIME_AUTHORITY"
    if stage in {"RUNTIME_AUTHORITY_VERIFIED", "PRODUCT_STARTED"}:
        return "BLOCKED_ECOMSRE_PRODUCT_V0233_PRODUCT_RESTART"
    if stage in {"BASELINE_RESTART_VERIFIED", "FORMAL_TRAFFIC_CONSUMED"}:
        return "BLOCKED_ECOMSRE_PRODUCT_V0233_FORMAL_HEALTHY_TRAFFIC"
    if diagnosis_failed:
        return "BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE"
    return "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"


def _terminalize_consumed_reservation_v0233(
    root: Path,
    *,
    trigger: BaseException,
) -> None:
    """Freeze an unbound consumed reservation without resuming any live action."""

    reservation = FormalExecutionReservationV0233.model_validate_json(
        (root / _RESERVATION_LOCATOR).read_bytes()
    )
    private_root = root / _PRIVATE_LOCATOR
    action_journal = FormalActionJournalV0233.build(
        observation_status="UNAVAILABLE",
        events=("RESERVATION_CONSUMED",),
    )
    source_before: FreshFormalSourceSelectionV0233 | None = None
    source_after: FreshFormalSourceSelectionV0233 | None = None
    starting_counts: FormalObservedStateCountsV0233 | None = None
    ending_counts: FormalObservedStateCountsV0233 | None = None
    starting_actions: Mapping[str, int | bool] | None = None
    ending_actions: Mapping[str, int | bool] | None = None
    try:
        _predecessor, source_root, source_before = _selected_source(root)
        source_after = source_before
        starting_counts = FormalObservedStateCountsV0233.model_validate(
            source_before.source_counts.model_dump(mode="json")
        )
        starting_actions = read_formal_diagnosis_action_totals_v0233(source_root)
    except (OSError, ValueError, RuntimeError):
        pass

    product_root = root / _FORMAL_DESTINATION
    clone_artifact_path = root / "docs/analysis/product-v0233-formal-state-clone.json"
    clone_exists = (
        product_root / "product.sqlite3"
    ).is_file() or clone_artifact_path.is_file()
    clone: FreshFormalStateCloneV0233 | None = None
    if clone_artifact_path.is_file():
        try:
            clone = FreshFormalStateCloneV0233.model_validate_json(
                clone_artifact_path.read_bytes()
            )
        except (OSError, ValueError):
            pass
    if (product_root / "product.sqlite3").is_file():
        try:
            ending_counts = FormalObservedStateCountsV0233.model_validate(
                read_raw_formal_state_counts_v0233(product_root)
            )
            ending_actions = read_formal_diagnosis_action_totals_v0233(product_root)
        except (OSError, ValueError, RuntimeError):
            pass

    new_incident_count: int | None = None
    new_diagnosis_count: int | None = None
    provider_calls: int | None = None
    agent_writes: int | None = None
    runbook_executions: int | None = None
    observed_action_authority: Literal["NONE"] | None = None
    if starting_counts is not None and ending_counts is not None:
        new_incident_count = (
            ending_counts.incident_count - starting_counts.incident_count
        )
        new_diagnosis_count = (
            ending_counts.diagnosis_job_count - starting_counts.diagnosis_job_count
        )
    if starting_actions is not None and ending_actions is not None:
        provider_calls = int(ending_actions["provider_calls"]) - int(
            starting_actions["provider_calls"]
        )
        agent_writes = int(ending_actions["agent_writes"]) - int(
            starting_actions["agent_writes"]
        )
        runbook_executions = int(ending_actions["runbook_executions"]) - int(
            starting_actions["runbook_executions"]
        )
        if bool(starting_actions["action_authority_none"]) and bool(
            ending_actions["action_authority_none"]
        ):
            observed_action_authority = "NONE"
    if not clone_exists:
        new_incident_count = 0
        new_diagnosis_count = 0

    safety_observation = FormalSafetyObservationV0233.build(
        observation_status="UNAVAILABLE",
        action_journal=action_journal.model_dump(mode="json"),
        starting_counts=(
            None if starting_counts is None else starting_counts.model_dump(mode="json")
        ),
        ending_counts=(
            None if ending_counts is None else ending_counts.model_dump(mode="json")
        ),
        new_incident_count=new_incident_count,
        new_diagnosis_count=new_diagnosis_count,
        provider_calls=provider_calls,
        agent_writes=agent_writes,
        runbook_executions=runbook_executions,
        fault_attempts=None,
        knowledge_loop_executions=None,
        observed_action_authority=observed_action_authority,
        safe=False,
    )
    closure_body = {
        "schema_version": "ecomsre.product.formal-closure-observation.v0233",
        "verdict": "BLOCKED",
        "failure": "FAIL_CLOSED_TERMINAL_RECOVERY",
        "trigger_type": type(trigger).__name__,
        "source_selection_before_sha256": (
            None if source_before is None else source_before.selection_sha256
        ),
        "source_selection_after_sha256": (
            None if source_after is None else source_after.selection_sha256
        ),
        "source_database_before_sha256": (
            None if source_before is None else source_before.source_database_file_sha256
        ),
        "source_database_after_sha256": (
            None if source_after is None else source_after.source_database_file_sha256
        ),
        "product_cleanup": "UNPROVEN",
        "demo_cleanup": "UNPROVEN",
        "formal_clone_database_owner_count": None,
        "clone_baseline_binding_exact": False,
        "safety_observation": safety_observation.model_dump(mode="json"),
    }
    closure = {
        **closure_body,
        "closure_sha256": semantic_sha256_v22(closure_body),
    }
    clone_count = 1 if clone_exists else 0
    blocker = FormalExecutionBlockerV0233.build(
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        failure_stage="FAIL_CLOSED_TERMINAL_RECOVERY",
        safe_error_code=f"{type(trigger).__name__}:FAIL_CLOSED_TERMINAL_RECOVERY"[:160],
        admission_sha256=reservation.admission.admission_sha256,
        reservation_sha256=reservation.reservation_sha256,
        formal_clone_count=clone_count,
        formal_clone_proof_status=(
            "NOT_CREATED"
            if clone_count == 0
            else ("OBSERVED" if clone is not None else "UNAVAILABLE")
        ),
        formal_clone_sha256=None if clone is None else clone.clone_sha256,
        formal_execution_count=1,
        new_incident_count=new_incident_count,
        new_diagnosis_count=new_diagnosis_count,
        cleanup_proof_sha256=(None if clone_count == 0 else closure["closure_sha256"]),
        safety_observation=safety_observation.model_dump(mode="json"),
    )
    blocked_manifest = _updated_manifest(
        root,
        phase=RepositoryPhaseV0233.FORMAL_BLOCKED.value,
        formal_blocker_sha256=blocker.blocker_sha256,
        cleanup_proof_sha256=blocker.cleanup_proof_sha256,
        formal_clone_count=clone_count,
        formal_execution_count=1,
        new_incident_count=new_incident_count,
        new_diagnosis_count=new_diagnosis_count,
        measured_result_count=0,
    )
    transaction_count: int | None = 0 if clone_count == 0 else None
    traffic_path = private_root / "traffic-execution.json"
    if traffic_path.is_file():
        try:
            transaction_count = HealthyTrafficExecutionV0232.model_validate_json(
                traffic_path.read_bytes()
            ).run.completed_transactions
        except (OSError, ValueError):
            pass
    blocked_progress = _progress_payload(
        root,
        phase="INCREMENT_4_FORMAL_BLOCKED",
        current_terminal=blocker.terminal,
        next_gate="NONE",
        formal_clone_count=clone_count,
        formal_execution_count=1,
        formal_transaction_count=transaction_count,
        new_incident_count=new_incident_count,
        new_diagnosis_count=new_diagnosis_count,
        measured_result_count=0,
        formal_blocker_sha256=blocker.blocker_sha256,
        cleanup_proof_sha256=blocker.cleanup_proof_sha256,
        repository_state_manifest_sha256=blocked_manifest.manifest_sha256,
    )
    publication = _terminal_publication_bundle(
        reservation=reservation,
        kind="BLOCKER",
        terminal=blocker.terminal,
        artifacts=(
            {
                "path": "docs/analysis/product-v0233-formal-closure.json",
                "mode": "CREATE_JSON",
                "payload": closure,
            },
            {
                "path": "docs/analysis/product-v0233-formal-blocker.json",
                "mode": "CREATE_JSON",
                "payload": blocker.model_dump(mode="json"),
            },
            {
                "path": "config/product-v0233/repository-state-manifest.json",
                "mode": "REPLACE_JSON",
                "payload": blocked_manifest.model_dump(mode="json"),
            },
            {
                "path": "docs/analysis/product-v0233-progress.json",
                "mode": "REPLACE_JSON",
                "payload": blocked_progress,
            },
        ),
    )
    _persist_and_apply_terminal_publication(
        root=root,
        private_root=private_root,
        bundle=publication,
    )


def _knowledge_handoff(
    result: NoFaultAcceptanceResultV0233,
) -> dict[str, Any]:
    ready = result.measured_terminal.endswith("FULLY_SUPPORTED")
    body = {
        "schema_version": "ecomsre.product.knowledge-loop-handoff.v0233",
        "terminal": (
            "ECOMSRE_PRODUCT_V0233_KNOWLEDGE_LOOP_HANDOFF_READY"
            if ready
            else "ECOMSRE_PRODUCT_V0233_KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED"
        ),
        "nofault_result_sha256": result.result_sha256,
        "measured_terminal": result.measured_terminal,
        "repair_requirements": () if ready else result.reasons,
        "knowledge_loop_campaigns": 0,
        "action_authority": "NONE",
    }
    return {**body, "handoff_sha256": semantic_sha256_v22(body)}


def _pipeline_public(
    pipeline: DiagnosisPipelineAcceptanceV0233,
) -> dict[str, Any]:
    body = {
        **pipeline.model_dump(mode="json"),
        "terminal": (
            "ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE_PASS"
            if pipeline.job_status == "SUCCEEDED"
            else "BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE"
        ),
    }
    return {**body, "public_projection_sha256": semantic_sha256_v22(body)}


def _run_formal_nofault_once_v0233(
    *,
    project_root: Path,
) -> NoFaultAcceptanceResultV0233:
    root = Path(project_root).resolve(strict=True)
    admission = strict_formal_admission_v0233(root)
    predecessor, source_root, source_before = _selected_source(root)
    _require_preserved_runtime_root_v02321(predecessor, source_root)
    clone_plan = FormalClonePlanV0233.model_validate_json(
        (root / "docs/analysis/product-v0233-formal-clone-plan.json").read_bytes()
    )
    campaign = load_fresh_formal_campaign_v0233(root)
    public_profile = load_fresh_traffic_profile_v0233(root, role="FORMAL")
    formal_profile = public_profile.engine_profile_v0232()
    contract = load_checkout_traffic_contract_v0232(root)
    if (
        public_profile.profile_sha256
        != _object(
            root / "docs/analysis/product-v0233-formal-contract-freeze.json"
        ).get("formal_profile_sha256")
        or contract.contract_sha256 != campaign.traffic_contract_sha256
        or formal_profile.transactions != 30
        or formal_profile.minimum_full_episode_duration_seconds != 300
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    manifest_v0231 = _object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest_v0231.get("private_state")
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
    _attempt, audit, _source_data_root = _attempt_context(predecessor)
    freeze = FormalContractFreezeV0233.model_validate_json(
        (root / "docs/analysis/product-v0233-formal-contract-freeze.json").read_bytes()
    )
    if (
        source_before.source_kind
        is not FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE
        or audit.environment_id != ENVIRONMENT_ID_V0232
        or tracked_runtime.descriptor_sha256
        != freeze.runtime_continuity_descriptor_sha256
        or preserved_authority.pilot_authority_sha256
        != freeze.pilot_runtime_authority_sha256
        or preserved_authority.read_authority.authority_sha256
        != freeze.read_authority_sha256
        or audit.active_opensearch_profile_sha256 != freeze.active_profile_sha256
        or audit.baseline_sha256 != freeze.active_baseline_sha256
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RUNTIME_AUTHORITY")
    frozen_semantic_surface_before = _frozen_semantic_surface_sha256_v0233(root)
    product_root = root / clone_plan.destination_locator
    starting_counts = source_before.source_counts
    starting_observed_counts = FormalObservedStateCountsV0233.model_validate(
        starting_counts.model_dump(mode="json")
    )
    source_action_totals = read_formal_diagnosis_action_totals_v0233(source_root)
    private_root = root / _PRIVATE_LOCATOR
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=private_root / "demo",
        binding=binding,
        context=context,
        bundle=bundle,
        preserved_authority=preserved_authority,
        preserved_resolved_compose=resolved_compose,
    )
    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_root,
        private_root=private_root / "product-processes",
    )
    if processes.cleanup_observation().get("verdict") != "CLEAN":
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_PRODUCT_RESTART")

    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime.now(UTC),
    )
    stage = "RESERVATION_PENDING"
    clone: FreshFormalStateCloneV0233 | None = None
    live_error: BaseException | None = None
    authority: RuntimeAuthorityProofV0233 | None = None
    restart: BaselineRestartProofV0233 | None = None
    execution: HealthyTrafficExecutionV0232 | None = None
    traffic: FormalTrafficResultV0233 | None = None
    fresh_snapshot_proof: FreshRuntimeSnapshotProofV0233 | None = None
    incident: Any = None
    incident_binding: IncidentTrafficBindingV0232 | None = None
    diagnosis: DiagnosisResultV1 | None = None
    evidence: EvidenceBundleV1 | None = None
    index: DiagnosisEvidenceIndexV0232 | None = None
    decision_trace: Any = None
    assessment: NoFaultEvidenceAssessmentV0232 | None = None
    pipeline: DiagnosisPipelineAcceptanceV0233 | None = None
    queue_before_sha256: str | None = None
    queue_after_sha256: str | None = None
    outer_baseline_before: str | None = None
    outer_baseline_after: str | None = None
    product_cleanup: Mapping[str, Any] = {"verdict": "BLOCKED"}
    demo_cleanup: Any = None
    source_after: FreshFormalSourceSelectionV0233 | None = None
    clone_owner_count: int | None = None
    clone_baseline_binding_exact = False
    frozen_semantic_surface_after: str | None = None
    diagnosis_failed = False
    safe_error_code = "FORMAL_EXECUTION_FAILED"
    action_events: list[FormalActionEventV0233] = []

    action_events.append("RESERVATION_CONSUMED")
    write_private_json(
        root / _RESERVATION_LOCATOR,
        reservation.model_dump(mode="json"),
        create_once=True,
    )
    try:
        private_root.mkdir(parents=True, mode=0o700)
        write_private_json(
            private_root / "admission.json",
            admission.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / "reservation.json",
            reservation.model_dump(mode="json"),
            create_once=True,
        )

        stage = "CLONE_PENDING"
        action_events.append("FORMAL_CLONE_REQUESTED")
        clone = clone_fresh_formal_state_v0233(
            selection=source_before,
            source_root=source_root,
            destination_root=root / clone_plan.destination_locator,
            destination_locator=clone_plan.destination_locator,
            owner_counter=_database_owner_count,
        )
        stage = "CLONE_CREATED"
        _write_public_create_once(
            root / "docs/analysis/product-v0233-formal-state-clone.json",
            clone.model_dump(mode="json"),
        )
        running_manifest = _updated_manifest(
            root,
            phase=RepositoryPhaseV0233.FORMAL_RUNNING.value,
            formal_clone_count=1,
            formal_execution_count=1,
        )
        _publish_manifest(root, running_manifest)
        _update_progress(
            root,
            phase="INCREMENT_4_FORMAL_RUNNING",
            current_terminal="ECOMSRE_PRODUCT_V0233_FORMAL_EXECUTION_ADMITTED",
            next_gate="FORMAL_ONE_SHOT_TERMINAL",
            formal_clone_count=1,
            formal_execution_count=1,
            formal_state_clone_sha256=clone.clone_sha256,
            repository_state_manifest_sha256=running_manifest.manifest_sha256,
        )

        observed_starting_counts = read_fresh_formal_state_counts_v0233(product_root)
        if observed_starting_counts != starting_counts:
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_STATE_CLONE")
        typed_plan = build_traffic_harness_typed_request_plan_v02321(
            campaign_sha256=campaign.campaign_sha256,
            role="FORMAL",
            state_clone_sha256=clone.clone_sha256,
            attempt_ordinal=1,
        )
        runtime_request = materialize_planned_request_v02321(
            typed_plan, tool_name="inspect_service_runtime"
        )
        write_private_json(
            private_root / "typed-request-plan.json",
            typed_plan.model_dump(mode="json"),
            create_once=True,
        )
        if (
            _database_owner_count(product_root / "product.sqlite3") != 0
            or processes.cleanup_observation().get("verdict") != "CLEAN"
            or _frozen_semantic_surface_sha256_v0233(root)
            != frozen_semantic_surface_before
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_PRODUCT_RESTART")

        lifecycle.admit_prestart()
        if lifecycle.runtime_descriptor != tracked_runtime:
            raise RuntimeError("RUNTIME_DESCRIPTOR_DRIFT")
        action_events.append("DEMO_START_REQUESTED")
        lifecycle.start()
        stage = "SANDBOX_STARTED"
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if lifecycle.rebound_authority != preserved_authority:
            raise RuntimeError("RUNTIME_AUTHORITY_DRIFT")
        checkout = _checkout_runtime(backend, runtime_request)
        if checkout != ("RUNNING", True, 0):
            raise RuntimeError("CHECKOUT_RUNTIME_NOT_HEALTHY")
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=formal_profile.queue_fault_flag,
        )
        queue_before_sha256 = queue_before.before_sha256
        outer_baseline_before = lifecycle.read_baseline_sha256()

        runtime_path = product_root / "pilot/runtime-readiness.json"
        first_snapshot = _runtime_snapshot(
            backend=backend,
            authority=preserved_authority,
        )
        first_rotation = _rotate_runtime_snapshot_v0231(
            data_root=product_root,
            path=runtime_path,
            snapshot=first_snapshot,
            private_root=private_root,
            ordinal=1,
        )
        _authority_proof(
            descriptor=tracked_runtime,
            authority=preserved_authority,
            backend=backend,
            rotation=first_rotation,
        )
        authority = RuntimeAuthorityProofV0233.build(
            admission_sha256=admission.admission_sha256,
            runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            pilot_runtime_authority_sha256=(preserved_authority.pilot_authority_sha256),
            runtime_connector_binding_sha256=(
                preserved_authority.connector_binding_sha256
            ),
            runtime_snapshot_sha256=first_snapshot.snapshot_sha256,
            checkout_state="RUNNING",
            checkout_healthy=True,
            checkout_restart_count=0,
        )
        write_private_json(
            private_root / "runtime-authority.json",
            authority.model_dump(mode="json"),
            create_once=True,
        )
        stage = "RUNTIME_AUTHORITY_VERIFIED"

        if (
            _frozen_semantic_surface_sha256_v0233(root)
            != frozen_semantic_surface_before
        ):
            raise RuntimeError("FROZEN_SEMANTIC_SURFACE_DRIFT")
        action_events.append("PRODUCT_START_REQUESTED")
        processes.start()
        stage = "PRODUCT_STARTED"
        before_restart = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=context.service_identity_sha256,
            baseline_candidate_identity_sha256=audit.service_identity_sha256,
            capability_sha256=context.capability_sha256,
        )
        action_events.append("PRODUCT_RESTART_REQUESTED")
        processes.restart()
        identity, capability, candidate = _load_persisted_bindings(product_root, audit)
        rebound_audit = ProductBaselineReadinessAuditV023.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/baselines/{audit.baseline_id}/window-audit-v023",
            )
        )
        after_restart = _restart_snapshot(
            processes,
            environment_id=audit.environment_id,
            service_identity_sha256=identity.identity_sha256,
            baseline_candidate_identity_sha256=candidate,
            capability_sha256=capability.capability_sha256,
        )
        BaselineRestartProofV023.build(
            before=before_restart,
            after=after_restart,
            connector_verification_count=0,
        )
        pending, running, failed = _queue_counts(product_root)
        if (
            identity.identity_sha256 != context.service_identity_sha256
            or capability.capability_sha256 != context.capability_sha256
            or candidate != audit.service_identity_sha256
            or rebound_audit.audit_sha256 != audit.audit_sha256
            or read_fresh_formal_state_counts_v0233(product_root) != starting_counts
            or pending != 0
            or running != 0
            or failed != starting_counts.failed_job_count
        ):
            raise RuntimeError("PRODUCT_RESTART_BINDING_DRIFT")
        restart = BaselineRestartProofV0233.build(
            admission_sha256=admission.admission_sha256,
            environment_id=audit.environment_id,
            active_baseline_id=audit.baseline_id,
            active_baseline_sha256=audit.baseline_sha256,
            active_profile_sha256=audit.active_opensearch_profile_sha256,
            readiness_audit_sha256=audit.audit_sha256,
            api_instance_id_before=before_restart.api_instance_id,
            api_instance_id_after=after_restart.api_instance_id,
            worker_instance_id_before=before_restart.worker_instance_id,
            worker_instance_id_after=after_restart.worker_instance_id,
            failed_jobs_before=starting_counts.failed_job_count,
            failed_jobs_after=failed,
        )
        write_private_json(
            private_root / "baseline-restart.json",
            restart.model_dump(mode="json"),
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
        dispatches: list[FormalTrafficDispatchCheckpointV02321] = []
        observations: list[FormalTrafficObservationCheckpointV02321] = []
        transaction_observations: list[Any] = []
        journal_state: dict[str, Any] = {}

        def persist(name: str, payload: Mapping[str, Any]) -> None:
            write_private_json(
                private_root / "traffic-journal" / name,
                dict(payload),
                create_once=True,
            )

        with HealthyTrafficRunnerV0232() as runner:
            action_events.append("FORMAL_TRAFFIC_REQUESTED")
            execution = run_formal_traffic_journaled_v02321(
                runner=runner,
                endpoint=_ENDPOINT,
                profile=formal_profile,
                contract=contract,
                consumption=consumption,
                dispatch_checkpoints=dispatches,
                observation_checkpoints=observations,
                observations=transaction_observations,
                state=journal_state,
                persist=persist,
            )
        write_private_json(
            private_root / "traffic-execution.json",
            execution.model_dump(mode="json"),
            create_once=True,
        )
        if not execution.run.passed:
            raise RuntimeError("FORMAL_TRAFFIC_NOT_PASS")
        remaining = 300 - (time.monotonic() - episode_started_monotonic)
        if remaining > 0:
            time.sleep(remaining)
        episode_ended_at = datetime.now(UTC)
        traffic = FormalTrafficResultV0233.build(
            admission_sha256=admission.admission_sha256,
            formal_profile_sha256=public_profile.profile_sha256,
            traffic_contract_sha256=contract.contract_sha256,
            execution=execution,
            episode_started_at=episode_started_at,
            episode_ended_at=episode_ended_at,
            monotonic_duration_ms=int(
                (time.monotonic() - episode_started_monotonic) * 1000
            ),
        )
        write_private_json(
            private_root / "formal-traffic.json",
            traffic.model_dump(mode="json"),
            create_once=True,
        )
        stage = "FORMAL_TRAFFIC_PASS"

        checkout_after = _checkout_runtime(backend, runtime_request)
        if checkout_after != ("RUNNING", True, 0):
            raise RuntimeError("CHECKOUT_RUNTIME_DRIFT")
        fresh_snapshot = _runtime_snapshot(
            backend=backend,
            authority=preserved_authority,
        )
        second_rotation = _rotate_runtime_snapshot_v0231(
            data_root=product_root,
            path=runtime_path,
            snapshot=fresh_snapshot,
            private_root=private_root,
            ordinal=2,
        )
        if (
            second_rotation["after_snapshot_sha256"] != fresh_snapshot.snapshot_sha256
            or fresh_snapshot.observed_at < traffic.episode_started_at
        ):
            raise RuntimeError("FRESH_RUNTIME_SNAPSHOT_DRIFT")
        fresh_snapshot_proof = FreshRuntimeSnapshotProofV0233.build(
            admission_sha256=admission.admission_sha256,
            formal_traffic_result_sha256=traffic.result_sha256,
            runtime_snapshot_sha256=fresh_snapshot.snapshot_sha256,
            observed_at=fresh_snapshot.observed_at,
            pilot_runtime_authority_sha256=(preserved_authority.pilot_authority_sha256),
            runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            runtime_connector_binding_sha256=(
                preserved_authority.connector_binding_sha256
            ),
            checkout_state="RUNNING",
            checkout_healthy=True,
            checkout_restart_count=0,
        )
        write_private_json(
            private_root / "fresh-runtime-snapshot.json",
            fresh_snapshot_proof.model_dump(mode="json"),
            create_once=True,
        )
        stage = "FRESH_RUNTIME_SNAPSHOT_VERIFIED"

        before_incident = read_fresh_formal_state_counts_v0233(product_root)
        _cardinality(
            source=starting_counts,
            current=before_incident,
            phase="PRE_INCIDENT",
        )
        admit_incident_creation_v0233(
            runtime_authority_pass=authority is not None,
            baseline_restart_pass=restart is not None,
            formal_traffic_pass=traffic is not None,
            fresh_runtime_snapshot_pass=fresh_snapshot_proof is not None,
            new_incident_count=0,
            new_diagnosis_count=0,
        )
        external_key = f"product-v0233-nofault-{campaign.campaign_sha256[:16]}"
        action_events.append("INCIDENT_CREATE_REQUESTED")
        incident = _request_or_recover_incident_v02321(
            request=lambda: _request_json(
                processes,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": audit.environment_id,
                    "external_incident_key": external_key,
                    "alert_name": "Product v0.2.3.3 No-Fault acceptance",
                    "summary": "Fresh formal healthy checkout observation with no fault active.",
                    "started_at": episode_started_at.isoformat(),
                    "ended_at": fresh_snapshot.observed_at.isoformat(),
                    "candidate_service_ids": list(audit.baseline_entity_service_ids),
                    "labels": {"fault": "none"},
                },
            ),
            recover=lambda: _recover_incident_by_external_key(
                product_root,
                environment_id=audit.environment_id,
                external_incident_key=external_key,
            ),
        )
        if (
            incident.service_identity_sha256 != context.service_identity_sha256
            or incident.source_capability_sha256 != context.capability_sha256
            or incident.baseline_id != audit.baseline_id
            or incident.baseline_sha256 != audit.baseline_sha256
        ):
            raise RuntimeError("INCIDENT_BINDING_DRIFT")
        write_private_json(
            private_root / "incident.json",
            incident.model_dump(mode="json"),
            create_once=True,
        )
        incident_binding = IncidentTrafficBindingV0232.build(
            incident_id=incident.incident_id,
            execution=execution,
            episode_started_at=episode_started_at,
            episode_ended_at=fresh_snapshot.observed_at,
        )
        write_private_json(
            private_root / "incident-traffic-binding.json",
            incident_binding.model_dump(mode="json"),
            create_once=True,
        )
        _cardinality(
            source=starting_counts,
            current=read_fresh_formal_state_counts_v0233(product_root),
            phase="POST_INCIDENT_PRE_DIAGNOSIS",
        )
        stage = "INCIDENT_CREATED"

        action_events.append("DIAGNOSIS_CREATE_REQUESTED")
        queued = _request_or_recover_diagnosis_job_v02321(
            request=lambda: _request_json(
                processes,
                "POST",
                f"/v1/incidents/{incident.incident_id}/diagnosis-jobs",
                payload=None,
            ),
            recover=lambda: _recover_diagnosis_job(
                product_root,
                incident_id=incident.incident_id,
            ),
        )
        write_private_json(
            private_root / "diagnosis-job.json",
            queued.model_dump(mode="json"),
            create_once=True,
        )
        stage = "DIAGNOSIS_SUBMITTED"
        completed_job = _wait_job(
            processes,
            queued.job_id,
            data_root=product_root,
            timeout_seconds=240,
        )
        write_private_json(
            private_root / "diagnosis-job-completion.json",
            completed_job.model_dump(mode="json"),
            create_once=True,
        )
        if completed_job.status is ProductJobStatusV1.SUCCEEDED and isinstance(
            completed_job.result, dict
        ):
            diagnosis = DiagnosisResultV1.model_validate(completed_job.result)
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
                product_root,
                expected_sha256=index.decision_trace_sha256,
            )
            assessment = score_nofault_evidence_v0232(
                diagnosis=diagnosis,
                bundle=evidence,
                index=index,
                decision_trace=decision_trace,
            )
            pipeline = _diagnosis_acceptance(
                product_root=product_root,
                job=completed_job,
                diagnosis=diagnosis,
                evidence=evidence,
                index=index,
                decision_trace_sha256=decision_trace.trace_sha256,
            )
            for name, model in (
                ("diagnosis.json", diagnosis),
                ("evidence-bundle.json", evidence),
                ("evidence-index.json", index),
                ("decision-trace.json", decision_trace),
                ("assessment.json", assessment),
                ("diagnosis-pipeline.json", pipeline),
            ):
                write_private_json(
                    private_root / name,
                    model.model_dump(mode="json"),
                    create_once=True,
                )
            _cardinality(
                source=starting_counts,
                current=read_fresh_formal_state_counts_v0233(product_root),
                phase="POST_DIAGNOSIS_SUCCEEDED",
            )
            if (
                diagnosis.provider_calls != 0
                or diagnosis.agent_writes != 0
                or diagnosis.runbook_executions != 0
                or diagnosis.action_authority.value != "NONE"
            ):
                raise RuntimeError("UNEXPECTED_ACTION_AUTHORITY")
            stage = "DIAGNOSIS_PIPELINE_PASS"
        else:
            diagnosis_failed = True
            pipeline = _diagnosis_acceptance(
                product_root=product_root,
                job=completed_job,
                diagnosis=None,
                evidence=None,
                index=None,
                decision_trace_sha256=None,
            )
            write_private_json(
                private_root / "diagnosis-pipeline.json",
                pipeline.model_dump(mode="json"),
                create_once=True,
            )
            _cardinality(
                source=starting_counts,
                current=read_fresh_formal_state_counts_v0233(product_root),
                phase="POST_DIAGNOSIS_FAILED",
            )
            raise _DiagnosisJobFailedV0233(
                acceptance=pipeline,
                safe_error_code=completed_job.safe_error_code
                or "INTERNAL_CONTRACT_FAILURE",
            )
    except BaseException as error:
        live_error = error
        if isinstance(error, _DiagnosisJobFailedV0233):
            safe_error_code = error.safe_error_code
        else:
            safe_error_code = f"{type(error).__name__}:{stage}"[:160]
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
        if clone is not None:
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
        try:
            _predecessor, _source_root, source_after = _selected_source(root)
            if clone is not None:
                clone_owner_count = _database_owner_count(
                    product_root / "product.sqlite3"
                )
        except BaseException as error:
            if live_error is None:
                live_error = error
        try:
            frozen_semantic_surface_after = _frozen_semantic_surface_sha256_v0233(root)
        except BaseException as error:
            if live_error is None:
                live_error = error
        if clone is not None:
            try:
                clone_baseline_binding_exact = read_formal_active_binding_v0233(
                    product_root
                ) == {
                    "environment_id": audit.environment_id,
                    "baseline_id": audit.baseline_id,
                    "baseline_sha256": audit.baseline_sha256,
                    "profile_sha256": audit.active_opensearch_profile_sha256,
                }
            except BaseException as error:
                if live_error is None:
                    live_error = error

    action_journal = FormalActionJournalV0233.build(
        observation_status="COMPLETE",
        events=tuple(action_events),
    )
    try:
        write_private_json(
            private_root / "action-journal.json",
            action_journal.model_dump(mode="json"),
            create_once=True,
        )
    except BaseException as error:
        if live_error is None:
            live_error = error
            safe_error_code = "FORMAL_ACTION_JOURNAL_PERSISTENCE_FAILED"

    safety_observation = _safety_observation(
        starting_counts=starting_observed_counts,
        source_action_totals=source_action_totals,
        product_root=product_root if clone is not None else None,
        action_journal=action_journal,
    )
    closure: dict[str, Any] | None = None
    closure_sha256: str | None = None
    closure_clean = False
    if clone is not None:
        try:
            closure, closure_clean = _closure_observation(
                queue_before_sha256=queue_before_sha256,
                queue_after_sha256=queue_after_sha256,
                outer_baseline_before_sha256=outer_baseline_before,
                outer_baseline_after_sha256=outer_baseline_after,
                source_before=source_before,
                source_after=source_after,
                product_cleanup=product_cleanup,
                demo_cleanup=demo_cleanup,
                clone_owner_count=clone_owner_count,
                clone_baseline_binding_exact=clone_baseline_binding_exact,
                frozen_semantic_surface_before_sha256=frozen_semantic_surface_before,
                frozen_semantic_surface_after_sha256=frozen_semantic_surface_after,
                safety_observation=safety_observation,
            )
        except BaseException as error:
            if live_error is None:
                live_error = error
                safe_error_code = "FORMAL_CLOSURE_CONSTRUCTION_FAILED"
            body = {
                "schema_version": "ecomsre.product.formal-closure-observation.v0233",
                "verdict": "BLOCKED",
                "failure": "FORMAL_CLOSURE_CONSTRUCTION_FAILED",
                "product_cleanup": dict(product_cleanup),
                "formal_clone_database_owner_count": clone_owner_count,
                "clone_baseline_binding_exact": clone_baseline_binding_exact,
                "frozen_semantic_surface_before_sha256": (
                    frozen_semantic_surface_before
                ),
                "frozen_semantic_surface_after_sha256": (frozen_semantic_surface_after),
                "safety_observation": safety_observation.model_dump(mode="json"),
            }
            closure = {**body, "closure_sha256": semantic_sha256_v22(body)}
            closure_clean = False
        closure_sha256 = str(closure["closure_sha256"])
        if not closure_clean and live_error is None:
            live_error = RuntimeError("FORMAL_CLOSURE_NOT_CLEAN")
            safe_error_code = "FORMAL_CLOSURE_NOT_CLEAN"

    ending_counts = safety_observation.ending_counts
    new_incident_count = safety_observation.new_incident_count
    new_diagnosis_count = safety_observation.new_diagnosis_count
    result: NoFaultAcceptanceResultV0233 | None = None
    handoff: dict[str, Any] | None = None
    if live_error is None:
        try:
            if clone is None:
                raise RuntimeError("FORMAL_STATE_CLONE_MISSING")
            required = (
                authority,
                restart,
                execution,
                traffic,
                fresh_snapshot_proof,
                incident,
                incident_binding,
                diagnosis,
                evidence,
                index,
                decision_trace,
                assessment,
                pipeline,
            )
            if any(value is None for value in required):
                raise RuntimeError("FORMAL_ACCEPTANCE_ARTIFACT_MISSING")
            if ending_counts is None or (
                new_incident_count != 1
                or new_diagnosis_count != 1
                or ending_counts.diagnosis_count - starting_counts.diagnosis_count != 1
                or ending_counts.diagnosis_evidence_index_count
                - starting_counts.diagnosis_evidence_index_count
                != 1
                or ending_counts.fault_family_count
                != starting_counts.fault_family_count
                or ending_counts.knowledge_artifact_count
                != starting_counts.knowledge_artifact_count
                or ending_counts.baseline_job_count
                != starting_counts.baseline_job_count
                or not safety_observation.safe
                or closure_sha256 is None
            ):
                raise RuntimeError("FORMAL_ACCEPTANCE_CARDINALITY_DRIFT")
            assert authority is not None
            assert restart is not None
            assert execution is not None
            assert traffic is not None
            assert fresh_snapshot_proof is not None
            assert incident_binding is not None
            assert diagnosis is not None
            assert evidence is not None
            assert index is not None
            assert decision_trace is not None
            assert assessment is not None
            assert pipeline is not None
            if (
                action_journal.fault_attempts != 0
                or action_journal.knowledge_loop_executions != 0
            ):
                raise RuntimeError("FORMAL_ACTION_JOURNAL_FORBIDDEN_DISPATCH")
            safety = SafetyCountersV0233(
                agent_writes=diagnosis.agent_writes,
                runbook_executions=diagnosis.runbook_executions,
                provider_calls=diagnosis.provider_calls,
                fault_attempts=cast(Literal[0], action_journal.fault_attempts),
                knowledge_loop_executions=cast(
                    Literal[0], action_journal.knowledge_loop_executions
                ),
            )
            result = NoFaultAcceptanceResultV0233.build_from_v0232(
                campaign_sha256=campaign.campaign_sha256,
                source_selection_sha256=source_before.selection_sha256,
                formal_clone_sha256=clone.clone_sha256,
                runtime_authority_proof_sha256=authority.proof_sha256,
                baseline_restart_proof_sha256=restart.proof_sha256,
                traffic_preflight_sha256=str(
                    _object(
                        root / "docs/analysis/product-v0233-traffic-preflight.json"
                    )["preflight_sha256"]
                ),
                formal_traffic_execution_sha256=execution.execution_sha256,
                fresh_runtime_snapshot_sha256=(
                    fresh_snapshot_proof.runtime_snapshot_sha256
                ),
                incident_traffic_binding_sha256=incident_binding.binding_sha256,
                incident_sha256=incident.incident_sha256,
                diagnosis_result_sha256=diagnosis.result_sha256,
                evidence_bundle_sha256=semantic_sha256_v22(
                    evidence.model_dump(mode="json")
                ),
                evidence_index_sha256=index.index_sha256,
                decision_trace_sha256=decision_trace.trace_sha256,
                stage_journal_tail_sha256=pipeline.journal_tail_sha256,
                v0232_assessment_sha256=assessment.result_sha256,
                v0232_measured_terminal=assessment.terminal.value,
                reasons=assessment.reasons,
                safety_counters=safety.model_dump(mode="json"),
                cleanup_proof_sha256=closure_sha256,
            )
            handoff = _knowledge_handoff(result)
        except BaseException as error:
            live_error = error
            stage = "ACCEPTANCE_ARTIFACT_CONSTRUCTION"
            safe_error_code = f"{type(error).__name__}:{stage}"[:160]
    if live_error is not None:
        terminal = _blocker_terminal(stage, diagnosis_failed=diagnosis_failed)
        blocker = FormalExecutionBlockerV0233.build(
            terminal=terminal,
            failure_stage=stage,
            safe_error_code=safe_error_code,
            admission_sha256=admission.admission_sha256,
            reservation_sha256=reservation.reservation_sha256,
            formal_clone_count=0 if clone is None else 1,
            formal_clone_proof_status=("NOT_CREATED" if clone is None else "OBSERVED"),
            formal_clone_sha256=None if clone is None else clone.clone_sha256,
            formal_execution_count=1,
            new_incident_count=(0 if clone is None else new_incident_count),
            new_diagnosis_count=(0 if clone is None else new_diagnosis_count),
            cleanup_proof_sha256=closure_sha256,
            journal_tail_sha256=(
                pipeline.journal_tail_sha256
                if diagnosis_failed and pipeline is not None
                else None
            ),
            exception_fingerprint=(
                pipeline.exception_fingerprint
                if diagnosis_failed and pipeline is not None
                else None
            ),
            private_failure_envelope_sha256=(
                pipeline.private_failure_envelope_sha256
                if diagnosis_failed and pipeline is not None
                else None
            ),
            safety_observation=safety_observation.model_dump(mode="json"),
        )
        terminal_artifacts: list[dict[str, Any]] = []
        if clone is not None:
            terminal_artifacts.append(
                {
                    "path": "docs/analysis/product-v0233-formal-state-clone.json",
                    "mode": "CREATE_JSON",
                    "payload": clone.model_dump(mode="json"),
                }
            )
        if closure is not None:
            terminal_artifacts.append(
                {
                    "path": "docs/analysis/product-v0233-formal-closure.json",
                    "mode": "CREATE_JSON",
                    "payload": closure,
                }
            )
        terminal_artifacts.append(
            {
                "path": "docs/analysis/product-v0233-formal-blocker.json",
                "mode": "CREATE_JSON",
                "payload": blocker.model_dump(mode="json"),
            }
        )
        if pipeline is not None:
            terminal_artifacts.append(
                {
                    "path": (
                        "docs/analysis/product-v0233-diagnosis-stage-journal.json"
                    ),
                    "mode": "CREATE_JSON",
                    "payload": _pipeline_public(pipeline),
                }
            )
        if diagnosis_failed and pipeline is not None:
            diagnosis_blocker = {
                "schema_version": "ecomsre.product.diagnosis-blocker.v0233",
                "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE",
                "job_id": pipeline.job_id,
                "safe_error_code": pipeline.safe_error_code,
                "failure_stage": pipeline.failure_stage,
                "exception_fingerprint": pipeline.exception_fingerprint,
                "journal_tail_sha256": pipeline.journal_tail_sha256,
                "private_failure_envelope_sha256": (
                    pipeline.private_failure_envelope_sha256
                ),
                "formal_blocker_sha256": blocker.blocker_sha256,
                "measured_result_count": 0,
                "action_authority": "NONE",
            }
            diagnosis_blocker = {
                **diagnosis_blocker,
                "blocker_sha256": semantic_sha256_v22(diagnosis_blocker),
            }
            terminal_artifacts.append(
                {
                    "path": "docs/analysis/product-v0233-diagnosis-blocker.json",
                    "mode": "CREATE_JSON",
                    "payload": diagnosis_blocker,
                }
            )
            terminal_artifacts.append(
                {
                    "path": "docs/analysis/product-v0233-diagnosis-blocker.md",
                    "mode": "CREATE_TEXT",
                    "payload": (
                        "# Product v0.2.3.3 Diagnosis Blocker\n\n"
                        "- Terminal: `BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE`\n"
                        f"- Job: `{pipeline.job_id}`\n"
                        f"- Safe error: `{pipeline.safe_error_code}`\n"
                        f"- Failure stage: `{pipeline.failure_stage}`\n"
                        "- Exception fingerprint: "
                        f"`{pipeline.exception_fingerprint}`\n"
                        f"- Journal tail SHA-256: `{pipeline.journal_tail_sha256}`\n"
                        "- Diagnosis retry: `NONE`\n"
                        "- Measured terminal: `NONE`\n"
                    ),
                }
            )
        blocked_manifest = _updated_manifest(
            root,
            phase=RepositoryPhaseV0233.FORMAL_BLOCKED.value,
            formal_blocker_sha256=blocker.blocker_sha256,
            cleanup_proof_sha256=closure_sha256,
            formal_clone_count=blocker.formal_clone_count,
            formal_execution_count=1,
            new_incident_count=blocker.new_incident_count,
            new_diagnosis_count=blocker.new_diagnosis_count,
            measured_result_count=0,
        )
        blocked_progress = _progress_payload(
            root,
            phase="INCREMENT_4_FORMAL_BLOCKED",
            current_terminal=blocker.terminal,
            next_gate="NONE",
            formal_clone_count=blocker.formal_clone_count,
            formal_execution_count=1,
            formal_transaction_count=(
                0 if execution is None else execution.run.completed_transactions
            ),
            new_incident_count=blocker.new_incident_count,
            new_diagnosis_count=blocker.new_diagnosis_count,
            measured_result_count=0,
            formal_blocker_sha256=blocker.blocker_sha256,
            cleanup_proof_sha256=closure_sha256,
            repository_state_manifest_sha256=blocked_manifest.manifest_sha256,
        )
        terminal_artifacts.extend(
            (
                {
                    "path": "config/product-v0233/repository-state-manifest.json",
                    "mode": "REPLACE_JSON",
                    "payload": blocked_manifest.model_dump(mode="json"),
                },
                {
                    "path": "docs/analysis/product-v0233-progress.json",
                    "mode": "REPLACE_JSON",
                    "payload": blocked_progress,
                },
            )
        )
        publication = _terminal_publication_bundle(
            reservation=reservation,
            kind="BLOCKER",
            terminal=blocker.terminal,
            artifacts=terminal_artifacts,
        )
        _persist_and_apply_terminal_publication(
            root=root,
            private_root=private_root,
            bundle=publication,
        )
        raise RuntimeError(blocker.terminal) from live_error

    assert result is not None
    assert clone is not None
    assert handoff is not None
    assert authority is not None
    assert restart is not None
    assert execution is not None
    assert traffic is not None
    assert fresh_snapshot_proof is not None
    assert incident_binding is not None
    assert diagnosis is not None
    assert evidence is not None
    assert index is not None
    assert decision_trace is not None
    assert assessment is not None
    assert pipeline is not None
    assert closure is not None
    assert closure_sha256 is not None
    public_outputs = (
        ("docs/analysis/product-v0233-runtime-authority.json", authority),
        ("docs/analysis/product-v0233-baseline-restart.json", restart),
        ("docs/analysis/product-v0233-formal-traffic.json", traffic),
        (
            "docs/analysis/product-v0233-fresh-runtime-snapshot.json",
            fresh_snapshot_proof,
        ),
        (
            "docs/analysis/product-v0233-incident-traffic-binding.json",
            incident_binding,
        ),
        ("docs/analysis/product-v0233-evidence-assessment.json", assessment),
        ("docs/results/product-v0233-nofault-acceptance.json", result),
    )
    measured_manifest = _updated_manifest(
        root,
        phase=RepositoryPhaseV0233.MEASURED_COMPLETE.value,
        formal_result_sha256=result.result_sha256,
        formal_blocker_sha256=None,
        knowledge_handoff_sha256=handoff["handoff_sha256"],
        cleanup_proof_sha256=closure_sha256,
        formal_clone_count=1,
        formal_execution_count=1,
        new_incident_count=1,
        new_diagnosis_count=1,
        measured_result_count=1,
    )
    measured_progress = _progress_payload(
        root,
        phase="INCREMENT_4_MEASURED_COMPLETE",
        current_terminal="ECOMSRE_PRODUCT_V0233_NOFAULT_ACCEPTANCE_COMPLETE",
        measured_terminal=result.measured_terminal,
        next_gate="ECOMSRE_PRODUCT_V0233_REPOSITORY_ACCEPTANCE_PASS",
        formal_clone_count=1,
        formal_execution_count=1,
        formal_transaction_count=30,
        new_incident_count=1,
        new_diagnosis_count=1,
        measured_result_count=1,
        formal_state_clone_sha256=clone.clone_sha256,
        runtime_authority_proof_sha256=authority.proof_sha256,
        baseline_restart_proof_sha256=restart.proof_sha256,
        formal_traffic_result_sha256=traffic.result_sha256,
        fresh_runtime_snapshot_sha256=(fresh_snapshot_proof.runtime_snapshot_sha256),
        diagnosis_pipeline_acceptance_sha256=pipeline.acceptance_sha256,
        nofault_result_sha256=result.result_sha256,
        knowledge_handoff_sha256=handoff["handoff_sha256"],
        cleanup_proof_sha256=closure_sha256,
        repository_state_manifest_sha256=measured_manifest.manifest_sha256,
    )
    reasons = "\n".join(f"- {reason}" for reason in result.reasons) or "- None"
    handoff_requirements = (
        "\n".join(f"- {item}" for item in handoff["repair_requirements"]) or "- None"
    )
    terminal_artifacts = [
        {
            "path": "docs/analysis/product-v0233-formal-state-clone.json",
            "mode": "CREATE_JSON",
            "payload": clone.model_dump(mode="json"),
        },
        {
            "path": "docs/analysis/product-v0233-formal-closure.json",
            "mode": "CREATE_JSON",
            "payload": closure,
        },
        *(
            {
                "path": locator,
                "mode": "CREATE_JSON",
                "payload": model.model_dump(mode="json"),
            }
            for locator, model in public_outputs
        ),
        {
            "path": "docs/analysis/product-v0233-diagnosis-stage-journal.json",
            "mode": "CREATE_JSON",
            "payload": _pipeline_public(pipeline),
        },
        {
            "path": "docs/analysis/product-v0233-knowledge-loop-handoff.json",
            "mode": "CREATE_JSON",
            "payload": handoff,
        },
        {
            "path": "docs/results/product-v0233-nofault-acceptance.md",
            "mode": "CREATE_TEXT",
            "payload": (
                "# Product v0.2.3.3 No-Fault Acceptance\n\n"
                f"- Measured terminal: `{result.measured_terminal}`\n"
                f"- Result SHA-256: `{result.result_sha256}`\n"
                "- Formal traffic: `30 / 30`, failures `0`, retries `0`\n"
                "- New Incident / Diagnosis: `1 / 1`\n"
                "- Fault / Knowledge / Agent / Runbook / Provider: `0 / 0 / 0 / 0 / 0`\n"
                "- Action authority: `NONE`\n"
                "- Cleanup: `CLEAN`\n\n"
                "## Reasons\n\n"
                f"{reasons}\n"
            ),
        },
        {
            "path": "docs/results/product-v0233-limitations.md",
            "mode": "CREATE_TEXT",
            "payload": (
                "# Product v0.2.3.3 Limitations\n\n"
                f"Measured terminal: `{result.measured_terminal}`.\n\n"
                f"{reasons}\n\n"
                "This campaign grants no Fault, Knowledge-Loop, Agent, Runbook, or Provider authority.\n"
            ),
        },
        {
            "path": "docs/results/product-v0233-interview-brief.md",
            "mode": "CREATE_TEXT",
            "payload": (
                "# Product v0.2.3.3 Interview Brief\n\n"
                "One fresh formal No-Fault campaign used a fresh Product-state clone, "
                "preserved Runtime authority, 30/30 healthy transactions, one new "
                "Incident, and one ordinary Diagnosis pipeline.\n\n"
                f"The evidence-bound measured terminal is `{result.measured_terminal}`. "
                "No fault, remediation, Agent write, Runbook execution, Provider call, "
                "or Knowledge-Loop campaign was authorized or executed.\n"
            ),
        },
        {
            "path": "docs/analysis/product-v0233-knowledge-loop-handoff.md",
            "mode": "CREATE_TEXT",
            "payload": (
                "# Product v0.2.3.3 Knowledge-Loop Handoff\n\n"
                f"- Terminal: `{handoff['terminal']}`\n"
                f"- Measured terminal: `{result.measured_terminal}`\n"
                "- Knowledge-Loop campaigns: `0`\n"
                "- Action authority: `NONE`\n\n"
                "## Repair requirements\n\n"
                f"{handoff_requirements}\n"
            ),
        },
        {
            "path": "config/product-v0233/repository-state-manifest.json",
            "mode": "REPLACE_JSON",
            "payload": measured_manifest.model_dump(mode="json"),
        },
        {
            "path": "docs/analysis/product-v0233-progress.json",
            "mode": "REPLACE_JSON",
            "payload": measured_progress,
        },
    ]
    publication = _terminal_publication_bundle(
        reservation=reservation,
        kind="MEASURED",
        terminal=result.measured_terminal,
        artifacts=terminal_artifacts,
    )
    _persist_and_apply_terminal_publication(
        root=root,
        private_root=private_root,
        bundle=publication,
    )
    return result


def _freeze_unbound_reservation_and_raise(
    root: Path,
    *,
    trigger: BaseException,
) -> NoReturn:
    try:
        _terminalize_consumed_reservation_v0233(root, trigger=trigger)
    except BaseException as terminalization_error:
        if (root / _PRIVATE_LOCATOR / "terminal-publication.json").is_file():
            _recover_terminal_publication(root)
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
        ) from terminalization_error
    raise RuntimeError(
        "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
    ) from trigger


def run_formal_nofault_v0233(
    *,
    project_root: Path,
) -> NoFaultAcceptanceResultV0233:
    """Run once, or complete only the terminal publication of consumed authority."""

    root = Path(project_root).resolve(strict=True)
    reservation_path = root / _RESERVATION_LOCATOR
    intent_path = root / _PRIVATE_LOCATOR / "terminal-publication.json"
    if reservation_path.exists():
        if intent_path.is_file():
            recovered = _recover_terminal_publication(root)
            assert recovered is not None
            return recovered
        _freeze_unbound_reservation_and_raise(
            root,
            trigger=RuntimeError("CONSUMED_RESERVATION_WITHOUT_TERMINAL_INTENT"),
        )
    try:
        return _run_formal_nofault_once_v0233(project_root=root)
    except BaseException as error:
        if reservation_path.exists():
            if intent_path.is_file():
                recovered = _recover_terminal_publication(root)
                assert recovered is not None
                return recovered
            _freeze_unbound_reservation_and_raise(root, trigger=error)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args(argv)
    result = run_formal_nofault_v0233(project_root=arguments.project_root)
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "run_formal_nofault_v0233",
    "strict_formal_admission_v0233",
)
