from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    NoFaultAcceptanceResultV0233,
    admit_incident_creation_v0233,
    load_fresh_traffic_profile_v0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalSourceSelectionV0233,
)
from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
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
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficRunnerV0232,
    IncidentTrafficBindingV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from ecomsre.product.pilot.serialization_v0233 import semantic_json_sha256_v0233
from scripts.product_v0233 import run_formal_nofault as formal_runner
from scripts.product_v0233.run_formal_nofault import (
    _frozen_semantic_surface_sha256_v0233,
    _persist_and_apply_terminal_publication,
    _terminal_publication_bundle,
    strict_formal_admission_v0233,
)


ROOT = Path(__file__).resolve().parents[2]


def _sha(character: str) -> str:
    return character * 64


def _healthy_checkout(request: httpx.Request) -> httpx.Response:
    payload = __import__("json").loads(request.content)
    if request.url.path == "/api/cart":
        return httpx.Response(
            200,
            json={
                "userId": payload["userId"],
                "items": [{"productId": "0PUK6V6EV0", "quantity": 1}],
            },
        )
    return httpx.Response(
        200,
        json={
            "orderId": "order-fixture",
            "shippingTrackingId": "tracking-fixture",
            "shippingCost": {"currencyCode": "USD", "units": 1, "nanos": 0},
            "shippingAddress": {
                "streetAddress": "1 Contract Way",
                "city": "Local",
                "state": "CA",
                "country": "United States",
                "zipCode": "94016",
            },
            "items": [
                {
                    "item": {
                        "productId": "0PUK6V6EV0",
                        "quantity": 1,
                        "product": {"id": "0PUK6V6EV0"},
                    },
                    "cost": {"currencyCode": "USD", "units": 1, "nanos": 0},
                }
            ],
        },
    )


def _successful_execution():
    profile = load_fresh_traffic_profile_v0233(ROOT, role="FORMAL")
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(_healthy_checkout),
        sleep=lambda _seconds: None,
    ) as runner:
        return runner.run(
            endpoint="http://127.0.0.1:18080/api/checkout",
            profile=profile.engine_profile_v0232(),
            contract=load_checkout_traffic_contract_v0232(ROOT),
            role="FORMAL",
        )


def _admission() -> FormalExecutionAdmissionV0233:
    return FormalExecutionAdmissionV0233.build(
        execution_head="1" * 40,
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_plan_sha256=_sha("3"),
        formal_contract_freeze_sha256=_sha("4"),
        pre_execution_review_sha256=_sha("5"),
        repository_state_manifest_sha256=_sha("6"),
    )


def _counts(**updates: int) -> FormalObservedStateCountsV0233:
    payload = {
        "baseline_count": 1,
        "active_baseline_count": 1,
        "baseline_job_count": 1,
        "verify_job_count": 1,
        "diagnosis_job_count": 1,
        "incident_count": 1,
        "diagnosis_count": 1,
        "evidence_object_count": 1,
        "diagnosis_evidence_index_count": 1,
        "diagnosis_stage_event_count": 0,
        "fault_family_count": 0,
        "knowledge_artifact_count": 0,
        "pending_job_count": 0,
        "running_job_count": 0,
        "failed_job_count": 2,
    }
    payload.update(updates)
    return FormalObservedStateCountsV0233.model_validate(payload)


def _safety(
    *,
    ending: FormalObservedStateCountsV0233 | None = None,
    safe: bool = True,
) -> FormalSafetyObservationV0233:
    starting = _counts()
    ending = ending or _counts(
        diagnosis_job_count=2,
        incident_count=2,
        diagnosis_count=2,
        evidence_object_count=2,
        diagnosis_evidence_index_count=2,
        diagnosis_stage_event_count=8,
    )
    action_journal = FormalActionJournalV0233.build(
        observation_status="COMPLETE",
        events=(
            "RESERVATION_CONSUMED",
            "FORMAL_CLONE_REQUESTED",
            "DEMO_START_REQUESTED",
            "PRODUCT_START_REQUESTED",
            "PRODUCT_RESTART_REQUESTED",
            "FORMAL_TRAFFIC_REQUESTED",
            "INCIDENT_CREATE_REQUESTED",
            "DIAGNOSIS_CREATE_REQUESTED",
        ),
    )
    return FormalSafetyObservationV0233.build(
        observation_status="OBSERVED",
        action_journal=action_journal.model_dump(mode="json"),
        starting_counts=starting.model_dump(mode="json"),
        ending_counts=ending.model_dump(mode="json"),
        new_incident_count=ending.incident_count - starting.incident_count,
        new_diagnosis_count=(ending.diagnosis_job_count - starting.diagnosis_job_count),
        provider_calls=0,
        agent_writes=0,
        runbook_executions=0,
        fault_attempts=0,
        knowledge_loop_executions=0,
        observed_action_authority="NONE",
        safe=safe,
    )


