"""One bounded live case through the existing Product API and Worker."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx

from ecomsre.product.app import create_app
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre.product.pilot.live_calibration_v02 import _request_json, _run_product_job
from ecomsre.product.pilot.live_knowledge_evolution_v030 import CANDIDATES_V030
from ecomsre.product.pilot.control_gate_v030 import (
    C1_CANDIDATES_V030,
    case_gate_passes_v030,
    control_record_passes_v030,
    evaluate_c1_queue_negative_v030,
)
from ecomsre.product.pilot.live_nofault_acceptance_v023 import _rotate_runtime_snapshot
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02
from ecomsre.product.settings import ProductSettingsV1
from ecomsre_live_sandbox.contracts import ensure_private_directory, write_private_json
from ecomsre_live_sandbox.knowledge_v030 import (
    ProductV030Lifecycle,
    observe_queue_lag_v030,
    owned_runtime_observation_v030,
)


CASES = {
    "N0-A": (30001, "BASELINE", 30, "NO_INCIDENT"),
    "N0-B": (30002, "BASELINE", 30, "NO_INCIDENT"),
    "C1": (30003, "PAYMENT", 10, "CORE_KNOWN"),
    "P1": (31001, "QUEUE", 3, "OPEN_WORLD"),
    "P2": (31002, "QUEUE", 3, "OPEN_WORLD"),
    "P3": (31003, "QUEUE", 3, "OPEN_WORLD"),
    "H1": (32001, "QUEUE", 3, "EXTENSION_KNOWN"),
}
FORBIDDEN = re.compile(
    r"kafkaQueueProblems|paymentFailure|defaultVariant|feature\s*flag|\.flagd\.json|\.local/product-v030|overload simulation",
    re.I,
)


def case_traffic_profile(case: str) -> tuple[HealthyTrafficProfileV021, int]:
    seed, state, count, _ = CASES[case]
    # Core Metrics reads a frozen five-minute window of rolling rates. Keep
    # C1's ten requests bounded but distribute them across that observation,
    # rather than interpreting a ten-second burst as five minutes of failure.
    return HealthyTrafficProfileV021(
        request_seed=seed,
        maximum_request_count=count,
        requests_per_second=1 / 30 if state == "PAYMENT" else 1,
        error_budget=count if state == "PAYMENT" else 1,
    ), 300 if state == "PAYMENT" else 60


def require_case_baseline(incident: dict, baseline: dict) -> None:
    if any(
        incident.get(field) != baseline[field]
        for field in ("baseline_id", "baseline_sha256")
    ):
        raise ValueError("case Baseline binding differs from the fixed control set")


def apply_case_fault(controller, state, result):
    # The controller writes before readback; a failed readback is mutation-possible.
    result["fault_write_attempt_count"] += 1
    result["activated"] = controller.apply(state)
    result["fault_enable_count"] += 1


def restore_case_flags(controller, result):
    return (
        controller.apply("BASELINE")
        if result["fault_write_attempt_count"]
        else controller.read("BASELINE")
    )


def queue_case_root_matches_unique_owner(
    diagnosis: dict, strong_queue: list, by_logical: dict[str, str]
) -> bool:
    queue_owners = {item.service for item in strong_queue}
    if len(queue_owners) != 1:
        return False
    owner = next(iter(queue_owners))
    return owner in by_logical and diagnosis["root_service_ids"] == [
        by_logical[owner]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--case", required=True, choices=tuple(CASES))
    parser.add_argument("--baseline-result", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    if not private.is_relative_to(root / ".local/product-v030"):
        raise ValueError("Goal private root differs")
    baseline_path = args.baseline_result.resolve()
    if not baseline_path.is_relative_to(private):
        raise ValueError("Baseline result escapes the current runtime")
    baseline = json.loads(baseline_path.read_text())
    if baseline["status"] != "PRODUCT_V030_FRESH_BASELINE_READY":
        raise ValueError("fresh five-window Baseline is not ready")
    seed, state, count, expected = CASES[args.case]
    traffic_profile, minimum_observation_seconds = case_traffic_profile(args.case)
    if state == "QUEUE":
        gate = json.loads((private / "pre-p1-acquisition-leakage.json").read_text())
        if (gate["status"] != "PASS" or gate.get("capability_limitations") != []
            or gate.get("environment_id") != baseline["environment"]["environment_id"]
            or gate.get("baseline_sha256") != baseline["baseline"]["baseline_sha256"]):
            raise ValueError("complete Product acquisition leakage gate has not passed")
        for control in ("N0-A", "N0-B", "C1"):
            record = json.loads((private / "cases" / control / "result.json").read_text())
            if (not control_record_passes_v030(record)
                or record["incident"]["environment_id"] != gate["environment_id"]):
                raise ValueError("the current environment's controls have not passed")
    if args.case == "H1":
        promotion = json.loads((private / "promotion.json").read_text())
        if promotion["status"] != "ECOMSRE_PRODUCT_V030_PROMOTION_ACTIVE":
            raise ValueError("H1 requires measured Promotion")
    case_root = private / "cases" / args.case
    if case_root.exists():
        raise FileExistsError(
            "case already began; preserve it and inspect its evidence"
        )
    ensure_private_directory(case_root)
    product_root = Path(baseline["product_data_root"]).resolve()
    if not product_root.is_relative_to(private):
        raise ValueError("Product data escapes the current runtime")
    authority_path = product_root / "pilot/runtime-authority.json"
    authority = PilotRuntimeAuthorityV02.model_validate_json(
        authority_path.read_bytes()
    )
    settings = ProductSettingsV1(
        data_root=product_root,
        pilot_runtime_authority_path=authority_path,
        connector_timeout_seconds=15,
        fault_family_review_min_occurrences=3,
    )
    environment_id = baseline["environment"]["environment_id"]
    if authority.environment_id != environment_id:
        raise ValueError("case environment authority differs")
    by_logical = {
        item["logical_service"]: item["service_id"]
        for item in baseline["verification"]["service_identity_map"]["services"]
    }
    candidates = C1_CANDIDATES_V030 if args.case == "C1" else CANDIDATES_V030[:3]
    lifecycle = ProductV030Lifecycle(
        repository_root=root,
        private_root=private,
        image_identities=root / ".local/product-v030/acquired-images.json",
    )
    result: dict[str, Any] = {
        "case": args.case,
        "seed": seed,
        "expected": expected,
        "started_at": datetime.now(UTC).isoformat(),
        "fault_enable_count": 0,
        "fault_write_attempt_count": 0,
        "traffic_profile": traffic_profile.model_dump(mode="json"),
        "minimum_observation_seconds": minimum_observation_seconds,
    }
    write_private_json(case_root / "started.json", result, create_once=True)
    try:
        lifecycle.admit()
        backend = lifecycle.authorize_reads()
        if backend.authority != authority.read_authority:
            raise ValueError("fresh runtime authority differs from the case environment")
        assert lifecycle.goal_controller is not None
        result["before_flags"] = lifecycle.goal_controller.read("BASELINE")
        with httpx.Client(timeout=10) as client:
            result["lag_before"] = observe_queue_lag_v030(client)
        if result["lag_before"]["lag"] >= 20:
            raise RuntimeError("case pre-state queue is not low")
        services, proof = owned_runtime_observation_v030(
            lifecycle.environment, candidates=CANDIDATES_V030
        )
        result["runtime_before"] = proof
        if any(not row["healthy"] or row["restart_count"] for row in services.values()):
            raise RuntimeError("case Runtime pre-state is not healthy")
        if state != "BASELINE":
            apply_case_fault(lifecycle.goal_controller, state, result)
        started_at = datetime.now(UTC)
        app = create_app(settings)
        with TestClient(app) as product:
            family_ids_before = tuple(
                item.family_id for item in app.state.knowledge.list_families(environment_id).items
            )
            if args.case == "C1":
                result["change"] = _request_json(
                    product,
                    "POST",
                    f"/v1/environments/{environment_id}/changes",
                    payload={
                        "service_id": by_logical["payment"],
                        "category": "CONFIGURATION",
                        "occurred_at": started_at.isoformat(),
                        "revision": "observed-local-config-1",
                        "summary": "Local payment configuration changed.",
                        "external_change_id": "product-v030-c1-change",
                    },
                )
            observations = []
            with httpx.Client(
                event_hooks={
                    "response": [
                        lambda response: observations.append(
                            {
                                "path": response.request.url.path,
                                "status": response.status_code,
                            }
                        )
                    ]
                }
            ) as traffic_client:
                measured = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                    endpoint="http://127.0.0.1:18080/api/checkout",
                    profile=traffic_profile,
                )
            result["traffic"] = {
                **measured.model_dump(mode="json"),
                "http_observations": observations,
            }
            if measured.attempted != count or (state != "PAYMENT" and measured.failed):
                raise RuntimeError("bounded case traffic did not complete")
            result["lag_observations"] = []
            deadline = time.monotonic() + 90
            strong_stamps: set[float] = set()
            with httpx.Client(timeout=10) as client:
                while True:
                    sample = observe_queue_lag_v030(client)
                    result["lag_observations"].append(sample)
                    if (
                        sample["lag"] >= 20
                        and sample["source_timestamp"] >= started_at.timestamp()
                    ):
                        strong_stamps.add(sample["source_timestamp"])
                    elapsed = (datetime.now(UTC) - started_at).total_seconds()
                    if elapsed >= minimum_observation_seconds and (
                        state != "QUEUE" or len(strong_stamps) >= 3
                    ):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "bounded observation did not obtain required samples"
                        )
                    time.sleep(5)
            services, result["runtime_during"] = owned_runtime_observation_v030(
                lifecycle.environment, candidates=CANDIDATES_V030
            )
            snapshot = PilotRuntimeSnapshotV02.build(
                environment_id=environment_id,
                authority_sha256=authority.connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=services,
            )
            snapshot_path = product_root / "pilot/runtime-readiness.json"
            write_private_json(
                case_root / "runtime-snapshot-before.json",
                json.loads(snapshot_path.read_text()),
                create_once=True,
            )
            _rotate_runtime_snapshot(
                path=snapshot_path, snapshot=snapshot, private_root=case_root, ordinal=1
            )
            write_private_json(
                case_root / "runtime-snapshot-during.json",
                snapshot.model_dump(mode="json"),
                create_once=True,
            )
            ended_at = datetime.now(UTC)
            result["incident"] = _request_json(
                product,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": environment_id,
                    "external_incident_key": f"product-v030-{private.name}-{args.case.lower()}",
                    "alert_name": "bounded-service-observation",
                    "summary": "Bounded local service telemetry observation.",
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "candidate_service_ids": sorted(
                        by_logical[name] for name in candidates
                    ),
                    "labels": {
                        "fault": "none" if state == "BASELINE" else "synthetic-unknown"
                    },
                },
            )
            require_case_baseline(result["incident"], baseline["baseline"])
            incident_id = result["incident"]["incident_id"]
            write_private_json(case_root / "observation.json", result, create_once=True)
            print(
                f"stage=INCIDENT_CREATED case={args.case} incident_id={incident_id}",
                flush=True,
            )
            result["job_id"], result["diagnosis"] = _run_product_job(
                product,
                settings,
                path=f"/v1/incidents/{incident_id}/diagnosis-jobs",
                worker_id=f"product-v030-{args.case.lower()}",
            )
            evidence = _request_json(
                product, "GET", f"/v1/incidents/{incident_id}/evidence"
            )
            write_private_json(case_root / "evidence.json", evidence, create_once=True)
            result["leaked_tokens"] = sorted(
                set(FORBIDDEN.findall(json.dumps(evidence)))
            )
            refs = {item["evidence_ref"] for item in evidence["objects"]}
            diagnosis = result["diagnosis"]
            result["supporting_refs_resolve"] = set(
                diagnosis["supporting_evidence_refs"]
            ).issubset(refs)
            if args.case == "C1":
                result["queue_negative_evidence"] = evaluate_c1_queue_negative_v030(
                    app.state.knowledge, incident_id
                )
            result["status"] = "PASS" if case_gate_passes_v030(result, expected) else "CASE_GATE_FAILED"
            if state == "QUEUE":
                material = app.state.knowledge._shadow_runtime_material(incident_id)
                strong_queue = [
                    item for item in material.runtime_input.generic_anomalies
                    if item.kind.value == "METRIC_QUEUE_LAG_OUTLIER"
                    and item.strength.value == "STRONG"
                    and item.service == "fraud-detection"
                ]
                result["strong_queue_anomalies"] = [
                    item.model_dump(mode="json") for item in strong_queue
                ]
                runtime_healthy = all(
                    row["healthy"] and row["state"] == "RUNNING"
                    for row in services.values()
                )
                report = diagnosis["provisional_report"]
                exact_terminal = (
                    report is not None
                    and report["terminal"] == "UNREGISTERED_INCIDENT_SUSPECTED"
                    and diagnosis["mechanism"] == "UNREGISTERED_OBSERVED_ANOMALY"
                    and diagnosis["core_or_extension_or_open_world"] == "OPEN_WORLD"
                    if expected == "OPEN_WORLD"
                    else report is None
                    and diagnosis["core_or_extension_or_open_world"] == "EXTENSION"
                    and diagnosis["mechanism"] == "kafka-queue-backlog"
                    and diagnosis["root_service_ids"] == promotion["majority_root_service_ids"]
                    and family_ids_before == tuple(
                        item.family_id for item in app.state.knowledge.list_families(environment_id).items
                    )
                )
                result["queue_case_gate"] = bool(
                    strong_queue and runtime_healthy and exact_terminal
                    and queue_case_root_matches_unique_owner(
                        diagnosis, strong_queue, by_logical
                    )
                    and all(set(item.evidence_refs).issubset(refs) for item in strong_queue)
                )
                if not result["queue_case_gate"]:
                    result["status"] = "CASE_GATE_FAILED"
    except Exception as error:
        result["status"] = "CASE_EXECUTION_FAILED"
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        try:
            if lifecycle.goal_controller is not None:
                result["restored"] = restore_case_flags(
                    lifecycle.goal_controller, result
                )
                restored_at = datetime.fromisoformat(
                    result["restored"]["observed_at"]
                ).timestamp()
                deadline = time.monotonic() + 360
                with httpx.Client(timeout=10) as client:
                    while True:
                        result["lag_after"] = observe_queue_lag_v030(client)
                        if (
                            result["lag_after"]["lag"] < 20
                            and result["lag_after"]["source_timestamp"] >= restored_at
                        ):
                            result["baseline_restored_and_drained"] = True
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError("post-case queue did not drain")
                        time.sleep(5)
        except Exception as error:
            result["cleanup_failure"] = str(error)
            result["status"] = "CASE_RECOVERY_FAILED"
        result["ended_at"] = datetime.now(UTC).isoformat()
        write_private_json(case_root / "result.json", result, create_once=True)
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "case",
                    "status",
                    "diagnosis",
                    "leaked_tokens",
                    "baseline_restored_and_drained",
                )
            }
        ),
        flush=True,
    )
    if result["status"] != "PASS":
        raise RuntimeError("case did not meet its original Goal gate")


if __name__ == "__main__":
    main()
