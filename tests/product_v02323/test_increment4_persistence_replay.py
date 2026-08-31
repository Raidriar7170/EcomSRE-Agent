from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.memory import BaselineProfileV22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import (
    BaselineBuildPolicyV1,
    BaselineRepositoryV1,
    EnvironmentBaselineV1,
)
from ecomsre.product.environment.capabilities import (
    CapabilityMatrixRepositoryV1,
    build_environment_capability_matrix,
)
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.contracts import IncidentCreateV1
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
)
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.jobs.contracts import ProductJobStatusV1, ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.diagnosis_replay_v02323 import (
    DiagnosisReplayContractErrorV02323,
    DiagnosisRootCauseDispositionV02323,
    build_frozen_replay_input_v02323,
    build_structural_acquisition_v02323,
    seal_tree_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    ReconstructionDispositionV02323,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.product_v02323.run_increment3_diagnosis_forensics import (
    _acquisition_payload,
)
from scripts.product_v02323.run_increment4_persistence_replay import (
    _build_attempts_manifest,
    _build_increment4_progress,
    _detect_persistence_commit,
    _stage_publication,
    _validate_replay_request,
    execute_persistence_replay_v02323,
    finalize_increment4_publication,
)
from scripts.ci.verify_product_v02323_increment4 import (
    _require_existing_files_unchanged,
    _require_inherited_rows_unchanged,
    _stable_database_payload,
)
import scripts.product_v02323.run_increment4_persistence_replay as increment4_runner


NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
REPLAY_ID = "replay-fedcba987654321001234567"


def _sealed(body: dict[str, object], field: str) -> dict[str, object]:
    return {**body, field: semantic_sha256_v22(body)}


def _fixture_state(product_root: Path):
    store = SqliteStoreV1(product_root / "product.sqlite3")
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    baselines = BaselineRepositoryV1(store)
    environment = environments.create(
        {
            "name": "schema9-replay-fixture",
            "connector_configs": [],
            "explicit_service_catalog": ["checkout"],
        },
        now=NOW.timestamp(),
    )
    identity = services.get_map(environment.environment_id)
    capability = build_environment_capability_matrix(
        environment_id=environment.environment_id,
        logical_services=("checkout",),
        connector_health=(),
        changes_available=False,
        verified_at=NOW,
    )
    capabilities.put(capability)
    policy = BaselineBuildPolicyV1()
    profile = BaselineProfileV22.build(
        metric_stats=(),
        trace_stats=(),
        resource_stats=(),
    )
    baseline_body: dict[str, object] = {
        "schema_version": "ecomsre.product.environment-baseline.v1",
        "baseline_id": "base-0123456789abcdef01234567",
        "environment_id": environment.environment_id,
        "service_ids": tuple(item.service_id for item in identity.services),
        "source_capability_sha256": capability.capability_sha256,
        "v22_baseline_profile": profile,
        "topology_edges": (),
        "normal_log_templates": (),
        "build_policy": policy,
        "window_count": policy.window_count,
        "successful_windows": policy.window_count,
        "built_at": NOW,
        "active": False,
    }
    draft = EnvironmentBaselineV1.model_construct(
        **baseline_body,
        baseline_sha256="0" * 64,
    )
    baseline = EnvironmentBaselineV1.model_validate(
        {
            **baseline_body,
            "baseline_sha256": semantic_sha256_v22(
                draft.model_dump(
                    mode="json",
                    exclude={"baseline_sha256", "active"},
                )
            ),
        }
    )
    baselines.put(baseline, activate=True)
    incidents = IncidentRepositoryV1(
        store,
        environments=environments,
        services=services,
        capabilities=capabilities,
        baselines=baselines,
    )
    incident = incidents.create(
        IncidentCreateV1(
            environment_id=environment.environment_id,
            external_incident_key="schema9-replay-fixture",
            alert_name="checkout unavailable",
            summary="Synthetic structural replay fixture.",
            started_at=NOW,
            ended_at=NOW,
            candidate_service_ids=(identity.services[0].service_id,),
        ),
        now=NOW.timestamp(),
    )
    jobs = JobRepositoryV1(store)
    original = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": incident.incident_id},
        idempotency_key="schema9-original-failure",
        now=NOW.timestamp() + 1,
    )
    claimed = jobs.claim_next(
        "fixture-worker",
        lease_seconds=60,
        now=NOW.timestamp() + 2,
    )
    assert claimed is not None and claimed.job_id == original.job_id
    original = jobs.fail(
        original.job_id,
        "fixture-worker",
        claimed.attempt_count,
        "INTERNAL_CONTRACT_FAILURE",
        now=NOW.timestamp() + 3,
    )
    return store, incident, baseline, original


