from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.rcaeval_adaptive_v1_offline_diagnosis import (  # noqa: E402
    analyze_arm_order,
    assert_public_payload,
    infer_failure_context,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 9, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("operation_types", "role", "route", "disposition"),
    (
        (
            ("FINAL_JUDGE",),
            "INITIAL_DIAGNOSIS",
            None,
            "UNAVAILABLE_INITIAL_FAILED_BEFORE_GATE",
        ),
        (
            ("FINAL_JUDGE", "LOGS_SPECIALIST"),
            "LOGS_VERIFIER",
            None,
            "UNAVAILABLE_AMBIGUOUS_LOGS_OR_BOTH",
        ),
        (
            ("FINAL_JUDGE", "TRACES_SPECIALIST"),
            "TRACE_CAUSAL_SPECIALIST",
            "VERIFY_TRACES",
            "KNOWN_FROM_OPERATION_SEQUENCE",
        ),
        (
            ("FINAL_JUDGE", "LOGS_SPECIALIST", "TRACES_SPECIALIST"),
            "TRACE_CAUSAL_SPECIALIST",
            "VERIFY_BOTH",
            "KNOWN_FROM_OPERATION_SEQUENCE",
        ),
        (
            ("FINAL_JUDGE", "LOGS_SPECIALIST", "FINAL_JUDGE"),
            "FUSION_JUDGE",
            "VERIFY_LOGS",
            "KNOWN_FROM_OPERATION_SEQUENCE",
        ),
        (
            (
                "FINAL_JUDGE",
                "LOGS_SPECIALIST",
                "TRACES_SPECIALIST",
                "FINAL_JUDGE",
            ),
            "FUSION_JUDGE",
            "VERIFY_BOTH",
            "KNOWN_FROM_OPERATION_SEQUENCE",
        ),
    ),
)
def test_failure_context_never_invents_a_gate_route(
    operation_types: tuple[str, ...],
    role: str,
    route: str | None,
    disposition: str,
) -> None:
    observed = infer_failure_context(operation_types)

    assert observed.operation_role == role
    assert observed.route == route
    assert observed.route_disposition == disposition


def test_arm_order_counts_attempts_before_adaptive_and_first_429() -> None:
    observed = analyze_arm_order(
        reference_intervals=((_utc(1), _utc(2)), (_utc(2), _utc(3))),
        adaptive_intervals=((_utc(4), _utc(5)), (_utc(5), _utc(6))),
        provider_attempt_intervals=(
            (_utc(1), _utc(1, 1)),
            (_utc(2), _utc(2, 1)),
            (_utc(4), _utc(4, 1)),
            (_utc(4, 20), _utc(4, 30)),
            (_utc(5), _utc(5, 1)),
        ),
        first_adaptive_429_at=_utc(4, 30),
    )

    assert observed["all_reference_completed_before_adaptive_started"] is True
    assert observed["wall_clock_overlap"] is False
    assert observed["provider_attempts_before_first_adaptive"] == 2
    assert observed["provider_attempts_before_first_adaptive_429"] == 3


@pytest.mark.parametrize(
    "payload",
    (
        {"case_id": "hidden"},
        {"nested": {"run_id": "hidden"}},
        {"path": "/Users/example/private-output.json"},
        {"reference": "metric:0001"},
        {"reference": "indicator:0001"},
        {"raw_provider_output": "hidden"},
    ),
)
def test_public_payload_rejects_private_or_case_level_material(payload: object) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        assert_public_payload(payload)


def test_public_payload_allows_aggregate_only_diagnostics() -> None:
    assert_public_payload(
        {
            "classification": [
                "POST_HOC_DEVELOPMENT_DIAGNOSTIC",
                "NO_PROVIDER_CALLS",
                "NOT_EXTERNAL_INFERENCE",
            ],
            "http_429": {"terminal_failures": 65, "recovered": 2},
            "gate": {"known_routes": 55, "unavailable_routes": 65},
        }
    )
