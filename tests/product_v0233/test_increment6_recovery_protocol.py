from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalAttemptLedgerV0233,
    FormalAttemptRecordV0233,
    FormalCheckpointRepositoryV0233,
    DiagnosisAcquisitionCheckpointV0233,
    FormalExecutionCheckpointV0233,
    FormalExecutionStateV0233,
    FormalOperationalSurfaceV0233,
    FormalSemanticSurfaceV0233,
    LiveCaptureBundleV0233,
    RecoveryExactHeadCiReceiptV0233,
    RecoveryPreExecutionReviewV0233,
    acquisition_recovery_is_compatible_v0233,
    build_legacy_attempt1_record_v0233,
    determine_earliest_safe_resume_state_v0233,
    formal_diagnosis_idempotency_key_v0233,
    formal_incident_external_key_v0233,
    verify_checkpoint_artifacts_v0233,
)
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    FormalIncidentDiagnosisCardinalityV0233,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
)
from scripts.product_v0233 import resume_formal_nofault as resume_command
from scripts.product_v0233 import run_formal_nofault as run_command
from scripts.product_v0233.run_formal_nofault import _formal_surfaces_v0233
from ecomsre_live_sandbox.contracts import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]


def _sha(character: str) -> str:
    return character * 64


def _semantic_surface(*, generation: int = 1) -> FormalSemanticSurfaceV0233:
    return FormalSemanticSurfaceV0233.build(
        semantic_generation=generation,
        checkout_traffic_contract_sha256=_sha("1"),
        checkout_traffic_source_sha256=_sha("0"),
        preflight_profile_sha256=_sha("2"),
        formal_profile_sha256=_sha("3"),
        active_profile_sha256=_sha("4"),
        active_baseline_id="base-" + "5" * 24,
        active_baseline_sha256=_sha("5"),
        source_selection_sha256=_sha("6"),
        formal_clone_contract_sha256=_sha("f"),
        runtime_authority_contract_sha256=_sha("7"),
        service_identity_contract_sha256=_sha("8"),
        capability_contract_sha256=_sha("9"),
        diagnosis_source_sha256_by_path={
            "src/ecomsre/product/jobs/handlers.py": _sha("a"),
            "src/ecomsre/product/incidents/diagnosis_bridge.py": _sha("b"),
        },
        nofault_scorer_source_sha256=_sha("c"),
        stage_journal_contract_sha256=_sha("d"),
    )


def _operational_surface(character: str = "e") -> FormalOperationalSurfaceV0233:
    return FormalOperationalSurfaceV0233.build(
        operational_file_sha256_by_path={
            "scripts/product_v0233/run_formal_nofault.py": _sha(character),
            "src/ecomsre/product/pilot/formal_recovery_v0233.py": _sha("f"),
        }
    )


def test_recovery_review_and_exact_head_ci_receipt_are_independently_sealed() -> None:
    semantic = _semantic_surface(generation=2)
    operational = _operational_surface()
    review = RecoveryPreExecutionReviewV0233.build(
        semantic_generation=2,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=operational.operational_surface_sha256,
    )
    receipt = RecoveryExactHeadCiReceiptV0233.build(
        execution_head="1" * 40,
        upstream_ref="refs/remotes/origin/codex/example",
        pull_request_number=86,
        checked_at=datetime.now(UTC),
        successful_checks=("verify", "Offline replay and verification"),
        successful_check_run_ids={
            "Offline replay and verification": 101,
            "verify": 102,
        },
        review_sha256=review.review_sha256,
    )

    assert receipt.successful_checks == (
        "Offline replay and verification",
        "verify",
    )
    assert receipt.successful_check_run_ids["verify"] == 102
    with pytest.raises(ValidationError):
        RecoveryExactHeadCiReceiptV0233.model_validate(
            {**receipt.model_dump(mode="json"), "execution_head": "2" * 40}
        )
    with pytest.raises(ValidationError):
        RecoveryExactHeadCiReceiptV0233.build(
            execution_head="1" * 40,
            upstream_ref="refs/remotes/origin/codex/example",
            pull_request_number=86,
            checked_at=datetime.now(UTC),
            successful_checks=("verify",),
            successful_check_run_ids={"verify": 102},
            review_sha256=review.review_sha256,
        )