def _contracts(incident, baseline, original):
    disposition_body: dict[str, object] = {
        "schema_version": "ecomsre.product.reconstruction-disposition.v02323",
        "goal_version": (
            "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
        ),
        "terminal": ("ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN"),
        "reconstruction_terminal": (
            "ECOMSRE_PRODUCT_V02323_PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS"
        ),
        "disposition": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "pristine_base_admission_sha256": "1" * 64,
        "formal_delta_sha256": "2" * 64,
        "schema9_contamination_audit_sha256": "3" * 64,
        "schema8_projection_export_sha256": "4" * 64,
        "reconstruction_sha256": "5" * 64,
        "post_formal_state_sha256": "6" * 64,
        "historical_raw_byte_authority": "LOST_RAW_BYTES_NOT_RECONSTRUCTED",
        "historical_logical_authority": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "replay_authority": "NOT_EXECUTED",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "raw_byte_equality_claimed": False,
        "diagnosis_persistence_replay_attempt_count": 0,
    }
    disposition = ReconstructionDispositionV02323.model_validate(
        _sealed(disposition_body, "disposition_sha256")
    )
    acquisition = build_structural_acquisition_v02323(
        incident=incident,
        baseline=baseline,
    )
    replay_input = build_frozen_replay_input_v02323(
        replay_id="replay-0123456789abcdef01234567",
        reconstruction_disposition_sha256=disposition.disposition_sha256,
        reconstruction_sha256=disposition.reconstruction_sha256,
        schema8_projection_sha256="7" * 64,
        incident=incident,
        original_failed_job_id=original.job_id,
        structural_input_sha256_by_kind={
            "acquisition": semantic_sha256_v22(_acquisition_payload(acquisition)),
        },
    )
    root_body: dict[str, object] = {
        "schema_version": "ecomsre.product.diagnosis-root-cause.v02323",
        "goal_version": (
            "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
        ),
        "terminal": "ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION_FROZEN",
        "disposition": "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN",
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
        "replay_input_sha256": replay_input.replay_input_sha256,
        "forensics_sha256": "8" * 64,
        "exact_original_acquisition_available": False,
        "deterministic_structural_defect_identified": False,
        "exact_original_failure_identity_claimed": False,
        "targeted_repair": "NOT_APPLICABLE",
        "bounded_reason": (
            "ORIGINAL_ACQUISITION_NOT_PERSISTED_AND_STRUCTURAL_PIPELINE_PASSED"
        ),
        "diagnosis_persistence_replay_attempt_count": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }
    root_cause = DiagnosisRootCauseDispositionV02323.model_validate(
        _sealed(root_body, "disposition_sha256")
    )
    return replay_input, disposition, root_cause