def _exact_branch_fixture(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(("git", "init", "--bare", "-q", str(remote)), check=True)
    subprocess.run(("git", "init", "-q", str(work)), check=True)
    subprocess.run(
        ("git", "config", "user.email", "formal-contract@example.invalid"),
        cwd=work,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Formal Contract"),
        cwd=work,
        check=True,
    )
    subprocess.run(
        (
            "git",
            "checkout",
            "-qb",
            "codex/product-v0233-fresh-formal-nofault-acceptance",
        ),
        cwd=work,
        check=True,
    )
    (work / ".gitignore").write_text(".local/\n", encoding="utf-8")
    (work / "marker.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(("git", "add", ".gitignore", "marker.txt"), cwd=work, check=True)
    subprocess.run(("git", "commit", "-qm", "fixture"), cwd=work, check=True)
    subprocess.run(
        ("git", "remote", "add", "origin", str(remote)), cwd=work, check=True
    )
    subprocess.run(("git", "push", "-qu", "origin", "HEAD"), cwd=work, check=True)
    return work


def test_formal_admission_and_reservation_are_exactly_once_and_self_sealed() -> None:
    admission = _admission()
    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime.now(UTC),
    )

    assert admission.formal_clone_count == 0
    assert admission.formal_execution_count == 0
    assert admission.new_incident_count == 0
    assert admission.new_diagnosis_count == 0
    assert admission.measured_result_count == 0
    assert reservation.formal_execution_ordinal == 1
    assert reservation.action_authority == "NONE"
    assert admission.admission_sha256 == semantic_sha256_v22(
        admission.model_dump(mode="json", exclude={"admission_sha256"})
    )
    assert reservation.reservation_sha256 == semantic_sha256_v22(
        reservation.model_dump(mode="json", exclude={"reservation_sha256"})
    )

    with pytest.raises(ValueError, match="reservation"):
        FormalExecutionReservationV0233.model_validate(
            {**reservation.model_dump(mode="json"), "reservation_sha256": _sha("0")}
        )


def test_json_seal_helper_normalizes_supported_acceptance_value_types() -> None:
    admission = _admission()
    value = {
        "observed_at": datetime(2026, 9, 1, tzinfo=UTC),
        "phase": RepositoryPhaseV0233.FORMAL_RUNNING,
        "path": Path("attempts/attempt-2"),
        "items": ("capture", "diagnosis"),
        "admission": admission,
    }
    expected = {
        "observed_at": "2026-09-01T00:00:00Z",
        "phase": "FORMAL_RUNNING",
        "path": "attempts/attempt-2",
        "items": ["capture", "diagnosis"],
        "admission": admission.model_dump(mode="json"),
    }

    assert semantic_json_sha256_v0233(value) == semantic_sha256_v22(expected)


def test_runtime_authority_proof_is_canonically_sealed() -> None:
    proof = RuntimeAuthorityProofV0233.build(
        admission_sha256=_sha("1"),
        runtime_continuity_descriptor_sha256=_sha("2"),
        pilot_runtime_authority_sha256=_sha("3"),
        runtime_connector_binding_sha256=_sha("4"),
        runtime_snapshot_sha256=_sha("5"),
        checkout_state="RUNNING",
        checkout_healthy=True,
        checkout_restart_count=0,
    )

    assert proof.proof_sha256 == semantic_sha256_v22(
        proof.model_dump(mode="json", exclude={"proof_sha256"})
    )


def test_historical_source_attempt_uses_original_raw_seals() -> None:
    payload = __import__("json").loads(
        (ROOT / "docs/analysis/product-v023-baseline-attempt-1.json").read_text()
    )
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (ROOT / "config/product-v0233/source-selection.json").read_bytes()
    )
    source_root = Path(payload["attempt"]["start"]["product_data_root"])

    audit = formal_runner._historical_source_audit_v0233(
        payload=payload,
        source_root=source_root,
        selection=selection,
    )

    assert audit.audit_sha256 == payload["readiness_audit_sha256"]
    assert audit.baseline_id == selection.active_baseline_id
    assert audit.baseline_sha256 == selection.active_baseline_sha256
    assert audit.active_opensearch_profile_sha256 == selection.active_profile_sha256


def test_historical_source_loader_accepts_exact_head_bytes(tmp_path: Path) -> None:
    canonical_bytes = (
        ROOT / "docs/analysis/product-v023-baseline-attempt-1.json"
    ).read_bytes()
    payload = __import__("json").loads(canonical_bytes)
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (ROOT / "config/product-v0233/source-selection.json").read_bytes()
    )
    source_root = Path(payload["attempt"]["start"]["product_data_root"])
    predecessor = tmp_path / "predecessor"
    canonical_root = tmp_path / "current"
    for checkout in (predecessor, canonical_root):
        path = checkout / "docs/analysis/product-v023-baseline-attempt-1.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(canonical_bytes)

    audit = formal_runner._load_historical_source_audit_v0233(
        root=canonical_root,
        predecessor=predecessor,
        source_root=source_root,
        selection=selection,
    )

    assert audit.audit_sha256 == payload["readiness_audit_sha256"]


