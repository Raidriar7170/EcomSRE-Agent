"""Verify the Product v0.2.2.2 connector smoke and Baseline handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
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
    OpenSearchConnectorSmokeReportV0222,
    OpenSearchWindowStatusV0222,
)
from scripts.ci.verify_product_v0222_increment4 import (
    verify_product_v0222_increment4,
)
from scripts.product_v0222.prove_active_profile_restart import (
    RESTART_PROOF_PASS_V0222,
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.2.2 artifact is not an object: {path}")
    return payload


def _verify_digest(payload: Mapping[str, object], field: str) -> None:
    expected = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != field}
    )
    if payload.get(field) != expected:
        raise ValueError(f"Product v0.2.2.2 {field} differs")


def verify_product_v0222_increment5(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    increment4 = verify_product_v0222_increment4(repository)
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            repository
            / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    smoke_profile = OpenSearchConnectorSmokeProfileV0222.model_validate_json(
        (
            repository / "config/product-v0222/opensearch/smoke-profile.json"
        ).read_text(encoding="utf-8")
    )
    smoke = OpenSearchConnectorSmokeReportV0222.model_validate_json(
        (
            repository / "docs/analysis/product-v0222-connector-smoke.json"
        ).read_text(encoding="utf-8")
    )
    fixture = OpenSearchSelectedProfileFixtureV0222.model_validate_json(
        (
            repository
            / "tests/fixtures/product_v0222/opensearch_selected_profile_shape.json"
        ).read_text(encoding="utf-8")
    )
    offline = OpenSearchOfflineProfileReportV0222.model_validate_json(
        (repository / "docs/analysis/product-v0222-offline-profile.json").read_text(
            encoding="utf-8"
        )
    )
    holdout = OpenSearchHoldoutVerificationReportV0222.model_validate_json(
        (
            repository / "docs/analysis/product-v0222-holdout-verification.json"
        ).read_text(encoding="utf-8")
    )
    restart_proof = _load(
        repository
        / "docs/analysis/product-v0222-active-profile-restart-proof.json"
    )
    service_identity = _load(
        repository
        / "docs/analysis/product-v0222-service-identity-binding.json"
    )
    handoff = _load(repository / "docs/analysis/product-v0222-baseline-handoff.json")
    progress = _load(repository / "docs/analysis/product-v0222-progress.json")
    _verify_digest(restart_proof, "proof_sha256")
    _verify_digest(service_identity, "identity_sha256")
    _verify_digest(handoff, "handoff_sha256")
    _verify_digest(progress, "progress_sha256")

    if (
        increment4["status"]
        != "ECOMSRE_PRODUCT_V0222_HOLDOUT_VERIFICATION_PASS"
        or active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or smoke_profile.active_profile_sha256 != active.profile_sha256
        or smoke.smoke_profile_sha256 != smoke_profile.smoke_profile_sha256
        or smoke.active_profile_sha256 != active.profile_sha256
    ):
        raise ValueError("Product v0.2.2.2 connector smoke profile binding differs")
    if (
        smoke.terminal != CONNECTOR_SMOKE_PASS_V0222
        or smoke.connector_verify_status != "AVAILABLE"
        or smoke.query_count != 3
        or smoke.nonempty_window_count != 3
        or smoke.accepted_checkout_record_count != 15
        or not smoke.active_profile_survived_restart
        or smoke.active_profile_file_sha256_before
        != smoke.active_profile_file_sha256_after
        or smoke.healthy_traffic_attempted != 30
        or smoke.healthy_traffic_succeeded != 30
        or smoke.queue_flag_value != 0
        or not smoke.baseline_unchanged
        or smoke.cleanup != "CLEAN"
        or any(
            value != 0
            for value in (
                smoke.outer_schema_failure_count,
                smoke.all_records_rejected_failure_count,
                smoke.service_alias_unmapped_count,
                smoke.timestamp_parse_failure_count,
            )
        )
        or any(
            item.status is not OpenSearchWindowStatusV0222.SUCCESS_NONEMPTY
            or not item.query_completed
            or item.returned_record_count != 5
            or item.accepted_checkout_record_count != 5
            or item.rejected_record_count != 0
            or item.rejection_fraction != 0
            for item in smoke.query_diagnostics
        )
    ):
        raise ValueError("Product v0.2.2.2 connector smoke terminal differs")
    reloaded_connector = OpenSearchConnectorV1(
        smoke_profile.connector_config,
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=5,
    )
    try:
        reloaded_capabilities = tuple(
            item.model_dump(mode="json")
            for item in reloaded_connector.capabilities()
        )
    finally:
        reloaded_connector.close()
    reloaded_capability_body = {
        "schema_version": (
            "ecomsre.product.opensearch-reloaded-capabilities.v0222"
        ),
        "connector_name": smoke_profile.connector_config.name,
        "capabilities": reloaded_capabilities,
    }
    child_payload = {
        "terminal": "ECOMSRE_PRODUCT_V0222_ACTIVE_PROFILE_CONSUMER_RELOADED",
        "child_pid": restart_proof.get("child_pid"),
        "active_profile_sha256": restart_proof.get("active_profile_sha256"),
        "active_profile_file_sha256": restart_proof.get(
            "active_profile_file_sha256"
        ),
        "smoke_profile_sha256": restart_proof.get("smoke_profile_sha256"),
        "smoke_profile_file_sha256": restart_proof.get(
            "smoke_profile_file_sha256"
        ),
        "connector_config_sha256": restart_proof.get(
            "connector_config_sha256"
        ),
        "reloaded_capabilities_sha256": restart_proof.get(
            "reloaded_capabilities_sha256"
        ),
        "network_request_count": 0,
    }
    if (
        restart_proof.get("terminal") != RESTART_PROOF_PASS_V0222
        or restart_proof.get("process_relation")
        != "DISTINCT_CONSUMER_PROCESS"
        or not isinstance(restart_proof.get("parent_pid"), int)
        or not isinstance(restart_proof.get("child_pid"), int)
        or restart_proof.get("parent_pid") == restart_proof.get("child_pid")
        or restart_proof.get("active_profile_sha256") != active.profile_sha256
        or restart_proof.get("active_profile_file_sha256")
        != smoke.active_profile_file_sha256_after
        or restart_proof.get("smoke_profile_sha256")
        != smoke_profile.smoke_profile_sha256
        or restart_proof.get("connector_config_sha256")
        != semantic_sha256_v22(
            smoke_profile.connector_config.model_dump(mode="json")
        )
        or restart_proof.get("reloaded_capabilities_sha256")
        != semantic_sha256_v22(reloaded_capability_body)
        or restart_proof.get("connector_smoke_sha256") != smoke.smoke_sha256
        or restart_proof.get("live_opensearch_capability_sha256")
        != smoke.opensearch_capability_sha256
        or restart_proof.get("network_request_count") != 0
        or restart_proof.get("live_smoke_rerun_count") != 0
        or restart_proof.get("action_authority") != "NONE"
        or restart_proof.get("child_payload_sha256")
        != semantic_sha256_v22(child_payload)
    ):
        raise ValueError("Product v0.2.2.2 active-profile restart proof differs")
    successful_query_shas = [
        item.query_result_sha256
        for item in smoke.query_diagnostics
        if item.status is OpenSearchWindowStatusV0222.SUCCESS_NONEMPTY
        and item.accepted_checkout_record_count > 0
    ]
    if (
        service_identity.get("logical_service") != "checkout"
        or service_identity.get("configured_service_aliases")
        != ["checkout", "checkoutservice"]
        or service_identity.get("service_source_field")
        != active.service_source_field
        or service_identity.get("service_query_field")
        != active.service_query_field
        or service_identity.get("successful_query_result_sha256")
        != successful_query_shas
        or service_identity.get("successful_query_count") != 3
        or service_identity.get("accepted_checkout_record_count") != 15
        or service_identity.get("connector_smoke_sha256") != smoke.smoke_sha256
        or service_identity.get("smoke_service_identity_sha256")
        != smoke.service_identity_sha256
    ):
        raise ValueError("Product v0.2.2.2 service identity binding differs")
    expected_handoff = {
        "status": "ECOMSRE_PRODUCT_V0222_BASELINE_HANDOFF_READY",
        "active_normalization_profile_sha256": active.profile_sha256,
        "capture_bundle_sha256": active.capture_bundle_sha256,
        "candidate_set_sha256": active.candidate_set_sha256,
        "operator_decision_sha256": active.operator_decision_sha256,
        "sanitized_fixture_sha256": fixture.fixture_sha256,
        "offline_parser_report_sha256": offline.report_sha256,
        "holdout_verification_sha256": holdout.verification_sha256,
        "connector_smoke_sha256": smoke.smoke_sha256,
        "active_profile_restart_proof_sha256": restart_proof["proof_sha256"],
        "service_identity_sha256": service_identity["identity_sha256"],
        "smoke_service_identity_sha256": smoke.service_identity_sha256,
        "opensearch_capability_sha256": smoke.opensearch_capability_sha256,
        "baseline_readiness_attempt_count": 0,
        "fault_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    if any(handoff.get(key) != value for key, value in expected_handoff.items()):
        raise ValueError("Product v0.2.2.2 Baseline handoff binding differs")
    binding = handoff.get("recommended_baseline_query_binding")
    if not isinstance(binding, dict) or binding.get("service_aliases") != [
        "checkout",
        "checkoutservice",
    ]:
        raise ValueError("Product v0.2.2.2 Baseline query binding differs")
    limitations = handoff.get("known_limitations")
    if (
        not isinstance(limitations, list)
        or len(limitations) < 4
        or not any("No Baseline Readiness" in item for item in limitations)
    ):
        raise ValueError("Product v0.2.2.2 known limitations differ")
    zero_fields = (
        "fault_attempt_count",
        "baseline_readiness_attempt_count",
        "product_diagnosis_attempt_count",
        "knowledge_loop_campaign_count",
        "agent_writes",
        "runbook_executions",
    )
    if (
        progress.get("increment") != 5
        or progress.get("terminal") != CONNECTOR_SMOKE_PASS_V0222
        or progress.get("connector_smoke_terminal") != CONNECTOR_SMOKE_PASS_V0222
        or progress.get("connector_smoke_sha256") != smoke.smoke_sha256
        or progress.get("connector_smoke_query_count") != 3
        or progress.get("active_profile_restart_proof_sha256")
        != restart_proof["proof_sha256"]
        or progress.get("service_identity_sha256")
        != service_identity["identity_sha256"]
        or progress.get("smoke_service_identity_sha256")
        != smoke.service_identity_sha256
        or progress.get("opensearch_capability_sha256")
        != smoke.opensearch_capability_sha256
        or progress.get("next_boundary") != "FINAL_REVIEW_CI_AND_MERGE"
        or progress.get("cleanup") != "CLEAN"
        or progress.get("action_authority") != "NONE"
        or any(progress.get(field) != 0 for field in zero_fields)
    ):
        raise ValueError("Product v0.2.2.2 Increment 5 progress differs")
    smoke_markdown = (
        repository / "docs/analysis/product-v0222-connector-smoke.md"
    ).read_text(encoding="utf-8")
    handoff_markdown = (
        repository / "docs/analysis/product-v0222-baseline-handoff.md"
    ).read_text(encoding="utf-8")
    if (
        smoke.terminal not in smoke_markdown
        or RESTART_PROOF_PASS_V0222 not in smoke_markdown
        or str(smoke.smoke_sha256) not in json.dumps(handoff)
        or str(restart_proof["proof_sha256"]) not in handoff_markdown
        or "No Baseline Readiness" not in handoff_markdown
    ):
        raise ValueError("Product v0.2.2.2 Increment 5 Markdown differs")

    private_validation = "TRACKED_EVIDENCE_ONLY"
    private_root = repository / smoke_profile.private_root
    if private_root.exists():
        start = _load(private_root / "connector-smoke-start.json")
        completion = _load(private_root / "connector-smoke-complete.json")
        _verify_digest(start, "report_sha256")
        _verify_digest(completion, "report_sha256")
        if (
            completion.get("terminal") != smoke.terminal
            or completion.get("smoke_sha256") != smoke.smoke_sha256
            or completion.get("query_count") != 3
            or completion.get("baseline_unchanged") is not True
            or completion.get("cleanup") != "CLEAN"
        ):
            raise ValueError("Product v0.2.2.2 private smoke binding differs")
        private_validation = "PRIVATE_SMOKE_VERIFIED"
    return {
        "status": smoke.terminal,
        "query_count": smoke.query_count,
        "nonempty_window_count": smoke.nonempty_window_count,
        "accepted_checkout_record_count": smoke.accepted_checkout_record_count,
        "active_profile_survived_restart": smoke.active_profile_survived_restart,
        "restart_proof_terminal": restart_proof["terminal"],
        "restart_proof_sha256": restart_proof["proof_sha256"],
        "live_smoke_rerun_count": restart_proof["live_smoke_rerun_count"],
        "smoke_sha256": smoke.smoke_sha256,
        "handoff_sha256": handoff["handoff_sha256"],
        "service_identity_sha256": service_identity["identity_sha256"],
        "successful_identity_query_count": service_identity[
            "successful_query_count"
        ],
        "opensearch_capability_sha256": smoke.opensearch_capability_sha256,
        "baseline_readiness_attempt_count": 0,
        "cleanup": smoke.cleanup,
        "private_validation": private_validation,
    }


def main() -> int:
    print(
        json.dumps(
            verify_product_v0222_increment5(Path.cwd()),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0222_increment5",)