def test_single_persistence_replay_uses_real_pipeline_and_is_not_repeatable(
    tmp_path: Path,
) -> None:
    product_root = tmp_path / "product"
    store, incident, baseline, original = _fixture_state(product_root)
    replay_input, disposition, root_cause = _contracts(incident, baseline, original)

    result = execute_persistence_replay_v02323(
        product_root,
        replay_id=REPLAY_ID,
        replay_input=replay_input,
        reconstruction_disposition=disposition,
        root_cause=root_cause,
        observed_at=NOW,
    )
    committed, recovered_result_sha256 = _detect_persistence_commit(
        product_root,
        replay_id=REPLAY_ID,
        formal_incident_id=incident.incident_id,
    )

    assert result.terminal == "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS"
    assert committed is True
    assert recovered_result_sha256 == result.diagnosis_result_sha256
    assert result.formal_incident_id == incident.incident_id
    assert result.original_failed_job_id == original.job_id
    assert result.recovery_job_status == "SUCCEEDED"
    assert result.diagnosis_terminal == "INSUFFICIENT_EVIDENCE"
    assert result.stage_event_count == 54
    assert result.stage_journal_terminal == "JOB_SUCCEEDED"
    assert result.original_failed_job_sha256_before == (
        result.original_failed_job_sha256_after
    )
    assert result.diagnosis_count_after == result.diagnosis_count_before + 1
    assert result.evidence_index_count_after == result.evidence_index_count_before + 1
    assert result.evidence_object_count_after == result.evidence_object_count_before + 7
    assert result.evidence_link_count_after == result.evidence_link_count_before + 6
    assert result.job_count_after == result.job_count_before + 1
    object_store = ContentAddressedObjectStoreV1(
        product_root / "objects", metadata_store=store
    )
    assert object_store.read_bytes(result.decision_trace_object_sha256)
    assert (
        JobRepositoryV1(store).get(original.job_id).status is ProductJobStatusV1.FAILED
    )
    assert (
        DiagnosisRepositoryV1(
            store,
            object_store,
        )
        .get(incident.incident_id)
        .result_sha256
        == result.diagnosis_result_sha256
    )
    events = DiagnosisStageJournalRepositoryV02322(store).list_events(
        result.recovery_job_id
    )
    assert len(events) == 54

    with pytest.raises(DiagnosisReplayContractErrorV02323):
        execute_persistence_replay_v02323(
            product_root,
            replay_id="replay-aaaaaaaaaaaaaaaaaaaaaaaa",
            replay_input=replay_input,
            reconstruction_disposition=disposition,
            root_cause=root_cause,
            observed_at=NOW,
        )


def test_replay_request_rejects_the_frozen_increment3_replay_id() -> None:
    with pytest.raises(DiagnosisReplayContractErrorV02323):
        _validate_replay_request(
            replay_id="replay-0123456789abcdef01234567",
            frozen_replay_id="replay-0123456789abcdef01234567",
            observed_at=NOW,
        )


