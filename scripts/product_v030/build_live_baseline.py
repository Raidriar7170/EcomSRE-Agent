"""Create one fresh five-window Baseline through the real Product API and Worker."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx

from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.product.app import create_app
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _authority_inputs,
    _request_json,
    _run_product_job,
)
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    CANDIDATES_V030,
    build_product_v030_environment_payload,
)
from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    _rotate_runtime_snapshot,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    load_pilot_runtime_authority_v02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre_live_sandbox.contracts import ensure_private_directory, write_private_json
from ecomsre_live_sandbox.knowledge_v030 import (
    ProductV030Lifecycle,
    observe_queue_lag_v030,
    owned_runtime_observation_v030,
)


def require_baseline_resource_coverage(baseline: dict) -> None:
    observed = {
        item["service"] for item in baseline["v22_baseline_profile"]["resource_stats"]
    }
    if observed != set(CANDIDATES_V030):
        raise RuntimeError("full-mode resource Baseline is incomplete")


def require_prebaseline_traffic_recovery(previous: dict) -> None:
    traffic = previous.get("traffic", {})
    exact_failure = (
        previous.get("status") == "PRODUCT_V030_BASELINE_PREPARATION_FAILED"
        and previous.get("failure", {}).get("message")
        == "fresh baseline healthy traffic did not complete"
        and "traffic_started_at" in previous
        and "baseline" not in previous
        and "baseline_job_id" not in previous
        and traffic.get("attempted") == 1
        and traffic.get("succeeded") == 0
        and traffic.get("failed") == 1
        and traffic.get("stopped_on_error_budget") is True
        and previous.get("environment", {}).get("environment_id")
        == previous.get("verification", {}).get("environment_id")
    )
    if not exact_failure:
        raise ValueError("only the exact one-request pre-Baseline traffic failure may recover")


def require_empty_prebaseline_product_state(product_root: Path) -> None:
    database = product_root / "product.sqlite3"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "environments",
                "baseline_versions",
                "incidents",
                "diagnosis_results",
                "fault_families",
                "registration_drafts",
                "shadow_evaluations",
                "promotion_records",
                "environment_extension_registrations",
                "environment_extension_registry_versions",
                "revocation_records",
                "human_reviews",
                "fault_family_members",
                "incident_fingerprints",
                "evidence_objects",
                "predicate_matrices",
            )
        }
        jobs = [
            (str(row[0]), str(row[1]), int(row[2]), int(row[3]))
            for row in connection.execute(
                "SELECT job_type, status, attempt_count, COUNT(*) "
                "FROM diagnosis_jobs GROUP BY job_type, status, attempt_count "
                "ORDER BY job_type, status, attempt_count"
            ).fetchall()
        ]
    finally:
        connection.close()
    if counts != {
        "environments": 1,
        "baseline_versions": 0,
        "incidents": 0,
        "diagnosis_results": 0,
        "fault_families": 0,
        "registration_drafts": 0,
        "shadow_evaluations": 0,
        "promotion_records": 0,
        "environment_extension_registrations": 0,
        "environment_extension_registry_versions": 0,
        "revocation_records": 0,
        "human_reviews": 0,
        "fault_family_members": 0,
        "incident_fingerprints": 0,
        "evidence_objects": 0,
        "predicate_matrices": 0,
    }:
        raise ValueError("Product state is not an empty pre-Baseline environment")
    if jobs != [("ENVIRONMENT_VERIFY", "SUCCEEDED", 1, 1)]:
        raise ValueError("Product state has an unexpected pre-Baseline job")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--resume-failed-setup", action="store_true")
    parser.add_argument("--resume-failed-prebaseline-traffic", action="store_true")
    parser.add_argument("--from-broker-probe", action="store_true")
    parser.add_argument("--phase-a-root", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    if not private.is_relative_to(root / ".local/product-v030"):
        raise ValueError("Goal private root differs")
    phase_a_root = private if args.phase_a_root is None else args.phase_a_root.resolve()
    if not phase_a_root.is_relative_to(root / ".local/product-v030"):
        raise ValueError("Phase A source root differs")
    verification = json.loads((phase_a_root / "phase-a-verification.json").read_text())
    if verification["status"] != "ECOMSRE_PRODUCT_V030_QUEUE_TELEMETRY_READY":
        raise ValueError("queue telemetry is not ready")
    output = private
    product_root = private / "product"
    recovery = args.resume_failed_prebaseline_traffic
    previous: dict | None = None
    if recovery and (args.resume_failed_setup or args.from_broker_probe):
        raise ValueError("pre-Baseline recovery cannot combine with another mode")
    if args.from_broker_probe:
        probe_path = private / "broker-probe-resumed.json"
        if not probe_path.exists():
            probe_path = private / "broker-probe.json"
        probe = json.loads(probe_path.read_text())
        if probe["status"] != "BROKER_TELEMETRY_PROBE_PASS":
            raise ValueError("broker full-acquisition probe has not passed")
        # Preserve the preparatory probe database and create a genuinely fresh
        # formal environment; a successful first probe needs no failed setup.
        product_root = private / "product-formal"
    if (
        (output / "baseline-result.json").exists()
        and not args.resume_failed_setup
        and not recovery
    ):
        raise FileExistsError("Baseline already attempted; preserve its result")
    if recovery:
        previous = json.loads((private / "baseline-result.json").read_text())
        require_prebaseline_traffic_recovery(previous)
        previous_product_root = previous.get("product_data_root")
        if (
            not isinstance(previous_product_root, str)
            or Path(previous_product_root).resolve() != product_root
        ):
            raise ValueError("recovery Product data root differs")
        diagnostic = json.loads(
            (private / "baseline-recovery-diagnostic.json").read_text()
        )
        if (
            diagnostic.get("status") != "PASS"
            or diagnostic.get("flags", {}).get("state") != "BASELINE"
            or diagnostic.get("lag", {}).get("lag", 20) >= 20
            or set(diagnostic.get("services", {})) != set(CANDIDATES_V030)
            or any(
                item.get("state") != "RUNNING"
                or not item.get("healthy")
                or item.get("restart_count")
                for item in diagnostic.get("services", {}).values()
            )
        ):
            raise ValueError("fresh recovery diagnostic did not pass")
        output = private / "baseline-traffic-resumed"
        ensure_private_directory(output)
        if (output / "baseline-result.json").exists():
            raise FileExistsError("pre-Baseline traffic recovery already attempted")
        require_empty_prebaseline_product_state(product_root)
    elif args.resume_failed_setup:
        previous = json.loads((private / "baseline-result.json").read_text())
        if (
            previous.get("status") != "PRODUCT_V030_BASELINE_PREPARATION_FAILED"
            or "traffic_started_at" in previous
            or "baseline" in previous
        ):
            raise ValueError("only failed pre-traffic Product setup may resume here")
        output = private / "baseline-setup-resumed"
        product_root = private / "product-formal"
        ensure_private_directory(output)
    ensure_private_directory(product_root)
    if not recovery and (product_root / "product.sqlite3").exists():
        raise FileExistsError("fresh Product environment already exists")
    lifecycle = ProductV030Lifecycle(
        repository_root=root,
        private_root=private,
        image_identities=root / ".local/product-v030/acquired-images.json",
    )
    lifecycle.admit()
    backend = lifecycle.authorize_reads()
    assert isinstance(backend, LocalSandboxReadBackend)
    assert lifecycle.goal_controller is not None
    before = lifecycle.goal_controller.read("BASELINE")
    if (
        recovery
        and previous is not None
        and before["document_sha256"]
        != previous.get("baseline_before", {}).get("document_sha256")
    ):
        raise ValueError("recovery Baseline flag document differs")
    profile_sha = hashlib.sha256(
        (private / "control/queue-telemetry-config.json").read_bytes()
    ).hexdigest()
    authority_inputs = _authority_inputs(backend)
    prebound = None
    if not recovery:
        prebound = PilotRuntimeAuthorityV02.build(
            environment_id="env-" + "0" * 24,
            allowed_logical_services=CANDIDATES_V030,
            profile_sha256=profile_sha,
            **authority_inputs,
        )
    authority_path = product_root / "pilot/runtime-authority.json"
    settings = ProductSettingsV1(
        data_root=product_root,
        pilot_runtime_authority_path=authority_path,
        connector_timeout_seconds=15,
        fault_family_review_min_occurrences=3,
    )
    result: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "baseline_before": before,
        "phase_a_verification_sha256": hashlib.sha256(
            (phase_a_root / "phase-a-verification.json").read_bytes()
        ).hexdigest(),
        "product_data_root": str(product_root),
    }
    if recovery:
        assert previous is not None
        result["recovery_of_sha256"] = hashlib.sha256(
            (private / "baseline-result.json").read_bytes()
        ).hexdigest()
        result["recovery_reason"] = "ONE_REQUEST_TRANSIENT_BEFORE_BASELINE"
    try:
        with httpx.Client(timeout=10) as lag_client:
            result["lag_before"] = observe_queue_lag_v030(lag_client)
        if result["lag_before"]["lag"] >= 20:
            raise RuntimeError("baseline pre-state queue is not low")
        app = create_app(settings)
        with TestClient(app) as client:
            if recovery:
                assert previous is not None
                environment = previous["environment"]
                environment_id = environment["environment_id"]
                persisted = app.state.environments.get(environment_id)
                if persisted.model_dump(mode="json") != environment:
                    raise ValueError("recovery persisted environment differs")
                authority = load_pilot_runtime_authority_v02(authority_path)
                expected_authority = PilotRuntimeAuthorityV02.build(
                    environment_id=environment_id,
                    allowed_logical_services=CANDIDATES_V030,
                    profile_sha256=profile_sha,
                    **authority_inputs,
                )
                if authority != expected_authority:
                    raise ValueError("recovery Runtime authority differs")
                payload = build_product_v030_environment_payload(
                    repository_root=root,
                    runtime_authority_sha256=authority.connector_binding_sha256,
                )
                if payload != json.loads((private / "environment.json").read_text()):
                    raise ValueError("recovery environment payload differs")
            else:
                assert prebound is not None
                payload = build_product_v030_environment_payload(
                    repository_root=root,
                    runtime_authority_sha256=prebound.connector_binding_sha256,
                )
                environment = _request_json(
                    client, "POST", "/v1/environments", payload=payload
                )
                environment_id = environment["environment_id"]
                authority = PilotRuntimeAuthorityV02.build(
                    environment_id=environment_id,
                    allowed_logical_services=CANDIDATES_V030,
                    profile_sha256=profile_sha,
                    **authority_inputs,
                )
                write_pilot_runtime_authority_v02(authority_path, authority)
            result["environment"] = environment
            services, proof = owned_runtime_observation_v030(
                lifecycle.environment, candidates=CANDIDATES_V030
            )
            if any(
                not item["healthy"]
                or item["state"] != "RUNNING"
                or item["restart_count"]
                for item in services.values()
            ):
                raise RuntimeError("full candidate Runtime pre-state is not healthy")
            snapshot = PilotRuntimeSnapshotV02.build(
                environment_id=environment_id,
                authority_sha256=authority.connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=services,
            )
            if recovery:
                prior_runtime_path = product_root / "pilot/runtime-readiness.json"
                write_private_json(
                    output / "runtime-snapshot-before.json",
                    json.loads(prior_runtime_path.read_text()),
                    create_once=True,
                )
                result["runtime_snapshot_before_sha256"] = hashlib.sha256(
                    prior_runtime_path.read_bytes()
                ).hexdigest()
            if recovery:
                result["runtime_snapshot_rotation"] = _rotate_runtime_snapshot(
                    path=product_root / "pilot/runtime-readiness.json",
                    snapshot=snapshot,
                    private_root=output,
                    ordinal=1,
                )
            else:
                write_pilot_runtime_snapshot_v02(
                    product_root / "pilot/runtime-readiness.json", snapshot
                )
            write_private_json(
                output / "baseline-runtime-proof.json", proof, create_once=True
            )
            write_private_json(output / "environment.json", payload, create_once=True)
            _, result["verification"] = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{environment_id}/verify-jobs",
                payload=None,
                worker_id="product-v030-verify",
            )
            print("stage=PRODUCT_ENVIRONMENT_VERIFIED", flush=True)
            write_private_json(
                output / "baseline-progress.json", result, create_once=False
            )
            started = time.monotonic()
            result["traffic_started_at"] = datetime.now(UTC).isoformat()
            with httpx.Client() as traffic_client:
                traffic = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                    endpoint="http://127.0.0.1:18080/api/checkout",
                    profile=HealthyTrafficProfileV021(
                        request_seed=29903 if recovery else 29901,
                        maximum_request_count=30,
                        requests_per_second=1 / 6,
                        error_budget=1,
                    ),
                )
            result["traffic"] = traffic.model_dump(mode="json")
            write_private_json(
                output / "baseline-progress.json", result, create_once=False
            )
            if traffic.failed or traffic.attempted != 30:
                raise RuntimeError("fresh baseline healthy traffic did not complete")
            print("stage=BASELINE_TRAFFIC_30_OF_30_COMPLETE", flush=True)
            while time.monotonic() - started < 360:
                time.sleep(min(5, 360 - (time.monotonic() - started)))
            result["baseline_after"] = lifecycle.goal_controller.read("BASELINE")
            with httpx.Client(timeout=10) as lag_client:
                result["lag_after"] = observe_queue_lag_v030(lag_client)
            if result["lag_after"]["lag"] >= 20:
                raise RuntimeError("baseline post-state queue is not low")
            if result["baseline_after"]["document_sha256"] != before["document_sha256"]:
                raise RuntimeError("queue-off Baseline document changed")
            print("stage=BUILDING_FIVE_BASELINE_WINDOWS", flush=True)
            job_id, result["baseline"] = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{environment_id}/baseline-jobs",
                payload={
                    "build_policy": {
                        "mode": "DEMO_ONLY",
                        "lookback_seconds": 180,
                        "window_count": 5,
                        "minimum_successful_windows": 5,
                        "warmup_seconds": 180,
                    },
                    "candidate_services": list(CANDIDATES_V030),
                    "activate": True,
                },
                worker_id="product-v030-baseline",
            )
            result["baseline_job_id"] = job_id
            require_baseline_resource_coverage(result["baseline"])
            result["status"] = "PRODUCT_V030_FRESH_BASELINE_READY"
    except Exception as error:
        result["status"] = "PRODUCT_V030_BASELINE_PREPARATION_FAILED"
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        result["ended_at"] = datetime.now(UTC).isoformat()
        write_private_json(output / "baseline-result.json", result, create_once=True)
    print(
        json.dumps(
            {
                "status": result["status"],
                "environment_id": result["environment"]["environment_id"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
