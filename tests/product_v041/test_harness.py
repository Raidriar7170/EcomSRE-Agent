"""Focused tests for the new harness boundary, independent of Docker."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
from scripts.product.live_safety_v041.api_transport import validate
from scripts.product.live_safety_v041.support import build_plan
from scripts.product.live_safety_v041.observer import window_observation
from ecomsre.product.remediation.execution_contracts import RecoveryPolicyV1


def test_transport_fixed_routes_and_replay():
    for route in [
        "/v1/remediation-approvals/appr-123",
        "/v1/remediation-attempts/attempt-123/receipts",
        "/v1/remediation-candidates/cand-123/attempts",
    ]:
        body = {"method": "POST", "route": route, "body": {}, "key": "minimal-90003"}
        assert validate(body) == body
    for route in ["/execute", "/v1/incidents/../execute", "https://example.org"]:
        with pytest.raises(ValueError):
            validate({"method": "POST", "route": route, "body": {}, "key": "minimal-1"})


def test_bound_api_has_only_read_channel(tmp_path: Path):
    images = {
        name: {"Id": name}
        for name in ["payment", "flagd", "otel-collector", "prometheus", "product"]
    }
    ports = {
        name: 12000 + i
        for i, name in enumerate(
            ["api", "probe", "flagd", "control", "prometheus", "observer", "gateway"]
        )
    }
    plan = build_plan(
        tmp_path,
        "case-one",
        images,
        ports,
        {"owned": "one"},
        {name: name for name in ["admin", "read", "write", "observer"]},
    )
    api = plan["services"]["bound-api"]
    assert api["network_mode"] == "none"
    assert "ports" not in api
    assert "networks" not in api
    assert "ECOMSRE_REMEDIATION_WRITE_TOKEN" not in api["environment"]
    targets = {m["target"] for m in api["volumes"]}
    assert "/run/remediation-read" in targets
    assert "/run/remediation-write" not in targets
    assert "/var/run/docker.sock" not in targets
    assert "/run/remediation-private" not in targets


def test_empty_real_window_preserves_zero_business_evidence():
    fields = {
        k: "a" * 64
        for k in [
            "baseline_sha256",
            "baseline_configuration_digest",
            "fault_configuration_digest",
            "target_identity_digest",
            "control_identity_sha256",
            "environment_ownership_digest",
        ]
    }
    now = datetime.now(UTC)
    policy = RecoveryPolicyV1.build(
        environment_id="env-" + "a" * 24,
        **fields,
        business_error_ratio_max=0.01,
        minimum_business_requests=10,
        window_seconds=10,
        created_at=now,
    )
    observation = window_observation(
        policy=policy,
        start=now,
        end=now + timedelta(seconds=10),
        elapsed=10000,
        requests=[],
        before=("a" * 64, True),
        after=("a" * 64, True),
    )
    assert observation.business_requests == 0
    assert observation.flag_evaluation_restored
    assert observation.business_requests < policy.minimum_business_requests
