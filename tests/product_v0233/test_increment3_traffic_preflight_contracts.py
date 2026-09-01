from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    load_fresh_formal_campaign_v0233,
    load_fresh_traffic_profile_v0233,
)
from ecomsre.product.pilot.traffic_preflight_v0233 import (
    ALLOWED_REPAIR_SURFACES_V0233,
    TRAFFIC_PREFLIGHT_PASS_V0233,
    DiagnosisSemanticSourceManifestV0233,
    FormalClonePlanV0233,
    FormalContractFreezeV0233,
    TrafficPreflightAttemptV0233,
    TrafficPreflightBlockedAttemptV0233,
    TrafficPreflightLedgerV0233,
    TrafficPreflightPassV0233,
    TrafficRepairSurfaceSnapshotV0233,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficRunV0232,
)
from tests.product_v0232.test_increment4_traffic_preflight import _execution
from scripts.product_v0233.run_traffic_preflight import (
    _attempt_chain,
    _diagnosis_source_manifest,
)


ROOT = Path(__file__).resolve().parents[2]


def _sha(character: str) -> str:
    return character * 64


def _attempt(*, success: bool = True) -> TrafficPreflightAttemptV0233:
    campaign = load_fresh_formal_campaign_v0233(ROOT)
    profile = load_fresh_traffic_profile_v0233(ROOT, role="PREFLIGHT")
    execution = _execution(transactions=10, success=success)
    engine = profile.engine_profile_v0232()
    run_payload = execution.run.model_dump(
        mode="python", exclude={"schema_version", "result_sha256"}
    )
    run_payload["profile_sha256"] = engine.profile_sha256
    run = HealthyTrafficRunV0232.build(**run_payload)
    execution = HealthyTrafficExecutionV0232.build(
        run=run,
        observations=execution.observations,
    )
    return TrafficPreflightAttemptV0233.build(
        attempt_id="20260901T030000Z-first",
        attempt_ordinal=1,
        prior_attempt_sha256=None,
        changed_surface=None,
        changed_surface_sha256=None,
        campaign_sha256=campaign.campaign_sha256,
        source_selection_sha256=campaign.source_selection_sha256,
        profile_sha256=profile.profile_sha256,
        engine_profile_sha256=engine.profile_sha256,
        traffic_contract_sha256=campaign.traffic_contract_sha256,
        typed_request_plan_sha256=_sha("1"),
        flagd_bind_descriptor_sha256=campaign.flagd_bind_descriptor_sha256,
        runtime_continuity_descriptor_sha256=(
            campaign.runtime_continuity_descriptor_sha256
        ),
        resolved_compose_sha256=_sha("2"),
        read_authority_sha256=_sha("3"),
        pilot_runtime_authority_sha256=_sha("4"),
        checkout_state="RUNNING",
        checkout_healthy=True,
        checkout_restart_count=0,
        execution=execution,
        queue_before_sha256=_sha("5"),
        queue_after_sha256=_sha("5"),
        outer_baseline_before_sha256=_sha("6"),
        outer_baseline_after_sha256=_sha("6"),
        source_state_before_sha256=_sha("7"),
        source_state_after_sha256=_sha("7"),
        source_incident_count=1,
        source_diagnosis_count=1,
        demo_cleanup={
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        },
        product_cleanup={
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "database_owner_count_before": 0,
            "database_owner_count_after": 0,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
        },
    )


def test_first_attempt_pass_ledger_and_preflight_are_self_sealed() -> None:
    attempt = _attempt()
    ledger = TrafficPreflightLedgerV0233.build(attempts=(attempt,))
    campaign = load_fresh_formal_campaign_v0233(ROOT)
    preflight = TrafficPreflightPassV0233.build(
        attempt=attempt,
        ledger_sha256=ledger.ledger_sha256,
        formal_profile_sha256=campaign.formal_profile_sha256,
    )

    assert attempt.terminal == "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT_PASS"
    assert attempt.execution.run.successful_transactions == 10
    assert attempt.execution.run.transport_retry_count == 0
    assert ledger.attempt_count == 1
    assert preflight.terminal == TRAFFIC_PREFLIGHT_PASS_V0233
    assert preflight.preflight_sha256 == semantic_sha256_v22(
        preflight.model_dump(mode="json", exclude={"preflight_sha256"})
    )


def test_identical_second_attempt_is_rejected() -> None:
    first = _attempt()
    payload = first.model_dump(mode="python", exclude={"attempt_sha256"})
    payload.update(
        attempt_id="20260901T031000Z-second",
        attempt_ordinal=2,
        prior_attempt_sha256=first.attempt_sha256,
    )
    with pytest.raises(ValueError, match="changed surface"):
        TrafficPreflightAttemptV0233.build(**payload)