@pytest.mark.parametrize(
    "drift",
    (
        "execution_head",
        "outer_terminal",
        "inner_terminal",
        "source_root",
        "profile_sha256",
        "baseline",
        "job_type",
        "build_policy",
        "audit",
        "cleanup",
        "zero_action",
    ),
)
def test_historical_source_loader_rejects_fully_resealed_predecessor_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    payload = __import__("json").loads(
        (ROOT / "docs/analysis/product-v023-baseline-attempt-1.json").read_text()
    )
    selection = FreshFormalSourceSelectionV0233.model_validate_json(
        (ROOT / "config/product-v0233/source-selection.json").read_bytes()
    )
    source_root = Path(payload["attempt"]["start"]["product_data_root"])
    if drift == "execution_head":
        payload["execution_head"] = "f" * 40
    elif drift == "outer_terminal":
        payload["terminal"] = "ECOMSRE_PRODUCT_V023_BASELINE_READINESS_PASS"
    elif drift == "inner_terminal":
        payload["attempt"]["completion"]["terminal"] = "BLOCKED"
    elif drift == "source_root":
        payload["attempt"]["start"]["product_data_root"] = "/tmp/dirty-source"
    elif drift == "profile_sha256":
        payload["attempt"]["start"]["profile_sha256"] = _sha("f")
    elif drift == "baseline":
        payload["active_baseline_id"] = "base-dirty"
    elif drift == "job_type":
        payload["attempt"]["completion"]["builder_job_record"]["job_type"] = "VERIFY"
    elif drift == "build_policy":
        payload["attempt"]["completion"]["builder_job_record"]["payload"]["request"][
            "build_policy"
        ]["lookback_seconds"] = 999
    elif drift == "audit":
        payload["attempt"]["completion"]["per_window_audit"][
            "final_builder_would_pass"
        ] = False
    elif drift == "cleanup":
        payload["product_cleanup"] = "UNKNOWN"
    else:
        payload["fault_attempt_count"] = 1
    start = payload["attempt"]["start"]
    start_body = {key: value for key, value in start.items() if key != "start_sha256"}
    start["start_sha256"] = semantic_sha256_v22(start_body)
    completion = payload["attempt"]["completion"]
    audit_payload = completion["per_window_audit"]
    audit_body = {
        key: value for key, value in audit_payload.items() if key != "audit_sha256"
    }
    audit_payload["audit_sha256"] = semantic_sha256_v22(audit_body)
    completion["per_window_audit_sha256"] = audit_payload["audit_sha256"]
    completion["builder_job_record"]["result"]["readiness_audit_v023"] = audit_payload
    completion["start_sha256"] = start["start_sha256"]
    completion["builder_job_evidence_sha256"] = semantic_sha256_v22(
        completion["builder_job_record"]
    )
    completion_body = {
        key: value for key, value in completion.items() if key != "completion_sha256"
    }
    completion["completion_sha256"] = semantic_sha256_v22(completion_body)
    ledger_body = {
        "schema_version": "ecomsre.product.baseline-attempt-ledger.v023",
        "attempts": [payload["attempt"]],
        "maximum_changed_attempts": 2,
    }
    payload["ledger_sha256"] = semantic_sha256_v22(ledger_body)

    predecessor = tmp_path / "predecessor"
    canonical_root = tmp_path / "current"
    predecessor_analysis = predecessor / "docs/analysis"
    canonical_analysis = canonical_root / "docs/analysis"
    predecessor_analysis.mkdir(parents=True)
    canonical_analysis.mkdir(parents=True)
    predecessor_path = predecessor_analysis / "product-v023-baseline-attempt-1.json"
    canonical_path = canonical_analysis / "product-v023-baseline-attempt-1.json"
    predecessor_path.write_text(__import__("json").dumps(payload))
    canonical_path.write_bytes(
        (ROOT / "docs/analysis/product-v023-baseline-attempt-1.json").read_bytes()
    )

    with pytest.raises(ValueError, match="historical Baseline frozen bytes"):
        formal_runner._load_historical_source_audit_v0233(
            root=canonical_root,
            predecessor=predecessor,
            source_root=source_root,
            selection=selection,
        )


def test_formal_traffic_requires_exact_30_zero_retry_and_minimum_duration() -> None:
    execution = _successful_execution()
    profile = load_fresh_traffic_profile_v0233(ROOT, role="FORMAL")
    started = datetime.now(UTC)
    result = FormalTrafficResultV0233.build(
        admission_sha256=_admission().admission_sha256,
        formal_profile_sha256=profile.profile_sha256,
        traffic_contract_sha256=execution.run.contract_sha256,
        execution=execution,
        episode_started_at=started,
        episode_ended_at=started + timedelta(seconds=300),
        monotonic_duration_ms=300_000,
    )

    assert result.terminal == "ECOMSRE_PRODUCT_V0233_FORMAL_HEALTHY_TRAFFIC_PASS"
    assert result.execution.run.successful_transactions == 30
    assert result.execution.run.failed_transactions == 0
    assert result.execution.run.transport_retry_count == 0
    assert result.result_sha256 == semantic_sha256_v22(
        result.model_dump(mode="json", exclude={"result_sha256"})
    )

    with pytest.raises(ValueError, match="formal traffic"):
        FormalTrafficResultV0233.model_validate(
            {
                **result.model_dump(mode="json"),
                "monotonic_duration_ms": 299_999,
                "result_sha256": _sha("0"),
            }
        )


