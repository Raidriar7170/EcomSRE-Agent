"""Real API/Worker healthy preparation, separate from the create-once fault."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import time
from typing import Any

import httpx

from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre.product.pilot.live_calibration_v02 import _authority_inputs
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    CANDIDATES_V030,
    build_product_v030_environment_payload,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre_live_sandbox.knowledge_v030 import owned_runtime_observation_v030
from scripts.live_sandbox.product_v040 import ProductV040Lifecycle
from scripts.product.v040_warmup import application_warmup
from scripts.product.v040_runtime import (
    ProductRuntimeV040,
    atomic_private,
    read_json,
    seal_private,
)


class ProductApiV040:
    def __init__(self, runtime: ProductRuntimeV040) -> None:
        self.runtime = runtime
        self.client = httpx.Client(
            base_url="http://127.0.0.1:18001",
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + runtime.env["ECOMSRE_ADMIN_TOKEN"]},
        )

    def call(
        self, method: str, path: str, *, payload: object = None, key: str | None = None
    ) -> dict[str, Any]:
        response = self.client.request(
            method, path, json=payload, headers={"Idempotency-Key": key} if key else {}
        )
        if not response.is_success:
            seal_private(
                self.runtime.private
                / "errors"
                / (
                    hashlib.sha256(
                        (path + datetime.now(UTC).isoformat()).encode()
                    ).hexdigest()
                    + ".json"
                ),
                {
                    "path": path,
                    "method": method,
                    "status": response.status_code,
                    "body": response.text,
                },
            )
            raise RuntimeError(
                "Product API request rejected; private evidence retained"
            )
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Product API result is not an object")
        return value

    def job(self, path: str, *, payload: object = None, key: str) -> dict[str, Any]:
        started = self.call("POST", path, payload=payload, key=key)
        seal_private(self.runtime.private / "host" / f"{key}-job-started.json", started)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            value = self.call("GET", f"/v1/jobs/{started['job_id']}")
            if value["status"] in {"SUCCEEDED", "FAILED"}:
                seal_private(
                    self.runtime.private / "host" / f"{key}-job-terminal.json", value
                )
                if value["status"] != "SUCCEEDED" or not isinstance(
                    value.get("result"), dict
                ):
                    raise RuntimeError("Product Worker job failed")
                return value["result"]
            time.sleep(1)
        raise RuntimeError("Product Worker job did not terminate within bound")


def runtime_snapshot(
    runtime: ProductRuntimeV040,
    lifecycle: ProductV040Lifecycle,
    authority: PilotRuntimeAuthorityV02,
    name: str,
) -> dict[str, Any]:
    services, proof = owned_runtime_observation_v030(
        lifecycle.environment, candidates=CANDIDATES_V030
    )
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=authority.environment_id,
        authority_sha256=authority.connector_binding_sha256,
        observed_at=datetime.now(UTC),
        services=services,
    )
    seal_private(
        runtime.private / "host" / f"{name}-runtime.json",
        {"proof": proof, "snapshot": snapshot.model_dump(mode="json")},
    )
    atomic_private(
        runtime.private / "product/pilot/runtime-readiness.json",
        snapshot.model_dump(mode="json"),
    )
    return services


def bounded_traffic(
    runtime: ProductRuntimeV040,
    profile: dict[str, Any],
    *,
    name: str,
    minimum_seconds: int,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    monotonic = time.monotonic()
    observations: list[dict[str, Any]] = []
    seal_private(
        runtime.private / "host" / f"{name}-traffic-started.json",
        {
            "started_at": started.isoformat(),
            "profile": profile,
            "minimum_seconds": minimum_seconds,
        },
    )
    with httpx.Client(
        trust_env=False,
        follow_redirects=False,
        event_hooks={
            "response": [
                lambda response: observations.append(
                    {
                        "path": response.request.url.path,
                        "status": response.status_code,
                        "observed_at": datetime.now(UTC).isoformat(),
                    }
                )
            ]
        },
    ) as client:
        value = BoundedHealthyCheckoutTrafficV021(client=client).run(
            endpoint="http://127.0.0.1:18080/api/checkout",
            profile=HealthyTrafficProfileV021.model_validate(profile),
        )
    while time.monotonic() - monotonic < minimum_seconds:
        time.sleep(min(5, minimum_seconds - (time.monotonic() - monotonic)))
    result = {
        "started_at": started.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "monotonic_seconds": time.monotonic() - monotonic,
        "traffic": value.model_dump(mode="json"),
        "observations": observations,
    }
    seal_private(runtime.private / "host" / f"{name}-traffic.json", result)
    return result


def healthy_baseline(
    runtime: ProductRuntimeV040, lifecycle: ProductV040Lifecycle
) -> dict[str, Any]:
    api = ProductApiV040(runtime)
    profile = read_json(runtime.repository / "config/product-v040/live-profile.v1.json")
    backend = lifecycle.authorize_reads()
    if not isinstance(backend, LocalSandboxReadBackend):
        raise ValueError("owned read backend required")
    inputs = _authority_inputs(backend)
    profile_sha = hashlib.sha256(
        (runtime.repository / "config/product-v040/live-profile.v1.json").read_bytes()
    ).hexdigest()
    prebound = PilotRuntimeAuthorityV02.build(
        environment_id="env-" + "0" * 24,
        allowed_logical_services=CANDIDATES_V030,
        profile_sha256=profile_sha,
        **inputs,
    )
    payload = build_product_v030_environment_payload(
        repository_root=runtime.repository,
        runtime_authority_sha256=prebound.connector_binding_sha256,
    )
    payload.update(
        name="bounded-local-observation",
        description="Local read-only service telemetry.",
    )
    for connector in payload["connector_configs"]:
        if connector["kind"] in {"PROMETHEUS", "OPENSEARCH", "JAEGER"}:
            connector["endpoint"] = (
                "http://remediation-observer:8081/observability/"
                + connector["kind"].lower()
            )
    environment = api.call(
        "POST", "/v1/environments", payload=payload, key="v040-environment"
    )
    environment_id = environment["environment_id"]
    authority = PilotRuntimeAuthorityV02.build(
        environment_id=environment_id,
        allowed_logical_services=CANDIDATES_V030,
        profile_sha256=profile_sha,
        **inputs,
    )
    write_pilot_runtime_authority_v02(
        runtime.private / "product/pilot/runtime-authority.json", authority
    )
    seal_private(runtime.private / "host/environment.json", environment)
    services = runtime_snapshot(runtime, lifecycle, authority, "verification")
    if any(
        not row["healthy"] or row["restart_count"] or row["state"] != "RUNNING"
        for row in services.values()
    ):
        raise ValueError("healthy preparation Runtime is not healthy")
    verification = api.job(
        f"/v1/environments/{environment_id}/verify-jobs", key="v040-verification"
    )
    application_warmup(runtime, profile["application_warmup"])
    traffic = bounded_traffic(
        runtime,
        profile["healthy_traffic"],
        name="healthy",
        minimum_seconds=profile["healthy_observation_seconds"],
    )
    if traffic["traffic"]["failed"] or traffic["traffic"]["attempted"] != 30:
        raise ValueError("healthy control traffic failed")
    assert lifecycle.goal_controller is not None
    before = lifecycle.goal_controller.read("BASELINE")
    runtime_snapshot(runtime, lifecycle, authority, "baseline")
    baseline = api.job(
        f"/v1/environments/{environment_id}/baseline-jobs",
        payload={
            "build_policy": profile["baseline_build_policy"],
            "candidate_services": list(CANDIDATES_V030),
            "activate": True,
        },
        key="v040-baseline",
    )
    if {
        row["service"] for row in baseline["v22_baseline_profile"]["resource_stats"]
    } != set(CANDIDATES_V030):
        raise ValueError("baseline resource coverage incomplete")
    after = lifecycle.goal_controller.read("BASELINE")
    if before["document_sha256"] != after["document_sha256"]:
        raise ValueError("healthy configuration drift")
    result = {
        "environment": environment,
        "verification": verification,
        "baseline": baseline,
        "traffic": traffic,
        "flags": after,
    }
    seal_private(runtime.private / "host/healthy-baseline.json", result)
    return result


def diagnosis(
    runtime: ProductRuntimeV040,
    lifecycle: ProductV040Lifecycle,
    *,
    name: str,
    started_at: str,
) -> dict[str, Any]:
    authority = PilotRuntimeAuthorityV02.model_validate_json(
        (runtime.private / "product/pilot/runtime-authority.json").read_bytes()
    )
    baseline = read_json(runtime.private / "host/healthy-baseline.json")
    api = ProductApiV040(runtime)
    runtime_snapshot(runtime, lifecycle, authority, name)
    # Snapshot is fixed before the Incident end and throughout the Worker job.
    ended = datetime.now(UTC)
    by_logical = {
        row["logical_service"]: row["service_id"]
        for row in baseline["verification"]["service_identity_map"]["services"]
    }
    incident = api.call(
        "POST",
        "/v1/incidents",
        payload={
            "environment_id": authority.environment_id,
            "external_incident_key": f"v040-{name}",
            "alert_name": "bounded-service-observation",
            "summary": "Bounded local service telemetry observation.",
            "started_at": started_at,
            "ended_at": ended.isoformat(),
            "candidate_service_ids": sorted(by_logical.values()),
            "labels": {},
        },
        key=f"v040-{name}-incident",
    )
    if (
        incident["baseline_id"] != baseline["baseline"]["baseline_id"]
        or incident["baseline_sha256"] != baseline["baseline"]["baseline_sha256"]
    ):
        raise ValueError("Incident did not use frozen Active Baseline")
    diagnosis = api.job(
        f"/v1/incidents/{incident['incident_id']}/diagnosis-jobs",
        key=f"v040-{name}-diagnosis",
    )
    evidence = api.call("GET", f"/v1/incidents/{incident['incident_id']}/evidence")
    result = {"incident": incident, "diagnosis": diagnosis, "evidence": evidence}
    seal_private(runtime.private / "host" / f"{name}-diagnosis.json", result)
    return result
