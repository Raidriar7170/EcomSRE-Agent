"""Prepare and run the Product v0.2.2.2 active-profile connector smoke."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OpenSearchHoldoutVerificationReportV0222,
    OpenSearchNormalizationProfileV0222,
    OpenSearchOfflineProfileReportV0222,
    OpenSearchProfileStatusV0222,
    OpenSearchSelectedProfileFixtureV0222,
)
from ecomsre.product.connectors.opensearch_smoke_v0222 import (
    CONNECTOR_SMOKE_PASS_V0222,
    OpenSearchConnectorSmokeProfileV0222,
    build_connector_smoke_profile_v0222,
    evaluate_connector_smoke_v0222,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
)
from ecomsre.product.pilot.live_capture_v0222 import load_capture_profile_v0222
from ecomsre.product.contracts import ConnectorKindV1
from scripts.ci.verify_product_v0222_increment4 import (
    verify_product_v0222_increment4,
)
from scripts.product_v0222.prove_active_profile_restart import (
    run_active_profile_restart_proof_v0222,
)


def _write_public(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v0222.tmp"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v0222.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_private_once(path: Path, payload: Mapping[str, object]) -> str:
    body = dict(payload)
    body["report_sha256"] = semantic_sha256_v22(body)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        encoded = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return str(body["report_sha256"])


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_connector_smoke_v0222(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    verify_product_v0222_increment4(repository)
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    smoke_profile = build_connector_smoke_profile_v0222(active_profile=active)
    output = repository / "config/product-v0222/opensearch/smoke-profile.json"
    if output.exists():
        retained = OpenSearchConnectorSmokeProfileV0222.model_validate_json(
            output.read_text(encoding="utf-8")
        )
        if retained != smoke_profile:
            raise ValueError("Product v0.2.2.2 smoke profile differs")
    else:
        _write_public(output, smoke_profile.model_dump(mode="json"))
    return {
        "status": "ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE_PROFILE_READY",
        "active_profile_sha256": active.profile_sha256,
        "smoke_profile_sha256": smoke_profile.smoke_profile_sha256,
        "window_count": smoke_profile.window_count,
    }


def _three_windows(started_at: datetime, ended_at: datetime) -> tuple[ConnectorWindowV1, ...]:
    if started_at >= ended_at:
        raise ValueError("Product v0.2.2.2 smoke traffic window differs")
    interval = (ended_at - started_at) / 3
    windows: list[ConnectorWindowV1] = []
    for index in range(3):
        window_start = started_at + interval * index
        window_end = started_at + interval * (index + 1) - timedelta(microseconds=1)
        windows.append(
            ConnectorWindowV1(started_at=window_start, ended_at=window_end)
        )
    return tuple(windows)


def _placeholder_health(
    smoke_profile: OpenSearchConnectorSmokeProfileV0222,
) -> ConnectorHealthResultV1:
    connector = OpenSearchConnectorV1(
        smoke_profile.connector_config,
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=5,
    )
    try:
        capabilities = connector.capabilities()
    finally:
        connector.close()
    return ConnectorHealthResultV1(
        connector_name="logs",
        kind=ConnectorKindV1.OPENSEARCH,
        status=ConnectorAvailabilityV1.UNAVAILABLE,
        capabilities=capabilities,
        discovered_services=(),
        safe_error_code="CONNECTOR_UNAVAILABLE",
        latency_ms=0,
    )


def _render_smoke_markdown(
    payload: Mapping[str, object],
    *,
    restart_proof: Mapping[str, object] | None = None,
) -> str:
    restart_lines = (
        (
            f"- fresh consumer relaunch proof: `{restart_proof['terminal']}`",
            f"- restart proof SHA: `{restart_proof['proof_sha256']}`",
            "- live smoke reruns for restart proof: `0`",
        )
        if restart_proof is not None
        else ("- fresh consumer relaunch proof: `NOT_RUN`",)
    )
    return "\n".join(
        (
            "# Product v0.2.2.2 Connector Smoke",
            "",
            f"Terminal: `{payload['terminal']}`",
            "",
            f"- active profile SHA: `{payload['active_profile_sha256']}`",
            f"- OpenSearch verify: `{payload['connector_verify_status']}`",
            f"- query count: `{payload['query_count']} / 3`",
            f"- nonempty windows: `{payload['nonempty_window_count']}`",
            f"- accepted checkout records: `{payload['accepted_checkout_record_count']}`",
            f"- legacy smoke restart flag: `{payload['active_profile_survived_restart']}`",
            *restart_lines,
            f"- queue flag: `{payload['queue_flag_value']}`",
            f"- cleanup: `{payload['cleanup']}`",
            "- fault / Baseline / Product Diagnosis / Knowledge Loop: `0 / 0 / 0 / 0`",
            "- Agent writes / Runbooks: `0 / 0`",
            "",
        )
    )


def build_service_identity_binding_v0222(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    capture_profile = load_capture_profile_v0222(
        repository / "config/product-v0222/opensearch/profile.json"
    )
    smoke = json.loads(
        (
            repository / "docs/analysis/product-v0222-connector-smoke.json"
        ).read_text(encoding="utf-8")
    )
    diagnostics = smoke.get("query_diagnostics")
    if not isinstance(diagnostics, list):
        raise ValueError("Product v0.2.2.2 service identity diagnostics differ")
    successful_query_shas = tuple(
        item["query_result_sha256"]
        for item in diagnostics
        if isinstance(item, dict)
        and item.get("status") == "SUCCESS_NONEMPTY"
        and int(item.get("accepted_checkout_record_count", 0)) > 0
    )
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.service-identity-binding.v0222",
        "logical_service": "checkout",
        "configured_service_aliases": tuple(capture_profile.checkout_aliases),
        "service_source_field": active.service_source_field,
        "service_query_field": active.service_query_field,
        "successful_query_result_sha256": successful_query_shas,
        "successful_query_count": len(successful_query_shas),
        "accepted_checkout_record_count": smoke.get(
            "accepted_checkout_record_count"
        ),
        "connector_smoke_sha256": smoke.get("smoke_sha256"),
        "smoke_service_identity_sha256": smoke.get("service_identity_sha256"),
    }
    body["identity_sha256"] = semantic_sha256_v22(body)
    return body


def _build_handoff(
    *,
    root: Path,
    active: OpenSearchNormalizationProfileV0222,
    smoke: Mapping[str, object],
    restart_proof: Mapping[str, object],
    service_identity: Mapping[str, object],
) -> dict[str, object]:
    capture_profile = load_capture_profile_v0222(
        root / "config/product-v0222/opensearch/profile.json"
    )
    fixture = OpenSearchSelectedProfileFixtureV0222.model_validate_json(
        (
            root
            / "tests/fixtures/product_v0222/opensearch_selected_profile_shape.json"
        ).read_text(encoding="utf-8")
    )
    offline = OpenSearchOfflineProfileReportV0222.model_validate_json(
        (root / "docs/analysis/product-v0222-offline-profile.json").read_text(
            encoding="utf-8"
        )
    )
    holdout = OpenSearchHoldoutVerificationReportV0222.model_validate_json(
        (
            root / "docs/analysis/product-v0222-holdout-verification.json"
        ).read_text(encoding="utf-8")
    )
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.baseline-handoff.v0222",
        "status": "ECOMSRE_PRODUCT_V0222_BASELINE_HANDOFF_READY",
        "recommended_next_goal": (
            "Product v0.2.3 Fresh Baseline Readiness and No-Fault Acceptance"
        ),
        "active_normalization_profile_sha256": active.profile_sha256,
        "capture_bundle_sha256": active.capture_bundle_sha256,
        "candidate_set_sha256": active.candidate_set_sha256,
        "operator_decision_sha256": active.operator_decision_sha256,
        "sanitized_fixture_sha256": fixture.fixture_sha256,
        "offline_parser_report_sha256": offline.report_sha256,
        "holdout_verification_sha256": holdout.verification_sha256,
        "connector_smoke_sha256": smoke["smoke_sha256"],
        "active_profile_restart_proof_sha256": restart_proof["proof_sha256"],
        "service_identity_sha256": service_identity["identity_sha256"],
        "smoke_service_identity_sha256": smoke["service_identity_sha256"],
        "opensearch_capability_sha256": smoke["opensearch_capability_sha256"],
        "recommended_baseline_query_binding": {
            "logical_service": "checkout",
            "service_aliases": capture_profile.checkout_aliases,
            "service_source_field": active.service_source_field,
            "service_query_field": active.service_query_field,
            "timestamp_field": active.timestamp_extraction.extraction.paths[0],
            "message_field": active.message_extraction.extraction.paths[0],
            "severity_field": active.severity_extraction.extraction.paths[0],
            "maximum_record_rejection_fraction": (
                active.maximum_record_rejection_fraction
            ),
        },
        "known_limitations": (
            "No Baseline Readiness or Product Diagnosis was run in this Goal.",
            "The active profile is verified against the project-owned local Sandbox.",
            "Trace ID remains optional because one retained capture record omitted it.",
            "The current Product connector fails a window closed on any schema-invalid hit.",
        ),
        "baseline_readiness_attempt_count": 0,
        "fault_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    payload["handoff_sha256"] = semantic_sha256_v22(payload)
    return payload


def _render_handoff_markdown(payload: Mapping[str, object]) -> str:
    limitations = payload["known_limitations"]
    assert isinstance(limitations, (list, tuple))
    return "\n".join(
        (
            "# Product v0.2.2.2 Baseline Handoff",
            "",
            f"Status: `{payload['status']}`",
            "",
            f"- active profile SHA: `{payload['active_normalization_profile_sha256']}`",
            f"- capture bundle SHA: `{payload['capture_bundle_sha256']}`",
            f"- Candidate Set SHA: `{payload['candidate_set_sha256']}`",
            f"- operator decision SHA: `{payload['operator_decision_sha256']}`",
            f"- connector smoke SHA: `{payload['connector_smoke_sha256']}`",
            f"- active-profile restart proof SHA: `{payload['active_profile_restart_proof_sha256']}`",
            f"- service identity SHA: `{payload['service_identity_sha256']}`",
            f"- historical smoke identity SHA: `{payload['smoke_service_identity_sha256']}`",
            "",
            "## Known limitations",
            "",
            *(f"- {item}" for item in limitations),
            "",
            "Recommended next Goal: `Product v0.2.3 Fresh Baseline Readiness and No-Fault Acceptance`.",
            "",
        )
    )


def _update_increment5_progress(
    *,
    repository: Path,
    report: Mapping[str, object],
    cleanup: str,
    restart_proof: Mapping[str, object] | None = None,
    service_identity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    progress_path = repository / "docs/analysis/product-v0222-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress.pop("progress_sha256", None)
    progress.update(
        {
            "increment": 5,
            "connector_smoke_sha256": report["smoke_sha256"],
            "connector_smoke_terminal": report["terminal"],
            "connector_smoke_query_count": report["query_count"],
            "smoke_service_identity_sha256": report[
                "service_identity_sha256"
            ],
            "service_identity_sha256": (
                service_identity["identity_sha256"]
                if service_identity is not None
                else report["service_identity_sha256"]
            ),
            "opensearch_capability_sha256": report[
                "opensearch_capability_sha256"
            ],
            "cleanup": cleanup,
            "terminal": report["terminal"],
            "next_boundary": (
                "FINAL_REVIEW_CI_AND_MERGE"
                if report["terminal"] == CONNECTOR_SMOKE_PASS_V0222
                else "STOP_WITH_CONNECTOR_SMOKE_BLOCKER"
            ),
        }
    )
    if restart_proof is not None:
        progress["active_profile_restart_proof_sha256"] = restart_proof[
            "proof_sha256"
        ]
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    _write_public(progress_path, progress)
    return progress


def finalize_existing_connector_smoke_v0222(root: Path) -> dict[str, object]:
    """Add offline relaunch and identity bindings without repeating live smoke."""

    repository = root.resolve(strict=True)
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    smoke = json.loads(
        (
            repository / "docs/analysis/product-v0222-connector-smoke.json"
        ).read_text(encoding="utf-8")
    )
    if smoke.get("terminal") != CONNECTOR_SMOKE_PASS_V0222:
        raise ValueError("Product v0.2.2.2 offline finalization requires smoke PASS")
    restart_proof = run_active_profile_restart_proof_v0222(repository)
    service_identity = build_service_identity_binding_v0222(repository)
    _write_public(
        repository
        / "docs/analysis/product-v0222-service-identity-binding.json",
        service_identity,
    )
    handoff = _build_handoff(
        root=repository,
        active=active,
        smoke=smoke,
        restart_proof=restart_proof,
        service_identity=service_identity,
    )
    _write_public(
        repository / "docs/analysis/product-v0222-baseline-handoff.json",
        handoff,
    )
    _write_text(
        repository / "docs/analysis/product-v0222-baseline-handoff.md",
        _render_handoff_markdown(handoff),
    )
    _write_text(
        repository / "docs/analysis/product-v0222-connector-smoke.md",
        _render_smoke_markdown(smoke, restart_proof=restart_proof),
    )
    progress = _update_increment5_progress(
        repository=repository,
        report=smoke,
        cleanup=str(smoke["cleanup"]),
        restart_proof=restart_proof,
        service_identity=service_identity,
    )
    return {
        "restart_proof_terminal": restart_proof["terminal"],
        "connector_smoke_sha256": smoke["smoke_sha256"],
        "restart_proof_sha256": restart_proof["proof_sha256"],
        "service_identity_sha256": service_identity["identity_sha256"],
        "handoff_sha256": handoff["handoff_sha256"],
        "progress_sha256": progress["progress_sha256"],
        "live_smoke_rerun_count": 0,
    }


def run_connector_smoke_v0222(root: Path) -> dict[str, Any]:
    repository = root.resolve(strict=True)
    increment4 = verify_product_v0222_increment4(repository)
    smoke_profile = OpenSearchConnectorSmokeProfileV0222.model_validate_json(
        (
            repository / "config/product-v0222/opensearch/smoke-profile.json"
        ).read_text(encoding="utf-8")
    )
    active_path = (
        repository / "config/product-v0222/opensearch/normalization-profile.json"
    )
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        active_path.read_text(encoding="utf-8")
    )
    capture_profile = load_capture_profile_v0222(
        repository / "config/product-v0222/opensearch/profile.json"
    )
    if (
        increment4["status"]
        != "ECOMSRE_PRODUCT_V0222_HOLDOUT_VERIFICATION_PASS"
        or active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or smoke_profile.active_profile_sha256 != active.profile_sha256
    ):
        raise ValueError("Product v0.2.2.2 connector smoke preflight differs")
    private_root = repository / smoke_profile.private_root
    start_path = private_root / "connector-smoke-start.json"
    completion_path = private_root / "connector-smoke-complete.json"
    protected = (
        start_path,
        completion_path,
        repository / "docs/analysis/product-v0222-connector-smoke.json",
        repository / "docs/analysis/product-v0222-baseline-handoff.json",
        repository
        / "docs/analysis/product-v0222-active-profile-restart-proof.json",
        repository
        / "docs/analysis/product-v0222-service-identity-binding.json",
    )
    if any(path.exists() for path in protected):
        raise ValueError("Product v0.2.2.2 connector smoke is already consumed")
    profile_file_sha_before = _file_sha256(active_path)
    _write_private_once(
        start_path,
        {
            "schema_version": "ecomsre.product.opensearch-connector-smoke-start.v0222",
            "session_id": smoke_profile.session_id,
            "started_at": datetime.now(UTC).isoformat(),
            "smoke_profile_sha256": smoke_profile.smoke_profile_sha256,
            "active_profile_sha256": active.profile_sha256,
            "window_count": 3,
            "queue_flag_value": 0,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=repository,
        private_root=private_root,
        stabilization_seconds=capture_profile.stabilization_seconds,
    )
    cleanup = CleanupObservation.unknown_blocked()
    baseline_before: str | None = None
    baseline_unchanged = False
    queue_flag_value = -1
    started = False
    failure: BaseException | None = None
    connector_health = _placeholder_health(smoke_profile)
    query_results: tuple[ConnectorQueryResultV1, ...] = ()
    traffic_result_sha256 = "0" * 64
    traffic_attempted = traffic_succeeded = 0
    try:
        lifecycle.admit()
        lifecycle.start()
        started = True
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise TypeError("Product v0.2.2.2 connector smoke lacks read authority")
        if backend.config.opensearch_base_url != smoke_profile.connector_config.endpoint:
            raise ValueError("Product v0.2.2.2 smoke endpoint binding differs")
        baseline_before = lifecycle.read_baseline_sha256()
        queue_flag_value = int(lifecycle.bundle.scenario.baseline_value)
        connector = OpenSearchConnectorV1(
            smoke_profile.connector_config,
            credential_resolver=CredentialResolverV1(environment={}),
            timeout_seconds=5,
        )
        try:
            connector_health = connector.verify()
        finally:
            connector.close()
        if connector_health.status is not ConnectorAvailabilityV1.AVAILABLE:
            raise RuntimeError("Product v0.2.2.2 OpenSearch verify unavailable")
        traffic_started = datetime.now(UTC)
        with httpx.Client(trust_env=False) as traffic_client:
            traffic = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=smoke_profile.healthy_traffic_profile,
            )
        traffic_ended = datetime.now(UTC)
        traffic_result_sha256 = traffic.result_sha256
        traffic_attempted = traffic.attempted
        traffic_succeeded = traffic.succeeded
        if traffic.attempted != 30 or traffic.succeeded != 30:
            raise RuntimeError("Product v0.2.2.2 connector smoke traffic failed")
        if smoke_profile.index_settle_seconds:
            time.sleep(smoke_profile.index_settle_seconds)
        reloaded_profile = OpenSearchNormalizationProfileV0222.model_validate_json(
            active_path.read_text(encoding="utf-8")
        )
        reloaded_smoke = OpenSearchConnectorSmokeProfileV0222.model_validate_json(
            (
                repository / "config/product-v0222/opensearch/smoke-profile.json"
            ).read_text(encoding="utf-8")
        )
        if (
            reloaded_profile != active
            or reloaded_smoke != smoke_profile
            or reloaded_smoke.active_profile_sha256 != reloaded_profile.profile_sha256
        ):
            raise RuntimeError("Product v0.2.2.2 active profile restart differs")
        query_connector = OpenSearchConnectorV1(
            reloaded_smoke.connector_config,
            credential_resolver=CredentialResolverV1(environment={}),
            timeout_seconds=5,
        )
        results: list[ConnectorQueryResultV1] = []
        try:
            for window in _three_windows(traffic_started, traffic_ended):
                context = ConnectorQueryContextV1(
                    environment_id="env-" + "0" * 24,
                    requested_services=("checkout",),
                    service_aliases={
                        alias: "checkout" for alias in capture_profile.checkout_aliases
                    },
                    window=window,
                    maximum_records=smoke_profile.maximum_records_per_window,
                )
                results.append(query_connector.query(context)[0])
        finally:
            query_connector.close()
        query_results = tuple(results)
        baseline_unchanged = lifecycle.read_baseline_sha256() == baseline_before
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
    except BaseException as error:
        failure = error
    finally:
        if started and cleanup.verdict != "CLEAN":
            if baseline_before is not None and lifecycle.controller is not None:
                try:
                    baseline_unchanged = (
                        lifecycle.read_baseline_sha256() == baseline_before
                    )
                except BaseException:
                    baseline_unchanged = False
            try:
                cleanup = lifecycle.cleanup_owned(
                    baseline_unchanged=baseline_unchanged
                )
            except BaseException:
                cleanup = CleanupObservation.unknown_blocked()
    profile_file_sha_after = _file_sha256(active_path)
    report = evaluate_connector_smoke_v0222(
        smoke_profile=smoke_profile,
        active_profile=active,
        connector_health=connector_health,
        query_results=query_results,
        active_profile_file_sha256_before=profile_file_sha_before,
        active_profile_file_sha256_after=profile_file_sha_after,
        healthy_traffic_result_sha256=traffic_result_sha256,
        healthy_traffic_attempted=traffic_attempted,
        healthy_traffic_succeeded=traffic_succeeded,
        queue_flag_value=queue_flag_value,
        baseline_unchanged=baseline_unchanged,
        cleanup=cleanup.verdict,
    )
    if failure is not None and report.terminal == CONNECTOR_SMOKE_PASS_V0222:
        raise RuntimeError("Product v0.2.2.2 connector smoke failure escaped")
    completion_sha256 = _write_private_once(
        completion_path,
        {
            "schema_version": "ecomsre.product.opensearch-connector-smoke-complete.v0222",
            "session_id": smoke_profile.session_id,
            "completed_at": datetime.now(UTC).isoformat(),
            "terminal": report.terminal,
            "safe_message": None if failure is None else str(failure)[:240],
            "smoke_sha256": report.smoke_sha256,
            "query_count": report.query_count,
            "baseline_unchanged": baseline_unchanged,
            "cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    smoke_payload = report.model_dump(mode="json")
    _write_public(
        repository / "docs/analysis/product-v0222-connector-smoke.json",
        smoke_payload,
    )
    finalization: dict[str, object] = {}
    if report.terminal == CONNECTOR_SMOKE_PASS_V0222:
        finalization = finalize_existing_connector_smoke_v0222(repository)
    else:
        _write_text(
            repository / "docs/analysis/product-v0222-connector-smoke.md",
            _render_smoke_markdown(smoke_payload),
        )
        _update_increment5_progress(
            repository=repository,
            report=smoke_payload,
            cleanup=cleanup.verdict,
        )
    return {
        "status": report.terminal,
        "smoke_sha256": report.smoke_sha256,
        "query_count": report.query_count,
        "nonempty_window_count": report.nonempty_window_count,
        "accepted_checkout_record_count": report.accepted_checkout_record_count,
        "active_profile_survived_restart": (
            report.active_profile_survived_restart
        ),
        "service_identity_sha256": report.service_identity_sha256,
        "opensearch_capability_sha256": report.opensearch_capability_sha256,
        "completion_sha256": completion_sha256,
        "cleanup": report.cleanup,
        **finalization,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute-live", action="store_true")
    mode.add_argument("--finalize-offline", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare:
        result = prepare_connector_smoke_v0222(args.project_root)
    elif args.execute_live:
        result = run_connector_smoke_v0222(args.project_root)
    else:
        result = finalize_existing_connector_smoke_v0222(args.project_root)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "prepare_connector_smoke_v0222",
    "build_service_identity_binding_v0222",
    "finalize_existing_connector_smoke_v0222",
    "run_connector_smoke_v0222",
    "main",
)