def test_blocked_attempt_remains_in_append_only_chain() -> None:
    first = TrafficPreflightBlockedAttemptV0233.build(
        attempt_id="20260901T030000Z-first",
        attempt_ordinal=1,
        prior_attempt_sha256=None,
        changed_surface=None,
        changed_surface_sha256=None,
        attempt_consumed=True,
        failure_stage="TRAFFIC_EXECUTION",
        safe_error_type="RuntimeError",
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        profile_sha256=_sha("3"),
        traffic_contract_sha256=_sha("4"),
        source_state_before_sha256=_sha("5"),
        source_state_after_sha256=_sha("5"),
        demo_cleanup=None,
        product_cleanup=None,
    )
    ledger = TrafficPreflightLedgerV0233.build(attempts=(first,))

    assert ledger.attempt_count == 1
    assert ledger.terminal_attempt_sha256 == first.attempt_sha256
    assert first.terminal == (
        "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT"
    )


def test_completed_nonpassing_execution_is_ledger_compatible() -> None:
    attempt = _attempt(success=False)
    ledger = TrafficPreflightLedgerV0233.build(attempts=(attempt,))

    assert attempt.terminal == (
        "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_ATTEMPT"
    )
    assert attempt.execution.run.completed_transactions == 10
    assert attempt.execution.run.successful_transactions == 0
    assert ledger.terminal_attempt_sha256 == attempt.attempt_sha256


def test_formal_freeze_binds_zero_one_shot_counters() -> None:
    campaign = load_fresh_formal_campaign_v0233(ROOT)
    freeze = FormalContractFreezeV0233.build(
        campaign_sha256=campaign.campaign_sha256,
        traffic_preflight_sha256=_sha("1"),
        source_selection_sha256=campaign.source_selection_sha256,
        formal_clone_plan_sha256=_sha("2"),
        traffic_contract_sha256=campaign.traffic_contract_sha256,
        preflight_profile_sha256=campaign.preflight_profile_sha256,
        preflight_profile_file_sha256=_sha("8"),
        formal_profile_sha256=campaign.formal_profile_sha256,
        formal_profile_file_sha256=_sha("9"),
        runtime_continuity_descriptor_sha256=(
            campaign.runtime_continuity_descriptor_sha256
        ),
        flagd_bind_descriptor_sha256=campaign.flagd_bind_descriptor_sha256,
        resolved_compose_sha256=_sha("5"),
        read_authority_sha256=_sha("6"),
        pilot_runtime_authority_sha256=_sha("7"),
        active_profile_sha256=campaign.active_profile_sha256,
        active_baseline_sha256=campaign.active_baseline_sha256,
        stage_journal_contract_sha256=campaign.stage_journal_contract_sha256,
        private_failure_contract_sha256=campaign.private_failure_contract_sha256,
        diagnosis_semantic_source_manifest_sha256=_sha("3"),
        nofault_scorer_source_sha256=campaign.nofault_scorer_source_sha256,
        prepared_repository_manifest_sha256=_sha("4"),
    )
    assert freeze.formal_clone_count == 0
    assert freeze.formal_execution_count == 0
    assert freeze.new_incident_count == 0
    assert freeze.new_diagnosis_count == 0
    assert freeze.measured_result_count == 0
    assert freeze.action_authority == "NONE"


def test_formal_clone_plan_uses_goal_destination() -> None:
    campaign = load_fresh_formal_campaign_v0233(ROOT)
    plan = FormalClonePlanV0233.build(
        source_selection_sha256=campaign.source_selection_sha256
    )

    assert plan.destination_locator == (
        ".local/product-v0233/formal-state/"
        "product-v0233-fresh-formal-nofault/product"
    )
    assert plan.formal_clone_count == 0


def test_diagnosis_source_manifest_closes_transitive_local_imports() -> None:
    manifest = _diagnosis_source_manifest(ROOT)
    reparsed = DiagnosisSemanticSourceManifestV0233.model_validate_json(
        manifest.model_dump_json()
    )

    assert reparsed == manifest
    assert "src/ecomsre/product/jobs/worker.py" in manifest.source_sha256_by_path
    assert "src/ecomsre/product/incidents/read_backend.py" in (
        manifest.source_sha256_by_path
    )
    assert "src/ecomsre/product/pilot/diagnosis_recovery_v0233.py" in (
        manifest.source_sha256_by_path
    )
    assert "src/ecomsre/product/pilot/serialization_v0233.py" not in (
        manifest.source_sha256_by_path
    )
    assert manifest.source_count > len(manifest.entry_point_paths)


