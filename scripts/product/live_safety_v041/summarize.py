"""Build matrix/timing from typed case results; never infer missing timestamps."""

from datetime import datetime
import json
from typing import Any
from .export import OUT

PAIRS = {
    "fault_ack_to_first_failure_ms": (
        "fault_write_acknowledged",
        "first_failed_business_request",
    ),
    "fault_ack_to_diagnosis_completed_ms": (
        "fault_write_acknowledged",
        "diagnosis_completed",
    ),
    "diagnosis_job_latency_ms": ("diagnosis_job_started", "diagnosis_completed"),
    "diagnosis_to_candidate_ms": ("diagnosis_completed", "candidate_persisted"),
    "approval_to_current_state_ms": ("approval_persisted", "current_state_observed"),
    "approval_to_authorization_ms": ("approval_persisted", "authorization_persisted"),
    "authorization_to_write_intent_ms": (
        "authorization_persisted",
        "write_intent_committed",
    ),
    "write_intent_to_gateway_consumption_ms": (
        "write_intent_committed",
        "gateway_restore_consumed",
    ),
    "gateway_consumption_to_receipt_ms": (
        "gateway_restore_consumed",
        "StepReceipt_persisted",
    ),
    "receipt_to_first_success_ms": (
        "StepReceipt_persisted",
        "first_successful_business_request",
    ),
    "receipt_to_window1_complete_ms": (
        "StepReceipt_persisted",
        "recovery_window_1_ended",
    ),
    "receipt_to_verified_recovery_ms": ("StepReceipt_persisted", "RECOVERED_persisted"),
    "fault_ack_to_verified_recovery_ms": (
        "fault_write_acknowledged",
        "RECOVERED_persisted",
    ),
    "cleanup_duration_ms": ("cleanup_started", "cleanup_completed"),
}


def duration(events: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    a, b = events.get(start, {}), events.get(end, {})
    base = {"start_event": start, "end_event": end, "sample_count": 1}
    if "utc" not in a or "utc" not in b or "NOT_MEASURED" in (a["utc"], b["utc"]):
        return {
            **base,
            "value_ms": "NOT_MEASURED",
            "clock": "NOT_MEASURED",
            "reason": "Authoritative endpoint timestamp unavailable",
        }
    if (
        isinstance(a.get("monotonic_ns"), int)
        and isinstance(b.get("monotonic_ns"), int)
        and a.get("clock_scope") == b.get("clock_scope")
    ):
        value = (b["monotonic_ns"] - a["monotonic_ns"]) / 1e6
        clock = "same-process monotonic"
    else:
        value = (
            datetime.fromisoformat(b["utc"].replace("Z", "+00:00"))
            - datetime.fromisoformat(a["utc"].replace("Z", "+00:00"))
        ).total_seconds() * 1000
        clock = "cross-object UTC"
    if value < 0:
        return {
            **base,
            "value_ms": "NOT_MEASURED",
            "clock": clock,
            "reason": "Non-causal timestamp ordering; no interpolation",
        }
    return {**base, "value_ms": round(value, 3), "clock": clock}


def summarize() -> None:
    cases = [json.loads(p.read_text()) for p in sorted(OUT.glob("case-s*.json"))]
    matrix: dict[str, Any] = {
        "schema_version": "ecomsre.product.live-safety-matrix.v1",
        "status": "PASS" if len(cases) == 5 else "IN_PROGRESS",
        "cases": [
            {
                "case_id": c["case_id"],
                "terminal": c["terminal"],
                "counts": c["counts"],
                "cleanup": "CLEAN" if c["cleanup"]["clean"] else "NOT_CLEAN",
                "source_head": c["source_head"],
            }
            for c in cases
        ],
        "sample_count_per_case": 1,
        "claim_boundary": "Five bounded pinned local Payment cases; not all attacks, production or general exactly-once.",
    }
    if len(cases) == 5:
        from scripts.ci.verify_product_v041_closeout import verify_case

        for c in cases:
            verify_case(c)
        matrix.update(
            unauthorized_product_writes=0,
            duplicate_product_writes=0,
            unknown_target_writes=0,
            alternate_runbook_executions=0,
            provider_calls=0,
            final_owned_resources={"container": 0, "network": 0, "volume": 0},
            non_owned_resources_unchanged=all(
                c["cleanup"]["non_owned_unchanged"] for c in cases
            ),
        )
    timings = {}
    for c in cases:
        ident = c["case_id"]
        events = c["timeline"]
        if ident == "S3":
            metrics = {k: duration(events, *pair) for k, pair in PAIRS.items()}
        elif ident == "S4":
            metrics = {
                "receipt_to_verification_failed_ms": duration(
                    events, "StepReceipt_persisted", "VERIFICATION_FAILED_persisted"
                )
            }
        else:
            metrics = {
                "time_to_safe_denial_ms": duration(
                    events, "safe_denial_request_started", "safe_denial"
                )
            }
        timings[ident] = {"sample_count": 1, "events": events, "metrics": metrics}
    timing = {
        "schema_version": "ecomsre.product.observed-timing.v1",
        "cases": timings,
        "metric_names": "Observed Time to Detect; Observed Diagnosis Latency; Observed Time to First Recovery; Observed Time to Verified Recovery; Observed End-to-End Recovery",
        "limitations": "No mean, P95 or SLO. UTC reflects persisted object fields and explicitly marked observation times. Missing exact gateway consumption timestamp is NOT_MEASURED. No file mtime or test duration substitutes.",
    }
    for name, value in [
        ("live-safety-matrix.json", matrix),
        ("timing-summary.json", timing),
    ]:
        (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    summarize()