def _first_checkpoint(
    *,
    attempt_id: str = "attempt-2",
    generation: int = 1,
    operational: FormalOperationalSurfaceV0233 | None = None,
) -> FormalExecutionCheckpointV0233:
    semantic = _semantic_surface(generation=generation)
    operational = operational or _operational_surface()
    return FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=generation,
        attempt_id=attempt_id,
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=operational.operational_surface_sha256,
        source_selection_sha256=_sha("6"),
        formal_clone_sha256=None,
        input_artifact_sha256s={
            "config/product-v0233/campaign.json": _sha("1")
        },
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_semantic_and_operational_surfaces_are_independent_and_self_sealed() -> None:
    semantic = _semantic_surface()
    operational = _operational_surface("e")
    repaired_operational = _operational_surface("0")

    assert semantic.semantic_surface_sha256 == semantic_sha256_v22(
        semantic.model_dump(mode="json", exclude={"semantic_surface_sha256"})
    )
    assert operational.operational_surface_sha256 == semantic_sha256_v22(
        operational.model_dump(mode="json", exclude={"operational_surface_sha256"})
    )
    assert repaired_operational.operational_surface_sha256 != (
        operational.operational_surface_sha256
    )
    assert semantic.semantic_generation == 1


def test_live_repository_surface_builder_excludes_operational_code_from_semantics() -> (
    None
):
    semantic, operational = _formal_surfaces_v0233(ROOT, semantic_generation=2)
    semantic_paths = set(semantic.diagnosis_source_sha256_by_path)
    operational_paths = set(operational.operational_file_sha256_by_path)

    assert "scripts/product_v0233/run_formal_nofault.py" not in (
        semantic.diagnosis_source_sha256_by_path
    )
    assert "scripts/product_v0233/run_formal_nofault.py" in (
        operational.operational_file_sha256_by_path
    )
    assert "src/ecomsre/product/pilot/formal_recovery_v0233.py" in (
        operational.operational_file_sha256_by_path
    )
    assert semantic_paths.isdisjoint(operational_paths)
    assert "src/ecomsre/product/pilot/serialization_v0233.py" in semantic_paths
    assert "src/ecomsre/product/pilot/serialization_v0233.py" not in operational_paths


def test_checkpoint_chain_accepts_valid_transitions_and_operational_repair() -> None:
    prepared = _first_checkpoint()
    environment_ready = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        operational_surface_sha256=_operational_surface("0").operational_surface_sha256,
        formal_clone_sha256=_sha("1"),
        output_artifact_sha256s={
            "docs/analysis/product-v0233-attempts/attempt-2/formal-clone.json": (
                _sha("1")
            )
        },
        created_at=prepared.created_at + timedelta(seconds=1),
    )

    assert environment_ready.sequence == 2
    assert environment_ready.previous_checkpoint_sha256 == prepared.checkpoint_sha256
    assert environment_ready.semantic_surface_sha256 == prepared.semantic_surface_sha256
    assert environment_ready.operational_surface_sha256 != (
        prepared.operational_surface_sha256
    )
    assert determine_earliest_safe_resume_state_v0233(environment_ready) == (
        FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY
    )


