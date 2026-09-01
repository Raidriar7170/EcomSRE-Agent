from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    FORMAL_CONTRACT_PREFLIGHT_PASS_V0233,
    NOFAULT_FULLY_SUPPORTED_V0233,
    DiagnosisPipelineAcceptanceV0233,
    FormalIncidentDiagnosisCardinalityV0233,
    FreshFormalCampaignV0233,
    NoFaultAcceptanceResultV0233,
    admit_incident_creation_v0233,
    load_fresh_formal_campaign_v0233,
    load_fresh_traffic_profile_v0233,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from scripts.product_v0233.run_contract_preflight import run_contract_preflight


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CASES = (
    "01_FULLY_SUPPORTED_HEALTHY",
    "02_CAPABILITY_LIMITED_BOUND",
    "03_NOT_SUPPORTED_CORE_KNOWN",
    "04_NOT_SUPPORTED_OPEN_WORLD",
    "05_NOT_SUPPORTED_CONFLICTING",
    "06_NOT_SUPPORTED_STALE_RUNTIME",
    "07_NOT_SUPPORTED_MISSING_P01",
    "08_NOT_SUPPORTED_UNRESOLVED_EVIDENCE_REF",
    "09_FAILED_DIAGNOSIS_STAGE",
    "10_FORMAL_TRAFFIC_BLOCKER_BEFORE_INCIDENT",
    "11_SOURCE_CLONE_DELTA_VALIDATION",
    "12_REPOSITORY_PHASE_VALIDATION",
)


def _isolated_preflight_root(tmp_path: Path) -> Path:
    paths = (
        "config/product-v0233/campaign.json",
        "config/product-v0233/source-selection.json",
        "config/product-v0233/traffic/preflight-profile.json",
        "config/product-v0233/traffic/formal-profile.json",
        "docs/analysis/product-v0233-predecessor-audit.json",
        "docs/analysis/product-v0233-clone-contract.json",
        "docs/analysis/product-v0233-progress.json",
    )
    for locator in paths:
        source = ROOT / locator
        destination = tmp_path / locator
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def _sha(character: str) -> str:
    return character * 64


def test_campaign_and_new_traffic_profiles_are_exact_and_self_sealed() -> None:
    preflight = load_fresh_traffic_profile_v0233(ROOT, role="PREFLIGHT")
    formal = load_fresh_traffic_profile_v0233(ROOT, role="FORMAL")
    campaign = load_fresh_formal_campaign_v0233(ROOT)

    assert (
        preflight.role,
        preflight.profile_id,
        preflight.transactions,
        preflight.requests_per_second,
        preflight.request_seed,
        preflight.maximum_failures,
        preflight.stabilization_seconds,
        preflight.transport_retries_allowed_for_pass,
    ) == ("PREFLIGHT", "product-v0233-preflight", 10, 1.0, 23083301, 0, 30, 0)
    assert (
        formal.role,
        formal.profile_id,
        formal.transactions,
        formal.requests_per_second,
        formal.request_seed,
        formal.maximum_failures,
        formal.minimum_full_episode_duration_seconds,
        formal.queue_fault_flag,
        formal.transport_retries_allowed_for_pass,
    ) == ("FORMAL", "product-v0233-formal", 30, 1.0, 23083302, 0, 300, 0, 0)
    assert campaign.preflight_profile_sha256 == preflight.profile_sha256
    assert campaign.formal_profile_sha256 == formal.profile_sha256
    assert campaign.traffic_contract_sha256 == (
        "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
    )
    assert campaign.formal_execution_limit == 1
    assert campaign.incident_limit == 1
    assert campaign.diagnosis_limit == 1
    assert campaign.fault_attempt_limit == 0
    assert campaign.knowledge_loop_limit == 0
    assert campaign.action_authority == "NONE"
    assert campaign.campaign_sha256 == semantic_sha256_v22(
        campaign.model_dump(mode="json", exclude={"campaign_sha256"})
    )


def test_nofault_wrapper_maps_existing_terminal_without_changing_semantics() -> None:
    result = NoFaultAcceptanceResultV0233.build_from_v0232(
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_sha256=_sha("3"),
        runtime_authority_proof_sha256=_sha("4"),
        baseline_restart_proof_sha256=_sha("5"),
        traffic_preflight_sha256=_sha("6"),
        formal_traffic_execution_sha256=_sha("7"),
        fresh_runtime_snapshot_sha256=_sha("8"),
        incident_traffic_binding_sha256=_sha("9"),
        incident_sha256=_sha("a"),
        diagnosis_result_sha256=_sha("b"),
        evidence_bundle_sha256=_sha("c"),
        evidence_index_sha256=_sha("d"),
        decision_trace_sha256=_sha("e"),
        stage_journal_tail_sha256=_sha("f"),
        v0232_assessment_sha256=_sha("0"),
        v0232_measured_terminal="ECOMSRE_PRODUCT_V0232_NOFAULT_FULLY_SUPPORTED",
        reasons=(),
        safety_counters={
            "agent_writes": 0,
            "runbook_executions": 0,
            "provider_calls": 0,
            "fault_attempts": 0,
            "knowledge_loop_executions": 0,
        },
        cleanup_proof_sha256=_sha("1"),
    )
    assert result.measured_terminal == NOFAULT_FULLY_SUPPORTED_V0233
    assert result.reasons == ()
    assert result.result_sha256 == semantic_sha256_v22(
        result.model_dump(mode="json", exclude={"result_sha256"})
    )


