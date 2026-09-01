from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
    FreshFormalSourceCandidateV0233,
    FreshFormalSourceKindV0233,
    FreshFormalSourceSelectionErrorV0233,
    admit_fresh_formal_source_v0233,
    clone_fresh_formal_state_v0233,
    select_fresh_formal_source_v0233,
)
from ecomsre.product.pilot.product_state_clone_v0232 import (
    admit_product_state_source_v0232,
)
from ecomsre.product.pilot.repository_state_v0233 import RepositoryPhaseV0233
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
)
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02
from ecomsre.product.storage.migrations import MIGRATIONS
from scripts.ci.verify_product_v0233_history import (
    HISTORY_AND_HANDOFF_PASS_V0233,
    verify_product_v0233_history,
)


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_ID = "env-" + "1" * 24
BASELINE_ID = "base-" + "2" * 24
BASELINE_SHA256 = "b" * 64
PROFILE_SHA256 = "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_source_state(root: Path, *, schema_version: int) -> Path:
    root.mkdir(parents=True)
    database = root / "product.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for version, name, statements in MIGRATIONS:
            if version > schema_version:
                break
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, f"2026-08-30T00:00:0{version}+00:00"),
            )

        identity_policy = {
            "schema_version": "ecomsre.product.service-identity-policy.v1",
            "canonical_field": "service.name",
            "prometheus_label": "service_name",
            "opensearch_field": "resource.attributes.service.name",
            "jaeger_service_field": "serviceName",
            "health_service_field": "service",
            "services": [
                {
                    "logical_service": "checkout",
                    "aliases": {
                        "prometheus": ["checkout"],
                        "opensearch": ["checkout"],
                        "jaeger": ["checkout"],
                        "http_health": ["checkout"],
                    },
                    "approved_many_to_one": False,
                }
            ],
        }
        connection.execute(
            "INSERT INTO environments(environment_id, name, description, timezone, "
            "service_identity_policy_json, explicit_service_catalog_json, created_at, "
            "updated_at) VALUES (?, 'fixture', '', 'UTC', ?, ?, ?, ?)",
            (
                ENVIRONMENT_ID,
                _json(identity_policy),
                _json(["checkout"]),
                "2026-08-30T00:00:00+00:00",
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO connector_configs(connector_config_id, environment_id, name, "
            "kind, endpoint, settings_json, credential_refs_json, created_at) "
            "VALUES ('conn-opensearch', ?, 'opensearch', 'OPENSEARCH', NULL, ?, '[]', ?)",
            (
                ENVIRONMENT_ID,
                _json(
                    {
                        "mode": "PROFILE_BOUND",
                        "profile_binding": {
                            "profile_status": "ACTIVE",
                            "selected_candidate_alias": "P01",
                            "profile_sha256": PROFILE_SHA256,
                        },
                    }
                ),
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO baseline_versions(baseline_id, environment_id, payload_json, "
            "active, created_at) VALUES (?, ?, ?, 1, ?)",
            (
                BASELINE_ID,
                ENVIRONMENT_ID,
                _json(
                    {
                        "baseline_id": BASELINE_ID,
                        "baseline_sha256": BASELINE_SHA256,
                        "environment_id": ENVIRONMENT_ID,
                    }
                ),
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO services(service_id, environment_id, payload_json, created_at, "
            "logical_service) VALUES (?, ?, ?, ?, 'checkout')",
            (
                "svc-" + "3" * 24,
                ENVIRONMENT_ID,
                _json(
                    {
                        "schema_version": "ecomsre.product.service-identity.v1",
                        "service_id": "svc-" + "3" * 24,
                        "logical_service": "checkout",
                        "aliases": {
                            "prometheus": ["checkout"],
                            "opensearch": ["checkout"],
                            "jaeger": ["checkout"],
                            "http_health": ["checkout"],
                        },
                    }
                ),
                "2026-08-30T00:00:00+00:00",
            ),
        )
        jobs = (
            ("job-build", "BASELINE_BUILD"),
            ("job-verify", "ENVIRONMENT_VERIFY"),
            ("job-diagnosis", "DIAGNOSIS"),
        )
        for ordinal, (job_id, job_type) in enumerate(jobs, start=1):
            connection.execute(
                "INSERT INTO diagnosis_jobs(job_id, job_type, status, payload_json, "
                "result_json, safe_error_code, idempotency_key, claimed_by, "
                "lease_expires_at, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'SUCCEEDED', '{}', '{}', NULL, ?, NULL, NULL, 1, ?, ?)",
                (job_id, job_type, f"fixture-{job_type}", float(ordinal), float(ordinal)),
            )
        connection.execute(
            "INSERT INTO incidents(incident_id, environment_id, external_incident_key, "
            "payload_json, created_at) VALUES ('inc-fixture', ?, 'fixture', '{}', ?)",
            (ENVIRONMENT_ID, "2026-08-30T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO diagnosis_results(diagnosis_id, incident_id, payload_json, "
            "created_at) VALUES ('diag-fixture', 'inc-fixture', '{}', ?)",
            ("2026-08-30T00:00:00+00:00",),
        )

        capability_body = {
            "schema_version": "ecomsre.product.environment-capability-matrix.v1",
            "environment_id": ENVIRONMENT_ID,
            "logical_services": ["checkout"],
            "sources": [],
            "mechanisms": [],
            "no_incident_eligible": False,
            "effective_policy_sha256": "e" * 64,
            "verified_at": "2026-08-30T00:00:00Z",
        }
        capability = EnvironmentCapabilityMatrixV1.model_validate(
            {
                **capability_body,
                "capability_sha256": semantic_sha256_v22(capability_body),
            }
        )
        connection.execute(
            "INSERT INTO environment_capability_matrices(environment_id, payload_json, "
            "created_at) VALUES (?, ?, ?)",
            (
                ENVIRONMENT_ID,
                _json(capability.model_dump(mode="json")),
                "2026-08-30T00:00:00+00:00",
            ),
        )

        object_bytes = b'{"kind":"fixture-evidence","value":1}'
        object_sha256 = hashlib.sha256(object_bytes).hexdigest()
        connection.execute(
            "INSERT INTO evidence_objects(object_sha256, byte_size, media_type, "
            "created_at) VALUES (?, ?, 'application/json', ?)",
            (object_sha256, len(object_bytes), "2026-08-30T00:00:00+00:00"),
        )
        connection.commit()
    finally:
        connection.close()

    object_path = root / "objects" / "sha256" / object_sha256[:2] / f"{object_sha256}.json"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(object_bytes)

    pilot = root / "pilot"
    pilot.mkdir()
    runtime_authority = PilotRuntimeAuthorityV02.build(
        environment_id=ENVIRONMENT_ID,
        allowed_logical_services=("checkout",),
        profile_sha256="c" * 64,
        daemon_identity_sha256="d" * 64,
        docker_context_sha256="e" * 64,
        config_bundle_sha256="f" * 64,
        resolved_sandbox_sha256="1" * 64,
        resolved_endpoints_sha256="2" * 64,
        ownership_scope_sha256="3" * 64,
    )
    runtime_snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=ENVIRONMENT_ID,
        authority_sha256=runtime_authority.connector_binding_sha256,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        services={
            "checkout": {"state": "RUNNING", "healthy": True, "restart_count": 0}
        },
    )
    (pilot / "runtime-authority.json").write_text(
        _json(runtime_authority.model_dump(mode="json")), encoding="utf-8"
    )
    (pilot / "runtime-readiness.json").write_text(
        _json(runtime_snapshot.model_dump(mode="json")), encoding="utf-8"
    )
    return root


def _candidate(
    root: Path,
    *,
    kind: FreshFormalSourceKindV0233,
    locator: str,
) -> FreshFormalSourceCandidateV0233:
    prior = admit_product_state_source_v0232(root, source_locator=locator)
    context = None
    state_sha256 = prior.source_sha256
    if kind is FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION:
        context = {
            "schema8_definition_sha256": "4" * 64,
            "formal_delta_sha256": "5" * 64,
            "source_projection_sha256": "6" * 64,
        }
        state_sha256 = semantic_sha256_v22(
            {
                "schema8_definition_sha256": context["schema8_definition_sha256"],
                "formal_delta_sha256": context["formal_delta_sha256"],
                "database_logical_sha256": prior.source_database_logical_sha256,
                "object_inventory_sha256": prior.source_object_inventory_sha256,
                "runtime_file_inventory_sha256": (
                    prior.source_runtime_file_inventory_sha256
                ),
                "source_projection_sha256": context["source_projection_sha256"],
            }
        )
    return FreshFormalSourceCandidateV0233(
        source_kind=kind,
        source_root=root,
        source_locator=locator,
        source_schema_version=(
            7 if kind is FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE else 8
        ),
        source_database_file_sha256=prior.source_database_file_sha256,
        source_database_logical_sha256=prior.source_database_logical_sha256,
        source_product_state_sha256=state_sha256,
        source_semantic_context=context,
        source_object_inventory_sha256=prior.source_object_inventory_sha256,
        source_runtime_inventory_sha256=prior.source_runtime_file_inventory_sha256,
        active_environment_id=prior.source_environment_id,
        active_baseline_id=prior.source_active_baseline_id,
        active_baseline_sha256=prior.source_active_baseline_sha256,
        active_profile_sha256=prior.source_profile_sha256,
    )


def test_history_and_handoff_bindings_are_exact() -> None:
    result = verify_product_v0233_history(ROOT)

    assert result["terminal"] == HISTORY_AND_HANDOFF_PASS_V0233
    assert result["starting_main"] == "6e07964e5595b4138decf0276189c76c3e278d87"
    assert result["merged_pull_request"] == 85
    assert result["handoff_sha256"] == (
        "72d272951412d696d50fb6ee44c96bbc4a1a6e5ace63d574b0636297b848847f"
    )
    assert result["measured_nofault_authority"] == "NONE"


def test_history_rejects_a_resealed_authority_drift(tmp_path: Path) -> None:
    source = ROOT / "config/product-v0233/historical-results.v1.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["predecessor"]["measured_nofault_authority"] = "MEASURED"
    body = dict(payload)
    body.pop("manifest_sha256")
    payload["manifest_sha256"] = semantic_sha256_v22(body)
    changed = tmp_path / "historical-results.v1.json"
    changed.write_text(_json(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="historical manifest differs"):
        verify_product_v0233_history(ROOT, manifest_path=changed)


def test_source_selection_prefers_pristine_and_falls_back_only_after_failure(
    tmp_path: Path,
) -> None:
    preferred_root = _build_source_state(tmp_path / "preferred", schema_version=7)
    fallback_root = _build_source_state(tmp_path / "fallback", schema_version=8)
    preferred = _candidate(
        preferred_root,
        kind=FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE,
        locator=".local/product-v023/preferred/product",
    )
    fallback = _candidate(
        fallback_root,
        kind=FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION,
        locator=".local/product-v02323/fallback/product",
    )

    selection = select_fresh_formal_source_v0233(
        preferred=preferred,
        fallback=fallback,
        owner_counter=lambda _database: 0,
    )
    assert selection.source_kind is FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE
    assert selection.selection_reason == "PREFERRED_SOURCE_ADMITTED"

    preferred_drift = preferred.model_copy(
        update={"source_database_file_sha256": "0" * 64}
    )
    fallback_selection = select_fresh_formal_source_v0233(
        preferred=preferred_drift,
        fallback=fallback,
        owner_counter=lambda _database: 0,
    )
    assert (
        fallback_selection.source_kind
        is FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION
    )
    assert fallback_selection.selection_reason == "PREFERRED_REJECTED_FALLBACK_ADMITTED"


def test_source_admission_rejects_owner_symlink_and_schema9(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source", schema_version=7)
    candidate = _candidate(
        source_root,
        kind=FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE,
        locator=".local/product-v023/source/product",
    )
    with pytest.raises(FreshFormalSourceSelectionErrorV0233, match="owner"):
        admit_fresh_formal_source_v0233(candidate, owner_counter=lambda _database: 1)

    linked = tmp_path / "linked"
    linked.symlink_to(source_root, target_is_directory=True)
    with pytest.raises(FreshFormalSourceSelectionErrorV0233, match="symlink"):
        admit_fresh_formal_source_v0233(
            candidate.model_copy(update={"source_root": linked}),
            owner_counter=lambda _database: 0,
        )

    schema9 = _build_source_state(tmp_path / "schema9", schema_version=9)
    schema9_candidate = _candidate(
        schema9,
        kind=FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE,
        locator=".local/product-v023/schema9/product",
    ).model_copy(update={"source_schema_version": 9})
    with pytest.raises(FreshFormalSourceSelectionErrorV0233, match="schema"):
        admit_fresh_formal_source_v0233(
            schema9_candidate,
            owner_counter=lambda _database: 0,
        )


def test_clone_uses_online_backup_and_migrates_only_the_clone(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source", schema_version=7)
    candidate = _candidate(
        source_root,
        kind=FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE,
        locator=".local/product-v023/source/product",
    )
    selection = admit_fresh_formal_source_v0233(
        candidate, owner_counter=lambda _database: 0
    )
    source_before = hashlib.sha256((source_root / "product.sqlite3").read_bytes()).hexdigest()
    destination = tmp_path / "clone" / "product"

    clone = clone_fresh_formal_state_v0233(
        selection=selection,
        source_root=source_root,
        destination_root=destination,
        destination_locator=".local/product-v0233/test-state/fixture/product",
        owner_counter=lambda _database: 0,
    )

    assert clone.terminal == SOURCE_AND_CLONE_CONTRACT_PASS_V0233
    assert clone.pre_migration_schema_version == 7
    assert clone.post_migration_schema_version == 9
    assert clone.source_counts == clone.starting_counts
    assert clone.source_database_logical_sha256 == clone.clone_database_logical_sha256_before_migration
    assert clone.active_baseline_id == BASELINE_ID
    assert clone.active_profile_sha256 == PROFILE_SHA256
    assert hashlib.sha256((source_root / "product.sqlite3").read_bytes()).hexdigest() == source_before

    readonly = sqlite3.connect(
        f"file:{(destination / 'product.sqlite3').as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        assert readonly.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 9
        assert readonly.execute("SELECT COUNT(*) FROM diagnosis_stage_events_v02322").fetchone()[0] == 0
        assert readonly.execute("SELECT COUNT(*) FROM diagnosis_evidence_indexes").fetchone()[0] == 0
    finally:
        readonly.close()


def test_repository_phase_model_is_exact() -> None:
    assert tuple(RepositoryPhaseV0233) == (
        RepositoryPhaseV0233.PREPARED,
        RepositoryPhaseV0233.TRAFFIC_PREFLIGHT_PASS,
        RepositoryPhaseV0233.FORMAL_RUNNING,
        RepositoryPhaseV0233.FORMAL_BLOCKED,
        RepositoryPhaseV0233.MEASURED_COMPLETE,
    )


def test_repository_phase_manifest_rejects_premature_measurement() -> None:
    prepared_body = {
        "schema_version": "ecomsre.product.repository-state.v0233",
        "goal_version": "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1",
        "phase": "PREPARED",
        "history_and_handoff_sha256": "1" * 64,
        "source_selection_sha256": "2" * 64,
        "clone_contract_sha256": "3" * 64,
        "campaign_sha256": "4" * 64,
        "contract_preflight_sha256": "5" * 64,
        "traffic_preflight_sha256": None,
        "formal_contract_freeze_sha256": None,
        "pre_execution_review_sha256": None,
        "formal_result_sha256": None,
        "formal_blocker_sha256": None,
        "knowledge_handoff_sha256": None,
        "cleanup_proof_sha256": None,
        "formal_clone_count": 0,
        "formal_execution_count": 0,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
        "action_authority": "NONE",
    }
    prepared = ProductV0233RepositoryStateManifest.model_validate(
        {
            **prepared_body,
            "manifest_sha256": semantic_sha256_v22(prepared_body),
        }
    )
    assert prepared.phase is RepositoryPhaseV0233.PREPARED

    invalid = prepared.model_dump(mode="json")
    invalid.update(
        {
            "phase": "MEASURED_COMPLETE",
            "formal_result_sha256": "6" * 64,
            "knowledge_handoff_sha256": "7" * 64,
            "cleanup_proof_sha256": "8" * 64,
            "formal_clone_count": 1,
            "formal_execution_count": 1,
            "new_incident_count": 1,
            "new_diagnosis_count": 0,
            "measured_result_count": 1,
        }
    )
    invalid.pop("manifest_sha256")
    invalid["manifest_sha256"] = semantic_sha256_v22(invalid)
    with pytest.raises(ValueError, match="phase artifact/counter contract differs"):
        ProductV0233RepositoryStateManifest.model_validate(invalid)


def test_written_increment1_artifacts_are_self_sealed_and_preformal() -> None:
    selection = json.loads(
        (ROOT / "config/product-v0233/source-selection.json").read_text(
            encoding="utf-8"
        )
    )
    assert selection["source_kind"] == "PRISTINE_PREFORMAL_BASE"
    assert selection["selection_sha256"] == semantic_sha256_v22(
        {key: value for key, value in selection.items() if key != "selection_sha256"}
    )

    clone = json.loads(
        (ROOT / "docs/analysis/product-v0233-clone-contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert clone["temporary_clone_removed"] is True
    assert clone["authoritative_formal_clone_count"] == 0
    assert clone["formal_execution_count"] == 0
    assert clone["new_incident_count"] == 0
    assert clone["new_diagnosis_count"] == 0
    assert clone["measured_result_count"] == 0
    assert clone["contract_sha256"] == semantic_sha256_v22(
        {key: value for key, value in clone.items() if key != "contract_sha256"}
    )
