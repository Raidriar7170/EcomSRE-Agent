from __future__ import annotations

import hashlib

from ecomsre.product.pilot.recovery_v02 import verify_baseline_recovery_v02


def test_recovery_requires_exact_flag_bytes_healthy_connectors_and_no_drift() -> None:
    baseline = b'{"flags":{"kafkaQueueProblems":0}}\n'
    result = verify_baseline_recovery_v02(
        environment_id="env-v02",
        expected_baseline_sha256=hashlib.sha256(baseline).hexdigest(),
        current_flag_bytes=baseline,
        connector_health={"logs": True, "metrics": True, "runtime": True},
        traffic_active=False,
        owned_drift_refs=(),
    )

    assert result.status == "PASS"
    assert result.baseline_restored is True
    assert result.connector_health == ("logs:HEALTHY", "metrics:HEALTHY", "runtime:HEALTHY")
    assert result.owned_drift_refs == ()


def test_recovery_fails_closed_on_any_unrestored_or_unhealthy_input() -> None:
    baseline = b"baseline"
    result = verify_baseline_recovery_v02(
        environment_id="env-v02",
        expected_baseline_sha256=hashlib.sha256(baseline).hexdigest(),
        current_flag_bytes=b"changed",
        connector_health={"logs": True, "runtime": False},
        traffic_active=True,
        owned_drift_refs=("owned-container:unexpected-state",),
    )

    assert result.status == "FAIL"
    assert result.baseline_restored is False
    assert result.traffic_stopped is False
    assert result.connector_health == ("logs:HEALTHY", "runtime:UNHEALTHY")