def test_stage_journal_success_and_failure_acceptance_are_disjoint() -> None:
    success = DiagnosisPipelineAcceptanceV0233.build_success(
        job_id="job-" + "1" * 24,
        journal_tail_sha256=_sha("1"),
        event_count=54,
        diagnosis_result_sha256=_sha("2"),
        evidence_bundle_sha256=_sha("3"),
        evidence_index_sha256=_sha("4"),
        decision_trace_sha256=_sha("5"),
    )
    failure = DiagnosisPipelineAcceptanceV0233.build_failure(
        job_id="job-" + "2" * 24,
        journal_tail_sha256=_sha("6"),
        event_count=8,
        failure_stage="EVIDENCE_INDEX_STARTED",
        safe_error_code="INTERNAL_CONTRACT_FAILURE",
        exception_fingerprint=_sha("7"),
        private_failure_envelope_sha256=_sha("8"),
    )
    assert success.job_status == "SUCCEEDED"
    assert success.stage_journal_terminal == "JOB_SUCCEEDED"
    assert success.private_failure_envelope_sha256 is None
    assert failure.job_status == "FAILED"
    assert failure.stage_journal_terminal == "FAILED"
    assert failure.diagnosis_result_sha256 is None

    with pytest.raises(ValueError, match="pipeline acceptance"):
        DiagnosisPipelineAcceptanceV0233.model_validate(
            {
                **failure.model_dump(mode="json"),
                "job_status": "SUCCEEDED",
                "acceptance_sha256": _sha("0"),
            }
        )


def test_incident_creation_gate_fails_before_formal_traffic_pass() -> None:
    with pytest.raises(ValueError, match="FORMAL_TRAFFIC_NOT_PASS"):
        admit_incident_creation_v0233(
            runtime_authority_pass=True,
            baseline_restart_pass=True,
            formal_traffic_pass=False,
            fresh_runtime_snapshot_pass=True,
            new_incident_count=0,
            new_diagnosis_count=0,
        )


def test_formal_incident_diagnosis_cardinality_is_delta_bound() -> None:
    counts = {
        "source_incident_count": 1,
        "source_diagnosis_job_count": 1,
        "source_diagnosis_result_count": 1,
        "source_evidence_index_count": 0,
        "source_fault_family_count": 0,
        "source_knowledge_artifact_count": 0,
        "source_baseline_job_count": 1,
        "current_incident_count": 2,
        "current_diagnosis_job_count": 2,
        "current_diagnosis_result_count": 2,
        "current_evidence_index_count": 1,
        "current_fault_family_count": 0,
        "current_knowledge_artifact_count": 0,
        "current_baseline_job_count": 1,
    }
    accepted = FormalIncidentDiagnosisCardinalityV0233.build(
        phase="POST_DIAGNOSIS_SUCCEEDED", **counts
    )
    assert accepted.phase == "POST_DIAGNOSIS_SUCCEEDED"
    with pytest.raises(ValueError, match="formal cardinality"):
        FormalIncidentDiagnosisCardinalityV0233.build(
            phase="POST_DIAGNOSIS_SUCCEEDED",
            **{**counts, "current_baseline_job_count": 2},
        )


def test_complete_contract_preflight_uses_ordinary_worker_and_all_cases_pass(
    tmp_path: Path,
) -> None:
    report = run_contract_preflight(_isolated_preflight_root(tmp_path))
    assert report.terminal == FORMAL_CONTRACT_PREFLIGHT_PASS_V0233
    assert report.case_count == len(EXPECTED_CASES)
    assert report.passed_case_count == len(EXPECTED_CASES)
    assert tuple(case.case_id for case in report.cases) == EXPECTED_CASES
    assert all(case.passed for case in report.cases)
    assert report.fixture_pipeline.job_status == "SUCCEEDED"
    assert report.fixture_pipeline.stage_journal_terminal == "JOB_SUCCEEDED"
    assert report.fixture_pipeline.event_count == 54
    assert report.fixture_pipeline.private_failure_envelope_sha256 is None
    assert report.fixture_evidence_bundle_persisted is True
    assert report.fixture_evidence_index_persisted is True
    assert report.fixture_decision_trace_persisted is True
    assert report.fixture_scorer_expected_terminal is True
    assert report.action_authority == "NONE"
    assert report.formal_execution_count == 0
    assert report.new_incident_count == 0
    assert report.new_diagnosis_count == 0


def test_written_preflight_and_prepared_repository_manifest_are_exact(
    tmp_path: Path,
) -> None:
    isolated = _isolated_preflight_root(tmp_path)
    report = run_contract_preflight(isolated)
    written = json.loads(
        (
            isolated
            / "docs/analysis/product-v0233-formal-contract-preflight.json"
        ).read_text()
    )
    manifest = ProductV0233RepositoryStateManifest.model_validate_json(
        (
            isolated / "config/product-v0233/repository-state-manifest.json"
        ).read_bytes()
    )

    assert written == report.model_dump(mode="json")
    assert manifest.phase is RepositoryPhaseV0233.PREPARED
    assert manifest.contract_preflight_sha256 == report.preflight_sha256
    assert manifest.formal_clone_count == 0
    assert manifest.formal_execution_count == 0
    assert manifest.new_incident_count == 0
    assert manifest.new_diagnosis_count == 0
    assert manifest.measured_result_count == 0


def test_campaign_rejects_resealed_scorer_source_drift() -> None:
    campaign = load_fresh_formal_campaign_v0233(ROOT)
    payload = campaign.model_dump(mode="json")
    payload["nofault_scorer_source_sha256"] = _sha("0")
    body = {key: value for key, value in payload.items() if key != "campaign_sha256"}
    payload["campaign_sha256"] = semantic_sha256_v22(body)
    with pytest.raises(ValueError, match="campaign binding"):
        FreshFormalCampaignV0233.model_validate(payload)