def test_sealed_publication_can_resume_without_replaying_diagnosis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_root = tmp_path / "fixture-product"
    _store, incident, baseline, original = _fixture_state(product_root)
    replay_input, disposition, root_cause = _contracts(incident, baseline, original)
    result = execute_persistence_replay_v02323(
        product_root,
        replay_id=REPLAY_ID,
        replay_input=replay_input,
        reconstruction_disposition=disposition,
        root_cause=root_cause,
        observed_at=NOW,
    )
    project = tmp_path / "project"
    progress_path = project / "docs/analysis/product-v02323-progress.json"
    progress_path.parent.mkdir(parents=True)
    progress_before = _sealed(
        {
            "increment": 3,
            "phase": "ROOT_CAUSE_DISPOSITION_FROZEN",
            "terminals": ["FROZEN"],
            "diagnosis_persistence_replay_attempt_count": 0,
            "next_gate": "INCREMENT_4_NO_DEFECT_PATH_AND_SINGLE_PERSISTENCE_REPLAY",
        },
        "progress_sha256",
    )
    progress_before_bytes = (
        json.dumps(progress_before, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    progress_path.write_bytes(progress_before_bytes)
    attempts = _build_attempts_manifest(result)
    progress = _build_increment4_progress(
        progress_before,
        result=result,
        attempts_sha256=attempts["attempts_sha256"],
    )
    attempt_root = (
        project
        / ".local/product-v02323/diagnosis-persistence-replay"
        / result.replay_id
    )
    attempt_root.mkdir(parents=True)
    _stage_publication(
        attempt_root,
        progress_before_bytes=progress_before_bytes,
        result=result,
        attempts=attempts,
        progress=progress,
    )
    seal_tree_v02323(attempt_root)

    original_write = increment4_runner._write_create_once
    injected = {"pending": True}

    def fail_once(path: Path, payload: bytes, *, replace: bool = False) -> None:
        if (
            injected["pending"]
            and path.name == "product-v02323-diagnosis-persistence-attempts.json"
            and "docs" in path.parts
        ):
            injected["pending"] = False
            temporary = path.with_name(f".{path.name}.v02323-increment3.tmp")
            temporary.write_bytes(payload)
            raise OSError("publication fault injection")
        original_write(path, payload, replace=replace)

    monkeypatch.setattr(increment4_runner, "_write_create_once", fail_once)
    with pytest.raises(OSError, match="publication fault injection"):
        finalize_increment4_publication(project, replay_id=result.replay_id)
    assert (
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
    ).is_file()
    assert not (
        project / "docs/analysis/product-v02323-diagnosis-persistence-attempts.json"
    ).exists()
    assert progress_path.read_bytes() == progress_before_bytes

    finalized = finalize_increment4_publication(project, replay_id=result.replay_id)
    assert finalized["result_sha256"] == result.result_sha256
    assert json.loads(progress_path.read_text())["increment"] == 4


@pytest.mark.parametrize(
    ("failure_stage", "expected_attempt_count", "expected_committed"),
    (("clone", 0, False), ("pipeline", 1, False), ("post_commit", 1, True)),
)
def test_attempt_failure_boundary_seals_clone_and_pipeline_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    expected_attempt_count: int,
    expected_committed: bool,
) -> None:
    fixture_root = tmp_path / "fixture"
    _store, incident, baseline, original = _fixture_state(fixture_root)
    replay_input, disposition, root_cause = _contracts(incident, baseline, original)
    project = tmp_path / "project"
    (project / "config/product-v02323/replay").mkdir(parents=True)
    (project / "docs/analysis").mkdir(parents=True)
    (project / "config/product-v02323/replay/replay-input.json").write_text("{}")
    (project / "docs/analysis/product-v02323-schema8-reconstruction.json").write_text(
        '{"reconstruction":{}}'
    )
    (
        project / "docs/analysis/product-v02323-reconstruction-disposition.json"
    ).write_text("{}")
    (project / "docs/analysis/product-v02323-diagnosis-root-cause.json").write_text(
        "{}"
    )
    progress = _sealed(
        {
            "increment": 3,
            "phase": "ROOT_CAUSE_DISPOSITION_FROZEN",
            "terminals": [],
            "diagnosis_persistence_replay_attempt_count": 0,
            "next_gate": "INCREMENT_4_NO_DEFECT_PATH_AND_SINGLE_PERSISTENCE_REPLAY",
        },
        "progress_sha256",
    )
    (project / "docs/analysis/product-v02323-progress.json").write_text(
        json.dumps(progress, sort_keys=True, separators=(",", ":")) + "\n"
    )
    monkeypatch.setattr(
        increment4_runner,
        "preflight_increment4",
        lambda *_args, **_kwargs: {
            "terminal": "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PERSISTENCE_PREFLIGHT_PASS",
            "progress_sha256_before": progress["progress_sha256"],
        },
    )
    monkeypatch.setattr(
        increment4_runner.FrozenIncidentReplayInputV02323,
        "model_validate_json",
        classmethod(lambda _cls, _value: replay_input),
    )
    monkeypatch.setattr(
        increment4_runner.Schema8ReconstructionV02323,
        "model_validate",
        classmethod(
            lambda _cls, _value: SimpleNamespace(
                reconstruction_locator="source-product"
            )
        ),
    )
    monkeypatch.setattr(
        increment4_runner.ReconstructionDispositionV02323,
        "model_validate_json",
        classmethod(lambda _cls, _value: disposition),
    )
    monkeypatch.setattr(
        increment4_runner.DiagnosisRootCauseDispositionV02323,
        "model_validate_json",
        classmethod(lambda _cls, _value: root_cause),
    )

    def injected_clone(_source: Path, destination: Path, *, applied_at: datetime):
        del applied_at
        if failure_stage == "clone":
            raise OSError("clone fault injection")
        destination.mkdir(parents=True)
        return {}

    monkeypatch.setattr(
        increment4_runner,
        "clone_and_apply_migration9_v02323",
        injected_clone,
    )
    if failure_stage in {"pipeline", "post_commit"}:
        monkeypatch.setattr(
            increment4_runner,
            "execute_persistence_replay_v02323",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("pipeline fault injection")
            ),
        )
    if failure_stage == "post_commit":
        monkeypatch.setattr(
            increment4_runner,
            "_detect_persistence_commit",
            lambda *_args, **_kwargs: (True, "a" * 64),
        )

    with pytest.raises((OSError, RuntimeError), match="fault injection"):
        increment4_runner.run_increment4(
            project,
            source_root=project,
            pristine_root=project,
            formal_private_root=project,
            replay_id=REPLAY_ID,
            observed_at=NOW,
        )
    attempt_root = (
        project / ".local/product-v02323/diagnosis-persistence-replay" / REPLAY_ID
    )
    assert increment4_runner.is_read_only_tree_v02323(attempt_root)
    failure = json.loads((attempt_root / "replay-failure.json").read_text())
    assert failure["diagnosis_persistence_replay_attempt_count"] == (
        expected_attempt_count
    )
    assert failure["persistence_attempt_started"] is bool(expected_attempt_count)
    assert failure["persistence_committed"] is expected_committed
    assert failure["result_sha256"] == ("a" * 64 if expected_committed else None)