def test_fresh_runtime_snapshot_with_utc_datetime_is_canonically_sealed() -> None:
    proof = FreshRuntimeSnapshotProofV0233.build(
        admission_sha256=_sha("1"),
        formal_traffic_result_sha256=_sha("2"),
        runtime_snapshot_sha256=_sha("3"),
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        pilot_runtime_authority_sha256=_sha("4"),
        runtime_continuity_descriptor_sha256=_sha("5"),
        runtime_connector_binding_sha256=_sha("6"),
    )

    assert proof.proof_sha256 == semantic_sha256_v22(
        proof.model_dump(mode="json", exclude={"proof_sha256"})
    )


def test_post_traffic_acceptance_artifact_dry_run_publishes_measured_result(
    tmp_path: Path,
) -> None:
    admission = _admission()
    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    execution = _successful_execution()
    profile = load_fresh_traffic_profile_v0233(ROOT, role="FORMAL")
    episode_started_at = execution.run.started_at - timedelta(seconds=1)
    episode_ended_at = episode_started_at + timedelta(seconds=300)
    traffic = FormalTrafficResultV0233.build(
        admission_sha256=admission.admission_sha256,
        formal_profile_sha256=profile.profile_sha256,
        traffic_contract_sha256=execution.run.contract_sha256,
        execution=execution,
        episode_started_at=episode_started_at,
        episode_ended_at=episode_ended_at,
        monotonic_duration_ms=300_000,
    )
    fresh_runtime = FreshRuntimeSnapshotProofV0233.build(
        admission_sha256=admission.admission_sha256,
        formal_traffic_result_sha256=traffic.result_sha256,
        runtime_snapshot_sha256=_sha("3"),
        observed_at=episode_ended_at,
        pilot_runtime_authority_sha256=_sha("4"),
        runtime_continuity_descriptor_sha256=_sha("5"),
        runtime_connector_binding_sha256=_sha("6"),
    )

    admit_incident_creation_v0233(
        runtime_authority_pass=True,
        baseline_restart_pass=True,
        formal_traffic_pass=True,
        fresh_runtime_snapshot_pass=True,
        new_incident_count=0,
        new_diagnosis_count=0,
    )
    binding = IncidentTrafficBindingV0232.build(
        incident_id="inc-product-v0233-dry-run",
        execution=execution,
        episode_started_at=episode_started_at,
        episode_ended_at=episode_ended_at,
    )
    result = NoFaultAcceptanceResultV0233.build_from_v0232(
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_sha256=_sha("3"),
        runtime_authority_proof_sha256=_sha("4"),
        baseline_restart_proof_sha256=_sha("5"),
        traffic_preflight_sha256=_sha("6"),
        formal_traffic_execution_sha256=traffic.result_sha256,
        fresh_runtime_snapshot_sha256=fresh_runtime.proof_sha256,
        incident_traffic_binding_sha256=binding.binding_sha256,
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
    bundle = _terminal_publication_bundle(
        reservation=reservation,
        kind="MEASURED",
        terminal=result.measured_terminal,
        artifacts=(
            {
                "path": "docs/analysis/product-v0233-fresh-runtime-snapshot.json",
                "mode": "CREATE_JSON",
                "payload": fresh_runtime.model_dump(mode="json"),
            },
            {
                "path": "docs/analysis/product-v0233-incident-traffic-binding.json",
                "mode": "CREATE_JSON",
                "payload": binding.model_dump(mode="json"),
            },
            {
                "path": "docs/results/product-v0233-nofault-acceptance.json",
                "mode": "CREATE_JSON",
                "payload": result.model_dump(mode="json"),
            },
            {
                "path": "config/product-v0233/repository-state-manifest.json",
                "mode": "REPLACE_JSON",
                "payload": {"phase": "FORMAL_MEASURED"},
            },
            {
                "path": "docs/analysis/product-v0233-progress.json",
                "mode": "REPLACE_JSON",
                "payload": {"phase": "FORMAL_MEASURED"},
            },
        ),
    )
    private_root = tmp_path / ".local/product-v0233/attempts/dry-run"

    _persist_and_apply_terminal_publication(
        root=tmp_path,
        private_root=private_root,
        bundle=bundle,
    )

    assert result.result_sha256 == semantic_sha256_v22(
        result.model_dump(mode="json", exclude={"result_sha256"})
    )
    assert binding.binding_sha256 == semantic_sha256_v22(
        binding.model_dump(mode="json", exclude={"binding_sha256"})
    )
    assert (private_root / "terminal-publication-completion.json").is_file()
    assert (tmp_path / "docs/results/product-v0233-nofault-acceptance.json").is_file()


def test_formal_closure_fails_closed_on_any_queue_baseline_or_source_drift() -> None:
    safety = _safety()
    closure = FormalClosureProofV0233.build(
        queue_before_sha256=_sha("1"),
        queue_after_sha256=_sha("1"),
        outer_baseline_before_sha256=_sha("2"),
        outer_baseline_after_sha256=_sha("2"),
        source_selection_before_sha256=_sha("3"),
        source_selection_after_sha256=_sha("3"),
        source_database_before_sha256=_sha("4"),
        source_database_after_sha256=_sha("4"),
        product_cleanup="CLEAN",
        demo_cleanup="CLEAN",
        owned_host_processes=0,
        owned_containers=0,
        owned_networks=0,
        owned_volumes=0,
        formal_clone_database_owner_count=0,
        non_owned_resources_changed=False,
        clone_baseline_binding_exact=True,
        frozen_semantic_surface_before_sha256=_sha("5"),
        frozen_semantic_surface_after_sha256=_sha("5"),
        safety_observation=safety.model_dump(mode="json"),
    )
    assert closure.verdict == "CLEAN"
    assert closure.closure_sha256 == semantic_sha256_v22(
        closure.model_dump(mode="json", exclude={"closure_sha256"})
    )

    with pytest.raises(ValueError, match="closure"):
        FormalClosureProofV0233.model_validate(
            {
                **closure.model_dump(mode="json"),
                "queue_after_sha256": _sha("5"),
                "closure_sha256": _sha("0"),
            }
        )


def test_safety_fails_closed_on_baseline_cardinality_drift() -> None:
    safety = _safety(
        ending=_counts(
            baseline_count=2,
            diagnosis_job_count=2,
            incident_count=2,
            diagnosis_count=2,
            evidence_object_count=2,
            diagnosis_evidence_index_count=2,
            diagnosis_stage_event_count=8,
        ),
        safe=False,
    )

    assert safety.safe is False
    assert safety.ending_counts is not None
    assert safety.ending_counts.baseline_count == 2


def test_action_journal_observes_forbidden_dispatch_instead_of_hardcoding_zero() -> (
    None
):
    action_journal = FormalActionJournalV0233.build(
        observation_status="COMPLETE",
        events=("RESERVATION_CONSUMED", "FAULT_ATTEMPT_REQUESTED"),
    )
    starting = _counts()
    ending = _counts(
        diagnosis_job_count=2,
        incident_count=2,
        diagnosis_count=2,
        evidence_object_count=2,
        diagnosis_evidence_index_count=2,
        diagnosis_stage_event_count=8,
    )
    safety = FormalSafetyObservationV0233.build(
        observation_status="OBSERVED",
        action_journal=action_journal.model_dump(mode="json"),
        starting_counts=starting.model_dump(mode="json"),
        ending_counts=ending.model_dump(mode="json"),
        new_incident_count=1,
        new_diagnosis_count=1,
        provider_calls=0,
        agent_writes=0,
        runbook_executions=0,
        fault_attempts=action_journal.fault_attempts,
        knowledge_loop_executions=action_journal.knowledge_loop_executions,
        observed_action_authority="NONE",
        safe=False,
    )

    assert action_journal.fault_attempts == 1
    assert safety.safe is False
    assert action_journal.journal_sha256 == semantic_sha256_v22(
        action_journal.model_dump(mode="json", exclude={"journal_sha256"})
    )
    assert safety.observation_sha256 == semantic_sha256_v22(
        safety.model_dump(mode="json", exclude={"observation_sha256"})
    )


def test_blocker_cannot_claim_a_measured_result() -> None:
    safety = _safety()
    blocker = FormalExecutionBlockerV0233.build(
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_DIAGNOSIS_PIPELINE",
        failure_stage="DIAGNOSIS_JOB_FAILED",
        safe_error_code="INTERNAL_CONTRACT_FAILURE",
        admission_sha256=_admission().admission_sha256,
        reservation_sha256=_sha("7"),
        formal_clone_count=1,
        formal_clone_proof_status="OBSERVED",
        formal_clone_sha256=_sha("8"),
        formal_execution_count=1,
        new_incident_count=1,
        new_diagnosis_count=1,
        cleanup_proof_sha256=_sha("9"),
        journal_tail_sha256=_sha("a"),
        exception_fingerprint=_sha("b"),
        private_failure_envelope_sha256=_sha("c"),
        safety_observation=safety.model_dump(mode="json"),
    )
    assert blocker.measured_result_count == 0
    assert blocker.measured_terminal is None
    assert blocker.blocker_sha256 == semantic_sha256_v22(
        blocker.model_dump(mode="json", exclude={"blocker_sha256"})
    )

    with pytest.raises(ValueError, match="Input should be None"):
        FormalExecutionBlockerV0233.model_validate(
            {
                **blocker.model_dump(mode="json"),
                "measured_terminal": "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED",
                "blocker_sha256": _sha("0"),
            }
        )


def test_blocker_preserves_unsafe_cardinality_without_truncation() -> None:
    safety = _safety(
        ending=_counts(
            diagnosis_job_count=3,
            incident_count=3,
            diagnosis_count=1,
            diagnosis_evidence_index_count=1,
            diagnosis_stage_event_count=4,
        )
    )
    blocker = FormalExecutionBlockerV0233.build(
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        failure_stage="ACCEPTANCE_ARTIFACT_CONSTRUCTION",
        safe_error_code="FORMAL_ACCEPTANCE_CARDINALITY_DRIFT",
        admission_sha256=_admission().admission_sha256,
        reservation_sha256=_sha("7"),
        formal_clone_count=1,
        formal_clone_proof_status="OBSERVED",
        formal_clone_sha256=_sha("8"),
        formal_execution_count=1,
        new_incident_count=2,
        new_diagnosis_count=2,
        cleanup_proof_sha256=_sha("9"),
        safety_observation=safety.model_dump(mode="json"),
    )

    assert blocker.new_incident_count == 2
    assert blocker.new_diagnosis_count == 2


def test_repository_manifest_can_truthfully_terminalize_clone_zero() -> None:
    body = {
        "schema_version": "ecomsre.product.repository-state.v0233",
        "goal_version": (
            "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
        ),
        "phase": RepositoryPhaseV0233.FORMAL_BLOCKED.value,
        "history_and_handoff_sha256": _sha("1"),
        "source_selection_sha256": _sha("2"),
        "clone_contract_sha256": _sha("3"),
        "campaign_sha256": _sha("4"),
        "contract_preflight_sha256": _sha("5"),
        "traffic_preflight_sha256": _sha("6"),
        "formal_contract_freeze_sha256": _sha("7"),
        "pre_execution_review_sha256": _sha("8"),
        "formal_result_sha256": None,
        "formal_blocker_sha256": _sha("9"),
        "knowledge_handoff_sha256": None,
        "cleanup_proof_sha256": None,
        "formal_clone_count": 0,
        "formal_execution_count": 1,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
        "action_authority": "NONE",
    }
    manifest = ProductV0233RepositoryStateManifest.model_validate(
        {**body, "manifest_sha256": semantic_sha256_v22(body)}
    )

    assert manifest.phase is RepositoryPhaseV0233.FORMAL_BLOCKED
    assert manifest.formal_clone_count == 0
    assert manifest.cleanup_proof_sha256 is None


def test_clone_zero_blocker_records_unavailable_poststate_without_fake_cleanup() -> (
    None
):
    starting = _counts()
    safety = FormalSafetyObservationV0233.build(
        observation_status="UNAVAILABLE",
        action_journal=FormalActionJournalV0233.build(
            observation_status="UNAVAILABLE",
            events=("RESERVATION_CONSUMED",),
        ).model_dump(mode="json"),
        starting_counts=starting.model_dump(mode="json"),
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
    blocker = FormalExecutionBlockerV0233.build(
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_STATE_CLONE",
        failure_stage="CLONE_PENDING",
        safe_error_code="STATE_CLONE_FAILED",
        admission_sha256=_admission().admission_sha256,
        reservation_sha256=_sha("7"),
        formal_clone_count=0,
        formal_clone_proof_status="NOT_CREATED",
        formal_clone_sha256=None,
        formal_execution_count=1,
        new_incident_count=0,
        new_diagnosis_count=0,
        cleanup_proof_sha256=None,
        safety_observation=safety.model_dump(mode="json"),
    )

    assert blocker.formal_clone_count == 0
    assert blocker.cleanup_proof_sha256 is None
    assert blocker.safety_observation.observation_status == "UNAVAILABLE"
    assert blocker.blocker_sha256 == semantic_sha256_v22(
        blocker.model_dump(mode="json", exclude={"blocker_sha256"})
    )


def test_restart_allows_historical_failed_jobs_but_rejects_a_new_failure() -> None:
    proof = BaselineRestartProofV0233.build(
        admission_sha256=_admission().admission_sha256,
        environment_id="env-" + "1" * 24,
        active_baseline_id="base-" + "2" * 24,
        active_baseline_sha256=_sha("3"),
        active_profile_sha256=_sha("4"),
        readiness_audit_sha256=_sha("5"),
        api_instance_id_before="api-" + "6" * 24,
        api_instance_id_after="api-" + "7" * 24,
        worker_instance_id_before="worker-" + "8" * 24,
        worker_instance_id_after="worker-" + "9" * 24,
        failed_jobs_before=2,
        failed_jobs_after=2,
    )
    assert proof.failed_jobs_after == 2
    assert proof.proof_sha256 == semantic_sha256_v22(
        proof.model_dump(mode="json", exclude={"proof_sha256"})
    )

    with pytest.raises(ValueError, match="restart proof"):
        BaselineRestartProofV0233.model_validate(
            {
                **proof.model_dump(mode="json"),
                "failed_jobs_after": 3,
                "proof_sha256": _sha("0"),
            }
        )


def test_dirty_checkout_is_rejected_before_any_formal_reservation(
    tmp_path: Path,
) -> None:
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "config", "user.email", "formal-contract@example.invalid"),
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Formal Contract"),
        cwd=tmp_path,
        check=True,
    )
    marker = tmp_path / "marker.txt"
    marker.write_text("clean\n", encoding="utf-8")
    subprocess.run(("git", "add", "marker.txt"), cwd=tmp_path, check=True)
    subprocess.run(("git", "commit", "-qm", "fixture"), cwd=tmp_path, check=True)
    marker.write_text("dirty\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0233_STARTING_HEAD",
    ):
        strict_formal_admission_v0233(tmp_path)

    assert not (tmp_path / ".local/product-v0233/formal-reservation.json").exists()