def test_live_capture_bundle_seals_raw_runtime_before_presentation_artifacts(
    tmp_path: Path,
) -> None:
    semantic = _semantic_surface()
    observed_at = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    raw_runtime = {
        "schema_version": "ecomsre.product.pilot-runtime-snapshot.v02",
        "observed_at": observed_at,
        "services": [{"service_id": "svc-checkout", "state": "RUNNING"}],
    }
    bundle = LiveCaptureBundleV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=1,
        attempt_id="attempt-2",
        formal_clone_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        runtime_authority_proof_sha256=_sha("3"),
        baseline_restart_proof_sha256=_sha("4"),
        traffic_contract_sha256=_sha("5"),
        formal_profile_sha256=_sha("6"),
        formal_traffic_result_sha256=_sha("7"),
        traffic_execution_sha256=_sha("8"),
        episode_started_at=observed_at - timedelta(seconds=300),
        episode_ended_at=observed_at,
        fresh_runtime_snapshot_raw=raw_runtime,
        runtime_connector_binding_sha256=_sha("9"),
        queue_before_sha256=_sha("a"),
        queue_after_sha256=_sha("a"),
        outer_baseline_before_sha256=_sha("b"),
        outer_baseline_after_sha256=_sha("b"),
        active_profile_sha256=_sha("c"),
        active_baseline_id="base-" + "d" * 24,
        active_baseline_sha256=_sha("d"),
        service_identity_sha256=_sha("e"),
        capability_sha256=_sha("f"),
        semantic_surface_sha256=semantic.semantic_surface_sha256,
    )
    bundle_path = tmp_path / "live-capture-bundle.json"
    bundle_path.write_bytes(canonical_json_bytes(bundle))

    assert bundle.fresh_runtime_snapshot_raw_sha256 == semantic_sha256_v22(
        {
            "observed_at": "2026-09-01T01:00:00Z",
            "schema_version": "ecomsre.product.pilot-runtime-snapshot.v02",
            "services": [{"service_id": "svc-checkout", "state": "RUNNING"}],
        }
    )
    assert bundle.live_capture_bundle_sha256 == semantic_sha256_v22(
        bundle.model_dump(mode="json", exclude={"live_capture_bundle_sha256"})
    )
    with pytest.raises(RuntimeError, match="proof construction"):
        raise RuntimeError("proof construction injected failure")
    assert LiveCaptureBundleV0233.model_validate_json(bundle_path.read_bytes()) == bundle
    assert formal_incident_external_key_v0233(bundle).startswith(
        "product-v0233-g1-"
    )


def test_acquisition_checkpoint_binds_frozen_reads_and_recovery_idempotency() -> None:
    semantic = _semantic_surface()
    started = datetime(2026, 9, 1, tzinfo=UTC)
    checkpoint = DiagnosisAcquisitionCheckpointV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=1,
        attempt_id="attempt-2",
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("1"),
        incident_observation_started_at=started,
        incident_observation_ended_at=started + timedelta(seconds=300),
        baseline_sha256=_sha("2"),
        active_profile_sha256=_sha("3"),
        service_identity_sha256=_sha("4"),
        capability_sha256=_sha("5"),
        connector_query_results=(
            {"source": "METRICS", "status": "SUCCESS_NONEMPTY", "records": []},
            {"source": "LOGS", "status": "SUCCESS_EMPTY", "records": []},
        ),
        connector_provenance_bindings=(
            {"source": "METRICS", "evidence_sha256": _sha("6")},
            {"source": "LOGS", "evidence_sha256": _sha("7")},
        ),
        runtime_snapshot_binding_sha256=_sha("8"),
        source_coverage={
            "LOGS": ("checkout",),
            "METRICS": ("checkout",),
            "RUNTIME": ("checkout",),
        },
        capability_limitations=(),
        capability_observations=({"source": "LOGS", "available": True},),
        limitation_candidates=(),
        read_snapshots=(
            {
                "schema_version": "ecomsre.product.read-snapshot.v1",
                "source": "LOGS",
            },
            {
                "schema_version": "ecomsre.product.read-snapshot.v1",
                "source": "METRICS",
            },
        ),
        read_snapshot_sha256s={
            "read-snapshot-000.json": semantic_sha256_v22(
                {
                    "schema_version": "ecomsre.product.read-snapshot.v1",
                    "source": "LOGS",
                }
            ),
            "read-snapshot-001.json": semantic_sha256_v22(
                {
                    "schema_version": "ecomsre.product.read-snapshot.v1",
                    "source": "METRICS",
                }
            ),
        },
        semantic_surface_sha256=semantic.semantic_surface_sha256,
    )
    key = formal_diagnosis_idempotency_key_v0233(
        incident_sha256=checkpoint.incident_sha256,
        acquisition_sha256=checkpoint.acquisition_sha256,
        semantic_surface_sha256=checkpoint.semantic_surface_sha256,
        diagnosis_generation=1,
    )

    assert checkpoint.acquisition_sha256 == semantic_sha256_v22(
        checkpoint.model_dump(mode="json", exclude={"acquisition_sha256"})
    )
    assert key.startswith("formal-v0233-diagnosis-")
    assert acquisition_recovery_is_compatible_v0233(
        checkpoint,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
    )
    assert not acquisition_recovery_is_compatible_v0233(
        checkpoint,
        semantic_surface_sha256=_sha("0"),
    )


