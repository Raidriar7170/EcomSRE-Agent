"""One owned-local Payment campaign under the already activated user Goal.

prepare creates healthy evidence only. campaign requires real independent
review, exact-head CI and local validation capsules before its create-once
freeze and fault. cleanup never restores flags or retries a mutation.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import time
from typing import Any

from ecomsre.product.remediation.live_evidence import LiveCleanupV040
from ecomsre_live_sandbox.environment import DockerSnapshot
from scripts.live_sandbox.product_v040 import IMAGE_PROOF_SHA256, ProductV040Lifecycle
from scripts.product.v040_control import (
    ObserverLoopV040,
    inject_once,
    observer_for,
    write_bindings,
)
from scripts.product.v040_gates import counts, freeze, network_denial
from scripts.product.v040_observer import resource_fingerprints
from scripts.product.v040_preparation import (
    ProductApiV040,
    bounded_traffic,
    diagnosis,
    healthy_baseline,
)
from scripts.product.v040_runtime import ProductRuntimeV040, read_json, seal_private


def cleanup(
    runtime: ProductRuntimeV040, lifecycle: ProductV040Lifecycle
) -> LiveCleanupV040:
    """Product first, then exact owned Demo. No baseline POST or file replacement."""
    started = read_json(runtime.private / "host/sandbox-start.json")
    environment = lifecycle.environment
    environment._baseline_snapshot = DockerSnapshot(
        **{key: frozenset(value) for key, value in started["snapshot"].items()}
    )
    runtime.boundary()
    if environment.verify_local_docker() != runtime.boundary():
        raise ValueError("cleanup daemon differs")
    environment.verify_upstream()
    resolved, _ = environment.resolve()
    admission = read_json(runtime.private / "sandbox/control/admission.json")
    if resolved.model_dump(mode="json") != admission["resolved"]:
        raise ValueError("cleanup Compose differs from admission")
    baseline_restored = False
    try:
        assert lifecycle.goal_controller is not None
        lifecycle.goal_controller.read("BASELINE")
        baseline_restored = True
    except Exception:
        pass
    product_after = runtime.cleanup()
    observed = environment.cleanup(baseline_restored=baseline_restored)
    non_owned = resource_fingerprints(
        runtime, exclude_projects={"ecomsre-product-v040", "ecomsre-live-sandbox-v1"}
    ) != read_json(runtime.private / "host/non-owned-before.json")
    result = LiveCleanupV040(
        verdict="CLEAN" if observed.verdict == "CLEAN" and not non_owned else "BLOCKED",
        baseline_restored=baseline_restored,
        owned_containers=observed.owned_containers + len(product_after["container"]),
        owned_networks=observed.owned_networks + len(product_after["network"]),
        owned_volumes=observed.owned_volumes + len(product_after["volume"]),
        non_owned_resources_changed=non_owned or observed.non_owned_resources_changed,
    )
    seal_private(runtime.private / "host/cleanup.json", result.model_dump(mode="json"))
    return result


def prepare(runtime: ProductRuntimeV040, image_proofs: Path) -> None:
    proof = image_proofs.read_bytes()
    if hashlib.sha256(proof).hexdigest() != IMAGE_PROOF_SHA256:
        raise ValueError("historical image proof content differs")
    runtime.initialize()
    # Retain the exact original proof bytes: its SHA is the admission authority.
    proof_path = runtime.private / "host/image-proofs-original.json"
    from ecomsre.product.remediation.window_requests import create_private_file

    create_private_file(proof_path, proof)
    # Lifecycle intentionally reads only this byte-exact private copy.
    lifecycle = ProductV040Lifecycle(
        repository_root=runtime.repository,
        private_root=runtime.private / "sandbox",
        image_identities=proof_path,
    )
    lifecycle.admit()
    environment = lifecycle.environment
    if environment.verify_local_docker() != runtime.boundary():
        raise ValueError("Docker authorities differ before startup")
    if any(environment.verify_owned_resources(require_complete=False).values()) or any(
        runtime.owned().values()
    ):
        raise ValueError("pre-existing Goal resources are not a new campaign")
    seal_private(
        runtime.private / "host/non-owned-before.json",
        resource_fingerprints(runtime, exclude_projects=set()),
    )
    runtime.build()
    snapshot = environment.snapshot_all_resources()
    seal_private(
        runtime.private / "host/sandbox-start.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "snapshot": {
                key: sorted(getattr(snapshot, key))
                for key in ("containers", "networks", "volumes")
            },
        },
    )
    observer_loop: ObserverLoopV040 | None = None
    try:
        lifecycle.start()
        lifecycle.wait_ready()
        runtime.start_bootstrap()
        baseline = healthy_baseline(runtime, lifecycle)
        write_bindings(runtime, lifecycle)
        observer = observer_for(runtime, lifecycle)
        observer_loop = ObserverLoopV040(observer)
        with observer_loop:
            runtime.enable()
            seal_private(
                runtime.private / "host/product-ownership.json", runtime.owned()
            )
            network_denial(runtime, observer)
            control = diagnosis(
                runtime,
                lifecycle,
                name="control",
                started_at=baseline["traffic"]["started_at"],
            )
            if control["diagnosis"]["terminal"] != "NO_INCIDENT":
                raise ValueError("healthy control did not produce NO_INCIDENT")
            api = ProductApiV040(runtime)
            projected = api.call(
                "POST",
                f"/v1/incidents/{control['incident']['incident_id']}/remediation-candidates",
                key="v040-control-candidates",
            )
            seal_private(runtime.private / "host/control-candidates.json", projected)
            if projected["candidates"]:
                raise ValueError("healthy control yielded a candidate")
            initial = counts(observer)
            if (
                any(
                    (
                        initial.fault_campaigns,
                        initial.accepted_attempts,
                        initial.write_intents,
                        initial.dispatches,
                        initial.receipts,
                        initial.recovery_windows,
                    )
                )
                or initial.forward_mutations != 0
            ):
                raise ValueError("preparation consumed formal authority")
            seal_private(
                runtime.private / "host/preparation-ready.json",
                {
                    "status": "PRE_EXECUTION_REVIEW_REQUIRED",
                    "created_at": datetime.now(UTC).isoformat(),
                    "counts": initial.model_dump(mode="json"),
                },
            )
        print(
            "PRE_EXECUTION_REVIEW_REQUIRED; fault=0; attempts=0; writes=0", flush=True
        )
    except Exception as error:
        runtime.capture_failure()
        lifecycle.capture_failure()
        seal_private(
            runtime.private / "host/preparation-failure.json",
            {
                "error_type": type(error).__name__,
                "message": str(error),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        if observer_loop is not None and observer_loop.thread.is_alive():
            raise RuntimeError(
                "active observer prevents safe cleanup; runtime retained"
            ) from error
        cleanup(runtime, lifecycle)
        raise


def campaign(runtime: ProductRuntimeV040, lifecycle: ProductV040Lifecycle) -> None:
    if (runtime.private / "host/fault-intent.json").exists() or (
        runtime.private / "host/frozen-manifest.json"
    ).exists():
        raise ValueError(
            "frozen campaign cannot be rerun; a new versioned Goal is required"
        )
    read_json(runtime.private / "host/preparation-ready.json")
    observer = observer_for(runtime, lifecycle)
    stage = "FAULT_CONTROL"
    result: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat()}
    observer_loop = ObserverLoopV040(observer)
    try:
        with observer_loop:
            manifest = freeze(runtime, observer)
            result["manifest_sha256"] = manifest.manifest_sha256
            inject_once(observer, manifest.manifest_sha256)
            started_at = datetime.now(UTC).isoformat()
            baseline = read_json(runtime.private / "host/healthy-baseline.json")
            payment = next(
                row["service_id"]
                for row in baseline["verification"]["service_identity_map"]["services"]
                if row["logical_service"] == "payment"
            )
            api = ProductApiV040(runtime)
            stage = "DIAGNOSIS"
            result["change"] = api.call(
                "POST",
                f"/v1/environments/{manifest.environment_id}/changes",
                payload={
                    "service_id": payment,
                    "category": "CONFIGURATION",
                    "occurred_at": started_at,
                    "revision": "observed-local-config-1",
                    "summary": "Local service configuration changed.",
                    "external_change_id": "v040-observed-config",
                },
                key="v040-change",
            )
            workload = read_json(
                runtime.repository / "config/product-v040/live-profile.v1.json"
            )
            result["traffic"] = bounded_traffic(
                runtime,
                workload["fault_traffic"],
                name="measurement",
                minimum_seconds=workload["fault_observation_seconds"],
            )
            result["diagnosis_record"] = diagnosis(
                runtime, lifecycle, name="measurement", started_at=started_at
            )
            diagnosed = result["diagnosis_record"]["diagnosis"]
            if (
                diagnosed["terminal"],
                diagnosed["core_or_extension_or_open_world"],
                diagnosed["root_service_ids"],
                diagnosed["broad_domain"],
                diagnosed["mechanism"],
            ) != (
                "CORE_KNOWN",
                "CORE",
                [payment],
                "CONFIGURATION",
                "CONFIGURATION_ERROR",
            ):
                raise ValueError(
                    "measured diagnosis did not support bounded Payment remediation"
                )
            refs = {
                item["evidence_ref"]
                for item in result["diagnosis_record"]["evidence"]["objects"]
            }
            if not set(diagnosed["supporting_evidence_refs"]).issubset(refs):
                raise ValueError("diagnosis supporting refs do not resolve")
            stage = "CANDIDATE"
            projection = api.call(
                "POST",
                f"/v1/incidents/{result['diagnosis_record']['incident']['incident_id']}/remediation-candidates",
                key="v040-measurement-candidate",
            )
            if len(projection["candidates"]) != 1:
                raise ValueError("exactly one supported candidate required")
            candidate = result["candidate"] = projection["candidates"][0]
            stage = "APPROVAL"
            approval = result["approval"] = api.call(
                "POST",
                f"/v1/remediation-candidates/{candidate['candidate_id']}/approvals",
                key="v040-approval",
                payload={
                    "approver": "LOCAL_OPERATOR",
                    "authorization_source": "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION",
                    "decision": "APPROVE",
                    "scope": {
                        "runbook_id": "ROLLBACK_CONFIGURATION",
                        "target_logical_service": "payment",
                        "maximum_forward_steps": 1,
                    },
                    "ttl_seconds": 600,
                },
            )
            stage = "AUTHORIZATION"
            seal_private(
                runtime.private / "host/attempt-request-intent.json",
                {
                    "candidate_id": candidate["candidate_id"],
                    "approval_id": approval["approval_id"],
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            attempt = result["attempt_creation"] = api.call(
                "POST",
                f"/v1/remediation-candidates/{candidate['candidate_id']}/attempts",
                key="v040-attempt",
                payload={"approval_id": approval["approval_id"]},
            )
            seal_private(runtime.private / "host/attempt-created.json", attempt)
            if attempt["authorization_id"] is None:
                raise ValueError("one attempt was denied authorization")
            stage = "EXECUTION"
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                current = api.call(
                    "GET", f"/v1/remediation-attempts/{attempt['attempt_id']}"
                )
                result["attempt"] = current
                if current["state"] in {
                    "APPLIED",
                    "VERIFYING",
                    "RECOVERED",
                    "VERIFICATION_FAILED",
                }:
                    stage = "VERIFICATION"
                if current["terminal"] is not None:
                    break
                time.sleep(1)
            if result["attempt"]["terminal"] is None:
                raise ValueError("attempt did not reach a bounded terminal")
            stage = "NONE"
    except Exception as error:
        result["failure"] = {"error_type": type(error).__name__, "message": str(error)}
    finally:
        result["blocked_stage"] = stage
        result["ended_at"] = datetime.now(UTC).isoformat()
        seal_private(runtime.private / "host/measured-execution.json", result)
        if observer_loop.thread.is_alive():
            seal_private(
                runtime.private / "host/cleanup-blocked-active-observer.json",
                {"blocked": True, "created_at": datetime.now(UTC).isoformat()},
            )
            raise RuntimeError(
                "active observer prevents safe cleanup; runtime retained"
            )
        cleanup(runtime, lifecycle)
    print(
        "MEASURED_RESULT_PRESERVED; no rerun permitted; final evidence verification required",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "campaign", "cleanup"))
    parser.add_argument("--image-proofs", type=Path)
    args = parser.parse_args()
    runtime = ProductRuntimeV040(Path(__file__).resolve().parents[2])
    with runtime.operation_lock():
        if args.phase == "prepare":
            if args.image_proofs is None:
                parser.error("prepare requires exact saved image proofs")
            prepare(runtime, args.image_proofs)
        else:
            runtime.load()
            lifecycle = ProductV040Lifecycle(
                repository_root=runtime.repository,
                private_root=runtime.private / "sandbox",
                image_identities=runtime.private / "host/image-proofs-original.json",
            )
            lifecycle.admit()
            if args.phase == "campaign":
                campaign(runtime, lifecycle)
            else:
                cleanup(runtime, lifecycle)


if __name__ == "__main__":
    main()