def test_exact_upstream_and_formal_destination_absence_are_pre_mutation_gates(
    tmp_path: Path,
) -> None:
    work = _exact_branch_fixture(tmp_path)
    destination = (
        work
        / ".local/product-v0233/formal-state"
        / "product-v0233-fresh-formal-nofault"
        / "product"
    )
    destination.mkdir(parents=True)

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
    ):
        strict_formal_admission_v0233(work)


def test_existing_reservation_is_rejected_before_gate_reentry(tmp_path: Path) -> None:
    work = _exact_branch_fixture(tmp_path)
    reservation = work / ".local/product-v0233/formal-reservation.json"
    reservation.parent.mkdir(parents=True)
    reservation.write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
    ):
        strict_formal_admission_v0233(work)


def test_wrong_upstream_is_a_typed_starting_head_blocker(tmp_path: Path) -> None:
    work = _exact_branch_fixture(tmp_path)
    subprocess.run(
        ("git", "branch", "--set-upstream-to", "origin/HEAD"),
        cwd=work,
        check=False,
        capture_output=True,
    )
    subprocess.run(
        (
            "git",
            "config",
            "branch.codex/product-v0233-fresh-formal-nofault-acceptance.merge",
            "refs/heads/wrong",
        ),
        cwd=work,
        check=True,
    )
    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0233_STARTING_HEAD",
    ):
        strict_formal_admission_v0233(work)