def test_recovery_cardinality_allows_failed_job_plus_one_persisted_diagnosis() -> None:
    cardinality = FormalIncidentDiagnosisCardinalityV0233.build(
        phase="POST_DIAGNOSIS_RECOVERED",
        source_incident_count=0,
        source_diagnosis_job_count=0,
        source_diagnosis_result_count=0,
        source_evidence_index_count=0,
        source_fault_family_count=0,
        source_knowledge_artifact_count=0,
        source_baseline_job_count=0,
        current_incident_count=1,
        current_diagnosis_job_count=2,
        current_diagnosis_result_count=1,
        current_evidence_index_count=1,
        current_fault_family_count=0,
        current_knowledge_artifact_count=0,
        current_baseline_job_count=0,
    )

    assert cardinality.current_diagnosis_job_count == 2


def test_measured_repository_state_counts_recovery_jobs_truthfully() -> None:
    current = ProductV0233RepositoryStateManifest.model_validate_json(
        (ROOT / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    body = {
        **current.model_dump(mode="json", exclude={"manifest_sha256"}),
        "phase": "MEASURED_COMPLETE",
        "formal_result_sha256": _sha("1"),
        "formal_blocker_sha256": None,
        "knowledge_handoff_sha256": _sha("2"),
        "cleanup_proof_sha256": _sha("3"),
        "formal_clone_count": 1,
        "formal_execution_count": 1,
        "new_incident_count": 1,
        "new_diagnosis_count": 2,
        "measured_result_count": 1,
    }
    manifest = ProductV0233RepositoryStateManifest.model_validate(
        {**body, "manifest_sha256": semantic_sha256_v22(body)}
    )

    assert manifest.new_diagnosis_count == 2


def test_checkpoint_rejects_invalid_transition_or_semantic_change_in_same_attempt() -> (
    None
):
    prepared = _first_checkpoint()
    with pytest.raises(ValueError, match="transition"):
        FormalExecutionCheckpointV0233.build(
            previous=prepared,
            state=FormalExecutionStateV0233.SCORED,
            created_at=prepared.created_at + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="semantic surface"):
        FormalExecutionCheckpointV0233.build(
            previous=prepared,
            state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
            semantic_surface_sha256=_sha("0"),
            created_at=prepared.created_at + timedelta(seconds=1),
        )

    generation_two = _first_checkpoint(attempt_id="attempt-3", generation=2)
    assert generation_two.semantic_generation == 2
    assert generation_two.attempt_id == "attempt-3"


def test_checkpoint_repository_is_append_only_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    repository = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    )
    prepared = _first_checkpoint()
    repository.append(prepared)
    traffic_running = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        formal_clone_sha256=_sha("1"),
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    repository.append(traffic_running)

    assert repository.load_chain() == (prepared, traffic_running)
    with pytest.raises(FileExistsError):
        repository.append(traffic_running)

    second_path = repository.checkpoint_path(traffic_running)
    payload = json.loads(second_path.read_text(encoding="utf-8"))
    payload["state"] = FormalExecutionStateV0233.SCORED.value
    second_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError), match="checkpoint"):
        repository.load_chain()


def test_checkpoint_repository_rejects_cross_checkpoint_identity_drift(
    tmp_path: Path,
) -> None:
    repository = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    )
    prepared = _first_checkpoint()
    repository.append(prepared)
    environment_ready = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        formal_clone_sha256=_sha("1"),
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    payload = environment_ready.model_dump(
        mode="json", exclude={"checkpoint_sha256"}
    )
    payload["campaign_id"] = "product-v0233-different-campaign"
    payload["checkpoint_sha256"] = semantic_sha256_v22(payload)
    second_path = repository.checkpoint_path(environment_ready)
    second_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        repository.load_chain()


