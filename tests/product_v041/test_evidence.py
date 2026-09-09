"""Regression checks reject inflated authority and fabricated timing."""

import json
from pathlib import Path
import pytest
from scripts.ci.verify_product_v041_closeout import verify_case
from scripts.product.live_safety_v041.summarize import duration

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "change",
    [
        "authorization",
        "write_intent",
        "executor_dispatch",
        "receipt",
        "gateway_consumption",
        "external_product_writes",
        "cleanup",
        "provider",
    ],
)
def test_zero_write_evidence_rejects_counter_or_cleanup_drift(change):
    case = json.loads(
        (
            ROOT / "docs/results/product-v041-live-safety/case-s1-revoked-approval.json"
        ).read_text()
    )
    if change == "cleanup":
        case["cleanup"]["remaining"]["container"] = 1
    elif change == "provider":
        case["provider_calls"] = 1
    else:
        case["counts"][change] = 1
    with pytest.raises(ValueError):
        verify_case(case)


def test_same_process_duration_uses_monotonic_despite_utc_drift():
    events = {
        "a": {"utc": "2026-09-09T10:00:00Z", "monotonic_ns": 0, "clock_scope": "a"},
        "b": {
            "utc": "2026-09-09T09:59:59Z",
            "monotonic_ns": 12000000,
            "clock_scope": "a",
        },
    }
    value = duration(events, "a", "b")
    assert value["value_ms"] == 12
    assert value["clock"] == "same-process monotonic"


def test_cross_process_monotonic_is_not_subtracted():
    events = {
        "a": {
            "utc": "2026-09-09T10:00:00Z",
            "monotonic_ns": 900000000,
            "clock_scope": "a",
        },
        "b": {"utc": "2026-09-09T10:00:01Z", "monotonic_ns": 1, "clock_scope": "b"},
    }
    assert duration(events, "a", "b")["value_ms"] == 1000
    assert duration(events, "a", "missing")["value_ms"] == "NOT_MEASURED"


def test_peer_witness_requires_exact_gateway_and_admits_observed_nat(tmp_path):
    from scripts.product.live_safety_v041.evidence import observed_gateway_peers

    (tmp_path / "control").mkdir()
    (tmp_path / "control/peer-witness.json").write_text(
        json.dumps({"peer": "192.0.2.1"})
    )
    with pytest.raises(ValueError, match="WITHOUT_GATEWAY"):
        observed_gateway_peers(tmp_path)
    row = {
        "Name": "/case-remediation-control-gateway",
        "NetworkSettings": {"Networks": {"control": {"IPAddress": "192.0.2.2"}}},
    }
    (tmp_path / "running-gateway.json").write_text(json.dumps(row))
    assert observed_gateway_peers(tmp_path) == {"192.0.2.1", "192.0.2.2"}
    assert "192.0.2.3" not in observed_gateway_peers(tmp_path)


@pytest.mark.parametrize(
    "field", ["provider_calls", "agent_writes", "runbook_executions"]
)
def test_healthy_diagnosis_counters_are_measured(field):
    case = json.loads(
        (
            ROOT
            / "docs/results/product-v041-live-safety/case-s0-healthy-non-action.json"
        ).read_text()
    )
    case["healthy_diagnosis"][field] = 1
    with pytest.raises(ValueError, match="HEALTHY_DIAGNOSIS"):
        verify_case(case)


@pytest.mark.parametrize("mutation", ["missing", "wrong_pair"])
def test_timing_requires_named_metric_and_exact_endpoints(mutation):
    from scripts.ci.verify_product_v041_closeout import verify_timing

    case = json.loads(
        (
            ROOT
            / "docs/results/product-v041-live-safety/case-s0-healthy-non-action.json"
        ).read_text()
    )
    item = json.loads(
        (ROOT / "docs/results/product-v041-live-safety/timing-summary.json").read_text()
    )["cases"]["S0"]
    if mutation == "missing":
        item["metrics"] = {}
    else:
        item["metrics"]["time_to_safe_denial_ms"] = duration(
            item["events"], "safe_denial", "safe_denial"
        )
    with pytest.raises(ValueError, match="TIMING_"):
        verify_timing(case, item)