def test_frozen_semantic_surface_recheck_fails_on_scorer_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _frozen_semantic_surface_sha256_v0233(ROOT)
    assert len(observed) == 64
    original = formal_runner._sha256_file

    def drifted(path: Path) -> str:
        if path.name == "nofault_acceptance_v0232.py":
            return _sha("0")
        return original(path)

    monkeypatch.setattr(formal_runner, "_sha256_file", drifted)
    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
    ):
        _frozen_semantic_surface_sha256_v0233(ROOT)


@pytest.mark.parametrize("failure_ordinal", (1, 2, 3, 4))
def test_terminal_publication_intent_recovers_after_each_injected_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_ordinal: int,
) -> None:
    reservation = FormalExecutionReservationV0233.build(
        admission=_admission(),
        reserved_at=datetime.now(UTC),
    )
    bundle = _terminal_publication_bundle(
        reservation=reservation,
        kind="BLOCKER",
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        artifacts=(
            {
                "path": "docs/analysis/product-v0233-formal-blocker.json",
                "mode": "CREATE_JSON",
                "payload": {"terminal": "BLOCKED"},
            },
            {
                "path": "config/product-v0233/repository-state-manifest.json",
                "mode": "REPLACE_JSON",
                "payload": {"phase": "FORMAL_BLOCKED"},
            },
            {
                "path": "docs/analysis/product-v0233-progress.json",
                "mode": "REPLACE_JSON",
                "payload": {"phase": "FORMAL_BLOCKED"},
            },
        ),
    )
    private_root = tmp_path / ".local/product-v0233/formal-execution"
    original_create = formal_runner._write_public_create_once
    original_replace = formal_runner._replace_public
    original_private = formal_runner.write_private_json
    writes = 0

    def fail_at_selected_write(operation, path: Path, payload: object) -> None:
        nonlocal writes
        writes += 1
        if writes == failure_ordinal:
            raise OSError("injected publication failure")
        operation(path, payload)

    def create(path: Path, payload: object) -> None:
        fail_at_selected_write(original_create, path, payload)

    def replace(path: Path, payload: object) -> None:
        fail_at_selected_write(original_replace, path, payload)

    def private(path: Path, payload: object, *, create_once: bool) -> str:
        nonlocal writes
        if path.name == "terminal-publication-completion.json":
            writes += 1
            if writes == failure_ordinal:
                raise OSError("injected publication failure")
        return original_private(path, payload, create_once=create_once)

    monkeypatch.setattr(formal_runner, "_write_public_create_once", create)
    monkeypatch.setattr(formal_runner, "_replace_public", replace)
    monkeypatch.setattr(formal_runner, "write_private_json", private)
    with pytest.raises(OSError, match="injected publication failure"):
        _persist_and_apply_terminal_publication(
            root=tmp_path,
            private_root=private_root,
            bundle=bundle,
        )
    assert (private_root / "terminal-publication.json").is_file()
    assert not (private_root / "terminal-publication-completion.json").exists()

    monkeypatch.setattr(formal_runner, "_write_public_create_once", original_create)
    monkeypatch.setattr(formal_runner, "_replace_public", original_replace)
    monkeypatch.setattr(formal_runner, "write_private_json", original_private)
    _persist_and_apply_terminal_publication(
        root=tmp_path,
        private_root=private_root,
        bundle=bundle,
    )
    assert (tmp_path / "docs/analysis/product-v0233-formal-blocker.json").is_file()
    assert (private_root / "terminal-publication-completion.json").is_file()