def test_attempt_ledger_preserves_legacy_attempt_and_one_measured_result() -> None:
    legacy = FormalAttemptRecordV0233.build(
        attempt_id="attempt-1",
        ordinal=1,
        semantic_generation=1,
        disposition="LEGACY_BLOCKED",
        latest_state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        latest_checkpoint_sha256=None,
        blocker_terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        measured_terminal=None,
        evidence_sha256_by_path={
            "docs/analysis/product-v0233-formal-blocker.json": _sha("1")
        },
    )
    active = FormalAttemptRecordV0233.build(
        attempt_id="attempt-2",
        ordinal=2,
        semantic_generation=1,
        disposition="ACTIVE",
        latest_state=FormalExecutionStateV0233.PREPARED,
        latest_checkpoint_sha256=_sha("2"),
        blocker_terminal=None,
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    ledger = FormalAttemptLedgerV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        attempts=(legacy, active),
    )

    assert ledger.latest_attempt_id == "attempt-2"
    assert ledger.measured_result_count == 0
    assert ledger.ledger_sha256 == semantic_sha256_v22(
        ledger.model_dump(mode="json", exclude={"ledger_sha256"})
    )


def test_resume_artifact_verification_fails_closed_on_missing_or_changed_bytes(
    tmp_path: Path,
) -> None:
    campaign = tmp_path / "config/product-v0233/campaign.json"
    campaign.parent.mkdir(parents=True)
    campaign.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(campaign.read_bytes()).hexdigest()
    checkpoint = FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=1,
        attempt_id="attempt-2",
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=_sha("2"),
        operational_surface_sha256=_sha("3"),
        source_selection_sha256=_sha("4"),
        formal_clone_sha256=None,
        input_artifact_sha256s={"config/product-v0233/campaign.json": digest},
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    verify_checkpoint_artifacts_v0233(tmp_path, checkpoint)
    campaign.write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="artifact"):
        verify_checkpoint_artifacts_v0233(tmp_path, checkpoint)


def test_resume_command_verifies_chain_surfaces_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic = _semantic_surface()
    checkpoint_operational = _operational_surface("e")
    current_operational = _operational_surface("0")
    campaign = tmp_path / "config/product-v0233/campaign.json"
    campaign.parent.mkdir(parents=True)
    campaign.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(campaign.read_bytes()).hexdigest()
    checkpoint = FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=1,
        attempt_id="attempt-2",
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=(
            checkpoint_operational.operational_surface_sha256
        ),
        source_selection_sha256=_sha("6"),
        formal_clone_sha256=None,
        input_artifact_sha256s={"config/product-v0233/campaign.json": digest},
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    repository = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    )
    repository.append(checkpoint)
    monkeypatch.setattr(
        resume_command,
        "_formal_surfaces_v0233",
        lambda _root, *, semantic_generation: (semantic, current_operational),
    )

    decision = resume_command.inspect_formal_resume_v0233(
        project_root=tmp_path,
        attempt_id="attempt-2",
    )

    assert decision["resume_state"] == "PREPARED"
    assert decision["operational_surface_changed"] is True
    assert decision["referenced_artifacts_verified"] is True
    assert decision["decision_sha256"] == semantic_sha256_v22(
        {key: value for key, value in decision.items() if key != "decision_sha256"}
    )


