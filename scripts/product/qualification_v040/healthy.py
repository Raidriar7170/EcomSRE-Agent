"""Frozen healthy traffic and Baseline semantics with no-fault stage boundaries."""

from __future__ import annotations
import hashlib
from collections.abc import Callable
from typing import Any
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    CANDIDATES_V030,
    build_product_v030_environment_payload,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from scripts.product.v040_preparation import (
    ProductApiV040,
    runtime_snapshot,
    bounded_traffic,
)
from scripts.product.v040_runtime import read_json, seal_private
from scripts.product.v040_warmup import application_warmup


def healthy_baseline(
    runtime: Any,
    lifecycle: Any,
    inputs: dict[str, str],
    checkpoint: Callable[[str], None],
) -> dict[str, Any]:
    checkpoint("BEFORE_CONNECTOR_VERIFICATION")
    api = ProductApiV040(runtime)
    profile = read_json(runtime.repository / "config/product-v040/live-profile.v1.json")
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
    checkpoint("AFTER_CONNECTOR_VERIFICATION")
    checkpoint("BEFORE_WARMUP")
    application_warmup(runtime, profile["application_warmup"])
    checkpoint("AFTER_WARMUP")
    checkpoint("BEFORE_HEALTHY_CONTROL")
    traffic = bounded_traffic(
        runtime,
        profile["healthy_traffic"],
        name="healthy",
        minimum_seconds=profile["healthy_observation_seconds"],
    )
    if traffic["traffic"]["failed"] or traffic["traffic"]["attempted"] != 30:
        raise ValueError("healthy control traffic failed")
    checkpoint("AFTER_HEALTHY_CONTROL")
    checkpoint("BEFORE_BASELINE")
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
    checkpoint("AFTER_BASELINE")
    return result