@pytest.mark.parametrize("existing_matches", (True, False))
def test_terminal_publication_recovery_is_idempotent_only_for_identical_json(
    tmp_path: Path,
    existing_matches: bool,
) -> None:
    reservation = FormalExecutionReservationV0233.build(
        admission=_admission(),
        reserved_at=datetime.now(UTC),
    )
    clone_payload = {"clone_sha256": _sha("1")}
    bundle = _terminal_publication_bundle(
        reservation=reservation,
        kind="BLOCKER",
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        artifacts=(
            {
                "path": "docs/analysis/product-v0233-formal-state-clone.json",
                "mode": "CREATE_JSON",
                "payload": clone_payload,
            },
            {
                "path": "docs/analysis/product-v0233-formal-blocker.json",
                "mode": "CREATE_JSON",
                "payload": {"terminal": "BLOCKED"},
            },
            {
                "path": "config/product-v0233/repository-state-manifest.json",
                "mode": "REPLACE_JSON",
                "payload": {"phase": "FORMAL_BLOCKED"},
            },
            {
                "path": "docs/analysis/product-v0233-progress.json",
                "mode": "REPLACE_JSON",
                "payload": {"phase": "FORMAL_BLOCKED"},
            },
        ),
    )
    clone_path = tmp_path / "docs/analysis/product-v0233-formal-state-clone.json"
    formal_runner._write_public_create_once(
        clone_path,
        clone_payload if existing_matches else {"clone_sha256": _sha("2")},
    )
    private_root = tmp_path / ".local/product-v0233/formal-execution"

    if existing_matches:
        _persist_and_apply_terminal_publication(
            root=tmp_path,
            private_root=private_root,
            bundle=bundle,
        )
        assert (tmp_path / "docs/analysis/product-v0233-formal-blocker.json").is_file()
        assert (private_root / "terminal-publication-completion.json").is_file()
    else:
        with pytest.raises(FileExistsError, match="public artifact differs"):
            _persist_and_apply_terminal_publication(
                root=tmp_path,
                private_root=private_root,
                bundle=bundle,
            )
        assert not (private_root / "terminal-publication-completion.json").exists()