def test_verifier_helpers_reject_stable_row_and_existing_object_drift(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_store, incident, baseline, original = _fixture_state(source_root)
    replay_input, disposition, root_cause = _contracts(incident, baseline, original)
    execute_persistence_replay_v02323(
        source_root,
        replay_id=REPLAY_ID,
        replay_input=replay_input,
        reconstruction_disposition=disposition,
        root_cause=root_cause,
        observed_at=NOW,
    )
    source_database = source_store.path
    replay_database = tmp_path / "replay.sqlite3"
    with (
        sqlite3.connect(source_database) as source,
        sqlite3.connect(replay_database) as replay,
    ):
        source.backup(replay)
    assert _stable_database_payload(source_database) == _stable_database_payload(
        replay_database
    )
    with sqlite3.connect(replay_database) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("UPDATE environments SET name = 'tampered'")
    assert _stable_database_payload(source_database) != _stable_database_payload(
        replay_database
    )
    _require_inherited_rows_unchanged(
        source_database,
        replay_database,
        table="diagnosis_results",
    )
    with sqlite3.connect(replay_database) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("UPDATE diagnosis_results SET payload_json = '{}'")
    with pytest.raises(ValueError, match="inherited diagnosis_results rows differ"):
        _require_inherited_rows_unchanged(
            source_database,
            replay_database,
            table="diagnosis_results",
        )

    source_objects = tmp_path / "source-objects"
    replay_objects = tmp_path / "replay-objects"
    source_objects.mkdir()
    replay_objects.mkdir()
    (source_objects / "existing.json").write_bytes(b"original")
    (replay_objects / "existing.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="inherited object bytes differ"):
        _require_existing_files_unchanged(source_objects, replay_objects)