def _write_attempt_chain(
    root: Path,
    attempts: tuple[
        TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233, ...
    ],
) -> None:
    analysis = root / "docs/analysis"
    analysis.mkdir(parents=True)
    ledger = TrafficPreflightLedgerV0233.build(attempts=attempts)
    (analysis / "product-v0233-traffic-preflight-ledger.json").write_text(
        ledger.model_dump_json(), encoding="utf-8"
    )
    for attempt in attempts:
        path = analysis / (
            "product-v0233-traffic-preflight-attempt-"
            f"{attempt.attempt_ordinal}.json"
        )
        path.write_text(attempt.model_dump_json(), encoding="utf-8")


def _blocked_attempt(*, ordinal: int, prior: str | None, stage: str = "START"):
    changed = ALLOWED_REPAIR_SURFACES_V0233[0] if ordinal > 1 else None
    return TrafficPreflightBlockedAttemptV0233.build(
        attempt_id=f"20260901T03000{ordinal}Z-attempt",
        attempt_ordinal=ordinal,
        prior_attempt_sha256=prior,
        changed_surface=changed,
        changed_surface_sha256=_sha("a") if changed else None,
        attempt_consumed=True,
        failure_stage=stage,
        safe_error_type="RuntimeError",
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        profile_sha256=_sha("3"),
        traffic_contract_sha256=_sha("4"),
        source_state_before_sha256=_sha("5"),
        source_state_after_sha256=_sha("5"),
        demo_cleanup={
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        },
        product_cleanup={
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "database_owner_count_before": 0,
            "database_owner_count_after": 0,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
        },
    )


def test_later_attempt_requires_exact_changed_surface_snapshot(tmp_path: Path) -> None:
    first = _blocked_attempt(ordinal=1, prior=None)
    _write_attempt_chain(tmp_path, (first,))
    for path in ALLOWED_REPAIR_SURFACES_V0233:
        candidate = tmp_path / path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("before", encoding="utf-8")
    body = {
        "schema_version": "ecomsre.product.traffic-repair-surface.v0233",
        "phase": "POST_ATTEMPT_PRE_REPAIR",
        "attempt_ordinal": 1,
        "attempt_sha256": first.attempt_sha256,
        "allowed_surface_paths": list(ALLOWED_REPAIR_SURFACES_V0233),
        "source_sha256_by_path": {
            path: hashlib.sha256(b"before").hexdigest()
            for path in ALLOWED_REPAIR_SURFACES_V0233
        },
    }
    snapshot = TrafficRepairSurfaceSnapshotV0233.model_validate(
        {**body, "snapshot_sha256": semantic_sha256_v22(body)}
    )
    snapshot_path = (
        tmp_path
        / "docs/analysis/product-v0233-traffic-repair-surface-attempt-1.json"
    )
    snapshot_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    changed = tmp_path / ALLOWED_REPAIR_SURFACES_V0233[0]
    with pytest.raises(ValueError, match="identical"):
        _attempt_chain(
            tmp_path,
            prior_attempt=(
                tmp_path
                / "docs/analysis/product-v0233-traffic-preflight-attempt-1.json"
            ),
            changed_surface=changed,
        )
    changed.write_text("after", encoding="utf-8")
    chain = _attempt_chain(
        tmp_path,
        prior_attempt=(
            tmp_path
            / "docs/analysis/product-v0233-traffic-preflight-attempt-1.json"
        ),
        changed_surface=changed,
    )
    assert chain[1] == 2
    assert chain[3] == ALLOWED_REPAIR_SURFACES_V0233[0]


def test_later_attempt_rejects_unproven_prior_cleanup(tmp_path: Path) -> None:
    first = TrafficPreflightBlockedAttemptV0233.build(
        **{
            **_blocked_attempt(ordinal=1, prior=None).model_dump(
                mode="python", exclude={"attempt_sha256", "demo_cleanup"}
            ),
            "demo_cleanup": None,
        }
    )
    _write_attempt_chain(tmp_path, (first,))

    with pytest.raises(ValueError, match="CLEAN closure"):
        _attempt_chain(
            tmp_path,
            prior_attempt=(
                tmp_path
                / "docs/analysis/product-v0233-traffic-preflight-attempt-1.json"
            ),
            changed_surface=None,
        )


def test_recurring_failure_class_closes_another_attempt(tmp_path: Path) -> None:
    first = _blocked_attempt(ordinal=1, prior=None, stage="READINESS")
    second = _blocked_attempt(
        ordinal=2, prior=first.attempt_sha256, stage="READINESS"
    )
    _write_attempt_chain(tmp_path, (first, second))

    with pytest.raises(ValueError, match="recurring"):
        _attempt_chain(
            tmp_path,
            prior_attempt=(
                tmp_path
                / "docs/analysis/product-v0233-traffic-preflight-attempt-2.json"
            ),
            changed_surface=None,
        )