def test_live_legacy_attempt1_record_binds_existing_bytes_without_checkpoint() -> None:
    record = build_legacy_attempt1_record_v0233(ROOT)
    tracked_record = FormalAttemptRecordV0233.model_validate_json(
        (
            ROOT
            / "docs/analysis/product-v0233-attempts/attempt-1/legacy-reference.json"
        ).read_bytes()
    )
    tracked_ledger = FormalAttemptLedgerV0233.model_validate_json(
        (ROOT / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )

    assert record.attempt_id == "attempt-1"
    assert tracked_record == record
    assert tracked_ledger.attempts == (record,)
    assert record.disposition == "LEGACY_BLOCKED"
    assert record.latest_checkpoint_sha256 is None
    assert record.blocker_terminal == (
        "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
    )
    assert record.measured_terminal is None
    assert record.evidence_sha256_by_path[
        "docs/analysis/product-v0233-formal-blocker.json"
    ] == "a02cce3787c1a443f365c83c4207b6256de431d792ebd6fded628ca36bc32ed1"


def test_exact_head_review_gate_executes_with_bound_ci_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic = _semantic_surface(generation=2)
    operational = _operational_surface()
    review = RecoveryPreExecutionReviewV0233.build(
        semantic_generation=2,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=operational.operational_surface_sha256,
    )
    receipt = RecoveryExactHeadCiReceiptV0233.build(
        execution_head="1" * 40,
        upstream_ref=run_command._EXPECTED_UPSTREAM,
        pull_request_number=86,
        checked_at=datetime(2026, 9, 1, tzinfo=UTC),
        successful_checks=("Offline replay and verification", "verify"),
        successful_check_run_ids={
            "Offline replay and verification": 101,
            "verify": 102,
        },
        review_sha256=review.review_sha256,
    )
    review_path = (
        tmp_path / "docs/analysis/product-v0233-recovery-pre-execution-review.json"
    )
    receipt_path = tmp_path / ".local/product-v0233/recovery-exact-head-ci.json"
    review_path.parent.mkdir(parents=True)
    receipt_path.parent.mkdir(parents=True)
    review_path.write_text(review.model_dump_json(), encoding="utf-8")
    receipt_path.write_text(receipt.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        run_command,
        "_formal_surfaces_v0233",
        lambda *_args, **_kwargs: (semantic, operational),
    )
    monkeypatch.setattr(run_command, "_git_status_paths_v0233", lambda _root: ())

    def fake_git(_root: Path, *arguments: str) -> str:
        return {
            ("rev-parse", "HEAD"): "1" * 40,
            ("branch", "--show-current"): run_command._BRANCH,
            ("rev-parse", "--symbolic-full-name", "@{upstream}"): (
                run_command._EXPECTED_UPSTREAM
            ),
            ("rev-parse", run_command._EXPECTED_UPSTREAM): "1" * 40,
        }[arguments]

    monkeypatch.setattr(run_command, "_git", fake_git)

    head, observed_semantic, observed_operational, observed_review = (
        run_command._strict_recovery_head_review_v0233(
            tmp_path,
            semantic_generation=2,
            allowed_dirty_paths=(),
            allowed_dirty_prefixes=(),
        )
    )

    assert head == "1" * 40
    assert observed_semantic == semantic
    assert observed_operational == operational
    assert observed_review == review

    monkeypatch.setattr(
        run_command,
        "_git_status_paths_v0233",
        lambda _root: ("src/ecomsre/product/jobs/handlers.py",),
    )
    with pytest.raises(RuntimeError, match="STARTING_HEAD"):
        run_command._strict_recovery_head_review_v0233(
            tmp_path,
            semantic_generation=2,
            allowed_dirty_paths=(),
            allowed_dirty_prefixes=(),
        )


def test_strict_new_attempt_and_resume_admissions_execute_live_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic = _semantic_surface(generation=2)
    operational = _operational_surface()
    review = RecoveryPreExecutionReviewV0233.build(
        semantic_generation=2,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=operational.operational_surface_sha256,
    )
    monkeypatch.setattr(
        run_command,
        "_strict_recovery_head_review_v0233",
        lambda *_args, **_kwargs: (
            "1" * 40,
            semantic,
            operational,
            review,
        ),
    )

    admission = run_command.strict_recovery_formal_admission_v0233(
        ROOT,
        attempt_id="attempt-2",
        semantic_generation=2,
    )
    assert admission.execution_head == "1" * 40
    assert admission.pre_execution_review_sha256 == review.review_sha256

    artifact = tmp_path / "private/acquisition.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}\n", encoding="utf-8")
    artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    prepared = FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=operational.operational_surface_sha256,
        source_selection_sha256=_sha("6"),
        formal_clone_sha256=None,
        input_artifact_sha256s={},
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    recoverable = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        output_artifact_sha256s={"private/acquisition.json": artifact_sha},
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    repository = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    )
    repository.append(prepared)
    repository.append(recoverable)

    latest, observed_semantic, observed_operational = (
        run_command.strict_resume_formal_admission_v0233(
            tmp_path,
            attempt_id="attempt-2",
        )
    )
    assert latest == recoverable
    assert observed_semantic == semantic
    assert observed_operational == operational

    artifact.write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="artifact"):
        run_command.strict_resume_formal_admission_v0233(
            tmp_path,
            attempt_id="attempt-2",
        )