def test_consumed_reservation_without_intent_freezes_typed_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation = FormalExecutionReservationV0233.build(
        admission=_admission(),
        reserved_at=datetime.now(UTC),
    )
    reservation_path = tmp_path / ".local/product-v0233/formal-reservation.json"
    reservation_path.parent.mkdir(parents=True)
    reservation_path.write_text(reservation.model_dump_json(), encoding="utf-8")
    for relative in (
        "config/product-v0233/repository-state-manifest.json",
        "docs/analysis/product-v0233-progress.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())

    def unavailable_source(_root: Path):
        raise RuntimeError("injected source read failure")

    monkeypatch.setattr(formal_runner, "_selected_source", unavailable_source)
    formal_runner._terminalize_consumed_reservation_v0233(
        tmp_path,
        trigger=OSError("injected post-reservation failure"),
    )

    blocker = __import__("json").loads(
        (tmp_path / "docs/analysis/product-v0233-formal-blocker.json").read_text()
    )
    manifest = __import__("json").loads(
        (tmp_path / "config/product-v0233/repository-state-manifest.json").read_text()
    )
    assert blocker["terminal"] == ("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    assert blocker["formal_clone_count"] == 0
    assert blocker["safety_observation"]["observation_status"] == "UNAVAILABLE"
    assert manifest["phase"] == "FORMAL_BLOCKED"
    assert (
        tmp_path / ".local/product-v0233/formal-execution/terminal-publication.json"
    ).is_file()


@pytest.mark.parametrize(
    "injected_error",
    (
        PermissionError("injected reservation chmod/stat failure"),
        RuntimeError("injected post-finally construction failure"),
    ),
)
def test_public_runner_preserves_attempt_for_resume_after_unpublished_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    injected_error: BaseException,
) -> None:
    reservation = FormalExecutionReservationV0233.build(
        admission=_admission(),
        reserved_at=datetime.now(UTC),
    )
    def fail_after_reservation(
        *, project_root: Path, attempt_id: str, semantic_generation: int
    ):
        assert attempt_id == "attempt-2"
        assert semantic_generation == 2
        reservation_path = (
            project_root
            / ".local/product-v0233/attempts/attempt-2/reservation.json"
        )
        reservation_path.parent.mkdir(parents=True)
        reservation_path.write_text(reservation.model_dump_json(), encoding="utf-8")
        raise injected_error

    monkeypatch.setattr(
        formal_runner,
        "_run_formal_nofault_once_v0233",
        fail_after_reservation,
    )
    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_REQUIRED",
    ):
        formal_runner.run_formal_nofault_v0233(project_root=tmp_path)

    assert (
        tmp_path / ".local/product-v0233/attempts/attempt-2/reservation.json"
    ).is_file()
    assert not (tmp_path / "docs/analysis/product-v0233-formal-blocker.json").exists()
